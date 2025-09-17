import requests
import json
import re
import os
from typing import Optional, Dict, Any
import logging
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OpenWebUIToDifyBridge:
    def __init__(self,
                 openwebui_base_url: str = None,
                 dify_api_key: str = None,
                 dify_base_url: str = None,
                 openwebui_token: str = None):
        # .env 파일에서 설정값 불러오기
        self.openwebui_base_url = openwebui_base_url or os.getenv("OPENWEBUI_BASE_URL", "http://localhost:3000")
        self.dify_api_key = dify_api_key or os.getenv("DIFY_API_KEY", "")
        self.dify_base_url = dify_base_url or os.getenv("DIFY_BASE_URL", "https://api.dify.ai/v1")
        self.openwebui_token = openwebui_token or os.getenv("OPENWEBUI_TOKEN", "")
        self.dify_app_id = os.getenv("DIFY_APP_ID", "")
        self.dify_workflow_id = os.getenv("DIFY_WORKFLOW_ID", "")
        self.dify_workflow_run_id = os.getenv("DIFY_WORKFLOW_RUN_ID", "")

        self.session = requests.Session()


    def chat_with_model(self, prompt: str, model: str) -> Dict[str, Any]:
        url = f'{self.openwebui_base_url}/api/chat/completions'
        headers = {
            'Authorization': f'Bearer {self.openwebui_token}',
            'Content-Type': 'application/json'
        }
        data = {
        "model": model,
        "messages": [
            {
            "role": "user",
            "content": f"{prompt}"
            }
        ]
        }
        response = requests.post(url, headers=headers, json=data)
        return response.json()

    def preprocess_response(self, response) -> Dict[str, Any]:
        """응답 전처리"""
        if not response:
            return {"processed_text": "", "metadata": {}}

        # Dict 응답에서 텍스트 추출
        if isinstance(response, dict):
            # OpenAI 형식
            if "choices" in response:
                text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            # Ollama 형식
            elif "message" in response:
                text = response.get("message", {}).get("content", "")
            # 직접 응답
            elif "response" in response:
                text = response.get("response", "")
            # content 필드
            elif "content" in response:
                text = response.get("content", "")
            else:
                text = str(response)
        else:
            text = str(response)

        # 텍스트 정리
        cleaned_text = self.clean_text(text)

        # 메타데이터 추출
        metadata = self.extract_metadata(text)

        # 키워드 추출
        keywords = self.extract_keywords(cleaned_text)


        return {
            "processed_text": cleaned_text,
            "metadata": {
                **metadata,
                "keywords": keywords,
                "original_length": len(response),
                "processed_length": len(cleaned_text)
            }
        }

    def clean_text(self, text: str) -> str:
        """텍스트 정리"""
        # HTML 태그 제거
        text = re.sub(r'<[^>]+>', '', text)

        # 여러 공백을 하나로
        text = re.sub(r'\s+', ' ', text)

        # 특수 문자 정리
        text = re.sub(r'[^\w\s가-힣.,!?]', '', text)

        return text.strip()

    def extract_metadata(self, text: str) -> Dict[str, Any]:
        """메타데이터 추출"""
        return {
            "word_count": len(text.split()),
            "char_count": len(text),
            "has_korean": bool(re.search(r'[가-힣]', text)),
            "has_english": bool(re.search(r'[a-zA-Z]', text)),
            "has_numbers": bool(re.search(r'\d', text))
        }

    def extract_keywords(self, text: str, max_keywords: int = 10) -> list:
        """키워드 추출 (간단한 빈도 기반)"""
        words = re.findall(r'\b\w+\b', text.lower())

        # 불용어 제거
        stopwords = {'이', '그', '저', '것', '는', '은', '가', '을', '를', 'the', 'a', 'an', 'and', 'or', 'but'}
        words = [word for word in words if word not in stopwords and len(word) > 1]

        # 빈도 계산
        word_freq = {}
        for word in words:
            word_freq[word] = word_freq.get(word, 0) + 1

        # 상위 키워드 반환
        return sorted(word_freq.keys(), key=lambda x: word_freq[x], reverse=True)[:max_keywords]


    def send_to_dify(self, processed_data: Dict[str, Any], ) -> Optional[Dict]:
        """Dify API로 전송"""
        try:
            url = f"{self.dify_base_url}/workflows/run"

            # Dify에 보낼 데이터 구성
            payload = {
                "inputs": {
                    "query": processed_data["processed_text"],
                },
                "response_mode": "blocking",
                "user": "Dify",
            }

            headers = {
                "Authorization": f"Bearer {self.dify_api_key}",
                "Content-Type": "application/json"
            }

            response = self.session.post(url, json=payload, headers=headers)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            logger.error(f"Dify API 요청 실패: {e}")
            return None

    def process_workflow(self, prompt: str, model: str = "gpt4",) -> str:
        """전체 워크플로우 실행"""
        logger.info(f"워크플로우 시작: {prompt[:50]}...")

        # 1. OpenWebUI에서 응답 받기
        logger.info("OpenWebUI에서 응답 받는 중...")
        openwebui_response = self.chat_with_model(prompt, model)

        if not openwebui_response:
            return json.dumps({"error": "OpenWebUI 응답 실패"}, ensure_ascii=False)

        # 2. 응답 전처리
        logger.info("응답 전처리 중...")
        processed_data = self.preprocess_response(openwebui_response)

        # 3. Dify로 전송
        logger.info("Dify API로 전송 중...")
        app_id = self.dify_app_id 
        dify_response = self.send_to_dify(processed_data,)

        result = {
            "original_response": openwebui_response,
            "processed_data": processed_data,
            "dify_response": dify_response,
            "success": dify_response is not None
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

# 사용 예시
if __name__ == "__main__":
    # .env 파일의 설정을 자동으로 사용
    bridge = OpenWebUIToDifyBridge()

    # 테스트 실행
    result = bridge.process_workflow(
        prompt="내일부터 휴가를 3일 쓰고 싶어요",
        model="gpt-4"
    )

    print(result)