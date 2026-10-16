"""渲染器漂移路径光标记账回归测试（``_rewrite_drifted`` 无变化早退）。

背景（用户报障：「命令补全在对话一轮后 TUI 渲染有问题」）：文档高于屏幕
（终端 24 行、文档 38~50 行）且弹窗缩小导致物理缓冲漂移（``_buf_h`` 保持
50 > 文档 38）后，``_rewrite_drifted`` 的「可见区无变化」早退分支把
``_cursor_row`` 改写为物理缓冲末行（24）——但**终端光标实际未移动**（仍在
``place_cursor`` 放置的输入行 22）。记账与实际脱节 2 行，下一帧
``place_cursor`` 按脱节值相对上移 → 光标落到 20，随后等高帧的增量写入整体
下移 2 行：补全弹窗的候选行/提示行错位、旧输入行残留在提示行位置。

本测试锁定：
  1. 无可见区变化时 ``_cursor_row`` 不被改写（记账 == 实际光标）；
  2. 端到端（pyte 重放终端输出）：超屏 + 缩短 + 等高漂移 + 输入行变化的
     完整序列后，屏幕内容与渲染帧一致。
"""

from __future__ import annotations

import io

import pyte

from src.tui.ink.output import Frame, Line, StyledRun
from src.tui.ink.renderer import InkRenderer

HEIGHT = 24
WIDTH = 80


def _frame(n: int, prefix: str, overrides: dict | None = None) -> Frame:
    overrides = overrides or {}
    lines = []
    for i in range(n):
        text = overrides.get(i, f"{prefix}-{i:02d}-" + "x" * 20)
        lines.append(Line([StyledRun(text)]))
    return Frame(lines)


def _expected(frame: Frame, height: int = HEIGHT) -> list:
    lines = [ln.plain.rstrip() for ln in frame.lines]
    if len(lines) >= height:
        return lines[-height:]
    return [""] * (height - len(lines)) + lines


def _display(screen) -> list:
    return [ln.rstrip() for ln in screen.display]


class _Term:
    """捕获渲染器输出并喂给 pyte（模拟终端）。"""

    def __init__(self):
        self.out = io.StringIO()
        self.screen = pyte.Screen(WIDTH, HEIGHT)
        self.stream = pyte.Stream(self.screen)

    def drain(self):
        data = self.out.getvalue()
        self.out.seek(0)
        self.out.truncate(0)
        if data:
            self.stream.feed(data)

    @property
    def cursor_row(self) -> int:
        return self.screen.cursor.y + 1


def _new_renderer() -> tuple:
    term = _Term()
    renderer = InkRenderer(stream=term.out, height=HEIGHT)
    renderer.set_width(WIDTH)
    return renderer, term


class TestIdleEarlyReturnKeepsCursorRow:
    def test_no_visible_change_keeps_cursor_row(self):
        """可见区无变化（仅屏幕上方文档行变化）时不改写 ``_cursor_row``。"""
        renderer, term = _new_renderer()
        try:
            renderer.render(_frame(50, "A"))
            term.drain()
            renderer.place_cursor(36, 6)  # 输入行（文档 1-based）→ 屏幕 22
            term.drain()
            assert renderer._buf_h == 50
            assert renderer._cursor_row == term.cursor_row

            shorter = _frame(38, "B")
            renderer.render(shorter)
            term.drain()
            renderer.place_cursor(36, 6)
            term.drain()
            cursor_before = renderer._cursor_row
            actual_before = term.cursor_row
            assert cursor_before == actual_before == 22

            # 仅文档第 0 行（已滚出可见区）变化 → 可见区无变化 → 早退分支
            top_changed = _frame(38, "B", overrides={0: "TOP-CHANGED"})
            renderer.render(top_changed)
            term.drain()

            assert renderer._cursor_row == cursor_before, (
                "早退分支不得改写 _cursor_row（记账与实际光标须一致）"
            )
            assert term.cursor_row == actual_before
        finally:
            term.out.close()

    def test_end_to_end_offscreen_shrink_then_equality(self):
        """端到端：超屏 + 缩短 + 漂移等高 + 输入行变化后屏幕与帧一致。"""
        renderer, term = _new_renderer()
        try:
            renderer.render(_frame(50, "A"))
            term.drain()
            assert _display(term.screen) == _expected(_frame(50, "A"))

            shorter = _frame(38, "B")
            renderer.render(shorter)
            term.drain()
            renderer.place_cursor(36, 6)
            term.drain()
            assert _display(term.screen) == _expected(shorter)

            # 漂移等高：仅屏幕上方变化 → 屏幕保持
            top_changed = _frame(38, "B", overrides={0: "TOP-CHANGED"})
            renderer.render(top_changed)
            term.drain()
            renderer.place_cursor(36, 6)
            term.drain()
            assert _display(term.screen) == _expected(top_changed)

            # 输入行（可见区）变化 → 屏幕必须与新帧一致（回归点：修复前整体
            # 下移 2 行、旧输入行残留）
            typed = _frame(38, "B", overrides={35: "> /help"})
            renderer.render(typed)
            term.drain()
            renderer.place_cursor(36, 6)
            term.drain()
            assert _display(term.screen) == _expected(typed)

            # 再一轮（弹窗缩小后输入继续变化）
            typed2 = _frame(38, "B", overrides={35: "> /help me"})
            renderer.render(typed2)
            term.drain()
            renderer.place_cursor(36, 8)
            term.drain()
            assert _display(term.screen) == _expected(typed2)
        finally:
            term.out.close()
