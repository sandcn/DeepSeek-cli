"""补全菜单缩小时光标差一行修复的回归测试（2026-08-19）。

bug：补全弹窗高度锁定（items 缩小时保持高度、底部补白）期间，
``CompletionPopup`` 用 ``h(TEXT, {"children": "", "height": pad_rows})``
补白——但 ink 布局系统 ``_layout_measure._measure`` 的 TEXT 分支**完全
忽略显式 ``height`` prop**（高度恒 = len(lines)，空文本 0 行）：

  - 弹窗实际渲染高度 = 1(标题) + count(选项) + **0(补白丢失)** + 1(提示)
  - ``_completion_height`` / ``position_cursor`` 按锁定高度
    （= 1 + n_rows + 1，n_rows = locked-2）计算输入光标行

两者差 ``pad_rows = n_rows - count`` 行——items 缩小 1 项（如打字过滤
20→19）时 pad_rows=1，**光标偏下 1 行（不在输入框里，差一行）**；
缩小 2/3 项时差 2/3 行；大幅缩小（> _LOCKED_PAD_LIMIT）时高度跟随
（pad=0）不差。

修复：``_layout_measure.py`` TEXT 分支尊重显式 ``height`` prop
（min-height 语义：``h = max(len(lines), int(height))``，畸形值回退
内容高度）——补白 TEXT 真正占 pad_rows 行，弹窗实际渲染高度与
``_completion_height`` 一致，光标回到输入行。
"""

from __future__ import annotations

import io

import pytest

from src.tui.ink.element import h, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.layout import layout_tree
from src.tui.ink import components as _components
from src.tui.ink.renderer import InkRenderer
from src.tui.ink import _cursor
from src.tui.app.model import AppModel
from src.tui.app.app import App

try:
    import pyte
except ImportError:  # pragma: no cover - 环境未安装 pyte 时跳过终端模拟用例
    pyte = None


# ── 布局层：TEXT 显式 height（min-height 语义） ──────────────


def _render_text_height(props: dict, width: int = 40) -> int:
    """渲染单个 TEXT 元素，返回文档总高度（= 该 TEXT 布局高度）。"""
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(TEXT, props), width, 0)
    return layout_tree(root, width)


class TestTextExplicitHeight:
    """TEXT 显式 height prop 的布局语义（修复核心）。"""

    def test_empty_text_with_height_occupies_rows(self):
        """空文本 + 显式 height=N → 占 N 行（修复前恒 0 行——补白丢失根因）。"""
        assert _render_text_height({"children": "", "height": 3}) == 3

    def test_empty_text_without_height_is_zero(self):
        """空文本无 height → 0 行（既有空 TEXT 语义零回归）。"""
        assert _render_text_height({"children": ""}) == 0

    def test_height_one_single_line_unchanged(self):
        """非空单行 + height=1 → 1 行（全项目 29 处既有调用零回归）。"""
        assert _render_text_height({"children": "hello", "height": 1}) == 1

    def test_content_taller_than_height_not_clipped(self):
        """内容行数 > 显式 height → 取较大者（min-height 语义，不截断内容）。"""
        assert _render_text_height({"children": "aaa bbb ccc ddd", "height": 1}, width=8) > 1

    def test_malformed_height_falls_back_to_content(self):
        """畸形 height（百分比/字符串）→ 回退内容高度不抛异常。"""
        assert _render_text_height({"children": "x", "height": "50%"}) == 1

    def test_height_zero_on_empty_keeps_zero(self):
        """height=0 + 空文本 → 0 行（显式 0 不放大）。"""
        assert _render_text_height({"children": "", "height": 0}) == 0


# ── 端到端：补全弹窗缩小 + 光标定位 ────────────────────────


@pytest.fixture(autouse=True)
def _fix_popup_rows(monkeypatch):
    """固定弹窗选项行数预算（消除真实终端高度依赖）。

    ``_completion_item_rows`` 经 TerminalWidthCache 读真实终端高度——测试
    固定为 20（大于测试用的 items 数，不触发行数截断）。
    """
    monkeypatch.setattr(
        "src.tui._input_metrics._completion_item_rows", lambda: 20,
    )


