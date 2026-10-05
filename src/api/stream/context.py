"""StreamContext — 流式处理的共享状态容器"""
from __future__ import annotations
import time

from ..events import publish_event
from ..stream_parse import ToolParseTracker
from ..stats import add_token_size, adjust_token_size
from ...core.tokens import count_cjk_other, estimate_tokens_from_counts


class StreamContext:
    """流式处理的共享状态

    各 handler 通过此对象共享和更新状态。
    """

    def __init__(self, model: str, display, label: str, silent: bool):
        self.model = model
        self.display = display
        self.label = label
        self.silent = silent

        # 模型阶段
        self.is_reasoning = True
        self.phase_thinking_sent = False
        self.phase_answering_sent = False

        # 内容累积
        self.content_full: str = ""
        self.reasoning_full: str = ""
        #: 上下文占用口径的流式增量：**只含 content 的整体估算**
        #:   （``estimate_tokens_from_counts``，与消息追加后
        #:   ``MessageStatsCache`` 的口径一致）。供
        #:   ``update_streaming_usage`` 实时刷新模式行 ``main · N%``——
        #:   reasoning 不随请求回传、工具参数在 ``message_to_text`` 中截断，
        #:   均不计入，避免流式结束后百分比回落跳变。
        self.streamed_output_tokens: int = 0

        # 使用量
        self.usage = {"input": 0, "output": 0, "input_cache_hit": 0, "input_cache_miss": 0}
        self.usage_accumulated = False

        # 工具调用
        self.tool_calls_map: dict = {}
        self.tracker = ToolParseTracker(self.tool_calls_map, display, label, silent=silent)

        # 中断
        self.esc_interrupted = False
        # ★ Bug B 修复：流迭代器因 Task 被取消而非正常结束
        self.task_cancelled = False

        # 计时
        self.stream_start_time = time.perf_counter()
        self._now: float = 0.0

        # 速度追踪
        self.speed_chunk_count = 0
        self.speed_last_update = self.stream_start_time
        self.speed_update_interval = 0.5
        self.last_live_est = 0
        self.token_estimate: int = 0
        self._live_total_dirty = False

        # ── 估算口径状态（字符分类增量 → 整体估算）────────────────────────
        # 逐 delta 分别调用 estimate_tokens 再求和会因 ``max(1, ...)`` 下限
        # 系统性高估（英文小分片可达 3 倍以上）。此处改为对每个增量只做
        # 字符分类计数累加，再用 estimate_tokens_from_counts 还原「累计全文
        # 整体估算」——数值与直接对全文调用 estimate_tokens 完全一致，且
        # 每 delta 仅 O(len(delta))。
        self._reasoning_cjk = 0
        self._reasoning_other = 0
        self._content_cjk = 0
        self._content_other = 0
        self._args_cjk = 0
        self._args_other = 0
        # 本流已通过 add_token_size 累加的估算总量——真实 usage 到达时用
        # ``真实 output - 本值`` 修正全局总 tok（状态栏）与速度分子。
        self.stream_added_tokens: int = 0

        # 状态标记（显式初始化，消除 getattr 防御式访问）
        self.final_usage_received = False
        self._cleaned_up = False

        # PhaseDone 发布去重标志（content.py / tool_calls.py / pipeline_async.py 共用）
        # 同 phase 每流至多发布一次；由 publish_phase_done_once() 读取并置位。
        self.phase_done_reasoning_sent = False
        self.phase_done_content_sent = False

    @property
    def now(self) -> float:
        """当前时间戳缓存（被 SpeedHandler 等高频调用时避免系统调用开销）。"""
        now = self._now
        return now if now > 0 else time.perf_counter()

    def publish_phase_done_once(self, phase: str) -> bool:
        """发布 PhaseDone 事件（同 phase 每流至多一次）。

        供 content.py / tool_calls.py / pipeline_async.py 统一调用，
        消除分散的「发布 + 置位」样板（原注释预告的 _phase_done_*_sent
        去重标记统一收敛于此）。

        Args:
            phase: 阶段名。"reasoning"/"content" 走去重（首次发布返回 True，
                二次返回 False，幂等跳过）；其他 phase（如 "segment_end"）
                直接发布且不置位，保留既有每次发布语义。

        Returns:
            True 表示本次已发布；False 表示该 phase 已发布过（幂等跳过）。
        """
        if phase == "reasoning":
            if self.phase_done_reasoning_sent:
                return False
            self.phase_done_reasoning_sent = True
        elif phase == "content":
            if self.phase_done_content_sent:
                return False
            self.phase_done_content_sent = True
        publish_event("PhaseDoneEvent", label=self.label or "", phase=phase)
        return True

    # ═══════════════════════════════════════════════════════════
    # 输出估算（字符分类增量 → 整体估算，单一口径）
    # ═══════════════════════════════════════════════════════════

    def _sync_estimate(self) -> int:
        """重算本流输出估算，返回 ``token_estimate`` 增量（>=0）。

        - ``token_estimate``：reasoning + content + 工具参数的整体估算总量
          （供 SpeedHandler 的会话 output 估算与全局总 tok）；
        - ``streamed_output_tokens``：**只含 content** 的整体估算——上下文
          占用口径（reasoning 不随请求回传；工具参数在 ``message_to_text``
          统计中截断 100 字符），与消息追加后的 ``MessageStatsCache`` 一致，
          避免流式结束百分比回落跳变；
        - 增量 >0 时同步 ``add_token_size`` 并累计 ``stream_added_tokens``
          （供真实 usage 修正）。
        """
        old = self.token_estimate
        total = (
            estimate_tokens_from_counts(self._reasoning_cjk, self._reasoning_other)
            + estimate_tokens_from_counts(self._content_cjk, self._content_other)
            + estimate_tokens_from_counts(self._args_cjk, self._args_other)
        )
        self.token_estimate = total
        self.streamed_output_tokens = estimate_tokens_from_counts(
            self._content_cjk, self._content_other)
        delta = total - old
        if delta > 0:
            add_token_size(delta)
            self.stream_added_tokens += delta
        return delta

    def add_reasoning_delta(self, text: str) -> int:
        """累积 reasoning 增量并重算估算（返回 token_estimate 增量）。"""
        if not text:
            return 0
        cjk, other = count_cjk_other(text)
        self._reasoning_cjk += cjk
        self._reasoning_other += other
        return self._sync_estimate()

    def add_content_delta(self, text: str) -> int:
        """累积 content 增量并重算估算（返回 token_estimate 增量）。"""
        if not text:
            return 0
        cjk, other = count_cjk_other(text)
        self._content_cjk += cjk
        self._content_other += other
        return self._sync_estimate()

    def add_args_delta(self, text: str) -> int:
        """累积工具参数增量并重算估算（返回 token_estimate 增量）。"""
        if not text:
            return 0
        cjk, other = count_cjk_other(text)
        self._args_cjk += cjk
        self._args_other += other
        return self._sync_estimate()

    def apply_real_usage(self, real_output: int) -> None:
        """真实 usage 到达：把本流估算累加修正为真实 output（可为负修正）。

        流式期间 ``add_token_size`` 累加估算值；此处以
        ``真实 output - stream_added_tokens`` 修正全局总 tok，使状态栏
        「总tok」与 /cost 的真实统计一致。
        """
        try:
            real = int(real_output or 0)
        except (TypeError, ValueError, OverflowError):
            real = 0
        correction = real - self.stream_added_tokens
        if correction:
            adjust_token_size(correction)
        self.stream_added_tokens = max(0, real)

    # ═══════════════════════════════════════════════════════════
    # 渲染器属性已移除（2026-10 架构清理）：渲染统一由 ChatUIConsumer 管理，
    # 原向后兼容占位属性 reasoning_renderer / content_renderer 无外部引用，
    # 已删除（避免死代码滞留）。
    # ═══════════════════════════════════════════════════════════
