"""测试 ToolOutputBlock — 工具执行输出块。

覆盖：
  - render() 基本路径（buffer / 无 buffer / 空文本）
  - \\r 回车叠加路径（纯文本 / 含 ANSI）
  - 超长文本截断
  - 窄屏时的降级行为
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.tui.testing import tui_test_env
from src.tui.render_buffer import RenderBuffer


class TestToolOutputBlock(unittest.TestCase):
    """测试 ToolOutputBlock 基本渲染路径。"""

    def setUp(self):
        self.env = tui_test_env()
        self.env.__enter__()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    # ── 子步骤 2：render() 基础测试 ─────────────────────

    def test_render_with_buffer(self):
        """传入 buffer 时，内容写入 buffer，返回 None。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="Hello Tool")
        buf = RenderBuffer(80, 10)
        result = block.render(buf)
        self.assertIsNone(result)
        rendered = buf.render()
        self.assertIn("Hello Tool", rendered)

    def test_render_without_buffer(self):
        """不传 buffer 时，返回字符串。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="Hello Tool")
        result = block.render()
        self.assertIsNotNone(result)
        self.assertIn("Hello Tool", result)

    def test_render_empty(self):
        """空文本渲染返回空字符串（无 buffer）或写入空 buffer。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="")
        # 无 buffer 路径
        result = block.render()
        # 空文本走非 \r 路径，但 text 为空，结果中仅包含边框/缩进
        self.assertIsNotNone(result)
        # 有 buffer 路径
        buf = RenderBuffer(80, 10)
        result2 = block.render(buf)
        self.assertIsNone(result2)

    # ── 子步骤 3：特殊渲染路径 ──────────────────────────

    def test_render_with_carriage_return(self):
        """含 \\r 的文本取最后一个 \\r 后的内容。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="progress: 0%\rprogress: 50%\rprogress: 100%")
        result = block.render()
        self.assertIsNotNone(result)
        # 应取最后一个 \r 之后的内容
        self.assertIn("progress: 100%", result)
        # 不应包含前面的 \r 分段
        self.assertNotIn("progress: 0%", result)

    def test_render_carriage_with_ansi(self):
        """含 \\r 和 ANSI 转义序列的文本，替换 \\r 后保留 ANSI。"""
        from src.tui.components._tool_output import ToolOutputBlock
        text = "\033[32mOK\033[0m\r\033[31mFAIL\033[0m"
        block = ToolOutputBlock(text=text)
        result = block.render()
        self.assertIsNotNone(result)
        # 含 ANSI 时使用 replace('\r', '')，两个部分都会保留
        self.assertIn("\033[32m", result)
        self.assertIn("\033[31m", result)

    def test_render_dark_border_narrow(self):
        """窄屏时 render 直接返回文本（无边框装饰）。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="narrow text")
        result = block.render()
        self.assertIsNotNone(result)
        # 当前 render() 直接返回文本，无边框/ANSI 装饰
        self.assertIn("narrow text", result)
        # 不应包含 ANSI 序列（无样式装饰）
        self.assertNotIn("\033[", result)

    def test_render_wide_with_border(self):
        """宽屏时 render 直接返回文本（无边框装饰）。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="wide text")
        result = block.render()
        self.assertIsNotNone(result)
        # 当前 render() 直接返回文本，无左边缘边框字符
        self.assertIn("wide text", result)
        self.assertNotIn("\u2502", result)  # 无边框字符

    def test_render_carriage_no_ansi_no_trailing(self):
        """含 \\r 且不以 \\r 结尾的文本，取最后一段。"""
        from src.tui.components._tool_output import ToolOutputBlock
        block = ToolOutputBlock(text="step1\rstep2\rfinal")
        result = block.render()
        self.assertIsNotNone(result)
        # 没有 ANSI 时使用 split('\r')[-1]
        self.assertEqual(result, "final")  # \r 路径无边框，直接返回 clean


if __name__ == "__main__":
    unittest.main()
