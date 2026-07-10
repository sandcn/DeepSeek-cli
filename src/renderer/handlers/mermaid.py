"""MermaidHandler — Mermaid 图表"""
import logging
from rich.text import Text
from rich.style import Style
from ..types import Token, TokenType
from .base import TokenHandler
from .._rendering import render_mermaid_close


_logger = logging.getLogger(__name__)


class MermaidHandler(TokenHandler):
    """处理 Mermaid 图表 Token。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.MERMAID_BLOCK_OPEN,
            TokenType.MERMAID_LINE,
            TokenType.MERMAID_BLOCK_CLOSE,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.MERMAID_BLOCK_OPEN: self._handle_mermaid_block_open,
            TokenType.MERMAID_LINE: self._handle_mermaid_line,
            TokenType.MERMAID_BLOCK_CLOSE: self._handle_mermaid_block_close,
        }

    def _handle_mermaid_block_open(self, token: Token, engine):
        """Mermaid 块打开：准备缓冲。"""
        try:
            engine.mermaid_buffer = []
            lang = token.meta.get("lang", "mermaid")
            fence_text = f"```{lang}"
            t = Text(fence_text, style=Style(dim=True, italic=True))
            if engine.typing_speed > 0:
                engine.write_typing(t, TokenHandler.code_typing_speed(engine))
            else:
                engine.write(t)
        except Exception:
            _logger.debug("Mermaid块打开渲染异常，跳过", exc_info=True)

    def _handle_mermaid_line(self, token: Token, engine):
        """累积 Mermaid 行（流式）。"""
        try:
            engine.mermaid_buffer.append(token.content)
        except Exception:
            _logger.debug("Mermaid行累积异常，跳过", exc_info=True)

    def _handle_mermaid_block_close(self, token: Token, engine):
        """Mermaid 块闭合：渲染整个图表，失败时降级为代码块。"""
        try:
            if engine.mermaid_buffer:
                source = '\n'.join(engine.mermaid_buffer)
            else:
                source = token.content

            try:
                result = engine.mermaid_renderer.render(source)
                engine.write(result)
            except Exception:
                # ── Mermaid 渲染失败降级 ────────────────────────
                # 当 mermaid_renderer.render() 抛出异常（如不支持的图表类型、
                # 解析语法错误等），降级为纯文本代码块显示，避免整个渲染流程崩溃。
                _logger.warning("Mermaid 图表渲染失败，降级为代码块显示 (source=%d chars)",
                                len(source), exc_info=True)
                # 降级提示
                fallback_hint = Text("  ⚠ Mermaid 渲染失败，显示源码", style=Style(dim=True, italic=True, color="bright_black"))
                engine.write(fallback_hint)
                # 以 dim 样式逐行输出源码，模拟代码块效果
                for line in source.split('\n'):
                    engine.write(Text(line, style=Style(dim=True)))

            t = render_mermaid_close()
            if engine.typing_speed > 0:
                engine.write_typing(t, TokenHandler.code_typing_speed(engine))
            else:
                engine.write(t)

            engine.mermaid_buffer = []
        except Exception:
            _logger.debug("Mermaid块关闭渲染异常，跳过", exc_info=True)
