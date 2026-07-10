"""_block_parser — RegexFreeBlockParser：无正则块级递归下降解析器。

从 `recursive_parser.py`（2277 行）提取的 RegexFreeBlockParser 类。
职责：将 Markdown 文本解析为 Token 流（块级扫描 + 内联委托）。
"""

from __future__ import annotations

import logging
from enum import IntEnum

_logger = logging.getLogger(__name__)

from ._utils import (
    _COMMON_LANGUAGES, _get_fence_info, decode_html_entities,
)
from .types import Token, TokenType, RenderContext

from .inline_nodes import (
    InlineNode, TextNode, BoldNode, ItalicNode, BoldItalicNode,
    UnderlineNode, InlineCodeNode, LinkNode, ImageNode,
    StrikethroughNode, HighlightNode, SubscriptNode, SuperscriptNode,
    InlineMathNode, FootnoteRefNode, AutoLinkNode, AutoLinkEmailNode,
    LineBreakNode, _NESTABLE_TYPES, _HTML_TAG_MAP, InlineRecursionError,
)
from .inline_parser import (
    _InlineParser, render_inline_to_text,
)
from ._table_utils import (
    _is_table_row, _is_table_separator,
    _parse_table_row, _parse_table_alignments,
    _SAFE_SENTINEL,
)
from ._block_helpers import (
    _is_empty_line, _count_leading, _strip_left, _rstrip_line,
    _is_only_chars, _starts_with, _has_only_chars,
    _LANG_BLACKLIST, _BLOCK_HTML_TAGS, _VOID_HTML_TAGS,
    _INDENT_SPACES, _INDENT_PREFIX,
    _INLINE_MAX_DEPTH, _MAX_PRESCAN_BUFFER,
    _is_blockquote_line, _get_blockquote_text,
    _is_code_fence_line, _strip_blockquote_prefix,
    _get_fence_lang, _rstrip_trailing_hashes,
)


# Mermaid 图表内容首行关键词（用于延迟 fence 后自动识别 mermaid 块）
_MERMAID_KEYWORDS: frozenset[str] = frozenset({
    "graph", "flowchart", "sequenceDiagram", "classDiagram",
    "stateDiagram", "stateDiagram-v2", "erDiagram", "gantt",
    "pie", "gitgraph", "mindmap", "timeline", "journey",
    "block", "packet", "quadrantChart", "requirementDiagram",
    "C4Context", "C4Container", "C4Component", "C4Deployment",
    "gitGraph",
})

# Setext 标题/HR 标记字符（避免每次调用创建元组）
_SETEXT_HR_CHARS: frozenset[str] = frozenset({'-', '=', '*', '_'})


class _State(IntEnum):
    """解析器状态枚举。"""
    NORMAL = 0
    CODE_FENCE = 1
    MATH_BLOCK = 2
    DISPLAY_MATH_BLOCK = 3
    MERMAID_BLOCK = 4
    DETAILS_BLOCK = 5
    INDENTED_CODE = 6
    HTML_BLOCK = 7
    FENCED_DIV = 8
    TABLE_ACTIVE = 10


# ═══════════════════════════════════════════════════════════
# RegexFreeBlockParser — 无正则的块级递归下降解析器
# ═══════════════════════════════════════════════════════════

class RegexFreeBlockParser:
    """真正无正则的递归下降块级 Markdown 解析器。

    所有块级语法检测通过字符级扫描完成，无任何正则表达式。
    引用块通过剥离 > 前缀后递归解析实现嵌套。

    状态机：
      NORMAL → 按优先级检测各块级语法
      CODE_FENCE → 代码 fence 块内
      MATH_BLOCK → $$ 数学块内
      DISPLAY_MATH_BLOCK → \\[ 显示数学块内
      MERMAID_BLOCK → Mermaid 块内
      DETAILS_BLOCK → <details> 块内
      INDENTED_CODE → 缩进代码块内
      HTML_BLOCK → HTML 块内
      TABLE_ACTIVE → 表格行解析中
    """

    _MAX_BUFFER_SIZE = 1_000_000

    def __init__(self, ctx: RenderContext | None = None):
        self._ctx = ctx if ctx is not None else RenderContext()
        self._buffer = ""
        self._state = _State.NORMAL
        # 必须在 _reset_normal_state 之前初始化列表状态变量和表格缓冲
        self._list_indents: list[int] = []
        self._table_rows: list[list[str]] = []
        self._table_alignments: list[str] = []
        self._table_pending_rows: list[str] = []
        # 定义列表续行缓冲
        self._def_cont_buffer: list[str] = []
        self._reset_normal_state()

        # 多行块状态
        self._block_fence_char: str = ''
        self._block_fence_len: int = 0
        self._block_lang: str = 'text'
        self._block_attrs: str = ''
        self._block_title: str = ''
        self._block_lines: list[str] = []
        self._block_html_tag: str = ''
        self._block_nested_fence: int = 0
        self._block_div_type: str = ''

        # 列表状态
        # _list_indents 已在 _reset_normal_state 之前初始化

        # 引用块状态
        self._bq_active: bool = False
        self._bq_depth_stack: list[int] = []
        self._bq_in_recursion: int = 0
        self._pending_lines: list[str] = []

        # Admonition
        self._in_admonition: bool = False
        self._admonition_type: str = ''

        # 脚注定义
        self._pending_fn_def: str | None = None

        # 延迟 fence（流式场景）
        self._deferred_fence: dict | None = None

        # Details 块状态
        self._details_depth: int = 0
        self._details_summary: str = ''
        self._details_open_emitted: bool = False

        # 自动关闭 fence 连续匹配计数器（降低误判）
        self._auto_close_streak: int = 0

        # 预扫描位置
        self._prescan_pos: int = 0

        # 每个 handler 独立的降级计数器，避免跨 handler 污染
        self._silent_downgrade_count: dict[str, int] = {}

        # 上一个 emit 的 TokenType（用于列表续行检测等需要前后文感知的场景）
        self._last_token_type: TokenType | None = None
        # 上一个 LIST_ITEM 的缩进量（用于续行缩进匹配）
        self._last_list_indent: int = -1
        # 上一个 LIST_ITEM 的内容起始列（= indent + marker宽度）
        self._last_list_content_col: int = -1

        # 标题 ID 去重字典
        self._used_heading_ids: dict[str, int] = {}

    def _reset_normal_state(self):
        """重置 NORMAL 状态变量。"""
        self._pending_lines = []
        self._table_pending_rows.clear()
        self._in_admonition = False
        self._admonition_type = ''
        self._pending_fn_def = None
        self._def_cont_buffer.clear()
        self._list_indents.clear()

    def _reset_for_buffer_trim(self):
        """缓冲区裁剪后重置所有可能残留的状态。"""
        self._reset_normal_state()
        self._table_rows.clear()
        self._table_alignments.clear()
        self._deferred_fence = None
        self._bq_active = False
        self._bq_depth_stack.clear()
        self._bq_in_recursion = 0
        self._auto_close_streak = 0
        self._prescan_pos = 0
        self._last_token_type = None
        self._last_list_indent = -1
        self._last_list_content_col = -1

    # ═══════════════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════════════

    def feed(self, text: str) -> list[Token]:
        """输入文本片段，返回已解析的 Token。"""
        tokens: list[Token] = []
        self._buffer += text

        # 缓冲区安全裁剪
        if len(self._buffer) > self._MAX_BUFFER_SIZE:
            if self._state != _State.NORMAL:
                self._flush_block(tokens)
                if self._state != _State.NORMAL:
                    self._state = _State.NORMAL
            half = len(self._buffer) // 2
            cutoff = self._buffer.find('\n', half)
            if cutoff >= 0:
                self._buffer = self._buffer[cutoff + 1:]
            else:
                cutoff = self._buffer.rfind('\n', 0, half)
                if cutoff >= 0:
                    self._buffer = self._buffer[cutoff + 1:]
                else:
                    cutoff = self._MAX_BUFFER_SIZE
                    self._buffer = self._buffer[-cutoff:]
            if self._bq_active:
                self._bq_in_recursion = 0
                self._emit_blockquote_close(tokens)
            self._flush_paragraph(tokens)
            if self._deferred_fence is not None:
                fence = self._deferred_fence
                self._deferred_fence = None
                fence['lang'] = 'text'
                self._start_code_fence(fence, tokens)
            self._reset_for_buffer_trim()

        # 预扫描参考链接和脚注
        self._prescan_refs()

        # 逐行处理
        while True:
            idx = self._buffer.find('\n')
            if idx == -1:
                break
            line = self._buffer[:idx + 1]
            self._buffer = self._buffer[idx + 1:]

            try:
                if self._state != _State.NORMAL:
                    self._feed_block_line(line, tokens)
                else:
                    self._parse_normal_line(line, tokens)
            except Exception:
                _logger.debug("行处理异常，跳过本行", exc_info=True)
                self._state = _State.NORMAL
                self._buffer = ""

        # 每次 feed 结束后重置预扫描位置，下次 feed 从头扫描
        self._prescan_pos = 0
        self._silent_downgrade_count.clear()
        return tokens

    def flush(self) -> list[Token]:
        """刷出所有剩余内容。"""
        tokens: list[Token] = []

        # ── 第1步：处理残留缓冲区 ──
        remaining = self._buffer.strip()
        if remaining:
            line = self._buffer + '\n'
            self._buffer = ''
            if self._state != _State.NORMAL:
                self._feed_block_line(line, tokens)
            else:
                self._parse_normal_line(line, tokens)

        # ── 第1.5步：处理未解析的延迟 fence ──
        if self._deferred_fence is not None:
            fence = self._deferred_fence
            self._deferred_fence = None
            self._start_code_fence(fence, tokens)

        # ── 第2步：刷出非 NORMAL 状态的块 ──
        if self._state != _State.NORMAL:
            if self._state == _State.CODE_FENCE:
                if self._block_lines:
                    for l in self._block_lines:
                        tokens.append(Token(TokenType.CODE_LINE, l))
                tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                                    {"lang": self._block_lang}))
            elif self._state == _State.MERMAID_BLOCK:
                source = ''.join(self._block_lines).strip()
                if source:
                    tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source))
            elif self._state == _State.INDENTED_CODE:
                tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                    "lang": "text", "indented": True,
                }))
            elif self._state == _State.FENCED_DIV:
                tokens.append(Token(TokenType.FENCED_DIV_CLOSE, "", {"type": self._block_div_type}))
                self._state = _State.NORMAL
            else:
                self._flush_block(tokens)
            self._state = _State.NORMAL

        # ── 第3步：关闭引用块（先于段落刷出，确保引用内容以 BLOCKQUOTE_LINE 发出） ──
        self._emit_blockquote_close(tokens)

        # ── 第4步：刷出段落缓冲 ──
        self._flush_paragraph(tokens)

        # ── 第5步：关闭 admonition ──
        if self._in_admonition:
            tokens.append(Token(TokenType.ADMONITION_CLOSE, "",
                                {"type": self._admonition_type}))
            self._in_admonition = False
            self._admonition_type = ''

        # ── 第6步：再次刷出段落 ──
        self._flush_paragraph(tokens)

        # ── 第7步：刷出残留的流式表格缓冲 ──
        if self._table_pending_rows:
            self._emit_pending_table(tokens)

        return tokens

    # ═══════════════════════════════════════════════════════════
    # 预扫描
    # ═══════════════════════════════════════════════════════════

    def _prescan_refs(self):
        """预扫描参考链接 [id]: url 和脚注定义 [^id]: content（增量）。"""
        try:
            if self._prescan_pos >= len(self._buffer):
                return
            buf = self._buffer
            n = len(buf)
            pos = self._prescan_pos
            while pos < n:
                nl = buf.find('\n', pos)
                if nl == -1:
                    break
                line = buf[pos:nl + 1]
                pos = nl + 1
                stripped = _strip_left(line)
                if not stripped:
                    continue
                if stripped[0] == '[':
                    colon_pos = stripped.find(']:')
                    if colon_pos > 0:
                        ref_id = stripped[1:colon_pos]
                        if '^' not in ref_id:
                            rest = stripped[colon_pos + 2:].strip()
                            url_end = self._find_url_end(rest)
                            url = rest[:url_end]
                            title = ''
                            after_url = rest[url_end:].strip()
                            if after_url and after_url[0] in '"\'':
                                quote = after_url[0]
                                end = after_url.find(quote, 1)
                                if end > 0:
                                    title = after_url[1:end]
                            if url and ref_id:
                                self._ctx.ref_map[ref_id] = (url, title)
                        else:
                            ref_id = ref_id[1:]
                            content = stripped[colon_pos + 2:].strip()
                            if ref_id and content:
                                self._ctx.fn_map[ref_id] = content
                                if ref_id not in self._ctx.fn_order:
                                    self._ctx.fn_order.append(ref_id)
            self._prescan_pos = pos
        except Exception:
            _logger.debug("_prescan_refs预扫描异常", exc_info=True)

    @staticmethod
    def _find_url_end(text: str) -> int:
        """找到 URL 的结束位置（遇到空白或结尾）。"""
        i = 0
        in_parentheses = 0
        while i < len(text):
            ch = text[i]
            if ch == '%' and i + 2 < len(text):
                i += 3
                continue
            if ch in ' \t':
                if in_parentheses == 0:
                    break
            elif ch == '(':
                in_parentheses += 1
            elif ch == ')':
                in_parentheses -= 1
                if in_parentheses < 0:
                    break
            i += 1
        return i

    # ═══════════════════════════════════════════════════════════
    # 行处理分发
    # ═══════════════════════════════════════════════════════════

    def _parse_normal_line(self, line: str, tokens: list[Token]):
        """NORMAL 状态：按首字符调度表分派语法检测。"""
        stripped = _strip_left(line).rstrip('\n')

        # ── 延迟 fence 处理 ──
        if self._deferred_fence is not None:
            self._handle_deferred_fence(stripped, tokens)
            return

        # Empty
        if not stripped or _is_empty_line(stripped):
            self._handle_empty_line(tokens)
            return

        # ── 注释行：[//]: # (comment) 或 [//]: # comment ──
        if stripped.startswith('[//]:'):
            return

        first = stripped[0] if stripped else ''

        # ── 定义续行检查（必须在字母快速通道之前，防止续行被段落吞噬） ──
        if self._pending_fn_def == '__def__':
            if line.startswith('    ') or line.startswith('\t'):
                cont = _rstrip_line(line).strip()
                if cont:
                    self._def_cont_buffer.append(cont)
                    return
            elif stripped.startswith(':'):
                pass
            else:
                self._pending_fn_def = None

        # ── GFM 无前导 pipe 表格行检测（在字母快速通道前拦截） ──
        # 如 "Name|Age" 或 "a|b|c" 样式的行，避免被段落吞噬
        # ★ 排除 blockquote 行（> 前缀）和递归引用块内部
        # ★ 排除列表项：- item | detail 之类不应误判为表格
        first_non_space = stripped.lstrip()
        list_prefix = False
        if first_non_space:
            ch0 = first_non_space[0]
            if ch0 in ('-', '*', '+') and len(first_non_space) > 1 and first_non_space[1] == ' ':
                list_prefix = True
            elif ch0.isdigit() and '. ' in first_non_space[:4]:
                list_prefix = True
        if (self._bq_in_recursion == 0
                and not self._table_pending_rows
                and not list_prefix
                and '|' in stripped
                and not stripped.startswith('|')
                and not _is_blockquote_line(stripped)):
            check = stripped.replace('\\|', '')
            parts = [p.strip() for p in check.split('|') if p.strip()]
            # 至少 2 个非空部分（单 pipe 分隔）且非分隔行
            if len(parts) >= 2 and not _is_table_separator(stripped):
                self._table_pending_rows.append(stripped)
                return

        # ── 字母快速通道 ──
        if self._may_be_paragraph_text(first, stripped):
            if self._bq_active and self._bq_in_recursion == 0:
                self._emit_blockquote_close(tokens)
            if self._table_pending_rows:
                self._emit_pending_table(tokens)

            # ── 列表续行检测：如果上一 Token 是 LIST_ITEM 且当前行缩进匹配 → 合并为续行 ──
            if (self._last_token_type is TokenType.LIST_ITEM
                    and self._last_list_indent >= 0
                    and tokens and tokens[-1].type is TokenType.LIST_ITEM):
                leading = 0
                for ch in line:
                    if ch in ' \t':
                        leading += 1
                    else:
                        break
                if leading >= self._last_list_content_col:
                    raw = line.rstrip('\n')
                    old_token = tokens[-1]
                    tokens[-1] = Token(
                        TokenType.LIST_ITEM,
                        old_token.content + '\n' + raw,
                        old_token.meta,
                    )
                    return

            self._handle_paragraph_line(line, tokens)
            return

        # 关闭引用块
        if self._bq_active and self._bq_in_recursion == 0:
            if first != '>' or not _is_blockquote_line(stripped):
                self._emit_blockquote_close(tokens)

        # ── 非首字符可检测的逻辑 ──
        # （定义续行已移至快速通道前）

        # 脚注续行
        if self._pending_fn_def is not None and self._pending_fn_def != '__def__':
            if line.startswith('    ') or line.startswith('\t') or line.startswith('  '):
                cont = _rstrip_line(line).strip()
                if cont and self._pending_fn_def in self._ctx.fn_map:
                    self._ctx.fn_map[self._pending_fn_def] += ' ' + cont
                    return
                else:
                    self._pending_fn_def = None
            else:
                self._pending_fn_def = None

        # 缩进代码块（但在列表上下文中优先作为列表内容/子列表项）
        if not self._in_admonition and (line[:4] == '    ' or (line and line[0] == '\t')):
            # 如果在活跃的列表上下文中，缩进行应通过 dispatch 处理为列表项/续行
            if self._list_indents or self._last_token_type is TokenType.LIST_ITEM:
                pass  # 让 dispatch 表处理，不拦截
            else:
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                self._start_indented_code(line, tokens)
                return

        # 表格活动状态
        if self._state == _State.TABLE_ACTIVE:
            if _is_table_row(stripped):
                self._table_rows.append(_parse_table_row(stripped))
                return
            self._emit_table(tokens)

        # 流式表格行缓冲
        if self._table_pending_rows:
            if _is_table_separator(stripped):
                self._start_table(stripped, tokens)
                return
            if _is_table_row(stripped):
                if len(self._table_pending_rows) >= 100:
                    self._emit_pending_table(tokens)
                self._table_pending_rows.append(stripped)
                return
            self._emit_pending_table(tokens)

        # Admonition 续行（或新的 [!TYPE] 切换）
        if self._in_admonition and first == '>':
            text = _get_blockquote_text(stripped)
            # 检测是否切换为新的 [!TYPE] 告示
            if text.startswith('[') and '!' in text[:8]:
                close_bracket = text.find(']')
                # 宽松检测：`]` 后任意字符都接受
                if close_bracket > 2:
                    new_type = text[2:close_bracket].upper()
                    if new_type in ('NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION', 'CITE',
                                    'INFO', 'SUCCESS', 'QUESTION', 'BUG', 'DANGER'):
                        # 关闭当前告示，打开新的
                        tokens.append(Token(TokenType.ADMONITION_CLOSE, "",
                                            {"type": self._admonition_type}))
                        self._admonition_type = new_type
                        adm_text = text[close_bracket + 1:].strip()
                        tokens.append(Token(TokenType.ADMONITION_OPEN, adm_text,
                                            {"type": new_type, "depth": 1}))
                        return
            tokens.append(Token(TokenType.ADMONITION_LINE, text,
                                {"depth": 1, "type": self._admonition_type}))
            return

        # ═══════════════════════════════════════════════════════════
        # 首字符调度表
        # ═══════════════════════════════════════════════════════════
        if self._dispatch_normal_line(first, stripped, line, tokens):
            return

        # 段落（fallback）
        self._handle_paragraph_line(line, tokens)

    def _dispatch_normal_line(self, first: str, stripped: str, line: str,
                               tokens: list[Token]) -> bool:
        """按首字符查表分派语法检测。返回 True 表示行已处理，False 降级为段落。"""

        def _handle_heading() -> bool:
            try:
                heading = self._try_heading(stripped)
                if heading is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._list_indents.clear()
                    tokens.append(heading)
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('heading', 0) + 1
                self._silent_downgrade_count['heading'] = count
                _logger.warning("Heading解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_fence() -> bool:
            try:
                fence = self._try_code_fence_start(stripped)
                if fence is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    if fence['deferred']:
                        self._deferred_fence = fence
                        return True
                    self._start_code_fence(fence, tokens)
                    if fence.get('extra'):
                        if self._state == _State.MERMAID_BLOCK:
                            tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                        else:
                            tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('fence', 0) + 1
                self._silent_downgrade_count['fence'] = count
                _logger.warning("Code fence解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_html() -> bool:
            lower_check = stripped.lower().lstrip()
            if lower_check.startswith('</details') or lower_check.startswith('</summary'):
                return False
            if self._try_details_open(stripped):
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                self._start_details(stripped, tokens)
                return True
            tag = self._try_block_html(stripped)
            if tag is not None:
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                self._start_html_block(tag, tokens, stripped)
                return True
            return False

        def _handle_bracket() -> bool:
            if ']:' in stripped:
                try:
                    fn = self._try_fn_def(stripped)
                    if fn is not None:
                        self._flush_paragraph(tokens)
                        self._handle_fn_def(fn, tokens)
                        return True
                    if self._try_ref_link(stripped):
                        return True
                except Exception:
                    count = self._silent_downgrade_count.get('bracket', 0) + 1
                    self._silent_downgrade_count['bracket'] = count
                    _logger.warning("脚注/参考链接解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            if stripped == '[TOC]' or stripped.rstrip() == '[TOC]':
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                tokens.append(Token(TokenType.TOC_MARKER, ""))
                return True
            return False

        def _handle_fenced_div() -> bool:
            if len(stripped) >= 3 and stripped[:3] == ':::':
                try:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._handle_fenced_div_open(stripped, tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('fenced_div', 0) + 1
                    self._silent_downgrade_count['fenced_div'] = count
                    _logger.warning("Fenced div解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_definition() -> bool:
            try:
                def_item = self._try_definition(stripped)
                if def_item is not None:
                    term = ""
                    if self._pending_lines:
                        last = self._pending_lines[-1]
                        if isinstance(last, str) and last.strip():
                            term = last.strip()
                            self._pending_lines = self._pending_lines[:-1]
                    # ★ 将续行内容合并到前一个 DEFINITION_ITEM 中（不是当前这个）
                    if self._def_cont_buffer:
                        cont_text = '\n'.join(self._def_cont_buffer)
                        if tokens and tokens[-1].type is TokenType.DEFINITION_ITEM:
                            old_content = tokens[-1].content
                            tokens[-1] = Token(TokenType.DEFINITION_ITEM,
                                               old_content + '\n' + cont_text,
                                               tokens[-1].meta)
                        else:
                            # 跨 feed 无前一个 DEFINITION_ITEM，暂存到 pending_lines
                            self._pending_lines.append(cont_text)
                        self._def_cont_buffer.clear()
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    tokens.append(Token(TokenType.DEFINITION_ITEM, def_item,
                                        {"term": term, "indent": 0}))
                    self._pending_fn_def = '__def__'
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('definition', 0) + 1
                self._silent_downgrade_count['definition'] = count
                _logger.warning("定义列表解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_colon() -> bool:
            """处理 : 开头的行：先试 fenced div (:::)，再试定义列表 (: text)。"""
            if _handle_fenced_div():
                return True
            return _handle_definition()

        def _handle_setext_or_hr() -> bool:
            try:
                if self._try_setext_or_hr(stripped, first, tokens):
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('setext_or_hr', 0) + 1
                self._silent_downgrade_count['setext_or_hr'] = count
                _logger.warning("Setext/HR解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            if first in ('-', '*'):
                try:
                    ul = self._try_ul_item(stripped, line)
                    if ul is not None:
                        self._flush_paragraph(tokens)
                        self._emit_blockquote_close(tokens)
                        self._update_list_indent(ul['indent'])
                        tokens.append(Token(TokenType.LIST_ITEM, ul['text'], {
                            "indent": ul['indent'], "depth": len(self._list_indents),
                            "bullet": True,
                            "todo": ul.get('todo', False),
                            "checked": ul.get('checked', False),
                            "cancelled": ul.get('cancelled', False),
                        }))
                        self._last_token_type = TokenType.LIST_ITEM
                        self._last_list_indent = ul['indent']
                        self._last_list_content_col = ul['indent'] + 2
                        return True
                except Exception:
                    count = self._silent_downgrade_count.get('ul_item_in_setext', 0) + 1
                    self._silent_downgrade_count['ul_item_in_setext'] = count
                    _logger.warning("无序列表解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_star() -> bool:
            if self._handle_abbreviation(stripped):
                return True
            return _handle_setext_or_hr()

        def _handle_display_math() -> bool:
            if len(stripped) == 2 and stripped == r'\[':
                try:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._start_display_math(tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('display_math', 0) + 1
                    self._silent_downgrade_count['display_math'] = count
                    _logger.warning("显示数学块解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_math_block() -> bool:
            if len(stripped) >= 2 and stripped[:2] == '$$' and _is_only_chars(stripped, '$'):
                try:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._start_math_block(tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('math_block', 0) + 1
                    self._silent_downgrade_count['math_block'] = count
                    _logger.warning("数学块解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_table() -> bool:
            # ★ 引用块内部不检测表格（让 > 前缀的表格行降级为段落）
            if self._bq_in_recursion > 0:
                return False
            try:
                if _is_table_row(stripped):
                    if len(self._table_pending_rows) >= 100:
                        self._emit_pending_table(tokens)
                    self._table_pending_rows.append(stripped)
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('table', 0) + 1
                self._silent_downgrade_count['table'] = count
                _logger.warning("表格行解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_blockquote() -> bool:
            if _is_blockquote_line(stripped):
                try:
                    # ★ 修复嵌套引用: 在进入 blockquote 前，若不在引用内，
                    # 刷新外部段落缓冲为 PARAGRAPH。
                    # 若已在引用内，延迟到 _parse_blockquote 中按深度变更刷新。
                    if self._pending_lines and not self._bq_active:
                        self._flush_paragraph(tokens)
                    self._parse_blockquote(stripped, tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('blockquote', 0) + 1
                    self._silent_downgrade_count['blockquote'] = count
                    _logger.warning("引用块解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_plus() -> bool:
            try:
                ul = self._try_ul_item(stripped, line)
                if ul is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._update_list_indent(ul['indent'])
                    tokens.append(Token(TokenType.LIST_ITEM, ul['text'], {
                        "indent": ul['indent'], "depth": len(self._list_indents),
                        "bullet": True,
                        "todo": ul.get('todo', False),
                        "checked": ul.get('checked', False),
                        "cancelled": ul.get('cancelled', False),
                    }))
                    self._last_token_type = TokenType.LIST_ITEM
                    self._last_list_indent = ul['indent']
                    self._last_list_content_col = ul['indent'] + 2  # "- " marker
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('plus', 0) + 1
                self._silent_downgrade_count['plus'] = count
                _logger.warning("无序列表解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_ordered_list() -> bool:
            try:
                ol = self._try_ol_item(stripped, line)
                if ol is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._update_list_indent(ol['indent'])
                    # marker_width 由 _try_ol_item 计算（"1. " 或 "1) "）
                    marker_width = ol.get('marker_width', len(str(ol['number'])) + 2)
                    tokens.append(Token(TokenType.LIST_ITEM, ol['text'], {
                        "indent": ol['indent'], "depth": len(self._list_indents),
                        "bullet": False, "number": ol['number'],
                        "start": ol.get('start', ol['number']),
                        "todo": ol.get('todo', False),
                        "checked": ol.get('checked', False),
                        "cancelled": ol.get('cancelled', False),
                        "delimiter": ol.get('delimiter', '.'),
                    }))
                    self._last_token_type = TokenType.LIST_ITEM
                    self._last_list_indent = ol['indent']
                    self._last_list_content_col = ol['indent'] + marker_width
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('ordered_list', 0) + 1
                self._silent_downgrade_count['ordered_list'] = count
                _logger.warning("有序列表解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        _DISPATCH: dict[str, callable] = {
            '#': _handle_heading,
            '`': _handle_fence,
            '~': _handle_fence,
            '<': _handle_html,
            '[': _handle_bracket,
            ':': _handle_colon,
            '-': _handle_setext_or_hr,
            '=': _handle_setext_or_hr,
            '*': _handle_star,
            '_': _handle_setext_or_hr,
            '\\': _handle_display_math,
            '$': _handle_math_block,
            '|': _handle_table,
            '>': _handle_blockquote,
            '+': _handle_plus,
        }

        if first.isdigit():
            return _handle_ordered_list()

        handler = _DISPATCH.get(first)
        if handler is not None:
            return handler()
        return False

    def _feed_block_line(self, line: str, tokens: list[Token]):
        """处理非 NORMAL 状态下的行。"""
        stripped = _strip_left(line).rstrip('\n')

        if self._bq_active and stripped and stripped[0] == '>':
            s = stripped
            while s.startswith('>'):
                s = _strip_blockquote_prefix(s)
            line = s + '\n'
            stripped = s

        if self._state == _State.CODE_FENCE:
            try:
                self._feed_code_fence_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Code fence块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.MATH_BLOCK:
            try:
                if stripped == '$$':
                    self._emit_math_block(tokens)
                elif stripped.endswith('$$') and len(stripped) > 2:
                    content_line = stripped[:-2].rstrip()
                    if content_line:
                        self._block_lines.append(content_line)
                    self._emit_math_block(tokens)
                else:
                    self._block_lines.append(line.rstrip('\n'))
            except Exception:
                _logger.debug("数学块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.DISPLAY_MATH_BLOCK:
            try:
                if stripped == r'\]':
                    self._emit_math_block(tokens)
                elif stripped.endswith(r'\]') and len(stripped) > 2:
                    content_line = stripped[:-2].rstrip()
                    if content_line:
                        self._block_lines.append(content_line)
                    self._emit_math_block(tokens)
                else:
                    self._block_lines.append(line.rstrip('\n'))
            except Exception:
                _logger.debug("显示数学块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.MERMAID_BLOCK:
            try:
                if _is_code_fence_line(stripped):
                    self._emit_mermaid_block(tokens)
                else:
                    self._block_lines.append(line)
            except Exception:
                _logger.debug("Mermaid块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.DETAILS_BLOCK:
            try:
                self._feed_details_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Details块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.INDENTED_CODE:
            try:
                self._feed_indented_code_line(line, stripped, tokens)
            except Exception:
                _logger.debug("缩进代码块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.FENCED_DIV:
            try:
                self._feed_fenced_div_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Fenced div块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.HTML_BLOCK:
            if self._is_html_close(stripped, self._block_html_tag):
                tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                    {"tag": self._block_html_tag}))
                self._state = _State.NORMAL
            else:
                tokens.append(Token(TokenType.HTML_BLOCK_LINE,
                                    line.rstrip('\n')))

        elif self._state == _State.TABLE_ACTIVE:
            try:
                check = _strip_blockquote_prefix(stripped)
                if check != stripped:
                    if _is_table_row(check):
                        self._table_rows.append(_parse_table_row(check))
                    else:
                        self._emit_table(tokens)
                        self._state = _State.NORMAL
                        self._parse_normal_line(line, tokens)
                elif _is_table_row(stripped):
                    self._table_rows.append(_parse_table_row(stripped))
                else:
                    self._emit_table(tokens)
                    self._state = _State.NORMAL
                    self._parse_normal_line(line, tokens)
            except Exception:
                _logger.debug("表格活动状态行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

    # ── 代码 fence 块内 ──────────────────────────────────

    def _should_auto_close_fence(self, stripped: str, line: str = '') -> bool:
        if not stripped:
            self._auto_close_streak = 0
            return False
        if self._block_lang.lower() not in ('text', 'txt', 'plain', ''):
            self._auto_close_streak = 0
            return False
        if line and (line[0] in ' \t'):
            self._auto_close_streak = 0
            return False
        if stripped[0] not in '#-*_|':
            self._auto_close_streak = 0
            return False
        matched = False
        if len(stripped) >= 3 and stripped[0] == '#':
            level = 0
            for ch in stripped:
                if ch == '#':
                    level += 1
                else:
                    break
            if 2 <= level <= 6 and level < len(stripped) and stripped[level] == ' ':
                matched = True
        if not matched and len(stripped) >= 3:
            first = stripped[0]
            if first in _SETEXT_HR_CHARS:
                only = True
                for ch in stripped:
                    if ch not in (' ', first):
                        only = False
                        break
                if only and len(stripped.replace(' ', '')) >= 3:
                    matched = True
        if not matched and stripped[0] == '|' and stripped.count('|') >= 2:
            # 只有包含分隔行模式（:- 等）才是真正的表格行，避免对含 pipe 的普通内容误触发
            if ':-' in stripped or '-:' in stripped or ':-:' in stripped:
                matched = True
        if matched:
            self._auto_close_streak += 1
            return self._auto_close_streak >= 5
        else:
            self._auto_close_streak = 0
            return False

    def _feed_code_fence_line(self, line: str, stripped: str, tokens: list[Token]):
        if stripped and not (stripped[0] in '#-*_|' and len(stripped) >= 3):
            self._auto_close_streak = 0

        if self._auto_close_streak > 0 and stripped and stripped[0] == '|':
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if any(c.isalnum() for c in cells if c):
                self._auto_close_streak = 0

        if self._should_auto_close_fence(stripped, line):
            tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                                {"lang": self._block_lang, "indented": False}))
            self._state = _State.NORMAL
            self._parse_normal_line(line, tokens)
            return
        fchar, flen, _ = _get_fence_info(stripped)
        if not fchar:
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        if fchar != self._block_fence_char:
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        if flen < self._block_fence_len:
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        close_lang = _get_fence_lang(stripped[flen:].strip())
        is_markdown = self._block_lang.lower() in ('markdown', 'md')
        if close_lang and is_markdown:
            self._block_nested_fence += 1
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        if not close_lang and is_markdown and self._block_nested_fence > 0:
            self._block_nested_fence -= 1
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                            {"lang": self._block_lang, "indented": False}))
        self._state = _State.NORMAL

    # ── Details 块内 ─────────────────────────────────────

    def _handle_details_summary_line(self, stripped: str, tokens: list[Token]):
        lower = stripped.lower()
        summary = ''
        rest = stripped
        sm_start = lower.find('<summary')
        if sm_start >= 0:
            tag_end = stripped.find('>', sm_start)
            if tag_end >= 0:
                content_start = tag_end + 1
                close = lower.find('</summary>', content_start)
                if close >= 0:
                    summary = stripped[content_start:close].strip()
                    rest = stripped[close + 10:].strip()
        self._details_summary = summary
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": summary}))
            self._details_open_emitted = True
        if rest:
            self._block_lines.append(rest)

    def _emit_details_close(self, tokens: list[Token]):
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": self._details_summary}))
            self._details_open_emitted = True
        if self._block_lines:
            for l in self._block_lines:
                tokens.append(Token(TokenType.DETAILS_LINE, l))
            self._block_lines = []
        tokens.append(Token(TokenType.DETAILS_CLOSE))
        self._state = _State.NORMAL

    def _feed_details_line(self, line: str, stripped: str, tokens: list[Token]):
        lower = stripped.lower()
        if lower.startswith('</details'):
            self._details_depth -= 1
            if self._details_depth > 0:
                self._block_lines.append(stripped)
                return
            self._emit_details_close(tokens)
            return
        if lower.startswith('<details'):
            self._details_depth += 1
            self._block_lines.append(stripped)
            return
        if '<summary' in lower:
            if self._details_depth > 1:
                self._block_lines.append(stripped)
            else:
                self._handle_details_summary_line(stripped, tokens)
            return
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": self._details_summary}))
            self._details_open_emitted = True
        self._block_lines.append(stripped)

    # ── 缩进代码块内 ────────────────────────────────────

    def _feed_indented_code_line(self, line: str, stripped: str, tokens: list[Token]):
        if _is_empty_line(stripped):
            tokens.append(Token(TokenType.CODE_LINE, ""))
            return
        if line[:4] == '    ' or (line and line[0] == '\t'):
            content = line[4:] if line[:4] == '    ' else line[1:]
            tokens.append(Token(TokenType.CODE_LINE, content.rstrip('\n')))
            return
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
            "lang": "text", "indented": True,
        }))
        self._state = _State.NORMAL
        self._parse_normal_line(line, tokens)

    # ═══════════════════════════════════════════════════════════
    # 块级语法尝试方法
    # ═══════════════════════════════════════════════════════════

    def _try_heading(self, stripped: str) -> Token | None:
        try:
            level = 0
            i = 0
            while i < len(stripped) and stripped[i] == '#':
                level += 1
                i += 1
            if level < 1 or level > 6:
                return None
            if i >= len(stripped):
                return None
            # ★ P0 修复: CommonMark 要求 # 后必须跟空格才构成 ATX 标题。
            #   #text（无空格）应降级为段落。
            if stripped[i] != ' ':
                return None
            text = stripped[i + 1:].strip()
            if not text:
                auto_id = self._generate_heading_id(text)
                return Token(TokenType.HEADING, '',
                             {"level": level, "id": auto_id})
            text = _rstrip_trailing_hashes(text)

            heading_id = ''
            attrs: dict | None = None
            if text.endswith('}'):
                brace_start = text.rfind('{')
                if brace_start >= 0:
                    raw = text[brace_start + 1:-1].strip()
                    if raw:
                        parsed = self._parse_heading_attrs(raw)
                        if parsed is not None:
                            heading_id = parsed.get('id', '')
                            attrs = parsed
                            text = text[:brace_start].strip()

            meta: dict = {"level": level}
            if heading_id:
                meta["id"] = heading_id
            else:
                auto_id = self._generate_heading_id(text)
                meta["id"] = auto_id
            if attrs:
                meta["attrs"] = attrs
            return Token(TokenType.HEADING, text, meta)
        except Exception:
            _logger.debug("_try_heading异常，返回None", exc_info=True)
            return None

    def _generate_heading_id(self, text: str) -> str:
        """从标题文本自动生成锚点 ID（带重复去重后缀 -1, -2...）。"""
        if not text:
            base = "section"
        else:
            result = text.lower()
            cleaned = []
            for ch in result:
                if ch.isalnum() or ch in ' -_':
                    cleaned.append(ch)
                else:
                    cleaned.append('-')
            result = ''.join(cleaned)
            result = result.replace(' ', '-')
            while '--' in result:
                result = result.replace('--', '-')
            result = result.strip('-')
            if len(result) > 80:
                result = result[:80].rstrip('-')
            base = result if result else "section"
        # Deduplication: append -1, -2, etc. for duplicate IDs
        if base in self._used_heading_ids:
            self._used_heading_ids[base] += 1
            return f"{base}-{self._used_heading_ids[base]}"
        else:
            self._used_heading_ids[base] = 0
            return base

    @staticmethod
    def _parse_heading_attrs(raw: str) -> dict | None:
        if not raw:
            return None

        def _split_attrs(s: str) -> list[str]:
            parts = []
            buf: list[str] = []
            quote = None
            for c in s:
                if c in '"\'' and quote is None:
                    quote = c
                    buf.append(c)
                elif c == quote:
                    quote = None
                    buf.append(c)
                elif c == ' ' and quote is None:
                    if buf:
                        parts.append(''.join(buf))
                        buf = []
                else:
                    buf.append(c)
            if buf:
                parts.append(''.join(buf))
            return parts

        result: dict = {}
        classes: list[str] = []
        for part in _split_attrs(raw):
            if not part:
                continue
            if part.startswith('#') and len(part) > 1:
                id_val = ''.join(c if c.isalnum() or c in '-_' else '_' for c in part[1:])
                result['id'] = id_val
            elif part.startswith('.') and len(part) > 1:
                cls = ''.join(c if c.isalnum() or c in '-_' else '_' for c in part[1:])
                classes.append(cls)
            elif '=' in part:
                key, _, val = part.partition('=')
                key = key.strip()
                val = val.strip()
                if key and val:
                    if len(val) >= 2 and val[0] in '"\'' and val[-1] == val[0]:
                        val = val[1:-1]
                    result[key] = val
        if classes:
            result['classes'] = classes
        return result if result else None

    @staticmethod
    def _detect_streaming_fence_merge(
        potential: str, remaining: str, lang_end: int,
    ) -> tuple[str, str]:
        """检测流式 chunk 边界合并导致的语言标识粘连。"""
        found_prefix = False
        for i in range(min(len(potential), 12), 2, -1):
            prefix = potential[:i]
            if prefix in _COMMON_LANGUAGES:
                lang = prefix
                extra_chars = potential[i:]
                remaining = extra_chars + remaining[lang_end:]
                found_prefix = True
                break
        if not found_prefix:
            lang = potential
            remaining = remaining[lang_end:]
        return lang, remaining

    def _try_code_fence_start(self, stripped: str) -> dict | None:
        try:
            first = stripped[0]
            if first not in ('`', '~'):
                return None
            count = 0
            for ch in stripped:
                if ch == first:
                    count += 1
                else:
                    break
            if count < 3:
                return None
            remaining = stripped[count:].strip()
            lang = ''
            attrs = ''
            title = ''
            extra = ''
            if remaining:
                lang_end = 0
                for ch in remaining:
                    if ch.isalnum() or ch in '+.#-_':
                        lang_end += 1
                    else:
                        break
                if lang_end > 0:
                    potential = remaining[:lang_end].lower()
                    if (potential in _COMMON_LANGUAGES
                            or (potential.isalpha() and len(potential) <= 8
                                and potential not in _LANG_BLACKLIST)):
                        lang = potential
                        remaining = remaining[lang_end:]
                    elif len(potential) <= 20 and potential[0].isalpha():
                        lang, remaining = self._detect_streaming_fence_merge(
                            potential, remaining, lang_end,
                        )
                if remaining and remaining[0] == '{':
                    brace_end = remaining.find('}')
                    if brace_end >= 0:
                        attrs = remaining[:brace_end + 1]
                        # Extract title= from within {} attrs if not already set
                        if not title:
                            inner = remaining[1:brace_end]
                            ti = inner.find('title=')
                            if ti >= 0:
                                after_eq = inner[ti + 6:]
                                if after_eq and after_eq[0] in '"\'':
                                    q = after_eq[0]
                                    end = after_eq.find(q, 1)
                                    if end > 0:
                                        title = after_eq[1:end]
                                elif after_eq:
                                    # Unquoted title: take up to next space or end
                                    end_space = after_eq.find(' ')
                                    if end_space > 0:
                                        title = after_eq[:end_space]
                                    else:
                                        title = after_eq
                        remaining = remaining[brace_end + 1:]
                remaining = remaining.strip()
                if remaining.startswith('title='):
                    after = remaining[6:]
                    if after and after[0] in '"\'':
                        q = after[0]
                        end = after.find(q, 1)
                        if end > 0:
                            title = after[1:end]
                            remaining = ''
                remaining = remaining.strip()
                if remaining and not remaining.startswith('title='):
                    extra = remaining
            return {
                'fence_char': first,
                'fence_len': count,
                'lang': lang if lang else None,
                'attrs': attrs,
                'title': title,
                'deferred': not lang,
                'extra': extra,
            }
        except Exception:
            _logger.debug("_try_code_fence_start异常，返回None", exc_info=True)
            return None

    def _start_code_fence(self, info: dict, tokens: list[Token]):
        self._state = _State.CODE_FENCE
        self._block_fence_char = info['fence_char']
        self._block_fence_len = info['fence_len']
        self._block_lang = info.get('lang') or 'text'
        self._block_attrs = info.get('attrs', '')
        self._block_title = info.get('title', '')
        self._block_lines = []
        self._block_nested_fence = 0
        self._auto_close_streak = 0
        lang = self._block_lang
        if lang.lower() in ('mermaid',) or lang.lower().startswith('mermaid'):
            self._state = _State.MERMAID_BLOCK
            tokens.append(Token(TokenType.MERMAID_BLOCK_OPEN, "",
                                {"lang": lang, "attrs": info['attrs']}))
        else:
            tokens.append(Token(TokenType.CODE_FENCE_OPEN, "", {
                "lang": lang, "attrs": info['attrs'], "title": info['title'],
            }))

    def _handle_deferred_fence(self, stripped: str, tokens: list[Token]):
        fence = self._deferred_fence
        self._deferred_fence = None

        if self._bq_active and stripped and stripped[0] == '>':
            s = stripped
            while s.startswith('>'):
                s = _strip_blockquote_prefix(s)
            stripped = s

        if _is_empty_line(stripped):
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
            tokens.append(Token(TokenType.CODE_LINE, ""))
            return
        if stripped and stripped[0] in ('`', '~'):
            fchar, flen, _ = _get_fence_info(stripped)
            if fchar and flen >= fence['fence_len'] and fchar == fence['fence_char']:
                self._start_code_fence(fence, tokens)
                if fence.get('extra'):
                    if self._state == _State.MERMAID_BLOCK:
                        tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                    else:
                        tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
                self._feed_code_fence_line(stripped + '\n', stripped, tokens)
                return
        lang = _get_fence_lang(stripped)
        if lang and lang in _COMMON_LANGUAGES:
            fence['lang'] = lang
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
            return
        if lang and lang in _MERMAID_KEYWORDS:
            fence['lang'] = 'mermaid'
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
            self._block_lines.append(stripped)
            return
        self._start_code_fence(fence, tokens)
        if fence.get('extra'):
            if self._state == _State.MERMAID_BLOCK:
                tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
            else:
                tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
        self._feed_code_fence_line(stripped + '\n', stripped, tokens)

    # ── Setext 标题 / HR ────────────────────────────────

    def _try_setext_or_hr(self, stripped: str, first: str, tokens: list[Token]) -> bool:
        try:
            if len(stripped) < 3:
                return False
            non_space_count = 0
            all_same = True
            for c in stripped:
                if c == ' ':
                    continue
                if c != first:
                    all_same = False
                    break
                non_space_count += 1
            if all_same and non_space_count >= 3:
                if first in ('=', '-') and self._pending_lines:
                    # CommonMark: Setext heading requires exactly one line before the underline.
                    # Multiple lines → treat underline as HR (horizontal rule).
                    if len(self._pending_lines) == 1:
                        last_line = self._pending_lines[0]
                        if last_line and not _is_empty_line(last_line):
                            level = 1 if first == '=' else 2
                            heading_text = last_line
                            self._pending_lines = []
                            self._list_indents.clear()
                            tokens.append(Token(TokenType.HEADING, heading_text,
                                                {"level": level}))
                            return True
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                self._list_indents.clear()
                tokens.append(Token(TokenType.HR))
                return True
            return False
        except Exception:
            _logger.debug("_try_setext_or_hr异常，返回False", exc_info=True)
            return False

    # ── 表格 ─────────────────────────────────────────────

    def _start_table(self, sep_line: str, tokens: list[Token]):
        self._table_alignments = _parse_table_alignments(sep_line)
        num_cols = len(self._table_alignments)
        if self._table_pending_rows:
            header_cells = _parse_table_row(self._table_pending_rows[0])
            if len(header_cells) > num_cols:
                self._table_rows = [header_cells[:num_cols]]
            else:
                self._table_rows = [header_cells]
            for row_str in self._table_pending_rows[1:]:
                row_cells = _parse_table_row(row_str)
                self._table_rows.append((row_cells + [''] * num_cols)[:num_cols])
            self._table_pending_rows.clear()
        else:
            self._table_rows = []
        self._state = _State.TABLE_ACTIVE

    def _emit_table(self, tokens: list[Token]):
        if self._table_rows and self._table_alignments:
            tokens.append(Token(TokenType.TABLE, "", {
                "rows": self._table_rows,
                "alignments": self._table_alignments,
            }))
        self._table_rows = []
        self._table_alignments = []
        self._table_pending_rows.clear()
        self._state = _State.NORMAL

    def _emit_pending_table(self, tokens: list[Token]):
        """将流式缓冲的连续表格行自动发射为 TABLE（≥2行）或 PARAGRAPH（1行）。"""
        if len(self._table_pending_rows) >= 2:
            header = _parse_table_row(self._table_pending_rows[0])
            num_cols = len(header)
            aligns = ['left'] * num_cols
            data_rows = [_parse_table_row(r) for r in self._table_pending_rows[1:]]
            rows = [header] + [
                (r + [''] * num_cols)[:num_cols] for r in data_rows
            ]
            tokens.append(Token(TokenType.TABLE, "", {
                "rows": rows,
                "alignments": aligns,
            }))
        elif len(self._table_pending_rows) == 1:
            tokens.append(Token(TokenType.PARAGRAPH, self._table_pending_rows[0]))
        self._table_pending_rows.clear()

    # ── 引用块 ──────────────────────────────────────────

    def _parse_blockquote(self, stripped: str, tokens: list[Token]):
        if not _is_blockquote_line(stripped):
            return
        if self._bq_in_recursion >= 50:
            self._handle_paragraph_line(stripped + '\n', tokens)
            return
        depth = 0
        in_gt = True
        gt_text = ''
        for ch in stripped:
            if ch == '>':
                if in_gt:
                    depth += 1
                else:
                    gt_text += ch
            elif ch == ' ' and in_gt:
                continue
            else:
                in_gt = False
                gt_text += ch if ch != ' ' or gt_text else ' '
        inner_stripped = gt_text.strip()
        inner_has_gt = inner_stripped.startswith('>')
        lower_text = inner_stripped.lower()
        if inner_stripped.startswith('[') and '!' in inner_stripped[:8]:
            adm_end = inner_stripped.find(']')
            # 宽松检测：`]` 后任意字符都接受，不再要求空格
            if adm_end > 2:
                adm_type = inner_stripped[2:adm_end].upper()
                if adm_type in ('NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION', 'CITE',
                                'INFO', 'SUCCESS', 'QUESTION', 'BUG', 'DANGER'):
                    if self._in_admonition:
                        tokens.append(Token(TokenType.ADMONITION_CLOSE, "",
                                            {"type": self._admonition_type}))
                    if self._bq_active:
                        self._emit_blockquote_close(tokens)
                    self._in_admonition = True
                    self._admonition_type = adm_type
                    adm_text = inner_stripped[adm_end + 1:].strip()
                    tokens.append(Token(TokenType.ADMONITION_OPEN, adm_text,
                                        {"type": adm_type, "depth": depth}))
                    return
        if self._in_admonition:
            tokens.append(Token(TokenType.ADMONITION_LINE, gt_text.strip(),
                                {"depth": depth, "type": self._admonition_type}))
            return
        if not self._bq_active:
            self._flush_paragraph(tokens)
            tokens.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                                {"depth": depth}))
            self._bq_active = True
            self._bq_depth_stack = [depth]
        elif depth > self._bq_depth_stack[-1]:
            # ★ 修复嵌套引用: 深度增加前将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
            old_depth = self._bq_depth_stack[-1]
            if self._pending_lines:
                content = '\n'.join(self._pending_lines)
                self._pending_lines.clear()
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": old_depth}))
            tokens.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                                {"depth": depth}))
            self._bq_depth_stack.append(depth)
        elif depth < self._bq_depth_stack[-1]:
            # ★ 修复嵌套引用: 深度减小前将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
            old_depth = self._bq_depth_stack[-1]
            if self._pending_lines:
                content = '\n'.join(self._pending_lines)
                self._pending_lines.clear()
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": old_depth}))
            while self._bq_depth_stack and depth < self._bq_depth_stack[-1]:
                prev = self._bq_depth_stack.pop()
                tokens.append(Token(TokenType.BLOCKQUOTE_CLOSE, "",
                                    {"depth": prev}))
            if not self._bq_depth_stack:
                self._bq_active = False
        elif inner_has_gt:
            # ★ 修复嵌套引用: 深度增加前将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
            old_depth = self._bq_depth_stack[-1]
            if self._pending_lines:
                content = '\n'.join(self._pending_lines)
                self._pending_lines.clear()
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": old_depth}))
            new_depth = depth + 1
            tokens.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                                {"depth": new_depth}))
            self._bq_depth_stack.append(new_depth)
        self._bq_in_recursion += 1
        try:
            inner_line = gt_text + '\n'
            self._parse_normal_line(inner_line, tokens)
        finally:
            self._bq_in_recursion -= 1

    def _emit_blockquote_close(self, tokens: list[Token]):
        if not self._bq_active:
            return
        if self._bq_in_recursion > 0:
            return
        # ★ 修复嵌套引用: 将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
        if self._pending_lines:
            depth = self._bq_depth_stack[-1] if self._bq_depth_stack else 1
            content = '\n'.join(self._pending_lines)
            self._pending_lines.clear()
            tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                {"depth": depth}))
        while self._bq_depth_stack:
            depth = self._bq_depth_stack.pop()
            tokens.append(Token(TokenType.BLOCKQUOTE_CLOSE, "",
                                {"depth": depth}))
        self._bq_active = False

    # ── 列表缩进 ────────────────────────────────────────

    def _update_list_indent(self, indent: int):
        try:
            if not self._list_indents or indent > self._list_indents[-1]:
                self._list_indents.append(indent)
            elif indent < self._list_indents[-1]:
                while self._list_indents and indent < self._list_indents[-1]:
                    self._list_indents.pop()
                if not self._list_indents or indent != self._list_indents[-1]:
                    self._list_indents.append(indent)
        except Exception:
            _logger.debug("_update_list_indent异常", exc_info=True)

    # ── 段落 ─────────────────────────────────────────────

    @staticmethod
    def _may_be_paragraph_text(first: str, stripped: str) -> bool:
        if first.isalpha():
            return True
        if first.isdigit():
            j = 1
            while j < len(stripped) and stripped[j].isdigit():
                j += 1
            # 支持 "1. " 和 "1) " 两种有序列表标记
            if j + 1 < len(stripped) and stripped[j] in ('.', ')') and stripped[j+1] == ' ':
                return False
            return True
        return False

    def _handle_paragraph_line(self, line: str, tokens: list[Token]):
        self._pending_fn_def = None
        self._def_cont_buffer.clear()
        raw = line.rstrip('\n')
        # ★ 尾随双空格 → 硬换行 (<br>)
        if len(raw) >= 2 and raw[-1] == ' ' and raw[-2] == ' ':
            raw = raw[:-2] + '<br>'
        # ★ 尾随反斜杠 → 硬换行（CommonMark 反斜杠换行语法）
        # 反斜杠换行：行末 \ 变成 <br>，但 \\ 是转义的反斜杠保持为 \
        elif len(raw) >= 1 and raw[-1] == '\\':
            backslash_count = 0
            i = len(raw) - 1
            while i >= 0 and raw[i] == '\\':
                backslash_count += 1
                i -= 1
            if backslash_count % 2 == 1:
                # 奇数个反斜杠：最后一个 \ 消耗为硬换行标记
                raw = raw[:-(backslash_count)] + '\\' * (backslash_count // 2) + '<br>'
        self._pending_lines.append(raw)

    def _flush_paragraph(self, tokens: list[Token]):
        """刷新段落到 Token。如果存在待刷的定义续行内容，先合并追加。"""
        if self._def_cont_buffer:
            cont_text = '\n'.join(self._def_cont_buffer)
            if self._pending_lines:
                self._pending_lines[-1] = self._pending_lines[-1] + '\n' + cont_text
            else:
                self._pending_lines.append(cont_text)
            self._def_cont_buffer.clear()
        if self._pending_lines:
            tokens.append(Token(TokenType.PARAGRAPH,
                                '\n'.join(self._pending_lines)))
            self._pending_lines = []

    # ── 空行 ─────────────────────────────────────────────

    def _handle_empty_line(self, tokens: list[Token]):
        self._pending_fn_def = None
        self._def_cont_buffer.clear()
        if self._in_admonition:
            tokens.append(Token(TokenType.ADMONITION_CLOSE, "",
                                {"type": self._admonition_type}))
            self._in_admonition = False
            self._admonition_type = ''
        self._flush_paragraph(tokens)
        self._emit_blockquote_close(tokens)
        self._list_indents.clear()
        if self._table_pending_rows:
            self._emit_pending_table(tokens)
        if self._state == _State.TABLE_ACTIVE:
            self._emit_table(tokens)
        tokens.append(Token(TokenType.EMPTY_LINE))

    # ── 显示数学块 ──────────────────────────────────────

    def _start_display_math(self, tokens: list[Token]):
        self._state = _State.DISPLAY_MATH_BLOCK
        self._block_lines = []
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _start_math_block(self, tokens: list[Token]):
        self._state = _State.MATH_BLOCK
        self._block_lines = []
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _emit_math_block(self, tokens: list[Token]):
        source = '\n'.join(self._block_lines)
        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                            {"source": source}))
        self._state = _State.NORMAL

    # ── Mermaid ──────────────────────────────────────────

    def _emit_mermaid_block(self, tokens: list[Token]):
        source = ''.join(self._block_lines).strip()
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source))
        self._state = _State.NORMAL

    # ── Details ─────────────────────────────────────────

    def _try_details_open(self, stripped: str) -> bool:
        try:
            lower = stripped.lower()
            return lower.startswith('<details')
        except Exception:
            _logger.debug("_try_details_open异常，返回False", exc_info=True)
            return False

    def _start_details(self, stripped: str, tokens: list[Token]):
        self._state = _State.DETAILS_BLOCK
        self._details_depth = 1
        self._details_summary = ''
        self._details_open_emitted = False
        self._block_lines = []
        lower = stripped.lower()
        sm_start = lower.find('<summary')
        if sm_start >= 0:
            self._handle_details_summary_line(stripped, tokens)
            lower_rest = stripped.lower()
            close_pos = lower_rest.find('</details>', sm_start)
            if close_pos >= 0:
                self._emit_details_close(tokens)

    # ── HTML 块 ──────────────────────────────────────────

    def _try_block_html(self, stripped: str) -> str | None:
        lower = stripped.lower()
        i = 0
        while i < len(lower) and lower[i] in ' \t':
            i += 1
        if i >= len(lower) or lower[i] != '<':
            return None
        i += 1
        if i < len(lower) and lower[i] == '/':
            i += 1
        tag_start = i
        while i < len(lower) and (lower[i].isalnum() or lower[i] in '-:'):
            i += 1
        tag = lower[tag_start:i]
        if tag and tag in _BLOCK_HTML_TAGS:
            return tag
        return None

    def _is_html_close(self, stripped: str, tag: str) -> bool:
        lower = stripped.lower().strip()
        return lower == f'</{tag}>' or lower.startswith(f'</{tag} ')

    def _start_html_block(self, tag: str, tokens: list[Token],
                           line_content: str = ""):
        self._state = _State.HTML_BLOCK
        self._block_html_tag = tag
        tokens.append(Token(TokenType.HTML_BLOCK_OPEN, "",
                            {"tag": tag}))
        if line_content:
            close_tag = f'</{tag}>'
            lower_line = line_content.lower()
            if close_tag in lower_line:
                tag_end = -1
                search_start = lower_line.find(tag)
                if search_start >= 0:
                    tag_end = line_content.find('>', search_start + len(tag))
                if tag_end >= 0:
                    inner = line_content[tag_end + 1:]
                    close_pos = inner.lower().find(close_tag)
                    if close_pos >= 0:
                        content = inner[:close_pos].strip()
                        if content:
                            tokens.append(Token(TokenType.HTML_BLOCK_LINE,
                                                content))
                        tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                            {"tag": tag}))
                        self._state = _State.NORMAL

    # ── Fenced Div ───────────────────────────────────────

    def _handle_fenced_div_open(self, stripped: str, tokens: list[Token]):
        rest = stripped[3:].lstrip()
        div_type = ''
        text = ''
        if rest:
            space = rest.find(' ')
            if space > 0:
                div_type = rest[:space]
                text = rest[space + 1:].strip()
            else:
                div_type = rest
        if not div_type:
            div_type = 'NOTE'
        self._block_div_type = div_type.upper()
        self._block_lines = []
        tokens.append(Token(TokenType.FENCED_DIV_OPEN, text, {"type": self._block_div_type}))
        self._state = _State.FENCED_DIV

    def _feed_fenced_div_line(self, line: str, stripped: str, tokens: list[Token]):
        if not stripped or _is_empty_line(line):
            # ★ 空行在 fenced div 内：不关闭 div，发射空行标记作为一条空白线
            tokens.append(Token(TokenType.FENCED_DIV_LINE, "", {"type": self._block_div_type, "empty": True}))
            return
        if stripped.strip() == ':::':
            tokens.append(Token(TokenType.FENCED_DIV_CLOSE, "", {"type": self._block_div_type}))
            self._state = _State.NORMAL
            return
        tokens.append(Token(TokenType.FENCED_DIV_LINE, stripped, {"type": self._block_div_type}))

    # ── 缩进代码块 ─────────────────────────────────────

    def _start_indented_code(self, line: str, tokens: list[Token]):
        self._state = _State.INDENTED_CODE
        tokens.append(Token(TokenType.CODE_FENCE_OPEN, "", {
            "lang": "text", "indented": True, "attrs": "",
        }))
        content = line[4:] if line[:4] == '    ' else line[1:]
        tokens.append(Token(TokenType.CODE_LINE, content.rstrip('\n')))

    # ── 缩写定义 ───────────────────────────────────────

    def _handle_abbreviation(self, stripped: str) -> bool:
        if not stripped.startswith('*['):
            return False
        if ']:' not in stripped:
            return False
        close_bracket = stripped.find(']:')
        if close_bracket <= 2:
            return False
        abbr = stripped[2:close_bracket].strip().upper()
        full_text = stripped[close_bracket + 2:].strip()
        if not abbr or not full_text:
            return False
        self._ctx.abbr_map[abbr] = full_text
        return True

    # ── 列表项检测 ─────────────────────────────────────

    @staticmethod
    def _parse_list_item_checkbox(text: str) -> tuple[str, bool, bool, bool]:
        """检测列表项是否为任务列表（checkbox），保留 checkbox 前缀在文本中。

        is_todo() 在下游渲染层通过扫描文本开头的 [ ]/[x]/[-] 来渲染勾选框，
        因此必须保留 checkbox 标记在 content 中。

        Returns:
            (text, is_todo, is_checked, is_cancelled)
            — text 保留 '[x] ' / '[ ] ' / '[-] ' 前缀
        """
        t = text.strip()
        if len(t) >= 4 and t[0] == '[' and t[2] == ']':
            if t[1] in ' xX':
                return text, True, t[1] in 'xX', False
            if t[1] == '-':
                return text, True, False, True
        return text, False, False, False

    def _try_ul_item(self, stripped: str, line: str) -> dict | None:
        try:
            indent = 0
            for ch in line:
                if ch in ' \t':
                    indent += 1
                else:
                    break
            content = _strip_left(stripped)
            if len(content) >= 2 and content[0] in ('-', '*', '+') and content[1] == ' ':
                text = _rstrip_line(content[2:])
                text, todo, checked, cancelled = self._parse_list_item_checkbox(text)
                return {
                    'indent': indent,
                    'text': text,
                    'todo': todo,
                    'checked': checked,
                    'cancelled': cancelled,
                }
            return None
        except Exception:
            _logger.debug("_try_ul_item异常，返回None", exc_info=True)
            return None

    def _try_ol_item(self, stripped: str, line: str) -> dict | None:
        try:
            indent = 0
            for ch in line:
                if ch in ' \t':
                    indent += 1
                else:
                    break
            content = _strip_left(stripped)
            i = 0
            num_str = ''
            while i < len(content) and content[i].isdigit():
                num_str += content[i]
                i += 1
            if not num_str:
                return None
            # 支持 "1. " 和 "1) " 两种有序列表标记
            if i < len(content) and content[i] in ".')":
                delimiter = content[i]
                i += 1
                if i < len(content) and content[i] == ' ':
                    text = _rstrip_line(content[i + 1:])
                    number = int(num_str)
                    text, todo, checked, cancelled = self._parse_list_item_checkbox(text)
                    # marker_width：含缩进后的标记宽度（"1. "=3, "1) "=3, "12. "=4）
                    marker_width = len(num_str) + 2  # digit + delimiter + ' '
                    return {
                        'indent': indent,
                        'number': number,
                        'text': text,
                        'start': number,
                        'todo': todo,
                        'checked': checked,
                        'cancelled': cancelled,
                        'delimiter': delimiter,
                        'marker_width': marker_width,
                    }
            return None
        except Exception:
            _logger.debug("_try_ol_item异常，返回None", exc_info=True)
            return None

    # ── 定义列表 ───────────────────────────────────────

    def _try_definition(self, stripped: str) -> str | None:
        try:
            i = 0
            while i < len(stripped) and stripped[i] in ' \t':
                i += 1
            # ★ 修复: 要求 : 后紧跟空格/制表符，排除 :emoji: 等模式
            if (i < len(stripped) and stripped[i] == ':'
                    and i + 1 < len(stripped) and stripped[i + 1] in ' \t'):
                rest = stripped[i + 1:].strip()
                return rest if rest else None
            return None
        except Exception:
            _logger.debug("_try_definition异常，返回None", exc_info=True)
            return None

    # ── 脚注定义 ───────────────────────────────────────

    def _try_fn_def(self, stripped: str) -> dict | None:
        try:
            if stripped[0] != '[':
                return None
            close = stripped.find(']:')
            if close <= 2 or close > 100:
                return None
            ref_id = stripped[1:close]
            if not ref_id.startswith('^'):
                return None
            ref_id = ref_id[1:]
            content = stripped[close + 2:].strip()
            if ref_id and content:
                return {'ref_id': ref_id, 'content': content}
            return None
        except Exception:
            _logger.debug("_try_fn_def异常，返回None", exc_info=True)
            return None

    def _handle_fn_def(self, fn_info: dict, tokens: list[Token]):
        ref_id = fn_info['ref_id']
        content = fn_info['content']
        self._ctx.fn_map[ref_id] = content
        if ref_id not in self._ctx.fn_order:
            self._ctx.fn_order.append(ref_id)
        self._pending_fn_def = ref_id

    # ── 参考链接 ───────────────────────────────────────

    def _try_ref_link(self, stripped: str) -> bool:
        try:
            if stripped[0] != '[':
                return False
            close = stripped.find(']:')
            if close <= 1:
                return False
            ref_id = stripped[1:close]
            if '^' in ref_id:
                return False
            rest = stripped[close + 2:].strip()
            url_end = self._find_url_end(rest)
            url = rest[:url_end]
            title = ''
            after_url = rest[url_end:].strip()
            if after_url and after_url[0] in '"\'':
                quote = after_url[0]
                end = after_url.find(quote, 1)
                if end > 0:
                    title = after_url[1:end]
            if url and ref_id:
                self._ctx.ref_map[ref_id] = (url, title)
            return True
        except Exception:
            _logger.debug("_try_ref_link异常，返回False", exc_info=True)
            return False

    # ── 块刷出 ─────────────────────────────────────────

    _FLUSH_DISPATCH: dict[_State, str] = {
        _State.CODE_FENCE: '_flush_code_fence',
        _State.MATH_BLOCK: '_flush_math_block',
        _State.DISPLAY_MATH_BLOCK: '_flush_math_block',
        _State.MERMAID_BLOCK: '_flush_mermaid_block',
        _State.DETAILS_BLOCK: '_flush_details_block',
        _State.INDENTED_CODE: '_flush_indented_code',
        _State.FENCED_DIV: '_flush_fenced_div',
        _State.HTML_BLOCK: '_flush_html_block',
        _State.TABLE_ACTIVE: '_flush_table',
    }

    def _flush_code_fence(self, tokens: list[Token]):
        if self._block_lines:
            for l in self._block_lines:
                tokens.append(Token(TokenType.CODE_LINE, l))
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                            {"lang": self._block_lang}))

    def _flush_math_block(self, tokens: list[Token]):
        source = '\n'.join(self._block_lines)
        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                            {"source": source}))

    def _flush_mermaid_block(self, tokens: list[Token]):
        source = ''.join(self._block_lines).strip()
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source))

    def _flush_details_block(self, tokens: list[Token]):
        self._emit_details_close(tokens)

    def _flush_indented_code(self, tokens: list[Token]):
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
            "lang": "text", "indented": True,
        }))

    def _flush_fenced_div(self, tokens: list[Token]):
        tokens.append(Token(TokenType.FENCED_DIV_CLOSE, "", {"type": self._block_div_type}))

    def _flush_html_block(self, tokens: list[Token]):
        tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                            {"tag": self._block_html_tag}))

    def _flush_table(self, tokens: list[Token]):
        if self._table_rows and self._table_alignments:
            self._emit_table(tokens)
        elif self._table_pending_rows:
            self._emit_pending_table(tokens)

    def _flush_block(self, tokens: list[Token]):
        """刷出当前非 NORMAL 状态的块（dispatch 模式）。"""
        handler_name = self._FLUSH_DISPATCH.get(self._state)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            handler(tokens)
        self._state = _State.NORMAL
