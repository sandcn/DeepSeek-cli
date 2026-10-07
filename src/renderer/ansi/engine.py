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
from . import _html_block
from .helpers import AnsiLine, Run
from .inline import use_render_context

_logger = logging.getLogger(__name__)


class AnsiRenderEngine:
    """Rich-free 渲染引擎：Token → list[AnsiLine]。

    Args:
        code_theme: pygments 代码高亮主题名。
        width: 终端宽度（表格宽度自适应；可由 set_width 更新）。
    """

    def __init__(self, code_theme: str = "monokai", width: int = 80, ctx=None):
        self._code_theme = code_theme
        self._width = width
        #: 渲染上下文（脚注编号 / 参考式链接 / 缩写替换；None 时按无上下文渲染）
        self._ctx = ctx
        self._reset_state()

    def set_width(self, width: int) -> None:
        """更新终端宽度（表格渲染用）。"""
        self._width = width

    def _reset_state(self) -> None:
        """重置流式缓冲状态。"""
        self._code_state: list | None = None
        self._math_state: list | None = None
        self._mermaid_state: list | None = None
        self._admonition: tuple | None = None
        # 折叠块 / FencedDiv 流式状态：
        #   _details = (summary, [正文行])；_fenced_div = (type, 头行文本, [正文行])
        # ★ 修复：原为 str（仅 summary/type）——正文行（DETAILS_LINE /
        #   FENCED_DIV_LINE）被直接丢弃，块内内容全部丢失、summary 也不显示。
        self._details: tuple | None = None
        self._fenced_div: tuple | None = None
        # HTML 块渲染状态（``_html_block.HtmlBlockState``；CLOSE 时清理）
        self._html_state = None

    def reset(self) -> None:
        """重置所有流式状态（close() 后调用）。"""
        self._reset_state()

    # ── 主入口 ──────────────────────────────────────

    def render(self, token: Token) -> list[AnsiLine]:
        """渲染单个 token（在渲染上下文中，供脚注/参考链接/缩写解析）。"""
        with use_render_context(self._ctx):
            lines = self._render_impl(token)
        list_indent = _token_list_indent(token)
        if list_indent is not None and lines:
            lines = _apply_list_prefix(lines, list_indent)
        depth = _token_bq_depth(token)
        if depth > 0 and lines:
            lines = _apply_bq_prefix(lines, depth)
        return lines

    def _render_impl(self, token: Token) -> list[AnsiLine]:
        """渲染单个 token 为 AnsiLine 列表（含流式缓冲副作用）。"""
        t = token.type
        try:
            if t == TokenType.PARAGRAPH:
                return blocks.render_paragraph(token)
            if t == TokenType.HEADING:
                return blocks.render_heading(token)
            if t == TokenType.HR:
                return blocks.render_hr(token, self._width)
            if t == TokenType.LIST_ITEM:
                return blocks.render_list_item(token)
            if t == TokenType.DEFINITION_ITEM:
                return blocks.render_definition_item(token)
            if t == TokenType.EMPTY_LINE:
                return blocks.render_empty_line(token)
            if t == TokenType.FRONT_MATTER:
                return blocks.render_front_matter(token)
            if t == TokenType.TABLE_CAPTION:
                return blocks.render_table_caption(token)
            if t == TokenType.LINE_BREAK:
                # 硬换行（`<br>` 独立 Token）→ 空行（与 Rich 路径 InlineHandler 一致）
                return [AnsiLine()]
            if t == TokenType.BLOCKQUOTE:
                # 旧式单 Token 引用块（兼容解析器旧契约）
                depth = max(1, int(token.meta.get("depth", 1)))
                return blocks.render_blockquote(_StrToken(token.content),
                                                depth=depth - 1)
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
                return _math.render_math_block(
                    src, dropped=int(token.meta.get("preview_dropped", 0) or 0))

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
                return _mermaid.render_mermaid_block(
                    src, dropped=int(token.meta.get("preview_dropped", 0) or 0))

            # 引用块：行级 Token 立即渲染（前缀由自身嵌套深度决定），
            # OPEN/CLOSE 仅表达结构、不产出内容行。修复前用单一 ``_bq_lines``
            # 缓冲 + CLOSE 时渲染：嵌套引用的 OPEN 会清空缓冲（丢失外层内容）、
            # 跨层 BLOCKQUOTE_LINE 在缓冲为 None 时被丢弃、每层 CLOSE 都重复
            # 渲染一次（多出空边框行）。与 Rich 路径 ``InlineHandler`` 语义一致。
            if t in (TokenType.BLOCKQUOTE_OPEN, TokenType.BLOCKQUOTE_CLOSE):
                return []
            if t == TokenType.BLOCKQUOTE_LINE:
                depth = max(1, int(token.meta.get("depth", 1)))
                return blocks.render_blockquote(_StrToken(token.content),
                                                depth=depth - 1)

            if t == TokenType.ADMONITION_OPEN:
                # OPEN content 为 ``[!TYPE]`` 同行文本（head 标题）——正文行
                # 由 ADMONITION_LINE 追加（head 不再混入正文缓冲）。
                ameta = dict(token.meta)
                if token.content:
                    ameta.setdefault("head_text", token.content)
                self._admonition = (ameta.get("type", "NOTE"), [], ameta)
                return []
            if t == TokenType.ADMONITION_LINE:
                if self._admonition is not None and token.content:
                    self._admonition[1].append(token.content)
                return []
            if t == TokenType.ADMONITION_CLOSE:
                body_tokens = token.meta.get("body_tokens")
                if self._admonition is not None:
                    atype, lines, ameta = self._admonition
                    self._admonition = None
                else:
                    # 流式预览：无引擎缓冲时从 token meta 渲染
                    ameta = dict(token.meta)
                    atype = ameta.get("type", "NOTE")
                    lines = list(ameta.get("body_lines") or [])
                title = str(ameta.get("title", "")
                            or token.meta.get("title", "") or "")
                collapsible = bool(ameta.get("collapsible", False))
                head_text = str(ameta.get("head_text",
                                          token.meta.get("head_text", "")) or "")
                dropped = int(token.meta.get("preview_dropped", 0) or 0)
                if body_tokens is not None:
                    # 正文以完整 Markdown 语义递归渲染（列表/代码块/引用/嵌套
                    # 告示…），整体缩进显示（与纯文本路径同为 4 空格缩进）。
                    head = blocks.render_admonition_head(
                        atype, head_text, title=title, collapsible=collapsible)
                    return self._render_nested_blocks(
                        head, body_tokens, dropped, indent="    ")
                meta = dict(ameta)
                meta["type"] = atype
                meta["head_text"] = head_text
                if title:
                    meta["title"] = title
                if dropped:
                    meta["preview_dropped"] = dropped
                return blocks.render_admonition(
                    _StrToken("\n".join(lines), meta)
                )

            if t == TokenType.DETAILS_OPEN:
                # 收集 summary + 正文行（DETAILS_LINE 到来时追加）
                self._details = (token.meta.get("summary", ""), [])
                return []
            if t == TokenType.DETAILS_LINE:
                if self._details is not None and token.content:
                    self._details[1].append(token.content)
                return []
            if t == TokenType.DETAILS_CLOSE:
                body_tokens = token.meta.get("body_tokens")
                if self._details is not None:
                    summary, _body = self._details
                    self._details = None
                else:
                    # ★ 修复（流式预览丢正文）：无引擎缓冲（预览路径）时
                    #   必须从 token meta 取 body_lines——修复前正文行固定为
                    #   ``[]``，<details> 预览只显示 summary、正文全部丢失。
                    summary = token.meta.get("summary", "")
                dropped = int(token.meta.get("preview_dropped", 0) or 0)
                if body_tokens is not None:
                    # 正文以完整 Markdown 语义递归渲染（列表/代码/引用…），
                    # 整体缩进显示（详见 ``_render_nested_blocks``）。
                    return self._render_nested_blocks(
                        blocks.render_details_head(
                            summary, bool(token.meta.get("open", False))),
                        body_tokens, dropped)
                body = list(token.meta.get("body_lines") or [])
                if token.content:
                    body = body + str(token.content).split("\n")
                meta: dict = {"summary": summary, "body_lines": body,
                              "open": bool(token.meta.get("open", False))}
                if dropped:
                    meta["preview_dropped"] = dropped
                return blocks.render_details(_StrToken("", meta))

            if t == TokenType.FENCED_DIV_OPEN:
                # 收集 type + 头行文本 + 正文行（FENCED_DIV_LINE 到来时追加）
                self._fenced_div = (token.meta.get("type", "NOTE"),
                                    token.content or "", [])
                return []
            if t == TokenType.FENCED_DIV_LINE:
                if self._fenced_div is not None and token.content:
                    self._fenced_div[2].append(token.content)
                return []
            if t == TokenType.FENCED_DIV_CLOSE:
                body_tokens = token.meta.get("body_tokens")
                if self._fenced_div is not None:
                    dtype, head_text, body = self._fenced_div
                    self._fenced_div = None
                else:
                    # ★ 修复（流式预览丢正文）：同 DETAILS_CLOSE——预览路径
                    #   必须从 token meta 取 body_lines（修复前正文丢失）。
                    dtype = token.meta.get("type", "NOTE")
                    head_text = token.content or ""
                    body = list(token.meta.get("body_lines") or [])
                dropped = int(token.meta.get("preview_dropped", 0) or 0)
                if body_tokens is not None:
                    # 正文以完整 Markdown 语义递归渲染（列表/代码/引用…），
                    # 整体缩进显示（与 <details>/告示同一机制）。
                    head = blocks.render_fenced_div_head(dtype, head_text)
                    return self._render_nested_blocks(head, body_tokens, dropped)
                meta: dict = {"type": dtype, "body_lines": body}
                if dropped:
                    meta["preview_dropped"] = dropped
                return blocks.render_fenced_div(_StrToken(head_text, meta))

            # HTML 块：语义化标签行 + 内容行（控件/媒体/居中/原始内容隐藏）
            if t == TokenType.HTML_BLOCK_OPEN:
                lines, self._html_state = _html_block.open_html_block(
                    token.meta.get("tag", "div"),
                    token.meta.get("attrs") or {}, self._width,
                    token.meta.get("tail") or "")
                return lines
            if t == TokenType.HTML_BLOCK_LINE:
                if not token.content:
                    return []
                state = self._html_state
                if state is None:
                    # 无 OPEN 的异常序列（如手工构造 Token）：按无状态语义渲染
                    state = _html_block.HtmlBlockState(
                        token.meta.get("tag", ""),
                        token.meta.get("attrs") or {})
                return _html_block.render_html_line(
                    token.content, state, self._width)
            if t == TokenType.HTML_BLOCK_CLOSE:
                lines = _html_block.close_html_block(self._html_state)
                self._html_state = None
                return lines

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
        # 流式预览 token（meta["closed"]=False）不渲染关闭围栏；
        # continuation（超长块被分段刷出的续段）不重复渲染标题/打开围栏。
        closed = token.meta.get("closed", True)
        continuation = bool(token.meta.get("continuation", False))
        linenos = bool(token.meta.get("linenos", False))
        lineno_start = int(token.meta.get("lineno_start", 1) or 1)
        lineno_step = int(token.meta.get("lineno_step", 1) or 1)
        return _code.render_code_block(
            source, lang, self._code_theme, hl, title,
            closed=closed, continuation=continuation, linenos=linenos,
            linenostart=lineno_start, linenostep=lineno_step,
        )

    def _flush_code(self) -> list[AnsiLine]:
        if self._code_state is None:
            return []
        lang, attrs, title, lines = self._code_state
        self._code_state = None
        source = "\n".join(lines)
        if not source and not lang and not title:
            return []
        from src.renderer._utils import parse_highlight_lines, parse_lineno_options
        linenos, lineno_start, lineno_step = parse_lineno_options(attrs)
        return _code.render_code_block(
            source, lang, self._code_theme,
            parse_highlight_lines(attrs), title,
            linenos=linenos,
            linenostart=lineno_start, linenostep=lineno_step,
        )

    # ── 嵌套块渲染（details 正文等） ────────────────────

    def _render_nested_blocks(self, head: AnsiLine,
                              tokens: list, dropped: int = 0,
                              indent: str = "  ") -> list[AnsiLine]:
        """渲染容器块正文（子 Token 序列）并整体缩进。

        Args:
            head: 容器头行（``▶ summary`` / ``■ TYPE title``）。
            tokens: 正文子 Token 序列。
            dropped: 预览截断行数（>0 时插入省略提示行）。
            indent: 正文行前缀（details 用 2 空格、admonition 用 4 空格）。

        用独立的子引擎渲染（不污染主引擎的流式缓冲状态），共享渲染上下文
        （脚注/参考式链接/缩写跨块生效），每行加缩进前缀。
        """
        out: list[AnsiLine] = [head]
        if dropped:
            from .code import render_omitted_line
            out.append(render_omitted_line(dropped))
        sub = AnsiRenderEngine(code_theme=self._code_theme,
                               width=self._width, ctx=self._ctx)
        prefix_style = blocks._STYLE_BQ
        for tok in tokens:
            for ln in sub.render(tok):
                if ln.runs:
                    # 不就地修改：子引擎行可能来自跨帧高亮缓存
                    # （``_LINE_HIGHLIGHT_CACHE``），就地插入缩进会在复用中叠加。
                    out.append(AnsiLine(
                        [Run(indent, prefix_style)] + list(ln.runs)))
                else:
                    out.append(ln)
        return out


