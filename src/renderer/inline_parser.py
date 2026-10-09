"""inline_parser — 内联 Markdown 递归下降解析器（无正则表达式）。

从 recursive_parser.py 拆分而来，包含 _InlineParser 及其辅助函数。
纯字符级扫描，无任何正则表达式。

原位置：recursive_parser.py（第298-1225行）

拆分说明：
  - _inline_html.py       — HTML 标签/注释/实体解析 Mixin
  - _inline_links.py      — 链接/图片/自动链接/脚注解析 Mixin
  - _inline_formatting.py — 粗体/斜体/删除线/高亮/上下标解析 Mixin
"""

from __future__ import annotations

import logging
import string
from bisect import bisect_left

_logger = logging.getLogger(__name__)

from .inline_nodes import (
    InlineNode, TextNode,
    InlineCodeNode, InlineMathNode,
    LineBreakNode,
    render_inline_to_text,
)
from .emoji_map import EMOJI_MAP

from ._inline_html import InlineHTMLMixin
from ._inline_links import InlineLinksMixin
from ._inline_formatting import InlineFormattingMixin


# ═══════════════════════════════════════════════════════════
# 内联解析器（真正的递归下降，无正则）
# ═══════════════════════════════════════════════════════════

class _InlineParser(InlineHTMLMixin, InlineLinksMixin, InlineFormattingMixin):
    """真正的递归下降内联 Markdown 解析器（字符级扫描，无正则）。"""
    _MAX_DEPTH = 20
    _FORMAT_CHARS: frozenset[str] = frozenset('\\`:*_~=$[<!&^@hHfFwW+|{%')
    _URL_PROTOCOLS: tuple[str, ...] = ('https://', 'http://', 'ftp://', 'ftps://')

    #: 可反斜杠转义的字符集合——CommonMark：**任何 ASCII 标点**都可被转义
    #: （``string.punctuation`` 恰为 ``!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~``）。
    #: 修复前仅列举了其中一部分（缺 ``" % & ' , / ; ? @``），这些标点前的
    #: 反斜杠会**原样泄漏**进正文（``a\%b`` → ``a\%b``）。
    _ESCAPABLE_CHARS: frozenset[str] = frozenset(string.punctuation)

    def __init__(self, text: str):
        self._text = text
        self._pos = 0
        self._n = len(text)
        # ★ 性能（英文流式渲染）：行内「兴趣位置」表——只有这些位置才可能产生
        #   非纯文本节点（核心格式触发字符，或裸 URL 前缀起点）。``_parse_until``
        #   据此二分查找一次跳过整段普通文本；英文文本中频次极高的 h/f/w 字母
        #   不再逐个尝试格式解析（构建口径见 ``_build_interest_positions``，
        #   与 ``text_has_inline_markup`` 的快速判否口径完全一致）。
        #   位置数超限（病态输入）时表为 ``None`` → 回退逐字符扫描（与优化前
        #   行为一致，见 ``_is_interest`` / ``_find_next_format_char``）。
        positions = _build_interest_positions(text)
        self._interest_positions = positions
        self._interest_set = frozenset(positions) if positions is not None else None

    @staticmethod
    def _make_nestable(cls: type, children: list[InlineNode]) -> InlineNode:
        text = render_inline_to_text(children)
        return cls(content=text, children=children)

    def parse(self) -> list[InlineNode]:
        nodes, _ = self._parse_until('', 0)
        return nodes

    def _is_interest(self, pos: int) -> bool:
        """位置 ``pos`` 是否为「兴趣位置」（需要尝试格式解析的点）。

        兴趣表被禁用（位置数超限）时按 ``_FORMAT_CHARS`` 逐字符判定——与优化
        前行为完全一致。
        """
        interest = self._interest_set
        if interest is None:
            return self._text[pos] in self._FORMAT_CHARS
        return pos in interest

    def _find_next_format_char(self, start: int, end: int) -> int:
        """找到 ``[start, end)`` 内下一个「兴趣位置」（无则返回 ``end``）。

        兴趣位置 = 核心格式触发字符（``_CORE_FORMAT_CHARS``）出现处，或裸 URL
        前缀起点（``h/H/f/F/w/W`` 且其后为 ``http:`` / ``https:`` / ``ftp:`` /
        ``ftps:`` / ``www.``，大小写不敏感）。表在 ``__init__`` 一次性构建
        （``_build_interest_positions``），此处二分查找 O(log k)；表被禁用时
        回退逐字符扫描（``_scan_next_format_char``，与优化前一致）。

        原实现逐字符 Python 扫描并按 ``_FORMAT_CHARS``（含 h/f/w 字母）判定，
        英文文本下每 4096 字符窗口触发约 200 次扫描与无效格式尝试——本方法
        改为一次跳过整段普通文本（普通字母不再进入 ``_try_format``）。
        """
        positions = self._interest_positions
        if positions is None:
            return self._scan_next_format_char(start, end)
        i = bisect_left(positions, start)
        if i < len(positions):
            p = positions[i]
            if p < end:
                return p
        return end

    def _scan_next_format_char(self, start: int, end: int) -> int:
        """逐字符扫描下一个 ``_FORMAT_CHARS`` 成员（兴趣表禁用时的回退路径）。"""
        text = self._text
        pos = start
        while pos < end:
            if text[pos] in self._FORMAT_CHARS:
                return pos
            pos += 1
        return end

    def _parse_until(self, close_delim: str,
                     depth: int = 0,
                     close_ok=None) -> tuple[list[InlineNode], bool]:
        """解析到 ``close_delim``（返回 ``(nodes, found)``）。

        ``close_ok(pos, length)`` 为闭合定界符条件（CommonMark 右定界符规则）：
        返回 ``False`` 时该位置的定界符不作为闭合点，按普通字符继续扫描。
        ``None`` 表示无条件匹配（既有行为）。
        """
        if depth > self._MAX_DEPTH:
            return [TextNode(content=self._text[self._pos:])], False

        nodes: list[InlineNode] = []
        plain_buf: list[str] = []
        close_len = len(close_delim)
        # 兴趣表引用循环外取值（表在解析期不变；``None`` 表示病态输入的
        # 回退模式——逐字符判定）
        interest_set = self._interest_set

        def _emit_plain():
            if plain_buf:
                nodes.append(TextNode(content=''.join(plain_buf)))
                plain_buf.clear()

        while self._pos < self._n:
            if close_len > 0 and self._try_match_str(close_delim) \
                    and (close_ok is None or close_ok(self._pos, close_len)):
                _emit_plain()
                return nodes, True

            if interest_set is None:
                # 兴趣表禁用（病态输入）：按 _FORMAT_CHARS 逐字符判定（原语义）
                is_interest = self._text[self._pos] in self._FORMAT_CHARS
            else:
                is_interest = self._pos in interest_set
            if not is_interest:
                # ★ 性能（英文流式渲染）：普通文本段一次跳过——二分查找下一个
                #   兴趣位置后整段切片追加（C 级），不再逐字符 Python 循环、
                #   也不再对普通字母（英文文本中 h/f/w 出现频率约 10%）逐个
                #   尝试格式解析。产出与逐字符路径完全一致（plain_buf 拼接的
                #   连续文本；TextNode 分段粒度变化不影响最终 runs——相邻同样式
                #   run 在 ``_append`` 处合并）。
                search_end = self._n
                if close_delim:
                    close_pos = self._text.find(close_delim, self._pos)
                    if close_pos >= 0 and close_pos < search_end:
                        search_end = close_pos
                fmt_pos = self._find_next_format_char(self._pos, search_end)
                if fmt_pos > self._pos:
                    plain_buf.append(self._text[self._pos:fmt_pos])
                    self._pos = fmt_pos
                    continue
                plain_buf.append(self._text[self._pos:])
                self._pos = self._n
                continue

            pos_before = self._pos
            try:
                node = self._try_format(depth)
            except Exception:
                node = None
            if node is not None:
                # ★ 修复（review 方向）：裸邮箱检测从 '@' 向前回溯局部部分，
                #   这些字符此前已被缓冲进 plain_buf——emit 前按局部部分长度
                #   截掉，避免 "foo@bar.com" 渲染为 "foofoo@bar.com"（仅当文本
                #   同时含其他行内标记时走本路径）。
                local_start = getattr(self, '_last_email_local_start', None)
                if (local_start is not None
                        and type(node).__name__ == 'AutoLinkEmailNode'):
                    self._trim_plain_buf(plain_buf, pos_before - local_start)
                self._last_email_local_start = None
                _emit_plain()
                nodes.append(node)
                continue

            plain_buf.append(self._text[self._pos])
            self._pos += 1

        _emit_plain()
        return nodes, False

    @staticmethod
    def _trim_plain_buf(plain_buf: list[str], n: int) -> None:
        """从 plain_buf 尾部截掉 n 个字符（跨字符串条目）。"""
        while n > 0 and plain_buf:
            s = plain_buf[-1]
            take = min(n, len(s))
            if take >= len(s):
                plain_buf.pop()
            else:
                plain_buf[-1] = s[:-take]
            n -= take

    def _try_format(self, depth: int) -> InlineNode | None:
        try:
            self._last_email_local_start = None
            ch = self._text[self._pos] if self._pos < self._n else ''

            entries = self._METHOD_CACHE.get(ch)
            if entries is not None:
                for method, needs_depth in entries:
                    if needs_depth:
                        node = method(self, depth)
                    else:
                        node = method(self)
                    if node is not None:
                        return node
                return None

            # ── 裸 URL 检测：仅在字母 h/f/w 且紧跟 :// 或 www. 前缀时触发 ──
            if ch in 'hHfFwW' and self._pos + 5 < self._n:
                c = self._text[self._pos]
                is_url_candidate = False
                if c in 'hH' and self._pos + 7 <= self._n:
                    is_url_candidate = (self._text[self._pos:self._pos+5].lower() == 'http:' or
                                        self._text[self._pos:self._pos+6].lower() == 'https:')
                elif c in 'fF' and self._pos + 6 <= self._n:
                    is_url_candidate = (self._text[self._pos:self._pos+4].lower() == 'ftp:' or
                                        self._text[self._pos:self._pos+5].lower() == 'ftps:')
                elif c in 'wW' and self._pos + 4 <= self._n:
                    is_url_candidate = self._text[self._pos:self._pos+4].lower() == 'www.'

                if is_url_candidate and (node := self._try_bare_url()):
                    return node

            return None
        except Exception:
            _logger.debug("_try_format 异常，跳过格式标记", exc_info=True)
            self._pos += 1
            return None

    def _try_match_str(self, s: str) -> bool:
        if self._pos + len(s) <= self._n:
            return self._text[self._pos:self._pos + len(s)] == s
        return False

    # ── 转义 ──────────────────────────────────────────────

    def _try_escape(self) -> InlineNode | None:
        try:
            if self._pos + 1 < self._n and self._text[self._pos] == '\\':
                ch = self._text[self._pos + 1]
                if ch in self._ESCAPABLE_CHARS:
                    self._pos += 2
                    return TextNode(content=ch)
            return None
        except Exception:
            _logger.debug("_try_escape 异常，降级处理", exc_info=True)
            return None

    # ── 行内代码 ─────────────────────────────────────────

    def _try_inline_code(self) -> InlineNode | None:
        """行内代码 `` `…` ``（CommonMark 反引号串语义）。

        起始为**连续 k 个反引号**（k ≥ 1），结束须为**恰好 k 个**反引号
        （修复前只支持 k=1/2，`` ``` ` `` ``` 等三反引号串被误当双反引号，
        内容错位）。内容保留软换行（与段落「软换行=换行」的终端呈现一致，
        行内代码跨软换行同样按行拆开）。
        """
        try:
            if self._text[self._pos] != '`':
                return None
            saved = self._pos
            n = self._n
            start = self._pos
            while self._pos < n and self._text[self._pos] == '`':
                self._pos += 1
            open_len = self._pos - start
            content_start = self._pos
            i = self._pos
            while i < n:
                if self._text[i] == '`':
                    j = i
                    while j < n and self._text[j] == '`':
                        j += 1
                    if j - i == open_len:
                        content = self._text[content_start:i]
                        self._pos = j
                        return InlineCodeNode(content=content)
                    i = j
                else:
                    i += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_inline_code 异常，降级处理", exc_info=True)
            return None

    # ── \(行内数学\) ──────────────────────────────────

    def _try_paren_math(self) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == r'\('):
                saved = self._pos
                self._pos += 2
                content_start = self._pos
                while self._pos < self._n:
                    if (self._pos + 1 < self._n
                            and self._text[self._pos:self._pos + 2] == r'\)'):
                        content = self._text[content_start:self._pos]
                        if not content:
                            # ★ 空数学（``\(\)``）不作为公式：回退让 ``\(`` /
                            #   ``\)`` 走反斜杠转义（CommonMark：``\(`` 是 ``(``
                            #   的转义）——修复前产出空数学节点，括号字符丢失
                            #   （``\(\)`` 渲染为 ``()`` 以外的空内容）。
                            self._pos = saved
                            return None
                        self._pos += 2
                        return InlineMathNode(content=content)
                    self._pos += 1
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_paren_math 异常，降级处理", exc_info=True)
            return None

    # ── $行内数学$ ─────────────────────────────────────

    def _try_inline_math(self) -> InlineNode | None:
        """``$...$`` 行内数学 / ``$$...$$`` 行内显示数学。

        定界规则（保守，避免把金额当公式）：

          - 起始 ``$`` 后不得为空白（``$ x $`` 不成公式）；
          - 结束 ``$`` 前不得为空白（``$5 and $6`` 不成公式，美元符号保留）；
          - ``$$`` 须成对；未成对时按普通文本处理（不误吞）。

        修复前无上述约束：``price $5 and $6`` 的美元符号被当定界符吞掉；
        ``$$x$$`` 被拆成「字面 ``$`` + 行内数学 + 字面 ``$``」（多出 ``$``）。
        """
        try:
            if self._pos >= self._n or self._text[self._pos] != '$':
                return None
            text, n = self._text, self._n
            if self._pos + 1 < n and text[self._pos + 1] == '$':
                # ``$$...$$``：成对时整体作为行内显示数学
                end = text.find('$$', self._pos + 2)
                if end > self._pos + 2:
                    content = text[self._pos + 2:end]
                    self._pos = end + 2
                    return InlineMathNode(content=content)
                return None
            nxt = text[self._pos + 1] if self._pos + 1 < n else ''
            if not nxt or nxt.isspace():
                return None
            saved = self._pos
            self._pos += 1
            content_start = self._pos
            depth = 0
            while self._pos < n:
                ch = text[self._pos]
                if ch == "\\" and self._pos + 1 < n:
                    # 转义字符（``\{`` / ``\}`` / ``\$``）不计入花括号深度
                    self._pos += 2
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth = max(0, depth - 1)
                elif (ch == "$" and depth == 0
                        and self._pos > content_start
                        and not text[self._pos - 1].isspace()
                        and not (self._pos + 1 < n
                                 and text[self._pos + 1] == "$")):
                    # 花括号内的 ``$`` 属于内容（``\text{a $x$ b}`` 的内联
                    # 数学切换），只有顶层 ``$`` 才结束行内数学
                    content = text[content_start:self._pos]
                    self._pos += 1
                    return InlineMathNode(content=content)
                self._pos += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_inline_math 异常，降级处理", exc_info=True)
            return None

    # ── Emoji ────────────────────────────────────────────

    def _try_emoji(self) -> InlineNode | None:
        try:
            if self._text[self._pos] != ':':
                return None
            saved = self._pos
            self._pos += 1
            name_start = self._pos
            while (self._pos < self._n
                   and (self._text[self._pos].isalnum()
                        or self._text[self._pos] in '_-+')):
                self._pos += 1
            if self._pos < self._n and self._text[self._pos] == ':':
                name = self._text[name_start:self._pos]
                full = f':{name}:'
                if full in EMOJI_MAP:
                    self._pos += 1
                    return TextNode(content=EMOJI_MAP[full])
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_emoji 异常，降级处理", exc_info=True)
            return None

    # ── <br> 换行 ──────────────────────────────────────

    def _try_line_break(self) -> InlineNode | None:
        """尝试解析 <br> / <br/> / <br /> 换行标签。"""
        try:
            # 检查至少有 4 个字符 <br> 或 <br/
            if self._pos + 3 < self._n:
                lower4 = self._text[self._pos:self._pos + 4].lower()
                if lower4 in ('<br>', '<br/'):
                    self._pos += 4
                    if self._pos < self._n and self._text[self._pos] == '>':
                        self._pos += 1
                    return LineBreakNode()
                # <br /> 带空格的自闭合
                if lower4 == '<br ' and self._pos + 5 < self._n                         and self._text[self._pos + 4:self._pos + 6] == '/>':
                    self._pos += 6
                    return LineBreakNode()
            return None
        except Exception:
            _logger.debug("_try_line_break 异常，降级处理", exc_info=True)
            return None


