"""会话级 token 统计核心 — _Stats 类 + 模块级单例。"""

from __future__ import annotations

import time
import threading

from ..constants import (
    STREAM_LABEL_LOOP_PROMPT,
    STREAM_LABEL_SUMMARIZE,
    STREAM_LABEL_TITLE,
)

#: 不计入「Agent 新增输入」的内部模型调用标签（压缩摘要 / 会话标题 / /loop
#: 续跑提词）。它们不属于主 Agent / SubAgent 对话，但 raw input 仍计入
#: ``token_stats["input"]``（费用与缓存明细保持 API 计费口径）。
_NON_AGENT_INPUT_LABELS = frozenset({
    STREAM_LABEL_SUMMARIZE,
    STREAM_LABEL_TITLE,
    STREAM_LABEL_LOOP_PROMPT,
})


def _agent_input_key(label, model) -> tuple[str, str]:
    """Agent 新增输入的基线键：``(归一化 label, model)``。

    - 主 Agent 的多种 label（None / 空串 / ``assistant`` / ``main``）统一为
      ``main``，避免不同调用路径把同一对话拆成两套基线；
    - 模型切换时 key 变化 → 视为新基线，本次计全量；
    - SubAgent 使用自己的 label（``agent-N`` / ``sa-*`` 等）独立累计。
    """
    text = str(label or "").strip()
    if not text or text in ("assistant", "main"):
        text = "main"
    return text, str(model or "")


