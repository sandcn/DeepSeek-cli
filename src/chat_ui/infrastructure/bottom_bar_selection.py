"""底部栏交互选择 — 在补全弹窗中运行阻塞式交互选择循环。

迁移自 src/ui/_bottom_bar_selection.py，适配新 BottomBarBridge API：
  - show_completions / cycle_completion / hide_completions 改为仅内存操作
  - 每次状态变更后通过 _render_and_redraw() 触发终端重绘
  - 使用 bb.completion_index 替代 bb._completion_idx

使用 Blessed Terminal.inkey() 读取键盘输入，自动处理 CSI/SS3 序列解析。
"""

from __future__ import annotations

import os
import sys
import logging
import shutil

from ...ui._blessed import get_terminal

_logger = logging.getLogger(__name__)

# Blessed 按键代码常量
_KEY_UP = 259
_KEY_DOWN = 258
_KEY_ENTER = 343
_KEY_ESCAPE = 361


def _render_and_redraw(bb) -> None:
    """从 bridge 状态构建 BottomBarContent VNode，渲染并写入终端。

    访问 bridge 内部状态构建完整的底部栏 VNode 内容（含补全弹窗），
    通过 force_redraw_from_vnode() 写入终端固定区域。

    与主 render 线程使用相同的 output_lock 串行化终端 I/O，
    因此可在 EscapeMonitor 线程中安全调用。
    """
    from ..components.bottom_bar_content import BottomBarContent  # noqa: PLC0415

    tw = shutil.get_terminal_size().columns
    snap = bb.get_completion_snapshot()

    content = BottomBarContent(
        term_width=tw,
        status_text="",
        input_text=bb._last_text,
        input_cursor_pos=bb._input_cursor_pos,
        is_streaming=bb._status_active,
        completion_items=tuple(snap["items"]),
        completion_selected=snap["selected"],
        completion_visible=bb.is_completion_visible,
        completion_title=snap["title"],
        completion_is_selection=snap["is_selection"],
        subagent_slots=bb._subagent_slots,
        claude_style=False,
    )
    bb.force_redraw_from_vnode(content.render())


def run_bottom_bar_selection(
    items: list[str],
    display_items: list[str],
    initial_idx: int = 0,
    title: str = "选择",
    bottom_bar=None,  # BottomBarBridge 实例
) -> dict:
    """在底部栏补全弹窗中运行交互式选择，返回选中结果。

    使用 Blessed Terminal.inkey() 读取键盘输入，自动处理
    CSI/SS3 箭头序列解析。

    Args:
        items: 原始选项列表（作为替换文本）。应为纯文本，不含 ANSI 码。
        display_items: 显示文本列表（与 items 一一对应）。建议纯文本。
        initial_idx: 初始光标位置。
        title: 弹窗标题。
        bottom_bar: BottomBarBridge 实例，传入时直接使用。

    Returns:
        {"action": "confirmed"|"cancel"|"error",
         "index": int | None}
    """
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return {"action": "error", "index": None}

    if bottom_bar is None:
        return {"action": "error", "index": None}

    bb = bottom_bar

    # setup() 幂等：已激活时直接返回，未激活时初始化 DECSTBM
    if not bb._active:
        try:
            bb.setup()
        except Exception:
            return {"action": "error", "index": None}

    bb.show_completions(display_items, initial_idx, texts=items, title=title)
    _render_and_redraw(bb)

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
                        _render_and_redraw(bb)
                    elif code == _KEY_DOWN:
                        bb.cycle_completion(1)
                        _render_and_redraw(bb)
                    elif code == _KEY_ENTER:
                        # ★ Android/Termux 终端可能以 KEY_ENTER(343) 序列发送 Enter
                        idx = bb.completion_index
                        if 0 <= idx < len(items):
                            return {"action": "confirmed", "index": idx}
                    elif code == _KEY_ESCAPE:
                        return {"action": "cancel", "index": None}
                    # 其他序列键忽略
                    continue

                # ── Enter → 确认 ──
                if key in ('\r', '\n'):
                    idx = bb.completion_index
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
            _render_and_redraw(bb)
        except Exception:
            pass
        try:
            # 清空 stdin 缓冲
            import termios as _termios
            _termios.tcflush(sys.stdin, _termios.TCIFLUSH)
        except Exception:
            pass
