import os
import sys
import time
import base64
import asyncio
import logging
from pathlib import Path
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from web_translate_client import WebTranslateClient
from osc_manager import osc_manager

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.setLevel(logging.ERROR)  # 设置为ERROR以减少日志输出

app = FastAPI()


def _resource_path(*parts: str) -> Path:
    """Return an absolute path to a bundled resource (PyInstaller) or source file."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)

# 重连配置
MAX_RECONNECT_ATTEMPTS = 5
INITIAL_RECONNECT_DELAY = 1.0  # 秒
MAX_RECONNECT_DELAY = 30.0  # 秒
RECONNECT_BACKOFF_FACTOR = 2.0

# WebSocket心跳配置
HEARTBEAT_INTERVAL = 25  # 每25秒发送一次心跳
WEBSOCKET_TIMEOUT = 60   # WebSocket接收超时时间调整为60秒

class ReconnectManager:
    def __init__(self):
        self.reconnect_attempts = 0
        self.last_reconnect_time = 0
        self.is_reconnecting = False
    
    def should_reconnect(self, error_code: int = None) -> bool:
        """检查是否应该重连"""
        if self.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"达到最大重连次数 {MAX_RECONNECT_ATTEMPTS}，停止重连")
            return False
        
        # 专门检测1011错误代码
        if error_code == 1011:
            logger.warning(f"检测到1011内部服务器错误，准备重连 (尝试 {self.reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")
            return True
        
        # 也可以处理其他错误代码
        if error_code in [1006, 1011, 1012, 1013, 1014, 1015]:
            logger.warning(f"检测到错误代码 {error_code}，准备重连 (尝试 {self.reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")
            return True
        
        return False
    
    def get_reconnect_delay(self, error_code: int = None) -> float:
        """获取重连延迟时间（指数退避）"""
        if error_code == 1011:
            logger.info("检测到1011错误，立即重连")
            return 0.0
            
        delay = min(INITIAL_RECONNECT_DELAY * (RECONNECT_BACKOFF_FACTOR ** self.reconnect_attempts), MAX_RECONNECT_DELAY)
        logger.info(f"重连延迟: {delay:.2f}秒")
        return delay
    
    def increment_attempts(self):
        """增加重连尝试次数"""
        self.reconnect_attempts += 1
        self.last_reconnect_time = time.time()
    
    def reset(self):
        """重置重连状态"""
        self.reconnect_attempts = 0
        self.last_reconnect_time = 0
        self.is_reconnecting = False
        logger.info("重连状态已重置")

async def create_and_connect_client(
    api_key: str,
    target_language: str,
    voice: str,
    audio_enabled: bool,
    osc_mute_control: bool,
    send_to_osc: bool,
    on_text_callback
) -> WebTranslateClient:
    """创建并连接WebTranslateClient"""
    
    client = WebTranslateClient(
        api_key=api_key,
        target_language=target_language,
        voice=voice,
        audio_enabled=audio_enabled,
        osc_mute_control=osc_mute_control,
        send_to_osc=send_to_osc
    )
    
    await client.connect()
    logger.info(f"WebTranslateClient连接成功, 目标语言: {target_language}, 音色: {voice}")
    
    # 设置OSC静音回调函数
    async def handle_mute_change(mute_value: bool):
        """处理OSC静音状态变化"""
        # 检查是否启用了OSC静音控制
        if not client.osc_mute_control_enabled:
            logger.debug(f"[OSC] OSC静音控制已禁用，忽略MuteSelf={mute_value}消息")
            return
        
        if mute_value:
            # 收到True，暂停处理语音
            await client.pause_audio_processing()
            logger.info(f"[OSC] 收到MuteSelf=True，暂停处理语音数据")
        else:
            # 收到False，恢复处理语音
            client.resume_audio_processing()
            logger.info(f"[OSC] 收到MuteSelf=False，恢复处理语音数据")
    
    osc_manager.set_mute_callback(handle_mute_change)
    
    return client

async def stream_video_data_task(client: WebTranslateClient, video_queue: asyncio.Queue):
    """从队列中获取视频数据并发送"""
    while True:
        try:
            frame = await video_queue.get()
            if frame is None:
                break
            
            await client.send_image_frame(frame)
            logger.debug(f"发送视频数据: {len(frame)} bytes")
            video_queue.task_done()
        except Exception as e:
            logger.error(f"发送视频数据时出错: {e}")

async def send_heartbeat(websocket: WebSocket):
    """发送心跳包任务"""
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await websocket.send_text("ping")
            logger.debug("发送心跳包")
        except Exception as e:
            logger.error(f"发送心跳包失败: {e}")
            break

@app.get("/")
async def get():
    try:
        index_path = _resource_path("static", "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception as e:
        logger.error(f"读取index.html失败: {e}")
        return HTMLResponse("<h1>服务器错误</h1>", status_code=500)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, 
                           target_language: str = "en", 
                           voice: str = "Cherry", 
                           audio_enabled: bool = True,
                           osc_mute_control: bool = True,
                           send_to_osc: bool = True,
                           line_breaks_enabled: bool = False):
    await websocket.accept()
    logger.info(f"WebSocket连接已建立, 目标语言: {target_language}, 音色: {voice}, 音频: {audio_enabled}, OSC静音控制: {osc_mute_control}, 发送到OSC: {send_to_osc}, 分行逻辑: {line_breaks_enabled}")
    
    # 确保OSC服务器已启动（只启动一次）
    await osc_manager.start_server()
    osc_manager.set_line_breaks_enabled(line_breaks_enabled)
    
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        logger.error("DASHSCOPE_API_KEY环境变量未设置")
        await websocket.close(code=1008, reason="API密钥未配置")
        return
    
    reconnect_manager = ReconnectManager()
    client = None
    message_task = None
    video_sender_task = None
    websocket_active = True
    video_queue = asyncio.Queue()
    
    # 获取当前事件循环（用于跨线程回调）
    loop = asyncio.get_running_loop()

    # Start heartbeat task once
    heartbeat_task = asyncio.create_task(send_heartbeat(websocket))
    logger.info("心跳任务已启动")
    
    def on_text_received(text: str):
        """处理接收到的翻译文本"""
        nonlocal websocket_active
        if not websocket_active:
            return
        try:
            import time
            timestamp = time.strftime("%H:%M:%S")
            # 在同一事件循环中安全调度
            asyncio.create_task(
                websocket.send_json({"type": "translation_text", "data": text})
            )
            logger.info(f"[{timestamp}] 服务端发送翻译文本: {text}")
        except Exception as e:
            logger.error(f"发送翻译文本失败: {e}")
            websocket_active = False

    try:
        while websocket_active:
            try:
                # 1. Create client if it doesn't exist
                if client is None:
                    logger.info("尝试创建并连接WebTranslateClient...")
                    
                    # 创建音频数据回调函数（用于发送音频给浏览器）
                    async def on_audio_received(audio_data: bytes):
                        nonlocal websocket_active
                        if not websocket_active:
                            return
                        try:
                            import time
                            timestamp = time.strftime("%H:%M:%S")
                            # 直接发送音频数据给浏览器
                            await websocket.send_bytes(audio_data)
                            logger.info(f"[{timestamp}] 服务端发送音频数据到浏览器: {len(audio_data)} bytes")
                        except Exception as e:
                            logger.error(f"发送音频数据失败: {e}")
                            websocket_active = False
                    
                    client = await create_and_connect_client(
                        api_key, target_language, voice, audio_enabled, osc_mute_control, send_to_osc, on_text_received
                    )
                    message_task = asyncio.create_task(client.handle_server_messages(on_text_received, on_audio_received))
                    video_sender_task = asyncio.create_task(stream_video_data_task(client, video_queue))
                    reconnect_manager.reset()
                    logger.info("WebTranslateClient已连接并准备就绪")

                # 2. Main message processing loop
                while websocket_active:
                    message = await asyncio.wait_for(websocket.receive(), timeout=WEBSOCKET_TIMEOUT)
                    
                    if message['type'] == 'websocket.receive':
                        if 'bytes' in message:
                            data = message['bytes']
                            if not data: continue
                            
                            stream_type = data[0]
                            content = data[1:]
                            
                            import time
                            timestamp = time.strftime("%H:%M:%S")
                            
                            if stream_type == 0:  # audio
                                await client.send_audio_chunk(content)
                                # logger.info(f"[{timestamp}] 服务端接收音频数据并发送到模型: {len(content)} bytes")
                            elif stream_type == 1:  # video
                                await video_queue.put(content)
                                logger.info(f"[{timestamp}] 服务端接收视频数据并入队: {len(content)} bytes")
                            else:
                                # 未知的二进制帧类型
                                logger.warning(f"[{timestamp}] 未知二进制流类型: {stream_type}, 长度: {len(content)} bytes")

                        elif 'text' in message:
                            text_data = message['text']
                            if text_data == "pong":
                                logger.debug("收到心跳回应")
                            else:
                                logger.info(f"收到文本消息: {text_data}")
                                # 处理前端的会话更新指令: {type:'session.update', target_language, voice, audio_enabled}
                                try:
                                    import json as _json
                                    payload = _json.loads(text_data)
                                    if isinstance(payload, dict):
                                        if payload.get('type') == 'session.update':
                                            lang = payload.get('target_language')
                                            voice = payload.get('voice')
                                            audio_enabled = payload.get('audio_enabled')
                                            if client:
                                                await client.update_session(
                                                    target_language=lang,
                                                    voice=voice,
                                                    audio_enabled=audio_enabled
                                                )
                                                logger.info(f"已下发会话更新: lang={lang}, voice={voice}, audio={audio_enabled}")
                                        elif payload.get('type') == 'format.update':
                                            line_breaks_enabled = bool(payload.get('line_breaks_enabled', False))
                                            osc_manager.set_line_breaks_enabled(line_breaks_enabled)
                                            logger.info(f"已更新分行逻辑: {line_breaks_enabled}")
                                except Exception as _e:
                                    logger.debug(f"解析/处理文本消息失败或非更新消息: {_e}")

                    elif message['type'] == 'websocket.disconnect':
                        logger.info("收到断开连接消息")
                        websocket_active = False
                        break 
                else: # TimeoutError
                    logger.debug("WebSocket接收超时，继续等待...")
                    continue 
            
            except Exception as e:
                logger.error(f"处理数据时发生错误: {e}")
                
                error_code = getattr(e, 'code', None)
                if hasattr(e, 'args') and len(e.args) > 0:
                    error_str = str(e.args[0])
                    if '1011' in error_str:
                        error_code = 1011
                
                if reconnect_manager.should_reconnect(error_code):
                    reconnect_manager.increment_attempts()
                    delay = reconnect_manager.get_reconnect_delay(error_code)
                    
                    # Clean up for reconnection
                    if message_task and not message_task.done():
                        message_task.cancel()
                    if video_sender_task:
                        await video_queue.put(None)
                        video_sender_task.cancel()
                    if client:
                        await client.close()
                        client = None
                    
                    if delay > 0:
                        logger.info(f"等待 {delay:.2f}秒后重连...")
                        await asyncio.sleep(delay)
                    continue 
                else:
                    logger.error("发生不可恢复的错误或达到最大重连次数，关闭连接")
                    websocket_active = False
                    break

    finally:
        logger.info("开始最终清理...")
        
        # 清除OSC回调
        osc_manager.clear_mute_callback()
        
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
        if message_task and not message_task.done():
            message_task.cancel()
        if video_sender_task and not video_sender_task.done():
            video_sender_task.cancel()
        if client:
            try:
                await client.close()
            except Exception:
                pass

        logger.info("清理完成，连接已关闭")

def run_server():
    """启动服务器"""
    logger.info("启动HTTP服务器...")
    uvicorn.run(app, host="0.0.0.0", port=19023, log_level="info")

if __name__ == "__main__":
    run_server()
