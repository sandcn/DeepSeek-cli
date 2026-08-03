"""补全弹窗闪烁修复测试（commit 顶部对齐 + 弹窗高度锁定）。

覆盖用户需求「补全弹出时，改变内容时，tui 会闪」的修复：
  - **顶部对齐局部重写**（InkRenderer）：文档仍高于屏幕时，弹窗/尾部区域
    高度变化只重写变化行 + 清残留，弹窗上方（历史消息）永不重写——
    消除打字时补全弹窗 items 数量变化引发的全可见区重写闪烁。
  - **弹窗高度锁定**（_completion_height）：弹窗打开期间高度只增不减——
    items 数量减少时弹窗高度保持（底部补白），doc 高度不变 → 等高 diff
    只重写弹窗行（不闪）。
"""

from __future__ import annotations

import io

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer
from src.tui.app.input_area import _completion_height
from src.tui.app.model import CompletionState


def _frame(*plain_lines: str) -> Frame:
    return Frame(Line.of(l) for l in plain_lines)


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
    """弹窗高度锁定（_completion_height 只增不减，弹窗打开期间）。"""

    def _mk(self, items, visible=True):
        c = CompletionState()
        c.visible = visible
        c.items = list(items)
        c.texts = list(items)
        c.types = ["file"] * len(items)
        c.descriptions = [""] * len(items)
        c.split_desc = False
        return c

    def test_height_never_decreases_while_open(self):
        c = self._mk([f"f{i}" for i in range(5)])
        h5 = _completion_height(c, 80)
        assert h5 == 7  # 标题 + 5 项 + 提示
        # items 减少：高度保持（锁定）
        c.items = [f"f{i}" for i in range(2)]
        c.texts = c.items
        h2 = _completion_height(c, 80)
        assert h2 == 7, f"items 减少高度应保持（锁定），实际 {h2}"
        # items 增加超过锁定：高度跟随（增高）
        c.items = [f"f{i}" for i in range(9)]
        c.texts = c.items
        h9 = _completion_height(c, 80)
        assert h9 == 11, f"items 增加高度应跟随，实际 {h9}"
        # 再次减少：仍不降
        c.items = [f"f{i}" for i in range(3)]
        c.texts = c.items
        h3 = _completion_height(c, 80)
        assert h3 == 11, f"再次减少高度应保持，实际 {h3}"

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
