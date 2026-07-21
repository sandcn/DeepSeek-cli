"""文本格式化工具 — 委托至 core/formatter.py

实现已下沉至 src.tui.core.formatter 以打破循环依赖，
此处保留 TextFormatter 类作为向后兼容的门面（Facade）。

本模块已下沉至 core 层以消除跨层横向依赖。
"""

from __future__ import annotations

from .formatter import (
    format_duration as _format_duration,
    format_token_count as _format_token_count,
    format_compact_speed as _format_compact_speed,
)


class TextFormatter:
    """文本格式化工具（门面，委托至 core/formatter.py）。"""

    @staticmethod
    def format_duration(seconds: float) -> str:
        return _format_duration(seconds)

    @staticmethod
    def format_token_count(tokens: int) -> str:
        return _format_token_count(tokens)

    @staticmethod
    def format_compact_speed(speed: float) -> str:
        return _format_compact_speed(speed)


__all__ = ["TextFormatter"]
