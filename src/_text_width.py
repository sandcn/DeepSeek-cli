"""文本显示宽度计算 — 单一真源（Layer 0，零依赖）。

背景（P1-2）：宽度计算此前存在 **三套独立实现**——``src.tui._width``
（TUI 框架的 ``wcswidth_simple``）、``src/renderer/_utils/_display``
（内容渲染的 ``cjk_display_width``）、``src/config/view_model``
（配置视图的 ``_display_width``）。三者区间表靠「注释同源约束」手工同步，
多次出现漂移（历史 H1/BUG-25/双宽度对齐修复均源于此），且 renderer/config
层无法依赖 tui 层（架构分层约束）。

本模块把**码点宽度语义**收敛为单一真源（区间表 + 判定函数），供上述三层
共同依赖——三层各自保留各自的公开函数名（``wcswidth_simple`` /
``cjk_display_width`` / ``_display_width``）作为薄委托，对外行为接口不变。

区间表口径（终端显示列）：
  - ASCII 可打印 ``0x20-0x7E`` → 1
  - 控制字符 ``0x00-0x1F`` / ``0x7F-0x9F`` → 0（含制表符，展开由调用方负责）
  - CJK / 全角 / emoji 宽符号 → 2
  - 组合标记 / 零宽字符 → 0
  - 其他 → 1
  - ANSI 转义序列（整段） → 0（``string_width`` 内联跳过）

依赖约束：本模块为 Layer 0，仅依赖标准库（``bisect``/``re``），
被 ``src.tui._width`` / ``src.renderer._utils._display`` /
``src.config.view_model`` 依赖，不得反向依赖任何上层模块。
"""

from __future__ import annotations

import bisect
import re

# ═══════════════════════════════════════════════════════════
# 区间表（码点 → 显示宽度分类）
# ═══════════════════════════════════════════════════════════

#: CJK / 全角宽字符区间（宽度 2）。
CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),    # Hangul Jamo（韩文辅音/元音字母）
    (0x2E80, 0x2FFF),    # CJK Radicals Supplement + Kangxi Radicals
    (0x3000, 0x303F),    # CJK Symbols and Punctuation（全角标点 、。「」）
    (0x3040, 0x33FF),    # Hiragana/Katakana/Bopomofo/CJK Compatibility
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0xAC00, 0xD7AF),    # Hangul Syllables（韩文音节）
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x20000, 0x2FFFF),  # CJK Ext B-F + 兼容表意补充（0x2F800-0x2FA1F）
    (0x30000, 0x3134F),  # CJK Unified Ideographs Extension G-H
)

#: 组合标记 / 零宽字符区间（宽度 0）。
ZERO_WIDTH_RANGES: tuple[tuple[int, int], ...] = (
    (0x0300, 0x036F),    # Combining Diacritical Marks
    (0x1AB0, 0x1AFF),    # Combining Diacritical Marks Extended
    (0x1DC0, 0x1DFF),    # Combining Diacritical Marks Supplement
    (0x20D0, 0x20FF),    # Combining Diacritical Marks for Symbols
    (0x200C, 0x200D),    # ZWNJ/ZWJ（零宽连接符）
    (0xFE00, 0xFE0F),    # Variation Selectors
    (0xFE20, 0xFE2F),    # Combining Half Marks
    (0xE0100, 0xE01EF),  # Variation Selectors Supplement
    (0x00AD, 0x00AD),    # SOFT HYPHEN
    (0x200B, 0x200B),    # ZERO WIDTH SPACE
    (0x200E, 0x200F),    # LRM/RLM
    (0x2060, 0x2064),    # Word Joiner / Zero-width no-break etc.
    (0xFEFF, 0xFEFF),    # ZERO WIDTH NO-BREAK SPACE / BOM
)

#: 全角字符区间（宽度 2；非 CJK 的全角形式）。
FULLWIDTH_RANGES: tuple[tuple[int, int], ...] = (
    (0xFF01, 0xFF60),    # Fullwidth Forms
    (0xFFE0, 0xFFE6),    # Fullwidth Signs
)

