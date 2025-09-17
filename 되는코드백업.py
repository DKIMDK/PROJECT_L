    async def forward_to_openwebui(self, request: Request, chat_data: ChatRequest) -> StreamingResponse:
        """Open WebUI로 요청 전달 (비동기)"""
        try:
            url = f'{self.openwebui_base_url}/api/chat/completions'
            headers = {
                'Authorization': f'Bearer {self.openwebui_token}',
                'Content-Type': 'application/json'
            }

            # 요청 데이터 준비
            request_data = chat_data.dict()
            
            if chat_data.stream:
                # 스트리밍 응답을 하나로 합쳐서 처리
                async with self.http_client.stream(
                    "POST", url, json=request_data, headers=headers
                ) as response:
                    response.raise_for_status()

                    # 모든 청크를 수집하여 완성된 응답 생성
                    collected_content = ""
                    full_response = None

                    async for chunk in response.aiter_raw():
                        if chunk:
                            chunk_str = chunk.decode('utf-8', errors='ignore')

                            if chunk_str.startswith('data: '):
                                json_str = chunk_str[len("data: "):].strip()
                                if json_str and json_str != '[DONE]':
                                    try:
                                        chunk_data = json.loads(json_str)
                                        if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                                            delta = chunk_data['choices'][0].get('delta', {})
                                            content = delta.get('content', '')
                                            if content:
                                                collected_content += content

                                            # 첫 번째 청크에서 기본 구조 가져오기
                                            if full_response is None:
                                                full_response = chunk_data.copy()
                                                full_response['object'] = 'chat.completion'
                                                full_response['choices'][0] = {
                                                    'index': 0,
                                                    'message': {
                                                        'role': 'assistant',
                                                        'content': '',
                                                        'refusal': None
                                                    },
                                                    'logprobs': None,
                                                    'finish_reason': None
                                                }

                                            # finish_reason 업데이트
                                            if chunk_data['choices'][0].get('finish_reason'):
                                                full_response['choices'][0]['finish_reason'] = chunk_data['choices'][0]['finish_reason']

                                    except json.JSONDecodeError:
                                        continue

                    # 완성된 응답 생성
                    if full_response:
                        full_response['choices'][0]['message']['content'] = collected_content
                        return JSONResponse(
                            content=full_response,
                            headers={
                                'Access-Control-Allow-Origin': '*',
                                'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
                                'Access-Control-Allow-Headers': 'Authorization, Content-Type',
                                'Access-Control-Allow-Credentials': 'true',
                            }
                        )
                    else:
                        # 백업: 빈 응답
                        return JSONResponse(
                            content={"error": "No valid response received"},
                            status_code=500
                        )
            else:
                # 일반 응답 처리
                response = await self.http_client.post(url, json=request_data, headers=headers)
                return JSONResponse(
                    content=response.json(),
                    status_code=response.status_code,
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
                        'Access-Control-Allow-Headers': 'Authorization, Content-Type',
                        'Access-Control-Allow-Credentials': 'true',
                    }) # type: ignore
            
        except Exception as e:
            logger.error(f"Open WebUI 전달 실패: {e}")
            raise HTTPException(status_code=500, detail="Open WebUI 연결 실패")