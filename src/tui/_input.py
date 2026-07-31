"""Input — 统一 TUI 输入管理（自包含单文件，精简版）。

合并原 src/tui/input.py 的全部逻辑，关键改动：
  - TerminalWidthCache 从 _screen.py 导入（替代 blessed 路径）
  - _compute_cursor_visual_pos 及其依赖内联，使用 wcswidth_simple() 替代 wcwidth.wcswidth()
  - 其余公开 API 完全兼容旧 Input 类

设计模式：
  - 模板方法（Template Method）：read_stdin_once() 骨架，_do_interrupt()/_handle_special_key() 具体步骤
  - 直接分发：process_events() 在 render 线程每帧调用，循环读取所有待处理输入并分发到缓冲/回调

线程模型：
  - Render 线程（daemon）：_drain_queue() 中每帧调用 process_events()，一次性处理所有 stdin 输入，统一处理 stdin 和渲染
"""

from __future__ import annotations

import os
import select
import time
import threading
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src._compat import dataclass
from src.api.escape_monitor._history import (
    _read_history_file,
    _append_to_history_file,
    _compact_history_file,
    _HISTORY_MAX_ENTRIES,
    _HISTORY_COMPACT_RATIO,
    _EOF_THRESHOLD,
    _SELECT_ERROR_THRESHOLD,
    UNIX_SELECT_TIMEOUT,
)
from src._compat_termios import HAS_TERMIOS, termios
from src.api.interrupt_async import request_interrupt_async
from src.tui._screen import wcswidth_simple, TerminalWidthCache

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

_CSI_READ_TIMEOUT = 0.01     # CSI 参数读取超时（秒）
_SS3_READ_TIMEOUT = 0.01     # SS3 读取超时（秒）
_UTF8_READ_TIMEOUT = 0.05    # UTF-8 多字节序列读取超时（秒）

# ═══════════════════════════════════════════════════════════
# 光标视觉位置计算（内联自 widgets/bottom_bar/cursor.py）
# 使用 wcswidth_simple() 替代 wcwidth.wcswidth()
# ═══════════════════════════════════════════════════════════

_TAB_WIDTH = 4  # 制表符宽度（列数）


def _expand_tabs(text: str, start_col: int = 0, tab_width: int | None = None) -> str:
    """将制表符按制表位展开为空格。

    每个 \\t 跳到下一个制表位列（tab_width 的整数倍），
    用空格填充至该列。

    Args:
        text: 含制表符的文本。
        start_col: 起始列（0-based）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后的纯空格文本。
    """
    if tab_width is None:
        tab_width = _TAB_WIDTH
    if '\t' not in text:
        return text
    result = []
    col = start_col
    for ch in text:
        if ch == '\n':
            result.append(ch)
            col = 0
        elif ch == '\t':
            spaces = tab_width - (col % tab_width)
            result.append(' ' * spaces)
            col += spaces
        else:
            cw = wcswidth_simple(ch)
            result.append(ch)
            col += cw if cw >= 0 else 1
    return ''.join(result)


def _tab_pos_to_expanded(text: str, pos: int,
                         tab_width: int | None = None) -> int:
    """将含制表符文本中的字符位置映射到展开后的位置。

    Args:
        text: 含制表符的原始文本。
        pos: 原始文本中的字符索引（<0 返回 -1）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后文本中对应的字符索引。
    """
    if pos < 0:
        return -1
    if tab_width is None:
        tab_width = _TAB_WIDTH
    expanded_pos = 0
    col = 0
    for i, ch in enumerate(text):
        if i >= pos:
            break
        if ch == '\t':
            spaces = tab_width - (col % tab_width)
            expanded_pos += spaces
            col += spaces
        elif ch == '\n':
            expanded_pos += 1
            col = 0
        else:
            cw = wcswidth_simple(ch)
            expanded_pos += 1
            col += cw if cw >= 0 else 1
    return expanded_pos


def _wrap_by_width(s: str, max_width: int) -> list[str]:
    """按终端列宽拆分文本为多行，每行不超过 max_width 列。

    优先按 \\n 拆分（强制换行），再对每段按列宽拆行。
    调用方应先通过 _expand_tabs 展开制表符。
    """
    if max_width <= 0 or not s:
        return [s] if s else [""]
    lines: list[str] = []
    for segment in s.split('\n'):
        remaining = segment
        while remaining:
            w = 0
            idx = 0
            for i, ch in enumerate(remaining):
                cw = wcswidth_simple(ch) if wcswidth_simple(ch) >= 0 else 1
                if w + cw > max_width:
                    break
                w += cw
                idx = i + 1
            if idx == 0:
                idx = 1
            lines.append(remaining[:idx])
            remaining = remaining[idx:]
        if not segment:
            lines.append("")
    return lines if lines else [""]


def _compute_cursor_visual_pos(
    text: str, cursor_pos: int, max_width: int,
) -> tuple[int, int]:
    """计算光标在带 \\n 的文本中的视觉位置（行号, 列号）。

    将文本按 \\n 拆分为逻辑行，每行分别制表符展开和按列宽拆行，
    定位光标所在逻辑行，累计前面逻辑行的视觉行数得到总行号偏移。

    Args:
        text: 原始输入文本（含 \\n）。
        cursor_pos: 光标在原始文本中的字符偏移（-1=末尾）。
        max_width: 每行最大列宽。

    Returns:
        (visual_line_idx, visual_col) —— 均为 0-based。
    """
    if not text:
        return (0, 0)

    # 确定绝对光标位置
    if cursor_pos < 0:
        abs_cursor = len(text)
    else:
        abs_cursor = cursor_pos

    # 拆分为逻辑行
    lines = text.split('\n')
    cum = 0  # 累计原始字符索引
    for logical_idx, logical_line in enumerate(lines):
        line_len = len(logical_line)
        if abs_cursor <= cum + line_len:
            # 光标在此逻辑行中（或在行末的 \n 上）
            pos_in_line = abs_cursor - cum

            # 展开并拆行
            expanded = _expand_tabs(logical_line)
            wrapped = _wrap_by_width(expanded, max_width)

            # 计算此逻辑行内光标所处视觉行和列
            expanded_in_line = _tab_pos_to_expanded(logical_line, pos_in_line)
            if expanded_in_line < 0:
                # 末尾
                last_seg = wrapped[-1] if wrapped else ""
                col_in_line = wcswidth_simple(last_seg)
                visual_line_in_logical = len(wrapped) - 1 if wrapped else 0
            else:
                cum2 = 0
                visual_line_in_logical = 0
                for i, seg in enumerate(wrapped):
                    if expanded_in_line <= cum2 + len(seg):
                        visual_line_in_logical = i
                        prefix = seg[:expanded_in_line - cum2]
                        col_in_line = wcswidth_simple(prefix)
                        break
                    cum2 += len(seg)
                else:
                    visual_line_in_logical = len(wrapped) - 1 if wrapped else 0
                    col_in_line = wcswidth_simple(wrapped[-1]) if wrapped else 0

            # 累计前面逻辑行的视觉行数
            total_before = 0
            for prev_line in lines[:logical_idx]:
                prev_expanded = _expand_tabs(prev_line)
                total_before += len(_wrap_by_width(prev_expanded, max_width))

            return (total_before + visual_line_in_logical, col_in_line)

        # 此逻辑行已消耗：字符数 + \n 的 1 个字符
        cum += line_len + 1

    # 超出范围 → 末尾
    last_line = lines[-1] if lines else ""
    expanded = _expand_tabs(last_line)
    wrapped = _wrap_by_width(expanded, max_width)
    last_seg = wrapped[-1] if wrapped else ""
    col = wcswidth_simple(last_seg)
    total_before = 0
    for prev_line in lines[:-1]:
        prev_expanded = _expand_tabs(prev_line)
        total_before += len(_wrap_by_width(prev_expanded, max_width))
    visual_row = total_before + (len(wrapped) - 1 if wrapped else 0)
    return (visual_row, col)