# ── 字符级格式调度表（类属性，避免每次调用重新构造） ──
_InlineParser._FORMAT_DISPATCH = {
    '\\': (('_try_paren_math', False), ('_try_escape', False)),
    '`':  (('_try_inline_code', False),),
    ':':  (('_try_emoji', False),),
    '*':  (('_try_bold_italic', True), ('_try_bold', True), ('_try_italic', True)),
    '_':  (('_try_bold_italic', True), ('_try_bold', True), ('_try_italic', True)),
    '~':  (('_try_strikethrough', True), ('_try_subscript', True)),
    '=':  (('_try_highlight', True),),
    '$':  (('_try_inline_math', False),),
    '[':  (('_try_footnote_ref', False), ('_try_wikilink', True), ('_try_link', True)),
    '<':  (('_try_line_break', False), ('_try_angle_autolink', False), ('_try_html_tag', True), ('_try_html_comment', False)),
    '!':  (('_try_image', True),),
    '&':  (('_try_html_entity', False),),
    '^':  (('_try_inline_footnote', False), ('_try_superscript', True)),
    '@':  (('_try_bare_email', False),),
    '+':  (('_try_underline', True),),
    '|':  (('_try_spoiler', True),),
    '{':  (('_try_critic_addition', True), ('_try_critic_deletion', True),
            ('_try_critic_highlight', True),
            ('_try_critic_substitution', True), ('_try_critic_comment', True),
            ('_try_small_text', True), ('_try_big_text', True),
            ('_try_color_text', True)),
    '%':  (('_try_inline_comment', True),),
}

