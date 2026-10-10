"""_inline_formatting — _InlineParser 格式标记解析 Mixin。

包含粗体、斜体、粗斜体、删除线、高亮、下标、上标的相关方法。
"""

from __future__ import annotations

import logging
import string
import unicodedata

from .inline_nodes import (
    InlineNode, TextNode,
    BoldNode, ItalicNode, BoldItalicNode,
    StrikethroughNode, HighlightNode,
    SubscriptNode, SuperscriptNode,
    UnderlineNode,
    SpoilerNode,
    CriticAdditionNode, CriticDeletionNode, CriticHighlightNode,
    CriticSubstitutionNode, CriticCommentNode,
    SmallTextNode, BigTextNode, ColorTextNode,
    WikiLinkNode, InlineCommentNode,
    InlineFootnoteNode,
    render_inline_to_text,
)

_logger = logging.getLogger(__name__)

#: ASCII 标点（CommonMark 的「标点」判定在 ASCII 区间的快速路径）
_ASCII_PUNCT: frozenset[str] = frozenset(string.punctuation)


def _is_punct_char(ch: str) -> bool:
    """字符是否属于 CommonMark 的「标点」类（Unicode P* 或 S*）。

    ``*``/``_`` 的左右定界符规则需要判断相邻字符是否为标点（「内侧空白 +
    外侧标点」的定界符组合不能开启/闭合强调）。快速路径：空字符与字母数字
    （含 CJK，``str.isalnum`` 为 C 级判定）直接判否——它们占据真实文本的绝大
    多数，省去 ``unicodedata`` 查表；ASCII 标点走 ``string.punctuation``；
    其余非 ASCII 字符才用 ``unicodedata.category``。
    """
    if not ch:
        return False
    if ch.isalnum():
        return False
    if ch.isascii():
        return ch in _ASCII_PUNCT
    cat = unicodedata.category(ch)
    return cat[0] == 'P' or cat[0] == 'S'


