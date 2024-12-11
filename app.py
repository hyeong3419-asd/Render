from flask import Flask, render_template, request, jsonify
import openai
import requests
import os
import json
import base64
import pymysql
from bs4 import BeautifulSoup
from datetime import datetime
from google.cloud import translate_v2 as translate

# 피드백 로그 파일 설정
FEEDBACK_LOG_FILE = 'logs/feedback_log.json'
if not os.path.exists('logs'):
    os.makedirs('logs')
    
app = Flask(__name__)

# OpenAI API 키 설정
openai.api_key = os.getenv("OPENAI_API_KEY")

# 구글 FactCheck API 기본 URL과 키 설정
FACTCHECK_API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
FACTCHECK_API_KEY = os.getenv("FACTCHECK_API_KEY")

# 환경 변수에서 Google 인증 정보 가져오기
google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not google_credentials:
    raise ValueError("환경 변수 'GOOGLE_APPLICATION_CREDENTIALS'가 설정되지 않았습니다.")
    
# JSON 문자열을 Python 딕셔너리로 변환
credentials_info = json.loads(google_credentials)

# /tmp/google-credentials.json 파일로 저장
credentials_path = "/tmp/google-credentials.json"

with open(credentials_path, "w") as f:
    json.dump(credentials_info, f)
    
# 인증 파일 경로 설정
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path
translator_client = translate.Client()

@app.route('/feedback', methods=['POST'])
def save_feedback():
    data = request.json
    feedback = {
        "query": data.get('query'),
        "rating": data.get('rating'),
        "comment": data.get('comment')
    }
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            sql = "INSERT INTO feedback_log (query, rating, comment) VALUES (%s, %s, %s)"
            cursor.execute(sql, (feedback['query'], feedback['rating'], feedback['comment']))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "피드백이 저장되었습니다."})
    except Exception as e:
        app.logger.error(f"Feedback save error: {str(e)}")
        return jsonify({"status": "error", "message": "피드백 저장 중 오류가 발생했습니다."})
        
def include_feedback_in_prompt(prompt='', query=''):
    feedbacks = get_feedback_for_query(query)
    if feedbacks:
        feedback_summary = "\n".join([f"- {fb['comment']} (Rating: {fb['rating']})" for fb in feedbacks])
        return f"{prompt}\n\nPast User Feedback for similar queries:\n{feedback_summary}\n\nUse this feedback to improve your response."
    return prompt


def translate_text(text, target_language="ko"):
    """
    Google Cloud Translation API를 사용한 번역
    """
    try:
        translation = translator_client.translate(text, target_language=target_language)
        return translation["translatedText"]
    except Exception as e:
        app.logger.error(f"Translation error: {str(e)}")
        return text  # 번역 실패 시 원문 반환
        
@app.route('/')
def home():
    return render_template('index.html')
    
@app.route('/check', methods=['POST'])
def check_news():
    query = request.form.get('query', '')
    prompt = """
    You are an expert in analyzing questions and providing insights based on reliable and structured information. Your response should prioritize information retrieved from the database. Follow these instructions:

### Task Instructions:
1. **Database Priority**:
   - First, check the database for relevant information related to the user's question. If relevant data is found, use it as the primary source to construct your response.
   - If no database information is available, supplement your response with general knowledge or logical reasoning.

2. **Question Interpretation**:
   - Recognize the input as a user question, not a news article. Your role is to analyze the question and provide a logical, fact-based response.

3. **Context and Reasoning**:
   - If the database information is insufficient, provide an informed opinion using general knowledge or validated reasoning.
   - Avoid assuming the input is a news article unless explicitly stated.

4. **Output Format**:
   - Deliver a concise and structured response, prioritizing clarity and relevance.
   - If using database information, state that it is retrieved from the database for transparency.
   
5. **Language and Translation**:
   Your final analysis must be delivered in **Korean only**, formatted as a single, cohesive paragraph. Use clear and natural Korean language, avoiding English terms unless absolutely necessary (e.g., proper nouns). Ensure the flow is logical and concise.
   also don't use the term "Acording to the database..", etc
### Example:
User Question: "What are the impacts of climate change on global agriculture?"
Database Information (if available): "Studies show that climate change is reducing crop yields by 15% in certain regions due to droughts and temperature variability."

Response:
"Climate change is reducing crop yields by approximately 15% in specific regions due to drought and temperature fluctuations. Additionally, general studies highlight long-term risks to global food security."

If no database information is available:
"While there is no database information available for this query, research indicates that climate change significantly impacts agriculture by altering weather patterns, increasing drought frequency, and reducing arable land."

---

### User Question:
[Insert user's question here]



    """
    if not query:
        return jsonify({"status": "error", "message": "검색어가 입력되지 않았습니다."})
    app.logger.info(f"Received query: {query}")
    try:
        translated_query = translator_client.translate(query, target_language='en')['translatedText']
        app.logger.info(f"Translated query: {translated_query}")
    except Exception as e:
        app.logger.error(f"Translation error: {str(e)}")
        return jsonify({"status": "error", "message": "번역 중 오류가 발생했습니다."})
        
    # 피드백 데이터 가져오기
    feedbacks = get_feedback_for_query(query)
    if feedbacks:
        feedback_summary = "\n".join([f"- {fb['comment']} (Rating: {fb['rating']})" for fb in feedbacks])
        prompt += f"\n\nPast User Feedback for similar queries:\n{feedback_summary}\n"
        
    # GPT 호출
    chatgpt_response = get_chatgpt_response(translated_query, prompt)
    
    # FactCheck API 호출
    factcheck_response = get_factcheck_response(translated_query)
    
    # FactCheck 결과 번역
    try:
        if factcheck_response.get("text"):
            factcheck_response["text"] = translate_text(factcheck_response["text"], target_language="ko")
            if factcheck_response.get("rating"):
                factcheck_response["rating"] = translate_text(factcheck_response["rating"], target_language="ko")
    except Exception as e:
        app.logger.error(f"FactCheck response translation error: {str(e)}")
    feedback_log = []
    if os.path.exists(FEEDBACK_LOG_FILE):
        with open(FEEDBACK_LOG_FILE, 'r') as f:
            feedback_log = json.load(f)
    related_feedback = [fb for fb in feedback_log if fb['query'] == query]
    naver_news_url = f"https://search.naver.com/search.naver?query={requests.utils.quote(query)}&where=news"
    google_news_url = f"https://www.google.com/search?q={requests.utils.quote(query)}&tbm=nws"
    result = {
        "status": "success",
        "query": query,
        "chatgpt_response": chatgpt_response,
        "factcheck_response": factcheck_response,
        "related_feedback": related_feedback,
        "naver_news_url": naver_news_url,
        "google_news_url": google_news_url
    }
    return jsonify(result)
    
