"""补全弹窗闪烁修复测试（commit 顶部对齐 + 弹窗高度锁定 + 补白上限）。

覆盖用户需求「补全弹出时，改变内容时，tui 会闪」的修复：
  - **顶部对齐局部重写**（InkRenderer）：文档仍高于屏幕时，弹窗/尾部区域
    高度变化只重写变化行 + 清残留，弹窗上方（历史消息）永不重写——
    消除打字时补全弹窗 items 数量变化引发的全可见区重写闪烁。
  - **弹窗高度锁定**（_completion_height）：弹窗打开期间 items 小幅减少时
    高度保持（底部补白，≤ _LOCKED_PAD_LIMIT 行），doc 高度不变 → 等高 diff
    只重写弹窗行（不闪）。
  - **补白上限**（_LOCKED_PAD_LIMIT）：items 大幅减少（如 20→1 项）时允许
    高度缩小——避免弹窗底部渲染十余行空白（渲染异常）。
"""

from __future__ import annotations

import io

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer
from src.tui.app.input_area import _completion_height, _build_lines
from src.tui.app.model import CompletionState
from src.tui.ink.fiber import Fiber


def _frame(*plain_lines: str) -> Frame:
    return Frame(Line.of(l) for l in plain_lines)


class _Box:
    """极简 LayoutBox 桩（input_area 渲染测试用）。"""

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x=0, y=0, w=80, h=1):
        self.x = x
        self.y = y
        self.w = w
        self.h = h


class TestCompletionPopupShrinkNoHistoryRewrite:
    """弹窗 items 数量变化（缩短）不重写历史消息（顶部对齐局部重写）。"""

    def test_popup_shrink_only_rewrites_popup_area(self):
        """文档高于屏幕：弹窗 5→2 项只重写弹窗区域 + 清残留，历史不重写。"""
        H = 30
        hist = [f"历史消息 {i}" for i in range(25)]
        # prev：弹窗 5 项（标题 + 5 项 + 提示）
        prev = (
            ["header", "分隔线"] + hist + ["status"]
            + ["▍ 补全 (1/5)", " ▶ f0", "   f1", "   f2", "   f3", "   f4", "Tab ↑↓ Esc"]
            + ["上分隔线", "> src/", "时间戳"]
        )
        # new：弹窗 2 项
        new = (
            ["header", "分隔线"] + hist + ["status"]
            + ["▍ 补全 (1/2)", " ▶ t0", "   t1", "Tab ↑↓ Esc"]
            + ["上分隔线", "> src/t", "时间戳"]
        )
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        r.render(_frame(*prev))
        out.seek(0)
        out.truncate()
        r.render(_frame(*new))
        val = out.getvalue()
        # 无 clear_screen（非 resize 均增量）
        assert "\x1b[2J" not in val
        # 历史消息不重写（弹窗上方保持）——仅弹窗区域 + input 区域重写
        assert "历史消息 20" not in val, (
            f"弹窗缩小不应重写历史消息（闪烁根因），实际: {val!r}"
        )
        # 重写行数 ≈ 弹窗 5 行 + input 3 行 + 清残留 3 行 ≤ 15
        rewrite_count = val.count("\x1b[K")
        assert rewrite_count <= 15, (
            f"弹窗缩小应只重写弹窗区域（≤15 行），实际 {rewrite_count}: {val!r}"
        )

    def test_popup_grow_after_shrink_no_history_rewrite(self):
        """弹窗 5→2→4 反复变化：全程历史不重写（顶部对齐局部 + 高度锁定）。"""
        H = 30
        hist = [f"历史消息 {i}" for i in range(25)]
        base = ["header", "分隔线"] + hist + ["status"]
        f5 = base + ["▍ 补全 (1/5)", " ▶ f0", "   f1", "   f2", "   f3", "   f4", "Tab ↑↓ Esc"] + ["上分隔线", "> src/", "时间戳"]
        f2 = base + ["▍ 补全 (1/2)", " ▶ t0", "   t1", "Tab ↑↓ Esc"] + ["上分隔线", "> src/t", "时间戳"]
        f4 = base + ["▍ 补全 (1/4)", " ▶ a0", "   a1", "   a2", "   a3", "Tab ↑↓ Esc"] + ["上分隔线", "> src/a", "时间戳"]
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        r.render(_frame(*f5))
        out.seek(0)
        out.truncate()
        for f in (f2, f4, f2):
            r.render(_frame(*f))
            val = out.getvalue()
            out.seek(0)
            out.truncate()
            assert "\x1b[2J" not in val
            assert "历史消息 20" not in val, f"弹窗变化不应重写历史: {val!r}"
            assert val.count("\x1b[K") <= 15


