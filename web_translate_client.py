import os
import time
import base64
import asyncio
import json
import websockets
import traceback
from osc_manager import osc_manager

class WebTranslateClient:
    """专门用于Web环境的翻译客户端，不依赖pyaudio"""
    
    def __init__(self, api_key: str, target_language: str = "en", voice: str | None = "Cherry", *, audio_enabled: bool = True, osc_mute_control: bool = True, send_to_osc: bool = True):
        if not api_key:
            raise ValueError("API key cannot be empty.")
            
        self.api_key = api_key
        self.target_language = target_language
        self.audio_enabled = audio_enabled
        self.voice = voice if audio_enabled else "Cherry"
        self.ws = None
        self.api_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-livetranslate-flash-realtime"
        
        # 音频配置（仅用于配置，不需要pyaudio）
        self.input_rate = 16000
        self.input_channels = 1
        self.output_rate = 24000
        self.output_channels = 1
        
        # 状态管理
        self.is_connected = False
        
        # 语音处理控制：默认为True表示处理语音数据
        self.is_processing_audio = True
        
        # OSC静音控制开关：控制是否响应OSC静音消息
        self.osc_mute_control_enabled = osc_mute_control
        
        # OSC发送开关：控制是否发送翻译结果到OSC
        self.send_to_osc_enabled = send_to_osc
        
        # 翻译耗时统计
        self.translation_start_time = None

    async def pause_audio_processing(self):
        """暂停处理语音数据"""
        if self.is_processing_audio:  # 只在处理中时才执行暂停逻辑
            self.is_processing_audio = False
            # 记录翻译开始时间
            import time
            self.translation_start_time = time.time()
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp}] [WebTranslateClient] ⏱️ 开始翻译计时")
            print(f"[WebTranslateClient] 暂停处理语音数据")
            
            # 发送1秒的空白音频表示语音结束
            await self._send_silence(duration_seconds=3.0)

    def resume_audio_processing(self):
        """恢复处理语音数据"""
        self.is_processing_audio = True
        print(f"[WebTranslateClient] 恢复处理语音数据")
    
    def update_token_usage(self, usage: dict):
        """更新token使用统计
        
        Args:
            usage: 包含token使用信息的字典
        """
        if not usage:
            return
        
        # 提取本次使用的token数
        total = usage.get("total_tokens", 0)
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        
        # 输入token详情
        input_details = usage.get("input_tokens_details", {})
        input_text = input_details.get("text_tokens", 0)
        input_audio = input_details.get("audio_tokens", 0)
        
        # 输出token详情
        output_details = usage.get("output_tokens_details", {})
        output_text = output_details.get("text_tokens", 0)
        output_audio = output_details.get("audio_tokens", 0)
        
        # 输出统计信息
        import time
        timestamp = time.strftime("%H:%M:%S")
        # 阿里云怎么又偷改api了？？
        # print(f"[{timestamp}] 📊 累计: 输入{input_tokens}(文本{input_text}+语音{input_audio}) 输出{output_tokens}(文本{output_text}+语音{output_audio}) 总计{total}")
    
    async def _send_silence(self, duration_seconds: float = 1.0):
        """发送空白音频数据
        
        Args:
            duration_seconds: 空白音频的时长（秒）
        """
        if not self.is_connected or not self.ws:
            return
        
        try:
            # 计算需要的样本数量
            # input_rate = 16000 Hz, 1 channel, 16-bit (2 bytes per sample)
            num_samples = int(self.input_rate * duration_seconds)
            silence_data = b'\x00' * (num_samples * 2)  # 2 bytes per sample
            
            import time
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp}] 发送{duration_seconds}秒空白音频 ({len(silence_data)} bytes)")
            
            event = {
                "event_id": f"event_{int(time.time() * 1000)}",
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(silence_data).decode()
            }
            await self.ws.send(json.dumps(event))
        except Exception as e:
            print(f"发送空白音频失败: {e}")

    async def send_osc_text(self, text: str, ongoing: bool):
        """发送文本到OSC聊天框"""
        # 如果是发送最终结果（不是进行中），计算耗时
        if not ongoing and self.translation_start_time is not None:
            import time
            elapsed_time = time.time() - self.translation_start_time
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp}] ✅ 翻译完成 - 耗时: {elapsed_time:.2f}秒")
            self.translation_start_time = None
        
        await osc_manager.send_text(text, ongoing, self.send_to_osc_enabled)

    async def connect(self):
        """建立到翻译服务的 WebSocket 连接。"""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            self.ws = await websockets.connect(self.api_url, additional_headers=headers)
            self.is_connected = True
            pass # print(f"成功连接到服务器: {self.api_url}")
            await self.configure_session()
        except Exception as e:
            pass # print(f"连接失败: {e}")
            self.is_connected = False
            raise

    async def configure_session(self):
        """配置翻译会话，设置目标语言、声音等。"""
        config = {
            "event_id": f"event_{int(time.time() * 1000)}",
            "type": "session.update",
            "session": {
                # 'modalities' 控制输出类型。
                # ["text", "audio"]: 同时返回翻译文本和合成音频（推荐）。
                # ["text"]: 仅返回翻译文本。
                "modalities": ["text", "audio"] if self.audio_enabled else ["text"],
                **({"voice": self.voice} if self.audio_enabled and self.voice else {}),
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "translation": {
                    "language": self.target_language
                }
            }
        }
        pass # print(f"发送会话配置: {json.dumps(config, indent=2, ensure_ascii=False)}")
        await self.ws.send(json.dumps(config))

    async def update_session(self, *, target_language: str | None = None, voice: str | None = None, audio_enabled: bool | None = None):
        """动态更新会话配置（语言/音色/输出通道）。"""
        if target_language is not None:
            self.target_language = target_language
        if voice is not None:
            self.voice = voice
        if audio_enabled is not None:
            self.audio_enabled = audio_enabled

        config = {
            "event_id": f"event_{int(time.time() * 1000)}",
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"] if self.audio_enabled else ["text"],
                **({"voice": self.voice} if self.audio_enabled and self.voice else {}),
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "translation": {
                    "language": self.target_language
                }
            }
        }
        pass # print(f"[update_session] 发送会话更新: {json.dumps(config, indent=2, ensure_ascii=False)}")
        await self.ws.send(json.dumps(config))

    async def send_audio_chunk(self, audio_data: bytes):
        """将音频数据块编码并发送到服务器。"""
        if not self.is_connected:
            return
        
        # 检查是否正在处理语音数据
        if not self.is_processing_audio:
            # 不处理的话，直接丢弃新的数据段
            import time
            timestamp = time.strftime("%H:%M:%S")
            pass # print(f"[{timestamp}] 语音处理已暂停，丢弃音频数据: {len(audio_data)} bytes")
            return
        
        import time    
        timestamp = time.strftime("%H:%M:%S")
        pass # print(f"[{timestamp}] WebTranslateClient发送音频到模型: {len(audio_data)} bytes")
            
        event = {
            "event_id": f"event_{int(time.time() * 1000)}",
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(audio_data).decode()
        }
        await self.ws.send(json.dumps(event))

    async def send_image_frame(self, image_bytes: bytes, *, event_id: str | None = None):
        """将单帧图像数据发送到服务器。

        约束:
        1. 图像格式: JPG/JPEG，推荐分辨率 480p/720p，最大 1080p。
        2. 单张大小 ≤ 500KB。
        3. 数据须使用 Base64 编码。
        4. 建议发送频率: 2 张/秒。
        5. 先发送音频，再发送图像。
        6. 成对使用 input_audio_buffer.commit 提交视频缓冲区。
        """

        if not self.is_connected:
            return

        if not image_bytes:
            raise ValueError("image_bytes 不能为空")

        # 编码为 Base64
        image_b64 = base64.b64encode(image_bytes).decode()

        event = {
            "event_id": event_id or f"event_{int(time.time() * 1000)}",
            "type": "input_image_buffer.append",
            "image": image_b64,
        }

        await self.ws.send(json.dumps(event))

    async def handle_server_messages(self, on_text_received, on_audio_received=None):
        """循环处理来自服务器的消息。
        
        Args:
            on_text_received: 文本回调函数
            on_audio_received: 音频回调函数（可选，用于Web环境）
        """
        try:
            async for message in self.ws:
                import time
                timestamp = time.strftime("%H:%M:%S")
                # 兼容文本/二进制消息
                if isinstance(message, (bytes, bytearray)):
                    try:
                        message = message.decode('utf-8', errors='ignore')
                    except Exception:
                        continue
                
                event = json.loads(message)
                event_type = event.get("type")
                pass # print(f"[{timestamp}] WebTranslateClient接收到事件: {event_type}")
                
                if event_type == "response.audio_transcript.delta":
                    text = event.get("transcript", "")
                    pass # print(f"[{timestamp}] 接收到翻译文本片段: '{text}'")
                    if text and on_text_received:
                        on_text_received(text)
                elif event_type == "response.text.delta":
                    text = event.get("delta", "")
                    pass # print(f"[{timestamp}] 接收到文本delta: '{text}'")
                    if text and on_text_received:
                        on_text_received(text)
                elif event_type == "response.output_text.delta":
                    text = event.get("delta", "")
                    pass # print(f"[{timestamp}] 接收到output_text delta: '{text}'")
                    if text and on_text_received:
                        on_text_received(text)
                
                elif event_type == "response.audio.delta" and self.audio_enabled:
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        audio_data = base64.b64decode(audio_b64)
                        pass # print(f"[{timestamp}] 接收到音频数据: {len(audio_data)} bytes (base64长度: {len(audio_b64)})")
                        if on_audio_received:
                            await on_audio_received(audio_data)
                        else:
                            pass # print(f"[{timestamp}] 警告：没有音频回调函数")
                    else:
                        pass # print(f"[{timestamp}] 音频delta为空")

                elif event_type == "response.done":
                    pass # print(f"[{timestamp}] 一轮响应完成。")
                    usage = event.get("response", {}).get("usage", {})
                    if usage:
                        # 更新并显示token统计
                        self.update_token_usage(usage)
                        
                elif event_type == "response.audio_transcript.done":
                    pass # print(f"[{timestamp}] 翻译文本完成。")
                    text = event.get("transcript", "")
                    if text:
                        print(f"[{timestamp}] audio_transcript 完整翻译文本: {text}")
                        if on_text_received:
                            on_text_received(text)
                        
                elif event_type == "response.text.done":
                    pass # print(f"[{timestamp}] 翻译文本完成。")
                    text = event.get("text", "")
                    if text:
                        print(f"[{timestamp}] {text}")
                        await self.send_osc_text(text, False)  # 发送到OSC                            
                        if on_text_received:
                            on_text_received(text)
                # 删除重复分支：已在上方统一处理 response.audio_transcript.done
                        
                elif event_type == "session.updated":
                    pass # print(f"[{timestamp}] 会话配置已更新")
                    
                else:
                    pass # print(f"[{timestamp}] 未处理的事件类型: {event_type}")
                    # if len(str(event)) < 500:  # 只打印短消息
                        # print(f"[{timestamp}] 事件内容: {event}")
                    
                    # 不完整的识别
                    if 'text' in event:
                        # 拼接结果
                        result = event['text']
                        if event['stash'].startswith(' '):
                            result += f" ... [{event['stash'][1:]}]"
                        else:
                            result += f" ... [{event['stash']}]"

                        print(f"[{timestamp}] {result}")
                        await self.send_osc_text(result, True)  # 发送到OSC                            
                            
        except websockets.exceptions.ConnectionClosed as e:
            pass # print(f"[WARNING] 连接已关闭: {e}")
            self.is_connected = False
        except Exception as e:
            pass # print(f"[ERROR] 消息处理时发生未知错误: {e}")
            traceback.print_exc()
            self.is_connected = False

    async def close(self):
        """优雅地关闭连接和资源。"""
        self.is_connected = False
        if self.ws:
            await self.ws.close()
            pass # print("WebSocket 连接已关闭。")
