r"""_inline_scan — 行内解析的扫描原语（无正则，C 级 ``str.find`` 驱动）。

职责：把行内解析热路径上「逐字符 Python 循环找终止符 / 定界符 run」的扫描
收敛为少量可复用的原语，消除超长单行（重复定界符 run、缺闭合标记）下的
O(n²) 退化：

  - :func:`find_first_of`——一次定位「首个属于给定字符集的字符」；
  - :class:`DelimiterRunCache`——缓存连续同字符 run 的终点（run 内每个位置
    都会被 ``_delim_flanking`` / ``_delim_run_len`` 查询，逐次扫描使长 run 行
    退化为平方）；
  - :func:`char_presence_index`——长文本的字符存在索引（必要条件快速判否：
    ``]`` 不存在时任何 ``[...]`` 语法必不成立）。

设计约束：
  - 保持解析层「无正则表达式」的既有契约（只用 ``str.find`` / ``in`` / 集合）；
  - 原语均为纯函数或轻量 memo，**不改变解析语义**（等价性由测试锁定）；
  - 缓存按解析器实例持有（文本不同不得跨实例复用）。
"""

from __future__ import annotations

#: 字符存在索引（``frozenset``）的最小文本长度——短文本直接 ``in`` 更快。
CHAR_INDEX_MIN = 256


def find_first_of(text: str, chars: str, start: int = 0) -> int:
    """``text`` 中从 ``start`` 起首个属于 ``chars`` 的字符下标（无则 -1）。

    以 ``len(chars)`` 次 C 级 ``str.find`` 取最小下标，替代等价语义的逐字符
    Python 扫描（超长 run 上快数十倍）。``chars`` 为空返回 -1；单字符时退化
    为一次 ``find``。

    Args:
        text: 待扫描文本。
        chars: 目标字符集合（字符串形式，字符按需去重由调用方保证）。
        start: 起始下标（含）。

    Returns:
        首个命中下标；无命中返回 -1。
    """
    if not chars:
        return -1
    if len(chars) == 1:
        return text.find(chars, start)
    best = -1
    for ch in chars:
        i = text.find(ch, start)
        if i >= 0 and (best < 0 or i < best):
            best = i
    return best


def char_presence_index(text: str) -> "frozenset[str] | None":
    """长文本的字符存在索引（短文本返回 ``None``，调用方回退 ``in``）。

    供「必要条件快速判否」使用：例如文本不含 ``]`` 时，``[...]`` 系列行内
    语法（链接/图片/脚注/WikiLink）**必然**解析失败——据此在 O(1) 内直接
    拒绝，消除「超长 run 上每个位置都扫描到文本末尾」的平方退化。
    """
    if len(text) < CHAR_INDEX_MIN:
        return None
    return frozenset(text)


class DelimiterRunCache:
    """连续同字符 run 的终点缓存（``*`` / ``_`` / ``+`` / ``|`` / ``~``）。

    ``_delim_flanking``（CommonMark 的定界符 flanking 判定）与
    ``_delim_run_len`` 需要「从 ``pos`` 起同字符 run 的终点」。同一 run 内的
    每个位置都会被查询（run 长 k 时约 k 次），若逐次全扫描则长 run 单行
    （``********…`` / ``========`` / ``||||…``）的行内解析退化为 O(n²)
    （8000 个 ``*`` 实测 7.2s）。本缓存记录最近一次扫描的 run 区间，命中区间
    内任意位置即 O(1) 返回。

    正确性：run 终点只由文本内容决定；命中条件同时校验「同字符」与「``pos``
    落在已扫描区间内」，故返回的终点与全扫描一致。缓存**按解析器实例持有**
    （不同文本不得共用，见 ``_InlineParser.__init__``）。

    线程安全：解析器实例不跨线程共享（实例内缓存无锁）。
    """

    __slots__ = ("_ch", "_start", "_end")

    def __init__(self) -> None:
        self._ch = ""
        self._start = 0
        self._end = 0

    def reset(self) -> None:
        """清空缓存（解析器复用/状态重建时调用）。"""
        self._ch = ""
        self._start = self._end = 0

    def run_end(self, text: str, pos: int, ch: str) -> int:
        """返回 ``text`` 中从 ``pos`` 起的同字符 ``ch`` run 的终点下标。

        ``text[pos] != ch`` 时返回 ``pos``（零长 run，与调用方「run 长度 0」
        语义一致）。
        """
        if ch == self._ch and self._start <= pos < self._end:
            return self._end
        n = len(text)
        i = pos
        while i < n and text[i] == ch:
            i += 1
        self._ch = ch
        self._start = pos
        self._end = i
        return i


__all__ = [
    "CHAR_INDEX_MIN",
    "DelimiterRunCache",
    "char_presence_index",
    "find_first_of",
]
