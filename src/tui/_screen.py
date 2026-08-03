"""纯 ANSI 终端屏幕管理 — 零第三方依赖。

提供终端尺寸查询、字符宽度计算、ANSI 转义序列生成、
SIGWINCH 信号处理等基础设施。所有函数为纯字符串返回或
直接写入 ``sys.__stdout__``，不依赖 blessed/wcwidth 等第三方库。

设计模式: 外观（Facade）— 作为所有终端 I/O 的统一入口。

遗留标注（2026-07-31 方向F）：鼠标输入不支持 / bracketed paste 无协议——
功能增强项，不在本次架构改进范围，**标记 P2 遗留**（后续如需鼠标支持须引入
终端能力协商与协议解析，评估后再实施）。
"""

from __future__ import annotations

import bisect
import fcntl
import io
import os
import signal
import struct
import sys
import termios
import threading
import time
from typing import Callable

# ANSI 颜色常量唯一真源在 src/tui/_const.py（方向F 步骤12 收敛）；
# 本模块 re-export 保持 bottom_bar 各子模块既有导入路径不变。
from ._const import (
    _COLOR_ACCENT,
    _COLOR_COMPLETE_CMD_PREFIX,
    _COLOR_COMPLETE_DIR,
    _COLOR_COMPLETE_MATCH,
    _COLOR_COMPLETE_TITLE,
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SELECT_BG,
    _COLOR_SELECT_FG,
    _COLOR_SEP,
    _COLOR_SPEED,
    _COLOR_TIME,
    _COLOR_TOKEN,
    _COLOR_TOOL_FAIL,
    _COLOR_TOOL_OK,
)


# ═══════════════════════════════════════════════════════════
# 终端尺寸查询
# ═══════════════════════════════════════════════════════════

def _get_terminal_size() -> tuple[int, int]:
    """获取终端尺寸 (宽度, 高度)。

    优先使用 ``fcntl.ioctl(TIOCGWINSZ)`` 获取精确尺寸，
    fallback ``os.get_terminal_size()``，
    最终兜底 (80, 24)。

    Returns:
        (cols, rows) 终端宽度和高度。
    """
    for fd_src in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = fd_src.fileno()
        except (io.UnsupportedOperation, OSError, AttributeError):
            continue
        try:
            # TIOCGWINSZ 结构体: unsigned short ws_row, ws_col, ws_xpixel, ws_ypixel
            buf = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
            rows, cols, _, _ = struct.unpack("HHHH", buf)
            if rows > 0 and cols > 0:
                return (cols, rows)
        except (OSError, struct.error):
            continue

    # Fallback: os.get_terminal_size()
    try:
        ts = os.get_terminal_size()
        if ts.columns > 0 and ts.lines > 0:
            return (ts.columns, ts.lines)
    except (OSError, ValueError):
        pass

    # 最终兜底
    return (80, 24)


# ═══════════════════════════════════════════════════════════
# 终端能力协商
# ═══════════════════════════════════════════════════════════

def detect_truecolor() -> bool:
    """检测终端是否支持 truecolor（24-bit 颜色）。

    方向3（单一真源收敛）：复用 ``core.color`` 的判定逻辑（含 NO_COLOR 强制
    降级 + TERM direct 判定）——修复前本模块独立实现（仅查 ``COLORTERM``，
    不尊重 ``NO_COLOR`` 规范），与 ``core/color`` 双实现语义漂移。此处调用
    ``_detect_truecolor_uncached``（无进程级缓存），保持本模块既有「每次
    独立检测」语义（test_screen 锁定，避免 core/color 缓存跨测试污染）。

    Returns:
        True — 终端宣称支持 truecolor；False — 默认 256 色降级。
    """
    from src.tui.core.color import _detect_truecolor_uncached
    return _detect_truecolor_uncached()


# ═══════════════════════════════════════════════════════════
# 字符宽度计算
# ═══════════════════════════════════════════════════════════

