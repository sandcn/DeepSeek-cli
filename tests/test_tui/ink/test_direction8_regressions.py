"""方向8 渲染正确性回归测试。

覆盖本轮发现的渲染 bug：
  1. ``_canvas_row_to_line`` 宽字符重叠键死循环（画布行含 CJK 宽字符 +
     后续键落在被占用列时无限循环 → 整帧渲染挂起）。
  2. 窄屏（width<4）用户消息 ``> `` 前缀 + CJK 内容超宽（宽度不变量破坏）。
  3. 窄屏补全弹窗标题/选项/提示行超宽。
"""

from __future__ import annotations

import io

from src.tui._screen import wcswidth_simple
from src.tui.app.model import AppModel, CompletionState
from src.tui.app import app as app_module
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame, _canvas_row_to_line
from src.tui.ink.output import Line, StyledRun
from src.tui.core.style import Style
from src.tui._const import RenderCommand
from src.tui.app.apply import apply_cmd
from types import SimpleNamespace


def _mk(cid, **kw):
    return SimpleNamespace(cid=cid, **kw)


def _render(model, width, height=10):
    r = Reconciler()
    root = r.create_root()
    el = app_module.build_app_element(model, width)
    r.render(root, el, width, height)
    return render_frame(root, width)


class TestCanvasRowToLineNoInfiniteLoop:
    """BUG：_canvas_row_to_line 对宽字符重叠键死循环（修复后不挂起）。"""

    def test_cjk_adjacent_keys_no_hang(self):
        """画布行含宽字符 + 重叠键时 _canvas_row_to_line 正常返回。"""
        # 模拟宽字符（补，宽2 占列1,2）后跟一个画到列2的字符（重叠）
        row = {
            0: (" ", None),
            1: ("补", Style(fg=45)),
            2: ("e", None),  # 与 '补' 的第二列重叠
            3: ("全", Style(fg=45)),
        }
        line = _canvas_row_to_line(row)
        assert isinstance(line, Line)

    def test_cjk_render_no_hang(self):
        """含宽字符输入 + 补全弹窗的完整渲染不挂起（修复前死循环）。"""
        model = AppModel()
        model.width = 5
        model.input_text = "你好"
        model.input_cursor = 2
        model.completion = CompletionState(
            visible=True,
            items=["/help", "/model", "/config", "/exit"],
            texts=["/help", "/model", "/config", "/exit"],
            selected=0,
            types=["command"] * 4,
            descriptions=["显示帮助信息帮助", "切换模型", "查看配置", "退出程序"],
            split_desc=True,
        )
        frame = _render(model, 5)
        assert frame.height > 0


class TestNarrowScreenNoOverflow:
    """窄屏渲染不超宽（行级 diff 宽度不变量）。"""

    def _assert_no_overflow(self, width):
        model = AppModel()
        model.width = width
        apply_cmd(model, _mk(RenderCommand.USER_MSG, text="你好世界 hello world" * 3))
        apply_cmd(model, _mk(RenderCommand.MAIN_PHASE, phase="answering"))
        apply_cmd(model, _mk(RenderCommand.CONTENT, text="这是内容 测试 wrap 1234567890" * 2))
        apply_cmd(model, _mk(RenderCommand.PHASE_DONE, phase="content"))
        apply_cmd(model, _mk(RenderCommand.TOOL_OPEN, tool_id="t", tool_name="bash", detail="长命令" * 4))
        apply_cmd(model, _mk(RenderCommand.TOOL_OUTPUT, tool_id="t", text="输出内容abc 123 456" * 2))
        apply_cmd(model, _mk(RenderCommand.TOOL_CLOSE, tool_id="t", success=True))
        frame = _render(model, width)
        for i, line in enumerate(frame.lines):
            assert wcswidth_simple(line.plain) <= width, (
                f"width={width} 行 {i} 超宽: {line.plain!r}"
            )

    def test_width2(self):
        self._assert_no_overflow(2)

    def test_width3(self):
        self._assert_no_overflow(3)

    def test_width4(self):
        self._assert_no_overflow(4)

    def test_width5(self):
        self._assert_no_overflow(5)

    def test_width8(self):
        self._assert_no_overflow(8)


class TestCompletionPopupNarrowScreen:
    """补全弹窗（分栏/非分栏）在窄屏不超宽。"""

    def test_split_completion_width5(self):
        model = AppModel()
        model.width = 5
        model.input_text = "你好"
        model.input_cursor = 2
        model.completion = CompletionState(
            visible=True,
            items=["/help", "/model", "/config", "/exit"],
            texts=["/help", "/model", "/config", "/exit"],
            selected=0,
            types=["command"] * 4,
            descriptions=["显示帮助信息帮助", "切换模型", "查看配置", "退出程序"],
            split_desc=True,
        )
        frame = _render(model, 5)
        for i, line in enumerate(frame.lines):
            assert wcswidth_simple(line.plain) <= 5, (
                f"width=5 行 {i} 超宽: {line.plain!r}"
            )

    def test_plain_completion_width8(self):
        model = AppModel()
        model.width = 8
        model.input_text = "hello"
        model.input_cursor = 5
        model.completion = CompletionState(
            visible=True,
            items=["/help", "/model", "/config"],
            texts=["/help", "/model", "/config"],
            selected=1,
            types=["command"] * 3,
            descriptions=["显示帮助", "切换模型", "查看配置"],
        )
        frame = _render(model, 8)
        for i, line in enumerate(frame.lines):
            assert wcswidth_simple(line.plain) <= 8, (
                f"width=8 行 {i} 超宽: {line.plain!r}"
            )
