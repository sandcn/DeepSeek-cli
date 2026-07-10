"""测试 MermaidHandler — Mermaid 图表渲染及渲染失败降级。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rich.text import Text
from rich.style import Style
import pytest

from src.api.renderer.handlers.mermaid import MermaidHandler
from src.api.renderer.types import Token, TokenType


def _make_mock_engine(typing_speed: int = 1):
    """构造 mock RenderEngine 实例。"""
    engine = MagicMock()
    engine._typing_speed = typing_speed
    engine.typing_speed = typing_speed
    engine.mermaid_buffer = []
    return engine


def _make_token(token_type: TokenType, content: str = "", meta: dict | None = None):
    """构造 Token 实例。"""
    return Token(type=token_type, content=content, meta=meta or {})


class TestMermaidHandlerRenderSuccess:
    """正常渲染路径测试。"""

    def test_block_close_renders_from_buffer(self):
        """从 mermaid_buffer 拼接源码并成功渲染。"""
        engine = _make_mock_engine()
        engine.mermaid_buffer = ["graph TD;", "A-->B;"]
        engine.mermaid_renderer.render.return_value = Text("rendered_graph")

        token = _make_token(TokenType.MERMAID_BLOCK_CLOSE)
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        # render 应被调用
        engine.mermaid_renderer.render.assert_called_once_with("graph TD;\nA-->B;")
        # 渲染结果应被写入（write 已自带换行，不再额外 write_line）
        engine.write.assert_any_call(Text("rendered_graph"))
        # buffer 应清空
        assert engine.mermaid_buffer == []

    def test_block_close_renders_from_token_content(self):
        """无 buffer 时从 token.content 读取源码并成功渲染。"""
        engine = _make_mock_engine()
        engine.mermaid_buffer = []
        engine.mermaid_renderer.render.return_value = Text("rendered_graph")

        token = _make_token(
            TokenType.MERMAID_BLOCK_CLOSE,
            content="graph LR;\nA-->C;",
        )
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        engine.mermaid_renderer.render.assert_called_once_with("graph LR;\nA-->C;")
        engine.write.assert_any_call(Text("rendered_graph"))
        assert engine.mermaid_buffer == []

    def test_block_close_writes_closing_fence_after_render(self):
        """渲染成功后应输出闭合的 ``` fence（typing_speed>0 时走 write_typing）。"""
        engine = _make_mock_engine()
        engine.mermaid_buffer = ["graph TD;", "A-->B;"]
        engine.mermaid_renderer.render.return_value = Text("rendered")

        token = _make_token(TokenType.MERMAID_BLOCK_CLOSE)
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        # typing_speed>0 时，闭合 fence 走 write_typing
        fence_calls = [
            call for call in engine.write_typing.call_args_list
            if isinstance(call[0][0], Text) and "```" in str(call[0][0])
        ]
        assert len(fence_calls) == 1, "应输出闭合 ``` fence"


class TestMermaidHandlerRenderFallback:
    """渲染失败降级为代码块显示测试。"""

    def test_render_exception_falls_back_to_dim_code_block(self):
        """render() 抛出异常时，以 dim 样式逐行输出源码。"""
        engine = _make_mock_engine()
        source = "graph TD;\nA-->B;"
        engine.mermaid_buffer = source.split("\n")
        engine.mermaid_renderer.render.side_effect = RuntimeError("渲染失败")

        token = _make_token(TokenType.MERMAID_BLOCK_CLOSE)
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        # 降级模式下，源码每一行应以 dim 样式写入
        write_calls = engine.write.call_args_list
        dim_texts = []
        for call in write_calls:
            arg = call[0][0]
            if isinstance(arg, Text) and arg.plain in source.split("\n"):
                dim_texts.append(arg)

        # 所有源码行都应被写入（降级显示）
        assert len(dim_texts) == 2, "应输出 graph TD; 和 A-->B; 两行"
        for t in dim_texts:
            assert t.style.dim, "降级输出的行应为 dim 样式"

    def test_render_exception_still_writes_closing_fence(self):
        """render() 失败后，仍应输出闭合的 ``` fence（typing_speed>0 时走 write_typing）。"""
        engine = _make_mock_engine()
        engine.mermaid_buffer = ["graph TD;", "A-->B;"]
        engine.mermaid_renderer.render.side_effect = RuntimeError("渲染失败")

        token = _make_token(TokenType.MERMAID_BLOCK_CLOSE)
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        # typing_speed>0 时，闭合 fence 走 write_typing
        fence_calls = [
            call for call in engine.write_typing.call_args_list
            if isinstance(call[0][0], Text) and "```" in str(call[0][0])
        ]
        assert len(fence_calls) == 1, "降级后仍应输出闭合 ``` fence"
        assert engine.mermaid_buffer == [], "buffer 应清空"

    def test_render_exception_empty_source(self):
        """空源码时 render() 失败，降级仍稳定。"""
        engine = _make_mock_engine()
        engine.mermaid_buffer = []
        engine.mermaid_renderer.render.side_effect = RuntimeError("空渲染失败")

        token = _make_token(TokenType.MERMAID_BLOCK_CLOSE, content="")
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        # 不应崩溃，buffer 应清空
        assert engine.mermaid_buffer == []
        # typing_speed>0 时，闭合 fence 走 write_typing
        fence_calls = [
            call for call in engine.write_typing.call_args_list
            if isinstance(call[0][0], Text) and "```" in str(call[0][0])
        ]
        assert len(fence_calls) == 1

    def test_render_exception_multiline_source_preserved(self):
        """多行源码在降级时完整输出。"""
        engine = _make_mock_engine()
        source_lines = [
            "sequenceDiagram",
            "    Alice->>John: Hello John,",
            "    John-->>Alice: Great!",
        ]
        engine.mermaid_buffer = list(source_lines)
        engine.mermaid_renderer.render.side_effect = ValueError("不支持的类型")

        token = _make_token(TokenType.MERMAID_BLOCK_CLOSE)
        handler = MermaidHandler()
        handler._handle_mermaid_block_close(token, engine)

        # 所有行都应被以 dim 样式输出
        write_calls = [
            call[0][0]
            for call in engine.write.call_args_list
            if isinstance(call[0][0], Text) and "```" not in str(call[0][0])
        ]
        # 应输出 3 行源码 + 可能的 write_line 换行
        actual_lines = [t.plain for t in write_calls if t.plain in source_lines]
        assert actual_lines == source_lines, (
            f"期望全部源码行被输出: {source_lines}, 实际: {actual_lines}"
        )
        for t in write_calls:
            if t.plain and "```" not in t.plain:
                assert t.style.dim, f"降级行 '{t.plain}' 应为 dim 样式"