# ── 轻量 Token 桩（组装 content/meta） ────────────────────


class _StrToken:
    """content 桩（供 render_blockquote）。"""

    __slots__ = ("content", "meta")

    def __init__(self, content, meta=None):
        self.content = content
        self.meta = meta or {}


# ── 引用块内元素前缀（bq_depth） ──────────────────────────


def _token_bq_depth(token: Token) -> int:
    """Token 的引用块嵌套深度（0 = 不在引用块内）。"""
    meta = getattr(token, "meta", None)
    if not meta:
        return 0
    try:
        return int(meta.get("bq_depth", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _token_list_indent(token: Token) -> int | None:
    """Token 所属列表项的缩进层级；``None`` 表示不在列表项内。

    与引用深度不同，顶层列表项的缩进层为 0——必须用「meta 是否携带该键」
    区分「不在列表内」与「顶层列表项内」，否则默认 0 会让所有 Token 都被
    误加列表前缀。
    """
    meta = getattr(token, "meta", None)
    if not meta or "list_indent" not in meta:
        return None
    try:
        return max(0, int(meta.get("list_indent") or 0))
    except (TypeError, ValueError):
        return None


def _apply_list_prefix(lines: list[AnsiLine], indent: int) -> list[AnsiLine]:
    """给列表项内的块级元素行补内容缩进前缀（与列表续行对齐）。

    前缀 ``"  " * indent + "  "`` 与 ``render_list_item`` 的续行缩进一致
    （``indent`` 为列表项 0-based 缩进层，额外两空格对齐内容起始列）。
    与引用前缀同样**不就地修改**输入行——行对象可能来自跨帧缓存。
    """
    prefix = "  " * max(0, indent) + "  "
    out: list[AnsiLine] = []
    for ln in lines:
        if ln.runs:
            out.append(AnsiLine([Run(prefix, blocks._STYLE_BQ)] + list(ln.runs)))
        else:
            out.append(ln)
    return out


def _apply_bq_prefix(lines: list[AnsiLine], depth: int) -> list[AnsiLine]:
    """给渲染行首插入 ``│ `` 引用前缀（引用块内的块级元素），返回新行列表。

    空行（无 Run）不插前缀，避免引用内空行变成残留边框。

    ★ 不就地修改输入行：行对象可能来自跨帧缓存（``_LINE_HIGHLIGHT_CACHE``
    的代码高亮行、预览行级缓存）——就地 ``runs.insert`` 会在同一行的下次
    复用中重复叠加前缀（流式下「引用内代码块行」出现两个 ``│ ``，一次性
    渲染因缓存尚未污染只出现一个）。此处构造新 ``AnsiLine`` 保证幂等。
    """
    prefix = "\u2502 " * max(1, depth)
    out: list[AnsiLine] = []
    pstyle = blocks.bq_prefix_style(depth)
    for ln in lines:
        if ln.runs:
            out.append(AnsiLine([Run(prefix, pstyle)] + list(ln.runs)))
        else:
            out.append(ln)
    return out


__all__ = ["AnsiRenderEngine"]
