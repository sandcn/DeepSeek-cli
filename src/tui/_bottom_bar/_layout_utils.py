"""底部栏公共布局工具 — _is_narrow / _visual_width / _truncate_by_width 唯一真源。

提取自 ``_layout.py`` 与 ``_popup.py`` 的重复实现（2026-07-31 TUI 架构改进步骤 4.4）。
两个模块均改为从此处导入，消除重复定义。函数签名与行为保持原样。
"""

from __future__ import annotations

from src.tui._screen import (
    _get_terminal_size,
    wcswidth_simple,
)


def _is_narrow() -> bool:
    """判断是否为窄屏（宽度 < 60 列）。"""
    w, _ = _get_terminal_size()
    return w < 60


def _visual_width(text: str) -> int:
    """计算字符串的可视宽度（去除 ANSI 转义序列）。"""
    w = 0
    i = 0
    while i < len(text):
        if text[i] == '\033':
            j = i + 1
            if j < len(text) and text[j] == '[':
                j += 1
                while j < len(text) and text[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                i = j + 1 if j < len(text) else len(text)
            elif j < len(text) and text[j] in ']PX^_':
                j += 1
                while j < len(text):
                    if text[j] == '\033' and j + 1 < len(text) and text[j + 1] == '\\':
                        i = j + 2
                        break
                    elif text[j] == '\a':
                        i = j + 1
                        break
                    j += 1
                else:
                    i = len(text)
            else:
                i = j + 1
        else:
            w += wcswidth_simple(text[i])
            i += 1
    return w


def _truncate_by_width(s: str, max_width: int) -> str:
    """按终端列宽截断字符串。"""
    w = 0
    for i, ch in enumerate(s):
        cw = wcswidth_simple(ch)
        if w + cw > max_width:
            return s[:i]
        w += cw
    return s


__all__ = ["_is_narrow", "_visual_width", "_truncate_by_width"]
