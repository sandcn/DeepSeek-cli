"""_BlockParserBlockMixin — RegexFreeBlockParser 的 Block 职责分组。

从 _block_parser.py 拆分（2026-08-06 架构整理）。
与 RegexFreeBlockParser 主体共享实例状态（self._state / self._buffer 等），
由主体类多继承组合，不直接实例化。
"""
from __future__ import annotations

import logging

from .types import Token, TokenType
from ._block_parser_common import _State
from ._block_helpers import (
    _is_blockquote_line,
    _is_empty_line,
    _rstrip_line,
    _rstrip_trailing_hashes,
    _strip_left,
)
from ._table_utils import (
    _parse_table_alignments,
    _parse_table_row,
)

_logger = logging.getLogger(__name__)

class _BlockParserBlockMixin:
    """见模块 docstring。"""

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

    def _flush_table(self, tokens: list[Token]):
        if self._table_rows and self._table_alignments:
            self._emit_table(tokens)
        elif self._table_pending_rows:
            self._emit_pending_table(tokens)

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
