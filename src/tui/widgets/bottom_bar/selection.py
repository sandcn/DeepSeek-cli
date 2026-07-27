"""底部栏交互选择 — 在补全弹窗中运行阻塞式交互选择循环。

从 _bottom_bar.py 提取，使用 Blessed Terminal.inkey() 替代原始
termios/tty/os.read 实现。Blessed 自动处理 CSI/SS3 序列解析。
"""

from __future__ import annotations

import logging
import os
import sys

from ...terminal.blessed import get_terminal

_logger = logging.getLogger(__name__)
from .blessed import (
    _is_cygwin_or_wsl,
    _save_terminal_settings,
    _restore_terminal_settings,
    _drain_stdin_residual,
)

# Blessed 按键代码常量
_KEY_UP = 259
_KEY_DOWN = 258
_KEY_ENTER = 343
_KEY_ESCAPE = 361


def _safe_completion_idx(bb, items: list) -> int:
    """安全获取 completion_idx，越界时回退到 0 并更新 bb。"""
    idx = bb._completion_idx
    if not (0 <= idx < len(items)):
        idx = 0
        bb._completion_idx = 0
    return idx


def _run_selection_raw(
    items: list[str],
    display_items: list[str],
    initial_idx: int,
    title: str,
    bb,
    input_instance=None,
) -> dict:
    """使用原始 I/O 的阻塞式选择循环（Cygwin 降级路径）。

    绕过 Blessed term.inkey()，直接使用 os.read(fd, 1) + select.select()
    读取按键输入。CSI/SS3 序列解析委托给 InputParser（通过 input_instance）。

    处理以下按键：
    - 上箭头（CSI A）→ bb.cycle_completion(-1)
    - 下箭头（CSI B）→ bb.cycle_completion(1)
    - Enter（\\r / \\n）→ 确认选择
    - r → resume（从此恢复，保留当前消息）
    - d → delete（从此删除）
    - R → resume_all（恢复全部）
    - Esc（单独 \\x1b）→ 取消选择

    Args:
        items: 原始选项列表（作为替换文本）。
        display_items: 显示文本列表（与 items 一一对应）。
        initial_idx: 初始光标位置。
        title: 弹窗标题。
        bb: _BottomBar 实例。
        input_instance: Input 门面类实例（可选），提供 InputParser 用于 CSI/SS3 解析。

    Returns:
        {"action": "confirmed"|"cancel"|"error"|"resume"|"delete"|"resume_all",
         "index": int | None}
    """
    import select
    from src._compat_termios import termios as _termios, tty

    if not items:
        return {"action": "error", "index": None}

    fd = sys.stdin.fileno()
    try:
        settings = _save_terminal_settings(fd)
    except Exception as exc:
        _logger.warning("_run_selection_raw: 保存终端设置失败: %s", exc)
        return {"action": "error", "index": None}
    try:
        tty.setcbreak(fd)
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
            except (ValueError, OSError, TypeError, AttributeError) as exc:
                _logger.debug("select 异常（非关键，继续轮询）: %s", exc)
                continue
            if not ready:
                continue

            try:
                raw = os.read(fd, 1)
                if not raw:
                    continue
            except (ValueError, OSError, TypeError) as exc:
                _logger.debug("os.read 异常（非关键，继续轮询）: %s", exc)
                continue

            ch = raw.decode("utf-8", errors="replace")

            # ── r → resume ──
            if ch == 'r':
                return {"action": "resume", "index": _safe_completion_idx(bb, items)}

            # ── d → delete ──
            if ch == 'd':
                return {"action": "delete", "index": _safe_completion_idx(bb, items)}

            # ── R → resume_all ──
            if ch == 'R':
                return {"action": "resume_all", "index": None}

            # ── Enter → 确认选择 ──
            if ch in ('\r', '\n'):
                return {"action": "confirmed", "index": _safe_completion_idx(bb, items)}

            # ── Esc 序列处理（委托 InputParser） ──
            if ch == '\x1b':
                if input_instance is not None:
                    key_event = input_instance.parse_sequence(fd)
                    if key_event.kind == "arrow_up":
                        bb.cycle_completion(-1)
                    elif key_event.kind == "arrow_down":
                        bb.cycle_completion(1)
                    elif key_event.kind == "escape":
                        return {"action": "cancel", "index": None}
                    elif key_event.kind == "interrupt":
                        return {"action": "cancel", "index": None}
                    # 其他 CSI/SS3 序列 → 忽略（继续轮询）
                else:
                    # ── 降级路径（无 Input 实例时使用手动解析） ──
                    try:
                        has_more, _, _ = select.select([fd], [], [], 0.05)
                    except (ValueError, OSError, TypeError, AttributeError) as exc:
                        _logger.debug("Esc select 异常，视为无后续字节: %s", exc)
                        has_more = False
                    if not has_more:
                        return {"action": "cancel", "index": None}

                    try:
                        next_raw = os.read(fd, 1)
                        if not next_raw:
                            return {"action": "cancel", "index": None}
                        next_ch = next_raw.decode("utf-8", errors="replace")
                    except (ValueError, OSError, TypeError) as exc:
                        _logger.debug("Esc os.read 异常，视为取消: %s", exc)
                        return {"action": "cancel", "index": None}

                    if next_ch == '[':
                        terminator = None
                        try:
                            while select.select([fd], [], [], 0.01)[0]:
                                c_raw = os.read(fd, 1)
                                if not c_raw:
                                    break
                                c = c_raw.decode("utf-8", errors="replace")
                                if c.isalpha() or c == '~':
                                    terminator = c
                                    break
                        except (ValueError, OSError, TypeError) as exc:
                            _logger.debug("CSI 序列读取异常: %s", exc)

                        if terminator == 'A':
                            bb.cycle_completion(-1)
                        elif terminator == 'B':
                            bb.cycle_completion(1)
                    elif next_ch == 'O':
                        try:
                            if select.select([fd], [], [], 0.01)[0]:
                                os.read(fd, 1)
                        except (ValueError, OSError, TypeError) as exc:
                            _logger.debug("SS3 序列处理异常: %s", exc)
                    else:
                        return {"action": "cancel", "index": None}
    except Exception as exc:
        _logger.warning("_run_selection_raw 异常: %s", exc)
        return {"action": "error", "index": None}
    finally:
        _restore_terminal_settings(fd, settings)
        try:
            _termios.tcflush(fd, _termios.TCIFLUSH)
        except Exception as exc:
            _logger.debug("_run_selection_raw finally tcflush 异常: %s", exc)