# ★ 优化：将 _FORMAT_DISPATCH 中的方法名预解析为实际方法引用
_InlineParser._METHOD_CACHE: dict[str, list[tuple]] = {}
for ch, entries in _InlineParser._FORMAT_DISPATCH.items():
    resolved = []
    for method_name, needs_depth in entries:
        resolved.append((getattr(_InlineParser, method_name), needs_depth))
    _InlineParser._METHOD_CACHE[ch] = resolved


# ═══════════════════════════════════════════════════════════
# 兴趣位置表（英文流式渲染性能）
# ═══════════════════════════════════════════════════════════
#
# ``_FORMAT_CHARS`` 中的 ``h/H/f/F/w/W`` 并非格式标记本身，而是**裸 URL
# 前缀**（``http:`` / ``https:`` / ``ftp:`` / ``ftps:`` / ``www.``）的首字母。
# 英文文本中这几个字母出现频率约 10%，若在解析主循环里逐字符对其尝试格式
# 解析，会产生大量无效调用（h/f/w 字母处 + 紧随其后的普通文本段扫描），
# 实测长段落流式预览每帧 ~0.9ms（4096 字符窗口）。
#
# 优化：解析前一次性构建「兴趣位置」表——核心格式触发字符出现处 + 裸 URL
# 前缀起点；主循环据此二分查找，一次跳过整段普通文本。表口径与
# ``text_has_inline_markup``（快速判否）完全一致：判定为「无行内标记」的文本
# 解析结果必为单一纯文本 Run。

