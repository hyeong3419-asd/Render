from flask import Flask, render_template, request, jsonify
import openai
import requests
from google.cloud import translate_v2 as translate
import os
import json
from datetime import datetime

# 피드백 로그 파일 설정
FEEDBACK_LOG_FILE = 'logs/feedback_log.json'
if not os.path.exists('logs'):
    os.makedirs('logs')

app = Flask(__name__)

# OpenAI API 설정
openai.api_key = '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'

# 구글 FactCheck API 기본 URL과 키 설정
FACTCHECK_API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
FACTCHECK_API_KEY = "AIzaSyAv4nZ5taO8Jc4ucf_ycmQsd3WiBXD6oaw"

# 환경 변수 설정 (코드에서 직접 설정)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "C:\Users\my_fake_news_detector (2)\credentials.json"

# 번역 클라이언트 초기화
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
        if os.path.exists(FEEDBACK_LOG_FILE):
            with open(FEEDBACK_LOG_FILE, 'r') as f:
                feedback_log = json.load(f)
        else:
            feedback_log = []

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
     # 명령 프롬프트 추가
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

    # 1. 한글 입력을 영어로 번역
    try:
        translated_query = translator_client.translate(query, target_language='en')['translatedText']
        app.logger.info(f"Translated query: {translated_query}")
    except Exception as e:
        app.logger.error(f"Translation error: {str(e)}")
        return jsonify({"status": "error", "message": "번역 중 오류가 발생했습니다."})

    # 2. 챗GPT API 호출 (명령 프롬프트를 포함하여)
    chatgpt_response = get_chatgpt_response(query, prompt)

    # 3. 구글 FactCheck API 호출
    factcheck_response = get_factcheck_response(translated_query)

    # 4. 검증 결과 메시지 생성
    verification_message = "검증 결과는 아래와 같습니다."

    # 5. 구글 FactCheck API 응답 한글로 번역
    if factcheck_response.get("text"):
        try:
            factcheck_response["text"] = translate_text(factcheck_response["text"], target_language="ko")
            if factcheck_response.get("rating"):
                factcheck_response["rating"] = translate_text(factcheck_response["rating"], target_language="ko")
        except Exception as e:
            app.logger.error(f"FactCheck response translation error: {str(e)}")

    
    # 피드백 로그 불러오기
    feedback_log = []
    if os.path.exists(FEEDBACK_LOG_FILE):
        with open(FEEDBACK_LOG_FILE, 'r') as f:
            feedback_log = json.load(f)

    related_feedback = [fb for fb in feedback_log if fb['query'] == query]


    # 뉴스 검색 URL 생성
    naver_news_url = f"https://search.naver.com/search.naver?query={requests.utils.quote(query)}&where=news"
    google_news_url = f"https://www.google.com/search?q={requests.utils.quote(query)}&tbm=nws"

    result = {
        "status": "success",
        "query": query,
        "verification_message": verification_message,
        "chatgpt_response": chatgpt_response,
        "factcheck_response": factcheck_response,
        "related_feedback": related_feedback,
        "naver_news_url": naver_news_url,
        "google_news_url": google_news_url
    }

    return jsonify(result)

def get_chatgpt_response(query, prompt=''):
    """
    ChatGPT 응답을 호출하고 피드백 로그를 포함
    """
    try:
         # 기존 프롬프트에 피드백 로그 추가
        prompt = include_feedback_in_prompt(prompt)

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt},  # 시스템 역할로 명령 프롬프트 추가
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
