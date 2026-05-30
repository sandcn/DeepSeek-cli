"""项目路径常量 — 统一管理文件存储位置"""
from pathlib import Path

# 项目根目录（src/..），相对于 paths.py 位置定位
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# .chat 目录（项目根目录下）
CHAT_DIR = _PROJECT_ROOT / ".chat"

# 聊天消息保存在 .chat/msg_list/
CHAT_MSGS_DIR = CHAT_DIR / "msg_list"

# 跨对话记忆文件（由 MainAgent 手动维护）保存在 .chat/memory/
CHAT_MEMORY_DIR = CHAT_DIR / "memory"


def ensure_memory_dir() -> None:
    """确保 .chat/memory/ 目录存在"""
    CHAT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def ensure_chat_msgs_dir() -> None:
    """确保 .chat/msg_list/ 目录存在"""
    CHAT_MSGS_DIR.mkdir(parents=True, exist_ok=True)
