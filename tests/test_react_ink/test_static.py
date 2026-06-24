"""Static 组件单元测试。

覆盖 Static 的累加渲染、key-based 追踪、缓存驱逐、clear 重置。
测试策略：构造 Static 实例，多次调用 render() 模拟增量渲染，
验证已渲染项不被覆盖、新项追加到末尾。
"""

from __future__ import annotations

import pytest

from src.chat_ui.react_ink._static import Static
from src.chat_ui._components import TuiComponent


# ── 测试辅助 ────────────────────────────────────────────

class _LineComp(TuiComponent):
    """渲染固定文本行的组件。"""

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


def _render_fn(item: str, index: int) -> _LineComp:
    """简单的渲染函数：item → _LineComp。"""
    return _LineComp(item)


# ═══════════════════════════════════════════════════════════
# TestStatic
# ═══════════════════════════════════════════════════════════

class TestStatic:
    """Static 组件测试。"""

    def test_renders_items(self):
        """初次渲染输出所有 items。"""
        static = Static(items=["a", "b", "c"], children=_render_fn)
        output = static.render()
        lines = output.split("\n")
        assert len(lines) == 3
        assert lines[0] == "a"
        assert lines[1] == "b"
        assert lines[2] == "c"

    def test_accumulates_new_items(self):
        """新 items 追加到已有输出之后。"""
        static = Static(items=["a"], children=_render_fn)
        out1 = static.render()
        assert out1 == "a"

        # 追加新 item
        static.items.append("b")
        out2 = static.render()
        lines = out2.split("\n")
        assert lines == ["a", "b"]

    def test_previous_items_unchanged(self):
        """已渲染 item 内容不变时不重新渲染。"""
        render_counts = {}

        def _counting_render(item: str, index: int) -> _LineComp:
            render_counts[item] = render_counts.get(item, 0) + 1
            return _LineComp(item)

        static = Static(items=["x"], children=_counting_render)
        static.render()
        assert render_counts["x"] == 1

        # items 不变 — 不重新渲染
        static.render()
        assert render_counts["x"] == 1  # 未被再次渲染

    def test_item_change_triggers_rerender(self):
        """同一位置 item 内容变化时重新渲染。"""
        static = Static(items=["old"], children=_render_fn)
        static.render()

        # 修改同一位置的 item
        static.items[0] = "new"
        out = static.render()
        lines = out.split("\n")
        # old 保持 + new 追加（因为 old 的 key 已渲染）
        assert "old" in lines
        assert "new" in lines

    def test_empty_items(self):
        """空 items 返回空字符串。"""
        static = Static(items=[], children=_render_fn)
        output = static.render()
        assert output == ""

    def test_clear_resets_cache(self):
        """clear() 后重新渲染所有 items。"""
        static = Static(items=["a", "b"], children=_render_fn)
        static.render()

        static.clear()
        # 清空后 items 仍存在
        assert len(static._rendered_output) == 0
        assert len(static._rendered_keys) == 0

        # 重新渲染
        out = static.render()
        lines = out.split("\n")
        assert lines == ["a", "b"]

    def test_max_cache_eviction(self):
        """超出 max_cache 时丢弃最旧项。"""
        static = Static(items=[], children=_render_fn, max_cache=3)
        for ch in ["a", "b", "c", "d", "e"]:
            static.items.append(ch)
            static.render()

        # max_cache=3，应仅保留最近 3 项："c", "d", "e"
        output = static.render()
        lines = output.split("\n")
        assert len(lines) == 3
        # 最旧的 a 和 b 被驱逐
        assert "a" not in lines
        assert "b" not in lines
        assert "c" in lines
        assert "d" in lines
        assert "e" in lines

    def test_append_item(self):
        """append_item() 追加但不触发渲染。"""
        static = Static(items=["a"], children=_render_fn)
        out1 = static.render()
        assert out1 == "a"

        static.append_item("b")
        # append_item 不触发渲染，输出不变
        assert static.render() == "a\nb"

    def test_no_render_fn(self):
        """无渲染函数时返回缓存或空字符串。"""
        static = Static(items=["a", "b"], children=None)
        out = static.render()
        # 无渲染函数 => 不渲染，直接返回已有缓存（空）
        assert out == ""
