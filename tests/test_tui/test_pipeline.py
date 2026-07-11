"""测试 CodeBlockBatcher 异常恢复行为。

覆盖 P2-7 修复：异常恢复时不回滚已发射 Token，仅清理缓冲状态。

核心问题：旧实现在 except 块中恢复 saved_* 快照，导致已发射到 result
（但因异常未返回）的 Token 在下次调用中被重复发射。修复后 except 址仅
清理缓冲状态（buffer/block_meta/feed_count/flushed_in_feed），不恢复快照。
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.renderer.pipeline import CodeBlockBatcher
from src.renderer.types import Token, TokenType, RenderContext


def _token(ttype: TokenType, content: str = "", meta: dict | None = None) -> Token:
    """快捷构造 Token。"""
    return Token(ttype, content, meta or {})


class TestExceptionRecoveryBufferCleared:
    """异常后缓冲区应被清空，而非恢复到 process() 调用前的状态。"""

    def test_cross_feed_close_then_exception(self):
        """跨 feed：feed 1 缓冲未闭合 → feed 2 闭合发射 CODE_BLOCK 后异常 → 缓冲清空。

        旧实现会恢复 saved_buffer=['a','b']，导致下次调用重复发射。
        """
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # Feed 1: 未闭合代码块
        feed1 = [
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "a = 1"),
            _token(TokenType.CODE_LINE, "b = 2"),
        ]
        batcher.process(feed1, ctx)
        assert batcher._buffer == ["a = 1", "b = 2"]
        assert batcher._block_meta is not None

        # Feed 2: CLOSE 闭合发射 CODE_BLOCK（第 1 次 parse_highlight_lines 成功），
        # 随后第二个代码块 CLOSE 触发异常（第 2 次 parse_highlight_lines 抛出）
        feed2 = [
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "c = 3"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ]
        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=[[], RuntimeError("test exception")],
        ):
            with pytest.raises(RuntimeError, match="test exception"):
                batcher.process(feed2, ctx)

        # 核心断言：缓冲区被清空，而非恢复到 feed 1 的 ["a = 1", "b = 2"]
        assert batcher._buffer == [], f"缓冲区应清空，实际: {batcher._buffer}"
        assert batcher._block_meta is None
        assert batcher._buffer_chars == 0
        assert batcher._feed_count == 0

    def test_exception_with_empty_buffer(self):
        """无跨 feed 缓冲时异常 → 缓冲区保持空。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(RuntimeError):
                batcher.process([
                    _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
                    _token(TokenType.CODE_LINE, "x"),
                    _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                ], ctx)

        assert batcher._buffer == []
        assert batcher._block_meta is None
        assert batcher._buffer_chars == 0

    def test_exception_clears_flushed_in_feed(self):
        """异常后 _flushed_in_feed 标志应被重置为 False。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # 先正常处理一个代码块
        batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "a"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ], ctx)

        # 触发异常
        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(RuntimeError):
                batcher.process([
                    _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
                    _token(TokenType.CODE_LINE, "b"),
                    _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                ], ctx)

        assert batcher._flushed_in_feed is False


class TestExceptionNoReemission:
    """异常后下次调用不应重新发射已发射过的缓冲内容。"""

    def test_no_duplicate_after_exception(self):
        """异常后下次调用不应包含旧缓冲数据。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # Feed 1: 缓冲未闭合代码块
        feed1 = [
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "old_data"),
        ]
        batcher.process(feed1, ctx)

        # Feed 2: CLOSE 发射 CODE_BLOCK 后异常
        feed2 = [
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "x"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ]
        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=[[], RuntimeError("boom")],
        ):
            with pytest.raises(RuntimeError):
                batcher.process(feed2, ctx)

        # Feed 3: 正常处理新代码块
        feed3 = [
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "text"}),
            _token(TokenType.CODE_LINE, "new_data"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "text"}),
        ]
        result = batcher.process(feed3, ctx)

        # 不应有 "old_data" 出现在任何 token 中
        for t in result:
            assert "old_data" not in t.content, f"旧数据被重复发射: {t}"
        # 应正常发射新代码块
        code_blocks = [t for t in result if t.type is TokenType.CODE_BLOCK]
        assert len(code_blocks) == 1
        assert "new_data" in code_blocks[0].content

    def test_no_duplicate_code_line_after_exception(self):
        """异常后下次调用不应重复发射 CODE_LINE token。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # Feed 1: 缓冲未闭合代码块
        batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "line_a"),
            _token(TokenType.CODE_LINE, "line_b"),
        ], ctx)

        # Feed 2: 触发异常
        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=[[], RuntimeError("boom")],
        ):
            with pytest.raises(RuntimeError):
                batcher.process([
                    _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                    _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
                    _token(TokenType.CODE_LINE, "x"),
                    _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                ], ctx)

        # Feed 3: 新代码块
        result = batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "text"}),
            _token(TokenType.CODE_LINE, "fresh"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "text"}),
        ], ctx)

        code_lines = [t for t in result if t.type is TokenType.CODE_LINE]
        # 不应有旧 CODE_LINE 泄漏
        assert all("line_a" not in t.content for t in code_lines), \
            f"旧 CODE_LINE 泄漏: {code_lines}"
        assert all("line_b" not in t.content for t in code_lines)


class TestExceptionRecovery:
    """异常后 batcher 能正常处理后续代码块。"""

    def test_recovery_after_exception(self):
        """异常后 batcher 能正常处理后续完整代码块。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # 触发异常
        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(RuntimeError):
                batcher.process([
                    _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
                    _token(TokenType.CODE_LINE, "x = 1"),
                    _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                ], ctx)

        # 后续正常处理
        result = batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "y = 2"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ], ctx)

        code_blocks = [t for t in result if t.type is TokenType.CODE_BLOCK]
        assert len(code_blocks) == 1
        assert "y = 2" in code_blocks[0].content

    def test_recovery_cross_feed_after_exception(self):
        """异常后能正常处理跨 feed 代码块。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # 触发异常
        with patch(
            "src.renderer.pipeline.parse_highlight_lines",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(RuntimeError):
                batcher.process([
                    _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
                    _token(TokenType.CODE_LINE, "x"),
                    _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                ], ctx)

        # 跨 feed 未闭合代码块
        batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "a"),
        ], ctx)

        result = batcher.process([
            _token(TokenType.CODE_LINE, "b"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ], ctx)

        code_blocks = [t for t in result if t.type is TokenType.CODE_BLOCK]
        assert len(code_blocks) == 1
        assert "a" in code_blocks[0].content
        assert "b" in code_blocks[0].content

    def test_multiple_exceptions_then_recovery(self):
        """连续多次异常后 batcher 仍能恢复。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        for i in range(3):
            with patch(
                "src.renderer.pipeline.parse_highlight_lines",
                side_effect=RuntimeError(f"fail_{i}"),
            ):
                with pytest.raises(RuntimeError):
                    batcher.process([
                        _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
                        _token(TokenType.CODE_LINE, f"x{i}"),
                        _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
                    ], ctx)

        # 正常恢复
        result = batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "recovered"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ], ctx)

        code_blocks = [t for t in result if t.type is TokenType.CODE_BLOCK]
        assert len(code_blocks) == 1
        assert "recovered" in code_blocks[0].content


