"""StreamingMarkdown 组件单元测试。

覆盖 StreamingMarkdown 的文本累积、增量渲染、光标闪烁、
done 状态、reset、VNode 产出等核心行为。

测试策略：构造 StreamingMarkdown 实例，通过 write() 写入文本块，
调用 flush_partial() 模拟逐帧渲染，用 Mock 替换 render_markdown
以避免实际 Markdown 解析开销。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.chat_ui.components.streaming_markdown import StreamingMarkdown
from src.chat_ui.vdom.vnode import VNode


# ═══════════════════════════════════════════════════════════
# TestStreamingMarkdown
# ═══════════════════════════════════════════════════════════

class TestStreamingMarkdown:
    """StreamingMarkdown 组件测试。"""

    # ── 初始化 ──

    def test_init_defaults(self):
        """默认初始化：光标启用，空文本。"""
        sm = StreamingMarkdown()
        assert sm._pending_text == ""
        assert sm._last_rendered_len == 0
        assert sm._cached_rendered == ""
        assert sm._done is False
        assert sm._show_cursor is True
        assert sm._cursor_char == "▊"

    def test_init_custom_cursor(self):
        """自定义光标字符。"""
        sm = StreamingMarkdown(cursor_char="|")
        assert sm._cursor_char == "|"

    def test_init_no_cursor(self):
        """show_cursor=False 禁用光标。"""
        sm = StreamingMarkdown(show_cursor=False)
        assert sm._show_cursor is False

    # ── write() ──

    def test_write_accumulates_text(self):
        """write() 累积文本。"""
        sm = StreamingMarkdown()
        sm.write("Hello")
        sm.write(" World")
        assert sm._pending_text == "Hello World"

    def test_write_after_done_ignored(self):
        """mark_done() 后 write() 被忽略。"""
        sm = StreamingMarkdown()
        sm.write("first")
        sm.mark_done()
        sm.write("ignored")
        assert sm._pending_text == "first"

    def test_write_accumulates_multiple_chunks(self):
        """多次 write 正确累积。"""
        sm = StreamingMarkdown()
        chunks = ["chunk1 ", "chunk2 ", "chunk3"]
        for c in chunks:
            sm.write(c)
        assert sm._pending_text == "chunk1 chunk2 chunk3"

    # ── flush_partial() — 基本行为 ──

    @patch("src.chat_ui.components.streaming_markdown._get_terminal_width", return_value=80)
    @patch("src.chat_ui.infrastructure.markdown_renderer.render_markdown")
    def test_flush_partial_no_change(self, mock_render, mock_tw):
        """文本未增长时 flush_partial 返回缓存（不调用 render_markdown）。"""
        sm = StreamingMarkdown()
        result = sm.flush_partial(0)
        # 无文本，缓存为空，偶数帧 → 只有光标
        assert result == "▊"
        mock_render.assert_not_called()

    @patch("src.chat_ui.components.streaming_markdown._get_terminal_width", return_value=80)
    @patch("src.chat_ui.infrastructure.markdown_renderer.render_markdown")
    def test_flush_partial_incremental(self, mock_render, mock_tw):
        """文本增长时触发增量渲染。"""
        mock_render.return_value = "[rendered]"
        sm = StreamingMarkdown()
        sm.write("new text")

        result = sm.flush_partial(0)
        mock_render.assert_called_once_with("new text", width=80)
        assert sm._cached_rendered == "[rendered]"
        # 偶数帧 → 光标追加
        assert result == "[rendered]▊"

    @patch("src.chat_ui.components.streaming_markdown._get_terminal_width", return_value=80)
    @patch("src.chat_ui.infrastructure.markdown_renderer.render_markdown")
    def test_flush_partial_delta_only(self, mock_render, mock_tw):
        """增量渲染仅对新增部分调用 render_markdown。"""
        mock_render.return_value = "[b]"
        sm = StreamingMarkdown()
        sm.write("a")
        sm.flush_partial(1)
        mock_render.assert_called_with("a", width=80)
        assert sm._cached_rendered == "[b]"
        assert sm._last_rendered_len == 1

        # 第二次 write 追加新文本
        sm.write("c")
        mock_render.reset_mock()
        mock_render.return_value = "[c]"
        sm.flush_partial(1)
        # 仅对增量 "c" 调用 render_markdown
        mock_render.assert_called_once_with("c", width=80)
        assert sm._cached_rendered == "[b][c]"
        assert sm._last_rendered_len == 2

    # ── flush_partial() — 光标闪烁 ──

    def test_flush_partial_cursor_even_frame(self):
        """偶数帧显示光标。"""
        sm = StreamingMarkdown()
        sm._cached_rendered = "text"
        result = sm._apply_cursor(0)
        assert result == "text▊"
        result = sm._apply_cursor(2)
        assert result == "text▊"
        result = sm._apply_cursor(4)
        assert result == "text▊"

    def test_flush_partial_cursor_odd_frame(self):
        """奇数帧不显示光标。"""
        sm = StreamingMarkdown()
        sm._cached_rendered = "text"
        result = sm._apply_cursor(1)
        assert result == "text"
        result = sm._apply_cursor(3)
        assert result == "text"

    # ── flush_partial() — 光标抑制 ──

    def test_flush_partial_no_cursor_when_done(self):
        """done 后不显示光标。"""
        sm = StreamingMarkdown()
        sm._cached_rendered = "text"
        sm.mark_done()
        result = sm._apply_cursor(0)  # 偶数帧但已 done
        assert result == "text"

    def test_flush_partial_no_cursor_when_disabled(self):
        """show_cursor=False 不显示光标。"""
        sm = StreamingMarkdown(show_cursor=False)
        sm._cached_rendered = "text"
        result = sm._apply_cursor(0)  # 偶数帧但光标禁用
        assert result == "text"

    # ── mark_done() ──

    def test_mark_done(self):
        """mark_done() 设置 _done=True。"""
        sm = StreamingMarkdown()
        assert sm._done is False
        sm.mark_done()
        assert sm._done is True

    # ── reset() ──

    def test_reset(self):
        """reset() 清空所有状态。"""
        sm = StreamingMarkdown()
        sm.write("some text")
        sm.mark_done()
        sm._last_rendered_len = 5
        sm._cached_rendered = "cached"

        sm.reset()
        assert sm._pending_text == ""
        assert sm._last_rendered_len == 0
        assert sm._cached_rendered == ""
        assert sm._done is False

    # ── key 属性 ──

    def test_key_property(self):
        """key 属性返回 "streaming_markdown"。"""
        sm = StreamingMarkdown()
        assert sm.key == "streaming_markdown"

    # ── update() ──

    def test_update_done_prop(self):
        """update({"done": True}) 返回 True 并设置 _done。"""
        sm = StreamingMarkdown()
        changed = sm.update({"done": True})
        assert changed is True
        assert sm._done is True

    def test_update_done_no_change(self):
        """update({"done": False}) 在 _done 已为 False 时不触发变更。"""
        sm = StreamingMarkdown()
        changed = sm.update({"done": False})
        assert changed is False

    def test_update_done_from_true_to_false(self):
        """update({"done": False}) 在 _done 为 True 时触发变更。"""
        sm = StreamingMarkdown()
        sm._done = True
        changed = sm.update({"done": False})
        assert changed is True
        assert sm._done is False

    def test_update_unknown_prop(self):
        """未知 prop 不触发变更。"""
        sm = StreamingMarkdown()
        changed = sm.update({"unknown": 123})
        assert changed is False

    # ── render_vnode() ──

    def test_render_vnode(self):
        """render_vnode() 返回正确 VNode。"""
        sm = StreamingMarkdown()
        sm._cached_rendered = "rendered output"
        sm._done = True
        sm._pending_text = "raw markdown"

        vnode = sm.render_vnode()
        assert isinstance(vnode, VNode)
        assert vnode.type == "streaming_markdown"
        assert vnode.key == "streaming_markdown"
        assert vnode.props["text"] == "rendered output"
        assert vnode.props["done"] is True
        assert vnode.props["pending_len"] == len("raw markdown")

    # ── render() ──

    def test_render_returns_cached(self):
        """render() 返回 _cached_rendered。"""
        sm = StreamingMarkdown()
        sm._cached_rendered = "cached value"
        result = sm.render()
        assert result == "cached value"

    # ── 综合场景：mock render_markdown 替换测试 ──

    @patch("src.chat_ui.components.streaming_markdown._get_terminal_width", return_value=80)
    @patch("src.chat_ui.infrastructure.markdown_renderer.render_markdown")
    def test_flush_partial_with_mocked_render(self, mock_render, mock_tw):
        """使用 mock render_markdown 验证完整增量渲染管道。"""
        mock_render.side_effect = lambda text, width: f"[{text}]"

        sm = StreamingMarkdown()
        sm.write("part1")
        out1 = sm.flush_partial(0)
        mock_render.assert_called_with("part1", width=80)
        assert "[part1]" in out1

        sm.write("part2")
        out2 = sm.flush_partial(1)
        # 第二次调用仅对增量部分
        assert mock_render.call_count == 2
        mock_render.assert_called_with("part2", width=80)
        assert sm._cached_rendered == "[part1][part2]"
        # 奇数帧无光标
        assert "▊" not in out2

    @patch("src.chat_ui.components.streaming_markdown._get_terminal_width", return_value=80)
    @patch("src.chat_ui.infrastructure.markdown_renderer.render_markdown")
    def test_flush_partial_complete_flow(self, mock_render, mock_tw):
        """完整流式渲染流程：写入 → 逐帧渲染 → mark_done → 最终输出。"""
        mock_render.side_effect = lambda text, width: f"<{text}>"

        sm = StreamingMarkdown()

        # 第 1 帧：写入 "Hello"
        sm.write("Hello")
        out1 = sm.flush_partial(0)
        assert "<Hello>" in out1
        assert out1.endswith("▊")  # 偶数帧有光标

        # 第 2 帧：无新文本，返回缓存
        mock_render.reset_mock()
        out2 = sm.flush_partial(1)
        mock_render.assert_not_called()
        assert "<Hello>" in out2
        assert "▊" not in out2  # 奇数帧无光标

        # 第 3 帧：追加 " World"
        sm.write(" World")
        mock_render.reset_mock()
        out3 = sm.flush_partial(2)
        mock_render.assert_called_once_with(" World", width=80)
        assert sm._cached_rendered == "<Hello>< World>"
        assert out3.endswith("▊")

        # 标记完成
        sm.mark_done()
        out4 = sm.flush_partial(3)
        assert "▊" not in out4  # done 后无光标
        assert out4 == "<Hello>< World>"