def get_chatgpt_response(query, base_prompt=''):
    # 데이터베이스 정보를 포함한 프롬프트 생성
    prompt_with_db_info = build_prompt_with_database_info(query, base_prompt)
    app.logger.info(f"Final GPT prompt: {prompt_with_db_info}")
    
    # OpenAI GPT 호출
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": prompt_with_db_info},
            {"role": "user", "content": query}
        ]
    )
    return response['choices'][0]['message']['content']


    
def get_factcheck_response(query):
    try:
        params = {
            'query': query,
            'key': FACTCHECK_API_KEY,
            'languageCode': 'en'
        }
        response = requests.get(FACTCHECK_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
        claims = data.get('claims', [])
        if claims:
            claim = claims[0]
            review = claim.get('claimReview', [{}])[0]
            image_url = review.get('publisher', {}).get('imageUrl', '')
            return {
                "text": claim.get('text', '내용 없음'),
                "rating": review.get('textualRating', '평가 없음'),
                "url": review.get('url', 'URL 없음'),
                "image_url": image_url
            }
        else:
            return {
                "text": "FactCheck API에서 관련된 검증 결과를 찾을 수 없습니다.",
                "rating": "",
                "url": "",
                "image_url": ""
            }
    except Exception as e:
        app.logger.error(f"FactCheck API error: {str(e)}")
        return {
            "text": f"FactCheck API 호출 에러: {str(e)}",
            "rating": "",
            "url": "",
            "image_url": ""
        }
        
def get_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        db=os.getenv('DB_NAME'),
        port=int(os.getenv('DB_PORT', '3306')),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    
def get_feedback_for_query(query):
    feedbacks = []
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT query, rating, comment FROM feedback_log WHERE query = %s ORDER BY id DESC LIMIT 10"
            cursor.execute(sql, (query,))
            feedbacks = cursor.fetchall()
    except Exception as e:
        app.logger.error(f"Error fetching feedback: {str(e)}")
    finally:
        conn.close()
    return feedbacks

def get_database_information(query):
    """
    데이터베이스에서 관련 정보를 검색
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            # 예: 특정 테이블에서 검색
            sql = "SELECT comment AS info FROM feedback_log WHERE query = %s LIMIT 1"
            cursor.execute(sql, (query,))
            result = cursor.fetchone()
        conn.close()
        app.logger.info(f"Database information retrieved: {result}")
        return result['info'] if result else None
    except Exception as e:
        app.logger.error(f"Database query error: {str(e)}")
        return None

def build_prompt_with_database_info(query, base_prompt):
    # 데이터베이스에서 정보 검색
    db_info = get_database_information(query)
    
    if db_info:
        # 데이터베이스 정보가 있을 경우 프롬프트에 우선 추가
        return (
            f"You have access to a database containing verified and reliable information. "
            f"For the query below, prioritize the following database information when formulating your response:\n"
            f"{db_info}\n\n"
            f"Query:\n{query}\n\n"
            f"If the database information is not sufficient, you may supplement it with your general knowledge."
        )
    else:
        # 데이터베이스 정보가 없을 경우 기본 프롬프트 사용
        return (
            f"{base_prompt}\n\n"
            f"Respond to the following query:\n{query}"
        )


if __name__ == '__main__':
    app.run(debug=True)
