"""AnsiRenderEngine — token → AnsiLine 行（替代 Rich RenderEngine）。

复用解析层（RecursiveDescentParser / TokenPipeline / CodeBlockBatcher），
将 Token 渲染为 AnsiLine 序列。支持流式状态（代码/数学/mermaid/引用/
admonition/details/fenced_div 的 OPEN/LINE/CLOSE 缓冲）。
"""

from __future__ import annotations

import logging

from src.renderer.types import TokenType, Token
from . import blocks
from . import table as _table
from . import code as _code
from . import math as _math
from . import mermaid as _mermaid
from .helpers import AnsiLine

_logger = logging.getLogger(__name__)


class AnsiRenderEngine:
    """Rich-free 渲染引擎：Token → list[AnsiLine]。

    Args:
        code_theme: pygments 代码高亮主题名。
        width: 终端宽度（表格宽度自适应；可由 set_width 更新）。
    """

    def __init__(self, code_theme: str = "monokai", width: int = 80):
        self._code_theme = code_theme
        self._width = width
        self._reset_state()

    def set_width(self, width: int) -> None:
        """更新终端宽度（表格渲染用）。"""
        self._width = width

    def _reset_state(self) -> None:
        """重置流式缓冲状态。"""
        self._code_state: list | None = None
        self._math_state: list | None = None
        self._mermaid_state: list | None = None
        self._bq_lines: list[str] | None = None
        self._admonition: tuple[str, int] | None = None
        self._details: str | None = None
        self._fenced_div: str | None = None

    def reset(self) -> None:
        """重置所有流式状态（close() 后调用）。"""
        self._reset_state()

    # ── 主入口 ──────────────────────────────────────

    def render(self, token: Token) -> list[AnsiLine]:
        """渲染单个 token 为 AnsiLine 列表（含流式缓冲副作用）。"""
        t = token.type
        try:
            if t == TokenType.PARAGRAPH:
                return blocks.render_paragraph(token)
            if t == TokenType.HEADING:
                return blocks.render_heading(token)
            if t == TokenType.HR:
                return blocks.render_hr(token)
            if t == TokenType.LIST_ITEM:
                return blocks.render_list_item(token)
            if t == TokenType.DEFINITION_ITEM:
                return blocks.render_definition_item(token)
            if t == TokenType.EMPTY_LINE:
                return blocks.render_empty_line(token)
            if t == TokenType.TABLE:
                return _table.render_table(token, self._width)

            # ── 流式块 ──
            if t == TokenType.CODE_BLOCK:
                return self._render_code_block(token)
            if t == TokenType.CODE_FENCE_OPEN:
                self._code_state = [token.meta.get("lang", ""), token.meta.get("attrs", ""), token.meta.get("title", ""), []]
                return []
            if t == TokenType.CODE_LINE:
                if self._code_state is not None:
                    self._code_state[3].append(token.content)
                return []
            if t == TokenType.CODE_FENCE_CLOSE:
                return self._flush_code()

            if t == TokenType.MATH_BLOCK_OPEN:
                self._math_state = []
                return []
            if t == TokenType.MATH_LINE:
                if self._math_state is not None:
                    self._math_state.append(token.content)
                return []
            if t == TokenType.MATH_BLOCK_CLOSE:
                src = token.meta.get("source") or "\n".join(self._math_state or [])
                self._math_state = None
                return _math.render_math_block(src)

            if t == TokenType.MERMAID_BLOCK_OPEN:
                self._mermaid_state = []
                return []
            if t == TokenType.MERMAID_LINE:
                if self._mermaid_state is not None:
                    self._mermaid_state.append(token.content)
                return []
            if t == TokenType.MERMAID_BLOCK_CLOSE:
                src = token.meta.get("source") or "\n".join(self._mermaid_state or [])
                self._mermaid_state = None
                return _mermaid.render_mermaid_block(src)

            if t == TokenType.BLOCKQUOTE_OPEN:
                self._bq_lines = []
                return []
            if t == TokenType.BLOCKQUOTE_LINE:
                if self._bq_lines is not None:
                    self._bq_lines.append(token.content)
                return []
            if t == TokenType.BLOCKQUOTE_CLOSE:
                lines = self._bq_lines or []
                self._bq_lines = None
                return blocks.render_blockquote(_StrToken("\n".join(lines)), depth=0)

            if t == TokenType.ADMONITION_OPEN:
                # OPEN content 即正文首行，meta 含 type/depth
                self._admonition = (token.meta.get("type", "NOTE"), [token.content])
                return []
            if t == TokenType.ADMONITION_LINE:
                if self._admonition is not None:
                    self._admonition[1].append(token.content)
                return []
            if t == TokenType.ADMONITION_CLOSE:
                if self._admonition is None:
                    return []
                atype, lines = self._admonition
                self._admonition = None
                return blocks.render_admonition(_StrToken("\n".join(lines), {"type": atype, "depth": 0}))

            if t == TokenType.DETAILS_OPEN:
                self._details = token.meta.get("summary", "")
                return []
            if t == TokenType.DETAILS_LINE:
                return []
            if t == TokenType.DETAILS_CLOSE:
                tok = _StrToken(self._details or "")
                self._details = None
                return blocks.render_details(tok)

            if t == TokenType.FENCED_DIV_OPEN:
                self._fenced_div = token.meta.get("type", "NOTE")
                return []
            if t == TokenType.FENCED_DIV_LINE:
                return []
            if t == TokenType.FENCED_DIV_CLOSE:
                tok = _StrToken(self._fenced_div or "NOTE")
                self._fenced_div = None
                return blocks.render_fenced_div(tok)

            # HTML 块：纯文本透传
            if t in (TokenType.HTML_BLOCK_OPEN, TokenType.HTML_BLOCK_LINE, TokenType.HTML_BLOCK_CLOSE):
                if t == TokenType.HTML_BLOCK_LINE and token.content:
                    return [AnsiLine.of(token.content)]
                return []

            _logger.debug("未处理 token 类型: %s", t)
            return []
        except Exception:
            _logger.warning("渲染 token %s 异常", t, exc_info=True)
            return []

    # ── 代码块 ──────────────────────────────────────

    def _render_code_block(self, token: Token) -> list[AnsiLine]:
        source = token.content
        lang = token.meta.get("lang", "")
        title = token.meta.get("title", "")
        hl = token.meta.get("highlight_lines") or []
        return _code.render_code_block(source, lang, self._code_theme, hl, title)

    def _flush_code(self) -> list[AnsiLine]:
        if self._code_state is None:
            return []
        lang, attrs, title, lines = self._code_state
        self._code_state = None
        source = "\n".join(lines)
        if not source and not lang and not title:
            return []
        return _code.render_code_block(source, lang, self._code_theme, [], title)


# ── 轻量 Token 桩（组装 content/meta） ────────────────────


class _StrToken:
    """content 桩（供 render_blockquote）。"""

    __slots__ = ("content", "meta")

    def __init__(self, content, meta=None):
        self.content = content
        self.meta = meta or {}


__all__ = ["AnsiRenderEngine"]
