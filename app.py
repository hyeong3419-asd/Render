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

def crawl_namu(query):
    """나무위키에서 특정 쿼리와 관련된 데이터를 크롤링"""
    base_url = "https://namu.wiki/w/"
    search_url = base_url + requests.utils.quote(query)

    try:
        response = requests.get(search_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # 페이지 주요 내용 가져오기
        content = soup.find(id="content")
        if content:
            return content.get_text(strip=True)
        return None
    except Exception as e:
        app.logger.error(f"Error crawling Namuwiki: {str(e)}")
        return None


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
        return f"{prompt}\n\nPast User Feedback for similar queries:\n{feedback_summary}\n\nUse this feedback to refine your response."
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
    You are an expert in detecting fake news, disinformation, and manipulated content. Analyze the following news article and determine whether it is 'Real', 'Fake', or 'Uncertain'. In making your determination, follow these steps:
    
    1. **Factual Accuracy**: Verify whether the claims made in the article are consistent with verifiable, factual information. Cross-check the details with trusted, established sources. If the article makes claims that contradict well-established facts, mark it as 'Fake'.

    2. **Credibility of Sources**: Assess whether the article’s claims are supported by reliable and verifiable sources. Check if the sources cited in the article are known for their accuracy and trustworthiness. If the sources are unverified, questionable, or lacking citations, consider marking the article as 'Fake'.

    3. **Conflicting or Inconsistent Information**: Check whether the article contains any contradictions or discrepancies with widely accepted knowledge. Cross-check the article with reputable fact-checking websites like [PolitiFact](https://www.politifact.com) or [Full Fact](https://fullfact.org). If conflicting facts are found, classify the article as 'Fake'.

    4. **Ambiguity or Vague Language**: Evaluate whether the article uses vague, unclear, or imprecise language that can lead to misinterpretation. Articles that lack precision or fail to explain key points should be flagged as 'Fake' if they are not backed by clear evidence.

    5. **Supporting Evidence**: Does the article provide adequate evidence to support its claims? If the article presents significant claims without providing proper data, studies, or expert opinions to back them up, it may be marked as 'Fake'.

    6. **Manipulated or Misleading Visuals**: Assess whether any images, videos, or graphics in the article have been altered, manipulated, or artificially generated. Use visual verification tools such as [Sensity AI](https://www.sensity.ai), [Defudger](https://defudger.com), or [Hive AI](https://hivemoderation.com) to check for authenticity. If manipulated visuals are detected, mark the article as 'Fake'.

    7. **Context Omission or Misrepresentation**: Check whether the article omits essential context or presents facts in a misleading way. Use tools like [Hoaxy](https://hoaxy.osome.iu.edu) to visualize the spread of information and determine if crucial context is missing. If context is manipulated, classify the article as 'Fake'.

    8. **번역**: 분석 결과는 각 항목별 번호나 소제목 없이 하나의 단일 단락으로 한국어만 사용해 제시하며, 불가피한 고유명사 외에는 영어 표현을 사용하지 않고 모두 번역하여 명확하고 일관된 흐름으로 자연스럽게 평가 결과를 전달한다.

    **Specialized Tools**:  
    - **Deepfake Detection**: If visuals seem altered or manipulated, assess them using deepfake detection tools such as [Sensity AI](https://www.sensity.ai) or [Defudger](https://defudger.com).  
    - **Disinformation Patterns**: Assess the spread patterns of the article on social media using [Fabula AI](https://www.fabula.ai) to detect disinformation based on how it propagates across platforms.

    **Failsafe**: If the article cannot be definitively classified as 'Real' or 'Fake' based on the above criteria, respond with 'Uncertain' and flag the article for further review. Provide specific reasons for the uncertainty (e.g., lack of reliable sources, possible manipulated visuals, etc.).
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

    # 나무위키 데이터 크롤링
    reference_text = crawl_namu(query)
    if reference_text:
        prompt += f"\n\nReference Text from Namuwiki:\n{reference_text}\n"

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
    prompt = include_feedback_in_prompt(base_prompt, query)
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": prompt},
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


if __name__ == '__main__':
    app.run(debug=True)
