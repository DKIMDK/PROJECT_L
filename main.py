import os
import json
import asyncio
import logging
import httpx
import websockets
from datetime import datetime
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re
import dateutil.parser
import uuid

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('middleware.log')
    ]
)
logger = logging.getLogger(__name__)

# Pydantic 모델들
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: bool = True
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None

class VacationTestRequest(BaseModel):
    message: str

class VacationRequestProcessor:
    """휴가 요청 처리기"""
    
    def __init__(self):
        # 휴가 관련 키워드들
        self.vacation_keywords = [
            '휴가', '연차', '반차', '병가', '출장', '외출', '조퇴', '지각',
            '휴직', '육아휴직', '병원', '약속', '일정', '스케줄'
        ]
        
        self.vacation_types = {
            '연차': 'annual_leave',
            '휴가': 'vacation',
            '반차': 'half_day',
            '병가': 'sick_leave',
            '출장': 'business_trip',
            '외출': 'time_out',
            '조퇴': 'early_leave',
            '지각': 'late_arrival',
            '휴직': 'leave_of_absence',
            '육아휴직': 'parental_leave'
        }

    def extract_vacation_info(self, message: str) -> Dict[str, Any]:
        """메시지에서 휴가 정보 추출"""
        try:
            is_vacation_request = any(keyword in message for keyword in self.vacation_keywords)
            
            vacation_type = None
            for korean_type, english_type in self.vacation_types.items():
                if korean_type in message:
                    vacation_type = english_type
                    break
            
            # 날짜 패턴 찾기
            dates = self.extract_dates(message)
            start_date = dates[0] if dates else None
            end_date = dates[1] if len(dates) > 1 else dates[0] if dates else None
            
            # 기간 추출
            duration = self.extract_duration(message)
            
            return {
                "is_vacation_request": is_vacation_request,
                "vacation_type": vacation_type,
                "start_date": start_date,
                "end_date": end_date,
                "duration": duration,
                "extracted_dates": dates,
                "confidence": 0.8 if is_vacation_request else 0.0
            }
        except Exception as e:
            logger.error(f"휴가 정보 추출 오류: {e}")
            return {
                "is_vacation_request": False,
                "vacation_type": None,
                "start_date": None,
                "end_date": None,
                "duration": None,
                "extracted_dates": [],
                "confidence": 0.0
            }

    def extract_dates(self, text: str) -> List[str]:
        """텍스트에서 날짜 추출"""
        dates = []
        
        # 다양한 날짜 패턴들
        patterns = [
            r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',  # 2024-01-15 또는 2024/01/15
            r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',  # 15-01-2024 또는 15/01/2024
            r'\d{1,2}월\s*\d{1,2}일',        # 1월 15일
            r'\d{1,2}월\d{1,2}일',           # 1월15일
            r'(\d{1,2})일',                   # 15일
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                normalized = self.normalize_date(match)
                if normalized not in dates:
                    dates.append(normalized)
        
        return dates

    def extract_duration(self, text: str) -> Optional[str]:
        """기간 정보 추출"""
        duration_patterns = [
            r'(\d+)일\s*간?',
            r'(\d+)주\s*간?',
            r'(\d+)개월\s*간?',
            r'(\d+)시간',
            r'하루',
            r'이틀',
            r'사흘'
        ]
        
        for pattern in duration_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        
        return None

    def normalize_date(self, date_str: str) -> str:
        """날짜 문자열을 YYYY-MM-DD 형식으로 정규화"""
        try:
            # 한글 날짜 처리 (예: 1월 15일)
            if '월' in date_str and '일' in date_str:
                match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', date_str)
                if match:
                    month = int(match.group(1))
                    day = int(match.group(2))
                    year = datetime.now().year
                    return f"{year:04d}-{month:02d}-{day:02d}"
            
            # 다른 형식들은 dateutil로 파싱
            parsed = dateutil.parser.parse(date_str)
            return parsed.strftime("%Y-%m-%d")
        except:
            return date_str

class OpenWebUIMiddleware:
    """Open WebUI 미들웨어 - 실제 OpenWebUI 구조에 맞게 수정"""

    def __init__(self):
        # 환경변수 설정
        self.openwebui_base_url = os.getenv("OPENWEBUI_BASE_URL", "http://localhost:3000")
        self.openwebui_token = os.getenv("OPENWEBUI_TOKEN", "")
        self.dify_api_key = os.getenv("DIFY_API_KEY", "")
        self.dify_base_url = os.getenv("DIFY_BASE_URL", "https://api.dify.ai/v1")
        
        # API 키 검증
        if not self.dify_api_key:
            logger.warning("⚠️ DIFY_API_KEY가 설정되지 않았습니다!")
        
        if not self.openwebui_token:
            logger.warning("⚠️ OPENWEBUI_TOKEN이 설정되지 않았습니다!")

        self.vacation_processor = VacationRequestProcessor()

        # httpx 클라이언트 설정
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            follow_redirects=True
        )

    async def process_user_message(self, message_content: str) -> Dict[str, Any]:
        """사용자 메시지 전처리"""
        logger.info(f"사용자 메시지 처리: {message_content[:50]}...")
        
        # 휴가 정보 추출
        vacation_info = self.vacation_processor.extract_vacation_info(message_content)
        
        # 메타데이터 생성
        metadata = {
            "word_count": len(message_content.split()),
            "char_count": len(message_content),
            "has_korean": bool(re.search(r'[가-힣]', message_content)),
            "has_english": bool(re.search(r'[a-zA-Z]', message_content)),
            "has_numbers": bool(re.search(r'\d', message_content)),
            "timestamp": datetime.now().isoformat()
        }
        
        return {
            "original_message": message_content,
            "vacation_info": vacation_info,
            "metadata": metadata
        }

    async def send_to_dify(self, processed_data: Dict[str, Any]) -> Optional[Dict]:
        """Dify API로 비동기 전송"""
        if not self.dify_api_key:
            logger.error("DIFY_API_KEY가 설정되지 않았습니다!")
            return None
            
        try:
            url = f"{self.dify_base_url}/workflows/run"
            
            payload = {
                "inputs": {
                    "original_text": processed_data["original_message"],
                    "vacation_info": json.dumps(processed_data["vacation_info"], ensure_ascii=False),
                    "is_vacation_request": processed_data["vacation_info"]["is_vacation_request"],
                    "vacation_type": processed_data["vacation_info"]["vacation_type"] or "",
                    "start_date": processed_data["vacation_info"]["start_date"] or "",
                    "end_date": processed_data["vacation_info"]["end_date"] or "",
                    "duration": processed_data["vacation_info"]["duration"] or "",
                    "metadata": json.dumps(processed_data["metadata"], ensure_ascii=False)
                },
                "response_mode": "blocking",
                "user": "vacation_system"
            }

            headers = {
                "Authorization": f"Bearer {self.dify_api_key}",
                "Content-Type": "application/json"
            }

            logger.info(f"Dify로 전송 중...")
            
            response = await self.http_client.post(url, json=payload, headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                logger.info("✅ Dify API 성공")
                return result
            else:
                logger.error(f"❌ Dify API 오류: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"💥 Dify API 오류: {e}")
            return None

    async def forward_to_openwebui(self, request: Request, chat_data: ChatRequest):
        """OpenWebUI로 요청 전달 - 정확한 프록시 처리"""
        try:
            url = f'{self.openwebui_base_url}/api/chat/completions'
            
            # 원본 요청 헤더 복사
            headers = {}
            for key, value in request.headers.items():
                if key.lower() not in ['host', 'content-length']:
                    headers[key] = value
            
            # Authorization 헤더 설정
            if self.openwebui_token and 'authorization' not in headers:
                headers['Authorization'] = f'Bearer {self.openwebui_token}'
            
            # Content-Type 확인
            if 'content-type' not in headers:
                headers['Content-Type'] = 'application/json'

            logger.info(f"OpenWebUI로 전달: {url}")
            logger.debug(f"Headers: {headers}")
            
            # OpenWebUI로 스트리밍 요청 전달
            async with self.http_client.stream(
                'POST',
                url,
                json=chat_data.dict(),
                headers=headers
            ) as response:
                
                if response.status_code != 200:
                    logger.error(f"OpenWebUI 오류: {response.status_code}")
                    error_content = await response.aread()
                    logger.error(f"Error content: {error_content.decode()}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"OpenWebUI API error: {response.status_code}"
                    )
                
                # 응답 헤더 준비
                response_headers = {}
                for key, value in response.headers.items():
                    if key.lower() not in ['content-length', 'transfer-encoding', 'connection']:
                        response_headers[key] = value
                
                # CORS 헤더 추가
                response_headers.update({
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Allow-Credentials": "true"
                })
                
                async def generate():
                    try:
                        async for chunk in response.aiter_bytes(chunk_size=1024):
                            if chunk:
                                yield chunk
                    except Exception as e:
                        logger.error(f"스트리밍 오류: {e}")
                        # 오류 시 종료 신호 전송
                        yield b"data: [DONE]\n\n"
                
                return StreamingResponse(
                    generate(),
                    media_type=response.headers.get("content-type", "text/event-stream"),
                    headers=response_headers,
                    status_code=response.status_code
                )
                
        except Exception as e:
            logger.error(f"OpenWebUI 전달 오류: {e}")
            raise HTTPException(status_code=500, detail=f"Forward error: {str(e)}")

    async def proxy_request(self, request: Request, path: str = ""):
        """일반 요청 프록시 - OpenWebUI 구조에 맞게 수정"""
        try:
            # URL 구성
            url = f"{self.openwebui_base_url}"
            if path:
                url += f"/{path}"
            
            # 쿼리 파라미터 포함
            if request.url.query:
                url += f"?{request.url.query}"
            
            # 헤더 준비
            headers = {}
            for key, value in request.headers.items():
                if key.lower() not in ['host', 'content-length']:
                    headers[key] = value
            
            # Authorization 헤더 추가
            if self.openwebui_token:
                headers['Authorization'] = f'Bearer {self.openwebui_token}'
            
            # 바디 읽기
            body = await request.body() if request.method in ['POST', 'PUT', 'PATCH'] else None
            
            logger.debug(f"프록시 요청: {request.method} {url}")
            
            # 요청 전달
            response = await self.http_client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params) if not request.url.query else None
            )
            
            # 응답 헤더 준비
            response_headers = {}
            for key, value in response.headers.items():
                if key.lower() not in ['content-length', 'transfer-encoding', 'connection']:
                    response_headers[key] = value
            
            # CORS 헤더 추가
            response_headers.update({
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Allow-Credentials": "true"
            })
            
            # 콘텐츠 타입에 따라 응답 처리
            content_type = response.headers.get('content-type', '')
            
            if 'application/json' in content_type:
                try:
                    content = response.json()
                except:
                    content = response.text
            else:
                content = response.content
            
            return StreamingResponse(
                iter([response.content]) if isinstance(response.content, bytes) else iter([response.content.encode()]),
                status_code=response.status_code,
                headers=response_headers,
                media_type=content_type or "application/octet-stream"
            )
            
        except Exception as e:
            logger.error(f"프록시 요청 오류: {e}")
            raise HTTPException(status_code=500, detail=str(e))

