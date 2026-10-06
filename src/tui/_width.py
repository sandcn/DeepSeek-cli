"""字符显示宽度计算 — 零第三方依赖（从 _screen.py 拆分，方向：模块边界优化）。

职责：终端字符显示宽度的纯计算（CJK/全角/Emoji/零宽字符/ANSI 序列跳过/
ASCII 快速路径/单字符缓存）。从 ``_screen.py`` 拆分独立，使屏幕管理模块
聚焦终端 I/O（尺寸查询/ANSI 序列/SIGWINCH），本模块专注显示宽度测量。

★ P1-2（单一真源）：码点宽度语义（区间表 + 判定）已收敛到
``src._text_width``（Layer 0，被 ``tui._width`` / ``renderer._utils._display``
/ ``config.view_model`` 共享，消除「多套区间表靠注释手工同步」的漂移风险）。
本模块保留 tui 侧 **公开 API 与性能路径**：

  - ``wcswidth_simple``：单字符缓存（``_char_width_cache``）+ 委托
    ``_text_width.string_width``（多字符通用路径）；
  - ``truncate_width`` / ``expand_tabs``：tui 特有工具（ANSI 穿透截断 /
    制表符展开）；
  - 区间表与 flat 边界表：re-export 自 ``src._text_width``，保持旧导入路径
    兼容（``from src.tui._width import _CJK_RANGES`` 等，测试锁定）。

Layer 0 — 零依赖（仅标准库），被 _screen 及 ink 框架消费。
"""

from __future__ import annotations

from src._text_width import (
    CJK_FLAT,
    CJK_RANGES,
    EMOJI_WIDE_FLAT,
    EMOJI_WIDE_RANGES,
    FULLWIDTH_FLAT,
    FULLWIDTH_RANGES,
    ZERO_WIDTH_FLAT,
    ZERO_WIDTH_RANGES,
    _ASCII_RUN_RE,
    _build_flat_ranges,
    _in_ranges_bisect,
    char_width as _char_width_impl,
    string_width as _string_width_impl,
    skip_ansi_at as _skip_ansi_at,
)
from src._text_width import (
    _CHAR_WIDTH_CACHE as _char_width_cache,
    CHAR_WIDTH_CACHE_MAX as _CHAR_WIDTH_CACHE_MAX,
)
from src._text_width import expand_tabs as _expand_tabs_impl

# ── 旧名称 re-export（下划线前缀；保持既有导入路径兼容） ──
_CJK_RANGES = CJK_RANGES
_ZERO_WIDTH_RANGES = ZERO_WIDTH_RANGES
_FULLWIDTH_RANGES = FULLWIDTH_RANGES
_EMOJI_WIDE_RANGES = EMOJI_WIDE_RANGES
_CJK_FLAT = CJK_FLAT
_FULLWIDTH_FLAT = FULLWIDTH_FLAT
_EMOJI_WIDE_FLAT = EMOJI_WIDE_FLAT
_ZERO_WIDTH_FLAT = ZERO_WIDTH_FLAT

#: 单字符显示宽度缓存 re-export（``wcswidth_simple`` 热路径——重复 CJK/emoji
#: 字符免区间二分）。★ 单一真源：缓存本体与上限均在 ``src._text_width``
#: （``char_width`` 与 renderer 层共用同一份缓存，避免同一字符在两套缓存
#: 中各算一次）；本模块仅 re-export 旧名称保持既有导入路径兼容。
#: 有界：超过上限时整体清空重建（终端文本字符集有界，清空后重新积累；
#: 宽度值确定性，正确性不受影响）。无锁可接受：GIL 下单条 get/set/clear
#: 均原子，最坏情况只是宽度重复计算。


def _wcswidth_single(ch: str) -> int:
    """单个字符的显示宽度（委托 ``src._text_width.char_width``，唯一真源）。

    公共导出（放宽/放宽前后兼容）：外部直接单字符调用场景仍可用；语义与
    ``_text_width.char_width`` 完全一致（ASCII 1 / 控制字符与孤立 ESC 0 /
    CJK 与全角与 emoji 宽 2 / 零宽 0 / 其他 1）。
    """
    return _char_width_impl(ch)


