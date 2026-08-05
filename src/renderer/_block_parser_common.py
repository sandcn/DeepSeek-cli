"""_block_parser_common — RegexFreeBlockParser 共享常量与状态枚举。

从 _block_parser.py 拆分（2026-08-06 架构整理），供主体类与各 Mixin 共享。
"""

from __future__ import annotations

from enum import IntEnum


class _State(IntEnum):
    """解析器状态枚举。"""
    NORMAL = 0
    CODE_FENCE = 1
    MATH_BLOCK = 2
    DISPLAY_MATH_BLOCK = 3
    MERMAID_BLOCK = 4
    DETAILS_BLOCK = 5
    INDENTED_CODE = 6
    HTML_BLOCK = 7
    FENCED_DIV = 8
    TABLE_ACTIVE = 10
