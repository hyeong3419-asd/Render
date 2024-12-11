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
    prompt = """
    You are an expert in detecting fake news, disinformation, and manipulated content. Analyze the following news article and determine whether it is 'Real', 'Fake', or 'Uncertain'. In making your determination, follow these steps:
    
    1. **Factual Accuracy**: Verify whether the claims made in the article are consistent with verifiable, factual information. Cross-check the details with trusted, established sources. If the article makes claims that contradict well-established facts, mark it as 'Fake'.

    2. **Credibility of Sources**: Assess whether the article’s claims are supported by reliable and verifiable sources. Check if the sources cited in the article are known for their accuracy and trustworthiness. If the sources are unverified, questionable, or lacking citations, consider marking the article as 'Fake'.

    3. **Conflicting or Inconsistent Information**: Check whether the article contains any contradictions or discrepancies with widely accepted knowledge. Cross-check the article with reputable fact-checking websites like [PolitiFact](https://www.politifact.com) or [Full Fact](https://fullfact.org). If conflicting facts are found, classify the article as 'Fake'.

    4. **Ambiguity or Vague Language**: Evaluate whether the article uses vague, unclear, or imprecise language that can lead to misinterpretation. Articles that lack precision or fail to explain key points should be flagged as 'Fake' if they are not backed by clear evidence.

    5. **Supporting Evidence**: Does the article provide adequate evidence to support its claims? If the article presents significant claims without providing proper data, studies, or expert opinions to back them up, it may be marked as 'Fake'.

    6. **Manipulated or Misleading Visuals**: Assess whether any images, videos, or graphics in the article have been altered, manipulated, or artificially generated. Use visual verification tools such as [Sensity AI](https://www.sensity.ai), [Defudger](https://defudger.com), or [Hive AI](https://hivemoderation.com) to check for authenticity. If manipulated visuals are detected, mark the article as 'Fake'.

    7. **Context Omission or Misrepresentation**: Check whether the article omits essential context or presents facts in a misleading way. Use tools like [Hoaxy](https://hoaxy.osome.iu.edu) to visualize the spread of information and determine if crucial context is missing. If context is manipulated, classify the article as 'Fake'.

    8. **번역**: 분석 결과는 각 항목별 제목 없이 한국어로 일관된 형태의 요약으로 제공되어야 합니다.

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