# ═══════════════════════════════════════════════════════════
# KeyEvent — 按键事件数据类
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class KeyEvent:
    """按键事件数据类。

    字段:
        kind: 按键类型标识字符串
        char: 可打印字符值（kind="char" 时有效）
        modifier: 修饰键位掩码（CSI u 模式使用，1=无修饰, 2=Shift, 3=Alt, 5=Ctrl）
        keycode: CSI u 键码（如 13=Enter）
        raw: 原始字节序列（调试用）
    """
    kind: str        # "char" | "enter" | "tab" | "backspace" | "escape" |
                     # "arrow_up" | "arrow_down" | "arrow_left" | "arrow_right" |
                     # "home" | "end" | "delete" | "ctrl_key" | "interrupt" | "csi_u" | "unknown"
    char: str = ""
    modifier: int = 0
    keycode: int = 0
    raw: bytes = b""


# ═══════════════════════════════════════════════════════════
# Input — 统一输入管理类（自包含）
# ═══════════════════════════════════════════════════════════

class Input:
    """统一输入管理类（自包含）。

    内联 InputParser（ANSI 解析）、InputBuffer（缓冲+历史）、
    CursorPositioner（光标定位）。
    stdin 读取由 Render 线程通过 read_stdin_once() 驱动。

    构造函数:
        fd: stdin 文件描述符（sys.stdin.fileno()）
        history_file: 历史文件路径
        term_width_cache: 可选，默认使用 TerminalWidthCache.get_default()
        cursor_tracker: 可选，全局光标追踪器
    """

    def __init__(
        self,
        fd: int,
        history_file: Path,
        term_width_cache: "TerminalWidthCache | None" = None,
        cursor_tracker=None,
    ) -> None:
        self._fd = fd
        self._term_width_cache = (
            term_width_cache if term_width_cache is not None
            else TerminalWidthCache.get_default()
        )
        self._cursor_tracker = cursor_tracker

        # ── 缓冲状态（原 InputBuffer） ──
        self._buffer: str = ""
        self._cursor_pos: int = 0
        self._submitted_text: str = ""
        self._input_ready = threading.Event()
        self._lock = threading.Lock()
        self._echo_callback = None

        # ── 历史（原 InputBuffer） ──
        self._history: list[str] = []
        self._history_idx: int = -1
        self._saved_input_before_history: str = ""
        self._history_file = history_file
        self._history_max_entries = 1000

        # ── 非可打印字符捕获 ──
        self._captured_input: bytearray = bytearray()
        self._captured_lock = threading.Lock()

        # ── 回调引用 ──
        self._special_key_callback = None
        self._completion_callback = None
        self._dismiss_completion_callback = None
        self._completion_navigate_callback = None
        self._auto_completion_callback = None

        # ── InputReader 支持（可选，由外部注入） ──
        self._reader = None

        # ── I/O 状态控制 ──
        self._io_started: bool = False
        self._active = threading.Event()
        self._active.set()
        self._stop = threading.Event()
        self._interrupted = threading.Event()
        self._suppress_enter: bool = False

        # ── 粘贴退避优化 ──
        self._paste_skip_counter: int = 0
        self._paste_skip_threshold: int = 10

        # ── 故障检测 ──
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason: str | None = None
        self._fd_status: str = "ok"

    # ── 公开属性 ──────────────────────────────────────────

    @property
    def fd(self) -> int:
        """stdin 文件描述符。"""
        return self._fd

    @property
    def width(self) -> int:
        """终端宽度（列数），TTL 缓存。"""
        return self._term_width_cache.get_width()

    @property
    def height(self) -> int:
        """终端高度（行数），TTL 缓存。"""
        return self._term_width_cache.get_height()

    @property
    def is_io_running(self) -> bool:
        """I/O 是否处于激活状态（标志位管理，非线程存活检测）。"""
        return self._io_started

    @property
    def interrupted(self) -> bool:
        """中断标志是否被设置。"""
        return self._interrupted.is_set()

    # ═══════════════════════════════════════════════════════
    # I/O 状态管理
    # ═══════════════════════════════════════════════════════

    def start_io(self) -> None:
        """激活 I/O 读取（标志位管理模式，不再创建 daemon 线程）。

        stdin 读取由 render 线程通过 ``read_stdin_once()`` 驱动，
        此方法仅重置状态标志位。调用前应确保终端已设置为 cbreak 模式
        （由 EscapeMonitor 保证）。幂等：重复调用仅重置标志位。
        """
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        self._io_started = True
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason = None
        self._fd_status = "ok"

    def stop_io(self) -> None:
        """停用 I/O 读取（标志位管理模式，不再 join 线程）。

        设置 stop 和 active 标志位，render 线程中 ``read_stdin_once()``
        检测到后停止读取。幂等安全。
        """
        self._stop.set()
        self._active.set()  # 确保 read_stdin_once() 状态检查快速退出
        self._io_started = False
        self._fd_status = "ok"

    def pause_io(self) -> None:
        """暂停 I/O 读取（供 EscapeMonitor 的特殊按键回调使用）。

        暂停后 ``read_stdin_once()`` 在 render 线程中检测到 ``_active``
        未设置时跳过读取。
        """
        self._active.clear()

    def resume_io(self) -> None:
        """恢复 I/O 读取（供 EscapeMonitor 的特殊按键回调使用）。"""
        self._active.set()

    # ═══════════════════════════════════════════════════════
    # 中断与特殊按键处理（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def _do_interrupt(self) -> None:
        """内联中断处理：设置中断标志 + 清空回显 + 请求异步中断。

        在 render 线程中调用（快速路径，由 ``read_stdin_once()`` 直接分发）。
        """
        if self._stop.is_set():
            return
        if not self.has_queued_input():
            self.reset_and_echo()
        else:
            self._flush_stdin_residual()
        self._interrupted.set()
        request_interrupt_async()

    def _handle_special_key(self, action: str) -> None:
        """处理特殊按键（Ctrl+G/O/N/R）：直接调用回调并应用结果。

        在 render 线程中调用（由 ``read_stdin_once()`` 直接分发）。
        终端模式切换由回调函数内部直接操作 EscapeMonitor 完成。
        """
        cb = self._special_key_callback
        if cb is None:
            return
        text = self.get_current_text()
        try:
            result = cb(action, text)
        except Exception:
            _logger.warning("特殊按键回调异常 (action=%s)", action, exc_info=True)
            return
        if result is not None and result != text:
            if action == 'editmsg':
                self.reset()
                self.set_buffer(result)
            else:
                self.reset()
                self.handle_chars(result)
        if action == 'editmsg':
            # editmsg 是用户主动发起的编辑/提交操作（Ctrl+O），
            # 清除 _suppress_enter 确保 _enter() 不被抑制
            self.set_suppress_enter(False)
            self._enter()

    def _flush_stdin_residual(self, max_flush: int = 50) -> None:
        """非阻塞清理 stdin 残留字节。"""
        if self._fd_status == "error":
            return
        flushed = 0
        while flushed < max_flush:
            if self._stop.is_set():
                return
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.05)
                if not ready:
                    break
                os.read(self._fd, 1)
                flushed += 1
            except (ValueError, OSError, TypeError, AttributeError):
                _logger.debug("排空 stdin 残留时异常", exc_info=True)
                break

    def flush_stdin_buffer(self, max_flush: int = 50) -> None:
        """公开方法：非阻塞清理 stdin 残留字节 + termios 缓冲区刷洗。

        先使用 select 排空可读字节（委托 _flush_stdin_residual），
        再通过 tcflush 刷洗内核输入队列（仅在 HAS_TERMIOS=True 时执行）。

        Args:
            max_flush: 最大排空字节数限制（传递给 _flush_stdin_residual）。
        """
        self._flush_stdin_residual(max_flush)
        if HAS_TERMIOS:
            try:
                termios.tcflush(self._fd, termios.TCIFLUSH)
            except Exception:
                _logger.debug("tcflush 失败", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # stdin 直接读取（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def read_stdin_once(self) -> bool:
        """单次非阻塞 stdin 读取 + 直接分发（不经过事件队列）。

        Render 线程每帧调用一次。使用 select timeout=0 确保不阻塞渲染帧。
        单次迭代逻辑改为直接分发（不经过 queue.Queue 中间队列）。

        设计模式: 模板方法 — ``read_stdin_once()`` 为骨架，
        保留 ``_do_interrupt()`` / ``_handle_special_key()`` 具体步骤。

        Returns:
            True — 有数据被处理（读取并分发了至少一个输入单元）。
            False — 无数据可读、I/O 未激活、或已停止。
        """
        import select as _select_mod
        fd = self._fd

        # ── 状态检查 ──
        if self._fd_status == "error":
            return False
        if not self._active.is_set() or self._stop.is_set():
            return False

        # ── select 非阻塞读取（timeout=0，不阻塞渲染帧） ──
        try:
            ready, _, _ = _select_mod.select([fd], [], [], 0)
        except (ValueError, OSError, TypeError, AttributeError):
            self._select_error_count += 1
            if self._select_error_count >= _SELECT_ERROR_THRESHOLD:
                _logger.warning(
                    "select 错误连续 %d 次，判定 stdin 不可用",
                    self._select_error_count,
                )
                self._exit_reason = "select_error"
                self._fd_status = "error"
            return False

        if not ready:
            return False

        self._select_error_count = 0

        try:
            raw = os.read(fd, 1)
            if not raw:
                self._eof_count += 1
                if self._eof_count >= _EOF_THRESHOLD:
                    _logger.warning(
                        "stdin EOF 连续 %d 次，判定 pty 已断开",
                        self._eof_count,
                    )
                    self._exit_reason = "eof"
                return False
            self._eof_count = 0
        except (ValueError, OSError, TypeError):
            self._fd_status = "error"
            return False

        first_byte = raw[0]

        # ── ASCII 控制字符分发 ──
        if first_byte < 0x20 or first_byte == 0x7F:
            try:
                event = self.feed_byte(first_byte)
                if event is None:
                    # ESC (0x1b) → 读取完整转义序列
                    event = self._parse_escape_sequence(fd)
                    kind = event.kind
                    if kind in ("escape", "interrupt"):
                        self._do_interrupt()
                    elif kind in (
                        "arrow_up", "arrow_down", "arrow_right", "arrow_left",
                        "home", "end", "delete", "backspace", "char",
                    ):
                        self._dispatch_key_event(event)
                    # unknown / csi_u → 静默忽略
                elif event.kind == "interrupt":
                    self._do_interrupt()
                elif event.kind == "ctrl_key":
                    ch = event.char
                    if ch == '\x07':          # Ctrl+G → vim
                        self._handle_special_key('vim')
                    elif ch == '\x0f':        # Ctrl+O → /editmsg
                        self._handle_special_key('editmsg')
                    elif ch in ('\x0e', '\x12'):  # Ctrl+N/R → 切换模型
                        self._handle_special_key('switch_model')
                    else:
                        self._dispatch_key_event(event)
                else:
                    # enter, tab, backspace, home, end, delete 等 → 直接分发
                    self._dispatch_key_event(event)
            except Exception:
                _logger.warning("控制字符分发异常", exc_info=True)
            return True

        # ── ASCII 可打印字符 ──
        if first_byte < 0x80:
            try:
                paste_text = self.try_read_paste(fd, chr(first_byte))
                if len(paste_text) > 1:
                    self.handle_chars(paste_text)
                    self._trigger_auto_completion()
                else:
                    event = self.feed_byte(first_byte)
                    if event is not None:
                        self._dispatch_key_event(event)
            except Exception:
                _logger.warning("ASCII 可打印字符分发异常", exc_info=True)
            return True

        # ── 多字节 UTF-8 序列 ──
        try:
            ch = self.read_utf8_char(fd, first_byte)
            if ch is not None:
                paste_text = self.try_read_paste(fd, ch)
                if len(paste_text) > 1:
                    self.handle_chars(paste_text)
                    self._trigger_auto_completion()
                else:
                    self._dispatch_key_event(
                        KeyEvent(kind='char', char=ch,
                                 raw=ch.encode("utf-8", errors="replace"))
                    )
            else:
                self.capture_bytes(bytes([first_byte]))
        except Exception:
            _logger.warning("多字节 UTF-8 字符分发异常", exc_info=True)
        return True

    # ── InputReader 支持 ─────────────────────────────

    def set_reader(self, reader) -> None:
        """注入 InputReader 实例，使 process_events 从队列消费。

        Args:
            reader: InputReader 实例，或 None 降级为直接 stdin 读取。
        """
        self._reader = reader

    # ═══════════════════════════════════════════════════════
    # 事件处理（render 线程调用）
    # ═══════════════════════════════════════════════════════

    def process_events(self) -> None:
        """处理所有输入事件（render 线程调用）。

        循环调用 ``read_stdin_once()`` 直到无可读数据，
        确保一次渲染帧内处理完所有待处理的输入。
        """
        try:
            while self.read_stdin_once():
                pass
        except Exception:
            _logger.warning("process_events 异常", exc_info=True)

    def _dispatch_key_event(self, event: KeyEvent) -> None:
        """根据 KeyEvent.kind 分发到对应的输入处理器。

        Ctrl+G/O/N/R 等 ctrl_key 事件已在 read_stdin_once() 中拦截处理，
        此处分发不会收到 ctrl_key 分支。
        """
        kind = event.kind

        if kind == "enter":
            self._dismiss_completion()
            if not self._suppress_enter:
                self._enter()
        elif kind == "tab":
            self._handle_tab()
        elif kind == "backspace":
            self._dismiss_completion()
            if event.modifier == 1:
                self._delete_word_left()
            else:
                self._backspace()
            self._trigger_auto_completion()
        elif kind == "interrupt":
            _logger.debug("_dispatch_key_event: interrupt 事件到达队列（应内联处理）")
        elif kind == "home":
            self._dismiss_completion()
            self._home()
        elif kind == "end":
            self._dismiss_completion()
            self._end()
        elif kind == "delete":
            modifier = event.modifier
            if modifier == 0:
                self._dismiss_completion()
                self._delete()
                self._trigger_auto_completion()
            elif modifier == 1:
                self._dismiss_completion()
                self._delete_word_left()
                self._trigger_auto_completion()
            elif modifier == 2:
                self._dismiss_completion()
                self._kill_to_bol()
                self._trigger_auto_completion()
            elif modifier == 3:
                self._dismiss_completion()
                self._kill_to_eol()
                self._trigger_auto_completion()
        elif kind == "arrow_up":
            self._handle_arrow_up()
        elif kind == "arrow_down":
            self._handle_arrow_down()
        elif kind == "arrow_right":
            if event.modifier == 5:
                self._word_right()
            else:
                self._right()
        elif kind == "arrow_left":
            if event.modifier == 5:
                self._word_left()
            else:
                self._left()
        elif kind == "unknown":
            self._dismiss_completion()
            if event.raw:
                with self._captured_lock:
                    self._captured_input.append(event.raw[0])
        elif kind == "char":
            if event.char:
                self.handle_char(event.char)
                self._trigger_auto_completion()

    # ═══════════════════════════════════════════════════════
    # 辅助分发方法
    # ═══════════════════════════════════════════════════════

    def _handle_tab(self) -> None:
        """处理 Tab 键：调用补全回调，失败则插入制表符。"""
        cb = self._completion_callback
        if cb is None:
            self.handle_char('\t')
            return
        text = self.get_current_text()
        try:
            result = cb(text)
        except Exception:
            _logger.debug("补全回调异常", exc_info=True)
            result = None
        if result is None:
            self.handle_char('\t')
        else:
            self.set_buffer(result)
            self._echo(result)
            self._trigger_auto_completion()

    def _handle_arrow_up(self) -> None:
        """处理上箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self.get_current_text()
                result = cb(-1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    self.set_buffer(result)
                    self._echo(result)
                    self._trigger_auto_completion()
                return
        self._up()

    def _handle_arrow_down(self) -> None:
        """处理下箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self.get_current_text()
                result = cb(1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    self.set_buffer(result)
                    self._echo(result)
                    self._trigger_auto_completion()
                return
        self._down()

    def _dismiss_completion(self) -> None:
        """如果补全弹窗可见，关闭它。"""
        cb = self._dismiss_completion_callback
        if cb is not None:
            try:
                cb()
            except Exception:
                _logger.debug("关闭补全回调异常", exc_info=True)

    def _trigger_auto_completion(self) -> None:
        """获取当前文本并调用自动补全回调。"""
        cb = self._auto_completion_callback
        if cb is None:
            return
        text = self.get_current_text()
        try:
            cb(text)
        except Exception:
            _logger.debug("自动补全回调异常", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # 解析方法（原 InputParser → 内联为私有方法）
    # ═══════════════════════════════════════════════════════

    def feed_byte(self, byte: int) -> KeyEvent | None:
        """单字节推入解析状态机。

        Args:
            byte: 单字节整数值 (0-255)。

        Returns:
            KeyEvent — 完整按键事件；None — 需要解析完整转义序列。
        """
        # ── ESC 序列入口 ──
        if byte == 0x1b:
            return None

        # ── ASCII 控制字符分发 ──
        if byte <= 0x1f or byte == 0x7f:
            return self._decode_control_char(byte)

        # ── ASCII 可打印 / 高位字节 ──
        try:
            ch = bytes([byte]).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            ch = chr(byte)
        return KeyEvent(kind="char", char=ch, raw=bytes([byte]))

    def parse_sequence(self, fd_override: int | None = None) -> KeyEvent:
        """解析 ESC 转义序列（含 I/O）。

        在首字节已确认为 0x1b 后调用。

        Args:
            fd_override: 可选 fd 覆盖，默认使用 self._fd。

        Returns:
            解析后的 KeyEvent。
        """
        return self._parse_escape_sequence(
            fd_override if fd_override is not None else self._fd,
        )

    def _parse_escape_sequence(self, fd: int) -> KeyEvent:
        """读取并解析 ESC 转义序列（含 I/O）。"""
        # 读取 ESC 后的下一个字节
        try:
            has_more, _, _ = select.select([fd], [], [], 0.05)
        except (ValueError, OSError, TypeError, AttributeError):
            return KeyEvent(kind="escape", raw=b"\x1b")

        if not has_more:
            return KeyEvent(kind="escape", raw=b"\x1b")

        try:
            raw2 = os.read(fd, 1)
            if not raw2:
                return KeyEvent(kind="escape", raw=b"\x1b")
            next_byte = raw2[0]
        except (ValueError, OSError, TypeError):
            return KeyEvent(kind="escape", raw=b"\x1b")

        # ── CSI 序列：ESC [ ──
        if next_byte == ord('['):
            return self._read_csi_sequence(fd)

        # ── SS3 序列：ESC O ──
        if next_byte == ord('O'):
            return self._read_ss3_sequence(fd)

        # ── Alt+Backspace：ESC DEL ──
        if next_byte == 0x7f:
            try:
                if select.select([fd], [], [], 0.01)[0]:
                    os.read(fd, 1)
            except (ValueError, OSError, TypeError):
                pass
            return KeyEvent(kind="backspace", modifier=1, raw=b"\x1b\x7f")

        # ── 双 Esc ──
        if next_byte == 0x1b:
            return KeyEvent(kind="interrupt", raw=b"\x1b\x1b")

        # ── 其他 ESC 组合 → 视为中断 ──
        return KeyEvent(kind="interrupt", raw=b"\x1b" + bytes([next_byte]))

    @staticmethod
    def _decode_control_char(byte: int) -> KeyEvent:
        """将 ASCII 控制字符 (0x00-0x1F / 0x7F) 解码为 KeyEvent。"""
        raw = bytes([byte])
        if byte in (0x0d, 0x0a):        # \r / \n
            return KeyEvent(kind="enter", raw=raw)
        if byte == 0x09:                 # \t
            return KeyEvent(kind="tab", raw=raw)
        if byte in (0x7f, 0x08):        # DEL / BS
            return KeyEvent(kind="backspace", raw=raw)
        if byte == 0x03:                 # Ctrl+C
            return KeyEvent(kind="interrupt", raw=raw)
        if byte == 0x01:                 # Ctrl+A → Home
            return KeyEvent(kind="home", raw=raw)
        if byte == 0x05:                 # Ctrl+E → End
            return KeyEvent(kind="end", raw=raw)
        if byte == 0x17:                 # Ctrl+W → delete word left
            return KeyEvent(kind="delete", modifier=1, raw=raw)
        if byte == 0x15:                 # Ctrl+U → kill to BOL
            return KeyEvent(kind="delete", modifier=2, raw=raw)
        if byte == 0x0b:                 # Ctrl+K → kill to EOL
            return KeyEvent(kind="delete", modifier=3, raw=raw)
        if byte in (0x07, 0x0f, 0x0e, 0x12):  # Ctrl+G/O/N/R → 特殊按键
            return KeyEvent(kind="ctrl_key", char=chr(byte), raw=raw)
        # 其他控制字符 → unknown
        return KeyEvent(kind="unknown", raw=raw)

    def _read_csi_sequence(self, fd: int) -> KeyEvent:
        """读取 CSI 序列参数 + 终结符并解析为 KeyEvent。"""
        params: list[int] = []
        current = ""
        terminator: str | None = None

        try:
            while select.select([fd], [], [], _CSI_READ_TIMEOUT)[0]:
                raw_c = os.read(fd, 1)
                if not raw_c:
                    break
                c = raw_c.decode("utf-8", errors="replace")
                if c == ';':
                    try:
                        params.append(int(current) if current else 0)
                    except ValueError:
                        params.append(0)
                    current = ""
                elif c.isdigit():
                    current += c
                elif c.isalpha() or c == '~':
                    if current:
                        try:
                            params.append(int(current))
                        except ValueError:
                            params.append(0)
                    terminator = c
                    break
        except (ValueError, OSError, TypeError):
            pass

        if terminator is None:
            return KeyEvent(kind="unknown", raw=b"\x1b[")

        return self._dispatch_csi(params, terminator)

    def _read_ss3_sequence(self, fd: int) -> KeyEvent:
        """读取 SS3 序列（ESC O + 字符，通常为 F1-F4）。"""
        try:
            if select.select([fd], [], [], _SS3_READ_TIMEOUT)[0]:
                raw_c = os.read(fd, 1)
                if raw_c:
                    return KeyEvent(kind="unknown", raw=b"\x1bO" + raw_c)
        except (ValueError, OSError, TypeError):
            pass
        return KeyEvent(kind="unknown", raw=b"\x1bO")

    @staticmethod
    def _dispatch_csi(params: list[int], terminator: str) -> KeyEvent:
        """根据 CSI 参数和终结符分发到对应的 KeyEvent。"""
        # ── CSI u 模式: \x1b[<keycode>;<modifier>u ──
        if terminator == 'u':
            keycode = params[0] if len(params) >= 1 else 0
            modifier = params[1] if len(params) >= 2 else 1
            raw = b"\x1b[" + Input._params_to_bytes(params) + b"u"
            if keycode == 13 and modifier in (2, 3, 5):
                return KeyEvent(kind="char", char="\n", modifier=modifier,
                                keycode=keycode, raw=raw)
            return KeyEvent(kind="csi_u", modifier=modifier, keycode=keycode, raw=raw)

        raw = b"\x1b[" + Input._params_to_bytes(params) + terminator.encode()

        # ── 功能键序列: \x1b[N~ ──
        if terminator == '~':
            p = params[0] if params else 0
            if p in (1, 7):
                return KeyEvent(kind="home", raw=raw)
            if p == 3:
                return KeyEvent(kind="delete", raw=raw)
            if p in (4, 8):
                return KeyEvent(kind="end", raw=raw)
            return KeyEvent(kind="unknown", raw=raw)

        # ── Home (\x1b[H) ──
        if terminator == 'H':
            return KeyEvent(kind="home", raw=raw)

        # ── End (\x1b[F) ──
        if terminator == 'F':
            return KeyEvent(kind="end", raw=raw)

        # ── 右箭头 / Ctrl+右 ──
        if terminator == 'C':
            if len(params) >= 2 and params[1] == 5:
                return KeyEvent(kind="arrow_right", modifier=5, raw=raw)
            return KeyEvent(kind="arrow_right", raw=raw)

        # ── 左箭头 / Ctrl+左 ──
        if terminator == 'D':
            if len(params) >= 2 and params[1] == 5:
                return KeyEvent(kind="arrow_left", modifier=5, raw=raw)
            return KeyEvent(kind="arrow_left", raw=raw)

        # ── 上箭头 ──
        if terminator == 'A':
            return KeyEvent(kind="arrow_up", raw=raw)

        # ── 下箭头 ──
        if terminator == 'B':
            return KeyEvent(kind="arrow_down", raw=raw)

        # ── 其他 CSI 序列 ──
        return KeyEvent(kind="unknown", raw=raw)

    @staticmethod
    def _params_to_bytes(params: list[int]) -> bytes:
        """将参数列表转为 CSI 参数字节串。"""
        if not params:
            return b""
        return ";".join(str(p) for p in params).encode()

    # ═══════════════════════════════════════════════════════
    # I/O 辅助方法
    # ═══════════════════════════════════════════════════════

    def read_byte(self) -> bytes:
        """从 fd 读取单个原始字节。

        Returns:
            读取到的单字节 bytes 对象；EOF/错误时返回空 bytes。
        """
        try:
            return os.read(self._fd, 1)
        except (ValueError, OSError, TypeError):
            return b""

    def read_with_timeout(self, timeout: float) -> bytes | None:
        """使用 select + os.read 读取单个字节，超时返回 None。"""
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (ValueError, OSError, TypeError, AttributeError):
            return None
        if not ready:
            return None
        try:
            raw = os.read(self._fd, 1)
            return raw if raw else None
        except (ValueError, OSError, TypeError):
            return None

    def try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（退避 select 检测突发字符流）。"""
        # 快速路径：若近期均非粘贴，跳过退避检测
        if self._paste_skip_counter >= self._paste_skip_threshold:
            try:
                has_more, _, _ = select.select([fd], [], [], 0.0)
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
                return first_chars
            # 有数据，重置计数器并进入粘贴检测
            self._paste_skip_counter = 0
        else:
            for delay in (0.0001, 0.002, 0.003):
                try:
                    has_more, _, _ = select.select([fd], [], [], delay)
                except (ValueError, OSError, TypeError, AttributeError):
                    return first_chars
                if not has_more:
                    self._paste_skip_counter += 1
                    return first_chars
        extra = b""
        try:
            while True:
                has_more, _, _ = select.select([fd], [], [], 0.01)
                if not has_more:
                    break
                more = os.read(fd, 65536)
                if not more:
                    break
                extra += more
                if len(extra) >= 262144:
                    break
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        if not extra:
            return first_chars
        return first_chars + extra.decode("utf-8", errors="replace")

    def read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列。"""
        if (first_byte & 0xE0) == 0xC0:
            total_bytes = 2
        elif (first_byte & 0xF0) == 0xE0:
            total_bytes = 3
        elif (first_byte & 0xF8) == 0xF0:
            total_bytes = 4
        else:
            return None

        buf = bytes([first_byte])
        for _ in range(total_bytes - 1):
            try:
                has_data, _, _ = select.select(
                    [fd], [], [], _UTF8_READ_TIMEOUT,
                )
            except (ValueError, OSError, TypeError, AttributeError):
                break
            if not has_data:
                break
            try:
                more = os.read(fd, 1)
                if not more:
                    break
                buf += more
            except (ValueError, OSError, TypeError):
                break

        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            return None

    # ═══════════════════════════════════════════════════════
    # 缓冲操作（原 InputBuffer → 内联为实例方法）
    # ═══════════════════════════════════════════════════════

    def handle_char(self, ch: str) -> None:
        """处理流式输入字符：插入到缓冲区光标位置并回显。"""
        if not (ch.isprintable() or ch in (' ', '\t', '\n')):
            return
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            self._buffer = (
                self._buffer[:self._cursor_pos]
                + ch
                + self._buffer[self._cursor_pos:]
            )
            self._cursor_pos += len(ch)
            text = self._buffer
        self._echo(text)

    def handle_chars(self, text: str) -> None:
        """批量处理多个字符（粘贴/预填场景），只在全部插入后触发一次回显。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            self._buffer = (
                self._buffer[:self._cursor_pos]
                + text
                + self._buffer[self._cursor_pos:]
            )
            self._cursor_pos += len(text)
            result = self._buffer
        self._echo(result)

    def get_queued_input(self) -> str | None:
        """获取排队输入（Enter 提交的文本），返回 None 表示无排队输入。"""
        if not self._input_ready.is_set():
            return None
        with self._lock:
            text = self._submitted_text
            self._submitted_text = ""
            self._input_ready.clear()
        return text

    def has_queued_input(self) -> bool:
        """是否有排队输入等待处理。"""
        return self._input_ready.is_set()

    def get_current_text(self) -> str:
        """获取当前正在输入的文本（不消费）。"""
        with self._lock:
            return self._buffer

    def reset(self) -> None:
        """清空所有流式输入状态（缓冲区、提交文本、历史导航、中断标志）。"""
        with self._lock:
            self._buffer = ""
            self._cursor_pos = 0
            self._submitted_text = ""
            self._input_ready.clear()
            self._history_idx = -1
            self._saved_input_before_history = ""
        self._interrupted.clear()

    def drain_all(self) -> tuple[str | None, str]:
        """排出所有流式输入状态：返回 (submitted_text, buffer_text)。"""
        with self._lock:
            submitted = self._submitted_text if self._input_ready.is_set() else None
            buffer_text = self._buffer
            self._submitted_text = ""
            self._buffer = ""
            self._cursor_pos = 0
            self._history_idx = -1
            self._saved_input_before_history = ""
        return submitted, buffer_text

    def set_buffer(self, text: str) -> None:
        """设置缓冲区文本（用于预填），光标移到末尾。"""
        with self._lock:
            self._buffer = text
            self._cursor_pos = len(text)
            self._history_idx = -1
            self._submitted_text = ""
            self._input_ready.clear()

    def get_history_indicator(self) -> str:
        """历史浏览状态指示器，非导航模式返回空字符串。"""
        return self._history_indicator

    # ═══════════════════════════════════════════════════════
    # 历史管理
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _unescape(line: str) -> str:
        """将文件中转义的 \\n 还原为真实换行符。"""
        return line.replace("\\n", "\n")

    def load_history(self) -> None:
        """从 INPUT_HISTORY_FILE 加载历史行（多进程安全）。"""
        raw, locked = _read_history_file()
        if not raw:
            return

        lines = raw.splitlines()
        if not lines:
            return

        # 第一趟 O(n)：记录每个条目在文件中的最后出现索引
        latest: dict[str, int] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            entry = self._unescape(stripped)
            if not entry:
                continue
            latest[entry] = i

        # 第二趟 O(n)：只保留最后出现的条目，保持原始顺序
        seen: set[str] = set()
        unique: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            entry = self._unescape(stripped)
            if not entry:
                continue
            if i == latest.get(entry) and entry not in seen:
                unique.append(entry)
                seen.add(entry)

        # 合并到现有内存历史
        file_entries = unique[:_HISTORY_MAX_ENTRIES]
        if self._history:
            if file_entries:
                existing = set(self._history)
                for entry in reversed(file_entries):
                    if entry not in existing:
                        self._history.append(entry)
                        existing.add(entry)
                self._history = self._history[:_HISTORY_MAX_ENTRIES]
        else:
            self._history = list(reversed(file_entries))

        if locked:
            _compact_history_file()

    # ═══════════════════════════════════════════════════════
    # 缓冲编辑操作（原 InputBuffer 内部方法 → 私有方法）
    # ═══════════════════════════════════════════════════════

    def _backspace(self) -> None:
        """退格：删除光标前一个字符。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos > 0:
                self._buffer = (
                    self._buffer[:self._cursor_pos - 1]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos -= 1
            text = self._buffer
        self._echo(text)

    def _left(self) -> None:
        """左箭头：光标左移一格。"""
        with self._lock:
            if self._cursor_pos > 0:
                self._cursor_pos -= 1
            text = self._buffer
        self._echo(text)

    def _right(self) -> None:
        """右箭头：光标右移一格。"""
        with self._lock:
            if self._cursor_pos < len(self._buffer):
                self._cursor_pos += 1
            text = self._buffer
        self._echo(text)

    def _enter(self) -> None:
        """Enter：保存提交文本、标记就绪、清空缓冲区。"""
        with self._lock:
            if self._input_ready.is_set():
                return
            text = self._buffer
            self._submitted_text = text
            self._buffer = ""
            self._cursor_pos = 0
            self._input_ready.set()
            if self._history_idx >= 0:
                self._history_idx = -1
            self._append_history_locked(text)
        self._echo("")

    def _append_history_locked(self, text: str) -> None:
        """保存输入到历史（需持 _lock）。"""
        if not text.strip():
            return
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        if len(self._history) > self._history_max_entries:
            self._history = self._history[:self._history_max_entries]
        escaped = text.replace("\n", "\\n")
        if not _append_to_history_file(escaped):
            _logger.warning("历史文件追加写入失败: %s", self._history_file)

    def _up(self) -> None:
        """上箭头：多行上移一行；首行或单行回退到历史浏览。"""
        # ── 阶段1：多行光标上移 ──
        text = None
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                cur_line = before_cursor.count('\n')
                if cur_line > 0:
                    lines = self._buffer.split('\n')
                    pos = sum(len(lines[i]) + 1 for i in range(cur_line))
                    col = self._cursor_pos - pos
                    prev_start = sum(len(lines[i]) + 1 for i in range(cur_line - 1))
                    prev_len = len(lines[cur_line - 1])
                    self._cursor_pos = prev_start + min(col, prev_len)
                    text = self._buffer
        if text is not None:
            self._echo(text)
            return

        # ── 阶段2：单行或首行 → 历史浏览 ──
        with self._lock:
            if not self._history:
                return
            if self._history_idx < 0:
                self._saved_input_before_history = self._buffer
                self._history_idx = 0
            elif self._history_idx < len(self._history) - 1:
                self._history_idx += 1
            self._buffer = self._history[self._history_idx]
            self._cursor_pos = len(self._buffer)
            text = self._buffer
        self._echo(text)

    def _home(self) -> None:
        """Home：光标移到当前逻辑行首。"""
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                last_nl = before_cursor.rfind('\n')
                self._cursor_pos = last_nl + 1
            else:
                self._cursor_pos = 0
            text = self._buffer
        self._echo(text)

    def _end(self) -> None:
        """End：光标移到当前逻辑行尾。"""
        with self._lock:
            if '\n' in self._buffer:
                after_cursor = self._buffer[self._cursor_pos:]
                next_nl = after_cursor.find('\n')
                if next_nl >= 0:
                    self._cursor_pos = self._cursor_pos + next_nl
                else:
                    self._cursor_pos = len(self._buffer)
            else:
                self._cursor_pos = len(self._buffer)
            text = self._buffer
        self._echo(text)

    def _word_left(self) -> None:
        """Ctrl+左：向左跳一个词。"""
        with self._lock:
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                pos = self._cursor_pos - 1
                while pos >= 0 and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                while pos >= 0 and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                self._cursor_pos = pos + 1
                text = self._buffer
        self._echo(text)

    def _word_right(self) -> None:
        """Ctrl+右：向右跳一个词。"""
        with self._lock:
            n = len(self._buffer)
            if self._cursor_pos >= n:
                text = self._buffer
            else:
                pos = self._cursor_pos
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                while pos < n and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                while pos < n and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos += 1
                self._cursor_pos = pos
                text = self._buffer
        self._echo(text)

    def _down(self) -> None:
        """下箭头：多行下移一行；尾行或单行回退到历史浏览。"""
        # ── 阶段1：多行光标下移 ──
        text = None
        with self._lock:
            if '\n' in self._buffer:
                before_cursor = self._buffer[:self._cursor_pos]
                cur_line = before_cursor.count('\n')
                lines = self._buffer.split('\n')
                if cur_line < len(lines) - 1:
                    pos = sum(len(lines[i]) + 1 for i in range(cur_line))
                    col = self._cursor_pos - pos
                    next_start = sum(len(lines[i]) + 1 for i in range(cur_line + 1))
                    next_len = len(lines[cur_line + 1])
                    self._cursor_pos = next_start + min(col, next_len)
                    text = self._buffer
        if text is not None:
            self._echo(text)
            return

        # ── 阶段2：尾行或单行 → 历史浏览 ──
        with self._lock:
            if not self._history:
                return
            if self._history_idx < 0:
                return
            elif self._history_idx > 0:
                self._history_idx -= 1
                self._buffer = self._history[self._history_idx]
            else:
                self._history_idx = -1
                self._buffer = self._saved_input_before_history
            self._cursor_pos = len(self._buffer)
            text = self._buffer
        self._echo(text)

    def _delete(self) -> None:
        """Del：删除光标后的字符。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            n = len(self._buffer)
            if self._cursor_pos < n:
                self._buffer = (
                    self._buffer[:self._cursor_pos]
                    + self._buffer[self._cursor_pos + 1:]
                )
            text = self._buffer
        self._echo(text)

    def _delete_word_left(self) -> None:
        """Ctrl+W / Alt+Backspace：删除光标前的一个词。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                pos = self._cursor_pos - 1
                while pos >= 0 and not (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                while pos >= 0 and (
                    self._buffer[pos].isalnum() or self._buffer[pos] == '_'
                ):
                    pos -= 1
                word_start = pos + 1
                self._buffer = (
                    self._buffer[:word_start]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos = word_start
                text = self._buffer
        self._echo(text)

    def _kill_to_bol(self) -> None:
        """Ctrl+U：删除光标到当前逻辑行首。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            if self._cursor_pos <= 0:
                text = self._buffer
            else:
                before_cursor = self._buffer[:self._cursor_pos]
                last_nl = before_cursor.rfind('\n')
                line_start = last_nl + 1
                self._buffer = (
                    self._buffer[:line_start]
                    + self._buffer[self._cursor_pos:]
                )
                self._cursor_pos = line_start
                text = self._buffer
        self._echo(text)

    def _kill_to_eol(self) -> None:
        """Ctrl+K：删除光标到当前逻辑行尾。"""
        with self._lock:
            if self._history_idx >= 0:
                self._history_idx = -1
            n = len(self._buffer)
            if self._cursor_pos >= n:
                text = self._buffer
            else:
                after_cursor = self._buffer[self._cursor_pos:]
                next_nl = after_cursor.find('\n')
                if next_nl >= 0:
                    line_end = self._cursor_pos + next_nl
                else:
                    line_end = n
                self._buffer = (
                    self._buffer[:self._cursor_pos]
                    + self._buffer[line_end:]
                )
                text = self._buffer
        self._echo(text)

    @property
    def _history_indicator(self) -> str:
        """历史浏览状态指示器。"""
        if self._history_idx < 0:
            return ""
        total = len(self._history)
        current = self._history_idx + 1
        return f" [历史 {current}/{total}]"

    def _echo(self, text: str) -> None:
        """调用回显回调。"""
        with self._lock:
            pos = self._cursor_pos
            indicator = self._history_indicator
            if indicator:
                display_text = text + indicator
            else:
                display_text = text
        cb = self._echo_callback
        if cb is not None:
            try:
                cb(display_text, pos)
            except Exception:
                _logger.debug("_echo 回显回调失败", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # 光标定位（原 CursorPositioner → 内联，使用 wcswidth_simple）
    # ═══════════════════════════════════════════════════════

    def compute_cursor(
        self,
        text: str,
        cursor_pos: int,
        bottom_lines: int,
        subagent_lines: int,
        completion_height: int,
    ) -> tuple[int, int, int, int]:
        """计算光标在终端上的位置。

        Args:
            text: 输入文本（含 \\n）。
            cursor_pos: 光标在文本中的偏移位置（-1=末尾）。
            bottom_lines: 底部栏总行数（含分隔线、状态行、输入行、补全弹窗）。
            subagent_lines: subagent 面板行数。
            completion_height: 补全弹窗高度（行数）。

        Returns:
            (r_cursor, cursor_col, vis_row, vis_col) 四元组：
              - r_cursor: 终端行号（1-based）
              - cursor_col: 终端列号（1-based）
              - vis_row: 视觉行（0-based）
              - vis_col: 视觉列（0-based）
        """
        width = self._term_width_cache.get_width()
        height = self._term_width_cache.get_height()

        max_input = max(1, width - 4)
        vis_row, vis_col = _compute_cursor_visual_pos(
            text, cursor_pos, max_input,
        )

        r_cursor = (
            height - bottom_lines + 4 + subagent_lines + completion_height + vis_row
        )
        r_cursor = max(1, min(r_cursor, height))

        cursor_col = min(3 + vis_col, width)

        return (r_cursor, cursor_col, vis_row, vis_col)

    # ═══════════════════════════════════════════════════════
    # 回调接口
    # ═══════════════════════════════════════════════════════

    def set_echo_callback(self, cb) -> None:
        """设置流式输入回显回调。

        cb 签名: (display_text: str, cursor_pos: int) -> None
        """
        self._echo_callback = cb

    def set_special_key_callback(self, cb) -> None:
        """设置特殊按键回调（Ctrl+G/O/N/R）。

        cb 签名: (action: str, current_text: str) -> str | None
        """
        self._special_key_callback = cb

    def set_completion_callback(self, cb) -> None:
        """设置 Tab 补全回调。

        cb 签名: (text: str) -> str | None
        """
        self._completion_callback = cb

    def set_dismiss_completion_callback(self, cb) -> None:
        """设置补全弹窗关闭回调。

        cb 签名: () -> None
        """
        self._dismiss_completion_callback = cb

    def set_completion_navigate_callback(self, cb) -> None:
        """设置补全弹窗上下导航回调。

        cb 签名: (delta: int, text: str) -> str | None
        """
        self._completion_navigate_callback = cb

    def set_auto_completion_callback(self, cb) -> None:
        """设置自动补全回调。

        cb 签名: (text: str) -> None
        """
        self._auto_completion_callback = cb

    def set_suppress_enter(self, suppress: bool) -> None:
        """设置 Enter 抑制标志（用于 editmsg 消息选择期间）。

        当 suppress=True 时，_dispatch_key_event 中的 Enter 分支
        将跳过 _enter() 调用，防止选择确认 Enter 被误提交为输入。

        线程安全：使用 _lock 保护。
        """
        with self._lock:
            self._suppress_enter = suppress

    def get_suppress_enter(self) -> bool:
        """获取当前 Enter 抑制状态。线程安全。"""
        with self._lock:
            return self._suppress_enter

    # ═══════════════════════════════════════════════════════
    # 便捷方法
    # ═══════════════════════════════════════════════════════

    def echo(self, text: str = "") -> None:
        """调用回显回调，自动获取当前文本如果未提供。"""
        if not text:
            text = self.get_current_text()
        self._echo(text)

    def reset_and_echo(self) -> None:
        """重置缓冲区并回显空字符串（清空输入行视觉）。"""
        self.reset()
        self._echo("")

    def capture_bytes(self, data: bytes) -> None:
        """追加原始字节到捕获缓冲区。线程安全。"""
        with self._captured_lock:
            self._captured_input.extend(data)

    def drain_captured(self) -> str:
        """排出并返回捕获的非可打印字符。"""
        with self._captured_lock:
            data = bytes(self._captured_input).decode("utf-8", errors="replace")
            self._captured_input.clear()
        return data


# ── 模块导出 ──────────────────────────────────────────────

__all__ = ["Input", "KeyEvent"]