#: 裸 URL 前缀首字母（大小写全覆盖）
_URL_LETTERS: frozenset[str] = frozenset('hHfFwW')

#: 核心格式触发字符（不含裸 URL 首字母——它们由前缀匹配精确驱动）
_CORE_FORMAT_CHARS: frozenset[str] = _InlineParser._FORMAT_CHARS - _URL_LETTERS

#: 裸 URL 前缀（小写比较；与 ``_try_format`` / ``_try_bare_url`` 的候选判定
#: 同一口径，覆盖大小写混合写法）
_URL_PREFIXES: tuple[str, ...] = ('http:', 'https:', 'ftp:', 'ftps:', 'www.')

#: 兴趣位置表规模上限。超过则**禁用表**（解析器回退为逐字符扫描，与优化前
#: 行为完全一致）——病态输入（如超长分隔线/下划线行）下避免表本身占用过多
#: 内存与构建时间；此时几乎每个字符都是触发字符，逐字符扫描并无性能损失。
_INTEREST_POSITIONS_MAX = 65536


def _build_interest_positions(text: str) -> "list[int] | None":
    """构建「需要尝试内联格式解析」的字符位置表（升序，无重复）。

    Args:
        text: 待解析文本。

    Returns:
        升序位置列表；位置数超过 ``_INTEREST_POSITIONS_MAX`` 时返回 ``None``
        （调用方回退逐字符扫描）。两类位置：
          1. 核心格式触发字符（``_CORE_FORMAT_CHARS``）出现处；
          2. 裸 URL 前缀起点（``h/H/f/F/w/W`` 且其后紧接 ``http:`` /
             ``https:`` / ``ftp:`` / ``ftps:`` / ``www.``，大小写不敏感）。

    实现用 C 级 ``str.find`` 逐个核心字符收集（每字符一次 memchr 扫描），
    URL 前缀经一次 ``str.lower`` 副本匹配全部大小写组合——总成本 O(|核心字符|·n)
    的 C 级扫描，对每帧 4096 字符窗口约数十微秒，远低于原逐字符 Python 扫描
    （每窗口 ~200 次格式尝试）。
    """
    if not text:
        return []
    max_positions = _INTEREST_POSITIONS_MAX
    positions: list[int] = []
    append = positions.append
    for ch in _CORE_FORMAT_CHARS:
        i = text.find(ch)
        while i >= 0:
            append(i)
            if len(positions) > max_positions:
                return None
            i = text.find(ch, i + 1)
    for ch in _URL_LETTERS:
        if ch in text:
            low = text.lower()
            for prefix in _URL_PREFIXES:
                i = low.find(prefix)
                while i >= 0:
                    append(i)
                    if len(positions) > max_positions:
                        return None
                    i = low.find(prefix, i + 1)
            break
    positions.sort()
    return positions


def text_has_inline_markup(text: str) -> bool:
    """文本是否**可能**产生非纯文本行内节点（快速判否）。

    返回 ``False`` 时，``_InlineParser(text).parse()`` 的结果必为单一纯文本
    （无任何格式节点），可直接走单 Run 快路径；返回 ``True`` 时需进入解析器。

    判定口径与 ``_build_interest_positions`` 完全一致：核心格式触发字符出现，
    或文本含裸 URL 前缀（大小写不敏感）。实现用少量 C 级 ``in`` / ``find``
    扫描（核心字符短路返回），比 ``frozenset.isdisjoint`` 逐字符查找更快
    （长文本下尤其明显）。
    """
    for ch in _CORE_FORMAT_CHARS:
        if ch in text:
            return True
    for ch in _URL_LETTERS:
        if ch in text:
            low = text.lower()
            for prefix in _URL_PREFIXES:
                if prefix in low:
                    return True
            return False
    return False


__all__ = ["_InlineParser", "text_has_inline_markup"]




