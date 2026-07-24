"""底部栏交互选择 — 在补全弹窗中运行阻塞式交互选择循环。

从 _bottom_bar.py 提取，使用 Blessed Terminal.inkey() 替代原始
termios/tty/os.read 实现。Blessed 自动处理 CSI/SS3 序列解析。
"""

from __future__ import annotations

import os
import sys
import logging

from ...terminal.blessed import get_terminal

_logger = logging.getLogger(__name__)

# Blessed 按键代码常量
_KEY_UP = 259
_KEY_DOWN = 258
_KEY_ENTER = 343
_KEY_ESCAPE = 361


def _is_cygwin_or_wsl() -> bool:
    """检测当前环境是否为 Cygwin 或 WSL，且标准输入为 tty。

    Cygwin 和 WSL 下 Blessed term.inkey() 可能无法正确解析 ANSI escape 序列，
    需要绕过 Blessed 路径改用原始 I/O 读取。

    WSL 检测分两步，按优先级依次尝试（任一满足即判定为 WSL）：
    1. 读取 /proc/version，若内容（不区分大小写）包含 "microsoft" 则判定为 WSL
       — 覆盖 WSL1 和 WSL2，也覆盖无 WSL_DISTRO_NAME 环境变量的场景
    2. 检查 WSL_DISTRO_NAME 环境变量是否存在（WSL2 下默认存在）
       — 作为 /proc/version 读取失败的兜底（权限不足、文件不存在等）
    两步均失败时判定为非 WSL。

    设计决策：
    - 两步检测互为备灾：/proc/version 覆盖 WSL1（无 WSL_DISTRO_NAME），
      WSL_DISTRO_NAME 覆盖 /proc/version 不可读的场景
    - 异常静默：所有文件读取和 env 检查均用 bare except 包裹，
      确保函数在任何异常场景下都不会抛异常，只返回 False
    - 前置 tty 检查：先检查 os.isatty()，非 tty 环境直接返回 False，
      避免无终端时不必要的文件读取

    Returns:
        True 若环境为 Cygwin 或 WSL 且 stdin 是 tty。
    """
    if not os.isatty(sys.stdin.fileno()):
        return False
    # ── Cygwin 检测 ──
    if sys.platform == 'cygwin':
        return True
    # ── WSL 检测 ──
    try:
        with open("/proc/version", "r") as f:
            content = f.read()
        if "microsoft" in content.lower():
            return True
    except Exception:
        pass
    try:
        if 'WSL_DISTRO_NAME' in os.environ:
            return True
    except Exception:
        pass
    return False


def _save_terminal_settings(fd: int):
    """保存当前终端设置，用于后续恢复。

    Args:
        fd: 终端文件描述符。

    Returns:
        termios 设置列表，可传给 _restore_terminal_settings 恢复。
    """
    from src._compat_termios import termios
    return termios.tcgetattr(fd)


def _safe_completion_idx(bb, items: list) -> int:
    """安全获取 completion_idx，越界时回退到 0 并更新 bb。"""
    idx = bb._completion_idx
    if not (0 <= idx < len(items)):
        idx = 0
        bb._completion_idx = 0
    return idx


def _restore_terminal_settings(fd: int, settings) -> None:
    """恢复终端设置，异常静默。

    Args:
        fd: 终端文件描述符。
        settings: _save_terminal_settings 返回的 termios 设置。
    """
    try:
        from src._compat_termios import termios
        termios.tcsetattr(fd, termios.TCSADRAIN, settings)
    except Exception:
        pass


