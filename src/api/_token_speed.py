"""全局 Token 速率统计器 — _TokenSpeedTracker 类 + 模块级单例。"""

from __future__ import annotations

import time
import threading
from collections import deque


class _TokenSpeedTracker:
    """全局 token 速率统计器（模块级单例）。

    线程安全，可从任意协程/线程调用 add_token_size()。
    提供两种速度指标：
      - 平均速度（avg_speed）：从第一次 add 至今的整体速率
      - 窗口速度（window_speed）：最近 N 秒内的实时速率
    """

    def __init__(self, window_seconds: float = 5.0):
        # _total_tokens 是历史累计值（跨轮次不清空，永不重置）
        self._total_tokens = 0
        self._start_time: float | None = None
        self._lock = threading.Lock()

        # ── 滑动窗口（实时速率）───────────────────────────
        self._window_seconds = window_seconds
        # 每个元素: (timestamp, token_count)
        self._window: deque[tuple[float, int]] = deque()

        # ── 基于总tok差值的每秒速度（不依赖 add_token_size 个体记录）──
        # 每个元素: (timestamp, total_tokens_snapshot)
        self._speed_records: deque[tuple[float, int]] = deque()

        # ── 快照去重（避免高频 stats_snapshot() 产生冗余 _speed_records）──
        self._last_snapshot_total: int = -1   # 上次快照时的 _total_tokens
        self._last_snapshot_time: float = 0.0 # 上次快照时间戳

    def add_token_size(self, size: int) -> None:
        """添加一批 token，自动更新总计数和速率窗口。"""
        if size <= 0:
            return
        with self._lock:
            now = time.time()
            self._total_tokens += size
            if self._start_time is None:
                self._start_time = now
            self._window.append((now, size))

    def _prune_window(self, now: float | None = None) -> None:
        """清理窗口：移除超出时间范围的旧记录。"""
        if now is None:
            now = time.time()
        cutoff = now - self._window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    @property
    def total_tokens(self) -> int:
        """总 token 数。"""
        with self._lock:
            return self._total_tokens

    @property
    def avg_speed(self) -> float:
        """平均速度（tokens/sec）：从第一次 add 至今的全程速率。

        尚未有数据时返回 0.0。
        """
        with self._lock:
            if self._start_time is None or self._total_tokens == 0:
                return 0.0
            elapsed = time.time() - self._start_time
            if elapsed <= 0:
                return 0.0
            return self._total_tokens / elapsed

    @property
    def window_speed(self) -> float:
        """窗口速度（tokens/sec）：最近 N 秒内的实时速率。

        窗口内无数据时返回 0.0。
        """
        now = time.time()
        with self._lock:
            self._prune_window(now)
            if not self._window:
                return 0.0
            window_tokens = sum(c for _, c in self._window)
            elapsed = now - self._window[0][0]
            if elapsed <= 0:
                return 0.0
            return window_tokens / elapsed

    @property
    def short_window_speed(self) -> float:
        """短窗口速度（tokens/sec）：最近 1 秒内的实时速率，更灵敏。

        只读遍历 _window，不修改窗口数据，不影响 window_speed 的 5 秒窗口。
        窗口内无数据时返回 0.0。
        """
        now = time.time()
        cutoff = now - 1.0
        with self._lock:
            if not self._window:
                return 0.0
            # 只读遍历：从头查找第一个 >= cutoff 的条目，不修改 _window
            idx = 0
            while idx < len(self._window) and self._window[idx][0] < cutoff:
                idx += 1
            if idx >= len(self._window):
                return 0.0
            tokens_1s = sum(c for _, c in list(self._window)[idx:])
            elapsed = now - self._window[idx][0]
            if elapsed <= 0:
                return 0.0
            return tokens_1s / elapsed

    def reset(self, keep_total: bool = True) -> None:
        """重置统计。

        Args:
            keep_total: 为 True（默认）时保留历史累计总 tok，
                        为 False 时完全重置（包括总 tok，仅用于测试）。
        """
        with self._lock:
            if not keep_total:
                self._total_tokens = 0
            # _total_tokens 默认是历史累计值，keep_total=True 时不修改
            self._start_time = None
            self._window.clear()
            self._speed_records.clear()
            self._last_snapshot_total = -1
            self._last_snapshot_time = 0.0

    @property
    def per_second_speed(self) -> float:
        """每秒实时速度：基于总 tok 差值计算 (tok/s)。

        记录总 tok 的时间序列快照，在 1 秒窗口内取差值：
        tok/s = (当前总tok - 窗口起点总tok) / 经过秒数

        窗口内数据不足 2 个采样点时返回 0.0。
        """
        now = time.time()
        with self._lock:
            # ★ 去重守卫（与 stats_snapshot() 共用去重状态，避免高频调用
            #    产生冗余 _speed_records 快照，导致速度计算窗口缩窄/虚高）
            _total = self._total_tokens
            _total_changed = _total != self._last_snapshot_total
            _time_elapsed = now - self._last_snapshot_time >= 0.1
            if _total_changed or _time_elapsed:
                self._speed_records.append((now, _total))
                self._last_snapshot_total = _total
                self._last_snapshot_time = now

            # 清理超出 1 秒的旧记录（保留 ≥2 条——最新 + 参考；修复前 `> 1`
            # 在 1s 边界把参考记录剪掉（now-1.0 浮点略大于旧记录时间）→ 只剩
            # 最新一条 → 恒 0.0。保留参考后按窗口内 delta 计算实时速度。）
            cutoff = now - 1.0
            while len(self._speed_records) > 2 and self._speed_records[0][0] < cutoff:
                self._speed_records.popleft()

            if len(self._speed_records) < 2:
                return 0.0

            old_ts, old_total = self._speed_records[0]
            elapsed = now - old_ts
            if elapsed <= 0:
                return 0.0
            delta = self._total_tokens - old_total
            return round(delta / elapsed, 2)

    def stats_snapshot(self) -> dict:
        """返回当前统计的快照字典（线程安全，一次调用获取全部）。"""
        with self._lock:
            total = self._total_tokens
            start = self._start_time
            now = time.time()
            self._prune_window(now)
            window_tokens = sum(c for _, c in self._window)
            window_elapsed = now - self._window[0][0] if self._window else 0.0
            elapsed = now - start if start else 0.0

            # 计算每秒速度（总 tok 差值法）
            # ★ 去重：仅当 total 变化或距上次快照 ≥100ms 时才追加记录，
            #   避免高频 force_redraw() → _format_status() → stats_snapshot()
            #   在 5ms 间隔下产生 ~200 条/秒的冗余快照。
            _total_changed = total != self._last_snapshot_total
            _time_elapsed = now - self._last_snapshot_time >= 0.1
            if _total_changed or _time_elapsed:
                self._speed_records.append((now, total))
                self._last_snapshot_total = total
                self._last_snapshot_time = now
            cutoff = now - 1.0
            # 保留 ≥2 条参考记录（与 per_second_speed() 一致修复——1s 边界
            # 剪枝误删参考导致恒 0）
            while len(self._speed_records) > 2 and self._speed_records[0][0] < cutoff:
                self._speed_records.popleft()
            if len(self._speed_records) >= 2:
                old_ts, old_total = self._speed_records[0]
                s_elapsed = now - old_ts
                per_sec_speed = round((total - old_total) / s_elapsed, 2) if s_elapsed > 0 else 0.0
            else:
                per_sec_speed = 0.0

            return {
                "total_tokens": total,
                "avg_speed": round(total / elapsed, 2) if elapsed > 0 else 0.0,
                "window_speed": round(window_tokens / window_elapsed, 2) if window_elapsed > 0 else 0.0,
                "elapsed_seconds": round(elapsed, 2),
                "per_second_speed": per_sec_speed,
            }


