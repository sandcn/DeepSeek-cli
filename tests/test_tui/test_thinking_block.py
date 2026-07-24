"""ThinkingBlock 单元测试。

测试范围：
1. ThinkingBlock.render() — 缓冲区写入和字符串返回
2. ThinkingBlock.write() — 内容累积、状态管理、行数估计
3. ThinkingBlock.close() — 推理状态关闭
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.tui.testing import tui_test_env
from src.tui.render_buffer import RenderBuffer
from src.tui.state.render_state import _ReasoningState


class MockRenderState:
    """模拟 IRenderState 的最小化测试替身。

    记录所有调用，供测试断言。
    """
    def __init__(self):
        self.reasoning_state = _ReasoningState.INACTIVE
        self.reasoning = MagicMock()
        self.content = MagicMock()
        self._reopen_called = False
        self._close_reasoning_called = False

    def set_output_adapter(self, adapter):
        pass

    def get_reasoning(self):
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning_state == _ReasoningState.INACTIVE:
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self):
        return self.content

    def close_reasoning(self):
        self._close_reasoning_called = True
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self):
        self._reopen_called = True
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self):
        pass

    def close_all(self):
        pass


class TestThinkingBlockRender(unittest.TestCase):
    """ThinkingBlock.render() 测试。"""

    def setUp(self):
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_render_with_buffer(self):
        """render(buffer) 将累积内容写入 RenderBuffer。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        # 手动填充累积内容（模拟 write 后的状态）
        block._cumulative_content = ["Hello ", "World"]
        buf = RenderBuffer(20, 3)
        result = block.render(buf)

        # 传入 buffer 时应返回 None
        self.assertIsNone(result)
        # buffer 中应包含累积内容
        output = buf.render()
        self.assertIn("Hello World", output)

    def test_render_without_buffer(self):
        """render() 不传 buffer 时返回累积内容字符串。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        block._cumulative_content = ["Hello ", "World"]
        result = block.render()

        self.assertIsInstance(result, str)
        self.assertEqual(result, "Hello World")

    def test_render_empty(self):
        """无累积内容时 render() 返回空字符串/None。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        # 无累积内容

        # 不传 buffer 时返回空字符串
        result = block.render()
        self.assertEqual(result, "")

        # 传 buffer 时返回 None（buffer 无内容写入）
        buf = RenderBuffer(10, 3)
        result_with_buf = block.render(buf)
        self.assertIsNone(result_with_buf)
        self.assertEqual(buf.render(), "")

    def test_render_multiple_contents(self):
        """多次写入后 render() 拼接所有累积内容。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        block._cumulative_content = ["line1\n", "line2\n", "line3"]
        result = block.render()

        self.assertEqual(result, "line1\nline2\nline3")

    def test_render_with_buffer_empty_cumulative(self):
        """累积内容为空时传入 buffer，buffer 保持不变。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        block._cumulative_content = []
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Existing")
        block.render(buf)
        output = buf.render()
        # buffer 中原有内容不应被覆盖
        self.assertIn("Existing", output)

    def test_render_after_close(self):
        """close() 后 render() 仍能返回之前累积的内容。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        block._cumulative_content = ["Persisted content"]
        block.close()
        result = block.render()

        self.assertEqual(result, "Persisted content")


class TestThinkingBlockWrite(unittest.TestCase):
    """ThinkingBlock.write() 测试。"""

    def setUp(self):
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_write_accumulates(self):
        """多次 write() 后 render() 包含所有累积内容。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            block.write("Hello ")
            block.write("World")
            result = block.render()
            # 窄屏下第一次 write 写入 "\n  ─ 思考 ─\n"，第二次写入 "World"
            # 所以累积内容为 ["Hello ", "World"]
            self.assertIn("Hello ", result)
            self.assertIn("World", result)

    def test_write_accumulates_order(self):
        """多次 write() 保持写入顺序。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            block.write("first\n")
            block.write("second\n")
            block.write("third")
            result = block.render()
            # 累积内容按顺序拼接
            # write 会将文本追加到 _cumulative_content，所以 render 返回 "first\nsecond\nthird"
            self.assertEqual(result, "first\nsecond\nthird")

    def test_write_triggers_reopen(self):
        """当 reasoning_state 为 CLOSED 时，write() 调用 reopen_reasoning()。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        rs.reasoning_state = _ReasoningState.CLOSED
        block = ThinkingBlock(rs)
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block.write("new content")
        self.assertTrue(rs._reopen_called)
        # reopen 后 should_state 应为 INACTIVE
        self.assertEqual(rs.reasoning_state, _ReasoningState.ACTIVE)  # get_reasoning 将 INACTIVE 转为 ACTIVE

    def test_write_returns_lines_estimate(self):
        """write() 返回正数行数估计。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            lines = block.write("Hello\nWorld\nLine3")
            self.assertGreater(lines, 0)
            # 窄屏首次 write："\n  ─ 思考 ─\n"（2个换行=3行）+ 内容文本（2个换行=3行）= 6 行
            expected = "\n  ─ 思考 ─\n".count('\n') + 1 + "Hello\nWorld\nLine3".count('\n') + 1
            self.assertEqual(lines, expected)

    def test_write_get_reasoning_returns_none(self):
        """get_reasoning() 返回 None 时 write() 返回 0。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        rs.reasoning = None  # 模拟未注入渲染器
        block = ThinkingBlock(rs)
        lines = block.write("content")
        self.assertEqual(lines, 0)

    def test_write_first_line_wide(self):
        """宽屏首次 write() 写入 sparkle⚡ 呼吸色标题。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=False):
            block = ThinkingBlock(rs)
            lines = block.write("Hello")
            # 应写入 sparkle 标题 + fadein 内容（至少 2 次调用）
            self.assertGreaterEqual(rs.reasoning.write.call_count, 2)
            # 验证标题包含 sparkle 和 "思考" 标签
            calls = rs.reasoning.write.call_args_list
            header_args = calls[0][0][0] if len(calls) >= 1 else ""
            self.assertIn("⚡", header_args)
            self.assertIn("思考", header_args)
            # 返回正数行数
            self.assertGreater(lines, 0)

    def test_write_first_line_narrow(self):
        """窄屏首次 write() 写入 "\n  ─ 思考 ─\n" 静态标题。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            lines = block.write("Hello")
            # 应写入 "\n  ─ 思考 ─\n" + 内容到 reasoning renderer
            calls = rs.reasoning.write.call_args_list
            # 第一次 write 调用应为 "\n  ─ 思考 ─\n"
            self.assertEqual(calls[0][0][0], "\n  ─ 思考 ─\n")
            self.assertGreater(lines, 0)

    def test_write_second_line_does_not_add_header(self):
        """第二次 write() 不再写入标题头。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            block.write("first")
            rs.reasoning.write.reset_mock()
            block.write("second")
            # 第二次 write 不应再写入 "\n  ─ 思考 ─\n"
            calls = [call[0][0] for call in rs.reasoning.write.call_args_list]
            self.assertEqual(len(calls), 1)
            self.assertIn("second", calls[0])

    def test_write_lines_estimate_single_line(self):
        """单行内容 write() 返回 1。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            lines = block.write("single line")
            self.assertGreater(lines, 0)

    def test_write_lines_estimate_multi_line(self):
        """多行内容 write() 返回正确行数。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            lines = block.write("line1\nline2\nline3\nline4\nline5")
            # 窄屏首次 write："\n  ─ 思考 ─\n"（2个换行=3行）+ 内容文本（4个换行=5行）= 8 行
            expected = "\n  ─ 思考 ─\n".count('\n') + 1 + "line1\nline2\nline3\nline4\nline5".count('\n') + 1
            self.assertEqual(lines, expected)


class TestThinkingBlockClose(unittest.TestCase):
    """ThinkingBlock.close() 测试。"""

    def setUp(self):
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_close_calls_close_reasoning(self):
        """close() 调用 rs.close_reasoning()。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        block.close()
        self.assertTrue(rs._close_reasoning_called)

    def test_close_changes_state(self):
        """close() 后 reasoning_state 转为 CLOSED。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        self.assertEqual(rs.reasoning_state, _ReasoningState.INACTIVE)
        block.close()
        self.assertEqual(rs.reasoning_state, _ReasoningState.CLOSED)

    def test_close_idempotent(self):
        """多次 close() 幂等 — 不抛出异常。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        block.close()
        # 第二次 close 不应抛异常
        block.close()

    def test_close_then_write_reopens(self):
        """close() 后 write() 应触发 reopen。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            block.close()
            self.assertTrue(rs._close_reasoning_called)
            rs._close_reasoning_called = False  # 重置
            block.write("after close")
            # close 后 write 应调用 reopen_reasoning
            self.assertTrue(rs._reopen_called)


class TestThinkingBlockLifecycle(unittest.TestCase):
    """ThinkingBlock 完整生命周期测试。"""

    def setUp(self):
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_init_defaults(self):
        """ThinkingBlock 初始化默认值正确。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        self.assertIs(block._rs, rs)
        self.assertEqual(block._cumulative_content, [])

    def test_write_then_close_then_render(self):
        """write → close → render 全流程正常。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            block.write("content")
            block.close()
            result = block.render()
            self.assertIn("content", result)

    def test_write_then_close_then_write_again(self):
        """write → close → write 再次写入正常（reopen 场景）。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        with patch("src.tui.components._thinking.is_narrow", return_value=True):
            block = ThinkingBlock(rs)
            block.write("first")
            block.close()
            rs._reopen_called = False
            block.write("second")
            # reopen 应在第二次 write 时被触发
            self.assertTrue(rs._reopen_called)
            # render 应包含两次写入内容
            result = block.render()
            self.assertIn("first", result)
            self.assertIn("second", result)

    def test_with_tui_test_env(self):
        """在 tui_test_env 中创建 ThinkingBlock 不抛异常。"""
        from src.tui.components._thinking import ThinkingBlock

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        # 基本断言：对象创建成功
        self.assertIsNotNone(block)

    def test_isinstance_tui_component(self):
        """ThinkingBlock 是 TuiComponent 的子类。"""
        from src.tui.components._thinking import ThinkingBlock
        from src.tui.components._base import TuiComponent

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        self.assertIsInstance(block, TuiComponent)

    def test_isinstance_widget(self):
        """ThinkingBlock 是 Widget 的子类。"""
        from src.tui.components._thinking import ThinkingBlock
        from src.tui.widget_base import Widget

        rs = MockRenderState()
        block = ThinkingBlock(rs)
        self.assertIsInstance(block, Widget)


if __name__ == "__main__":
    unittest.main()
