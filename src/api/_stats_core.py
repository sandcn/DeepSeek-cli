"""会话级 token 统计核心 — _Stats 类 + 模块级单例。"""

import time
import threading


class _Stats:
    """会话级统计状态封装。"""

    __slots__ = (
        "_lock",
        "token_stats",
        "session_start_time",
        "last_tool_parse_elapsed",
        "last_stream_speed",
    )

    def __init__(self):
        # ── 通用锁（保护所有字段）─────────────────────────
        self._lock = threading.Lock()

        # ── 会话级 token 统计 ──────────────────────────────
        self.token_stats = {"input": 0, "output": 0, "calls": 0}
        self.session_start_time = time.time()

        # ── 上次工具调用解析耗时（供 agent 侧显示用）───────
        self.last_tool_parse_elapsed = 0.0

        # ── 上次流式输出速度（供 agent 侧显示用）───────────
        self.last_stream_speed = 0.0

    def accumulate_usage(self, usage):
        """线程安全地累加一次 API 调用的 usage 到全局统计。"""
        with self._lock:
            self.token_stats["input"] += usage.get("input", 0)
            self.token_stats["output"] += usage.get("output", 0)
            self.token_stats["calls"] += 1

    def set_tool_parse_elapsed(self, elapsed):
        """线程安全地更新上次工具调用解析耗时。"""
        with self._lock:
            self.last_tool_parse_elapsed = elapsed

    def set_stream_speed(self, speed):
        """线程安全地更新上次流式输出速度。"""
        with self._lock:
            self.last_stream_speed = speed

    def get_last_tool_parse_elapsed(self) -> float:
        """线程安全地获取上次工具调用解析耗时。"""
        with self._lock:
            return self.last_tool_parse_elapsed

    def get_last_stream_speed(self) -> float:
        """线程安全地获取上次流式输出速度。"""
        with self._lock:
            return self.last_stream_speed

    def get_stats_snapshot(self):
        """返回 token_stats 的快照副本（线程安全）。"""
        with self._lock:
            return dict(self.token_stats)


# ── 模块级单例 ────────────────────────────────────────────
_stats = _Stats()


# ── 模块级读取接口（从 _stats 实例实时读取）──────────────
def get_session_start_time():
    """返回会话开始时间（从 _stats 实例实时读取）。"""
    return _stats.session_start_time


def get_token_stats():
    """返回 token_stats 的快照副本（线程安全）。"""
    return _stats.get_stats_snapshot()


def get_last_tool_parse_elapsed():
    """返回上次工具调用解析耗时（线程安全）。"""
    return _stats.get_last_tool_parse_elapsed()


def get_last_stream_speed():
    """返回上次流式输出速度（线程安全）。"""
    return _stats.get_last_stream_speed()


# ── 模块级函数（向后兼容委托）─────────────────────────────
def get_total_input_tokens() -> int:
    """返回总输入 token 数。"""
    with _stats._lock:
        return _stats.token_stats["input"]


def get_total_output_tokens() -> int:
    """返回总输出 token 数。"""
    with _stats._lock:
        return _stats.token_stats["output"]


def reset_stats() -> None:
    """重置 _Stats 的 token 统计和会话开始时间。"""
    with _stats._lock:
        _stats.token_stats = {"input": 0, "output": 0, "calls": 0}
        _stats.session_start_time = time.time()


# ── 模块级函数（向后兼容委托）─────────────────────────────
accumulate_usage = _stats.accumulate_usage
set_tool_parse_elapsed = _stats.set_tool_parse_elapsed
set_stream_speed = _stats.set_stream_speed