# ── 模块级单例 ────────────────────────────────────────────
_token_speed = _TokenSpeedTracker()


# ── 模块级接口 ────────────────────────────────────────────
def add_token_size(size: int) -> None:
    """添加一批 token 到全局统计。

    典型用法：流式输出每收到一批 token 就调用此函数。

    Args:
        size: 本次收到的 token 数量（>0 时有效）。
    """
    _token_speed.add_token_size(size)


def get_total_tokens() -> int:
    """获取全局总 token 数。"""
    return _token_speed.total_tokens


def get_token_speed() -> float:
    """获取全局 token 生成速度（tokens/sec，最近 5 秒滑动窗口）。

    返回实时速率，适合用于展示"当前速度"。
    """
    return _token_speed.window_speed


def get_short_window_speed() -> float:
    """获取全局 token 生成速度（tokens/sec，最近 1 秒滑动窗口）。

    返回更灵敏的实时速率，适合用于展示"每秒实时速度"。
    """
    return _token_speed.short_window_speed


def get_avg_token_speed() -> float:
    """获取全局平均 token 速度（tokens/sec，从首次调用至今）。"""
    return _token_speed.avg_speed


def get_per_second_speed() -> float:
    """获取基于总 tok 差值的每秒实时速度 (tok/s)。

    记录总 tok 的时间序列快照，在 1 秒窗口内取差值，
    不受 reset 影响（总 tok 是历史累计值）。
    """
    return _token_speed.per_second_speed


def get_token_speed_snapshot() -> dict:
    """获取全局 token 统计快照。

    Returns:
        {
            "total_tokens": int,        # 总 token 数（历史累计，不清空）
            "avg_speed": float,         # 平均速度 tokens/sec
            "window_speed": float,      # 实时窗口速度 tokens/sec
            "elapsed_seconds": float,   # 已统计秒数
            "per_second_speed": float,  # 每秒实时速度 tok/s（总 tok 差值法）
        }
    """
    return _token_speed.stats_snapshot()


def reset_token_speed(keep_total: bool = True) -> None:
    """重置全局 token 速率统计。

    Args:
        keep_total: 为 True（默认）时保留历史累计总 tok，
                    为 False 时完全重置（包括总 tok，仅用于测试）。
    """
    _token_speed.reset(keep_total=keep_total)
