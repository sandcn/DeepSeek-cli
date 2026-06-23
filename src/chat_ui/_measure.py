"""测量工具 — measureElement 函数，测量 TuiComponent 渲染尺寸。

提供基于终端宽度的组件尺寸测量，支持 CJK 字符双列宽计算、
ANSI 转义序列剥离和 Rich Text 纯文本提取。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._components import TuiComponent

from rich.text import Text

_logger = logging.getLogger(__name__)


# 覆盖 CSI + OSC (BEL/ST 终止) + DCS/APC/PM/SOS (ST 终止) 等序列
_ANSI_RE = re.compile(
    r'\x1b\[[0-9;]*[a-zA-Z]'          # CSI: \x1b[ ... 字母
    r'|\x1b\][^\x07]*\x07'             # OSC BEL 终止
    r'|\x1b\][^\x1b]*\x1b\\'           # OSC ST 终止
    r'|\x1b[PX^_][^\x1b]*\x1b\\'       # DCS/APC/PM/SOS ST 终止
)


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列（CSI / OSC / DCS / APC 等）。"""
    return _ANSI_RE.sub('', text)


def _char_width(ch: str) -> int:
    """返回单个字符的显示宽度（CJK 字符占 2 列）。

    基于 Unicode codepoint 范围实现，与 src/api/renderer/_utils/_display.py
    的 cjk_display_width 基于相同的 CJK 宽度约定，但区间范围存在已知分歧（Hangul
    Jamo 上限、Yi 音节、CJK Extension B 上限、组合变音符号处理、emoji 区间等）。
    建议未来统一到 wcwidth 库（src/ui/_bottom_cursor.py 已使用）。参见 P2-11 记录。
    """
    cp = ord(ch)
    # 零宽字符
    if (cp in (0x00AD,) or
            0x0300 <= cp <= 0x036F or
            0x0483 <= cp <= 0x0489 or
            0x200B <= cp <= 0x200F or
            0x2028 <= cp <= 0x202E or
            0x2060 <= cp <= 0x2069 or
            0xFE00 <= cp <= 0xFE0F or
            cp == 0xFEFF):
        return 0
    # CJK/宽字符范围（含 Emoji、杂项符号、国旗区域指示符等）
    if (0x1100 <= cp <= 0x115F or 0x2329 <= cp <= 0x232A or
            0x2E80 <= cp <= 0xA4CF or 0xA960 <= cp <= 0xA97C or
            0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF or
            0xFE10 <= cp <= 0xFE19 or 0xFE30 <= cp <= 0xFE6F or
            0xFF01 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6 or
            0x1F004 <= cp <= 0x1F251 or 0x20000 <= cp <= 0x3FFFD or
            0x1F300 <= cp <= 0x1F9FF or
            0x231A <= cp <= 0x231B or
            0x23E9 <= cp <= 0x23F3 or
            0x23F8 <= cp <= 0x23FA or
            cp == 0x24C2 or
            0x25AA <= cp <= 0x25AB or
            cp == 0x25B6 or
            cp == 0x25C0 or
            0x25FB <= cp <= 0x25FE or
            0x2600 <= cp <= 0x27BF or
            0x2934 <= cp <= 0x2935 or
            cp == 0x2B50 or
            cp == 0x2B55 or
            cp == 0x3030 or
            cp == 0x303E or
            0x1F1E6 <= cp <= 0x1F1FF):
        return 2
    return 1


def _display_width(text: str) -> int:
    """计算字符串的显示列宽（CJK 字符占 2 列）。

    Args:
        text: 待测量的纯文本字符串（已剥离 ANSI/Rich 标记）

    Returns:
        显示列宽（整数）
    """
    return sum(_char_width(ch) for ch in text)


def _truncate_by_width(text: str, max_width: int) -> str:
    """按显示宽度截断字符串（CJK 字符占 2 列）。

    逐字符累加显示宽度，当累加宽度超过 max_width 时停止。

    Args:
        text: 待截断的纯文本字符串
        max_width: 目标最大显示列宽

    Returns:
        截断后的字符串，其 _display_width <= max_width
    """
    if not text:
        return ""
    result: list[str] = []
    current = 0
    for ch in text:
        cw = _char_width(ch)
        if current + cw > max_width:
            break
        result.append(ch)
        current += cw
    return "".join(result)


def measureElement(component: "TuiComponent", terminal_width: int = 80) -> tuple[int, int]:
    """测量组件渲染后的尺寸。

    调用 component.render() 获取输出，转为纯文本后按终端宽度
    计算实际占用的行数和最大列宽。

    Args:
        component: 要测量的 TuiComponent 实例
        terminal_width: 终端宽度（字符数），默认 80

    Returns:
        (rows, cols) 元组：
        - rows: 组件占用的行数（>= 1，空组件返回 0）
        - cols: 组件占用最大列宽（字符数）
    """
    if terminal_width <= 0:
        _logger.debug("measureElement: terminal_width=%d <= 0, 回退为默认值 80", terminal_width)
        terminal_width = 80

    # 调用 render() 获取输出
    try:
        output = component.render()
    except Exception:
        _logger.debug("measureElement: render() 异常", exc_info=True)
        return (0, 0)

    # 处理 None 返回
    if output is None:
        return (0, 0)

    # 转为纯文本：Rich Text 用 .plain 属性，str 直接使用
    if isinstance(output, Text):
        plain = output.plain
    else:
        plain = str(output)

    # 剥离残留的 ANSI 转义序列
    plain = _strip_ansi(plain)

    # 空文本返回 (0, 0)
    if not plain:
        return (0, 0)

    # 按换行符分割逻辑行
    lines = plain.split('\n')

    total_rows = 0
    max_cols = 0

    for line in lines:
        dw = _display_width(line)
        if dw > max_cols:
            max_cols = dw

        # 空行仍占 1 行
        if dw == 0:
            total_rows += 1
        else:
            # 按终端宽度计算换行后的实际行数
            wrapped_rows = (dw + terminal_width - 1) // terminal_width
            total_rows += max(wrapped_rows, 1)

    return (total_rows, max_cols)
