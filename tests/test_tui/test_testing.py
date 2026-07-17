"""测试 src.tui.testing — 测试辅助工具。"""

from __future__ import annotations

from src.tui.testing import tui_test_env, BufferOutputAdapter
from src.tui.framework import Framework
from src.tui.animation.animator import AnimatorContext
from src.tui.core.effects import EffectRegistry
from rich.text import Text


class TestTuiTestEnv:
    """测试 tui_test_env 上下文管理器。"""

    def test_resets_framework_on_entry(self):
        """进入上下文后 Framework 被复位。"""
        old = Framework.get_default()
        with tui_test_env():
            new = Framework.get_default()
            assert old is not new

    def test_resets_animator_on_entry(self):
        """进入上下文后 AnimatorContext 被复位。"""
        old = AnimatorContext.get_default()
        with tui_test_env():
            new = AnimatorContext.get_default()
            assert old is not new

    def test_resets_on_exit(self):
        """退出上下文后单例再次复位。"""
        with tui_test_env():
            inner = Framework.get_default()
        outer = Framework.get_default()
        assert inner is not outer

    def test_effect_registry_cleared(self):
        """进入上下文后 EffectRegistry 被清空。"""
        with tui_test_env():
            assert EffectRegistry.all_names() == []

    def test_nested_safe(self):
        """嵌套使用安全。"""
        with tui_test_env():
            a = Framework.get_default()
            with tui_test_env():
                b = Framework.get_default()
                assert a is not b


class TestBufferOutputAdapter:
    """测试 BufferOutputAdapter。"""

    def test_write_string(self):
        """write() 写入字符串。"""
        adapter = BufferOutputAdapter()
        adapter.write("Hello")
        assert adapter.getvalue() == "Hello"

    def test_write_rich_text(self):
        """write() 写入 rich.text.Text 对象。"""
        adapter = BufferOutputAdapter()
        adapter.write(Text("Rich", style="bold"))
        assert adapter.getvalue() == "Rich"

    def test_write_line(self):
        """write_line() 追加换行。"""
        adapter = BufferOutputAdapter()
        adapter.write_line("Hello")
        assert adapter.getvalue() == "Hello\n"

    def test_write_raw(self):
        """write_raw() 写入原始文本。"""
        adapter = BufferOutputAdapter()
        adapter.write_raw("\033[31mRed\033[0m")
        assert adapter.getvalue() == "\033[31mRed\033[0m"

    def test_clear(self):
        """clear() 清空缓冲区。"""
        adapter = BufferOutputAdapter()
        adapter.write("Hello")
        adapter.clear()
        assert adapter.getvalue() == ""

    def test_flush_no_error(self):
        """flush() 不抛出异常。"""
        adapter = BufferOutputAdapter()
        adapter.flush()

    def test_render_frame(self):
        """render_frame() 写入多行。"""
        adapter = BufferOutputAdapter()
        result = adapter.render_frame(["line1", "line2"], 2)
        assert result == 2
        assert "line1" in adapter.getvalue()
        assert "line2" in adapter.getvalue()

    def test_lines_property(self):
        """lines 属性返回快照。"""
        adapter = BufferOutputAdapter()
        adapter.write("a")
        adapter.write("b")
        assert adapter.lines == ["a", "b"]
        # 修改返回的列表不应影响原缓冲区
        adapter.lines.clear()
        assert adapter.getvalue() == "ab"

    def test_terminal_width(self):
        """terminal_width 默认 120。"""
        adapter = BufferOutputAdapter()
        assert adapter.terminal_width == 120

    def test_len(self):
        """__len__ 返回缓冲区长度。"""
        adapter = BufferOutputAdapter()
        assert len(adapter) == 0
        adapter.write("a")
        assert len(adapter) == 1

    def test_getitem(self):
        """支持索引访问。"""
        adapter = BufferOutputAdapter()
        adapter.write("first")
        adapter.write("second")
        assert adapter[0] == "first"
        assert adapter[1] == "second"