class InlineFormattingMixin:
    """_InlineParser 格式标记解析 Mixin。

    提供以下方法：
      _try_bold_italic()
      _try_bold()
      _try_italic()
      _parse_italic_content()
      _try_strikethrough()
      _try_highlight()
      _try_subscript()
      _try_superscript()
      _try_inline_footnote()
    """

    def _delim_flanking(self, pos: int, length: int) -> tuple[bool, bool]:
        """定界符 run 的 ``(left_flanking, right_flanking)``（CommonMark）。

        - left-flanking：其后字符不是空白，且（其后字符不是标点 或 其前字符是
          空白或标点）；
        - right-flanking：其前字符不是空白，且（其前字符不是标点 或 其后字符是
          空白或标点）。

        ★ 2026-10-10（第十批）：``after`` 取**整个定界符 run 之后**的字符，
        而非调用方尝试消耗的长度之后——CommonMark 的 flanking 判定基于完整
        run（同一 run 内的字符互为标点，用截断长度永远得到「后字符是标点 →
        可开」）。修复前 ``foo *****``（run 尾部为行尾空白）被误判为可开、
        整段标记被剥离（``foo *``）；``**** is not…`` 丢字符（``** is…``）。
        """
        text, n = self._text, self._n
        run_len = length
        # 需要按**完整 run** 判定的定界符字符：``*``/``_``（强调）、``=``
        # （高亮 ``==x==``）、``+``（下划线 ``++x++``）、``~``（删除线
        # ``~~x~~``）、``|``（剧透 ``||x||``）——这些语法与强调同族，flanking
        # 判定同样基于完整 run（``===b`` 的 ``==`` 不是定界符）。
        if 0 <= pos < n and text[pos] in '*_=+~|':
            ch = text[pos]
            tail = pos + length
            # 快速路径：``length`` 处已是 run 之外（常见——调用方按完整 run
            # 长度尝试）→ 无需扫描；仅当其后仍是同字符时才向后扩展。
            if tail < n and text[tail] == ch:
                i = tail
                while i < n and text[i] == ch:
                    i += 1
                run_len = i - pos
        before = text[pos - 1] if pos > 0 else ''
        after_pos = pos + run_len
        after = text[after_pos] if after_pos < n else ''
        left = (after != '' and not after.isspace()
                and (not _is_punct_char(after)
                     or before == '' or before.isspace()
                     or _is_punct_char(before)))
        right = (before != '' and not before.isspace()
                 and (not _is_punct_char(before)
                      or after == '' or after.isspace()
                      or _is_punct_char(after)))
        return left, right

    def _delim_can_open(self, pos: int, length: int, ch: str = '*') -> bool:
        """``pos`` 处 ``length`` 个强调定界符是否可作为**左定界符**（开启强调）。

        CommonMark：``*`` 的左定界符 = left-flanking；``_`` 额外要求「不是
        right-flanking，或其前字符是标点」（词内下划线不能开启强调）。

        修复前无此判定：``a * foo bar*`` / ``** foo bar**`` / ``a_"foo"_``
        等「内侧空白 + 外侧普通字符」的组合被误判为强调，标记被剥离、正文
        形态被破坏。
        """
        left, right = self._delim_flanking(pos, length)
        if not left:
            return False
        if ch == '_' and right:
            before = self._text[pos - 1] if pos > 0 else ''
            if not _is_punct_char(before):
                return False
        return True

    def _delim_can_close(self, pos: int, length: int, ch: str = '*') -> bool:
        """``pos`` 处 ``length`` 个强调定界符是否可作为**右定界符**（闭合强调）。

        CommonMark：``*`` 的右定界符 = right-flanking；``_`` 额外要求「不是
        left-flanking，或其后续字符是标点」（词内下划线不能闭合强调）。

        修复前无此判定：``**foo bar **`` / ``_foo bar _`` 等尾部空格的定界符
        被误判为闭合；``_a_b_`` 中间的词内 ``_`` 被误判为闭合，内容错位
        （渲染为 ``ab_``，丢失一个下划线）。
        """
        left, right = self._delim_flanking(pos, length)
        if not right:
            return False
        if ch == '_' and left:
            after_pos = pos + length
            after = self._text[after_pos] if after_pos < self._n else ''
            if not _is_punct_char(after):
                return False
        return True

    def _delim_run_len(self, pos: int, ch: str) -> int:
        """``pos`` 处同字符 ``ch`` 的连续 run 长度（0 表示非 ``ch``）。"""
        text, n = self._text, self._n
        i = pos
        while i < n and text[i] == ch:
            i += 1
        return i - pos

    def _make_emphasis_close_check(self, opener_pos: int, opener_len: int,
                                   ch: str):
        """构造强调闭合判定回调（右定界符条件 + CommonMark rule of three）。

        rule of three：若「闭合 run 可开启 或 开启 run 可闭合」，且两者长度之和
        是 3 的倍数而两者长度不都是 3 的倍数，则该对定界符不匹配
        （``*foo**bar**baz*`` 的外层 em 不在一对 ``**`` 上闭合，而由内层
        strong 先配对）。缺失该规则时 ``**a*b***`` 之类的组合会残留标记。
        """
        def check(pos: int, _length: int | None = None) -> bool:
            run_len = self._delim_run_len(pos, ch)
            if run_len == 0:
                return False
            if not self._delim_can_close(pos, run_len, ch):
                return False
            closer_can_open = self._delim_can_open(pos, run_len, ch)
            opener_can_close = self._delim_can_close(opener_pos, opener_len, ch)
            if closer_can_open or opener_can_close:
                total = opener_len + run_len
                if total % 3 == 0 and (opener_len % 3 != 0 or run_len % 3 != 0):
                    return False
            return True
        return check

    # ── 粗斜体 *** / ___ ──────────────────────────────────

    def _try_bold_italic(self, depth: int) -> InlineNode | None:
        key = ('bi', self._pos)
        if key in self._emph_fail:
            return None
        node = self._try_bold_italic_impl(depth)
        if node is None:
            self._emph_fail.add(key)
        return node

    def _try_bold_italic_impl(self, depth: int) -> InlineNode | None:
        try:
            saved = self._pos
            triple = self._text[self._pos:self._pos + 3]
            if not (self._pos + 3 < self._n and triple in ('***', '___')):
                return None
            # ★ Bug B3 fix: ___ 在词内（如 ___init___）不应触发粗斜体。
            # ★ dunder 保护（用户选定折中）：dunder 形态（___init___ 等）整体
            #   原样输出为文本——既保持 dunder 保护，也避免其尾部 ``___`` 与
            #   后续下划线跨越配对。
            if triple == '___':
                end = self._dunder_span_end(3)
                if end > 0:
                    self._pos = end
                    return TextNode(content=self._text[saved:end])
                if self._is_word_boundary_underscore(3):
                    return None
            # ★ 左定界符条件（CommonMark）：``*** foo***`` 之类的内侧空白不开启
            ch = triple[0]
            if not self._delim_can_open(saved, 3, ch):
                return None
            self._pos += 3
            children, found = self._parse_until(
                triple, depth + 1,
                close_ok=self._make_emphasis_close_check(saved, 3, ch))
            if found:
                self._pos += 3
                return self._make_nestable(BoldItalicNode, children)
            # ★ run 长度 ≥ 3 但 ``***…***`` 不成立 → CommonMark 的「1+2 分配」：
            #   用 1 个定界符开启 em，其余定界符作为内容开头由递归解析处理
            #   （``***foo** bar*`` → em("**foo** bar")，内部 ``**`` 成 strong）。
            #   修复前直接放弃 → 标记残留（``***foo** bar*`` 渲染为 ``*foo bar*``）。
            if not self._delim_can_open(saved, 1, ch):
                self._pos = saved
                return None
            self._pos = saved + 1
            children, found = self._parse_italic_content(
                ch, depth + 1,
                close_ok=self._make_emphasis_close_check(saved, 1, ch))
            if found:
                self._pos += 1
                return self._make_nestable(ItalicNode, children)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_bold_italic 异常，降级处理", exc_info=True)
            return None

    # ── 粗体 ** / __ ───────────────────────────────────

    def _try_bold(self, depth: int) -> InlineNode | None:
        key = ('b', self._pos)
        if key in self._emph_fail:
            return None
        node = self._try_bold_impl(depth)
        if node is None:
            self._emph_fail.add(key)
        return node

    def _try_bold_impl(self, depth: int) -> InlineNode | None:
        try:
            saved = self._pos
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '**'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '*')):
                if not self._delim_can_open(saved, 2, '*'):
                    return None
                self._pos += 2
                children, found = self._parse_until(
                    '**', depth + 1,
                    close_ok=self._make_emphasis_close_check(saved, 2, '*'))
                if found:
                    self._pos += 2
                    return self._make_nestable(BoldNode, children)
                self._pos = saved
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '__'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '_')):
                # ★ dunder 保护（用户选定折中）：``__init__`` / ``__my_var__``
                #   等 dunder 形态整体原样输出为文本，不渲染粗体。
                end = self._dunder_span_end(2)
                if end > 0:
                    self._pos = end
                    return TextNode(content=self._text[saved:end])
                if not self._is_word_boundary_underscore(2):
                    if not self._delim_can_open(saved, 2, '_'):
                        return None
                    self._pos += 2
                    children, found = self._parse_until(
                        '__', depth + 1,
                        close_ok=self._make_emphasis_close_check(saved, 2, '_'))
                    if found:
                        self._pos += 2
                        return self._make_nestable(BoldNode, children)
                    self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_bold 异常，降级处理", exc_info=True)
            return None

    # ── 斜体 * / _ ─────────────────────────────────────

    def _try_italic(self, depth: int) -> InlineNode | None:
        key = ('i', self._pos)
        if key in self._emph_fail:
            return None
        node = self._try_italic_impl(depth)
        if node is None:
            self._emph_fail.add(key)
        return node

    def _try_italic_impl(self, depth: int) -> InlineNode | None:
        try:
            saved = self._pos
            if (self._text[self._pos] == '*'
                    and not (self._pos + 1 < self._n
                             and self._text[self._pos + 1] == '*')):
                if not self._delim_can_open(saved, 1, '*'):
                    return None
                self._pos += 1
                children, found = self._parse_italic_content(
                    '*', depth + 1,
                    close_ok=self._make_emphasis_close_check(saved, 1, '*'))
                if found:
                    self._pos += 1
                    return self._make_nestable(ItalicNode, children)
                self._pos = saved
            if (self._text[self._pos] == '_'
                    and not (self._pos + 1 < self._n
                             and self._text[self._pos + 1] == '_')
                    # ★ 修复（下划线标识符字符丢失）：当前 ``_`` 的前一个字符
                    #   也是 ``_``（即处于 ``__`` 的**第二个**下划线）时，不再
                    #   单独开启斜体——``__`` 已由粗体 / dunder 判定整体处理，
                    #   判定为非格式时应整体原样输出。修复前 ``__my_var__``
                    #   （dunder 保护不渲染粗体）的第二个 ``_`` 被当作斜体起点、
                    #   与 ``my`` 后的 ``_`` 配对，渲染为 ``_myvar__``（丢失
                    #   一个下划线且内容错位）。
                    and not (self._pos > 0 and self._text[self._pos - 1] == '_')
                    and not self._is_word_boundary_underscore()):
                if not self._delim_can_open(saved, 1, '_'):
                    return None
                self._pos += 1
                children, found = self._parse_italic_content(
                    '_', depth + 1,
                    close_ok=self._make_emphasis_close_check(saved, 1, '_'))
                if found:
                    self._pos += 1
                    return self._make_nestable(ItalicNode, children)
                self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_italic 异常，降级处理", exc_info=True)
            return None

    def _parse_italic_content(self, delim: str, depth: int,
                              close_ok=None) -> tuple[list[InlineNode], bool]:
        """解析斜体内容（``close_ok`` 为闭合定界符条件，CommonMark 右定界符）。

        ``close_ok(pos, length)`` 返回 ``False`` 时该 ``delim`` 不作为闭合
        定界符（例如 ``_foo bar _`` 尾部的 ``_`` 前是空白 → 不闭合），按普通
        字符继续扫描。
        """
        if depth > self._MAX_DEPTH:
            return [TextNode(content=self._text[self._pos:])], False
        try:
            nodes: list[InlineNode] = []
            plain_buf: list[str] = []

            def _emit_plain():
                if plain_buf:
                    nodes.append(TextNode(content=''.join(plain_buf)))
                    plain_buf.clear()

            while self._pos < self._n:
                ch = self._text[self._pos]
                if ch == delim:
                    if (self._pos + 1 < self._n
                            and self._text[self._pos + 1] == delim):
                        node = self._try_format(depth)
                        if node is not None:
                            _emit_plain()
                            nodes.append(node)
                            continue
                        # ★ run 长 ≥ 2 且嵌套解析不成立：仍可只消耗 1 个定界符
                        #   作为闭合（CommonMark：闭合 run 从前往后消耗）——
                        #   ``**a*b***`` 的 ``***`` 闭合 run 用 1 个闭合内层 em、
                        #   余下 2 个闭合外层 strong。修复前整段 ``**`` 当作文本，
                        #   标记残留（``*ab***``）。
                        if close_ok is not None and close_ok(self._pos, 1):
                            _emit_plain()
                            return nodes, True
                        plain_buf.append(self._text[self._pos])
                        plain_buf.append(self._text[self._pos + 1])
                        self._pos += 2
                        continue
                    else:
                        if close_ok is not None and not close_ok(self._pos, 1):
                            # 不能作为闭合：先尝试作为**嵌套开启**——如
                            # ``*(*foo*)*`` 中 ``(`` 后的 ``*``（外层 em 内的
                            # 内层 em）、``_foo _bar_ baz_`` 的内层 em；失败
                            # 才按普通字符。修复前直接当文本 → 嵌套强调失效、
                            # 标记残留。
                            node = self._try_format(depth)
                            if node is not None:
                                _emit_plain()
                                nodes.append(node)
                                continue
                            plain_buf.append(ch)
                            self._pos += 1
                            continue
                        _emit_plain()
                        return nodes, True
                node = self._try_format(depth)
                if node is not None:
                    _emit_plain()
                    nodes.append(node)
                    continue
                plain_buf.append(ch)
                self._pos += 1
            _emit_plain()
            return nodes, False
        except Exception:
            _logger.debug("_parse_italic_content 异常，降级处理", exc_info=True)
            return [], False

    # ── 双字符 span 定界符（== / ++ / || / ~~） ──────────

    def _span_delim_can_open(self, pos: int, length: int = 2) -> bool:
        """``pos`` 处双字符定界符是否可作**左定界符**（CommonMark flanking）。

        适用 ``==x==``（高亮）、``++x++``（下划线）、``||x||``（剧透）、
        ``~~x~~``（删除线）——与强调同族，要求 **left-flanking**：其后非空白，
        且（其后非标点 或 其前为空白/标点）。

        修复前这些语法只看「两字符定界符出现过」，技术文本里的 ``==`` /
        ``++`` / ``||`` 会被当定界符吞掉并把中间内容误加样式（高亮/下划线/
        剧透）：``mode=="change"`` → ``mode"change"``、``a++ 与 b++`` →
        下划线中间的 `` 与 b``。
        """
        return self._delim_flanking(pos, length)[0]

    def _span_delim_can_close(self, pos: int, length: int = 2) -> bool:
        """``pos`` 处双字符定界符是否可作**右定界符**（``_parse_until`` 回调）。

        右定界符 = right-flanking：其前非空白，且（其前非标点 或 其后为空白/
        标点）。不满足时该定界符不作为闭合点、按普通文本继续扫描——``a == b
        == c`` / ``a ++ b ++ c`` 等「两侧皆空白」的定界符保持原样。
        """
        return self._delim_flanking(pos, length)[1]

    # ── 删除线 ~~ ───────────────────────────────────────

    def _try_strikethrough(self, depth: int) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '~~'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '~')):
                # ★ 定界符 flanking：与高亮同口径（不再吞掉技术文本里的 ~~）
                if not self._span_delim_can_open(self._pos):
                    return None
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until(
                    '~~', depth + 1, close_ok=self._span_delim_can_close)
                if found:
                    self._pos += 2
                    return self._make_nestable(StrikethroughNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_strikethrough 异常，降级处理", exc_info=True)
            return None

    # ── 高亮 == ─────────────────────────────────────────

    def _try_highlight(self, depth: int) -> InlineNode | None:
        try:
            # ★ 修复: == 后跟 > 是粗箭头 ==>, 前邻 < 是粗箭头 <==
            #    避免 highlight 语法吞掉箭头
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '=='
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '=')
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '>'
                             and (self._pos == 0 or self._text[self._pos - 1] != '='))
                    and not (self._pos > 0
                             and self._text[self._pos - 1] == '<')):
                # ★ 修复（定界符 flanking）：左定界符条件不满足时不开启高亮
                #   ——``mode=="change"`` 这类「左字母 + 右引号」的代码语义
                #   ``==`` 不再吞掉定界符、不再把中间内容误高亮。
                if not self._span_delim_can_open(self._pos):
                    return None
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until(
                    '==', depth + 1, close_ok=self._span_delim_can_close)
                if found:
                    self._pos += 2
                    return self._make_nestable(HighlightNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_highlight 异常，降级处理", exc_info=True)
            return None

    # ── 下标 ~ ──────────────────────────────────────────

    def _try_subscript(self, depth: int) -> InlineNode | None:
        try:
            if (self._text[self._pos] == '~'
                    and not (self._pos + 1 < self._n
                             and self._text[self._pos + 1] == '~')):
                saved = self._pos
                self._pos += 1
                # ★ 修复 Bug: 空格后不应触发下标（~ text~ 不是合法下标）
                if self._pos < self._n and self._text[self._pos] in ' \t\n\r':
                    self._pos = saved
                    return None
                # ★ 修复 Bug: 下标内容不得含空白（与上标同规则）——
                #   ``a~b c~d`` 不再把 ``b c`` 当作下标。
                if not self._span_has_no_space('~'):
                    self._pos = saved
                    return None
                children, found = self._parse_until('~', depth + 1)
                if found:
                    # ★ 修复 Bug: 若闭合 ~ 后紧跟另一个 ~（~~strikethrough），
                    # 回退以避免吞噬父级 ~~ 闭合定界符
                    if self._pos + 1 < self._n and self._text[self._pos + 1] == '~':
                        self._pos = saved
                        return None
                    self._pos += 1
                    return self._make_nestable(SubscriptNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_subscript 异常，降级处理", exc_info=True)
            return None

    # ── 上标 ^ ──────────────────────────────────────────

    def _span_has_no_space(self, delim: str) -> bool:
        """当前位置到下一个未转义 ``delim`` 之间是否无空白（含闭合符）。

        CommonMark 的上下标类扩展（markdown-it-sub / -sup）要求定界符之间的
        内容不含空白——否则 ``x^2 + y^2`` 会把 ``2 + y`` 整段当作上标、
        ``a~b c~d`` 把 ``b c`` 当作下标。返回 False 表示「含空白」或
        「无闭合定界符」，调用方据此回退（与 ``_parse_until`` found=False 同语义）。
        """
        i = self._pos
        while i < self._n:
            ch = self._text[i]
            if ch == '\\' and i + 1 < self._n:
                i += 2
                continue
            if ch == delim:
                return True
            if ch in ' \t\n\r':
                return False
            i += 1
        return False

    def _try_superscript(self, depth: int) -> InlineNode | None:
        try:
            if self._text[self._pos] == '^':
                saved = self._pos
                self._pos += 1
                # ★ 修复 Bug: 空格后不应触发上标（^ text^ 不是合法上标）
                if self._pos < self._n and self._text[self._pos] in ' \t\n\r':
                    self._pos = saved
                    return None
                # ★ 修复 Bug: 上标内容不得含空白——``x^2 + y^2`` 修复前把
                #   ``2 + y`` 当上标（渲染为 ``x² ⁺ ʸ2``），现原样输出。
                if not self._span_has_no_space('^'):
                    self._pos = saved
                    return None
                children, found = self._parse_until('^', depth + 1)
                if found:
                    self._pos += 1
                    return self._make_nestable(SuperscriptNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_superscript 异常，降级处理", exc_info=True)
            return None

    def _try_inline_footnote(self) -> InlineNode | None:
        """解析行内脚注 ``^[脚注文本]``（Pandoc inline footnote）。

        与上标 ``^x^`` 共享触发字符 ``^``——``^`` 后紧跟 ``[`` 时按行内脚注
        解析（方括号配对，支持嵌套 ``[]`` 与反斜杠转义），否则返回 ``None``
        交回上标处理器。配对失败/跨行未闭合时不消费（由文本兜底，流式下
        不会吞掉后续行内容）。
        """
        try:
            if self._text[self._pos] != '^':
                return None
            if self._pos + 1 >= self._n or self._text[self._pos + 1] != '[':
                return None
            saved = self._pos
            i = self._pos + 2
            n = self._n
            depth = 0
            buf: list[str] = []
            while i < n:
                ch = self._text[i]
                if ch == '\\' and i + 1 < n:
                    buf.append(self._text[i:i + 2])
                    i += 2
                    continue
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    if depth == 0:
                        self._pos = i + 1
                        return InlineFootnoteNode(
                            content=''.join(buf).strip())
                    depth -= 1
                elif ch == '\n':
                    # 行内脚注不跨行（流式语义：未闭合时不吞并后续行）
                    break
                buf.append(ch)
                i += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_inline_footnote 异常，降级处理", exc_info=True)
            return None

    # ── 词内下划线保护 ─────────────────────────────────

    @staticmethod
    def _is_ascii_identifier(middle: str) -> bool:
        """内容是否全为 ASCII 字母/数字/下划线（Python 标识符字符）。

        dunder 保护的分级依据：仅**纯 ASCII 标识符**（``__init__``）按 dunder
        保护、不触发粗体；含非 ASCII 字符（如 ``__粗体__``）按普通粗体标记
        渲染——``str.isalnum()`` 对 CJK 字符同样返回 True，不区分会让中文
        粗体永远无法渲染（行首 ``__粗体__`` 被误判为 ``__init__`` 类标识符）。
        """
        return bool(middle) and all(
            (ch.isalnum() and ch.isascii()) or ch == '_' for ch in middle)

    def _is_word_boundary_underscore(self, count: int = 1) -> bool:
        """如果当前位置的 `_`×count 被字母数字包围，则不视为格式标记。

        Args:
            count: 下划线数量（1=斜体, 2=粗体, 3=粗斜体）

        对 count>=2 的特殊规则：行首的 __ 后紧跟 ASCII 字母数字且闭合内容为
        纯 ASCII 标识符（``__init__``）→ dunder 名，不触发格式；含非 ASCII
        （``__粗体__``）按粗体标记渲染。（★ 用户选定折中：保留 dunder 保护。）
        """
        try:
            if self._pos + count >= self._n:
                return False
            next_ch = self._text[self._pos + count]
            if self._pos > 0:
                prev_ch = self._text[self._pos - 1]
                return prev_ch.isalnum() and next_ch.isalnum()
            else:
                # 行首位置：
                #   count=1 (_xxx_)：无前邻字符，不视为词内（斜体正常触发）
                #   count>=2 (__xxx__)：后紧跟 ASCII 字母数字且中间内容为纯
                #   ASCII 标识符 → __init__ 类 dunder 前缀（含 CJK 等非 ASCII
                #   时按粗体渲染，见 ``_is_ascii_identifier``）。
                if count >= 2:
                    if not (next_ch.isalnum() and next_ch.isascii()):
                        return False
                    delim = '_' * count
                    end = self._text.find(delim, self._pos + count)
                    if end < 0:
                        return False
                    return self._is_ascii_identifier(
                        self._text[self._pos + count:end])
                return False
        except Exception:
            _logger.debug("_is_word_boundary_underscore 异常，降级处理", exc_info=True)
            return False

    def _dunder_span_end(self, count: int = 2) -> int:
        """当前位置 ``_``×count 是否为 dunder 标识符；是则返回闭合后位置。

        判定（与旧 ``_is_dunder_pattern`` 同源，统一为单一真源）：
          - ``_``×count 后紧跟字母数字或下划线；
          - ~32 字符内找到闭合 ``_``×count；
          - 闭合后不是字母数字（排除 ``__xxx__yyy`` 嵌套模式）；
          - 中间内容为**纯 ASCII** 标识符字符（含 CJK 等非 ASCII 时按普通
            粗体 / 粗斜体标记渲染，见 ``_is_ascii_identifier``）。

        Returns:
            闭合定界符之后的位置；``0`` 表示不是 dunder 形态。
        """
        try:
            delim = '_' * count
            pos_after = self._pos + count
            if pos_after >= self._n:
                return 0
            ch_after = self._text[pos_after]
            if not (ch_after.isalnum() or ch_after == '_'):
                return 0
            # 向前扫描最多 32 字符寻找闭合定界符
            limit = min(pos_after + 32, self._n)
            close_pos = self._text.find(delim, pos_after, limit)
            if close_pos < 0:
                return 0
            after_close = close_pos + count
            if after_close < self._n and self._text[after_close].isalnum():
                return 0
            if not self._is_ascii_identifier(self._text[pos_after:close_pos]):
                return 0
            return after_close
        except Exception:
            _logger.debug("_dunder_span_end 异常，降级处理", exc_info=True)
            return 0

    def _is_dunder_pattern(self) -> bool:
        """检测 ``__`` 后是否紧跟 Python dunder 模式（如 ``__init__``）。"""
        return self._dunder_span_end(2) > 0

    def _is_dunder_pattern_triple(self) -> bool:
        """检测 ``___`` 后是否紧跟 triple-dunder 模式（如 ``___init___``）。"""
        return self._dunder_span_end(3) > 0

    # ── 下划线 ++ ──────────────────────────────────────

    def _try_underline(self, depth: int) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '++'):
                # ★ 定界符 flanking：``a++ 与 b++`` 这类自增/运算符文本不再
                #   被当作下划线配对（``++`` 吞掉、中间内容被加下划线样式）。
                if not self._span_delim_can_open(self._pos):
                    return None
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until(
                    '++', depth + 1, close_ok=self._span_delim_can_close)
                if found:
                    self._pos += 2
                    return self._make_nestable(UnderlineNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_underline 异常，降级处理", exc_info=True)
            return None

    # ── 剧透/黑幕 || ─────────────────────────────────────

    def _try_spoiler(self, depth: int) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '||'):
                # ★ 定界符 flanking：``a || b || c``（逻辑或）不再被当作剧透
                #   配对（``||`` 吞掉、中间内容被加黑幕样式）。
                if not self._span_delim_can_open(self._pos):
                    return None
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until(
                    '||', depth + 1, close_ok=self._span_delim_can_close)
                if found:
                    self._pos += 2
                    return self._make_nestable(SpoilerNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_spoiler 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {++added++} ────────────────────────

    def _try_critic_addition(self, depth: int) -> InlineNode | None:
        """{++...++} → CriticAdditionNode（绿色背景文本）。"""
        try:
            if (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{++'
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '+')):
                saved = self._pos
                self._pos += 3
                children, found = self._parse_until('++}', depth + 1)
                if found:
                    self._pos += 3  # skip ++}
                    return self._make_nestable(CriticAdditionNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_critic_addition 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {--deleted--} ──────────────────────

    def _try_critic_deletion(self, depth: int) -> InlineNode | None:
        """{--...--} → CriticDeletionNode（红色删除线文本）。"""
        try:
            if (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{--'
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '-')):
                saved = self._pos
                self._pos += 3
                children, found = self._parse_until('--}', depth + 1)
                if found:
                    self._pos += 3  # skip --}
                    return self._make_nestable(CriticDeletionNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_critic_deletion 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {==highlight==} ────────────────────

    def _try_critic_highlight(self, depth: int) -> InlineNode | None:
        """``{==...==}`` → CriticHighlightNode（黄底黑字高亮）。

        CriticMarkup 标准五类标记中的「高亮」（另见 ``{++ ++}`` / ``{-- --}``
        / ``{~~ ~> ~~}`` / ``{>> <<}``）。与 ``==x==`` 行内高亮语法互不冲突：
        本语法要求双侧花括号包裹。
        """
        try:
            if (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{=='
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '=')):
                saved = self._pos
                self._pos += 3
                children, found = self._parse_until('==}', depth + 1)
                if found:
                    self._pos += 3  # skip ==}
                    return self._make_nestable(CriticHighlightNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_critic_highlight 异常，降级处理", exc_info=True)
            return None

    # ── 小号文本 {-small-} ──────────────────────────────

    def _try_small_text(self, depth: int) -> InlineNode | None:
        """{-...-} → SmallTextNode（dim 小号文本）。

        注意：{- 不能被 -- 匹配（优先走 _try_critic_deletion）。
        """
        try:
            if (self._pos + 3 < self._n
                    and self._text[self._pos:self._pos + 2] == '{-'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '-')):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('-}', depth + 1)
                if found:
                    self._pos += 2  # skip -}
                    return self._make_nestable(SmallTextNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_small_text 异常，降级处理", exc_info=True)
            return None

    # ── 大号文本 {+big+} ────────────────────────────────

    def _try_big_text(self, depth: int) -> InlineNode | None:
        """``{+...}`` → BigTextNode（放大文本，终端以加粗近似）。

        与 ``{-small-}`` 对称的放大语法。注意与 CriticMarkup 添加 ``{++ ++}``
        的区分：调度表中 ``_try_critic_addition`` 先于本方法，且此处显式排除
        第三个字符为 ``+`` 的 ``{++`` 形态，两者互不误吞。
        """
        try:
            if (self._pos + 3 < self._n
                    and self._text[self._pos:self._pos + 2] == '{+'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '+')):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('+}', depth + 1)
                if found:
                    self._pos += 2  # skip +}
                    return self._make_nestable(BigTextNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_big_text 异常，降级处理", exc_info=True)
            return None

    # ── 彩色文本 {color:red}text{color} ─────────────────

    # 已知颜色名白名单（Rich 终端色名）
    _KNOWN_COLORS: frozenset[str] = frozenset({
        'red', 'green', 'blue', 'yellow', 'cyan', 'magenta',
        'white', 'black', 'grey30', 'grey50', 'bright_red',
        'bright_green', 'bright_blue', 'bright_yellow',
        'bright_cyan', 'bright_magenta', 'bright_white',
        'orange1', 'purple', 'pink1',
    })

    def _try_color_text(self, depth: int) -> InlineNode | None:
        """{color:COLOR}...{color} → ColorTextNode。

        检测 {color: 前缀 + 已知颜色名 + } 模式。
        """
        try:
            if not (self._pos + 7 < self._n
                    and self._text[self._pos:self._pos + 7] == '{color:'):
                return None
            # 查找 } 结束标记提取颜色名
            saved = self._pos
            color_start = self._pos + 7
            color_end = self._text.find('}', color_start)
            if color_end < 0 or color_end - color_start > 20:
                return None
            color_name = self._text[color_start:color_end].lower()
            if color_name not in self._KNOWN_COLORS:
                return None
            self._pos = color_end + 1  # skip {color:COLOR}
            children, found = self._parse_until('{color}', depth + 1)
            if found:
                self._pos += 7  # skip {color}
                text = render_inline_to_text(children)
                return ColorTextNode(content=text, children=children, color=color_name)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_color_text 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {~~old~>new~~} 替换 ─────────────────

    def _try_critic_substitution(self, depth: int) -> InlineNode | None:
        """{~~old~>new~~} → CriticSubstitutionNode（删除线旧文本 + 绿色插入新文本）。

        解析为 children=旧文本节点列表, meta['new_children']=新文本节点列表。
        ★ P0 修复: 支持反斜杠转义分隔符 ~>，防止内容中的 ~> 被错误分割。
        """
        try:
            if not (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{~~'
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '~')):
                return None
            saved = self._pos
            self._pos += 3  # skip {~~
            # 在内容中查找未转义的 ~> 分隔符（支持 \~> 转义）
            old_children, found_sep = self._parse_until_unescaped('~>', depth + 1, escape_char='\\')
            if not found_sep or not old_children:
                self._pos = saved
                return None
            self._pos += 2  # skip ~>
            new_children, found_close = self._parse_until('~~}', depth + 1)
            if not found_close:
                self._pos = saved
                return None
            self._pos += 3  # skip ~~}
            old_text = render_inline_to_text(old_children)
            new_text = render_inline_to_text(new_children)
            full_text = f"{old_text}→{new_text}"
            return CriticSubstitutionNode(
                content=full_text,
                children=old_children,
                meta={"new_children": new_children},
            )
        except Exception:
            _logger.debug("_try_critic_substitution 异常，降级处理", exc_info=True)
            return None

    def _parse_until_unescaped(self, delim: str, depth: int, escape_char: str = '\\'
                               ) -> tuple[list[InlineNode], bool]:
        """解析直到遇到未转义的分隔符，支持 escape_char 转义。

        Returns:
            (children, found) — found=True 表示找到分隔符（含转义处理）。
        """
        if depth > self._MAX_DEPTH:
            return [TextNode(content=self._text[self._pos:])], False
        nodes: list[InlineNode] = []
        plain_buf: list[str] = []
        delim_len = len(delim)

        def _emit_plain():
            if plain_buf:
                nodes.append(TextNode(content=''.join(plain_buf)))
                plain_buf.clear()

        while self._pos < self._n:
            # 转义字符：跳过下一个字符作为字面量
            if escape_char and self._text[self._pos] == escape_char:
                if self._pos + 1 < self._n:
                    plain_buf.append(self._text[self._pos + 1])
                    self._pos += 2
                else:
                    plain_buf.append(escape_char)
                    self._pos += 1
                continue
            # 检查分隔符
            if self._pos + delim_len <= self._n and self._text[self._pos:self._pos + delim_len] == delim:
                _emit_plain()
                return nodes, True
            # 尝试内联格式（★ 性能：仅兴趣位置进入——英文文本中的普通字母
            #   不再逐个尝试格式解析，见 ``_build_interest_positions``）
            node = self._try_format(depth) if self._is_interest(self._pos) else None
            if node is not None:
                _emit_plain()
                nodes.append(node)
                continue
            plain_buf.append(self._text[self._pos])
            self._pos += 1
        _emit_plain()
        return nodes, False

    # ── CriticMarkup {>>comment<<} 批注 ─────────────────

    def _try_critic_comment(self, depth: int) -> InlineNode | None:
        """{>>...<<} → CriticCommentNode（批注/注释文本）。"""
        try:
            if not (self._pos + 3 < self._n
                    and self._text[self._pos:self._pos + 3] == '{>>'):
                return None
            saved = self._pos
            self._pos += 3  # skip {>>
            children, found = self._parse_until('<<}', depth + 1)
            if found:
                self._pos += 3  # skip <<}
                text = render_inline_to_text(children)
                return CriticCommentNode(content=text, children=children)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_critic_comment 异常，降级处理", exc_info=True)
            return None

    # ── Wiki 链接 [[target]] 或 [[target|display]] ──────────

    def _try_wikilink(self, depth: int) -> InlineNode | None:
        """[[target]] 或 [[target|display]] → WikiLinkNode。

        双 [[ 开头，匹配到 ]] 结束。
        支持 | 分隔 display 文本。
        """
        try:
            if not (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 2] == '[['):
                return None
            saved = self._pos
            self._pos += 2  # skip [[
            # 扫描到 ]] ，收集内容
            content_start = self._pos
            while self._pos < self._n:
                if (self._pos + 1 < self._n
                        and self._text[self._pos:self._pos + 2] == ']]'):
                    content = self._text[content_start:self._pos]
                    self._pos += 2  # skip ]]
                    # 解析 target 和可选的 display
                    pipe_idx = content.find('|')
                    if pipe_idx >= 0:
                        target = content[:pipe_idx].strip()
                        display = content[pipe_idx + 1:].strip() or None
                    else:
                        target = content.strip()
                        display = None
                    if target:
                        return WikiLinkNode(
                            content=display or target,
                            target=target,
                            display=display,
                        )
                    self._pos = saved
                    return None
                self._pos += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_wikilink 异常，降级处理", exc_info=True)
            return None

    # ── 行内注释 %% comment %% ────────────────────────────

    def _try_inline_comment(self, depth: int) -> InlineNode | None:
        """%% comment %% → InlineCommentNode（隐藏文本）。

        规则：
        - 开头 %% 不能紧跟第三个 %（避免 %%% 混淆）
        - 开头 %% 不能紧跟在前一个 % 之后（避免 x%%% 中 2-3位触发）
        - 结尾 %% 不能紧跟在前一个 % 之后（避免 %%text%%% 中末三位触发）
        """
        try:
            if not (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 2] == '%%'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '%')):
                return None
            # ★ 修复: 开头 %% 不能紧跟在前一个 % 之后（如 text%%% → 位置5的%%不应触发）
            if self._pos > 0 and self._text[self._pos - 1] == '%':
                return None
            saved = self._pos
            self._pos += 2  # skip %%
            content_start = self._pos
            while self._pos < self._n:
                if (self._pos + 1 < self._n
                        and self._text[self._pos:self._pos + 2] == '%%'
                        and not (self._pos + 2 < self._n
                                 and self._text[self._pos + 2] == '%')):
                    # ★ 修复: 结尾 %% 不能紧跟在前一个 % 之后（如 %%text%%% → 末三位）
                    if self._pos > 0 and self._text[self._pos - 1] == '%':
                        self._pos += 1
                        continue
                    content = self._text[content_start:self._pos]
                    self._pos += 2  # skip %%
                    return InlineCommentNode(content=content)
                self._pos += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_inline_comment 异常，降级处理", exc_info=True)
            return None
