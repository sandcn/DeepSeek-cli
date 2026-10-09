"""_line_delims — 段落行边界「未闭合行内定界符」增量跟踪。

流式段落预览每帧都要呈现当前未闭合段落。段落提交路径
（``blocks.render_paragraph``）把整段作为连续文本解析——跨软换行的行内标记
（``**粗体\\n跨行**``、`` `code\\ncode` ``）因此可以配对。预览若逐行独立解析
则会泄漏标记、与提交结果不一致（视觉跳变）。

修复前的判定（``AnsiStreamRenderer._paragraph_needs_multiline_inline``）过于
保守：多行段落只要含任一「行内标记起始字符」就整段解析——长段落流式期间每帧
O(整段) 重解析，累计 O(n²)（1200 行、12 万字符段落逐 16 字符写入实测 >150s）。

本模块提供精确判定：**只要段落各「完整行」的行边界处不存在未闭合的行内开
定界符，逐行解析就与整段解析等价**（行内解析顺序进行、不依赖未来文本），可走
行级增量缓存（``LinePreviewCache``）；否则从最早出现未闭合的行开始整段解析。

判定原则是**保守**：宁可多报「未闭合」（回退整段解析，仅损失性能），不误判为
闭合（会造成预览与提交不一致）。因此扫描规则覆盖解析器
（``src.renderer.inline_parser._InlineParser``）的全部成对定界符，允许与解析器
实现细节存在差异，但不漏掉任何可能的开定界符。

增量：按完整行做前缀 / 头部滑窗复用（``_line_match``），只扫描新出现的完整行，
并为每行保存扫描后的状态快照；活动行（最后一个未换行的行）不参与稳定行统计
（它每帧变化，由整段解析承担）。
"""

from __future__ import annotations

import re

from ._line_match import common_prefix_len, sliding_drop

#: 需要单独处理的行内定界符字符（正则仅用于「跳到下一个候选位置」，不做语义解析）
_SPECIAL_RE = re.compile(r"[\\`*_~=+|%$^\[\]{}<>()]")

#: 以「成对 toggle」跟踪的定界符（出现一个开/闭单元即翻转奇偶）
_TOGGLE_CHARS = frozenset("*_~=+|%$^")

#: 以「配对计数」跟踪的状态键（计数 > 0 表示存在未闭合开定界符）
_PAIR_KEYS = frozenset(("[", "{", "<", "lp", "mp", "code"))

#: 允许作为定界符的最小连续长度（单字符在普通文本中过常见，避免误报）
_MIN_RUN = {"=": 2, "|": 2, "%": 2, "+": 2}

#: CriticMarkup / 着色容器 / 小字文本的 ``{`` 起始前缀（与解析器 ``_try_critic_*`` 对齐）
_BRACE_PREFIXES = ("{++", "{--", "{==", "{~~", "{>>", "{-", "{+", "{color:")

#: toggle 定界符的键最大重复长度（``***`` 与更长连续同字符归并，保守不拆分）
_TOGGLE_KEY_MAX = 3


def _toggle_key(ch: str, k: int) -> str:
    """toggle 定界符键：``ch`` 重复 ``min(k, 3)`` 次（``**`` 与 ``*`` 分开计数）。

    解析器把 ``**``（粗体）与 ``*``（斜体）、``~~``（删除线）与 ``~``（下标）
    视为不同定界符；分开计数可识别 ``**a*`` 这类「两种定界符各自未闭合」的
    跨行形态（合并计数会因总数为偶而漏报）。
    """
    return ch * (k if k < _TOGGLE_KEY_MAX else _TOGGLE_KEY_MAX)


def _scan_line(line: str, state: dict) -> None:
    """扫描一行文本，就地更新未闭合定界符状态 ``state``。

    ``state`` 为跨行持续的字典：``_TOGGLE_CHARS`` 键存「已见定界符单元数的
    奇偶」（奇数 = 未闭合），``_PAIR_KEYS`` 键存「未配对的开始标记数」。

    实现用正则 ``search`` 跳到下一个候选字符——普通文本（无定界符）一次搜索
    即到行尾，成本与定界符数量成正比而非行长（长段落预览热路径）。
    """
    n = len(line)
    search = _SPECIAL_RE.search
    pos = 0
    while True:
        m = search(line, pos)
        if m is None:
            return
        i = m.start()
        ch = line[i]
        if ch == "\\":
            # 转义：``\(`` / ``\)`` 仍是行内数学开闭（解析器 ``_try_paren_math``）
            j = i + 1
            if j < n:
                nxt = line[j]
                if nxt == "(":
                    state["mp"] = state.get("mp", 0) + 1
                elif nxt == ")":
                    v = state.get("mp", 0)
                    if v > 0:
                        state["mp"] = v - 1
            pos = i + 2
            continue
        if ch == "`":
            k = 1
            while i + k < n and line[i + k] == "`":
                k += 1
            end = line.find("`" * k, i + k)
            if end < 0:
                # 未闭合代码段：其后内容全部处于代码内 → 标记未闭合并结束本行
                state["code"] = state.get("code", 0) + 1
                return
            pos = end + k
            continue
        if ch in _TOGGLE_CHARS:
            k = 1
            while i + k < n and line[i + k] == ch:
                k += 1
            pos = i + k
            if k < _MIN_RUN.get(ch, 1):
                continue
            if ch == "_":
                prev_ch = line[i - 1] if i > 0 else ""
                next_ch = line[pos] if pos < n else ""
                if prev_ch.isalnum() and next_ch.isalnum():
                    # 词内下划线（snake_case / foo__bar）不触发强调（与解析器
                    # ``_is_word_boundary_underscore`` 一致）
                    continue
            key = _toggle_key(ch, k)
            state[key] = state.get(key, 0) + 1
            continue
        if ch == "[":
            state["["] = state.get("[", 0) + 1
            pos = i + 1
            continue
        if ch == "]":
            v = state.get("[", 0)
            if v > 0:
                state["["] = v - 1
            if i + 1 < n and line[i + 1] == "(":
                # ``](`` 链接目标括号（跨行可能出现 ``[t](\\nurl)``）
                state["lp"] = state.get("lp", 0) + 1
                pos = i + 2
                continue
            # 跳过紧随的 ``[`` 或 ``(`` 以免重复计数（``](`` 已整体处理）
            pos = i + 1
            continue
        if ch == ")":
            v = state.get("lp", 0)
            if v > 0:
                state["lp"] = v - 1
            pos = i + 1
            continue
        if ch == "{":
            if any(line.startswith(p, i) for p in _BRACE_PREFIXES):
                state["{"] = state.get("{", 0) + 1
            pos = i + 1
            continue
        if ch == "}":
            v = state.get("{", 0)
            if v > 0:
                state["{"] = v - 1
            pos = i + 1
            continue
        if ch == "<":
            next_ch = line[i + 1] if i + 1 < n else ""
            if next_ch.isalpha() or next_ch in "/!?":
                state["<"] = state.get("<", 0) + 1
            pos = i + 1
            continue
        if ch == ">":
            v = state.get("<", 0)
            if v > 0:
                state["<"] = v - 1
            pos = i + 1
            continue
        pos = i + 1


