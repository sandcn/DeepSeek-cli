"""底部栏交互选择 — 在补全弹窗中运行阻塞式交互选择循环。

从 _bottom_bar.py 提取，纯标准库实现（termios/tty/os/select），无外部依赖。
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty


def run_bottom_bar_selection(
    items: list[str],
    display_items: list[str],
    initial_idx: int = 0,
    title: str = "选择",
) -> dict:
    """在底部栏补全弹窗中运行交互式选择，返回选中结果。

    纯标准库实现（termios/tty/os/select），无外部库依赖。
    同时处理 CSI（\\x1b[A/B）和 SS3（\\x1bOA/B）两种箭头序列。

    Args:
        items: 原始选项列表（作为替换文本）。应为纯文本，不含 ANSI 码。
        display_items: 显示文本列表（与 items 一一对应）。建议纯文本。
        initial_idx: 初始光标位置。
        title: 弹窗标题。

    Returns:
        {"action": "confirmed"|"cancel"|"error",
         "index": int | None}
    """
    from ..chat_ui import get_active_chat_ui

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return {"action": "error", "index": None}

    chat_ui = get_active_chat_ui()
    if chat_ui is None:
        return {"action": "error", "index": None}
    bb = chat_ui._bottom_bar
    if bb is None:
        return {"action": "error", "index": None}

    if not bb._active:
        try:
            bb.setup()
        except Exception:
            return {"action": "error", "index": None}

    bb.show_completions(display_items, initial_idx, texts=items, title=title)

    old_settings = None
    try:
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        termios.tcflush(fd, termios.TCIFLUSH)

        while True:
            try:
                ready, _, _ = select.select([fd], [], [], None)
            except (ValueError, OSError):
                continue
            if not ready:
                continue

            try:
                raw = os.read(fd, 1)
                if not raw:
                    continue
            except (ValueError, OSError):
                continue

            b = raw[0]

            # ── ESC / ANSI 序列 ──
            if b == 0x1b:
                try:
                    has_more, _, _ = select.select([fd], [], [], 0.3)
                    if has_more:
                        nxt = os.read(fd, 1)
                        if nxt == b'[':
                            # CSI: \x1b[A ↑, \x1b[B ↓, \x1b[C →, \x1b[D ←
                            has_term, _, _ = select.select([fd], [], [], 0.1)
                            if has_term:
                                term = os.read(fd, 1)
                                if term == b'A':
                                    bb.cycle_completion(-1)
                                elif term == b'B':
                                    bb.cycle_completion(1)
                            continue
                        elif nxt == b'O':
                            # SS3: \x1bOA ↑, \x1bOB ↓
                            has_term, _, _ = select.select([fd], [], [], 0.1)
                            if has_term:
                                term = os.read(fd, 1)
                                if term == b'A':
                                    bb.cycle_completion(-1)
                                elif term == b'B':
                                    bb.cycle_completion(1)
                            continue
                except (ValueError, OSError):
                    pass
                return {"action": "cancel", "index": None}

            # ── Enter → 确认 ──
            elif b in (0x0d, 0x0a):
                idx = bb._completion_idx
                if 0 <= idx < len(items):
                    return {"action": "confirmed", "index": idx}

    except Exception:
        return {"action": "error", "index": None}
    finally:
        if old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
        try:
            bb.hide_completions()
        except Exception:
            pass
        try:
            while select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.read(1)
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass
