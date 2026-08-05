"""_BlockParserLineMixin — RegexFreeBlockParser 的 Line 职责分组。

从 _block_parser.py 拆分（2026-08-06 架构整理）。
与 RegexFreeBlockParser 主体共享实例状态（self._state / self._buffer 等），
由主体类多继承组合，不直接实例化。
"""
from __future__ import annotations

import logging

from .types import Token, TokenType
from ._block_parser_common import _State
from ._block_helpers import (
    _get_blockquote_text,
    _is_blockquote_line,
    _is_code_fence_line,
    _is_empty_line,
    _is_only_chars,
    _rstrip_line,
    _strip_blockquote_prefix,
    _strip_left,
)
from ._table_utils import (
    _is_table_row,
    _is_table_separator,
    _parse_table_row,
)

_logger = logging.getLogger(__name__)

class _BlockParserLineMixin:
    """见模块 docstring。"""

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
