"""统计端口 — StatsPort

适配器实现已移至 src.core.adapters.stats。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class StatsPort(ABC):
    """统计端口 — token 用量与速度统计"""

    @abstractmethod
    def accumulate_usage(self, input_tokens: int, output_tokens: int) -> None:
        """累加 token 用量"""
        ...

    @abstractmethod
    def set_tool_parse_elapsed(self, elapsed: float) -> None:
        """设置工具解析耗时"""
        ...

    @abstractmethod
    def set_stream_speed(self, speed: float) -> None:
        """设置流式输出速度（字符/秒）"""
        ...

    @abstractmethod
    def get_total_input_tokens(self) -> int:
        """获取总输入 token 数"""
        ...

    @abstractmethod
    def get_total_output_tokens(self) -> int:
        """获取总输出 token 数"""
        ...

    @abstractmethod
    def get_token_speed(self) -> float:
        """获取 token 速度（字符/秒）"""
        ...

    @abstractmethod
    def get_avg_token_speed(self) -> float:
        """获取平均 token 速度"""
        ...

    @abstractmethod
    def get_short_window_speed(self) -> float:
        """获取短窗口速度"""
        ...

    @abstractmethod
    def snapshot(self) -> dict:
        """获取统计快照"""
        ...

    @abstractmethod
    def reset(self) -> None:
        """重置统计"""
        ...
