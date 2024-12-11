from flask import Flask, render_template, request, jsonify
import openai
import requests
import os
import json
import base64
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

# 환경 변수에서 인증 파일 경로를 읽음
credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
if not credentials_path:
    raise ValueError("환경 변수 'GOOGLE_APPLICATION_CREDENTIALS'가 설정되지 않았습니다.")

# JSON 내용을 파일로 저장
credentials_path = "/tmp/google-credentials.json"
with open(credentials_path, "w") as f:
    f.write(credentials_json)

# 인증 파일 경로 설정
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path

# Translator 클라이언트 초기화
translator_client = translate.Client()


@app.route('/feedback', methods=['POST'])
def save_feedback():
    """
    사용자 피드백을 저장하는 API
    """
    data = request.json
    feedback = {
        "query": data.get('query'),
        "rating": data.get('rating'),
        "comment": data.get('comment'),
        "timestamp": datetime.now().isoformat()
    }
    try:
        # 로그 파일이 있는지 확인
        feedback_log = []
        if os.path.exists(FEEDBACK_LOG_FILE):
            with open(FEEDBACK_LOG_FILE, 'r') as f:
                feedback_log = json.load(f)

        # 피드백 저장
        feedback_log.append(feedback)
        with open(FEEDBACK_LOG_FILE, 'w') as f:
            json.dump(feedback_log, f, ensure_ascii=False, indent=4)

        return jsonify({"status": "success", "message": "피드백이 저장되었습니다."})
    except Exception as e:
        app.logger.error(f"Feedback save error: {str(e)}")
        return jsonify({"status": "error", "message": "피드백 저장 중 오류가 발생했습니다."})


def include_feedback_in_prompt(prompt=''):
    """
    기존 피드백 로그를 GPT 프롬프트에 추가
    """
    try:
        feedback_log = []
        if os.path.exists(FEEDBACK_LOG_FILE):
            with open(FEEDBACK_LOG_FILE, 'r') as f:
                feedback_log = json.load(f)

        # 로그 데이터를 프롬프트에 추가
        feedback_summary = "\n".join(
            [f"Query: {fb['query']}, Rating: {fb['rating']}, Comment: {fb['comment']}" for fb in feedback_log]
        )
        return f"{prompt}\n\nPast User Feedback:\n{feedback_summary}"
    except Exception as e:
        app.logger.error(f"Error including feedback in prompt: {str(e)}")
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
    prompt = """You are an expert in detecting fake news, disinformation, and manipulated content. Analyze the following news article and determine whether it is 'Real', 'Fake', or 'Uncertain'."""

    if not query:
        return jsonify({"status": "error", "message": "검색어가 입력되지 않았습니다."})

    app.logger.info(f"Received query: {query}")

    try:
        translated_query = translator_client.translate(query, target_language='en')['translatedText']
        app.logger.info(f"Translated query: {translated_query}")
    except Exception as e:
        app.logger.error(f"Translation error: {str(e)}")
        return jsonify({"status": "error", "message": "번역 중 오류가 발생했습니다."})

    chatgpt_response = get_chatgpt_response(translated_query, prompt)
    factcheck_response = get_factcheck_response(translated_query)

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

def get_chatgpt_response(query, prompt=''):
    try:
        prompt = include_feedback_in_prompt(prompt)
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query}
            ]
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        app.logger.error(f"ChatGPT API error: {str(e)}")
        return f"챗GPT 호출 에러: {str(e)}"

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

if __name__ == '__main__':
    app.run(debug=True)
