"""字符显示宽度计算 — 零第三方依赖（从 _screen.py 拆分，方向：模块边界优化）。

职责：终端字符显示宽度的纯计算（CJK/全角/Emoji/零宽字符/ANSI 序列跳过/
ASCII 快速路径/单字符缓存）。从 ``_screen.py`` 拆分独立，使屏幕管理模块
聚焦终端 I/O（尺寸查询/ANSI 序列/SIGWINCH），本模块专注显示宽度测量。

与 ``renderer/_utils/_display.cjk_display_width`` 双宽度函数对齐（注释同源
约束——两者共享同一套区间表语义，改动须同步）。

Layer 0 — 零依赖（仅标准库 bisect/re），被 _screen 及 ink 框架消费。
"""

from __future__ import annotations

import bisect
import re

# ═══════════════════════════════════════════════════════════
# 区间表（码点 → 显示宽度分类）
# ═══════════════════════════════════════════════════════════

_CJK_RANGES: list[tuple[int, int]] = [
    # ★ CJK 符号标点区（、。「」〈〉【】等，U+3000-U+303F）——修复前缺失：
    #   "。"、"、" 等全角标点被误算宽度 1（实际 2），导致行宽测量偏小 →
    #   内容实际超宽触发终端 wraparound → 渲染错乱（user_select 弹窗按键
    #   导航复现）。与 ``renderer/_utils/_display.cjk_display_width`` 的
    #   ``0x2E80-0x9FFF`` 块对齐（双宽度函数一致，注释同源约束）。
    (0x3000, 0x303F),    # CJK Symbols and Punctuation（全角标点宽度 2）
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
    (0x20000, 0x2CEAF),  # CJK Unified Ideographs Extension B-F
    (0x30000, 0x3134F),  # CJK Unified Ideographs Extension G-H
]

# 组合标记/零宽字符范围
_ZERO_WIDTH_RANGES: list[tuple[int, int]] = [
    (0x0300, 0x036F),   # Combining Diacritical Marks
    (0x1AB0, 0x1AFF),   # Combining Diacritical Marks Extended
    (0x1DC0, 0x1DFF),   # Combining Diacritical Marks Supplement
    (0x20D0, 0x20FF),   # Combining Diacritical Marks for Symbols
    (0x200C, 0x200D),   # ZWNJ/ZWJ（零宽连接符）——emoji ZWJ 序列按各组件宽度累加
    (0xFE00, 0xFE0F),   # Variation Selectors
    (0xFE20, 0xFE2F),   # Combining Half Marks
    (0xE0100, 0xE01EF), # Variation Selectors Supplement
    # ★ BUG-25（review 方向，双宽度函数对齐）：对齐 renderer/_utils/_display
    #   ``cjk_display_width`` 的零宽字符集——修复前这些字符在 ink 侧
    #   （``wcswidth_simple``）计 1、在 renderer 侧（``cjk_display_width``）
    #   计 0：``_block_to_ink_lines`` 的 wrap 判断用 ``wrap_line``（cjk 测宽
    #   ≤ width 不 wrap）而 committed 行渲染宽度按 ``wcswidth_simple``（=
    #   width+1）→ 破坏行级 diff 宽度不变量（BOM 文件/软连字符/零宽空格等
    #   含零宽字符的行超宽）。加入后两函数一致（与 wcwidth 语义对齐）。
    (0x00AD, 0x00AD),   # SOFT HYPHEN
    (0x200B, 0x200B),   # ZERO WIDTH SPACE
    (0x200E, 0x200F),   # LRM/RLM
    (0x2060, 0x2064),   # Word Joiner / Zero-width no-break etc.
    (0xFEFF, 0xFEFF),   # ZERO WIDTH NO-BREAK SPACE / BOM
]

# 全角字符范围（宽度为2的非CJK字符）
_FULLWIDTH_RANGES: list[tuple[int, int]] = [
    (0xFF01, 0xFF60),   # Fullwidth Forms
    (0xFFE0, 0xFFE6),   # Fullwidth Signs
]

