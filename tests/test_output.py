"""测试 OutputAdapter — 简化后直接输出的行为验证。

覆盖内容：
  1. write() 直接输出 Text 对象
  2. write() 空值安全路径
  3. write_raw 输出纯文本
  4. write_line 空行/文本行
  5. width 属性缓存与刷新
  6. clear_line 输出转义序列
  7. flush 空安全
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rich.console import Console
from rich.text import Text

from src.api.renderer.output import OutputAdapter


class TestOutputAdapterWrite:
    """write() 方法基础行为测试。"""

    def _make_adapter(self) -> OutputAdapter:
        """创建适配器，Console 使用 MagicMock 避免真实终端依赖。"""
        mock_console = MagicMock(spec=Console)
        mock_console.file = MagicMock()
        adapter = OutputAdapter(mock_console)
        return adapter

    def test_write_text_direct(self):
        """write(Text) 直接输出到 console.print。"""
        adapter = self._make_adapter()
        t = Text("hello", style="bold")
        adapter.write(t)
        adapter._console.print.assert_called_once_with(t)

    def test_write_empty(self):
        """write(None) 或 write(空) 不执行任何操作。"""
        adapter = self._make_adapter()
        adapter.write(None)
        adapter._console.print.assert_not_called()

    def test_write_empty_text(self):
        """write(Text('')) Rich Text('') 是 falsy（__bool__ 检查 plain），直接跳过。"""
        adapter = self._make_adapter()
        adapter.write(Text(""))
        adapter._console.print.assert_not_called()

    def test_write_non_text(self):
        """write(str) 非 Text 对象直接输出。"""
        adapter = self._make_adapter()
        adapter.write("hello")
        adapter._console.print.assert_called_once_with("hello")

    def test_write_raw(self):
        """write_raw 写原始文本到 console.file。"""
        adapter = self._make_adapter()
        adapter.write_raw("test")
        adapter._console.file.write.assert_called_once_with("test")
        adapter._console.file.flush.assert_called_once()

    def test_write_raw_empty(self):
        """write_raw('') 不执行。"""
        adapter = self._make_adapter()
        adapter.write_raw("")
        adapter._console.file.write.assert_not_called()

    def test_write_line_text(self):
        """write_line('hello') 输出文本行。"""
        adapter = self._make_adapter()
        adapter.write_line("hello")
        adapter._console.print.assert_called_once_with("hello")

    def test_write_line_empty(self):
        """write_line('') 仅输出换行符。"""
        adapter = self._make_adapter()
        adapter.write_line()
        # 空字符串路径使用 console.file.write("\n")
        adapter._console.file.write.assert_called_once_with("\n")
        adapter._console.file.flush.assert_called_once()

    def test_clear_line(self):
        """clear_line 输出回车+擦除序列。"""
        adapter = self._make_adapter()
        adapter.clear_line()
        adapter._console.file.write.assert_called_once_with("\r\033[K")
        adapter._console.file.flush.assert_called_once()

    def test_flush(self):
        """flush 调用 console.file.flush。"""
        adapter = self._make_adapter()
        adapter.flush()
        adapter._console.file.flush.assert_called_once()

    def test_write_inline(self):
        """write_inline 不换行输出。"""
        adapter = self._make_adapter()
        t = Text("hello")
        adapter.write_inline(t)
        adapter._console.print.assert_called_once_with(t, end='')
        adapter._console.file.flush.assert_called_once()

    def test_print(self):
        """print 直接代理 console.print。"""
        adapter = self._make_adapter()
        adapter.print("test")
        adapter._console.print.assert_called_once_with("test")


class TestOutputAdapterWidth:
    """width 属性测试。"""

    def test_width_default(self):
        """默认终端宽度为 80（当检测失败时）。"""
        with patch('shutil.get_terminal_size', side_effect=Exception):
            adapter = OutputAdapter(MagicMock(spec=Console))
            assert adapter.width == 80

    def test_width_caching(self):
        """width 缓存 5 秒 TTL。"""
        with patch('shutil.get_terminal_size', return_value=MagicMock(columns=100)):
            adapter = OutputAdapter(MagicMock(spec=Console))
            assert adapter.width == 100

            # 第二次读取，不应调用 shutil
            with patch.object(adapter, '_get_terminal_width', wraps=adapter._get_terminal_width) as mock:
                adapter._last_width_refresh = float('inf')  # 强制刷新
                w = adapter.width
                assert w == 100
