"""
统一选择器 — 基于 prompt_toolkit 的交互式选择界面。

合并自:
  - msg_list.py 的 _run_picker (消息列表选择器)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.styles import Style

_logger = logging.getLogger(__name__)


# -- 常量 ------------------------------------------------
_SEP_WIDTH = 25
_PREVIEW_MAX = 3

# -- 高频字符串常量 --------------------------------------
_CURSOR = "cursor"
_SELECTED = "selected"
_ACTION = "action"
_CANCEL = "cancel"


# -- UI 样式 ---------------------------------------------
STYLE = Style.from_dict({
    "title":           "bold cyan",
    "sep":             "#666666",
    "selected":        "bold reverse",
    "highlight":       "bold yellow",
    "normal":          "",
    "dim":             "#666666 italic",
    "hint":            "#888888",
    "status":          "bold green",
    "warning":         "bold yellow",
    "role.user":       "bold cyan",
    "role.assistant":  "bold #44aa44",
    "role.tool":       "#777777",
})


# -- 数据类型 -------------------------------------------

@dataclass
class PickerResult:
    """选择器返回结果。"""
    selected_indices: list[int] = field(default_factory=list)
    selected_items: list[str] = field(default_factory=list)
    action: str = ""
    """操作类型。

    内置动作: "confirmed" | "cancel" | "timeout"
    自定义动作（key_setup 设置时）: 由 key_setup 回调定义
    """


# -- 窗口滚动 -------------------------------------------

def scroll_window(cursor: int, state: dict, total: int) -> tuple[int, int]:
    """计算可见窗口 [start, end)。

    Args:
        cursor: 当前光标位置
        state: 状态字典（必须包含 "scroll" 和 "max" 键）
        total: 总项数

    Returns:
        (start, end) 可见窗口的起始和结束索引
    """
    max_visible = state.get("max", 15)
    if total <= max_visible:
        return 0, total
    offset = state.get("scroll", 0)
    if cursor < offset:
        offset = cursor
    elif cursor >= offset + max_visible:
        offset = cursor - max_visible + 1
    state["scroll"] = offset
    return offset, min(offset + max_visible, total)


# -- 选择器类 -------------------------------------------

class Picker:
    """统一的交互式选择器。

    支持通用选择（单选/多选/超时）和自定义渲染（通过 make_lines 回调）。
    """

    def __init__(
        self,
        title: str,
        items: list,
        multi_select: bool = False,
        default_indices: list[int] | None = None,
        timeout: int = 30,
        make_lines: Callable | None = None,
        key_setup: Callable | None = None,
        initial_cursor: int | None = None,
    ):
        """初始化选择器。

        Args:
            title: 选择界面标题
            items: 选项列表
            multi_select: 是否允许多选
            default_indices: 默认选中的索引列表（超时/取消时回退）
            timeout: 超时秒数（0=无超时）
            make_lines: 可选自定义行渲染函数。
                签名: ``make_lines(items, cursor, state) -> list[tuple[str, str]]``
                如果为 None，使用内置的通用渲染。
            key_setup: 可选自定义按键绑定回调。
                签名: ``key_setup(kb: KeyBindings, state: dict) -> None``
                如果为 None，使用默认按键绑定（Enter确认, Space切换选择）。
            initial_cursor: 初始光标位置。为 None 时从 default_indices 推断。
        """
        self.title = title
        self.items = list(items)
        self.multi_select = multi_select
        self.default_indices = list(default_indices or [])
        self.timeout = timeout
        self.make_lines = make_lines
        self.key_setup = key_setup
        self.initial_cursor = initial_cursor

    # -- 内置渲染 ----------------------------------------

    def _default_make_lines(
        self,
        items: list,
        cursor: int,
        state: dict,
    ) -> list[tuple[str, str]]:
        """内置通用行渲染（当 make_lines=None 时使用）。"""
        lines: list[tuple[str, str]] = []

        # 标题
        lines.append(("class:title", f"  -- {self.title} --\n"))
        lines.append(("class:sep", "  " + "-" * _SEP_WIDTH + "\n"))

        # 提示信息
        if self.multi_select:
            lines.append(("class:hint",
                          "  [空格]切换选择 [回车]确认 [ESC]取消/超时\n"))
        else:
            lines.append(("class:hint",
                          "  [回车]选择 [ESC]取消/超时\n"))

        if self.timeout > 0:
            lines.append(("class:hint", f"  超时: {self.timeout}秒\n"))

        lines.append(("class:sep", "  " + "-" * _SEP_WIDTH + "\n"))

        # 计算可见窗口
        s, e = scroll_window(cursor, state, len(items))

        if s > 0:
            lines.append(("class:dim", "    ↑ 更多选项...\n"))

        for i in range(s, e):
            option_text = items[i]
            if i == cursor:
                if self.multi_select:
                    marker = "\u25c9 " if i in state[_SELECTED] else "\u25cb "
                    lines.append(("class:selected", f" > {marker}{option_text}\n"))
                else:
                    lines.append(("class:selected", f" > {option_text}\n"))
            else:
                if self.multi_select:
                    marker = "\u25c9 " if i in state[_SELECTED] else "\u25cb "
                    lines.append(
                        ("class:highlight" if i in state[_SELECTED]
                         else "class:normal",
                         f"   {marker}{option_text}\n"))
                else:
                    lines.append(("class:normal", f"   {option_text}\n"))

        if e < len(items):
            lines.append(("class:dim", "    ↓ 更多选项...\n"))

        lines.append(("class:sep", "  " + "-" * _SEP_WIDTH + "\n"))

        # 当前选择状态预览
        if self.multi_select:
            sel_count = len(state[_SELECTED])
            if sel_count > 0:
                texts = [items[i] for i in sorted(state[_SELECTED])]
                preview = ", ".join(texts[:_PREVIEW_MAX])
                if len(texts) > _PREVIEW_MAX:
                    preview += f" ... (+{len(texts) - _PREVIEW_MAX}个)"
                lines.append(("class:status",
                              f"  已选择 {sel_count} 项: {preview}\n"))
            else:
                lines.append(("class:status", "  未选择任何项\n"))
        else:
            if cursor < len(items):
                lines.append(("class:status",
                              f"  当前选项: {items[cursor]}\n"))

        if state.get("timeout_reached", False):
            lines.append(("class:warning", "  超时，使用默认选项\n"))

        return lines

    # -- 运行 --------------------------------------------

    # -- 构建按键绑定 ----------------------------------------

    def _build_key_bindings(self, n: int, state: dict) -> KeyBindings:
        """构建按键绑定（从 _build_app 提取，消除方法过长问题）。"""
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            if state[_CURSOR] > 0:
                state[_CURSOR] -= 1

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            if state[_CURSOR] < n - 1:
                state[_CURSOR] += 1

        @kb.add("home")
        def _home(event):
            state[_CURSOR] = 0
            state["scroll"] = 0

        @kb.add("end")
        def _end(event):
            state[_CURSOR] = n - 1

        @kb.add("escape")
        def _cancel(event):
            state[_ACTION] = _CANCEL
            event.app.exit()

        @kb.add("c-c")
        def _cancel_ctrlc(event):
            state[_ACTION] = _CANCEL
            event.app.exit()

        PAGE_STEP = max(1, state.get("max", 15) - 2)

        @kb.add("pageup")
        def _pageup(event):
            state[_CURSOR] = max(0, state[_CURSOR] - PAGE_STEP)

        @kb.add("pagedown")
        def _pagedown(event):
            state[_CURSOR] = min(n - 1, state[_CURSOR] + PAGE_STEP)

        if self.key_setup:
            self.key_setup(kb, state)
        else:
            @kb.add("enter")
            def _confirm(event):
                state[_ACTION] = "confirmed"
                event.app.exit()

            if self.multi_select:
                @kb.add("space")
                def _toggle(event):
                    idx = state[_CURSOR]
                    if idx in state[_SELECTED]:
                        state[_SELECTED].remove(idx)
                    else:
                        state[_SELECTED].add(idx)

        @kb.add(Keys.Any)
        def _catch_all(event):
            event.app.invalidate()

        return kb

    # -- 构建应用（run / run_async 共用） --------------------

    def _build_app(self, n: int) -> tuple[Application, dict, threading.Timer | None, dict | None]:
        """构建 Application 和状态对象（消除 run/run_async 重复代码）。

        Returns:
            (app, state, timer, raw_mode_guard)
        """
        # -- 初始光标位置 --
        if self.initial_cursor is not None:
            cursor = max(0, min(self.initial_cursor, n - 1)) if n > 0 else 0
        elif self.default_indices:
            cursor = max(0, min(self.default_indices[0], n - 1)) if n > 0 else 0
        else:
            cursor = 0

        # -- 选择器状态 --
        state: dict = {
            _CURSOR: cursor,
            "scroll": 0,
            _ACTION: None,
            "max": 15,
            _SELECTED: set(self.default_indices),
            "timeout_reached": False,
        }

        # -- 按键绑定 --
        kb = self._build_key_bindings(n, state)

        # -- 渲染函数 --
        if self.make_lines:
            def renderer():
                return self.make_lines(self.items, state[_CURSOR], state)
        else:
            def renderer():
                return self._default_make_lines(
                    self.items, state[_CURSOR], state,
                )

        # -- UI 构建 --
        control = FormattedTextControl(renderer)
        window = Window(content=control, always_hide_cursor=True)
        layout = Layout(HSplit([window]))

        output = create_output()

        app = Application(
            layout=layout, key_bindings=kb, style=STYLE,
            full_screen=True, mouse_support=True,
            output=output,
        )

        # -- 超时定时器 --
        timer: threading.Timer | None = None
        if self.timeout > 0:
            def timeout_handler():
                state["timeout_reached"] = True
                state[_ACTION] = "timeout"
                try:
                    if app.loop is not None and app.loop.is_running():
                        app.loop.call_soon_threadsafe(app.exit)
                        return
                except Exception:
                    _logger.debug("picker timeout call_soon_threadsafe 失败")
                try:
                    app.exit()
                except Exception:
                    _logger.debug("picker timeout app.exit 失败")

            timer = threading.Timer(self.timeout, timeout_handler)
            timer.daemon = True
            timer.start()

        # -- Raw mode 保护 --
        # 注意：以下 _enter_raw_mode() 与 prompt_toolkit 内部自动设置的
        # raw mode 存在竞态条件。prompt_toolkit 的 Application.run_async()
        # 启动时可能自行设置/切换 stdin raw mode，与这里的显式设置冲突。
        # 此处仅做兜底保护；若失败（如非 tty 环境），记录 warning 但不崩溃。
        try:
            _raw_mode_guard = self._enter_raw_mode()
        except Exception:
            _logger.warning("进入 raw mode 失败（非 tty 环境？），继续运行")
            _raw_mode_guard = None

        return app, state, timer, _raw_mode_guard

    # -- 运行 --------------------------------------------

    def run(self) -> PickerResult:
        """运行交互式选择器（同步入口）。

        Returns:
            PickerResult 包含选中索引、选中项和操作类型。
        """
        return asyncio.run(self._run())

    async def run_async(self) -> PickerResult:
        """异步运行交互式选择器（在 asyncio 事件循环中直接运行）。

        Returns:
            PickerResult 包含选中索引、选中项和操作类型。
        """
        return await self._run()

    async def _run(self) -> PickerResult:
        """内部运行逻辑，消除 run / run_async 重复代码。

        _run 始终在 asyncio 事件循环中执行（run() 通过 asyncio.run() 创建，
        run_async() 在外层事件循环中直接调用）。因此必须始终使用
        await app.run_async()，而非 app.run() —— 后者内部会再次调用
        asyncio.run()，在 Python 3.13+ 中导致嵌套事件循环异常。
        """
        n = len(self.items)
        app, state, timer, guard = self._build_app(n)
        try:
            await app.run_async()
        except (EOFError, KeyboardInterrupt):
            if state[_ACTION] is None:
                state[_ACTION] = _CANCEL
        except Exception as e:
            err_msg = str(e)
            if ("Application is not running" not in err_msg
                    and "Return value already set" not in err_msg):
                raise
        finally:
            self._leave_raw_mode(guard)
            if timer:
                timer.cancel()
        return self._build_result(state)

    def _build_result(self, state: dict) -> PickerResult:
        """从 state 构建 PickerResult（run / run_async 共用）。"""
        n = len(self.items)
        action = state.get(_ACTION) or _CANCEL

        if action == "confirmed":
            if self.multi_select:
                sel_indices = sorted(state[_SELECTED])
            else:
                sel_indices = [state[_CURSOR]]
        elif action in (_CANCEL, "timeout"):
            sel_indices = (
                sorted(self.default_indices) if self.default_indices else []
            )
        else:
            sel_indices = [state[_CURSOR]] if n > 0 else []

        sel_items = [self.items[i] for i in sel_indices if i < n]

        return PickerResult(
            selected_indices=sel_indices,
            selected_items=sel_items,
            action=action,
        )

    # -- Raw Mode 保护（委托到 _terminal 层） --------------

    @staticmethod
    def _enter_raw_mode() -> dict | None:
        """显式设置 stdin 为 raw mode，兜底保护。

        委托到 _terminal.enter_raw_mode()，将终端控制职责归入终端层。
        """
        from .tui._terminal import enter_raw_mode as _enter_raw
        return _enter_raw()

    @staticmethod
    def _leave_raw_mode(guard: dict | None) -> None:
        """恢复原始终端属性，并关闭备用 fd。

        Args:
            guard: _enter_raw_mode 的返回值，None 表示无需恢复。
        """
        from .tui._terminal import leave_raw_mode as _leave_raw
        _leave_raw(guard)