class _CompletionShrinkHarness:
    """补全弹窗渲染 + 光标定位端到端夹具。"""

    def __init__(self, height: int = 40):
        self.model = AppModel()
        self.model.width = 80
        self.rec = Reconciler(schedule_callback=None)
        self.root = self.rec.create_root()
        self.renderer = InkRenderer(stream=io.StringIO(), height=height)
        self.screen = pyte.Screen(80, height) if pyte else None
        self.stream = pyte.Stream(self.screen) if pyte else None

    def set_items(self, n: int, selected: int = 0):
        c = self.model.completion
        c.visible = True
        c.items = [f"cmd-{i:02d}" for i in range(n)]
        c.texts = list(c.items)
        c.types = ["command"] * n
        c.selected = selected

    def render(self):
        """渲染一帧 + 光标定位（与 session._render_frame 相同链路）。"""
        el = h(App, {"model": self.model, "width": 80})
        self.rec.render(self.root, el, 80, 40)
        frame = _components.render_frame(self.root, 80)
        self.renderer.render(frame)
        fiber = _cursor.find_input_fiber(self.root)
        assert fiber is not None, "未找到输入区 fiber"
        _cursor.position_cursor(self.renderer, 80, fiber)
        if self.stream is not None:
            out = self.renderer._stream.getvalue()
            self.screen.reset()
            self.stream.feed(out)
        return frame

    def cursor_vs_input_row(self) -> int:
        """pyte 光标行与输入行（'> ' 开头）的行差（0 = 光标在输入行）。"""
        assert self.screen is not None
        input_row = None
        for i, line in enumerate(self.screen.display):
            if line.startswith("> "):
                input_row = i
                break
        assert input_row is not None, "屏幕上未找到输入行（'> ' 前缀）"
        return self.screen.cursor.y - input_row


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestCompletionShrinkCursor:
    """补全菜单缩小时光标定位（原 bug：items 缩小 pad_rows 行时光标偏下）。"""

    def test_shrink_one_item_cursor_on_input_row(self):
        """★ 原发 bug 用例：20 项 → 19 项（锁定高度补白 1 行）光标恰好在输入行。

        修复前：补白 TEXT 实际 0 行 → 弹窗矮 1 行 → 光标偏下 1 行
        （「光标不会定位在输入框里，差一行」）。
        """
        t = _CompletionShrinkHarness()
        t.set_items(20)
        t.render()
        t.set_items(19)
        frame = t.render()
        # 高度锁定语义：补白 ≤ _LOCKED_PAD_LIMIT(3) 时弹窗高度保持 → 帧等高
        # ★ BEAUTY-36（2026-08-19）：欢迎屏单行 → 5 行欢迎卡（+4 行），
        #   空状态帧高度快照 29 → 33。
        assert t.model.completion.locked_height == 22
        assert frame.height == 33, "锁定高度下帧高度应保持（底部补白占行）"
        assert t.cursor_vs_input_row() == 0, "光标应恰好在输入行（修复前差 1 行）"

    def test_shrink_three_items_cursor_on_input_row(self):
        """20 → 17 项（补白 3 行，锁定上限）：帧等高 + 光标在输入行。"""
        t = _CompletionShrinkHarness()
        t.set_items(20)
        t.render()
        t.set_items(17)
        frame = t.render()
        assert t.model.completion.locked_height == 22
        assert frame.height == 33  # BEAUTY-36：欢迎屏 5 行（29+4）
        assert t.cursor_vs_input_row() == 0, "修复前差 3 行"

    def test_grow_then_shrink_sequence(self):
        """逐级缩小序列（20→19→18→17→16→2）：每帧光标都在输入行。"""
        t = _CompletionShrinkHarness()
        t.set_items(20)
        t.render()
        for n in (19, 18, 17, 16, 2):
            t.set_items(n)
            t.render()
            assert t.cursor_vs_input_row() == 0, (
                f"items={n} 时光标偏离输入行"
            )
        # 大幅缩小后高度跟随（locked = min(n,20)+2 = 4）
        assert t.model.completion.locked_height == 4

    def test_shrink_big_allows_height_collapse(self):
        """大幅缩小（20→2，补白 18 > 上限 3）：高度允许缩小到 need。"""
        t = _CompletionShrinkHarness()
        t.set_items(20)
        t.render()
        t.set_items(2)
        frame = t.render()
        assert t.model.completion.locked_height == 4  # 缩到 2+2
        assert frame.height == 15  # BEAUTY-36：欢迎屏 5 行（11+4）
        assert t.cursor_vs_input_row() == 0

    def test_navigate_after_shrink_cursor_still_on_input(self):
        """缩小后弹窗内导航（selected 变化）光标仍在输入行。"""
        t = _CompletionShrinkHarness()
        t.set_items(20)
        t.render()
        t.set_items(19, selected=5)
        t.render()
        assert t.cursor_vs_input_row() == 0


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestCompletionLockedFrameHeight:
    """锁定高度语义：items 小幅缩小时帧高度不变（等高 diff 防闪烁契约）。"""

    def test_frame_height_stable_on_small_shrink(self):
        """20→19→18→17 帧高度恒定；16（大幅）时按 need 缩小。"""
        t = _CompletionShrinkHarness()
        t.set_items(20)
        h0 = t.render().height
        for n in (19, 18, 17):
            t.set_items(n)
            assert t.render().height == h0, f"items={n} 帧高度应锁定（补白占行）"
        t.set_items(16)
        assert t.render().height < h0, "大幅缩小（补白 4 > 上限 3）应允许缩小"

    def test_pad_rows_rendered_as_blank_lines(self):
        """补白行在帧中为空行（弹窗提示行下方有 pad_rows 行空白）。"""
        t = _CompletionShrinkHarness()
        t.set_items(20)
        t.render()
        t.set_items(19)
        frame = t.render()
        lines_text = ["".join(r.text for r in ln.runs) for ln in frame.lines]
        # 弹窗行序：标题 → 19 项候选 → 1 行补白 → 提示行（completionPopup
        # children 顺序：head/control/pad/hint——补白在最后候选与提示行之间）
        hint_idx = next(
            i for i, txt in enumerate(lines_text) if "Tab" in txt and "Esc" in txt
        )
        assert lines_text[hint_idx - 1].strip() == "", (
            "最后候选与提示行之间应有 1 行补白空白（修复前补白丢失）"
        )
        assert "cmd-18" in lines_text[hint_idx - 2], "倒数第二行应为最后一项"