class TestCompletionHeightLocked:
    """弹窗高度锁定（_completion_height 补白上限：items 小幅减少保持、大幅减少缩小）。"""

    def _mk(self, items, visible=True):
        c = CompletionState()
        c.visible = visible
        c.items = list(items)
        c.texts = list(items)
        c.types = ["file"] * len(items)
        c.descriptions = [""] * len(items)
        c.split_desc = False
        return c

    def test_height_kept_on_small_shrink_while_open(self):
        """items 小幅减少（补白 ≤ _LOCKED_PAD_LIMIT）：高度保持（锁定，防闪烁）。"""
        c = self._mk([f"f{i}" for i in range(5)])
        h5 = _completion_height(c, 80)
        assert h5 == 7  # 标题 + 5 项 + 提示
        # items 5→2：need 4（补白 3 ≤ 上限 3）→ 高度保持 7（锁定）
        c.items = [f"f{i}" for i in range(2)]
        c.texts = c.items
        h2 = _completion_height(c, 80)
        assert h2 == 7, f"items 小幅减少高度应保持（锁定），实际 {h2}"
        # items 增加超过锁定：高度跟随（增高）
        c.items = [f"f{i}" for i in range(9)]
        c.texts = c.items
        h9 = _completion_height(c, 80)
        assert h9 == 11, f"items 增加高度应跟随，实际 {h9}"

    def test_height_shrinks_on_large_shrink_while_open(self):
        """items 大幅减少（补白 > _LOCKED_PAD_LIMIT）：允许缩小，避免弹窗大片空白。"""
        c = self._mk([f"f{i}" for i in range(5)])
        h5 = _completion_height(c, 80)
        assert h5 == 7
        # items 5→1：need 3（补白 4 > 上限 3）→ 缩小到 3（不再保留 4 行空白）
        c.items = [f"f{i}" for i in range(1)]
        c.texts = c.items
        h1 = _completion_height(c, 80)
        assert h1 == 3, f"items 大幅减少应缩小高度（避免大片空白），实际 {h1}"
        # 缩小后 items 再次小幅增加：仍跟随（need 2 > locked 3 时增高）
        c.items = [f"f{i}" for i in range(2)]
        c.texts = c.items
        h2 = _completion_height(c, 80)
        assert h2 == 4, f"items 增加高度应跟随，实际 {h2}"

    def test_height_locked_cap_on_many_to_few(self):
        """20→1 项：高度从 16 缩到 3（补白上限生效，不再渲染十余行空白）。"""
        c = self._mk([f"f{i}" for i in range(20)])
        h20 = _completion_height(c, 80)
        assert h20 == 16
        c.items = ["f0"]
        c.texts = c.items
        h1 = _completion_height(c, 80)
        assert h1 == 3, f"20→1 项高度应缩小到 3（避免大片空白），实际 {h1}"

    def test_height_reset_after_close(self):
        c = self._mk([f"f{i}" for i in range(5)])
        _completion_height(c, 80)
        assert c.locked_height == 7
        # 关闭弹窗（hide_completions 重置 locked_height）
        c.locked_height = 0
        c.visible = False
        assert _completion_height(c, 80) == 0
        # 重新打开：重新锁定
        c.visible = True
        c.items = [f"g{i}" for i in range(3)]
        c.texts = c.items
        assert _completion_height(c, 80) == 5


