"""统计端口 — StatsPort

核心层通过此接口收集和查询 token 统计数据，
不直接依赖 src/api/stats。
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
    def get_session_start_time(self) -> float:
        """获取会话开始时间"""
        ...

    @abstractmethod
    def snapshot(self) -> dict:
        """获取统计快照"""
        ...

    @abstractmethod
    def reset(self) -> None:
        """重置统计"""
        ...


class DefaultStatsAdapter(StatsPort):
    """默认统计适配器 — 包装 src/api/stats"""

    def accumulate_usage(self, input_tokens: int, output_tokens: int) -> None:
        from ...api.stats import accumulate_usage
        accumulate_usage(input_tokens, output_tokens)

    def set_tool_parse_elapsed(self, elapsed: float) -> None:
        from ...api.stats import set_tool_parse_elapsed
        set_tool_parse_elapsed(elapsed)

    def set_stream_speed(self, speed: float) -> None:
        from ...api.stats import set_stream_speed
        set_stream_speed(speed)

    def get_total_input_tokens(self) -> int:
        from ...api.stats import get_total_input_tokens
        return get_total_input_tokens()

    def get_total_output_tokens(self) -> int:
        from ...api.stats import get_total_output_tokens
        return get_total_output_tokens()

    def get_token_speed(self) -> float:
        from ...api.stats import get_token_speed
        return get_token_speed()

    def get_avg_token_speed(self) -> float:
        from ...api.stats import get_avg_token_speed
        return get_avg_token_speed()

    def get_short_window_speed(self) -> float:
        from ...api.stats import get_short_window_speed
        return get_short_window_speed()

    def get_session_start_time(self) -> float:
        from ...api.stats import get_session_start_time
        return get_session_start_time()

    def snapshot(self) -> dict:
        from ...api.stats import get_token_stats
        result = get_token_stats()
        return dict(result) if isinstance(result, dict) else {}

    def reset(self) -> None:
        from ...api.stats import reset_stats
        reset_stats()


class MockStatsAdapter(StatsPort):
    """Mock 统计适配器 — 用于测试"""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_parse_elapsed = 0.0
        self.stream_speed = 0.0
        self._speeds: list[float] = []

    def accumulate_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def set_tool_parse_elapsed(self, elapsed: float) -> None:
        self.tool_parse_elapsed = elapsed

    def set_stream_speed(self, speed: float) -> None:
        self.stream_speed = speed
        self._speeds.append(speed)

    def get_total_input_tokens(self) -> int:
        return self.input_tokens

    def get_total_output_tokens(self) -> int:
        return self.output_tokens

    def get_token_speed(self) -> float:
        return self.stream_speed

    def get_avg_token_speed(self) -> float:
        if not self._speeds:
            return 0.0
        return sum(self._speeds) / len(self._speeds)

    def get_short_window_speed(self) -> float:
        window = self._speeds[-5:] if len(self._speeds) > 5 else self._speeds
        if not window:
            return 0.0
        return sum(window) / len(window)

    def get_session_start_time(self) -> float:
        return 0.0

    def snapshot(self) -> dict:
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "speed": self.stream_speed,
            "avg_speed": self.get_avg_token_speed(),
        }

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_parse_elapsed = 0.0
        self.stream_speed = 0.0
        self._speeds.clear()