def wcswidth_simple(text: str) -> int:
    """计算字符串的显示宽度（零第三方依赖）。

    规则：
    - ASCII 可打印字符 (0x20-0x7E)：宽度 1
    - 控制字符 (0x00-0x1F, 0x7F-0x9F)：宽度 0（含制表符 \\t——控制字符分支）
    - ANSI 转义序列（\\x1b 起始）：整段宽度 0（修复前 ``\\x1b[31m`` 的
      ``[31m`` 被逐字符计宽，ANSI 行测宽虚高导致换行/截断错位）
    - CJK 字符：宽度 2
    - 全角字符：宽度 2
    - 组合标记/零宽字符：宽度 0
    - 其他：宽度 1

    ★ P1-2（单一真源）：多字符通用路径委托 ``src._text_width.string_width``
    （区间表 + ASCII 段快路径 + ANSI 跳过统一实现）；单字符路径委托
    ``src._text_width.char_width``（ASCII/控制字符算术快路径 + 有界缓存
    ——与 renderer 层共用同一份缓存，同一字符不需两套缓存各算一次）。

    Args:
        text: 输入字符串。

    Returns:
        显示宽度（整数）。
    """
    if len(text) == 1:
        return _char_width_impl(text)
    return _string_width_impl(text)


def expand_tabs(text: str, start_col: int = 0, tab_width: int = 8) -> str:
    """展开制表符为空格并剔除回车（使显示宽度与终端渲染一致）。

    ★ P1-2（单一真源）：委托 ``src._text_width.expand_tabs``（与 renderer 侧
    ``renderer._utils.expand_tabs`` 同一实现）——历史两份实现（本模块与
    renderer）语义一致但需手工同步，收敛后漂移风险消除。

    ★ 显示错乱根因修复（2026-10-05）：制表符 ``\\t`` 是控制字符
    （``wcswidth_simple`` 计宽 0），但真实终端把它当 HT 跳到下一个 tab stop
    （默认每 8 列补空格）——行内容含 ``\\t`` 时「计算宽度 < 实际渲染宽度」，
    配合「工具卡整行占满终端宽度」的填充（按计算宽度补空格到终端列宽），
    实际渲染宽度 = 终端列宽 + tab 展开补偿量 > 终端列宽 → 终端自动换行，
    后续行光标定位整体错位。本函数在文本进入渲染模型前把 ``\\t`` 只展开
    一次，之后宽度计算与实际渲染恒一致（都是空格），错乱消除。

    Args:
        text: 待规范化文本。
        start_col: 文本起始显示列（默认 0；制表位按 ``col % tab_width`` 对齐）。
        tab_width: 制表宽度（列；<=0 回退 8）。

    Returns:
        展开后的文本（不含 ``\\t``/``\\r``；无该字符时原样返回）。
    """
    return _expand_tabs_impl(text, start_col, tab_width)


def truncate_width(s: str, max_w: int) -> str:
    """按显示宽度截断字符串（不拆 CJK），返回截断后文本。

    公共工具（单一真源）：由 ``_popup_builder._truncate_width`` 提升为公共函数，
    供 config_view（配置显示截断）等消费——避免跨模块依赖下划线私有 API。
    纯 ASCII 可打印字符串宽度 == 字符数——C 实现的 ``isascii()`` +
    ``isprintable()`` 单趟扫描后直接切片（逐字符 ``wcswidth_simple`` 的
    Python 循环仅用于含 CJK/emoji/控制字符的文本）。

    ★ P2（review）：识别 ANSI 转义序列——修复前逐字符 ``wcswidth_simple``
    把 ESC 后序列正文（``[31m`` 等）按可见字符计宽，且截断可能停在序列中间
    产出残缺转义（如 ``truncate_width("\\x1b[31mabcdef", 3) == "\\x1b[31"``，
    宽度应为 0）。现经 ``_skip_ansi_at`` 整段穿透（序列宽度 0，不计入预算、
    不被截断拆散）。
    """
    if max_w <= 0:
        return ""
    if s.isascii() and s.isprintable():
        return s if len(s) <= max_w else s[:max_w]
    w = 0
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\x1b":
            j = _skip_ansi_at(s, i)
            out.append(s[i:j])
            i = j
            continue
        cw = wcswidth_simple(ch)
        if w + cw > max_w:
            break
        out.append(ch)
        w += cw
        i += 1
    return "".join(out)


__all__ = [
    "wcswidth_simple",
    "truncate_width",
    "expand_tabs",
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