class TestCompletionPopupNoLargeBlank:
    """补白上限（_LOCKED_PAD_LIMIT）— items 大幅减少后弹窗不渲染大片空白。"""

    def _lines(self, items):
        c = CompletionState(
            visible=True, items=list(items), texts=list(items), selected=0,
            title="补全", types=[""] * len(items), descriptions=[""] * len(items),
            split_desc=False,
        )
        h = _completion_height(c, 80)
        props = dict(
            text="", cursor_pos=0, prompt="> ", completion=c,
            status_active=False, cpu=0, mem=0,
        )
        f = Fiber("host", "input-area", props)
        f.layout_box = _Box(0, 0, 80, 1)
        lines = _build_lines(f)
        return h, lines[:h]  # 弹窗区 = 前 h 行（标题 + 候选 + 提示）

    def test_many_to_few_no_large_blank_regression(self):
        """20→1 项：弹窗高度缩到 3，渲染区无大片空白（修复前高度保持 16 → 13 行空白）。"""
        # 同一 CompletionState 持续（locked_height 累积）——模拟打字 items 减少
        c = CompletionState(
            visible=True, items=[f"f{i}" for i in range(20)],
            texts=[f"f{i}" for i in range(20)], selected=0,
            title="补全", types=[""] * 20, descriptions=[""] * 20, split_desc=False,
        )
        h20 = _completion_height(c, 80)
        assert h20 == 16
        # items 大幅减少
        c.items = ["f0"]
        c.texts = c.items
        h1 = _completion_height(c, 80)
        assert h1 == 3
        props = dict(
            text="", cursor_pos=0, prompt="> ", completion=c,
            status_active=False, cpu=0, mem=0,
        )
        f = Fiber("host", "input-area", props)
        f.layout_box = _Box(0, 0, 80, 1)
        lines = _build_lines(f)
        popup = lines[:h1]
        blanks = sum(1 for l in popup if not l.plain.strip())
        assert blanks == 0, (
            f"items 大幅减少后弹窗不应有大片空白，实际空白 {blanks} 行: "
            f"{[l.plain for l in popup]!r}"
        )
        assert popup[0].plain == " ▍ 补全 (1/1)"
        assert popup[-1].plain == " Tab ↑↓ Esc"

    def test_small_shrink_still_pads_limited_regression(self):
        """5→2 项：高度保持 7（补白 3 行 ≤ 上限，防闪烁）——空白行数受控。"""
        c = CompletionState(
            visible=True, items=[f"f{i}" for i in range(5)],
            texts=[f"f{i}" for i in range(5)], selected=0,
            title="补全", types=[""] * 5, descriptions=[""] * 5, split_desc=False,
        )
        h5 = _completion_height(c, 80)
        assert h5 == 7
        c.items = ["f0", "f1"]
        c.texts = c.items
        h2 = _completion_height(c, 80)
        assert h2 == 7  # 锁定保持
        props = dict(
            text="", cursor_pos=0, prompt="> ", completion=c,
            status_active=False, cpu=0, mem=0,
        )
        f = Fiber("host", "input-area", props)
        f.layout_box = _Box(0, 0, 80, 1)
        lines = _build_lines(f)
        popup = lines[:h2]
        blanks = sum(1 for l in popup if not l.plain.strip())
        # 标题 + 2 项 + 提示 = 4 行内容；7 行弹窗 → 补白 3 行（≤ 上限）
        assert blanks == 3, f"5→2 项补白应为 3 行（防闪烁），实际 {blanks}"


class TestCompletionPopupNewlineSanitized:
    """方向F·步骤15 — 候选项文本含换行符时的渲染防御（/load 会话标题等）。

    多行用户消息作为会话标题时 title 含 ``\\n``——Line 内嵌字面换行会把
    一"行"拆成多行，破坏帧行号/diff/光标定位。渲染前统一归一化为空格。
    """

    def _build_with_items(self, items):
        c = CompletionState(
            visible=True, items=list(items), texts=list(items), selected=0,
            title="补全", types=["session"] * len(items),
            descriptions=[""] * len(items), split_desc=False,
        )
        props = dict(
            text="/load tui", cursor_pos=10, prompt="> ", completion=c,
            status_active=False, cpu=0, mem=0,
        )
        f = Fiber("host", "input-area", props)
        f.layout_box = _Box(0, 0, 80, 1)
        return _build_lines(f)

    def test_popup_line_no_newline_regression(self):
        """候选项含 ``\\n`` → 渲染行不含换行（归一化为空格）。"""
        lines = self._build_with_items([
            "abc12345 - tui:\n1.分析bug\n2.完善",
            "def67890 - 正常标题",
        ])
        popup = lines[:4]  # 标题 + 2 候选 + 提示
        for l in popup:
            assert "\n" not in l.plain, (
                f"补全弹窗行不应含换行符（会拆行破坏渲染），实际 {l.plain!r}"
            )
        # 归一化后：原 \n 变为空格（内容可能被 cell_w 截断，仅验证换行消失）
        assert "tui: 1.分析bug" in popup[1].plain, popup[1].plain

    def test_styled_completion_newline_defensive(self):
        """_styled_completion 防御：任意候选项含换行均归一化（含命令/路径类型）。"""
        from src.tui.app.input_area import _styled_completion
        for item_type in ("session", "file", "dir", "command"):
            line = _styled_completion("a\nb", item_type, "", 20)
            assert "\n" not in line.plain, (
                f"_styled_completion 类型 {item_type} 输出不应含换行: {line.plain!r}"
            )