def _run_selection_raw(
    items: list[str],
    display_items: list[str],
    initial_idx: int,
    title: str,
    bb,
) -> dict:
    """使用原始 I/O 的阻塞式选择循环（Cygwin 降级路径）。

    绕过 Blessed term.inkey()，直接使用 os.read(fd, 1) + select.select()
    读取按键输入，手动解析 ANSI CSI 序列。参考 EscapeMonitor._handle_escape()
    的 ANSI 解析风格。

    处理以下按键：
    - 上箭头（\\x1b[A / CSI A）→ bb.cycle_completion(-1)
    - 下箭头（\\x1b[B / CSI B）→ bb.cycle_completion(1)
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
    except Exception:
        return {"action": "error", "index": None}
    try:
        tty.setcbreak(fd)
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
            except (ValueError, OSError, TypeError, AttributeError):
                continue
            if not ready:
                continue

            try:
                raw = os.read(fd, 1)
                if not raw:
                    continue
            except (ValueError, OSError, TypeError):
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

            # ── Esc 序列处理 ──
            if ch == '\x1b':
                # 检测是否有后续字节（ANSI 序列）
                try:
                    has_more, _, _ = select.select([fd], [], [], 0.05)
                except (ValueError, OSError, TypeError, AttributeError):
                    has_more = False
                if not has_more:
                    # 单独 Esc → 取消
                    return {"action": "cancel", "index": None}

                # 读取后续字节
                try:
                    next_raw = os.read(fd, 1)
                    if not next_raw:
                        return {"action": "cancel", "index": None}
                    next_ch = next_raw.decode("utf-8", errors="replace")
                except (ValueError, OSError, TypeError):
                    return {"action": "cancel", "index": None}

                if next_ch == '[':
                    # CSI 序列：读取参数 + 终结符（参考 EscapeMonitor._handle_escape）
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
                    except (ValueError, OSError, TypeError):
                        pass

                    if terminator == 'A':
                        bb.cycle_completion(-1)
                    elif terminator == 'B':
                        bb.cycle_completion(1)
                    # 其他 CSI 序列（含 ~ 的功能键等）→ 忽略
                elif next_ch == 'O':
                    # SS3 序列（如 F1-F4）→ 消耗后续字节后忽略
                    try:
                        if select.select([fd], [], [], 0.01)[0]:
                            os.read(fd, 1)
                    except (ValueError, OSError, TypeError):
                        pass
                else:
                    # 其他 ESC 组合 → 取消
                    return {"action": "cancel", "index": None}
    except Exception:
        _logger.warning("_run_selection_raw 异常", exc_info=True)
        return {"action": "error", "index": None}
    finally:
        _restore_terminal_settings(fd, settings)
        try:
            _termios.tcflush(fd, _termios.TCIFLUSH)
        except Exception:
            pass


def _drain_stdin_residual(
    fd: int,
    timeout_per_round: float = 0.02,
    rounds: int = 3,
    max_per_round: int = 4096,
) -> None:
    """对 stdin 执行多轮排空，清除终端模式切换后延迟到达的残余字节。

    使用 select.select + os.read 进行非阻塞读取，辅以轮间 tcflush 确定性能清空。
    3 轮 × 20ms 轮询 + 每轮后 tcflush，总超时 ≤60ms。

    Args:
        fd: 终端文件描述符（如 sys.stdin.fileno()）。
        timeout_per_round: 每轮 select 超时时间（秒），默认 0.02（20ms）。
        rounds: 轮数，默认 3。
        max_per_round: 每轮最大读取字节数，默认 4096。
    """
    import select
    for _ in range(rounds):
        ready = False
        try:
            ready, _, _ = select.select([fd], [], [], timeout_per_round)
        except Exception:
            pass
        if ready:
            try:
                os.read(fd, max_per_round)
            except Exception:
                pass
        # 轮间 tcflush：清空可能已到达但被 select 遗漏的字节
        try:
            from src._compat_termios import termios as _termios
            _termios.tcflush(fd, _termios.TCIFLUSH)
        except Exception:
            pass
    # ★ 最后兜底：非阻塞检查 + tcflush，关闭最终轮 tcflush 与函数返回之间的微小间隙
    try:
        r, _, _ = select.select([fd], [], [], 0)
        if r:
            os.read(fd, max_per_round)
    except Exception:
        pass
    try:
        from src._compat_termios import termios as _termios
        _termios.tcflush(fd, _termios.TCIFLUSH)
    except Exception:
        pass


def run_bottom_bar_selection(
    items: list[str],
    display_items: list[str],
    initial_idx: int = 0,
    title: str = "选择",
    bottom_bar=None,
) -> dict:
    """在底部栏补全弹窗中运行交互式选择，返回选中结果。

    使用 Blessed Terminal.inkey() 读取键盘输入，自动处理
    CSI/SS3 箭头序列解析。

    Args:
        items: 原始选项列表（作为替换文本）。应为纯文本，不含 ANSI 码。
        display_items: 显示文本列表（与 items 一一对应）。建议纯文本。
        initial_idx: 初始光标位置。
        title: 弹窗标题。
        bottom_bar: _BottomBar 实例（可选）。传入后避免反向依赖 chat_ui 获取底部栏。

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
        except Exception:
            return {"action": "error", "index": None}

    if _is_cygwin_or_wsl():
        try:
            bb.show_completions(display_items, initial_idx, texts=items, title=title)
        except Exception as exc:
            _logger.warning("Cygwin show_completions 异常: %s", exc, exc_info=True)
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
        except Exception:
            pass
        try:
            return _run_selection_raw(items, display_items, initial_idx, title, bb)
        finally:
            try:
                bb.hide_completions()
            except Exception:
                pass
            if not was_active:
                bb._active = False

    bb.show_completions(display_items, initial_idx, texts=items, title=title)

    # 防御性清空 stdin：作为方案 B 防御层，即使调用方（monitor.stop() 中
    # 方案 A 的 tcflush）未正确清空内核缓冲区，也能在进入 cbreak 前兜底清空，
    # 防止终端模式切换残留的 \n 字节被 term.inkey() 误消费为 Enter 键。
    try:
        from src._compat_termios import termios as _termios
        _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
    except Exception:
        pass

    try:
        term = get_terminal()
        with term.cbreak():  # 替代 tty.setcbreak + termios
            # ── Post-cbreak drain：清空 cbreak 模式切换后残留的 stdin 字节 ──
            # Android/Termux 环境下，cbreak 模式切换可能导致终端驱动重新产生字节。
            # 使用 _drain_stdin_residual() 3轮×20ms排空 + 轮间tcflush覆盖延迟到达的残余字节。
            try:
                _drain_stdin_residual(fd)
            except Exception:
                pass

            # ★ 最后一层防御：非阻塞排空 _drain_stdin_residual 与 term.inkey()
            # 之间的微小间隙。终端模式切换（cooked→cbreak）产生的 \r/\n 残留字节
            # 可能延迟到达（尤其在 Android/Termux 环境下），若恰好落在
            # drain 结束与 inkey 起始之间，会被 inkey 误消费为 Enter 键，
            # 导致选择弹窗立即确认首条消息 → 意外截断会话。
            try:
                import select as _sel
                r, _, _ = _sel.select([fd], [], [], 0.01)
                if r:
                    os.read(fd, 1)
            except Exception:
                pass
            try:
                from src._compat_termios import termios as _termios
                _termios.tcflush(fd, _termios.TCIFLUSH)
            except Exception:
                pass

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
                        return {"action": "confirmed", "index": _safe_completion_idx(bb, items)}
                    elif code == _KEY_ESCAPE:
                        return {"action": "cancel", "index": None}
                    # 其他序列键忽略
                    continue

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

    except Exception as exc:
        _logger.warning("run_bottom_bar_selection Blessed 路径异常，降级到 Raw I/O: %s", exc, exc_info=True)
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
        except Exception:
            pass
        try:
            from src._compat_termios import termios as _termios
            _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
        except Exception:
            pass
        # 降级到 Raw I/O 路径
        try:
            return _run_selection_raw(items, display_items, initial_idx, title, bb)
        except Exception as raw_exc:
            _logger.warning("run_bottom_bar_selection Raw I/O 降级路径异常: %s", raw_exc, exc_info=True)
            return {"action": "error", "index": None}
    finally:
        try:
            bb.hide_completions()
        except Exception:
            pass
        if not was_active:
            bb._active = False
        try:
            # 清空 stdin 缓冲
            from src._compat_termios import termios as _termios
            _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
        except Exception:
            pass