#: Emoji 宽符号区间（终端以 2 列渲染）。
#: ⚠ 不含 ✔✎⚙✕ 等文本呈现符号（宽度 1）——误计为 2 会导致表格/布局错位。
#: Regional Indicator（RI，0x1F1E6-0x1F1FF，国旗字母）排除在外：单 RI 计 1、
#: 成对 RI（国旗）按 1×2=2 列（与主流 wcwidth 一致）。
EMOJI_WIDE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1F1E5),   # 主要 emoji 块（📖📄🔍 等；不含 RI 码点）
    (0x1F200, 0x1FAFF),   # 主要 emoji 块续（🈁 等；RI 码点已排除）
    (0x231A, 0x231B),     # ⌚⏳
    (0x23E9, 0x23EC),     # ⏩⏪⏫⏬
    (0x23F0, 0x23F0),     # ⏰
    (0x23F3, 0x23F3),     # ⏳
    (0x25FD, 0x25FE),     # ◽◾
    (0x2614, 0x2615),     # ☔☕
    (0x2648, 0x2653),     # 星座
    (0x267F, 0x267F),     # ♿
    (0x2693, 0x2693),     # ⚓
    (0x26A1, 0x26A1),     # ⚡（shell 工具图标）
    (0x26AA, 0x26AB),     # ⚪⚫
    (0x26BD, 0x26BE),     # ⚽⚾
    (0x26C4, 0x26C5),     # ⛄⛅
    (0x26CE, 0x26CE),     # ⛎
    (0x26D4, 0x26D4),     # ⛔
    (0x26EA, 0x26EA),     # ⛪
    (0x26F2, 0x26F3),     # ⛲⛳
    (0x26F5, 0x26F5),     # ⛵
    (0x26FA, 0x26FA),     # ⛺
    (0x26FD, 0x26FD),     # ⛽
    (0x2705, 0x2705),     # ✅
    (0x270A, 0x270B),     # ✊✋
    (0x2728, 0x2728),     # ✨
    (0x274C, 0x274C),     # ❌
    (0x274E, 0x274E),     # ❎
    (0x2753, 0x2755),     # ❓❔❕（user_select 图标 ❓）
    (0x2757, 0x2757),     # ❗
    (0x2795, 0x2797),     # ➕➖➗
    (0x27B0, 0x27B0),     # ➰
    (0x27BF, 0x27BF),     # ➿
    (0x2B1B, 0x2B1C),     # ⬛⬜
    (0x2B50, 0x2B50),     # ⭐
    (0x2B55, 0x2B55),     # ⭕
)


def _build_flat_ranges(
    ranges: "list[tuple[int, int]] | tuple[tuple[int, int], ...]",
) -> list[int]:
    """构造区间表的扁平排序边界数组（每个区间起点/终点后一位，供 bisect 定位）。

    二分正确性依赖「有序不重叠」不变式——实现时显式排序 + 断言（区间表声明为
    有序不重叠；断言保护排序不变式，防后续误改区间表破坏二分）。

    Args:
        ranges: 区间列表（``[(lo, hi), ...]``；顺序无关，内部排序）。

    Returns:
        扁平的 ``[lo0, hi0+1, lo1, hi1+1, ...]`` 数组。

    Raises:
        AssertionError: 区间非法（``lo > hi``）或区间重叠/乱序。
    """
    ordered = sorted(ranges, key=lambda r: r[0])
    flat: list[int] = []
    prev_end = -1
    for lo, hi in ordered:
        assert lo <= hi, f"区间非法: ({lo}, {hi})"
        assert lo > prev_end, f"区间表重叠/乱序: ({lo}, {hi}) 与前一区间 {prev_end}"
        flat.append(lo)
        flat.append(hi + 1)
        prev_end = hi
    return flat


def _in_ranges_bisect(cp: int, flat: list[int]) -> bool:
    """二分定位码点是否落在区间内（flat 为起点/终点后一位交替数组）。

    区间 ``[lo, hi]`` 展开为 ``lo, hi+1`` 两个边界；``bisect_right`` 返回
    第一个 ``> cp`` 的边界索引——索引为奇数 ⇒ cp 落在某区间内（O(log n)）。
    """
    idx = bisect.bisect_right(flat, cp)
    return (idx % 2) == 1


#: 预计算的排序扁平边界表（热路径二分用）
CJK_FLAT: list[int] = _build_flat_ranges(CJK_RANGES)
FULLWIDTH_FLAT: list[int] = _build_flat_ranges(FULLWIDTH_RANGES)
EMOJI_WIDE_FLAT: list[int] = _build_flat_ranges(EMOJI_WIDE_RANGES)
ZERO_WIDTH_FLAT: list[int] = _build_flat_ranges(ZERO_WIDTH_RANGES)

