"""CodeHandler — 代码块相关（fence/open/line/close）"""

from __future__ import annotations

import logging
from rich.text import Text
from rich.style import Style
from ..types import Token, TokenType
from .._rendering import (
    render_code_title_bar,
    render_code_fence_open,
    render_code_fence_close,
    render_code_block_syntax,
    highlight_line,
    render_diff_line,
)
from .._utils import parse_highlight_lines

from .base import TokenHandler

logger = logging.getLogger(__name__)

# 行数千分位格式化的阈值（≥ 1000 行时使用千分位逗号）
_LARGE_LINE_THRESHOLD = 1000


def _make_line_count_text(line_count: int) -> Text:
    """生成代码块行数提示文本。"""
    if line_count >= _LARGE_LINE_THRESHOLD:
        line_display = f"{line_count:,}"
        return Text(
            f"  // {line_display} 行",
            style=Style(dim=True, color="bright_black"),
        )
    return Text(
        f"  // {line_count} 行",
        style=Style(dim=True, color="bright_black"),
    )


class CodeHandler(TokenHandler):
    """处理代码块 Token：fence 打开/代码行/fence 关闭/整块代码。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.CODE_FENCE_OPEN,
            TokenType.CODE_LINE,
            TokenType.CODE_FENCE_CLOSE,
            TokenType.CODE_BLOCK,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.CODE_FENCE_OPEN: self._handle_code_fence_open,
            TokenType.CODE_LINE: self._handle_code_line,
            TokenType.CODE_FENCE_CLOSE: self._handle_code_fence_close,
            TokenType.CODE_BLOCK: self._handle_code_block,
        }

    # ── 标题栏渲染 ──────────────────────────────────────

    def _render_code_title_bar(self, title: str, lang: str, engine) -> Text:
        """渲染代码块标题栏（┌─ 文件名 ────────────────┐）。"""
        return render_code_title_bar(title, lang, engine.output_width)

    # ── 代码块打开 ──────────────────────────────────────

    def _handle_code_fence_open(self, token: Token, engine):
        """代码块 fence 打开。"""
        try:
            engine.code_state.lang = token.meta.get("lang", "text")
            engine.code_state.line_num = 0
            engine.code_state.indented = token.meta.get("indented", False)

            attrs = token.meta.get("attrs", "")
            title = token.meta.get("title", "")
            engine.code_state.highlight_lines = parse_highlight_lines(attrs)

            if title:
                t_title = self._render_code_title_bar(title, engine.code_state.lang, engine)
                engine._output.write(t_title)

            if engine.code_state.indented:
                t = render_code_fence_open(engine.code_state.lang, indented=True)
            else:
                t = render_code_fence_open(engine.code_state.lang, attrs=attrs)

            engine._output.write(t)
        except Exception:
            logger.debug("代码块打开渲染异常，跳过", exc_info=True)


    # ── 代码行 ──────────────────────────────────────────

    def _handle_code_line(self, token: Token, engine):
        """输出代码行（语法高亮）。"""
        try:
            line = token.content
            engine.code_state.line_num += 1
            engine.ensure_theme()

            if not line:
                engine.write_line()
                return

            lang = engine.code_state.lang or "text"

            # Diff 语言特殊处理：行首字符决定颜色
            if lang == "diff" and line:
                code_text = render_diff_line(line)
            else:
                lexer = engine.get_lexer(lang)

                if lexer is None:
                    # 快速路径：词法分析器不可用时直接输出纯文本
                    code_text = Text(line)
                else:
                    code_text = self._highlight_line(line, lexer, engine)

            engine._output.write(code_text)
        except Exception:
            logger.debug("代码行渲染异常，跳过", exc_info=True)

    # ── 代码块关闭 ──────────────────────────────────────

    def _handle_code_fence_close(self, token: Token, engine):
        """代码块闭合：输出视觉标记 + 行数提示 + 清理缓冲。"""
        try:
            t = render_code_fence_close(indented=engine.code_state.indented)

            # 新特性：行数提示 — 在 fence 关闭标记后追加 `// N lines`
            line_count = engine.code_state.line_num
            if line_count >= 0:
                t.append_text(_make_line_count_text(line_count))

            engine._output.write(t)

            engine.code_state.lang = ""
            engine.code_state.indented = False
            engine.code_state.line_num = 0
            engine.code_state.highlight_lines = []
        except Exception:
            logger.debug("代码块关闭渲染异常，跳过", exc_info=True)

    # ── 整块代码（由 CodeBlockBatcher 管道过滤器生成）──

    def _handle_code_block(self, token: Token, engine):
        """渲染整块代码，即时输出。"""
        try:
            source = token.content
            lang = token.meta.get("lang", "text")
            title = token.meta.get("title", "")

            if title:
                t_title = self._render_code_title_bar(title, lang, engine)
                engine._output.write(t_title)

            # fence_open 视觉标记（```python 或 📄）
            attrs = token.meta.get("attrs", "")
            indented = token.meta.get("indented", False)
            if indented:
                t = render_code_fence_open(lang, indented=True)
            else:
                t = render_code_fence_open(lang, attrs=attrs)
            engine._output.write(t)

            lines = source.split('\n')
            engine.ensure_theme()

            highlight_lines = token.meta.get("highlight_lines", [])

            if lines:
                # 即时模式：整块 Syntax 一次性渲染
                # ★ 使用 split 保留原始空行（与 typing 路径一致的 lines 来源），
                # 避免 source 含尾随 \n 导致 Syntax 多渲染一个空行（防御性修复）。
                self._render_code_block_instant(
                    '\n'.join(lines), lang, engine,
                    highlight_lines=highlight_lines,
                )

            # fence 关闭标记（含行数提示）

            indented = token.meta.get("indented", False)
            t = render_code_fence_close(indented=indented)
            line_count = len(lines)
            if line_count >= 0:
                t.append_text(_make_line_count_text(line_count))
            engine._output.write(t)

            engine.code_state.lang = ""
            engine.code_state.line_num = 0
            engine.code_state.highlight_lines = []
        except Exception:
            logger.debug("整块代码渲染异常，跳过", exc_info=True)

    def _render_code_block_instant(self, source: str, lang: str, engine,
                                    highlight_lines: list[int] | None = None):
        """即时模式：整块 Syntax 一次性渲染（diff 语言逐行处理）。"""
        try:
            # Diff 语言绕过 Syntax 高亮，逐行用 render_diff_line 处理
            if lang == "diff":
                lines = source.split('\n')
                for line in lines:
                    code_text = render_diff_line(line)
                    engine.write(code_text)
                    engine.write_line()
                return

            syntax = render_code_block_syntax(
                source, lang, engine.code_theme,
                highlight_lines=highlight_lines,
            )

            # write(syntax) 经 console.print() 输出，已自动追加换行。
            # 此处不再额外 write_line()，避免代码块与关闭 ``` 之间多出空行。
            engine.write(syntax)
        except Exception:
            logger.debug("即时代码块渲染异常，跳过", exc_info=True)

    def _highlight_line(self, line: str, lexer, engine) -> Text:
        """对单行代码进行语法高亮，返回 Rich Text。"""
        return highlight_line(line, lexer, engine.theme)
