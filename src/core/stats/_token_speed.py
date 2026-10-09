"""全局 Token 速率统计器 — _TokenSpeedTracker 类 + 模块级单例。"""

from __future__ import annotations

import math
import time
import threading
from collections import deque


#: 「已知耗时的批量生成」速率保鲜期（秒）。
#:
#: 非流式调用（未走流式管线的直连调用，如直接调用 ``call_model_sync``）在
#: **调用结束**时才拿到真实 usage，其 token 一次性进入统计——窗口差值法会
#: 把整批算进最后一个采样间隔（虚高几十倍），且 1 秒采样窗口滑走后速率直接
#: 归零（用户看不到这次生成的 tok/s）。
#: ``add_token_size_batch`` 记录该批次的**真实平均速率**（size / elapsed），
#: 在窗口差值法失真/归零时回退到它；超过本保鲜期回退失效（速率自然归零，
#: 不会长期显示陈旧速率）。
_BATCH_RATE_TTL = 5.0


class _TokenSpeedTracker:
    """全局 token 速率统计器（模块级单例）。

    线程安全，可从任意协程/线程调用 ``add_token_size()``。
    提供两种速度指标：
      - 平均速度（avg_speed）：从第一次 add 至今的整体速率
      - 窗口速度（window_speed）：最近 N 秒内的实时速率

    另一路每秒实时速度（``per_second_speed``，状态栏 tok/s）按总 tok 差值法
    计算；非流式调用的批量生成（``add_token_size_batch``，未走流式管线的
    直连调用）以已知耗时记账，使该次生成的 tok/s 显示为真实平均速率
    ``size / elapsed`` 而不是一次性突刺或瞬间归零（见 ``_BATCH_RATE_TTL``）。
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

        # ── 最近一次「已知耗时的批量生成」─────────────────────
        # (结束时间戳, token 数, 生成耗时秒, 该批结束后的总 tok)。
        # 供 per_second_speed / stats_snapshot 在窗口差值法失真或归零时回退到
        # 真实平均速率（见 _BATCH_RATE_TTL 与 add_token_size_batch）。
        self._last_batch: tuple[float, int, float, int] | None = None

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

    def add_token_size_batch(self, size: int, elapsed: float) -> None:
        """计入一次「已知耗时」的批量生成（未走流式管线的直连非流式调用）。

        与 ``add_token_size`` 的差异：本方法知道这批 token 的真实生成耗时，
        因此把该批次记为「批量生成事件」——总 tok 仍一次性累加（历史累计语义
        不变），而 ``per_second_speed`` / ``stats_snapshot().per_second_speed``
        在窗口差值法失真（整批算进最后一个采样间隔 → 虚高几十倍）或短窗口
        滑走后归零时，回退到本批次的**真实平均速率** ``size / elapsed``，
        使状态栏「tok/s」反映这次生成的真实速度（见 ``_BATCH_RATE_TTL``）。

        实时速率窗口（``window_speed`` / ``short_window_speed``）按普通批次
        累加（条目时间取当前时刻，保持窗口单调有序）。

        Args:
            size: 本批生成 token 数（<=0 / 不可解析时忽略）。
            elapsed: 本批 token 的真实生成耗时（秒）。<=0 / 非有限值时按 0
                处理——退化为一次性计入（与 ``add_token_size`` 等价，无速率
                回退）。
        """
        try:
            size = int(size)
        except (TypeError, ValueError, OverflowError):
            return
        if size <= 0:
            return
        try:
            seconds = float(elapsed)
        except (TypeError, ValueError, OverflowError):
            seconds = 0.0
        if not math.isfinite(seconds) or seconds < 0:
            seconds = 0.0
        with self._lock:
            now = time.time()
            self._total_tokens += size
            if self._start_time is None:
                # 首批即为批量生成：起始时间回填到生成起点，平均速率不失真。
                self._start_time = now - seconds
            self._window.append((now, size))
            self._last_batch = (now, size, seconds, self._total_tokens)

    def adjust_token_size(self, size: int) -> None:
        """修正总 token 计数（可为负）——用真实 usage 覆盖流式估算偏差。

        流式生成期间 ``add_token_size`` 累加的是**估算值**；真实 usage 到达
        后用本方法把本流估算累加修正为真实 output（``size = 真实 - 已累加
        估算``，通常为负）。总计数钳制到 >= 0，绝不为负；修正量同时写入
        速率窗口（负值可被 ``window_speed`` / ``per_second_speed`` 的钳制消化）。

        Args:
            size: 修正量（可正可负、可为 0）；非整数/不可解析值忽略。
        """
        try:
            size = int(size)
        except (TypeError, ValueError, OverflowError):
            return
        if size == 0:
            return
        with self._lock:
            new_total = self._total_tokens + size
            if new_total < 0:
                size = -self._total_tokens
                new_total = 0
            if size == 0:
                return
            now = time.time()
            self._total_tokens = new_total
            if self._start_time is None and size > 0:
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
            window_tokens = max(0, sum(c for _, c in self._window))
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
            tokens_1s = max(0, sum(c for _, c in list(self._window)[idx:]))
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
            self._last_batch = None

    @property
    def per_second_speed(self) -> float:
        """每秒实时速度：基于总 tok 差值计算 (tok/s)。

        记录总 tok 的时间序列快照，在 1 秒窗口内取差值：
        tok/s = (当前总tok - 窗口起点总tok) / 经过秒数

        窗口内数据不足 2 个采样点时返回 0.0；最近一次「已知耗时的批量生成」
        （``add_token_size_batch``，未走流式管线的直连调用）仍在保鲜期内
        且其后无新 token 时，返回该批次的真实平均速率——否则整批会被算进最后
        一个采样间隔（虚高）或随窗口滑走（归零），状态栏「tok/s」看不到这次
        生成的速率。
        """
        now = time.time()
        with self._lock:
            return self._per_second_speed_locked(now)

    def _per_second_speed_locked(self, now: float) -> float:
        """每秒实时速度计算（调用方须持有 ``self._lock``）。

        ★ 去重守卫（与 ``stats_snapshot()`` 共用去重状态，避免高频调用产生冗余
        ``_speed_records`` 快照，导致速度计算窗口缩窄/虚高）；1 秒窗口剪枝保留
        ≥2 条（最新 + 参考——修复前 ``> 1`` 在 1s 边界把参考记录剪掉，只剩最新
        一条 → 恒 0.0）。
        """
        _total = self._total_tokens
        _total_changed = _total != self._last_snapshot_total
        _time_elapsed = now - self._last_snapshot_time >= 0.1
        if _total_changed or _time_elapsed:
            self._speed_records.append((now, _total))
            self._last_snapshot_total = _total
            self._last_snapshot_time = now

        cutoff = now - 1.0
        while len(self._speed_records) > 2 and self._speed_records[0][0] < cutoff:
            self._speed_records.popleft()

        # 最近一次「已知耗时的批量生成」优先：该批次一次性计入，窗口差值法会
        # 把整批算进最后一个采样间隔（虚高）或随后滑走（归零）——两种情形都
        # 应以批次真实平均速率呈现（其后有新 token 时 _batch_rate_locked 返回 0，
        # 自动交回窗口差值法）。
        batch_rate = self._batch_rate_locked(now)
        if batch_rate > 0:
            return batch_rate

        if len(self._speed_records) < 2:
            return 0.0
        old_ts, old_total = self._speed_records[0]
        elapsed = now - old_ts
        if elapsed <= 0:
            return 0.0
        # ★ 真实 usage 修正（adjust_token_size）可使 delta 为负——速度恒非负
        #   （负值对用户无意义，且会让状态栏速度段抖动）。
        return max(0.0, round((_total - old_total) / elapsed, 2))

    def record_generation_rate(self, size: int, elapsed: float) -> None:
        """登记一次生成的**真实平均速率**（不改动总 token 统计）。

        与 ``add_token_size_batch`` 的差异：本方法**不累加** token——总 tok 已由
        流式增量实时累加并经真实 usage 校正（见 ``StreamContext.apply_real_usage``）；
        本方法只登记速率元数据（复用 ``_last_batch`` 回退机制），使状态栏
        「tok/s」在生成结束后的宽限期内仍显示本次生成的真实平均速率
        ``size / elapsed``，而不是随 1 秒采样窗口滑走归零。

        Args:
            size: 本次生成 token 数（<=0 / 不可解析时忽略）。
            elapsed: 本次生成耗时（秒）；<=0 / 非有限值时忽略。
        """
        try:
            size_int = int(size)
            seconds = float(elapsed)
        except (TypeError, ValueError, OverflowError):
            return
        if size_int <= 0 or not math.isfinite(seconds) or seconds <= 0:
            return
        with self._lock:
            # total_after 取**当前**总 tok：其后无新 token 时速率回退有效
            # （``_batch_rate_locked`` 校验 total 相等），有新 token 自动失效。
            self._last_batch = (time.time(), size_int, seconds, self._total_tokens)

    def _batch_rate_locked(self, now: float) -> float:
        """最近一次批量生成的真实平均速率（tok/s）；不可用返回 0.0。

        仅在「批次仍在 ``_BATCH_RATE_TTL`` 保鲜期内」且「此后没有新增 token」
        （说明该批次仍是最近一次生成活动）时有效——否则返回 0.0，交由窗口差值
        法计算。调用方须持有 ``self._lock``。
        """
        batch = self._last_batch
        if batch is None:
            return 0.0
        end_ts, size, seconds, total_after = batch
        if seconds <= 0 or now - end_ts > _BATCH_RATE_TTL:
            return 0.0
        if self._total_tokens != total_after:
            return 0.0
        return max(0.0, round(size / seconds, 2))

    def stats_snapshot(self) -> dict:
        """返回当前统计的快照字典（线程安全，一次调用获取全部）。"""
        with self._lock:
            total = self._total_tokens
            start = self._start_time
            now = time.time()
            self._prune_window(now)
            window_tokens = max(0, sum(c for _, c in self._window))
            window_elapsed = now - self._window[0][0] if self._window else 0.0
            elapsed = now - start if start else 0.0

            # 计算每秒速度（总 tok 差值法；含「已知耗时批量生成」速率回退）
            # ★ 去重：仅当 total 变化或距上次快照 ≥100ms 时才追加记录，
            #   避免高频 force_redraw() → _format_status() → stats_snapshot()
            #   在 5ms 间隔下产生 ~200 条/秒的冗余快照。
            per_sec_speed = self._per_second_speed_locked(now)

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


def add_token_size_batch(size: int, elapsed: float) -> None:
    """计入一次「已知耗时」的批量生成（未走流式管线的直连非流式调用）。

    典型用法：非流式模型调用结束后拿到真实 usage 时调用——
    ``add_token_size_batch(usage["output"], api_duration)``。总 tok 一次性
    累加（历史累计语义不变），并让 ``per_second_speed`` 在该批次之后回退到
    真实平均速率 ``size / elapsed``（避免整批算进最后一个采样间隔而虚高、
    或随短窗口滑走而瞬间归零）。

    Args:
        size: 本批生成 token 数（<=0 时忽略）。
        elapsed: 本批 token 的真实生成耗时（秒）；<=0 / 非有限值按 0 处理
            （退化为一次性计入，无速率回退）。
    """
    _token_speed.add_token_size_batch(size, elapsed)


def record_generation_rate(size: int, elapsed: float) -> None:
    """登记一次生成的真实平均速率（不改动总 tok）。

    供**流式**调用在结束后登记本次生成速率：总 tok 已由流式管线实时累加并经
    真实 usage 校正，本函数只登记速率元数据，使状态栏「tok/s」在生成结束后的
    宽限期内仍显示本次生成的真实平均速率（见
    ``_TokenSpeedTracker.record_generation_rate``）。

    Args:
        size: 本次生成 token 数（<=0 时忽略）。
        elapsed: 本次生成耗时（秒）；<=0 / 非有限值时忽略。
    """
    _token_speed.record_generation_rate(size, elapsed)


def adjust_token_size(size: int) -> None:
    """修正全局总 token（可为负）——真实 usage 覆盖流式估算偏差。

    流式期间 ``add_token_size`` 累加估算值，真实 usage 到达后用本函数传入
    ``真实值 - 已累加估算`` 修正（通常为负），使状态栏总 tok 与 /cost 的
    真实统计口径一致。总计数钳制到 >= 0。

    Args:
        size: 修正量（可正可负）。
    """
    _token_speed.adjust_token_size(size)


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
    不受 reset 影响（总 tok 是历史累计值）。最近一次「已知耗时的批量生成」
    （``add_token_size_batch``）仍在保鲜期内且其后无新 token 时返回该批次的
    真实平均速率（未走流式管线的直连非流式调用的 tok/s 因此可见）。
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
            "per_second_speed": float,  # 每秒实时速度 tok/s（总 tok 差值法；
                                        # 批量生成保鲜期内为批次真实平均速率）
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