#: ASCII 可打印连续段正则（``string_width`` 多字符路径快速跳过——C 实现
#: 扫描比逐字符 Python ``ord()`` + 比较快 ~2x；混合文本热路径收益明显）。
#: 匹配 ``[\x20-\x7e]``（与 ASCII 分支宽度 1 一致）；``\x7f``（DEL，宽 0）与
#: ``\x1b``（ESC）不在其中，正确走各自分支。
_ASCII_RUN_RE = re.compile(r"[\x20-\x7e]+")


def skip_ansi_at(text: str, i: int) -> int:
    """跳过从 ``text[i]``（\\x1b）开始的完整 ANSI 转义序列，返回序列后索引。

    支持三类（与 ``src.tui.ink._ansi_utils._ANSI_RE`` 匹配范围对齐）：
      - CSI：``\\x1b[`` + 参数中间字节(0x20-0x3F) + 最终字节(0x40-0x7E)
      - OSC：``\\x1b]`` + 内容 + BEL(\\x07) 或 ST(``\\x1b\\\\``)
      - 单字符控制：``\\x1b`` + Fe 终字节(0x40-0x5F)

    残缺/嵌套序列安全跳过（不抛异常）：已消费的合法前缀返回，孤立 ESC 仅
    跳过 ESC 本身——宽度测量场景整段计宽 0。

    Args:
        text: 输入字符串。
        i: ``\\x1b`` 所在索引。

    Returns:
        跳过序列后的下一个索引（保证 > i，不越界）。
    """
    n = len(text)
    if i >= n or text[i] != "\x1b":
        return i + 1
    j = i + 1
    if j >= n:
        return j
    c = text[j]
    if c == "[":
        # CSI：参数中间字节 0x20-0x3F（数字/分号/冒号/问号/空格）+ 最终字节
        # 0x40-0x7E（@A-Z[\]^_`a-z{|}~）——覆盖真彩冒号格式与终端键序列。
        k = j + 1
        while k < n and 0x20 <= ord(text[k]) <= 0x3F:
            k += 1
        if k < n and 0x40 <= ord(text[k]) <= 0x7E:
            return k + 1
        return k  # 残缺 CSI：跳过已消费参数
    if c == "]":
        # OSC：\x1b] ... (BEL 或 ST)
        k = j + 1
        while k < n and text[k] not in ("\x07", "\x1b"):
            k += 1
        if k < n and text[k] == "\x07":
            return k + 1
        if k < n and text[k] == "\x1b" and k + 1 < n and text[k + 1] == "\\":
            return k + 2
        return k  # 残缺 OSC
    # 单字符控制序列：\x1bX（X 为 Fe 终字节 0x40-0x5F）
    if 0x40 <= ord(c) <= 0x5F:
        return j + 1
    return j  # 孤立 ESC：仅跳过 ESC 本身


def codepoint_width(cp: int) -> int:
    """码点显示宽度（1/2/0；不含 ANSI/控制字符特殊分支）。

    ★ 与 ``char_width`` 的分工：本函数面向纯码点（无 ``str`` 构造），供
    区间表直查场景复用；控制字符（``cp < 0x20`` / ``0x7F-0x9F``）经
    ``char_width`` 判定为 0，本函数不重复处理（调用方应已过滤）。
    """
    if 0x20 <= cp <= 0x7E:
        return 1
    if _in_ranges_bisect(cp, CJK_FLAT):
        return 2
    if _in_ranges_bisect(cp, FULLWIDTH_FLAT):
        return 2
    if _in_ranges_bisect(cp, EMOJI_WIDE_FLAT):
        return 2
    if _in_ranges_bisect(cp, ZERO_WIDTH_FLAT):
        return 0
    return 1


def char_width(ch: str) -> int:
    """单个字符的显示宽度（含控制字符 / 孤立 ESC 的 0 宽判定）。

    控制字符（``0x00-0x1F``，含制表符 ``\\t``）与 ``0x7F-0x9F`` 宽度 0
    （制表符展开由调用方经 ``expand_tabs`` 负责）；孤立 ESC（``\\x1b``）
    宽度 0（与 ``skip_ansi_at`` 语义一致）。
    """
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E:
        return 1
    if ch == "\x1b" or cp < 0x20 or (0x7F <= cp <= 0x9F):
        return 0
    return codepoint_width(cp)


