"""StreamContext — 流式处理的共享状态容器"""
from __future__ import annotations
import time

from ..events import publish_event
from ..stream_parse import ToolParseTracker


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

        # 使用量
        self.usage = {"input": 0, "output": 0}
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
    # 渲染器属性（已迁移到 ChatUIConsumer，此处保留向后兼容占位）
    # ═══════════════════════════════════════════════════════════

    @property
    def reasoning_renderer(self):
        """推理渲染器（已废弃，ChatUIConsumer 管理终端渲染）。

        返回 None，不再创建渲染器实例。
        """
        return None

    @reasoning_renderer.setter
    def reasoning_renderer(self, _value):
        pass  # 接受赋值但不存储（向后兼容）

    @property
    def content_renderer(self):
        """内容渲染器（已废弃，ChatUIConsumer 管理终端渲染）。

        返回 None，不再创建渲染器实例。
        """
        return None

    @content_renderer.setter
    def content_renderer(self, _value):
        pass  # 接受赋值但不存储（向后兼容）