def run_bottom_bar_selection(
    items: list[str],
    display_items: list[str],
    initial_idx: int = 0,
    title: str = "选择",
    bottom_bar=None,
    input_instance=None,
) -> dict:
    """在底部栏补全弹窗中运行交互式选择，返回选中结果。

    使用 Blessed Terminal.inkey() 读取键盘输入，自动处理
    CSI/SS3 箭头序列解析。

    进入主循环前，执行 Blessed-native 暖机排空（150ms 窗口，
    term.inkey(timeout=0.02) 短超时探测）：过滤终端模式切换
    (cooked→cbreak) 产生的 \\r/\\n 延迟残留字节，真实按键则退出暖机
    进入正常处理。与 _drain_stdin_residual() 协作形成双层防御。

    Args:
        items: 原始选项列表（作为替换文本）。应为纯文本，不含 ANSI 码。
        display_items: 显示文本列表（与 items 一一对应）。建议纯文本。
        initial_idx: 初始光标位置。
        title: 弹窗标题。
        bottom_bar: _BottomBar 实例（可选）。传入后避免反向依赖 chat_ui 获取底部栏。
        input_instance: Input 门面类实例（可选）。降级路径中传递给 _run_selection_raw()
                       用于 CSI/SS3 解析。

    Returns:
        {"action": "confirmed"|"cancel"|"error"|"resume"|"delete"|"resume_all",
         "index": int | None}
    """
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return {"action": "error", "index": None}

    if not items:
        return {"action": "error", "index": None}

    bb = bottom_bar
    if bb is None:
        from ....tui.state.consumer_registry import get_active_chat_ui  # fallback — 让 ui/tui 调用方传入 bottom_bar
        chat_ui = get_active_chat_ui()
        if chat_ui is None:
            return {"action": "error", "index": None}
        bb = chat_ui.bottom_bar
        if bb is None:
            return {"action": "error", "index": None}

    was_active = bb._active
    if not bb._active:
        try:
            bb.setup()
        except Exception as exc:
            _logger.warning("bb.setup() 异常: %s", exc)
            return {"action": "error", "index": None}

    if _is_cygwin_or_wsl():
        try:
            bb.show_completions(display_items, initial_idx, texts=items, title=title)
        except Exception as exc:
            _logger.warning("Cygwin show_completions 异常: %s", exc)
            if not was_active:
                bb._active = False
            return {"action": "error", "index": None}
        # ★ 防御性清空 stdin：与下方 Blessed 路径一致。monitor.stop() 恢复终端
        #    cooked 模式后，Cygwin PTY 可能在模式切换时产生残留控制序列字节
        #    （如 ESC 字节），若不清空，_run_selection_raw 一进入就会读到残留
        #    的 ESC 字节而立即返回 cancel，导致选择界面"闪一下"就消失。
        #    放在 show_completions 之后、_run_selection_raw 之前，与 Blessed 路径
        #    （show_completions → tcflush → cbreak → drain → inkey）顺序对齐，
        #    统一防御 show_completions 终端写入回显残留 + monitor.stop 残留。
        try:
            from src._compat_termios import termios as _termios
            _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
        except Exception as exc:
            _logger.debug("Cygwin 防御性 tcflush 异常: %s", exc)
        try:
            return _run_selection_raw(items, display_items, initial_idx, title, bb, input_instance)
        finally:
            try:
                bb.hide_completions()
            except Exception as exc:
                _logger.debug("Cygwin hide_completions 异常: %s", exc)
            if not was_active:
                bb._active = False

    bb.show_completions(display_items, initial_idx, texts=items, title=title)

    # 防御性清空 stdin：作为方案 B 防御层，即使调用方（monitor.stop() 中
    # 方案 A 的 tcflush）未正确清空内核缓冲区，也能在进入 cbreak 前兜底清空，
    # 防止终端模式切换残留的 \n 字节被 term.inkey() 误消费为 Enter 键。
    try:
        from src._compat_termios import termios as _termios
        _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
    except Exception as exc:
        _logger.debug("方案 B 防御性 tcflush 异常: %s", exc)

    try:
        term = get_terminal()
        with term.cbreak():  # 替代 tty.setcbreak + termios
            # ── Post-cbreak drain：清空 cbreak 模式切换后残留的 stdin 字节 ──
            # Android/Termux 环境下，cbreak 模式切换可能导致终端驱动重新产生字节。
            # 使用 _drain_stdin_residual() 3轮×20ms排空 + 轮间tcflush覆盖延迟到达的残余字节。
            try:
                _drain_stdin_residual(fd)
            except Exception as exc:
                _logger.debug("Post-cbreak drain 异常: %s", exc)

            # ★ Blessed-native 暖机排空：弥补 _drain_stdin_residual 与 term.inkey()
            # 之间的时序间隙。终端模式切换（cooked→cbreak）产生的 \r/\n 残留字节
            # 可能延迟到达（尤其在 Android/Termux 环境下），若恰好落在
            # drain 结束与 inkey 起始之间，会被 inkey 误消费为 Enter 键，
            # 导致选择弹窗立即确认首条消息 → 意外截断会话。
            # 暖机阶段：用 term.inkey(timeout=0.02) 短超时探测（与主循环同路径），
            # 在 150ms 窗口内过滤 \r/\n 残留，真实按键退出暖机统一处理。
            import time as _time
            _warmup_deadline = _time.monotonic() + 0.15
            while _time.monotonic() < _warmup_deadline:
                try:
                    key = term.inkey(timeout=0.02)
                except Exception as exc:
                    _logger.debug("暖机 inkey 异常: %s", exc)
                    continue
                if not key:
                    continue
                # \r/\n 残留 → 丢弃并重置暖机截止时间（应对连续残留）
                if str(key) in ('\r', '\n'):
                    _warmup_deadline = _time.monotonic() + 0.15
                    continue
                # 真实按键 → 退出暖机，统一分发
                result = _handle_key(key, bb, items)
                if result is not None:
                    return result
                break

            def _handle_key(key, bb, items):
                """处理单个按键分发。返回 dict 表示需立即返回，返回 None 表示继续循环。

                闭包捕获 _safe_completion_idx（模块级函数），通过参数接收 bb 和 items。
                """
                # ── 功能键（箭头等）─
                if key.is_sequence:
                    code = key.code
                    if code == _KEY_UP:
                        bb.cycle_completion(-1)
                    elif code == _KEY_DOWN:
                        bb.cycle_completion(1)
                    elif code == _KEY_ENTER:
                        # ★ Android/Termux 终端可能以 KEY_ENTER(343) 序列发送 Enter
                        return {"action": "confirmed", "index": _safe_completion_idx(bb, items)}
                    elif code == _KEY_ESCAPE:
                        return {"action": "cancel", "index": None}
                    # 其他序列键忽略
                    return None

                # ── r → resume（从此恢复）──
                if key == 'r':
                    return {"action": "resume", "index": _safe_completion_idx(bb, items)}

                # ── d → delete（从此删除）──
                if key == 'd':
                    return {"action": "delete", "index": _safe_completion_idx(bb, items)}

                # ── R → resume_all（恢复全部）──
                # 注意：必须放在 key in ('\r', '\n') 之前，因为 ASCII 中 'R'(82) 与 '\r'(13) 不同
                if key == 'R':
                    return {"action": "resume_all", "index": None}

                # ── Enter → 确认 ──
                if key in ('\r', '\n'):
                    return {"action": "confirmed", "index": _safe_completion_idx(bb, items)}

                # ── Esc（单独收到）─
                if key == '\x1b':
                    return {"action": "cancel", "index": None}

                return None

            while True:
                try:
                    key = term.inkey(timeout=None)
                except Exception as exc:
                    _logger.debug("term.inkey 异常（非关键，继续轮询）: %s", exc)
                    continue
                if not key:
                    continue

                result = _handle_key(key, bb, items)
                if result is not None:
                    return result

    except Exception as exc:
        _logger.warning("run_bottom_bar_selection Blessed 路径异常，降级到 Raw I/O: %s", exc)
        # ── 万能降级兜底 ──
        # Blessed term.inkey() 在某些终端环境（如 WSL 中未走 _is_cygwin_or_wsl()
        # 前置检测的场景、Termux 特定版本、或其他非标准 PTY）可能抛出异常。
        # 此降级路径与 _is_cygwin_or_wsl() 前置检测互补：
        #   - _is_cygwin_or_wsl() 在进入 Blessed 路径前就切换到 Raw I/O
        #   - 此降级路径在 Blessed 路径运行时才出异常时触发，作为万能兜底
        # 降级前清理终端状态（hide_completions + tcflush），确保 _run_selection_raw
        # 不会读到残留的控制序列字节。
        try:
            bb.hide_completions()
        except Exception as exc:
            _logger.debug("降级前 hide_completions 异常: %s", exc)
        try:
            from src._compat_termios import termios as _termios
            _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
        except Exception as exc:
            _logger.debug("降级前 tcflush 异常: %s", exc)
        # 降级到 Raw I/O 路径
        try:
            return _run_selection_raw(items, display_items, initial_idx, title, bb, input_instance)
        except Exception as raw_exc:
            _logger.warning("run_bottom_bar_selection Raw I/O 降级路径异常: %s", raw_exc)
            return {"action": "error", "index": None}
    finally:
        try:
            bb.hide_completions()
        except Exception as exc:
            _logger.debug("finally hide_completions 异常: %s", exc)
        if not was_active:
            bb._active = False
        try:
            # 清空 stdin 缓冲
            from src._compat_termios import termios as _termios
            _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
        except Exception as exc:
            _logger.debug("finally tcflush 异常: %s", exc)
