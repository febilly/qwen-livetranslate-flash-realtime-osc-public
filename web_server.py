import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from web_translate_client import WebTranslateClient

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.setLevel(logging.ERROR)  # 设置为ERROR以减少日志输出


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

        if error_code == 1011:
            logger.warning(f"检测到1011内部服务器错误，准备重连 (尝试 {self.reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS})")
            return True

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
    voice_clone_frequency: str | None,
    source_language: str | None = None,
) -> WebTranslateClient:
    """创建并连接WebTranslateClient"""
    client = WebTranslateClient(
        api_key=api_key,
        target_language=target_language,
        voice=voice,
        audio_enabled=audio_enabled,
        voice_clone_frequency=voice_clone_frequency,
        source_language=source_language,
    )

    await client.connect()
    logger.info(f"WebTranslateClient连接成功, 目标语言: {target_language}, 音色: {voice}, 源语言: {source_language}")
    return client


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


async def receive_start_config(websocket: WebSocket) -> dict:
    """等待前端发送初始配置，API Key 由前端提供。"""
    raw_message = await asyncio.wait_for(websocket.receive_text(), timeout=WEBSOCKET_TIMEOUT)
    payload = json.loads(raw_message)
    if not isinstance(payload, dict) or payload.get("type") != "connection.start":
        raise ValueError("首条消息必须是 connection.start 配置")

    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        raise ValueError("API密钥未填写")

    return payload