# FastAPI 앱 생성
app = FastAPI(
    title="OpenWebUI-Dify Middleware",
    description="OpenWebUI와 Dify를 연결하는 미들웨어",
    version="2.0.0"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 미들웨어 인스턴스 생성
middleware = OpenWebUIMiddleware()

# 백그라운드 작업 함수
async def process_vacation_in_background(user_message: str):
    """백그라운드에서 휴가 처리"""
    try:
        logger.info("🔄 백그라운드 휴가 처리 시작")
        
        # 사용자 메시지 전처리
        processed_data = await middleware.process_user_message(user_message)
        
        # 휴가 요청인지 확인
        if processed_data["vacation_info"]["is_vacation_request"]:
            logger.info("🏖️ 휴가 요청 감지 - Dify로 전송중...")
            dify_response = await middleware.send_to_dify(processed_data)
            
            if dify_response:
                logger.info("✅ Dify 처리 완료")
                if 'data' in dify_response and 'outputs' in dify_response['data']:
                    outputs = dify_response['data']['outputs']
                    logger.info(f"Dify 결과: {outputs}")
            else:
                logger.warning("❌ Dify 처리 실패")
        else:
            logger.info("💬 일반 대화 - Dify 전송 스킵")
            
    except Exception as e:
        logger.error(f"백그라운드 처리 오류: {e}")

# API 엔드포인트들
@app.post("/api/chat/completions")
async def chat_completions(
    request: Request,
    chat_data: ChatRequest,
    background_tasks: BackgroundTasks
):
    """채팅 완료 API 엔드포인트 - 실시간 스트리밍 지원"""
    try:
        logger.info(f"🔥 채팅 요청 수신 - 모델: {chat_data.model}")
        logger.info(f"📝 메시지 수: {len(chat_data.messages)}")

        # 사용자 메시지 추출
        user_message = None
        for message in reversed(chat_data.messages):
            if message.role == 'user':
                user_message = message.content
                break

        # 백그라운드에서 휴가 처리
        if user_message:
            background_tasks.add_task(process_vacation_in_background, user_message)

        # OpenWebUI로 스트리밍 요청 전달
        return await middleware.forward_to_openwebui(request, chat_data)

    except Exception as e:
        logger.error(f"미들웨어 처리 오류: {e}")
        # 오류 발생 시에도 원본 요청을 전달
        return await middleware.forward_to_openwebui(request, chat_data)

# WebSocket 프록시 (Socket.IO 지원)
@app.websocket("/socket.io/")
async def websocket_proxy(websocket: WebSocket):
    """WebSocket/Socket.IO 프록시"""
    await websocket.accept()
    
    upstream_ws = None
    try:
        # OpenWebUI WebSocket 연결
        ws_url = f"ws://localhost:3000/socket.io/"
        
        if websocket.query_params:
            query_string = "&".join([f"{k}={v}" for k, v in websocket.query_params.items()])
            ws_url += f"?{query_string}"
        
        logger.info(f"WebSocket 프록시: {ws_url}")
        
        # 업스트림 연결
        upstream_ws = await websockets.connect(ws_url)
        
        async def forward_to_upstream():
            try:
                while True:
                    data = await websocket.receive()
                    if data.get('type') == 'websocket.receive':
                        message = data.get('text', '') or data.get('bytes', b'')
                        if isinstance(message, str):
                            await upstream_ws.send(message)
                        else:
                            await upstream_ws.send(message)
                    elif data.get('type') == 'websocket.disconnect':
                        break
            except WebSocketDisconnect:
                logger.info("클라이언트 연결 종료")
            except Exception as e:
                logger.error(f"업스트림 전달 오류: {e}")

        async def forward_to_client():
            try:
                while True:
                    message = await upstream_ws.recv()
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)
            except Exception as e:
                logger.error(f"클라이언트 전달 오류: {e}")

        # 양방향 메시지 전달
        await asyncio.gather(
            forward_to_upstream(),
            forward_to_client(),
            return_exceptions=True
        )

    except Exception as e:
        logger.error(f"WebSocket 프록시 오류: {e}")
    finally:
        if upstream_ws:
            await upstream_ws.close()

