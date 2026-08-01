"""InkBridge — _BottomBar 兼容桥（底层为 AppModel + InkSession）。

对外保持 ``_BottomBar`` 的公开方法面（app_loop/_CmplHandler 依赖），
内部全部映射到 AppModel 状态 + InkSession 重渲染请求：
  - 状态域：set_model_name / enable_status / disable_status / reset_tool_count /
    increment_tool / decrement_tool / increment_tool_fail / set_main_phase /
    get_status_elapsed
  - 补全域：is_completion_visible / show_completions / hide_completions /
    cycle_completion / get_selected_completion
  - 输入：set_input_state → 模型输入状态 + 重渲染
  - 生命周期 no-op：setup / teardown / is_active / ensure_cursor_in_upper /
    ensure_cursor_in_lower（非全屏流动模型无 DECSTBM）
  - subagent：set_subagent_frame → 模型行（兼容旧路径）
"""

from __future__ import annotations

import time

from .app.model import CompletionState


class _CompletionProxy:
    """兼容 _BottomBar._completion 内部字段访问（user_select 等直接读写）。"""

    def __init__(self, model):
        self._model = model

    @property
    def _visible(self) -> bool:
        return self._model.completion.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._model.completion.visible = bool(value)

    @property
    def _popup_height(self) -> int:
        return self._model.completion.popup_height

    @_popup_height.setter
    def _popup_height(self, value: int) -> None:
        self._model.completion.popup_height = int(value)

    @property
    def _items(self) -> list:
        return self._model.completion.items

    @_items.setter
    def _items(self, value: list) -> None:
        self._model.completion.items = list(value)

    @property
    def _texts(self) -> list:
        return self._model.completion.texts

    @_texts.setter
    def _texts(self, value: list) -> None:
        self._model.completion.texts = list(value)


class InkBridge:
    """底部栏/状态/补全桥（AppModel + InkSession）。"""

    # 兼容 user_select 的 _MIN_HEIGHT 检查（is_active 恒 True，实际不会走到）
    _MIN_HEIGHT = 12

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
        st = self._model.status
        st.tool_count += 1
        st.tool_total += 1
        if st.tool_phase_start <= 0:
            st.tool_phase_start = time.monotonic()
        self._request_redraw()

    def decrement_tool(self) -> None:
        st = self._model.status
        if st.tool_count > 0:
            st.tool_count -= 1
        if st.tool_count <= 0:
            st.tool_phase_start = 0.0
        self._request_redraw()

    def increment_tool_fail(self) -> None:
        self._model.status.tool_fail += 1
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
                         match_prefix="") -> None:
        if not items:
            return
        c = self._model.completion
        c.visible = True
        c.title = title
        c.items = list(items)
        c.texts = list(texts) if texts is not None else list(items)
        c.selected = min(int(selected_idx), max(0, len(items) - 1))
        c.start_pos = int(start_pos)
        c.orig_prefix = orig_prefix
        c.types = list(types) if types is not None else []
        c.match_prefix = match_prefix
        self._request_redraw()

    def hide_completions(self) -> None:
        if not self._model.completion.visible:
            return
        self._model.completion = CompletionState()
        self._request_redraw()

    def cycle_completion(self, delta: int = 1) -> int:
        c = self._model.completion
        if not c.visible or not c.items:
            return 0
        n = len(c.items)
        c.selected = (c.selected + delta) % n
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

    # ── 兼容 _BottomBar 内部字段（user_select 等直接读写） ──

    @property
    def _last_text(self) -> str:
        return self._model.input_text

    @_last_text.setter
    def _last_text(self, value: str) -> None:
        self._model.input_text = value
        self._request_redraw()

    @property
    def _last_rendered_text(self) -> str:
        return self._model.input_text

    @_last_rendered_text.setter
    def _last_rendered_text(self, value: str) -> None:
        self._model.input_text = value
        self._request_redraw()

    @property
    def _bottom_lines(self) -> int:
        """非全屏模型无 DECSTBM：返回输入区近似行数（兼容访问）。"""
        return 5

    @property
    def _last_bottom_lines(self) -> int:
        return 5

    @_last_bottom_lines.setter
    def _last_bottom_lines(self, value: int) -> None:
        pass

    @property
    def _last_scroll_end(self) -> int:
        return 0

    @_last_scroll_end.setter
    def _last_scroll_end(self, value: int) -> None:
        pass

    @property
    def _completion_idx(self) -> int:
        return self._model.completion.selected

    @_completion_idx.setter
    def _completion_idx(self, value: int) -> None:
        self._model.completion.selected = int(value)
        self._request_redraw()

    @property
    def _completion(self) -> "_CompletionProxy":
        """兼容 _completion._visible/_popup_height/_items/_texts 访问。"""
        return _CompletionProxy(self._model)

    def force_redraw(self) -> None:
        """强制重绘（非全屏模型：请求下一帧渲染）。"""
        self._request_redraw()

    # ── 生命周期 no-op ─────────────────────────────

    @property
    def is_active(self) -> bool:
        return True

    def set_active(self, active: bool) -> None:
        pass

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def ensure_cursor_in_upper(self) -> None:
        pass

    def ensure_cursor_in_lower(self) -> None:
        pass

    # ── subagent（兼容旧路径） ──────────────────────

    def set_subagent_frame(self, lines) -> None:
        self._model.subagent_lines = list(lines)
        self._request_redraw()

    # ── 内部 ───────────────────────────────────────

    def _request_redraw(self) -> None:
        try:
            self._session.request_bottom_redraw()
        except Exception:
            pass


__all__ = ["InkBridge"]
