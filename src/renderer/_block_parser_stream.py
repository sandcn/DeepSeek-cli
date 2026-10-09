"""_block_parser 流式/块级状态处理 mixin — 从 _block_parser.py 拆分。

承载非 NORMAL 状态下的行处理（代码 fence / 数学 / Mermaid / Details /
缩进代码 / HTML / 表格活动）与块级语法尝试（标题 / 代码 fence 起始 /
Setext / 延迟 fence）方法群。``RegexFreeBlockParser`` 继承本 mixin。

拆分目的：降低 ``_block_parser.py`` 单文件体量，保持行为与调用关系不变
（方法间通过 ``self`` 分派，跨 mixin 正常）。
"""

from __future__ import annotations

import logging

from ._block_parser_state import (
    _State, _MERMAID_KEYWORDS, _SETEXT_HR_CHARS, _HTML_HEADING_LEVELS,
)
from ._utils import _COMMON_LANGUAGES, _get_fence_info
from .types import Token, TokenType
from ._table_utils import _is_table_data_row, _parse_table_row, _normalize_table_cells
from ._block_helpers import (
    _is_empty_line, _strip_left,
    _is_code_fence_line, _strip_blockquote_prefix,
    _get_fence_lang, _rstrip_trailing_hashes,
    _LANG_BLACKLIST,
)

_logger = logging.getLogger(__name__)

# 自动关闭 fence 启发式阈值（text/无语言代码块「围栏漏闭合」容错）
_AUTO_CLOSE_MIN_STREAK = 6
"""连续呈现 Markdown 块级结构的行数阈值。"""

_AUTO_CLOSE_MIN_KINDS = 2
"""连续匹配行所需的**结构类型**种数（标题/分隔线/表格分隔）——要求结构
多样，避免代码块内连续同类型行（如多行 ``# 注释``）被误判为块外 Markdown。"""

#: 代码围栏 info 中「裸属性」关键字（无 ``key=`` 时也认作属性，不当作代码首行）
_FENCE_ATTR_KEYS: frozenset = frozenset({
    "hl_lines", "hl-lines", "hllines", "linenos", "numberlines",
    "line-numbers", "line_numbers", "linenostart", "linestart",
    "first-line", "firstline", "linenostep", "linestep",
    "emphasize-lines", "emphasize_lines",
})


def _extract_title_from_attr_text(text: str) -> str:
    """从属性文本提取 ``title="..."`` / ``title=xxx``（无则空串）。"""
    if not text:
        return ""
    ti = text.lower().find("title=")
    if ti < 0:
        return ""
    after = text[ti + 6:]
    if not after:
        return ""
    if after[0] in '"\'':
        q = after[0]
        end = after.find(q, 1)
        return after[1:end] if end > 0 else ""
    sp = after.find(" ")
    return after[:sp] if sp > 0 else after


def _looks_like_fence_attrs(text: str) -> bool:
    """剩余 info 文本是否为「裸属性序列」（``hl_lines="1,3" linenos``）。

    每个空白分隔的片段须为 ``name=value``（``name`` 仅字母/数字/``-``/``_``）
    或已知属性关键字（``linenos`` 等）。全部片段满足才认定为属性——避免把
    ```python print(1) 之类的代码首行误当属性吞掉（那会静默丢失代码内容）。
    """
    parts = text.split()
    if not parts:
        return False
    for part in parts:
        low = part.lower()
        if "=" in part:
            name = part.split("=", 1)[0].strip()
            if not name or not all(c.isalnum() or c in "-_" for c in name):
                return False
            continue
        if low in _FENCE_ATTR_KEYS:
            continue
        return False
    return True


