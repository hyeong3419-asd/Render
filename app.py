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
    You are a highly skilled analyst specializing in detecting fake news, disinformation, and manipulated content. Your role is to analyze the following news article and determine whether it is 'Real', 'Fake', or 'Uncertain'. Follow these detailed instructions:

### Step-by-step Analysis:
1. **Factual Accuracy**:
   Verify if the claims in the article align with well-established, verifiable facts. Use reputable, trusted sources to cross-check the information. If the claims directly contradict established facts, classify the article as 'Fake'.

2. **Credibility of Sources**:
   Evaluate the reliability of the sources cited in the article. Identify whether they are known for factual accuracy and trustworthiness. If sources are missing, questionable, or unverifiable, lean towards marking the article as 'Fake'.

3. **Conflicting or Inconsistent Information**:
   Look for contradictions or discrepancies with widely accepted knowledge. Consult fact-checking platforms like [PolitiFact](https://www.politifact.com) or [Full Fact](https://fullfact.org). If significant conflicts are found, categorize the article as 'Fake'.

4. **Ambiguity or Vague Language**:
   Assess whether the article uses imprecise or unclear language that can lead to misinterpretation. Articles with vague claims unsupported by concrete evidence should be marked as 'Fake'.

5. **Supporting Evidence**:
   Check whether the article provides sufficient data, studies, or expert opinions to back its claims. Articles making bold claims without adequate evidence are likely 'Fake'.

6. **Manipulated or Misleading Visuals**:
   Examine any visuals, such as images or videos, for signs of manipulation. Utilize tools like [Sensity AI](https://www.sensity.ai), [Defudger](https://defudger.com), or [Hive AI](https://hivemoderation.com) to verify their authenticity. Manipulated visuals should result in a 'Fake' classification.

7. **Context Omission or Misrepresentation**:
   Determine if the article omits crucial context or presents facts in a misleading manner. Tools like [Hoaxy](https://hoaxy.osome.iu.edu) can help analyze the spread of information. Articles that manipulate context should be marked as 'Fake'.

8. **Language and Translation**:
   Your final analysis must be delivered in **Korean only**, formatted as a single, cohesive paragraph. Use clear and natural Korean language, avoiding English terms unless absolutely necessary (e.g., proper nouns). Ensure the flow is logical and concise.

9.**Database Priority**:
   If database information is provided, use it as the primary basis for your response. Clearly state that it is database-sourced.

10.**Supplemental Reasoning**:
   If database information is insufficient or unavailable, use general knowledge and logical reasoning.

11.**Indeterminate Cases**:
   If the article cannot be conclusively classified as 'Real' or 'Fake', respond with 'Uncertain'. Specify reasons, such as lack of reliable sources or inconclusive evidence.   

### Additional Instructions:
- Use the provided database information if applicable.
- Prioritize factual correctness over stylistic elements.
- If the article cannot be definitively classified as 'Real' or 'Fake' due to insufficient information, respond with 'Uncertain'. Include specific reasons, such as lack of reliable sources or inconclusive visuals.

### Response Format:
Respond with a single Korean paragraph addressing all criteria comprehensively. Avoid numbering or sectioning the response. Provide an analysis that integrates all relevant aspects.

### Example Query:
Analyze the following article: [Insert article text here].


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