def _snapshot_empty(snapshot) -> bool:
    """状态快照（``(键, 值)`` 序列）是否表示「行边界处无未闭合开定界符」。"""
    for k, v in snapshot:
        if k in _PAIR_KEYS:
            if v > 0:
                return False
        elif v & 1:
            return False
    return True


def _state_empty(state: dict) -> bool:
    """状态字典是否表示「行边界处无未闭合开定界符」。"""
    if not state:
        return True
    return _snapshot_empty(state.items())


class ParagraphBoundaryScanner:
    """增量跟踪段落文本「可在行边界安全逐行渲染」的完整行数。

    用法::

        scanner = ParagraphBoundaryScanner()
        stable = scanner.stable_line_count(content)   # 每帧调用（增量）

    ``content`` 为段落累积文本（最后一行通常无换行，属活动行，不计入稳定
    行）。返回值 ``k`` 表示前 ``k`` 个完整行（按 ``\\n`` 分隔）逐行解析与整段
    解析等价，可走行级增量缓存；第 ``k`` 行起需整段解析。

    增量与滑窗：按完整行做前缀复用（流式只追加）；预览有界化导致头部滑窗时
    按 ``_line_match.sliding_drop`` 检测重叠偏移，复用重叠区间的行与状态快照，
    仅扫描尾部新增行——避免每帧重扫整段（长段落预览热路径）。
    """

    __slots__ = ("_lines", "_states", "_first_unstable", "_last_nl", "_last_len")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """清空状态（段落闭合 / 预览清空 / 内容分歧时调用）。"""
        self._lines: list[str] = []
        self._states: list[tuple] = []
        # 第一个「行末仍存在未闭合开定界符」的行索引（-1 = 尚无）
        self._first_unstable = -1
        # 快速路径缓存：上次文本的「最后一个换行下标」与文本长度（-2/-1 表示无）
        self._last_nl = -2
        self._last_len = -1

    def stable_line_count(self, text: str) -> int:
        """返回可安全逐行渲染的完整行数（增量）。"""
        if not text:
            self.reset()
            return 0
        nl = text.rfind("\n")
        if nl < 0:
            return self._stable_count()
        # ★ 性能（流式追加热路径）：最后换行位置未变且文本只增长 → 完整行
        #   集合未变（新增内容全在活动行内），直接复用——免每帧 O(段落长度)
        #   的 ``text[:nl].split("\\n")`` 与 O(行数) 的前缀比较（长段落流式预览
        #   的主要成本）。段落切换伴随预览清空（``reset``）→ 缓存同步失效，
        #   不会跨段落误用。
        if nl == self._last_nl and len(text) >= self._last_len:
            return self._stable_count()
        complete = text[:nl].split("\n")
        lines = self._lines
        states = self._states
        if lines:
            common = common_prefix_len(lines, complete)
            if common < len(lines):
                drop = sliding_drop(lines, complete)
                if drop:
                    del lines[:drop]
                    del states[:drop]
                    self._shift_first_unstable(drop)
                    common = common_prefix_len(lines, complete)
                if common < len(lines):
                    del lines[common:]
                    del states[common:]
                    self._recompute_first_unstable()
        state = dict(states[-1]) if states else {}
        for idx in range(len(lines), len(complete)):
            line = complete[idx]
            _scan_line(line, state)
            lines.append(line)
            states.append(tuple(state.items()))
            if self._first_unstable < 0 and not _state_empty(state):
                self._first_unstable = idx
        self._last_nl = nl
        self._last_len = len(text)
        return self._stable_count()

    def _stable_count(self) -> int:
        first = self._first_unstable
        return first if first >= 0 else len(self._lines)

    def _shift_first_unstable(self, drop: int) -> None:
        """头部丢弃 ``drop`` 行后调整首个不稳定行索引（需重算时重算）。"""
        first = self._first_unstable
        if first < 0:
            return
        if first >= drop:
            self._first_unstable = first - drop
        else:
            self._recompute_first_unstable()

    def _recompute_first_unstable(self) -> None:
        for i, snapshot in enumerate(self._states):
            if not _snapshot_empty(snapshot):
                self._first_unstable = i
                return
        self._first_unstable = -1


__all__ = ["ParagraphBoundaryScanner"]