def _html_tag_delta(line: str, tag: str) -> int:
    """行内某 HTML 标签的开合净变化数（``<tag`` 数 - ``</tag>`` 数）。

    闭合标签按前缀识别（``</ul>``）；开标签要求标签名边界（``<ulx`` 不计入）。
    用于 HTML 块内**嵌套同名标签**的深度跟踪——``<ul><li>…<ul>…</ul></li></ul>``
    内层 ``</ul>`` 不再误结束整个块。
    """
    low = line.lower()
    opens = 0
    closes = 0
    i = 0
    n = len(low)
    while True:
        j = low.find('<', i)
        if j < 0:
            break
        i = j + 1
        if low.startswith('/' + tag, i):
            after = i + 1 + len(tag)
            if after >= n or not (low[after].isalnum() or low[after] in '-:'):
                closes += 1
            i = after
            continue
        if low.startswith(tag, i):
            after = i + len(tag)
            if after >= n or not (low[after].isalnum() or low[after] in '-:'):
                opens += 1
            i = after
    return opens - closes


class _BlockParserStreamMixin:
    """非 NORMAL 状态行处理 + 块级语法尝试方法群（详见模块 docstring）。"""

    def _feed_block_line(self, line: str, tokens: list[Token]):
        """处理非 NORMAL 状态下的行。"""
        stripped = _strip_left(line).rstrip('\n')

        if self._bq_active and stripped and stripped[0] == '>':
            s = stripped
            while s.startswith('>'):
                s = _strip_blockquote_prefix(s)
            line = s + '\n'
            stripped = s

        if self._state == _State.FRONT_MATTER:
            try:
                self._feed_front_matter_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Front Matter块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.CODE_FENCE:
            try:
                self._feed_code_fence_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Code fence块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.MATH_BLOCK:
            try:
                if self._math_env_end is not None:
                    # KaTeX auto-render 显示环境：以 ``\end{env}`` 结束；
                    # 源码整段保留（含 ``\begin`` / ``\end`` 行）
                    self._block_lines.append(line.rstrip('\n'))
                    if stripped.startswith(self._math_env_end):
                        self._emit_math_block(tokens)
                elif stripped == '$$':
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
                if _is_code_fence_line(_strip_left(stripped)):
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

        elif self._state == _State.ADMONITION_BLOCK:
            if _is_empty_line(stripped):
                # ★ 空行保留为正文空行（不关闭）：fenced 告示（``!!! type`` /
                #   ``??? type``）正文可持续到**首个非缩进行**，空行分隔的多段
                #   正文属同一告示（与 ``:::`` fenced div 同语义）。修复前空行
                #   即关闭告示，紧随的缩进行被解析为「缩进代码块」——多段正文
                #   的后续段落泄漏成代码块（内容仍在但渲染形态错误）。
                self._block_lines.append("")
                tokens.append(Token(TokenType.ADMONITION_LINE, "", {
                    "depth": 1, "type": self._admonition_type,
                }))
                return
            if line[:4] == '    ' or (line and line[0] == '\t'):
                content = line[4:] if line[:4] == '    ' else line[1:]
                content = content.rstrip('\n')
                self._block_lines.append(content)
                tokens.append(Token(TokenType.ADMONITION_LINE, content, {
                    "depth": 1, "type": self._admonition_type,
                }))
                return
            # 非缩进行 → 告示块结束，当前行按 NORMAL 重新解析
            self._emit_admonition_block_close(tokens)
            self._parse_normal_line(line, tokens)

        elif self._state == _State.HTML_BLOCK:
            # 嵌套同名标签深度跟踪：内层 ``</ul>``（``<ul><li>…<ul>…</ul>``）
            # 不再误判为整个 HTML 块的结束。
            self._html_depth += _html_tag_delta(stripped, self._block_html_tag)
            if self._block_html_tag == 'p':
                self._feed_html_paragraph_line(stripped, tokens)
                return
            heading_level = _HTML_HEADING_LEVELS.get(self._block_html_tag)
            if heading_level is not None:
                self._feed_html_heading_line(stripped, tokens, heading_level)
                return
            if self._block_html_tag == 'pre':
                # 代码块保留原始行（缩进有意义，不能传 stripped）
                self._feed_html_pre_line(line, tokens)
                return
            if self._block_html_tag == 'blockquote':
                self._feed_html_blockquote_line(stripped, tokens)
                return
            if self._html_depth <= 0:
                self._collect_html_line(line)
                if not self._emit_html_structured(tokens):
                    for raw in self._html_lines:
                        tokens.append(Token(TokenType.HTML_BLOCK_LINE, raw,
                                            {"tag": self._block_html_tag}))
                    self._html_lines = []
                    tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                        {"tag": self._block_html_tag}))
                self._state = _State.NORMAL
            elif self._collect_html_line(line):
                pass
            else:
                tokens.append(Token(TokenType.HTML_BLOCK_LINE,
                                    line.rstrip('\n'),
                                    {"tag": self._block_html_tag}))

        elif self._state == _State.TABLE_ACTIVE:
            try:
                # ★ 修复（表格数据行丢失）：数据行判定改用「表格已建立」的宽松
                #   规则（``_is_table_data_row``，≥1 pipe 且列数不超过表头）——
                #   与表头建立时的判定标准一致。``_is_table_row`` 对无前导 pipe
                #   的行要求 ≥2 pipe（防普通文本误判），导致 ``1 | 2`` 这类
                #   数据行被判为非表格 → 表格只剩表头、数据行退化成段落。
                header_cols = len(self._table_rows[0]) if self._table_rows else None
                check = _strip_blockquote_prefix(stripped)
                if check != stripped:
                    if _is_table_data_row(check, header_cols):
                        self._table_rows.append(_normalize_table_cells(
                            _parse_table_row(check), header_cols))
                    else:
                        self._emit_table(tokens)
                        self._state = _State.NORMAL
                        self._parse_normal_line(line, tokens)
                elif _is_table_data_row(stripped, header_cols):
                    self._table_rows.append(_normalize_table_cells(
                        _parse_table_row(stripped), header_cols))
                else:
                    self._emit_table(tokens)
                    self._state = _State.NORMAL
                    self._parse_normal_line(line, tokens)
            except Exception:
                _logger.debug("表格活动状态行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

    # ── 代码 fence 块内 ──────────────────────────────────

    def _reset_auto_close(self) -> None:
        """重置自动关闭 fence 的连续匹配状态（计数器 + 结构类型集合）。"""
        self._auto_close_streak = 0
        self._auto_close_kinds.clear()

    @staticmethod
    def _auto_close_line_kind(stripped: str) -> str | None:
        """判断行是否呈现 Markdown 块级结构，返回结构类型名（否则 None）。

        仅服务 ``_should_auto_close_fence`` 的「围栏漏闭合」启发式；类型名
        用于统计结构多样性。
        """
        if len(stripped) >= 3 and stripped[0] == '#':
            level = 0
            for ch in stripped:
                if ch == '#':
                    level += 1
                else:
                    break
            if 2 <= level <= 6 and level < len(stripped) and stripped[level] == ' ':
                return 'heading'
        if len(stripped) >= 3:
            first = stripped[0]
            if first in _SETEXT_HR_CHARS:
                only = True
                for ch in stripped:
                    if ch not in (' ', first):
                        only = False
                        break
                if only and len(stripped.replace(' ', '')) >= 3:
                    return 'hr'
        if stripped[0] == '|' and stripped.count('|') >= 2:
            # 只有包含分隔行模式（:- 等）才是真正的表格行，避免对含 pipe 的普通内容误触发
            if ':-' in stripped or '-:' in stripped or ':-:' in stripped:
                return 'table'
        return None

    def _should_auto_close_fence(self, stripped: str, line: str = '') -> bool:
        if not stripped:
            self._reset_auto_close()
            return False
        if self._block_lang.lower() not in ('text', 'txt', 'plain', ''):
            self._reset_auto_close()
            return False
        if self._code_content_seen:
            # ★ 加固（降低误截断）：块内已出现普通内容行（真实代码 / 缩进 /
            #   非结构文本）→ 视为「含实际内容的代码块」而非「纯 Markdown 块
            #   漏写闭合围栏」，放弃自动关闭。真实漏闭合块通常整块为 Markdown
            #   结构行（无普通内容行），故容错保留；而「代码 + 尾部 Markdown
            #   示例」块不再被误截断。
            self._reset_auto_close()
            return False
        if line and (line[0] in ' \t'):
            self._code_content_seen = True
            self._reset_auto_close()
            return False
        if stripped[0] not in '#-*_|':
            self._code_content_seen = True
            self._reset_auto_close()
            return False
        kind = self._auto_close_line_kind(stripped)
        if kind is None:
            self._code_content_seen = True
            self._reset_auto_close()
            return False
        self._auto_close_kinds.add(kind)
        self._auto_close_streak += 1
        # ★ 加固（降低误判）：连续匹配行数达标 **且** 结构类型多样（≥2 种）。
        #   修复前仅计连续行数——代码块内连续 5 行同类型 Markdown 形态
        #   （如多行 ``# 注释``、多行 ``---``）即被误判为「围栏漏闭合」，
        #   合法代码内容被截断为块外 Markdown。多样性门槛使纯注释/纯分隔行
        #   序列不再触发。
        return (self._auto_close_streak >= _AUTO_CLOSE_MIN_STREAK
                and len(self._auto_close_kinds) >= _AUTO_CLOSE_MIN_KINDS)

    def _feed_code_fence_line(self, line: str, stripped: str, tokens: list[Token]):
        # 关闭围栏允许最多 3 空格缩进（CommonMark）——引用剥离（``>   ``` ``）
        # 或列表缩进后可能残留前导空格，判定前先剥离行首空白；内容行按原样
        # （保留相对缩进）写入。
        check = _strip_left(stripped)
        if check and not (check[0] in '#-*_|' and len(check) >= 3):
            self._reset_auto_close()

        if self._auto_close_streak > 0 and check and check[0] == '|':
            cells = [c.strip() for c in check.strip('|').split('|')]
            if any(c.isalnum() for c in cells if c):
                self._reset_auto_close()

        if self._should_auto_close_fence(check, line):
            tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                                {"lang": self._block_lang, "indented": False}))
            self._state = _State.NORMAL
            self._parse_normal_line(line, tokens)
            return
        fchar, flen, _ = _get_fence_info(check)
        if not fchar:
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        if fchar != self._block_fence_char:
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        if flen < self._block_fence_len:
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        close_lang = _get_fence_lang(check[flen:].strip())
        is_markdown = self._block_lang.lower() in ('markdown', 'md')
        if close_lang and is_markdown:
            self._block_nested_fence += 1
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        if not close_lang and is_markdown and self._block_nested_fence > 0:
            self._block_nested_fence -= 1
            self._emit_code_line(line.rstrip('\n'), tokens)
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
                                {"summary": summary,
                                 "open": self._details_open}))
            self._details_open_emitted = True
        if rest:
            self._block_lines.append(rest)

    def _emit_details_close(self, tokens: list[Token]):
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": self._details_summary,
                                 "open": self._details_open}))
            self._details_open_emitted = True
        # 正文以完整 Markdown 语义递归解析（列表/代码块/引用/强调…），
        # 结果挂在 CLOSE 的 meta["body_tokens"]（渲染层整体渲染并缩进）；
        # 同时保留原始行 body_lines（Rich 路径与流式预览逐行渲染用）。
        body_lines = list(self._block_lines)
        # 去尾部空行（空行分隔的多段正文保留；尾部空行是块结束的产物）
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        body_tokens = self._parse_sub_blocks(body_lines)
        self._block_lines = []
        tokens.append(Token(TokenType.DETAILS_CLOSE, "", {
            "body_tokens": body_tokens,
            "body_lines": body_lines,
            "open": self._details_open,
        }))
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
            # ★ 尾随空行不计入代码块（CommonMark）：空行先暂存，仅当后续
            #   仍有缩进内容时才作为块内空行补发；块以空行结束时丢弃——
            #   修复前空行立即发射，文档 ``    代码\n\n正文`` 的代码块渲染
            #   出多余的空行（``\n\n`` 尾随空白行）。
            self._indented_code_pending_blanks += 1
            return
        pad = self._indented_code_pad
        if line[:pad] == ' ' * pad:
            content = line[pad:]
        elif pad == 4 and line[:4] == '    ':
            content = line[4:]
        elif pad == 4 and line and line[0] == '\t':
            content = line[1:]
        else:
            # 缩进不足 → 代码块结束（列表项上下文中的 ``pad`` 更大，行首缩进
            # 不足以归入列表项内代码块时同样结束）
            self._indented_code_pending_blanks = 0
            tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                "lang": "text", "indented": True,
            }))
            self._state = _State.NORMAL
            self._parse_normal_line(line, tokens)
            return
        while self._indented_code_pending_blanks > 0:
            self._emit_code_line("", tokens)
            self._indented_code_pending_blanks -= 1
        self._emit_code_line(content.rstrip('\n'), tokens)

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
                # ★ 修复（info 属性丢失）：lang 名后的剩余文本可能以前导空格
                #   开头（`` ```python {1,3-5} ``）——修复前未 strip 即检查
                #   ``remaining[0] == '{'``，大括号属性（行高亮 / 行号）整段
                #   被当成 extra 落入代码内容（``{1,3-5}`` 显示为第一行）。
                # ★ 扩展（多属性组）：``{1,3}{linenos}{title="x"}`` 连续大括号
                #   组全部并入 attrs——修复前只取首组，后续组（``{linenos}``）
                #   落入代码内容成为首行。
                remaining = remaining.strip()
                while remaining.startswith('{'):
                    brace_end = remaining.find('}')
                    if brace_end < 0:
                        break
                    inner_attrs = remaining[1:brace_end]
                    attrs += remaining[:brace_end + 1]
                    if not title:
                        title = _extract_title_from_attr_text(inner_attrs)
                    remaining = remaining[brace_end + 1:].strip()
                # 裸属性（``hl_lines="1,3" linenos``）：整段由属性片段构成时
                # 并入 attrs；否则保留为代码首行 extra（兼容
                # ```python print(1) 式写法——不得吞掉真实代码文本）。
                if remaining and _looks_like_fence_attrs(remaining):
                    if not title:
                        title = _extract_title_from_attr_text(remaining)
                    attrs += (' ' if attrs else '') + remaining
                    remaining = ''
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
        self._preview_code_lines = []
        self._preview_code_dropped = 0
        self._block_nested_fence = 0
        self._reset_auto_close()
        self._code_content_seen = False
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
                    self._emit_code_line(fence['extra'], tokens)
            self._emit_code_line("", tokens)
            return
        if stripped and stripped[0] in ('`', '~'):
            fchar, flen, _ = _get_fence_info(stripped)
            if fchar and flen >= fence['fence_len'] and fchar == fence['fence_char']:
                self._start_code_fence(fence, tokens)
                if fence.get('extra'):
                    if self._state == _State.MERMAID_BLOCK:
                        tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                    else:
                        self._emit_code_line(fence['extra'], tokens)
                self._feed_code_fence_line(stripped + '\n', stripped, tokens)
                return
        lang = _get_fence_lang(stripped)
        # ★ 修复（内容丢失）：仅当整行就是语言标识（无附加内容）时才把该行
        #   当作语言名——修复前 ``_get_fence_lang`` 只取前缀（``plain text``
        #   → ``plain``），整行被当语言行吞掉，首行代码内容丢失。
        is_pure_lang = bool(lang) and lang == stripped
        if is_pure_lang and lang in _COMMON_LANGUAGES:
            fence['lang'] = lang
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    self._emit_code_line(fence['extra'], tokens)
            return
        if is_pure_lang and lang in _MERMAID_KEYWORDS:
            fence['lang'] = 'mermaid'
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    self._emit_code_line(fence['extra'], tokens)
            self._block_lines.append(stripped)
            return
        self._start_code_fence(fence, tokens)
        if fence.get('extra'):
            if self._state == _State.MERMAID_BLOCK:
                tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
            else:
                tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
        self._feed_code_fence_line(stripped + '\n', stripped, tokens)


__all__ = ["_BlockParserStreamMixin"]
