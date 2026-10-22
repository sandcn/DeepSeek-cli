"""运行时数据路径常量 — 统一管理文件存储位置（用户主目录 ``~/.chat``）"""
from pathlib import Path

# 项目根目录（src/..），相对于 paths.py 位置定位。
# 注：运行时数据不再写项目目录，保留该常量仅供需要定位源码树的调用方使用。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 运行时数据根目录（用户主目录下，跨项目共用；首次写入时自动创建）
CHAT_DIR = Path.home() / ".chat"

# 聊天消息保存在 ~/.chat/msg_list/
CHAT_MSGS_DIR = CHAT_DIR / "msg_list"

# 跨对话记忆文件（由 MainAgent 手动维护）保存在 ~/.chat/memory/
CHAT_MEMORY_DIR = CHAT_DIR / "memory"

# 运行时自修改插件目录（cordis_define/run/stop/undefine 工具使用）
RUNTIME_PLUGINS_DIR = CHAT_DIR / "runtime_plugins"

# 图片附件存储（read_image 的 durable attachment：内容寻址、跨会话可复用）
CHAT_ATTACHMENTS_DIR = CHAT_DIR / "attachments"


def ensure_attachments_dir() -> None:
    """确保 ~/.chat/attachments/ 目录存在"""
    CHAT_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_runtime_plugins_dir() -> None:
    """确保 ~/.chat/runtime_plugins/ 目录存在"""
    RUNTIME_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_memory_dir() -> None:
    """确保 ~/.chat/memory/ 目录存在"""
    CHAT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def ensure_chat_msgs_dir() -> None:
    """确保 ~/.chat/msg_list/ 目录存在"""
    CHAT_MSGS_DIR.mkdir(parents=True, exist_ok=True)