def string_width(text: str) -> int:
    """字符串显示宽度（跳过 ANSI 转义序列；控制字符不计宽）。

    规则（与各调用层既有口径一致，收敛为唯一实现）：
      - 纯可打印 ASCII 快速路径（宽度 == 字符数）；
      - ANSI 转义序列（``\\x1b`` 起始）整段宽度 0；
      - 控制字符（``< 0x20`` / ``0x7F-0x9F``，含 ``\\t``）宽度 0；
      - CJK/全角/emoji 宽 2、零宽 0、其他 1。

    Args:
        text: 输入字符串。

    Returns:
        显示宽度（整数）。
    """
    if text.isascii() and text.isprintable():
        # C 实现单趟扫描（比逐字符 Python 循环快数倍）——纯 ASCII 可打印
        # 文本宽度 == 字符数（无 ANSI/控制/零宽/宽字符）。
        return len(text)
    width = 0
    i = 0
    n = len(text)
    while i < n:
        m = _ASCII_RUN_RE.match(text, i)
        if m is not None:
            width += m.end() - i
            i = m.end()
            continue
        cp = ord(text[i])
        if text[i] == "\x1b":
            i = skip_ansi_at(text, i)
        elif cp < 0x20 or (0x7F <= cp <= 0x9F):
            i += 1  # 控制字符（含 \t）宽度 0
        elif _in_ranges_bisect(cp, CJK_FLAT):
            width += 2
            i += 1
        elif _in_ranges_bisect(cp, FULLWIDTH_FLAT):
            width += 2
            i += 1
        elif _in_ranges_bisect(cp, EMOJI_WIDE_FLAT):
            width += 2
            i += 1
        elif _in_ranges_bisect(cp, ZERO_WIDTH_FLAT):
            i += 1
        else:
            width += 1
            i += 1
    return width


def zero_width_codepoints() -> frozenset:
    """零宽码点集合（``ZERO_WIDTH_RANGES`` 全展开为 ``frozenset``）。

    供 ``renderer._utils`` 的 ``_ZERO_WIDTH_CHARS`` 兼容导出使用（历史实现
    为手工单点集合 + 组合标记区间展开，本函数统一由区间表派生，杜绝两处
    零宽集合漂移）。
    """
    cps: set[int] = set()
    for lo, hi in ZERO_WIDTH_RANGES:
        cps.update(range(lo, hi + 1))
    return frozenset(cps)


def expand_tabs(text: str, start_col: int = 0, tab_width: int = 8) -> str:
    """展开制表符为空格并剔除回车（使显示宽度与终端渲染一致）。

    制表符 ``\\t`` 是控制字符（``char_width`` 计宽 0），但真实终端把它当 HT
    跳到下一个 tab stop（默认每 8 列补空格）——含 ``\\t`` 的行「计算宽度 <
    实际渲染宽度」，配合「整行占满终端宽度」的填充会触发终端自动换行，后续
    行错位。本函数在文本进入渲染模型前把 ``\\t`` 只展开一次，之后宽度计算
    与实际渲染恒一致。回车 ``\\r`` 一并剔除（重置列）；``\\n`` 保留并把列
    基准重置到 ``start_col``。

    Args:
        text: 待规范化文本。
        start_col: 文本起始显示列（默认 0；制表位按 ``col % tab_width`` 对齐）。
        tab_width: 制表宽度（列；<=0 回退 8）。

    Returns:
        展开后的文本（不含 ``\\t``/``\\r``；无该字符时原样返回）。
    """
    if "\t" not in text and "\r" not in text:
        return text
    if tab_width <= 0:
        tab_width = 8
    out: list[str] = []
    col = start_col
    for ch in text:
        if ch == "\t":
            n = tab_width - (col % tab_width)
            out.append(" " * n)
            col += n
        elif ch == "\r":
            col = start_col
        elif ch == "\n":
            out.append(ch)
            col = start_col
        else:
            out.append(ch)
            col += char_width(ch)
    return "".join(out)


__all__ = [
    "CJK_RANGES",
    "ZERO_WIDTH_RANGES",
    "FULLWIDTH_RANGES",
    "EMOJI_WIDE_RANGES",
    "CJK_FLAT",
    "FULLWIDTH_FLAT",
    "EMOJI_WIDE_FLAT",
    "ZERO_WIDTH_FLAT",
    "_build_flat_ranges",
    "_in_ranges_bisect",
    "_ASCII_RUN_RE",
    "skip_ansi_at",
    "codepoint_width",
    "char_width",
    "string_width",
    "zero_width_codepoints",
    "expand_tabs",
]
