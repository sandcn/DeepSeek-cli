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
from .._utils import parse_highlight_lines, parse_linenos

from .base import TokenHandler

logger = logging.getLogger(__name__)

# 行数千分位格式化的阈值（≥ 1000 行时使用千分位逗号）
_LARGE_LINE_THRESHOLD = 1000


def _line_number_prefix(idx: int, width: int = 2) -> str:
    """代码行号前缀（右对齐，宽度固定 2——逐行渲染时总行数未知）。"""
    return f"{idx:>{width}} "


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
            engine.code_state.linenos = parse_linenos(attrs)

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
        """输出代码行（语法高亮 + 可选行号）。"""
        try:
            line = token.content
            engine.code_state.line_num += 1
            engine.ensure_theme()

            if not line:
                if engine.code_state.linenos:
                    engine.write_line(
                        _line_number_prefix(engine.code_state.line_num)
                    )
                else:
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

            if engine.code_state.linenos:
                prefix = Text(
                    _line_number_prefix(engine.code_state.line_num),
                    style=Style(dim=True, color="bright_black"),
                )
                prefix.append_text(code_text)
                code_text = prefix

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
            engine.code_state.linenos = False
        except Exception:
            logger.debug("代码块关闭渲染异常，跳过", exc_info=True)

    # ── 整块代码（由 CodeBlockBatcher 管道过滤器生成）──

    def _handle_code_block(self, token: Token, engine):
        """渲染整块代码（支持超长块分段刷出的续段语义）。

        ``meta["continuation"]``（非首段）/``meta["closed"]``（逻辑块是否
        闭合）来自 ``CodeBlockBatcher``：续段不重复渲染标题栏与打开围栏，
        未闭合段不渲染关闭围栏——一个逻辑代码块始终呈现为一个围栏块。
        """
        try:
            source = token.content
            lang = token.meta.get("lang", "text")
            title = token.meta.get("title", "")
            attrs = token.meta.get("attrs", "")
            indented = token.meta.get("indented", False)
            continuation = bool(token.meta.get("continuation", False))
            closed = bool(token.meta.get("closed", True))

            if title and not continuation:
                t_title = self._render_code_title_bar(title, lang, engine)
                engine._output.write(t_title)

            # fence_open 视觉标记（```python 或 📄）——续段不重复输出
            if not continuation:
                if indented:
                    t = render_code_fence_open(lang, indented=True)
                else:
                    t = render_code_fence_open(lang, attrs=attrs)
                engine._output.write(t)

            engine.ensure_theme()
            highlight_lines = token.meta.get("highlight_lines", [])
            linenos = bool(token.meta.get("linenos", False))
            if source:
                # ★ 修复（review 方向）：rstrip('\n') 移除尾部换行——修复前
                #   '\n'.join(source.split('\n')) 恒等于 source（宣称的防御性
                #   修复是 no-op），且尾部换行使 len(lines) 多计一行（2 行代码
                #   块显示 // 3 行）。
                lines = source.rstrip('\n').split('\n')
                self._render_code_block_instant(
                    '\n'.join(lines), lang, engine,
                    highlight_lines=highlight_lines,
                    linenos=linenos,
                )
            else:
                lines = []

            # fence 关闭标记（含行数提示）——仅逻辑块闭合时输出
            if closed:
                t = render_code_fence_close(indented=indented)
                t.append_text(_make_line_count_text(len(lines)))
                engine._output.write(t)

            engine.code_state.lang = ""
            engine.code_state.line_num = 0
            engine.code_state.highlight_lines = []
            engine.code_state.linenos = False
        except Exception:
            logger.debug("整块代码渲染异常，跳过", exc_info=True)

    def _render_code_block_instant(self, source: str, lang: str, engine,
                                    highlight_lines: list[int] | None = None,
                                    linenos: bool = False):
        """即时模式：整块 Syntax 一次性渲染（diff 语言逐行处理）。"""
        try:
            # Diff 语言绕过 Syntax 高亮，逐行用 render_diff_line 处理
            if lang == "diff":
                lines = source.split('\n')
                for i, line in enumerate(lines, 1):
                    code_text = render_diff_line(line)
                    if linenos:
                        prefix = Text(
                            _line_number_prefix(i),
                            style=Style(dim=True, color="bright_black"),
                        )
                        prefix.append_text(code_text)
                        code_text = prefix
                    engine.write(code_text)
                    engine.write_line()
                return

            syntax = render_code_block_syntax(
                source, lang, engine.code_theme,
                highlight_lines=highlight_lines,
                linenos=linenos,
            )

            # write(syntax) 经 console.print() 输出，已自动追加换行。
            # 此处不再额外 write_line()，避免代码块与关闭 ``` 之间多出空行。
            engine.write(syntax)
        except Exception:
            logger.debug("即时代码块渲染异常，跳过", exc_info=True)

    def _highlight_line(self, line: str, lexer, engine) -> Text:
        """对单行代码进行语法高亮，返回 Rich Text。"""
        return highlight_line(line, lexer, engine.theme)
