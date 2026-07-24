"""AnswerBlock 单元测试 — 助手回答块组件测试。

覆盖 render/write/close 全路径。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.tui.state.render_state import _ReasoningState


class MockRenderState:
    """模拟 IRenderState Protocol 的测试辅助类。

    简化 ChatRenderState 的行为，仅暴露 AnswerBlock 所需的接口：
      - reasoning_state: 推理渲染器状态
      - get_content(): 返回内容渲染器 mock
      - close_reasoning(): 关闭推理渲染器，状态转换到 CLOSED
      - close_content(): 关闭内容渲染器
    """

    def __init__(self):
        self.reasoning_state = _ReasoningState.INACTIVE
        self.reasoning = MagicMock()
        self.content = MagicMock()

    def set_output_adapter(self, adapter) -> None:
        pass

    def get_reasoning(self):
        return self.reasoning

    def get_content(self):
        return self.content

    def close_reasoning(self) -> None:
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        pass

    def close_all(self) -> None:
        pass


class TestAnswerBlockRender(unittest.TestCase):
    """AnswerBlock.render() 测试 — buffer/字符串双路径。"""

    def setUp(self):
        """每个测试前复位单例。"""
        from src.tui.testing import tui_test_env
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def _make_block(self, rs=None):
        """创建 AnswerBlock 实例的辅助方法。"""
        from src.tui.components._answer import AnswerBlock
        rs = rs or MockRenderState()
        return AnswerBlock(rs)

    def _write_content(self, block, *texts):
        """向 block 写入内容的辅助方法。"""
        for text in texts:
            block.write(text)

    def test_render_with_buffer(self):
        """传入 RenderBuffer 时，内容写入 buffer 并返回 None。"""
        from src.tui.render_buffer import RenderBuffer
        block = self._make_block()
        self._write_content(block, "Hello, ", "world!")

        buf = RenderBuffer(40, 3)
        result = block.render(buf)

        self.assertIsNone(result, "传入 buffer 时应返回 None")
        output = buf.render()
        self.assertIn("Hello, world!", output)

    def test_render_without_buffer(self):
        """不传 buffer 时，返回累积文本字符串。"""
        block = self._make_block()
        self._write_content(block, "Hello", " ", "AnswerBlock!")

        result = block.render()
        self.assertIsInstance(result, str)
        self.assertEqual(result, "Hello AnswerBlock!")

    def test_render_empty(self):
        """无写入内容时 render() 返回空字符串。"""
        from src.tui.render_buffer import RenderBuffer
        block = self._make_block()

        # 不传 buffer
        result = block.render()
        self.assertEqual(result, "")

        # 传 buffer 时，buffer render 输出应为空（无可见字符）
        buf = RenderBuffer(40, 3)
        result_with_buf = block.render(buf)
        self.assertIsNone(result_with_buf)
        output = buf.render()
        self.assertEqual(output.strip(), "")

    def test_render_multiple_write(self):
        """多次 write() 后 render() 包含所有累积内容。"""
        from src.tui.render_buffer import RenderBuffer
        block = self._make_block()
        texts = ["Line 1\n", "Line 2\n", "Line 3"]
        self._write_content(block, *texts)

        result = block.render()
        self.assertEqual(result, "Line 1\nLine 2\nLine 3")

    def test_render_empty_write_after(self):
        """write('') 后 render() 行为正常。"""
        from src.tui.render_buffer import RenderBuffer
        block = self._make_block()
        block.write("")  # 空写入
        block.write("actual")

        result = block.render()
        self.assertEqual(result, "actual")

    def test_render_buffer_empty_content(self):
        """空内容写入 buffer 时，buffer 内容为空字符串。"""
        from src.tui.render_buffer import RenderBuffer
        block = self._make_block()
        # 创建 block 后不写入任何内容直接 render(buffer)
        buf = RenderBuffer(10, 3)
        result = block.render(buf)
        self.assertIsNone(result)
        # buffer render 输出应为空（无可见字符）
        output = buf.render()
        self.assertEqual(output.strip(), "")


class TestAnswerBlockWrite(unittest.TestCase):
    """AnswerBlock.write() 测试 — 内容累积/状态管理。"""

    def setUp(self):
        """每个测试前复位单例。"""
        from src.tui.testing import tui_test_env
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_write_accumulates(self):
        """多次 write() 后内容正确累积。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.write("First chunk. ")
        block.write("Second chunk.")

        result = block.render()
        self.assertEqual(result, "First chunk. Second chunk.")

    def test_first_write_triggers_close_reasoning(self):
        """首次 write() 时自动关闭推理状态（若正在推理中）。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        rs.reasoning_state = _ReasoningState.ACTIVE  # 模拟推理进行中
        block = AnswerBlock(rs)

        # 首次 write 应触发 close_reasoning
        block.write("Hello")

        # reasoning_state 应转为 CLOSED
        self.assertEqual(rs.reasoning_state, _ReasoningState.CLOSED)

    def test_write_does_not_close_if_already_closed(self):
        """推理已关闭时 write() 不应再次触发 close_reasoning。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        rs.reasoning_state = _ReasoningState.CLOSED  # 已关闭
        rs.close_reasoning = MagicMock()  # 替换为可追踪的 mock
        block = AnswerBlock(rs)

        block.write("Hello")

        # 当 reasoning_state 为 CLOSED 时，条件
        #   not in (CLOSED, INACTIVE) 为 False，
        # 因此 close_reasoning 应 NOT 被调用
        rs.close_reasoning.assert_not_called()

    def test_write_does_not_close_if_inactive(self):
        """推理未开始时 write() 不应触发 close_reasoning。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        rs.reasoning_state = _ReasoningState.INACTIVE  # 未开始
        rs.close_reasoning = MagicMock()
        block = AnswerBlock(rs)

        block.write("Hello")

        # INACTIVE 状态下不应触发 close_reasoning
        rs.close_reasoning.assert_not_called()

    def test_write_lines_estimate(self):
        """write() 返回正数行数估计。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        lines = block.write("Single line")
        self.assertGreater(lines, 0)

        lines_multi = block.write("Line 1\nLine 2\nLine 3")
        self.assertEqual(lines_multi, 3)

    def test_write_returns_one_for_empty_string(self):
        """空字符串 write() 返回至少 1 行估计。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        lines = block.write("")
        self.assertEqual(lines, 1)

    def test_content_write_called(self):
        """write() 将内容传递给 IncrementalRenderer.write()。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.write("test content")

        # content mock 的 write 应被调用
        rs.content.write.assert_called_once_with("test content")

    def test_first_write_fadein(self):
        """首次写入非窄屏时触发 FadeIn（仅验证不抛出异常）。"""
        from src.tui.components._answer import AnswerBlock
        from src.tui.testing import tui_test_env
        # tui_test_env 已由 setUp 复位 AnimatorContext
        rs = MockRenderState()
        block = AnswerBlock(rs)

        # 非窄屏 + 有 AnimatorContext → FadeIn 不应抛出异常
        try:
            block.write("Hello with fade-in")
        except Exception as e:
            self.fail(f"首次 write() 在非窄屏下抛出异常: {e}")

        # 第二次 write 也不应异常（first_write=False 后走常规路径）
        try:
            block.write("Second write")
        except Exception as e:
            self.fail(f"第二次 write() 抛出异常: {e}")

    def test_write_cumulative_after_multiple_writes(self):
        """多次 write 后累积内容完整。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.write("A")
        block.write("B")
        block.write("C")

        self.assertEqual(block._cumulative_content, ["A", "B", "C"])


class TestAnswerBlockClose(unittest.TestCase):
    """AnswerBlock.close() 测试。"""

    def setUp(self):
        """每个测试前复位单例。"""
        from src.tui.testing import tui_test_env
        self._env = tui_test_env()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_close_calls_close_content(self):
        """close() 调用 close_content()。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        rs.close_content = MagicMock()
        block = AnswerBlock(rs)

        block.close()

        rs.close_content.assert_called_once()

    def test_close_after_render(self):
        """close() 后 render() 仍可获取累积内容。"""
        from src.tui.components._answer import AnswerBlock
        from src.tui.render_buffer import RenderBuffer
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.write("Content before close")
        block.close()

        # closed 后 render 仍返回累积内容
        result = block.render()
        self.assertEqual(result, "Content before close")

    def test_close_idempotent(self):
        """close() 多次调用不抛异常。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.close()  # 第一次
        try:
            block.close()  # 第二次
        except Exception as e:
            self.fail(f"第二次 close() 抛出异常: {e}")

    def test_render_after_close_with_buffer(self):
        """close() 后 render(buffer) 仍正常写入。"""
        from src.tui.components._answer import AnswerBlock
        from src.tui.render_buffer import RenderBuffer
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.write("Close then render")
        block.close()

        buf = RenderBuffer(40, 3)
        result = block.render(buf)
        self.assertIsNone(result)
        output = buf.render()
        self.assertIn("Close then render", output)

    def test_render_empty_after_close(self):
        """close() 后无内容时 render() 返回空字符串。"""
        from src.tui.components._answer import AnswerBlock
        rs = MockRenderState()
        block = AnswerBlock(rs)

        block.close()
        result = block.render()
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
