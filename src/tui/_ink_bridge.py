"""InkBridge — _BottomBar 兼容桥（底层为 AppModel + InkSession）。

对外保持 ``_BottomBar`` 的公开方法面（app_loop/_CmplHandler 依赖），
内部全部映射到 AppModel 状态 + InkSession 重渲染请求：
  - 状态域：set_model_name / enable_status / disable_status / reset_tool_count /
    increment_tool / decrement_tool / increment_tool_fail / set_main_phase /
    get_status_elapsed
  - 补全域：is_completion_visible / show_completions / hide_completions /
    cycle_completion / get_selected_completion
  - 输入：set_input_state → 模型输入状态 + 重渲染
  - 兼容访问器域（方向C 步骤8 拆分至 _ink_bridge_compat）：
    生命周期 no-op（setup/teardown/is_active/ensure_cursor_*）与 _BottomBar
    内部字段（_last_text/_bottom_lines/_completion_idx/_completion 等）
  - subagent：set_subagent_frame → 模型行（兼容旧路径）

方向C 步骤8 拆分说明：
  兼容访问器域（生命周期 no-op + _BottomBar 内部字段）迁移至
  ``src/tui/_ink_bridge_compat.py`` 的 ``_BottomBarCompatMixin``；
  本模块保留状态域/补全域/输入域/子代理域。公开方法面与构造签名不变。
"""

from __future__ import annotations

import logging
import time

from .app.model import CompletionState
from ._ink_bridge_compat import (
    _BottomBarCompatMixin,
    _CompletionProxy,  # noqa: F401  re-export 保持路径兼容
)

_logger = logging.getLogger(__name__)


class InkBridge(_BottomBarCompatMixin):
    """底部栏/状态/补全桥（AppModel + InkSession）。

    继承 ``_BottomBarCompatMixin``（兼容访问器域：生命周期 no-op +
    _BottomBar 内部字段）；本类保留状态/补全/输入/子代理域。
    """

    def __init__(self, model, session):
        self._model = model
        self._session = session

    # ── 状态域 ─────────────────────────────────────

    def set_model_name(self, name: str) -> None:
        self._model.status.model_name = name
        self._request_redraw()

    def enable_status(self) -> None:
        self._model.status.status_active = True
        self._request_redraw()

    def disable_status(self) -> None:
        self._model.status.status_active = False
        self._request_redraw()

    def reset_tool_count(self) -> None:
        st = self._model.status
        st.tool_count = 0
        st.tool_fail = 0
        st.tool_total = 0
        st.tool_phase_start = 0.0
        self._request_redraw()

    def increment_tool(self) -> None:
        # ★ 方向5（工具计数收敛）：委托 app.apply.tool_count_inc 单一真源。
        from src.tui.app.apply import tool_count_inc
        tool_count_inc(self._model.status)
        self._request_redraw()

    def decrement_tool(self) -> None:
        # ★ 方向5（工具计数收敛）：委托 app.apply.tool_count_dec 单一真源。
        from src.tui.app.apply import tool_count_dec
        tool_count_dec(self._model.status)
        self._request_redraw()

    def increment_tool_fail(self) -> None:
        # ★ 方向5（工具计数收敛）：委托 app.apply.tool_fail_inc 单一真源。
        from src.tui.app.apply import tool_fail_inc
        tool_fail_inc(self._model.status)
        self._request_redraw()

    def set_main_phase(self, phase: str) -> None:
        st = self._model.status
        if phase != st.main_phase:
            st.main_phase_start = time.monotonic()
        st.main_phase = phase
        self._request_redraw()

    def get_status_elapsed(self) -> float:
        try:
            from src.tui._snapshot import _get_snapshot
            fn = _get_snapshot()
            if fn is None:
                return 0.0
            return fn().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    # ── 补全域 ─────────────────────────────────────

    @property
    def is_completion_visible(self) -> bool:
        return self._model.completion.visible

    def show_completions(self, items, selected_idx, texts=None, start_pos=0,
                         orig_prefix="", title="补全", types=None,
                         match_prefix="", descriptions=None,
                         split_desc=False) -> None:
        if not items:
            return
        c = self._model.completion
        c.visible = True
        c.title = title
        c.items = list(items)
        c.texts = list(texts) if texts is not None else list(items)
        # ★ 1.8 修复：selected_idx 负值钳制到 0（修复前 min(int(-1), len-1) = -1
        #   → 负索引越界；改为 max(0, min(...)) 双向钳制；超上界仍钳到 len-1）。
        c.selected = max(0, min(int(selected_idx), len(items) - 1))
        c.start_pos = int(start_pos)
        c.orig_prefix = orig_prefix
        c.types = list(types) if types is not None else []
        c.match_prefix = match_prefix
        # Claude TUI parity 步骤 3.7：斜杠命令描述（缺省空列表兼容旧调用）
        c.descriptions = list(descriptions) if descriptions is not None else []
        # 分栏说明模式（user_select）：True 时弹窗右侧显示当前选中项说明
        c.split_desc = bool(split_desc)
        # 方向A 步骤1：show 时同步 _last_completion_idx（修复陈旧索引——
        # 新补全会话不再读到 hide 保留的旧索引；hide 语义保留，message_editor 依赖）。
        self._last_completion_idx = c.selected
        self._request_redraw()

    def hide_completions(self) -> None:
        if not self._model.completion.visible:
            return
        # 保存隐藏前选中索引（兼容 _BottomBar.get_selected_completion_index）
        self._last_completion_idx = self._model.completion.selected
        # 重置弹窗高度锁定（补全弹窗闪烁修复）：下次打开重新锁定
        self._model.completion.locked_height = 0
        self._model.completion = CompletionState()
        self._request_redraw()

    def get_selected_completion_index(self) -> int:
        """返回当前选中索引（可见时用选中；隐藏后用隐藏前索引）。"""
        c = self._model.completion
        if c.visible and c.items:
            return c.selected
        return getattr(self, "_last_completion_idx", 0)

    def cycle_completion(self, delta: int = 1) -> int:
        c = self._model.completion
        if not c.visible or not c.items:
            return 0
        n = len(c.items)
        c.selected = (c.selected + delta) % n
        # 方向A 步骤1：cycle 后同步 _last_completion_idx（修复陈旧索引——
        # 新补全会话不再读到 hide 保留的旧索引；hide 语义保留，message_editor 依赖）。
        self._last_completion_idx = c.selected
        self._request_redraw()  # 高亮移动需重绘
        return c.selected

    def get_selected_completion(self) -> tuple[str, int, str]:
        c = self._model.completion
        if not c.visible or not c.texts:
            return ("", 0, "")
        idx = min(c.selected, len(c.texts) - 1)
        return (c.texts[idx], c.start_pos, c.orig_prefix)

    # ── 输入 ────────────────────────────────────────

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        self._model.input_text = text
        self._model.input_cursor = cursor_pos
        self._request_redraw()

    # ── subagent（兼容旧路径） ──────────────────────

    def set_subagent_frame(self, lines) -> None:
        self._model.subagent_lines = list(lines)
        self._request_redraw()

    # ── 内部 ───────────────────────────────────────

    def _request_redraw(self) -> None:
        # P2-8：不再裸吞异常——记录 debug 日志（request_bottom_redraw 异常
        # 属非关键降级，不阻断调用方）。
        try:
            self._session.request_bottom_redraw()
        except Exception:
            _logger.debug("request_bottom_redraw 异常", exc_info=True)


__all__ = ["InkBridge"]