class _Stats:
    """会话级统计状态封装。"""

    __slots__ = (
        "_lock",
        "token_stats",
        "session_start_time",
        "session_start_monotonic",
        "last_tool_parse_elapsed",
        "last_stream_speed",
        "_last_agent_input",
    )

    def __init__(self):
        # ── 通用锁（保护所有字段）─────────────────────────
        self._lock = threading.Lock()

        # ── 会话级 token 统计 ──────────────────────────────
        # input/output: API 实际计费的总输入/输出 token（full prompt）
        # input_cache_hit/input_cache_miss: 缓存命中/未命中的输入 token
        #   （命中 + 未命中 = input，供 /usage 正确计算缓存折扣费用）
        # input_delta: 各 Agent 上下文增量累计（本次 input - 同 Agent 上次
        #   input；首次/缩小/切模型计全量）——展示口径，不含历史上下文重复量
        self.token_stats = {
            "input": 0,
            "output": 0,
            "calls": 0,
            "input_cache_hit": 0,
            "input_cache_miss": 0,
            "input_delta": 0,
        }
        #: Agent 新增输入的基线：``(label, model) → 上次 input``。
        self._last_agent_input: dict[tuple[str, str], int] = {}
        self.session_start_time = time.time()
        # ★ 会话起点（单调时钟）：会话**耗时**的唯一来源——墙钟可能被 NTP
        #   校时/手工调整而跳变（甚至回退），使「时长」出现负值或突变。
        #   ``session_start_time``（墙钟 epoch）保留给需要绝对时间的消费方。
        self.session_start_monotonic = time.monotonic()

        # ── 上次工具调用解析耗时（供 agent 侧显示用）───────
        self.last_tool_parse_elapsed = 0.0

        # ── 上次流式输出速度（供 agent 侧显示用）───────────
        self.last_stream_speed = 0.0

    def accumulate_usage(self, usage, increment_calls=True, label=None, model=None):
        """线程安全地累加一次 API 调用的 usage 到全局统计。

        Args:
            usage: {"input": int, "output": int, "input_cache_hit": int,
                    "input_cache_miss": int} 字典；缓存字段可选（缺省按 0 累加，
                    兼容旧调用方 / 流式实时估计阶段）。
            increment_calls: 是否累加调用次数。流式过程中的实时 token
                估计累计（SpeedHandler）应传 False——该累计不是一次真实
                API 调用，只影响 token 展示；真实 usage 到达时由
                accumulate_usage(..., increment_calls=True) 统一计一次
                calls，避免 /usage 的调用次数虚高。
            label: API 调用标签（主 Agent ``assistant`` / SubAgent
                ``agent-N`` / 后台 ``sa-*`` / 内部 ``summarize`` 等）。仅真实
                usage（``increment_calls=True``）且非内部标签时参与
                ``input_delta`` 的基线计算。
            model: 本次调用模型名。与 label 共同构成基线键——切模型时视为
                新基线，本次计全量。
        """
        with self._lock:
            self.token_stats["input"] += usage.get("input", 0)
            self.token_stats["output"] += usage.get("output", 0)
            self.token_stats["input_cache_hit"] += usage.get("input_cache_hit", 0)
            self.token_stats["input_cache_miss"] += usage.get("input_cache_miss", 0)
            if increment_calls:
                self.token_stats["calls"] += 1
                self._accumulate_agent_input_locked(usage, label, model)

    def _accumulate_agent_input_locked(self, usage, label, model) -> None:
        """累计单次真实调用的「Agent 新增输入」（调用方须持有 ``_lock``）。

        规则（2026-10-22 用户需求）：
          - 同一 ``(label, model)`` 连续调用：``delta = 本次 input - 上次 input``；
          - 首次 / 上下文缩小（压缩、重试后重建等）/ 切模型：基线重置，本次计全量；
          - 内部调用（压缩摘要 / 会话标题 / loop 提词）不计；
          - 只影响 ``input_delta``（展示口径），``input`` 仍是 full prompt
            计费口径，费用与缓存命中率不变。
        """
        text_label = str(label or "").strip()
        if text_label in _NON_AGENT_INPUT_LABELS:
            return
        try:
            current = int(usage.get("input", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return
        if current <= 0:
            return
        key = _agent_input_key(label, model)
        last = self._last_agent_input.get(key)
        if last is None or current < last:
            delta = current
        else:
            delta = current - last
        if delta > 0:
            self.token_stats["input_delta"] += delta
        self._last_agent_input[key] = current

    def reset_agent_input_baseline(self) -> None:
        """清空 Agent 新增输入的基线（不清已累计的 ``input_delta``）。"""
        with self._lock:
            self._last_agent_input.clear()

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
    """返回会话开始时间（墙钟 epoch；从 _stats 实例实时读取）。"""
    return _stats.session_start_time


def get_session_elapsed() -> float:
    """返回会话已运行秒数（单调时钟，不受系统时间调整影响）。"""
    return max(0.0, time.monotonic() - _stats.session_start_monotonic)


def get_token_stats():
    """返回 token_stats 的快照副本（线程安全）。"""
    return _stats.get_stats_snapshot()


def compute_cache_hit_rate(stats) -> float | None:
    """缓存命中率（dsh 口径）＝ 命中 / (命中 + 未命中)。

    口径与 dsh 的 ``cacheReadTokens / (cacheReadTokens + uncachedInputTokens
    + cacheWriteTokens)`` 对齐：**只算输入侧**，输出 token 不进分母。本项目
    stats 只有「命中 / 未命中」两桶、未单列 cache write，因此分母取现有两桶
    之和——DeepSeek / OpenAI（无 cache write 字段）与 dsh 完全一致；
    Anthropic 的 cache creation 目前未单独计入任一桶（沿用既有 stats 口径）。

    Args:
        stats: token_stats 快照（``input_cache_hit`` / ``input_cache_miss``）。

    Returns:
        0.0~1.0 的命中率；**无缓存拆分数据**（缺少 ``input_cache_miss`` /
        二者合计为 0，例如 provider 未上报缓存字段或尚未收到真实 usage）
        返回 ``None``——调用方必须显示「—」，不得冒充 0%。真实零命中
        （``hit=0`` 且 ``miss>0``）返回 0.0。
    """
    if not isinstance(stats, dict):
        return None
    miss_raw = stats.get("input_cache_miss")
    if miss_raw is None:
        return None
    try:
        hit = int(stats.get("input_cache_hit", 0) or 0)
        miss = int(miss_raw or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    hit = max(0, hit)
    miss = max(0, miss)
    total = hit + miss
    if total <= 0:
        return None
    return hit / total


def get_last_tool_parse_elapsed():
    """返回上次工具调用解析耗时（线程安全）。"""
    return _stats.get_last_tool_parse_elapsed()


def get_last_stream_speed():
    """返回上次流式输出速度（线程安全）。"""
    return _stats.get_last_stream_speed()


# ── 模块级函数（向后兼容委托）─────────────────────────────
def get_total_input_tokens() -> int:
    """返回 API 计费口径的总输入 token 数（每次完整 prompt 累加）。"""
    with _stats._lock:
        return _stats.token_stats["input"]


def get_total_agent_input_tokens() -> int:
    """返回各 Agent 上下文增量累计的输入 token 数（展示口径）。

    与 ``get_total_input_tokens()`` 的区别：本函数按 ``(label, model)``
    记录同 Agent 上次 input，只累加每次差值（首次/缩小/切模型计全量），
    因此不会把重复发送的历史上下文反复计入；内部调用（压缩摘要 / 标题 /
    loop 提词）不计。费用估算仍应使用 ``get_total_input_tokens()``。
    """
    with _stats._lock:
        return _stats.token_stats.get("input_delta", 0)


def get_total_output_tokens() -> int:
    """返回总输出 token 数。"""
    with _stats._lock:
        return _stats.token_stats["output"]


def get_total_input_cache_hit_tokens() -> int:
    """返回缓存命中的输入 token 数。"""
    with _stats._lock:
        return _stats.token_stats.get("input_cache_hit", 0)


def get_total_input_cache_miss_tokens() -> int:
    """返回缓存未命中的输入 token 数。"""
    with _stats._lock:
        return _stats.token_stats.get("input_cache_miss", 0)


def reset_agent_input_baseline() -> None:
    """清空 Agent 新增输入的基线（新会话 / 显式重置时调用）。

    只清基线不重置已累计的 ``input_delta``——总输入仍按进程累计，与输出
    总量口径一致；下一次调用按「首次」计全量。
    """
    _stats.reset_agent_input_baseline()


def reset_stats() -> None:
    """重置 _Stats 的 token 统计和会话开始时间。"""
    with _stats._lock:
        _stats.token_stats = {
            "input": 0,
            "output": 0,
            "calls": 0,
            "input_cache_hit": 0,
            "input_cache_miss": 0,
            "input_delta": 0,
        }
        _stats._last_agent_input.clear()
        _stats.session_start_time = time.time()
        _stats.session_start_monotonic = time.monotonic()


# ── 模块级函数（向后兼容委托）─────────────────────────────
accumulate_usage = _stats.accumulate_usage
set_tool_parse_elapsed = _stats.set_tool_parse_elapsed
set_stream_speed = _stats.set_stream_speed
