"""微信 ClawBot 远程控制模块。

通过微信官方 iLink Bot API（ClawBot 插件协议）实现：
- 扫码登录微信 ClawBot
- 远程发送命令（/shell 等）与 AI 对话（复用 DeepSeek 会话引擎）
- 结果分段回显到微信，含"正在输入"状态与 24h 自动重连
"""

from .client import IlinkClient, extract_text, is_user_message
from .runner import ClawBotRunner, run_clawbot

__all__ = [
    "IlinkClient",
    "extract_text",
    "is_user_message",
    "ClawBotRunner",
    "run_clawbot",
]
