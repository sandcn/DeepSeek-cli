"""底部栏交互选择 — 在补全弹窗中运行阻塞式交互选择循环。

从 _bottom_bar.py 提取，使用 Blessed Terminal.inkey() 替代原始
termios/tty/os.read 实现。Blessed 自动处理 CSI/SS3 序列解析。
"""

from __future__ import annotations

import os
import sys
import logging

from ._blessed import get_terminal

_logger = logging.getLogger(__name__)

# Blessed 按键代码常量
_KEY_UP = 259
_KEY_DOWN = 258
_KEY_ENTER = 343
_KEY_ESCAPE = 361


def run_bottom_bar_selection(
    items: list[str],
    display_items: list[str],
    initial_idx: int = 0,
    title: str = "选择",
    bottom_bar=None,  # _BottomBar 实例，传入时直接使用（避免 get_active_chat_ui）
) -> dict:
    """在底部栏补全弹窗中运行交互式选择，返回选中结果。

    使用 Blessed Terminal.inkey() 读取键盘输入，自动处理
    CSI/SS3 箭头序列解析。

    Args:
        items: 原始选项列表（作为替换文本）。应为纯文本，不含 ANSI 码。
        display_items: 显示文本列表（与 items 一一对应）。建议纯文本。
        initial_idx: 初始光标位置。
        title: 弹窗标题。

    Returns:
        {"action": "confirmed"|"cancel"|"error",
         "index": int | None}
    """
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return {"action": "error", "index": None}

    if bottom_bar is not None:
        bb = bottom_bar
    else:
        return {"action": "error", "index": None}

    if not bb._active:
        try:
            bb.setup()
        except Exception:
            return {"action": "error", "index": None}

    bb.show_completions(display_items, initial_idx, texts=items, title=title)

    try:
        term = get_terminal()
        with term.cbreak():  # 替代 tty.setcbreak + termios
            while True:
                try:
                    key = term.inkey(timeout=None)
                except Exception:
                    continue
                if not key:
                    continue

                # ── 功能键（箭头等）─
                if key.is_sequence:
                    code = key.code
                    if code == _KEY_UP:
                        bb.cycle_completion(-1)
                    elif code == _KEY_DOWN:
                        bb.cycle_completion(1)
                    elif code == _KEY_ENTER:
                        # ★ Android/Termux 终端可能以 KEY_ENTER(343) 序列发送 Enter
                        idx = bb._completion_idx
                        if 0 <= idx < len(items):
                            return {"action": "confirmed", "index": idx}
                    elif code == _KEY_ESCAPE:
                        return {"action": "cancel", "index": None}
                    # 其他序列键忽略
                    continue

                # ── Enter → 确认 ──
                if key in ('\r', '\n'):
                    idx = bb._completion_idx
                    if 0 <= idx < len(items):
                        return {"action": "confirmed", "index": idx}

                # ── Esc（单独收到）─
                if key == '\x1b':
                    return {"action": "cancel", "index": None}

    except Exception as exc:
        _logger.debug("run_bottom_bar_selection 异常: %s", exc)
        return {"action": "error", "index": None}
    finally:
        try:
            bb.hide_completions()
        except Exception:
            pass
        try:
            # 清空 stdin 缓冲
            import termios as _termios
            _termios.tcflush(sys.stdin, _termios.TCIFLUSH)
        except Exception:
            pass