async def get(request):
    try:
        index_path = _resource_path("static", "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception as e:
        logger.error(f"读取index.html失败: {e}")
        return HTMLResponse("<h1>服务器错误</h1>", status_code=500)


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket连接已建立，等待前端配置")

    reconnect_manager = ReconnectManager()
    client = None
    message_task = None
    websocket_active = True
    start_config = None

    heartbeat_task = asyncio.create_task(send_heartbeat(websocket))
    logger.info("心跳任务已启动")

    def on_text_received(text: str, is_final: bool = False):
        """处理接收到的翻译文本"""
        nonlocal websocket_active
        if not websocket_active:
            return
        try:
            timestamp = time.strftime("%H:%M:%S")
            asyncio.create_task(
                websocket.send_json({"type": "translation_text", "data": text, "is_final": is_final})
            )
            logger.info(f"[{timestamp}] 服务端发送翻译文本: {text} (is_final: {is_final})")
        except Exception as e:
            logger.error(f"发送翻译文本失败: {e}")
            websocket_active = False

    def on_asr_received(text: str, is_final: bool = False):
        """处理接收到的 ASR (说话人原文) 文本"""
        nonlocal websocket_active
        if not websocket_active:
            return
        try:
            timestamp = time.strftime("%H:%M:%S")
            asyncio.create_task(
                websocket.send_json({"type": "asr_text", "data": text, "is_final": is_final})
            )
            logger.info(f"[{timestamp}] 服务端发送 ASR 文本: {text} (is_final: {is_final})")
        except Exception as e:
            logger.error(f"发送 ASR 文本失败: {e}")
            websocket_active = False

    async def on_audio_received(audio_data: bytes):
        nonlocal websocket_active
        if not websocket_active:
            return
        try:
            timestamp = time.strftime("%H:%M:%S")
            await websocket.send_bytes(audio_data)
            logger.info(f"[{timestamp}] 服务端发送音频数据到浏览器: {len(audio_data)} bytes")
        except Exception as e:
            logger.error(f"发送音频数据失败: {e}")
            websocket_active = False

    async def connect_model_client():
        config = start_config or {}
        model_client = await create_and_connect_client(
            api_key=str(config.get("api_key", "")).strip(),
            target_language=config.get("target_language", "en"),
            voice=config.get("voice", "Tina"),
            audio_enabled=bool(config.get("audio_enabled", True)),
            voice_clone_frequency=config.get("voice_clone_frequency") or None,
            source_language=config.get("source_language") or None,
        )
        if config.get("mic_mode") == "push_to_talk":
            await model_client.pause_audio_processing()
        return model_client

    try:
        try:
            start_config = await receive_start_config(websocket)
        except Exception as e:
            logger.error(f"接收初始配置失败: {e}")
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close(code=1008, reason=str(e))
            return

        while websocket_active:
            try:
                if client is None:
                    logger.info("尝试创建并连接WebTranslateClient...")
                    client = await connect_model_client()
                    message_task = asyncio.create_task(client.handle_server_messages(on_text_received, on_audio_received, on_asr_received))
                    reconnect_manager.reset()
                    await websocket.send_json({"type": "ready"})
                    logger.info("WebTranslateClient已连接并准备就绪")

                while websocket_active:
                    message = await asyncio.wait_for(websocket.receive(), timeout=WEBSOCKET_TIMEOUT)

                    if message["type"] == "websocket.receive":
                        if "bytes" in message:
                            data = message["bytes"]
                            if not data:
                                continue

                            stream_type = data[0]
                            content = data[1:]
                            timestamp = time.strftime("%H:%M:%S")

                            if stream_type == 0:
                                await client.send_audio_chunk(content)
                            else:
                                logger.warning(f"[{timestamp}] 未知二进制流类型: {stream_type}, 长度: {len(content)} bytes")

                        elif "text" in message:
                            text_data = message["text"]
                            if text_data == "pong":
                                logger.debug("收到心跳回应")
                                continue

                            logger.info(f"收到文本消息: {text_data}")
                            try:
                                payload = json.loads(text_data)
                                if not isinstance(payload, dict):
                                    continue

                                if payload.get("type") == "session.update":
                                    await client.update_session(
                                        target_language=payload.get("target_language"),
                                        voice=payload.get("voice"),
                                        audio_enabled=payload.get("audio_enabled"),
                                        voice_clone_frequency=payload.get("voice_clone_frequency", ""),
                                    )
                                    logger.info("已下发会话更新")
                                elif payload.get("type") == "audio.processing":
                                    if bool(payload.get("active")):
                                        client.resume_audio_processing()
                                    else:
                                        await client.pause_audio_processing()
                            except Exception as e:
                                logger.debug(f"解析/处理文本消息失败或非更新消息: {e}")

                    elif message["type"] == "websocket.disconnect":
                        logger.info("收到断开连接消息")
                        websocket_active = False
                        break

            except asyncio.TimeoutError:
                logger.debug("WebSocket接收超时，继续等待...")
                continue
            except Exception as e:
                logger.error(f"处理数据时发生错误: {e}")

                error_code = getattr(e, "code", None)
                if hasattr(e, "args") and len(e.args) > 0 and "1011" in str(e.args[0]):
                    error_code = 1011

                if reconnect_manager.should_reconnect(error_code):
                    reconnect_manager.increment_attempts()
                    delay = reconnect_manager.get_reconnect_delay(error_code)

                    if message_task and not message_task.done():
                        message_task.cancel()
                    if client:
                        await client.close()
                        client = None

                    if delay > 0:
                        logger.info(f"等待 {delay:.2f}秒后重连...")
                        await asyncio.sleep(delay)
                    continue

                logger.error("发生不可恢复的错误或达到最大重连次数，关闭连接")
                websocket_active = False
                break

    finally:
        logger.info("开始最终清理...")

        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
        if message_task and not message_task.done():
            message_task.cancel()
        if client:
            try:
                await client.close()
            except Exception:
                pass

        logger.info("清理完成，连接已关闭")


app = Starlette(routes=[
    Route("/", get),
    WebSocketRoute("/ws", websocket_endpoint),
])


def run_server():
    """启动服务器"""
    logger.info("启动HTTP服务器...")
    uvicorn.run(app, host="0.0.0.0", port=9023, log_level="info")


if __name__ == "__main__":
    run_server()