# CJK Unified Ideographs 及扩展区范围
_CJK_RANGES: list[tuple[int, int]] = [
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


def _in_ranges(cp: int, ranges: list[tuple[int, int]]) -> bool:
    """检查码点是否在范围内（线性扫描，兼容旧调用面；热路径用二分版）。"""
    return any(lo <= cp <= hi for lo, hi in ranges)


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


def _skip_ansi_at(text: str, i: int) -> int:
    """跳过从 ``text[i]``（\\x1b）开始的完整 ANSI 转义序列，返回序列后索引。

    支持三类（与 ``ink.helpers._ANSI_RE`` 匹配范围对齐，步骤2 统一 ANSI
    工具；本函数为 _screen 层局部最小匹配器——避免 Layer 0 → ink 反向
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
    width = 0
    i = 0
    n = len(text)
    while i < n:
        cp = ord(text[i])
        if 0x20 <= cp <= 0x7E:
            width += 1
            i += 1
        elif text[i] == "\x1b":
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


# ═══════════════════════════════════════════════════════════
# 滚动区域 (DECSTBM)
# ═══════════════════════════════════════════════════════════

def set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域。

    Args:
        top: 顶部行号（1-based）。
        bottom: 底部行号（1-based）。

    Returns:
        ANSI DECSTBM 序列。
    """
    return f"\033[{top};{bottom}r"


def reset_scroll_region() -> str:
    """重置滚动区域为全屏。

    Returns:
        ANSI 序列。
    """
    return "\033[r"


# ═══════════════════════════════════════════════════════════
# 光标控制
# ═══════════════════════════════════════════════════════════

def cursor_save() -> str:
    """保存光标位置 (SCOSC)。

    Returns:
        ANSI SCOSC 序列。
    """
    return "\033[s"


def cursor_restore() -> str:
    """恢复光标位置 (SCRC)。

    Returns:
        ANSI SCRC 序列。
    """
    return "\033[u"


def cursor_goto(row: int, col: int) -> str:
    """移动光标到指定位置 (CUP, 1-based)。

    Args:
        row: 目标行号（1-based）。
        col: 目标列号（1-based）。

    Returns:
        ANSI CUP 序列。
    """
    return f"\033[{row};{col}H"


def cursor_up(n: int = 1) -> str:
    """光标上移 n 行 (CUU)。

    Args:
        n: 移动行数。

    Returns:
        ANSI CUU 序列。
    """
    return f"\033[{n}A"


def cursor_down(n: int = 1) -> str:
    """光标下移 n 行 (CUD)。

    Args:
        n: 移动行数。

    Returns:
        ANSI CUD 序列。
    """
    return f"\033[{n}B"


def cursor_forward(n: int = 1) -> str:
    """光标右移 n 列 (CUF)。

    Args:
        n: 移动列数。

    Returns:
        ANSI CUF 序列。
    """
    return f"\033[{n}C"


def cursor_back(n: int = 1) -> str:
    """光标左移 n 列 (CUB)。

    Args:
        n: 移动列数。

    Returns:
        ANSI CUB 序列。
    """
    return f"\033[{n}D"


def cursor_hide() -> str:
    """隐藏光标。

    Returns:
        ANSI DECTCEM 序列（隐藏）。
    """
    return "\033[?25l"


def cursor_show() -> str:
    """显示光标。

    Returns:
        ANSI DECTCEM 序列（显示）。
    """
    return "\033[?25h"


# ═══════════════════════════════════════════════════════════
# 清屏/清行
# ═══════════════════════════════════════════════════════════

def clear_line() -> str:
    """清除从光标到行尾的内容 (EL 0)。

    Returns:
        ANSI EL 序列。
    """
    return "\r\033[K"


def clear_line_full() -> str:
    """清除整行 (EL 2)。

    Returns:
        ANSI EL 序列。
    """
    return "\r\033[2K"


def clear_screen_from_cursor() -> str:
    """清除从光标到屏幕末尾 (ED 0)。

    Returns:
        ANSI ED 序列。
    """
    return "\033[0J"


def clear_screen_to_cursor() -> str:
    """清除从屏幕开头到光标 (ED 1)。

    Returns:
        ANSI ED 序列。
    """
    return "\033[1J"


def clear_screen() -> str:
    """清除整个屏幕 (ED 2) 并归位光标。

    Returns:
        ANSI ED + CUP 序列。
    """
    return "\033[2J\033[H"


def move_clear(row: int) -> str:
    """组合光标定位 + 清行。

    Args:
        row: 目标行号（1-based）。

    Returns:
        CUP + EL 组合序列。
    """
    return f"\033[{row};1H\033[K"


# ═══════════════════════════════════════════════════════════
# 滚动
# ═══════════════════════════════════════════════════════════

def scroll_up(n: int = 1) -> str:
    """向上滚动 n 行 (SU)。

    Args:
        n: 滚动行数。

    Returns:
        ANSI SU 序列。
    """
    return f"\033[{n}S"


def scroll_down(n: int = 1) -> str:
    """向下滚动 n 行 (SD)。

    Args:
        n: 滚动行数。

    Returns:
        ANSI SD 序列。
    """
    return f"\033[{n}T"


# ═══════════════════════════════════════════════════════════
# 颜色 / SGR
# ═══════════════════════════════════════════════════════════

def sgr(*codes: int) -> str:
    """构建 SGR 序列。

    Args:
        codes: SGR 参数码。

    Returns:
        ANSI SGR 序列。
    """
    if not codes:
        return "\033[0m"
    return f"\033[{';'.join(str(c) for c in codes)}m"


def sgr_reset() -> str:
    """SGR 重置。

    Returns:
        ANSI SGR 重置序列。
    """
    return "\033[0m"


def fg_256(color: int) -> str:
    """设置 256 色前景色。

    Args:
        color: 256 色号 (0-255)。

    Returns:
        ANSI SGR 序列。
    """
    return f"\033[38;5;{color}m"


def bg_256(color: int) -> str:
    """设置 256 色背景色。

    Args:
        color: 256 色号 (0-255)。

    Returns:
        ANSI SGR 序列。
    """
    return f"\033[48;5;{color}m"


def fg_truecolor(r: int, g: int, b: int) -> str:
    """设置 24-bit 前景色。

    Args:
        r: 红色通道 (0-255)。
        g: 绿色通道 (0-255)。
        b: 蓝色通道 (0-255)。

    Returns:
        ANSI 24-bit SGR 序列。
    """
    return f"\033[38;2;{r};{g};{b}m"


def bg_truecolor(r: int, g: int, b: int) -> str:
    """设置 24-bit 背景色。

    Args:
        r: 红色通道 (0-255)。
        g: 绿色通道 (0-255)。
        b: 蓝色通道 (0-255)。

    Returns:
        ANSI 24-bit SGR 序列。
    """
    return f"\033[48;2;{r};{g};{b}m"


# ═══════════════════════════════════════════════════════════
# 窗口标题
# ═══════════════════════════════════════════════════════════

def set_window_title(title: str) -> None:
    """设置终端窗口标题。

    通过 OSC 序列 ``\\033]0;title\\007`` 设置。
    直接写入 ``sys.__stdout__``。

    Args:
        title: 窗口标题。
    """
    try:
        sys.__stdout__.write(f"\033]0;{title}\007")
        sys.__stdout__.flush()
    except (OSError, ValueError, AttributeError):  # BUG-52：无 TTY 时 stdout 为 None
        pass


# ═══════════════════════════════════════════════════════════
# ANSI 颜色常量（256 色体系）— re-export from _const
# ═══════════════════════════════════════════════════════════
# 唯一真源已收敛至 src/tui/_const.py（方向F 步骤12）；本模块保留 re-export，
# 使 bottom_bar 各子模块（_bar/_layout/_popup/_render）既有
# ``from src.tui._screen import _COLOR_*`` 导入路径不变。
# （常量定义见本文件顶部 from ._const import ...）


# ═══════════════════════════════════════════════════════════
# 便捷组合函数
# ═══════════════════════════════════════════════════════════

def write_stdout(data: str) -> None:
    """直接写入 ``sys.__stdout__``。

    仅紧急路径使用，禁止常规调用（常规内容/布局写一律走统一输出管线）。

    ★ BUG-52（review 方向）：except 补充 ``AttributeError``——``sys.__stdout__``
    为 None（无 TTY daemon）时 ``.write`` 抛 AttributeError（修复前仅捕获
    OSError/ValueError，无 TTY 场景异常泄漏）。
    """
    try:
        sys.__stdout__.write(data)
        sys.__stdout__.flush()
    except (OSError, ValueError, AttributeError):
        pass


# ═══════════════════════════════════════════════════════════
# SIGWINCH 信号处理
# ═══════════════════════════════════════════════════════════

_sigwinch_callbacks: list[Callable[[int, int], None]] = []
_sigwinch_registered: bool = False
# BUG-T4：信号处理器只置标志（信号安全），渲染循环经 process_sigwinch() 消费
_sigwinch_pending: bool = False


def register_sigwinch_callback(cb: Callable[[int, int], None]) -> None:
    """注册 SIGWINCH 回调。

    窗口尺寸变化时，回调被调用并传入 (width, height)。

    Args:
        cb: 回调函数，签名为 ``(width: int, height: int) -> None``。
    """
    global _sigwinch_registered
    if cb not in _sigwinch_callbacks:
        _sigwinch_callbacks.append(cb)
    if not _sigwinch_registered:
        try:
            signal.signal(signal.SIGWINCH, _handle_sigwinch)
            _sigwinch_registered = True
        except (OSError, ValueError):
            pass


def unregister_sigwinch_callback(cb: Callable[[int, int], None]) -> None:
    """取消注册 SIGWINCH 回调。

    Args:
        cb: 之前注册的回调函数。
    """
    try:
        _sigwinch_callbacks.remove(cb)
    except ValueError:
        pass


def _handle_sigwinch(signum: int, frame: object) -> None:
    """SIGWINCH 信号处理器 — 仅置标志（信号安全）。

    BUG-T4：信号处理器中禁止调用非信号安全操作（fcntl.ioctl / Event.set /
    用户回调 / logging）。终端尺寸刷新与回调执行迁移到 ``process_sigwinch()``，
    由渲染循环轮询调用。

    Args:
        signum: 信号编号。
        frame: 当前栈帧（未使用）。
    """
    global _sigwinch_pending
    _sigwinch_pending = True


def process_sigwinch() -> bool:
    """处理待处理的 SIGWINCH 事件（渲染线程轮询调用）。

    若信号处理器已置位 pending 标志，则复位标志并在**正常线程上下文**中
    刷新终端尺寸 + 遍历执行 SIGWINCH 回调（每个回调 try/except 隔离，
    防止单个回调崩溃中断其他回调）。

    Returns:
        True — 本帧有 SIGWINCH 待处理且已处理；
        False — 无待处理事件。
    """
    global _sigwinch_pending
    if not _sigwinch_pending:
        return False
    _sigwinch_pending = False
    try:
        width, height = _get_terminal_size()
    except Exception:
        width, height = 80, 24
    for cb in _sigwinch_callbacks:
        try:
            cb(width, height)
        except Exception:
            pass
    return True


# ═══════════════════════════════════════════════════════════
# TerminalWidthCache — 终端宽度缓存（TTL 惰性缓存 + 主动失效）
# ═══════════════════════════════════════════════════════════

class TerminalWidthCache:
    """终端宽度缓存 — TTL 惰性缓存 + 主动失效。

    提供与旧 ``terminal/terminal.py`` 中同名的兼容实现，
    使用 ``_get_terminal_size()`` 替代 blessed Terminal。

    设计模式: 装饰器（Decorator）— 在 ``_get_terminal_size()`` 之上
    添加 TTL 缓存层。
    """

    _instance: TerminalWidthCache | None = None

    def __init__(self, ttl: float = 60.0) -> None:
        """初始化缓存。

        Args:
            ttl: TTL 秒数（默认 60 秒）。get_width/get_height 在 TTL 内
                 返回缓存值，过期后调用 _get_terminal_size() 获取新值。
        """
        self._ttl = ttl
        self._width: int = 0
        self._height: int = 0
        self._last_width_fetch: float = 0.0
        self._last_height_fetch: float = 0.0
        self._fetch()

    def _fetch(self) -> None:
        """从底层获取终端尺寸并更新缓存。"""
        try:
            self._width, self._height = _get_terminal_size()
        except Exception:
            self._width, self._height = 80, 24
        now = time.monotonic()
        self._last_width_fetch = now
        self._last_height_fetch = now

    def _is_expired(self, last_fetch: float) -> bool:
        """检查缓存是否过期（超过 TTL）。"""
        return (time.monotonic() - last_fetch) > self._ttl

    def get_width(self) -> int:
        """获取终端宽度（TTL 缓存）。"""
        if self._is_expired(self._last_width_fetch):
            try:
                w, h = _get_terminal_size()
                self._width = w
                self._height = h
            except Exception:
                self._width, self._height = 80, 24
            now = time.monotonic()
            self._last_width_fetch = now
            self._last_height_fetch = now
        return self._width

    def get_height(self) -> int:
        """获取终端高度（TTL 缓存）。"""
        if self._is_expired(self._last_height_fetch):
            try:
                w, h = _get_terminal_size()
                self._width = w
                self._height = h
            except Exception:
                self._width, self._height = 80, 24
            now = time.monotonic()
            self._last_width_fetch = now
            self._last_height_fetch = now
        return self._height

    def get_dimensions(self) -> tuple[int, int]:
        """获取终端尺寸 (宽度, 高度)。

        ★ 方向1（高度陈旧修复）：高度经 ``get_height()`` 走独立 TTL 检查——
        修复前直接读 ``_height`` 字段绕过 height TTL（width TTL 未过期时
        返回陈旧高度）。
        """
        # 先获取宽度（也会更新高度缓存）
        w = self.get_width()
        h = self.get_height()
        return (w, h)

    def force_refresh(self) -> None:
        """绕过 TTL 立即刷新宽度和高度。"""
        self._fetch()

    def clear(self) -> None:
        """清空缓存，下次查询强制刷新。"""
        self._last_width_fetch = 0.0
        self._last_height_fetch = 0.0

    def refresh_height(self) -> int:
        """强制刷新高度缓存，返回新高度。

        Returns:
            当前终端高度。
        """
        try:
            w, h = _get_terminal_size()
            self._width = w
            self._height = h
        except Exception:
            self._width, self._height = 80, 24
        now = time.monotonic()
        self._last_width_fetch = now
        self._last_height_fetch = now
        return self._height

    @classmethod
    def get_default(cls) -> TerminalWidthCache:
        """获取全局单例（双检锁——方向1 步骤1：并发首次调用不产生多实例）。

        多实例会各自 TTL 缓存导致宽度不一致（并发首次调用竞态）；双检锁为
        Python 标准模式（GIL 下安全）；实例已存在时无锁路径零开销。
        """
        if cls._instance is None:
            with _instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


#: TerminalWidthCache 单例双检锁（get_default 并发首次调用互斥）
_instance_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════
# narrow_sep_width — 窄屏分隔线宽度（兼容旧 API）
# ═══════════════════════════════════════════════════════════

def narrow_sep_width(width: int | None = None, threshold: int = 40) -> int:
    """计算窄屏分隔线宽度。

    当终端宽度 < threshold 时使用缩短的宽度。

    Args:
        width: 终端宽度，None 时自动获取。
        threshold: 窄屏阈值。

    Returns:
        分隔线宽度。
    """
    if width is None:
        width = TerminalWidthCache.get_default().get_width()
    if width < threshold:
        return max(10, width - 2)
    return width
