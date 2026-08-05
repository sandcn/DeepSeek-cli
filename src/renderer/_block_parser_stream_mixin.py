"""_BlockParserStreamMixin — RegexFreeBlockParser 的 Stream 职责分组。

从 _block_parser.py 拆分（2026-08-06 架构整理）。
与 RegexFreeBlockParser 主体共享实例状态（self._state / self._buffer 等），
由主体类多继承组合，不直接实例化。
"""
from __future__ import annotations

import logging

from .types import Token, TokenType
from ._block_parser_common import _State
from ._block_helpers import (
    _strip_left,
)

_logger = logging.getLogger(__name__)

class _BlockParserStreamMixin:
    """见模块 docstring。"""

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

    def _flush_block(self, tokens: list[Token]):
        """刷出当前非 NORMAL 状态的块（dispatch 模式）。"""
        handler_name = self._FLUSH_DISPATCH.get(self._state)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            handler(tokens)
        self._state = _State.NORMAL

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