# Emoji 宽符号范围（终端以 2 列渲染；与 wcwidth 的 emoji-wide 集对齐）。
# 1F000+ 主要 emoji 块恒宽；2600-27BF 仅列出的具体码点为宽
# （⚠ 不包含 ✔✎⚙✕ 等文本呈现符号——它们宽度为 1，误计为 2 会导致表格错位）。
# ★ 方向1（RI 码点）：原 (0x1F000, 0x1FAFF) 覆盖 Regional Indicator（RI，
#   0x1F1E6-0x1F1FF，国旗字母），将其排除——单 RI 终端行为有差异（部分终端
#   按 2 列渲染），本实现取保守语义：单 RI 宽 1、成对 RI（国旗）按 1×2=2 列
#   （与主流 wcwidth 一致）。0x1F200+（如 🈁）仍按 2 列计。
_EMOJI_WIDE_RANGES: list[tuple[int, int]] = [
    (0x1F000, 0x1F1E5),   # 主要 emoji 块（📖📄🔍 等；不含 RI 码点）
    (0x1F200, 0x1FAFF),   # 主要 emoji 块续（🈁 等；RI 码点 0x1F1E6-0x1F1FF 已排除）
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
]


def _build_flat_ranges(ranges: list[tuple[int, int]]) -> list[int]:
    """构造区间表的扁平排序边界数组（每个区间起点/终点后一位，供 bisect 定位）。

    二分正确性依赖「有序不重叠」不变式——实现时显式排序 + 断言（区间表
    声明为有序不重叠；断言保护排序不变式，防后续误改区间表破坏二分）。

    Args:
        ranges: 有序不重叠区间列表（升序；启动时排序保证）。

    Returns:
        扁平的 ``[lo0, hi0+1, lo1, hi1+1, ...]`` 数组。
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
    """二分定位码点所在区间（flat 为排序后起点/终点后一位交替数组）。

    区间 ``[lo, hi]`` 展开为 ``lo, hi+1`` 两个边界；``bisect_right`` 返回
    第一个 > cp 的边界索引——索引为奇数 ⇒ cp 落在某区间内（O(log n)）。
    """
    idx = bisect.bisect_right(flat, cp)
    return (idx % 2) == 1


#: 预计算的排序扁平边界表（wcswidth_simple 热路径用；区间内容不变）
_CJK_FLAT = _build_flat_ranges(_CJK_RANGES)
_FULLWIDTH_FLAT = _build_flat_ranges(_FULLWIDTH_RANGES)
_EMOJI_WIDE_FLAT = _build_flat_ranges(_EMOJI_WIDE_RANGES)
_ZERO_WIDTH_FLAT = _build_flat_ranges(_ZERO_WIDTH_RANGES)

#: ASCII 可打印连续段正则（wcswidth_simple 多字符路径快速跳过——PERF-9）。
#: C 实现扫描比逐字符 Python ``ord()`` + 比较快 ~2x；混合文本（大量 ASCII
#: + 少量 CJK/emoji/控制字符）热路径（状态栏/输入行/工具输出）收益明显。
#: 匹配 ``[\x20-\x7e]``（0x20-0x7E，与下方 ASCII 分支宽度 1 一致）——
#: ``\x7f``（DEL，宽 0）与 ``\x1b``（ESC）不在其中，正确走各自分支。
_ASCII_RUN_RE = re.compile(r"[\x20-\x7e]+")


def _skip_ansi_at(text: str, i: int) -> int:
    """跳过从 ``text[i]``（\\x1b）开始的完整 ANSI 转义序列，返回序列后索引。

    支持三类（与 ``ink.helpers._ANSI_RE`` 匹配范围对齐，步骤2 统一 ANSI
    工具；本函数为 _width 层局部最小匹配器——避免 Layer 0 → ink 反向
    依赖）：
      - CSI：``\\x1b[`` + 参数(0-9;?) + 最终字节(A-Za-z)
      - OSC：``\\x1b]`` + 内容 + BEL(\\x07) 或 ST(\\x1b\\\\)
      - 单字符控制：``\\x1b`` + [@-Z\\\\-_]

    残缺/嵌套序列安全跳过（不抛异常）：已消费的合法前缀返回，孤立 ESC
    仅跳过 ESC 本身——宽度测量场景整段计宽 0。

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
        # CSI：\x1b[ 参数中间字节(0x20-0x3F：数字/分号/冒号/问号/空格) +
        # 最终字节(0x40-0x7E：@A-Z[\]^_`a-z{|}~)。
        # ★ BUG-33（review 方向）：修复前参数仅扫 ``0123456789;?``、最终字节
        #   仅收 ``A-Za-z``——``\x1b[38:2::255:0:0m``（真彩冒号格式）在 ``:``
        #   处停、``\x1b[3~``（Delete/PageUp 终端键）在 ``~`` 处停，残留字符
        #   被 ``wcswidth_simple`` 逐字符计宽 → 行宽虚高 → wrap/截断/对齐错位。
        #   按 ECMA-48 中间字节/最终字节全范围修复（与 ink.helpers._ANSI_RE
        #   同步收敛）。
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
    # 单字符控制序列：\x1bX（X 为 @-Z \ - _）
    if ("@" <= c <= "Z") or c in ("\\", "-", "_"):
        return j + 1
    return j  # 孤立 ESC：仅跳过 ESC 本身


