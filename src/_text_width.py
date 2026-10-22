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
from collections import Counter

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

    二分正确性依赖「有序不重叠」不变式——实现时显式排序 + 校验（区间表声明为
    有序不重叠；校验保护排序不变式，防后续误改区间表破坏二分）。

    Args:
        ranges: 区间列表（``[(lo, hi), ...]``；顺序无关，内部排序）。

    Returns:
        扁平的 ``[lo0, hi0+1, lo1, hi1+1, ...]`` 数组。

    Raises:
        ValueError: 区间非法（``lo > hi``）或区间重叠/乱序。
            ★ 用显式 raise 而非 ``assert``：``python -O`` 会剥离 assert，
            而宽度区间表是二分查找的正确性根基，不变式被破坏必须立刻失败。
    """
    ordered = sorted(ranges, key=lambda r: r[0])
    flat: list[int] = []
    prev_end = -1
    for lo, hi in ordered:
        if lo > hi:
            raise ValueError(f"区间非法: ({lo}, {hi})")
        if lo <= prev_end:
            raise ValueError(f"区间表重叠/乱序: ({lo}, {hi}) 与前一区间 {prev_end}")
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


#: 单字符宽度缓存（有界）：超长行换行/截断逐字符测宽时，同一字符（CJK/emoji）
#: 免重复区间二分。ASCII（宽度 1）与控制字符（宽度 0）走算术快路径不经缓存
#: ——dict 查找对它们反而更慢。超上限整体清空（宽度值确定性，正确性不受影响）。
CHAR_WIDTH_CACHE_MAX = 4096
_CHAR_WIDTH_CACHE: dict[str, int] = {}

#: ``string_width`` 的「计数字符宽度」快速路径启用阈值（字符数）：长文本用
#: ``Counter``（C 级计数）+ 每唯一字符一次 ``char_width`` 代替逐字符 Python
#: 循环。短文本下 ``Counter`` 的建表开销不划算，仍走逐字符路径。
_STRING_WIDTH_COUNTER_MIN = 64


def char_width(ch: str) -> int:
    """单个字符的显示宽度（含控制字符 / 孤立 ESC 的 0 宽判定）。

    控制字符（``0x00-0x1F``，含制表符 ``\\t``）与 ``0x7F-0x9F`` 宽度 0
    （制表符展开由调用方经 ``expand_tabs`` 负责）；孤立 ESC（``\\x1b``）
    宽度 0（与 ``skip_ansi_at`` 语义一致）。

    ★ 性能（超长单行）：非 ASCII 字符经有界缓存返回——``wrap_line`` /
    ``truncate_line`` 对超长行逐字符测宽是流式渲染热路径，修复前每次
    重建 ``code_point → width`` 区间二分（并触发一次正则 match）。
    """
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E:
        return 1
    if ch == "\x1b" or cp < 0x20 or (0x7F <= cp <= 0x9F):
        return 0
    w = _CHAR_WIDTH_CACHE.get(ch)
    if w is not None:
        return w
    w = codepoint_width(cp)
    if len(_CHAR_WIDTH_CACHE) >= CHAR_WIDTH_CACHE_MAX:
        _CHAR_WIDTH_CACHE.clear()
    _CHAR_WIDTH_CACHE[ch] = w
    return w


#: 需要按「字形簇」合并计算的码点：ZWJ（emoji 组合）、keycap、肤色修饰符、
#: 标签序列（旗帜）。组合标记本身宽度 0，逐码点求和已与字形簇宽度一致，
#: 无需进入簇路径。
def _is_extend_cp(cp: int) -> bool:
    """码点是否附着在前一个基字符上（组合标记 / VS / ZWJ / 肤色修饰符）。"""
    if cp == 0x200D or cp == 0x20E3:
        return True
    if 0x1F3FB <= cp <= 0x1F3FF:   # emoji skin tone modifiers
        return True
    if 0xFE00 <= cp <= 0xFE0F:     # variation selectors
        return True
    if 0xE0100 <= cp <= 0xE01EF:   # variation selectors supplement
        return True
    if 0xE0020 <= cp <= 0xE007F:   # tag characters（旗帜序列）
        return True
    return _in_ranges_bisect(cp, ZERO_WIDTH_FLAT)


#: 需要按字形簇整体处理的码点（C 级正则单趟判定）：
#:   - ZWJ（U+200D）/ keycap 组合符（U+20E3）；
#:   - Regional Indicator（国旗字母）：成对构成国旗（宽 2），拆开会变两个字母；
#:   - 肤色修饰符（U+1F3FB-U+1F3FF）：附着在基 emoji 上，单独逐字符计宽为 2；
#:   - 标签字符（U+E0020-U+E007F）：旗帜等标签序列。
_GRAPHEME_SPECIAL_RE = re.compile(
    r"[\u200d\u20e3\U0001F1E6-\U0001F1FF\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F]"
)


def _has_grapheme_special(text: str) -> bool:
    """文本是否含「必须按字形簇计算宽度」的码点（ZWJ / keycap / 肤色 / 标签 / RI）。"""
    if text.isascii():
        # ASCII 文本（绝大多数热路径）：不含任何特殊码点，C 级单趟判定直接返回。
        return False
    return _GRAPHEME_SPECIAL_RE.search(text) is not None


def _is_emoji_base_cp(cp: int) -> bool:
    """码点是否可作为 ZWJ 组合 emoji 的基字符（UAX #29 Extended_Pictographic 近似）。

    ZWJ 只连接 emoji（``👨‍👩‍👧``），不能吞并 CJK / 普通文字——修复前只要后一
    字符不是 ASCII 就无条件吞并（``"👨‍中"`` 被当成单簇宽 2，实际终端宽度 4）。
    范围取常见 emoji 块 + 少量 BMP 符号；未列出的符号按普通字符处理（宽度
    计算保守，不会低估）。
    """
    if 0x1F000 <= cp <= 0x1FAFF:   # 表情、手势、动物、国旗、区域指示符
        return True
    if 0x2600 <= cp <= 0x27BF:     # 杂项符号 / 装饰符号
        return True
    if 0x2B00 <= cp <= 0x2BFF:     # 杂项符号和箭头
        return True
    if 0x2190 <= cp <= 0x21FF:     # 箭头
        return True
    if 0x2300 <= cp <= 0x23FF:     # 杂项技术符号（⌚/⏰ 等）
        return True
    return cp in (
        0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
        0x3030, 0x303D, 0x3297, 0x3299,
    )


def grapheme_clusters(text: str):
    """按字形簇切分文本（简化 UAX #29：组合标记 / ZWJ / 肤色 / RI 对）。

    终端把 ``👨‍👩‍👧‍👦`` 渲染为一个宽度 2 的字形；逐码点切分会在换行/截断时
    把 ZWJ 序列拦腰截断（出现散落的家庭成员 emoji）。本函数把基字符与
    其后的 Extend / ZWJ + 下一基字符归为同一簇，供换行/截断/测宽使用。
    """
    n = len(text)
    i = 0
    while i < n:
        start = i
        cp = ord(text[i])
        i += 1
        if cp < 0x20 or 0x7F <= cp <= 0x9F:
            # UAX #29 GB4：Control（含 \t \n \r）之后必须断开——修复前制表符
            # 会吸附后续组合标记成为 "\t\u0301" 单簇，``expand_tabs`` 的簇
            # 路径只识别独立 "\t"，字面制表符漏展开进入渲染（宽度与实际
            # 终端列错位）；换行吸附肤色修饰符亦会让断行后的簇元信息失效。
            yield text[start:i]
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:
            # Regional Indicator：成对构成国旗（单 RI 仍是独立簇）。
            # 成对后继续走下方 Extend 吸收逻辑（与普通基字符一致）——
            # 修复前此处直接 yield，RI 后的 keycap/VS/ZWJ 成为孤立零宽
            # 簇，截断/换行可能在两簇之间切开（ellipsis 与孤立 keycap
            # 拼接后宽度被误计为 2）。
            if i < n and 0x1F1E6 <= ord(text[i]) <= 0x1F1FF:
                i += 1
        while i < n:
            next_cp = ord(text[i])
            if not _is_extend_cp(next_cp):
                break
            i += 1
            if next_cp == 0x200D and i < n:
                # ZWJ 后必须吞掉下一个**emoji 基字符**，保持组合 emoji 完整。
                # 控制字符 / ASCII / CJK / 普通文字不可能是 emoji 基字符——
                # 无条件吞并会让 ``"👨‍中"`` 被当成单簇宽 2（实际宽 4）。
                nxt = ord(text[i])
                if not _is_emoji_base_cp(nxt):
                    break
                i += 1
                # 继续吞掉该基字符上的修饰符（VS / 肤色）
                while i < n and _is_extend_cp(ord(text[i])) \
                        and ord(text[i]) != 0x200D:
                    i += 1
        yield text[start:i]


def needs_grapheme_clusters(text: str) -> bool:
    """文本是否含必须按字形簇整体处理的码点。

    供换行/截断/绘制等布局路径选择「逐字符」快路径或「字形簇」原子路径：
    ZWJ 序列、keycap、肤色修饰符、标签序列必须原子处理；不满足时逐字符
    宽度求和与字形簇宽度一致（组合标记宽 0、国旗 RI 单字符宽 1 成对宽 2）。
    """
    return _has_grapheme_special(text)


def grapheme_spans(text: str):
    """按字形簇切分并返回 ``(start, end, cluster)``（字符索引区间，左闭右开）。

    与 ``grapheme_clusters`` 同一实现（单一真源），额外携带字符偏移，供
    需要保留样式/列位置信息的布局路径（换行、截断、画布绘制）使用。
    """
    pos = 0
    for cluster in grapheme_clusters(text):
        end = pos + len(cluster)
        yield pos, end, cluster
        pos = end


def cluster_width(cluster: str) -> int:
    """字形簇的显示宽度（ZWJ 组合 / 肤色 / keycap / 国旗按终端实际宽度）。"""
    if not cluster:
        return 0
    if len(cluster) == 1:
        return char_width(cluster)
    cps = [ord(c) for c in cluster]
    # ★ 修复（2026-10-23 画布列键往返丢字）：ZWJ 只有**后跟 emoji 基字符**时
    #   才构成组合 emoji（👨‍👩‍👦 宽 2）。孤立/尾随 ZWJ（如 ``"1\\u200d"``——
    #   ``grapheme_clusters`` 在 ZWJ 后不是 emoji 基字符时按 UAX #29 在此断簇）
    #   只是零宽格式符；修复前任何含 ZWJ 的簇都返回 2 → ``wcswidth_simple``
    #   把 ``"1\\u200d"`` 计为 2 列：画布 ``_put_char`` 合并零宽时列不推进，而
    #   ``_canvas_row_to_line`` 按 ``wcswidth_simple(条目)`` 推导列宽 → 列键
    #   往返丢字符（``Line._w`` 同步虚高）。emoji ZWJ 序列语义保持不变。
    for idx, cp in enumerate(cps):
        if cp == 0x200D and idx + 1 < len(cps) and _is_emoji_base_cp(cps[idx + 1]):
            return 2
    if any(cp == 0x20E3 for cp in cps):
        # keycap 组合（``1️⃣`` = 数字/#/* + VS16 + U+20E3）宽 2；U+20E3
        # 附着在非 keycap 基字符（如 ``…``）上时只是零宽组合符，宽度按
        # 基字符计算（修复前无条件返回 2：ellipsis 与孤立 keycap 拼接后
        # 被计为 2 列，截断结果超宽）。
        base = cps[0]
        if 0x30 <= base <= 0x39 or base in (0x23, 0x2A):
            return 2
    if any(0x1F3FB <= cp <= 0x1F3FF for cp in cps):
        return 2
    if any(0xE0020 <= cp <= 0xE007F for cp in cps):
        return 2
    if any(0x1F1E6 <= cp <= 0x1F1FF for cp in cps):
        return 2
    first = char_width(cluster[0])
    if first:
        return first
    return max((char_width(c) for c in cluster), default=0)


def string_width(text: str) -> int:
    """字符串显示宽度（跳过 ANSI 转义序列；控制字符不计宽）。

    规则（与各调用层既有口径一致，收敛为唯一实现）：
      - 纯可打印 ASCII 快速路径（宽度 == 字符数）；
      - 无 ANSI 转义序列的长文本走**计数字符宽度**路径（``Counter`` 一次
        C 级计数 + 每个唯一字符一次 ``char_width``，见下）；
      - ANSI 转义序列（``\\x1b`` 起始）整段宽度 0；
      - 控制字符（``< 0x20`` / ``0x7F-0x9F``，含 ``\\t``）宽度 0；
      - CJK/全角/emoji 宽 2、零宽 0、其他 1。

    ★ 性能（超长单行）：长文本（≥ ``_STRING_WIDTH_COUNTER_MIN`` 字符）且
    不含 ESC 时，用 ``collections.Counter``（C 级计数）统计唯一字符出现次数，
    再对每个唯一字符调一次 ``char_width`` 求和——超长行换行/截断/渲染热路径
    此前对每个字符做一次 Python 层 ``ord`` + 字典查找，4096 字符 CJK 行实测
    ~2.6ms，改后为一次 C 级计数 + 少量字典查找（~0.2ms）。无 ESC 时「Σ计数×
    字符宽度」与逐字符路径**严格等价**（控制字符由 ``char_width`` 返回 0、
    ASCII 段返回 1，均与逐字符分支一致）；含 ESC 时 ANSI 序列需整段计 0，
    帧内出现概率低，回退逐字符路径。

    Args:
        text: 输入字符串。

    Returns:
        显示宽度（整数）。
    """
    if text.isascii() and text.isprintable():
        # C 实现单趟扫描（比逐字符 Python 循环快数倍）——纯 ASCII 可打印
        # 文本宽度 == 字符数（无 ANSI/控制/零宽/宽字符）。
        return len(text)
    if _has_grapheme_special(text):
        # ZWJ / 肤色 / keycap / 标签序列：按字形簇测宽（终端把组合 emoji
        # 渲染为一个字形；逐码点求和会高估 2~8 倍，导致布局错位/提前换行）。
        # 含 ANSI 时按转义序列切段，各段独立按簇测宽（修复前整串回退逐
        # 字符路径 → 组合 emoji 被高估，如 ``\x1b[31m👨‍👩‍👧\x1b[0m`` 计 6 列）。
        if "\x1b" not in text:
            return sum(cluster_width(c) for c in grapheme_clusters(text))
        total = 0
        i = 0
        n = len(text)
        segment_start = 0
        while i < n:
            if text[i] != "\x1b":
                i += 1
                continue
            if segment_start < i:
                total += sum(
                    cluster_width(c)
                    for c in grapheme_clusters(text[segment_start:i])
                )
            i = skip_ansi_at(text, i)
            segment_start = i
        if segment_start < n:
            total += sum(
                cluster_width(c)
                for c in grapheme_clusters(text[segment_start:])
            )
        return total
    if len(text) >= _STRING_WIDTH_COUNTER_MIN and "\x1b" not in text:
        # 无 ANSI：Σ（唯一字符出现次数 × 字符宽度）——等价于逐字符求和。
        return sum(cnt * char_width(ch) for ch, cnt in Counter(text).items())
    cache = _CHAR_WIDTH_CACHE
    width = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            # 连续可打印 ASCII 段：正则（C 实现）一次吃掉整段
            m = _ASCII_RUN_RE.match(text, i)
            if m is not None:
                end = m.end()
                width += end - i
                i = end
                continue
            width += 1
            i += 1
        elif ch == "\x1b":
            i = skip_ansi_at(text, i)
        elif cp < 0x20 or (0x7F <= cp <= 0x9F):
            i += 1  # 控制字符（含 \t）宽度 0
        else:
            # 非 ASCII：走 char_width 的有界缓存（内联查找省一次函数调用——
            # 长 CJK 行测宽是换行/截断/布局热路径，每帧对同一批字符反复测宽）。
            w = cache.get(ch)
            if w is None:
                w = char_width(ch)
            width += w
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
    # ★ 字形簇 / ANSI 感知：制表位列宽必须按终端实际列宽推进——逐码点
    #   ``char_width`` 会把 ZWJ 组合 emoji 计成 2×成员数（如 👨‍👩‍👧 计 6 列，
    #   实际 2 列）、把 ANSI 序列正文计为可见字符，后续 ``\t`` 补齐空格数
    #   错误（与 ``string_width`` / ``_input_layout._expand_tabs`` 口径分裂）。
    if needs_grapheme_clusters(text) or "\x1b" in text:
        skip_until = 0
        for start, _end, cluster in grapheme_spans(text):
            if start < skip_until:
                continue
            if cluster == "\x1b":
                stop = skip_ansi_at(text, start)
                out.append(text[start:stop])
                skip_until = stop
                continue
            if cluster == "\t":
                n = tab_width - (col % tab_width)
                out.append(" " * n)
                col += n
            elif cluster == "\r":
                col = start_col
            elif cluster == "\n":
                out.append(cluster)
                col = start_col
            else:
                out.append(cluster)
                col += cluster_width(cluster)
        return "".join(out)
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
    "grapheme_clusters",
    "grapheme_spans",
    "needs_grapheme_clusters",
    "cluster_width",
    "zero_width_codepoints",
    "expand_tabs",
]