@app.get("/health")
async def health_check():
    """헬스 체크 엔드포인트"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "openwebui_url": middleware.openwebui_base_url,
        "dify_configured": bool(middleware.dify_api_key),
        "server": "FastAPI v2.0"
    }

@app.post("/test-vacation")
async def test_vacation(request: VacationTestRequest):
    """휴가 처리 테스트 엔드포인트"""
    try:
        processed_data = await middleware.process_user_message(request.message)
        
        result = {
            "processed_data": processed_data,
            "dify_response": None
        }
        
        if processed_data["vacation_info"]["is_vacation_request"]:
            dify_response = await middleware.send_to_dify(processed_data)
            result["dify_response"] = dify_response
        
        return result
    except Exception as e:
        logger.error(f"테스트 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# OpenWebUI API 엔드포인트 프록시
@app.get("/api/models")
async def get_models(request: Request):
    """모델 목록 API 프록시"""
    return await middleware.proxy_request(request, "api/models")

@app.get("/api/tags")
async def get_tags(request: Request):
    """태그 목록 API 프록시"""
    return await middleware.proxy_request(request, "api/tags")

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def api_proxy(request: Request, path: str = ""):
    """API 요청 프록시"""
    return await middleware.proxy_request(request, f"api/{path}")

# 정적 파일 및 기타 모든 요청 프록시
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_all(request: Request, path: str = ""):
    """모든 다른 요청을 Open WebUI로 프록시"""
    return await middleware.proxy_request(request, path)

# 루트 경로 프록시
@app.get("/")
async def proxy_root(request: Request):
    """루트 경로 프록시"""
    return await middleware.proxy_request(request, "")

# 앱 종료 시 정리
@app.on_event("shutdown")
async def shutdown_event():
    """앱 종료 시 리소스 정리"""
    await middleware.http_client.aclose()

if __name__ == '__main__':
    import uvicorn
    
    print("🚀 Open WebUI-Dify 미들웨어 서버 v2.0 시작")
    print(f"📱 Open WebUI: {middleware.openwebui_base_url}")
    print(f"🤖 Dify API: {'설정됨' if middleware.dify_api_key else '미설정'}")
    print("🔗 미들웨어 서버: http://localhost:8080")
    print("\n📋 주요 기능:")
    print("✅ 실시간 스트리밍 채팅 지원")
    print("✅ WebSocket/Socket.IO 프록시")
    print("✅ 백그라운드 Dify 처리")
    print("✅ 완전한 OpenWebUI 프록시")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )