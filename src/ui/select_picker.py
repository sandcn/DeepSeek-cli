"""选择器 — 底部栏补全弹窗交互选择。

移除 prompt_toolkit 依赖，改用 _BottomBar 的补全弹窗 + 纯标准库 I/O
（termios/tty/os/select），入口与原有接口兼容。

接口：
  run_picker(title, options, multi_select=False, default_options=None, timeout=120)
    → {"selected": [str], "action": "confirmed"|"cancel"|"timeout"|"non_interactive"}
  run_picker_async(title, options, ...)  — 同 run_picker（当前为同步包装）
"""

from __future__ import annotations

import json
import os
import select
import sys
import termios
import time
import tty

from ..chat_ui import get_active_chat_ui


def _flush_stdin():
    """清空 stdin 残留字节。"""
    while select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.read(1)
    try:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (OSError, AttributeError):
        pass


def _save_termios() -> dict | None:
    """保存当前终端设置，用于后续强制恢复。"""
    try:
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            return {"fd": fd, "old": termios.tcgetattr(fd)}
    except Exception:
        return None
    return None


def _restore_termios(guard: dict | None) -> None:
    """强制恢复终端设置（兜底清理）。"""
    if guard is None:
        return
    try:
        termios.tcsetattr(guard["fd"], termios.TCSADRAIN, guard["old"])
    except Exception:
        pass


def run_picker(
    title: str,
    options: list[str],
    multi_select: bool = False,
    default_options: list[str] | None = None,
    timeout: int = 120,
) -> dict:
    """
    在底部栏补全弹窗中运行交互式选择。

    纯标准库实现（termios/tty/os/select），无外部库依赖。
    同时处理 CSI（\\x1b[A/B）和 SS3（\\x1bOA/B）两种箭头序列。

    Args:
        title: 选择界面标题
        options: 可选选项列表
        multi_select: 是否允许多选
        default_options: 默认选中的选项列表
        timeout: 超时时间（秒），0 表示无超时

    Returns:
        {"selected": [选项文本列表], "action": "confirmed"|"cancel"|"timeout"|"non_interactive"}
    """
    if not options:
        return {"selected": [], "action": "non_interactive"}

    # 非交互环境检测
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return {"selected": list(default_options or []), "action": "non_interactive"}

    chat_ui = get_active_chat_ui()
    if chat_ui is None:
        return {"selected": list(default_options or []), "action": "non_interactive"}
    bb = chat_ui._bottom_bar
    if bb is None:
        return {"selected": list(default_options or []), "action": "non_interactive"}

    if not bb._active:
        try:
            bb.setup()
        except Exception:
            return {"selected": list(default_options or []), "action": "non_interactive"}

    default_options = default_options or []
    default_indices = {i for i, o in enumerate(options) if o in default_options}

    # 构建显示文本
    if multi_select:
        display_items = [
            f"{'✓' if i in default_indices else ' '}  {opt}"
            for i, opt in enumerate(options)
        ]
    else:
        display_items = [f"{i + 1}. {opt}" for i, opt in enumerate(options)]

    # 初始光标
    initial_idx = 0
    if default_indices:
        initial_idx = min(default_indices)
    initial_idx = min(initial_idx, len(options) - 1)

    # 多选状态
    selected_indices: set[int] = set(default_indices)
    current_idx = initial_idx

    bb.show_completions(display_items, current_idx, texts=options, title=title)

    old_settings = None
    guard = _save_termios()
    deadline = None if timeout <= 0 else time.monotonic() + timeout

    try:
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        termios.tcflush(fd, termios.TCIFLUSH)

        while True:
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (ValueError, OSError):
                continue
            if not ready:
                break  # 超时

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
                            has_term, _, _ = select.select([fd], [], [], 0.1)
                            if has_term:
                                term = os.read(fd, 1)
                                if term == b'A':
                                    current_idx = bb.cycle_completion(-1)
                                elif term == b'B':
                                    current_idx = bb.cycle_completion(1)
                            continue
                        elif nxt == b'O':
                            has_term, _, _ = select.select([fd], [], [], 0.1)
                            if has_term:
                                term = os.read(fd, 1)
                                if term == b'A':
                                    current_idx = bb.cycle_completion(-1)
                                elif term == b'B':
                                    current_idx = bb.cycle_completion(1)
                            continue
                except (ValueError, OSError):
                    pass
                bb.hide_completions()
                return {"selected": list(default_options), "action": "cancel"}

            # ── 空格 → 切换选中（多选） ──
            elif b == 0x20 and multi_select:
                idx = bb._completion_idx
                if 0 <= idx < len(options):
                    if idx in selected_indices:
                        selected_indices.discard(idx)
                    else:
                        selected_indices.add(idx)
                    # 更新显示
                    new_disp = []
                    for i, opt in enumerate(options):
                        prefix = "✓ " if i in selected_indices else "  "
                        new_disp.append(f"{prefix}{opt}")
                    show_idx = min(bb._completion_idx, len(new_disp) - 1)
                    bb.show_completions(new_disp, show_idx, texts=options, title=title)
                continue

            # ── Enter → 确认 ──
            elif b in (0x0d, 0x0a):
                if multi_select:
                    selected = [options[i] for i in sorted(selected_indices)]
                    if not selected:
                        selected = list(default_options or [])
                    bb.hide_completions()
                    return {"selected": selected, "action": "confirmed"}
                else:
                    idx = bb._completion_idx
                    if 0 <= idx < len(options):
                        chosen = options[idx]
                        bb.hide_completions()
                        return {"selected": [chosen], "action": "confirmed"}
                    continue

        # 超时
        bb.hide_completions()
        return {"selected": list(default_options or []), "action": "timeout"}

    except Exception:
        return {"selected": list(default_options or []), "action": "timeout"}
    finally:
        if old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
        _restore_termios(guard)
        try:
            bb.hide_completions()
        except Exception:
            pass
        _flush_stdin()


async def run_picker_async(
    title: str,
    options: list[str],
    multi_select: bool = False,
    default_options: list[str] | None = None,
    timeout: int = 120,
) -> dict:
    """异步运行交互式选择器（当前为同步包装，行为与 run_picker 一致）。

    保留此入口兼容 async 调用方。
    """
    return run_picker(title, options, multi_select, default_options, timeout)
