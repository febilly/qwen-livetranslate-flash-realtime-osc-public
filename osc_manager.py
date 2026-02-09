"""
OSC (Open Sound Control) 管理模块
负责处理VRChat的OSC通信，包括接收静音消息和发送聊天框消息
"""
import asyncio
import logging
import re
from pythonosc import udp_client
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

logger = logging.getLogger(__name__)

# 定义发送到VRChat聊天框的最大文本长度
MAX_LENGTH=144

class OSCManager:
    """OSC管理器单例类，负责OSC服务器和客户端的管理"""
    
    _instance = None
    _server = None
    _client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OSCManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._server = None
            self._client = None
            self._mute_callback = None  # 静音状态变化的回调函数
            self._line_breaks_enabled = False
            
            # OSC客户端配置（发送到VRChat）
            self._osc_client_host = "127.0.0.1"
            self._osc_client_port = 9000
            
            # OSC服务器配置（接收来自VRChat）
            self._osc_server_host = "127.0.0.1"
            self._osc_server_port = 9001
            
            logger.info("[OSC] OSC管理器已初始化")
    
    def set_mute_callback(self, callback):
        """
        设置静音状态变化的回调函数
        
        Args:
            callback: 回调函数，接收一个布尔参数 (mute_value)
                     当收到 MuteSelf=True 时调用 callback(True)
                     当收到 MuteSelf=False 时调用 callback(False)
        """
        self._mute_callback = callback
        logger.info("[OSC] 已设置静音状态回调函数")
    
    def clear_mute_callback(self):
        """清除静音状态回调函数"""
        self._mute_callback = None
        logger.info("[OSC] 已清除静音状态回调函数")
    
    def get_udp_client(self):
        """获取OSC UDP客户端实例（用于发送消息）"""
        if self._client is None:
            self._client = udp_client.SimpleUDPClient(
                self._osc_client_host,
                self._osc_client_port
            )
            logger.info(f"[OSC] OSC客户端已创建，目标地址: {self._osc_client_host}:{self._osc_client_port}")
        return self._client
    
    def _handle_mute_self(self, address, *args):
        """处理来自OSC的MuteSelf消息"""
        if args and len(args) > 0:
            mute_value = args[0]
            logger.info(f"[OSC] 收到MuteSelf消息: {mute_value}")
            
            # 如果设置了回调函数，则调用它
            if self._mute_callback is not None:
                try:
                    # 如果回调是协程函数，需要创建任务
                    if asyncio.iscoroutinefunction(self._mute_callback):
                        asyncio.create_task(self._mute_callback(mute_value))
                    else:
                        self._mute_callback(mute_value)
                except Exception as e:
                    logger.error(f"[OSC] 调用静音回调函数时出错: {e}")
            else:
                logger.debug(f"[OSC] 未设置静音回调函数，忽略MuteSelf消息")
    
    async def start_server(self):
        """启动OSC服务器监听（全局单例）"""
        if self._server is not None:
            logger.info("[OSC] OSC服务器已在运行中")
            return
        
        dispatcher = Dispatcher()
        dispatcher.map("/avatar/parameters/MuteSelf", self._handle_mute_self)
        
        self._server = AsyncIOOSCUDPServer(
            (self._osc_server_host, self._osc_server_port),
            dispatcher,
            asyncio.get_event_loop()
        )
        
        transport, protocol = await self._server.create_serve_endpoint()
        logger.info(f"[OSC] OSC服务器已启动，监听地址: {self._osc_server_host}:{self._osc_server_port}")
        return transport
    
    async def stop_server(self):
        """停止OSC服务器"""
        if self._server is not None:
            # 注意：AsyncIOOSCUDPServer 没有直接的关闭方法
            # 可以通过关闭transport来停止
            logger.info("[OSC] OSC服务器停止（如需要可实现）")
            self._server = None

    def set_line_breaks_enabled(self, enabled: bool):
        """设置是否启用分行逻辑。"""
        self._line_breaks_enabled = bool(enabled)
        logger.info(f"[OSC] 分行逻辑已{'启用' if self._line_breaks_enabled else '禁用'}")
    
    def _truncate_text(self, text: str, max_length: int = 144) -> str:
        """
        截断过长的文本，优先删除前面的句子
        
        Args:
            text: 需要截断的文本
            max_length: 最大长度限制
            
        Returns:
            截断后的文本
        """
        if len(text) <= max_length:
            return text
        
        # 句子结束标记
        SENTENCE_ENDERS = [
            '.', '?', '!', ',',           # Common
            '。', '？', '！', '，',        # CJK
            '…', '...', '‽',             # Stylistic & Special (includes 3-dot ellipsis)
            '։', '؟', ';', '،',           # Armenian, Arabic, Greek (as question mark), Arabic comma
            '।', '॥', '።', '။', '།',    # Indic, Ethiopic, Myanmar, Tibetan
            '、', '‚', '٫'               # Japanese enumeration comma, low comma, Arabic decimal separator
        ]
        
        # 当文本超长时，删除最前面的句子而不是截断末尾
        while len(text) > max_length:
            # 尝试找到第一个句子的结束位置
            first_sentence_end = -1
            for ender in SENTENCE_ENDERS:
                idx = text.find(ender)
                if idx != -1 and (first_sentence_end == -1 or idx < first_sentence_end):
                    first_sentence_end = idx
            
            if first_sentence_end != -1:
                # 删除第一个句子（包括标点符号后的空格）
                text = text[first_sentence_end + 1:].lstrip()
            else:
                # 如果没有找到标点符号，删除前面的字符直到长度合适
                text = text[len(text) - max_length:]
                break
        
        return text

    def _insert_newlines_after_sentence_enders(self, text: str) -> str:
        """在句末标点后插入换行符，并折叠标点后的空白。

        注意：这里不处理“省略号/三个点”断行。
        省略号用于“已确认/未确认”分隔时由上层逻辑统一放置，避免被重复插入换行。
        """
        if not text:
            return text

        # 统一换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 处理中/英文常见句末标点（不含 '.'，'.' 单独做启发式处理）
        text = re.sub(r"([。！？!?…])(?!\n)\s*", r"\1\n", text)

        # '.' 启发式：仅在其后是空白/结尾/闭合引号括号等时认为是句末
        # 这样可避免把 '3.14'、'example.com' 之类错误断行
        text = re.sub(
            r"(?<!\.)\.(?=(\s|$|[\"\'\)\]\}》」』”’]))(?!\n)\s*",
            ".\n",
            text,
        )

        return text

    def _split_confirmed_unconfirmed(self, text: str) -> tuple[str, str, str] | None:
        """按分隔符把文本拆成“已确认 + 分隔符 + 未确认”。

        当前约定：已确认和未确认通常用三个点 "..." 分隔（有时也可能是中文省略号“……”）。
        返回 (confirmed, delimiter, unconfirmed)，或 None（表示没有未确认段）。
        """
        if not text:
            return None

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 选择“最后一个”有效分隔符，避免正文里出现的省略号误触发。
        def find_last_delim(delim: str):
            idx = text.rfind(delim)
            if idx == -1:
                return -1
            left = text[:idx]
            right = text[idx + len(delim):]
            if left.strip() and right.strip():
                return idx
            return -1

        idx = find_last_delim("...")
        delim = "..."
        if idx == -1:
            idx = find_last_delim("……")
            delim = "……"

        if idx == -1:
            return None

        confirmed = text[:idx].rstrip()
        unconfirmed = text[idx + len(delim):].strip()
        return confirmed, delim, unconfirmed

    def _format_text_for_chatbox(self, text: str) -> str:
        """发送到聊天框前的统一格式化。

        规则：
        - 在句号/问号/感叹号等句末标点后换行（只作用于“已确认”部分）
        - 若存在“未确认”部分：未确认部分强制单独一行（保持单行，不做句末换行）
        - 分隔符（"..." 或 “……”）固定放在最后一个已确认句子的末尾
        - 确保分隔符不会被额外插入换行
        """
        if not text:
            return text

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 保护方括号内的未确认内容，避免在其中插入换行
        bracketed_segments: list[str] = []

        def _bracket_replacer(match: re.Match) -> str:
            content = match.group(0)
            # 折叠内部空白，确保未确认部分保持单行
            content = re.sub(r"\s+", " ", content).strip()
            bracketed_segments.append(content)
            return f"__BRACKETED_{len(bracketed_segments) - 1}__"

        protected_text = re.sub(r"\[[^\]]*\]", _bracket_replacer, text)

        split_res = self._split_confirmed_unconfirmed(protected_text)
        if split_res is None:
            formatted = self._insert_newlines_after_sentence_enders(protected_text)
            for idx, content in enumerate(bracketed_segments):
                formatted = formatted.replace(f"__BRACKETED_{idx}__", content)
            return formatted

        confirmed, delim, unconfirmed = split_res

        confirmed_formatted = self._insert_newlines_after_sentence_enders(confirmed).rstrip()

        # 未确认部分必须单行：把内部换行/多空白折叠成单空格
        unconfirmed_single_line = re.sub(r"\s+", " ", unconfirmed).strip()

        if not confirmed_formatted:
            # 极端情况：只有未确认（理论上不会），则不强行添加分隔符
            return unconfirmed_single_line

        if not unconfirmed_single_line:
            # 极端情况：没有未确认内容，视为普通文本
            formatted = confirmed_formatted
            for idx, content in enumerate(bracketed_segments):
                formatted = formatted.replace(f"__BRACKETED_{idx}__", content)
            return formatted

        # 这里明确只插入一个换行，用作“未确认单独一行”
        formatted = f"{confirmed_formatted}{delim}\n{unconfirmed_single_line}"
        for idx, content in enumerate(bracketed_segments):
            formatted = formatted.replace(f"__BRACKETED_{idx}__", content)
        return formatted
    
    async def send_text(self, text: str, ongoing: bool, enabled: bool = True):
        """
        发送文本到VRChat聊天框
        
        Args:
            text: 要发送的文本
            ongoing: 是否正在输入中
            enabled: 是否启用发送功能
        """
        if not enabled:
            return

        if self._line_breaks_enabled:
            # 发送前格式化：句末换行 + 未确认单独一行 + 省略号贴到确认句末
            # （并将换行符长度计入后续裁剪）
            text = self._format_text_for_chatbox(text)

        # 截断过长的文本（包含换行符在内的总长度限制）
        text = self._truncate_text(text, max_length=MAX_LENGTH)
        
        try:
            client = self.get_udp_client()
            client.send_message("/chatbox/typing", ongoing)
            client.send_message("/chatbox/input", [text, True, not ongoing])
        except Exception as e:
            logger.error(f"[OSC] 发送OSC消息失败: {e}")
        finally:
            # logger.info(f"[OSC] 发送聊天框消息: '{text}' (ongoing={ongoing})")
            pass


# 创建全局单例实例
osc_manager = OSCManager()