class TestNormalProcessingUnaffected:
    """修复不影响正常处理逻辑。"""

    def test_normal_complete_code_block(self):
        """正常完整代码块仍被正确批处理。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        result = batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "print('hello')"),
            _token(TokenType.CODE_LINE, "x = 42"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ], ctx)

        code_blocks = [t for t in result if t.type is TokenType.CODE_BLOCK]
        assert len(code_blocks) == 1
        assert "print('hello')" in code_blocks[0].content
        assert "x = 42" in code_blocks[0].content

    def test_normal_cross_feed_code_block(self):
        """跨 feed 代码块仍被正确合并。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        batcher.process([
            _token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            _token(TokenType.CODE_LINE, "line1"),
        ], ctx)

        result = batcher.process([
            _token(TokenType.CODE_LINE, "line2"),
            _token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
        ], ctx)

        code_blocks = [t for t in result if t.type is TokenType.CODE_BLOCK]
        assert len(code_blocks) == 1
        assert "line1" in code_blocks[0].content
        assert "line2" in code_blocks[0].content

    def test_normal_non_code_tokens_passthrough(self):
        """非代码 Token 仍直接通过。"""
        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        result = batcher.process([
            _token(TokenType.PARAGRAPH, "hello world"),
            _token(TokenType.EMPTY_LINE),
        ], ctx)

        assert len(result) == 2
        assert result[0].type is TokenType.PARAGRAPH
        assert result[1].type is TokenType.EMPTY_LINE