#: 单字符显示宽度缓存（wcswidth_simple 热路径——重复 CJK/emoji 字符免区间二分）。
#: 有界：超过 ``_CHAR_WIDTH_CACHE_MAX`` 时整体清空重建（终端文本字符集有界，
#: 清空后重新积累；宽度值确定性，正确性不受影响）。
_CHAR_WIDTH_CACHE_MAX = 4096
_char_width_cache: dict[str, int] = {}


def _wcswidth_single(ch: str) -> int:
    """计算单个字符的显示宽度（wcswidth_simple 内部辅助，缓存专用）。"""
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E:
        return 1
    if ch == "\x1b":
        return 0  # 孤立 ESC 宽度 0（_skip_ansi_at 语义）
    if cp < 0x20 or (0x7F <= cp <= 0x9F):
        return 0  # 控制字符
    if _in_ranges_bisect(cp, _CJK_FLAT):
        return 2
    if _in_ranges_bisect(cp, _FULLWIDTH_FLAT):
        return 2
    if _in_ranges_bisect(cp, _EMOJI_WIDE_FLAT):
        return 2
    if _in_ranges_bisect(cp, _ZERO_WIDTH_FLAT):
        return 0
    return 1


def wcswidth_simple(text: str) -> int:
    """计算字符串的显示宽度（零第三方依赖）。

    规则：
    - ASCII 可打印字符 (0x20-0x7E)：宽度 1
    - 控制字符 (0x00-0x1F, 0x7F-0x9F)：宽度 0（含制表符 \\t——控制字符分支）
    - ANSI 转义序列（\\x1b 起始）：整段宽度 0（方向1 步骤1 修复——修复前
      ``\\x1b[31m`` 的 ``[31m`` 被逐字符计宽，ANSI 行测宽虚高导致换行/截断
      错位）
    - CJK 字符：宽度 2
    - 全角字符：宽度 2
    - 组合标记/零宽字符：宽度 0
    - 其他：宽度 1

    实现（方向1 步骤1）：码点区间判定由线性扫描改为排序边界数组 +
    ``bisect`` 二分（单字符 O(log n)；ASCII 0x20-0x7E 走 O(1) 快路径）。
    区间表内容不变（行为语义保持，测试锁定）。

    方向3（性能）：单字符快速路径——重复 CJK/emoji 字符经有界 dict 缓存
    免区间二分（``_char_width_cache``，缓存命中 O(1)）。ASCII 走原有 O(1)
    快路径（不经缓存——dict 查找对 ASCII 反而更慢）。

    Args:
        text: 输入字符串。

    Returns:
        显示宽度（整数）。
    """
    if len(text) == 1:
        ch = text
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            return 1  # ASCII 快路径（不经缓存）
        if ch == "\x1b" or cp < 0x20 or (0x7F <= cp <= 0x9F):
            return 0  # 控制/孤立 ESC 快路径
        w = _char_width_cache.get(ch)
        if w is not None:
            return w
        w = _wcswidth_single(ch)
        if len(_char_width_cache) >= _CHAR_WIDTH_CACHE_MAX:
            _char_width_cache.clear()
        _char_width_cache[ch] = w
        return w
    # ★ 纯可打印 ASCII 快速路径（PERF-7）：C 实现的 ``isascii()`` +
    # ``isprintable()`` 单趟扫描（比逐字符 Python 循环快数倍）——纯 ASCII
    # 可打印字符串宽度 == 字符数（无 ANSI/控制/零宽/宽字符）。``isprintable()``
    # 对 \x1b/控制字符返回 False（含转义序列的文本回退常规路径），对
    # 非 ASCII（CJK/emoji/全角）由 ``isascii()`` 排除。渲染热路径中状态栏/
    # 工具输出/输入行等大量纯 ASCII 文本受益。
    if text.isascii() and text.isprintable():
        return len(text)
    width = 0
    i = 0
    n = len(text)
    while i < n:
        # ★ 性能（PERF-9）：ASCII 连续段快速跳过——正则（C 实现）扫描比逐
        #   字符 Python ``ord()`` + 分支判断快 ~2x。混合文本（状态栏/输入行
        #   等大量 ASCII + 少量 CJK）热路径收益明显；``[\x20-\x7e]`` 与下方
        #   ASCII 分支宽度 1 一致，``\x7f``（DEL）/``\x1b``（ESC）不在其中
        #   正确走各自分支。纯 ASCII 可打印文本已在上方快路径返回，本优化
        #   服务混合文本与含控制/ANSI 的文本。
        m = _ASCII_RUN_RE.match(text, i)
        if m is not None:
            width += m.end() - i
            i = m.end()
            continue
        cp = ord(text[i])
        if text[i] == "\x1b":
            # ANSI 转义序列：整段宽度 0（跳过完整序列；残缺/孤立 ESC 安全）
            i = _skip_ansi_at(text, i)
        elif cp < 0x20 or (0x7F <= cp <= 0x9F):
            width += 0  # 控制字符（含制表符 \t——宽度 0，不展开为空格）
            i += 1
        elif _in_ranges_bisect(cp, _CJK_FLAT):
            width += 2
            i += 1
        elif _in_ranges_bisect(cp, _FULLWIDTH_FLAT):
            width += 2
            i += 1
        elif _in_ranges_bisect(cp, _EMOJI_WIDE_FLAT):
            width += 2
            i += 1
        elif _in_ranges_bisect(cp, _ZERO_WIDTH_FLAT):
            width += 0
            i += 1
        else:
            width += 1
            i += 1
    return width


__all__ = [
    "wcswidth_simple",
    "_CJK_RANGES",
    "_ZERO_WIDTH_RANGES",
    "_FULLWIDTH_RANGES",
    "_EMOJI_WIDE_RANGES",
    "_build_flat_ranges",
    "_in_ranges_bisect",
    "_CJK_FLAT",
    "_FULLWIDTH_FLAT",
    "_EMOJI_WIDE_FLAT",
    "_ZERO_WIDTH_FLAT",
    "_ASCII_RUN_RE",
    "_skip_ansi_at",
    "_CHAR_WIDTH_CACHE_MAX",
    "_char_width_cache",
    "_wcswidth_single",
]
