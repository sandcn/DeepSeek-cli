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
        # ★ P3（review）：显式初始化 ``_last_completion_idx``——修复前仅在
        #   show/hide/cycle 时动态赋值、读取经 ``getattr(..., 0)`` 兜底
        #   （隐式契约）。
        self._last_completion_idx = 0

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

    @property
    def is_completion_loading(self) -> bool:
        """补全弹窗是否处于「数据加载中」占位态（此时 Tab 不应用补全）。"""
        c = self._model.completion
        return bool(c.visible and getattr(c, "loading", False))

    def show_completions(self, items, selected_idx, texts=None, start_pos=0,
                         orig_prefix="", title="补全", types=None,
                         match_prefix="", descriptions=None,
                         split_desc=False, loading=False) -> None:
        # loading=True：异步数据源未就绪——允许空 items（弹窗显示「加载中…」
        # 占位，数据就绪后经 ``show_completions`` 再次调用刷新为真实候选）。
        if not items and not loading:
            return
        # ★ 原子替换完成态对象（而非逐字段更新）：异步补全下本方法可能由
        #   后台加载线程调用，渲染线程并发读取——整体替换保证读到的
        #   items/texts/selected 始终自洽（逐字段更新存在「items 已换、texts
        #   未换」的中间态，Tab 应用会拿到错位候选）。旧状态的弹窗行缓存与
        #   滚动偏移与高度锁定继承（内容未变时复用，避免每帧重建）。
        old = self._model.completion
        c = CompletionState()
        c.visible = True
        c.loading = bool(loading)
        c.title = title
        c.items = list(items)
        c.texts = list(texts) if texts is not None else list(items)
        # ★ 1.8 修复：selected_idx 负值钳制到 0（修复前 min(int(-1), len-1) = -1
        #   → 负索引越界；改为 max(0, min(...)) 双向钳制；超上界仍钳到 len-1）。
        #   items 空（loading 占位）时钳到 0。
        c.selected = max(0, min(int(selected_idx), len(c.items) - 1))
        c.start_pos = int(start_pos)
        c.orig_prefix = orig_prefix
        c.types = list(types) if types is not None else []
        c.match_prefix = match_prefix
        # Claude TUI parity 步骤 3.7：斜杠命令描述（缺省空列表兼容旧调用）
        c.descriptions = list(descriptions) if descriptions is not None else []
        # 分栏说明模式（user_select）：True 时弹窗右侧显示当前选中项说明
        c.split_desc = bool(split_desc)
        if old.visible:
            c.locked_height = old.locked_height
            c._popup_lines_cache = old._popup_lines_cache
            c._popup_scroll = old._popup_scroll
        self._model.completion = c
        # 方向A 步骤1：show 时同步 _last_completion_idx（修复陈旧索引——
        # 新补全会话不再读到 hide 保留的旧索引；hide 语义保留，message_editor 依赖）。
        self._last_completion_idx = c.selected
        self._request_redraw()

    def hide_completions(self) -> None:
        if not self._model.completion.visible:
            return
        # 保存隐藏前选中索引（兼容 _BottomBar.get_selected_completion_index）
        self._last_completion_idx = self._model.completion.selected
        # ★ P3（review）：删除冗余的 ``locked_height = 0``（下方整体替换为
        #   ``CompletionState()``，其 locked_height 本就是 0）。
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

    # ── 通用浮动窗口（2026-10-21 用户需求） ─────────

    @staticmethod
    def _window_id(window) -> str:
        """窗口 id 归一化（``str`` + ``strip``；缺失/空白返回空串）。

        校验口径统一（2026-10-21）：渲染层按 strip 后的 id 去重/丢弃空白 id，
        bridge 必须同口径——否则 ``"   "`` 这类 id 会登记进模型却永远不显示。
        """
        wid = getattr(window, "id", None)
        return str(wid).strip() if wid is not None else ""

    def open_floating_window(self, window) -> bool:
        """打开（或替换同 id）浮动窗口并请求重绘。

        Args:
            window: ``app.floating.FloatingWindowSpec``。

        Returns:
            True=已登记；False=无效规格（None / 空白 id / 非法 render）——
            不改变模型。

        ``close_on_escape=True`` 且未提供 ``on_close`` 时自动注入关闭回调
        （``lambda: self.close_floating_window(id)``）——调用方无需持有 bridge。
        """
        if window is None:
            return False
        wid = self._window_id(window)
        if not wid:
            _logger.warning("浮窗 id 为空/空白，已忽略：%r", getattr(window, "id", None))
            return False
        render = getattr(window, "render", None)
        if render is not None and not callable(render):
            _logger.warning(
                "浮窗 render 不是可调用对象，已忽略：id=%r type=%s",
                wid, type(render).__name__,
            )
            return False
        # Esc 关闭：bridge 注入默认关闭实现（未显式提供时）
        if (
            getattr(window, "close_on_escape", False)
            and getattr(window, "on_close", None) is None
        ):
            copy_with = getattr(window, "copy_with", None)
            if callable(copy_with):
                window = copy_with(
                    on_close=lambda: self.close_floating_window(wid),
                )
            else:
                try:
                    window.on_close = lambda: self.close_floating_window(wid)
                except Exception:
                    _logger.debug(
                        "浮窗 on_close 注入失败 id=%r", wid, exc_info=True,
                    )
        windows = list(getattr(self._model, "floating_windows", None) or [])
        replaced = False
        for i, existing in enumerate(windows):
            if self._window_id(existing) == wid:
                windows[i] = window
                replaced = True
                break
        if not replaced:
            windows.append(window)
        self._model.floating_windows = windows
        self._request_redraw()
        return True

    def update_floating_window(self, window_id: str, **changes) -> bool:
        """更新已打开窗口的规格字段（不可变替换）并请求重绘。

        Returns:
            True=找到并更新；False=窗口不存在 / 请求改 id / 含未知字段
            （无操作；未知字段此前会「返回 True 但什么都没改」）。

        禁止改 ``id``：``copy_with`` 接受 id 会让窗口在模型里换名——按旧 id
        调用的 update/close 全部失效，且可能与既有 id 冲突。
        """
        wid = str(window_id).strip() if window_id is not None else ""
        if not wid:
            return False
        if "id" in changes:
            _logger.warning("浮窗 update 不允许修改 id（请关闭后重开）：id=%r", wid)
            return False
        windows = list(getattr(self._model, "floating_windows", None) or [])
        for i, existing in enumerate(windows):
            if self._window_id(existing) != wid:
                continue
            copy_with = getattr(existing, "copy_with", None)
            if callable(copy_with):
                known = getattr(type(existing), "__slots__", ())
                unknown = [key for key in changes if known and key not in known]
                if unknown:
                    _logger.warning(
                        "浮窗 update 含未知字段，已拒绝：id=%r fields=%r",
                        wid, unknown,
                    )
                    return False
                windows[i] = copy_with(**changes)
            else:  # 非 FloatingWindowSpec 的鸭子类型：逐字段赋值
                for key, value in changes.items():
                    try:
                        setattr(existing, key, value)
                    except Exception:
                        _logger.debug(
                            "浮窗字段更新失败 id=%r key=%r", wid, key,
                            exc_info=True,
                        )
            self._model.floating_windows = windows
            self._request_redraw()
            return True
        return False

    def close_floating_window(self, window_id: str) -> bool:
        """关闭指定浮动窗口（下一帧原内容自动还原）。"""
        wid = str(window_id).strip() if window_id is not None else ""
        if not wid:
            return False
        windows = list(getattr(self._model, "floating_windows", None) or [])
        kept = [w for w in windows if self._window_id(w) != wid]
        if len(kept) == len(windows):
            return False
        self._model.floating_windows = kept
        self._request_redraw()
        return True

    def clear_floating_windows(self) -> None:
        """关闭全部浮动窗口（幂等）。"""
        if getattr(self._model, "floating_windows", None):
            self._model.floating_windows = []
            self._request_redraw()

    # ── /loop 循环状态（循环浮窗数据源） ─────────────

    _LOOP_STATUS_FIELDS = (
        "active", "total", "round", "run", "runs_per_round", "prompt",
        # 计时锚点（2026-10-21 用户需求）：浮窗按「当前时间 - 本轮开始时间」
        # 动态显示本轮已运行时长，随 `/loop` 每轮/每次运行更新。
        "round_started_at", "run_started_at",
    )

    def _loop_status(self):
        """取（必要时创建）模型的循环状态对象。"""
        st = getattr(self._model, "loop_status", None)
        if st is None:
            from .app._state_types import LoopStatusState
            st = LoopStatusState()
            self._model.loop_status = st
        return st

    def set_loop_status(self, **fields) -> None:
        """更新 ``/loop`` 循环状态并请求重绘（仅接受已知字段）。"""
        st = self._loop_status()
        changed = False
        for key in self._LOOP_STATUS_FIELDS:
            if key in fields:
                setattr(st, key, fields[key])
                changed = True
        if changed:
            self._request_redraw()

    def reset_loop_status(self) -> None:
        """重置循环状态（循环结束/中断；浮窗内容清空）。"""
        from .app._state_types import LoopStatusState
        self._model.loop_status = LoopStatusState()
        self._request_redraw()

    # ── 内部 ───────────────────────────────────────

    def _request_redraw(self) -> None:
        # P2-8：不再裸吞异常——记录 debug 日志（request_bottom_redraw 异常
        # 属非关键降级，不阻断调用方）。
        try:
            self._session.request_bottom_redraw()
        except Exception:
            _logger.debug("request_bottom_redraw 异常", exc_info=True)


__all__ = ["InkBridge", "_CompletionProxy"]
