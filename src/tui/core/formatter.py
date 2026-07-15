"""文本格式化工具函数 — 零依赖核心层，避免循环导入。

从 parallel/_text_formatter.py 的 TextFormatter 类提取纯格式化函数，
下沉到 core 层以消除循环依赖（cost → parallel → frame → components → cost）。

所有函数为纯函数，无副作用，无 I/O，不依赖任何外部模块。
"""
from tui_framework.core.formatter import *

__all__ = [
    "format_duration",
    "format_token_count",
    "format_compact_speed",
]
