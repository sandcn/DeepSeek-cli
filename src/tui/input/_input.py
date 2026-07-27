"""Input 门面类 — 统一 TUI 输入管理入口。

组合 InputBuffer、InputParser、CursorPositioner、TerminalWidthCache，
为 EscapeMonitor / _BottomBar / InteractiveLoop 提供统一的输入管理接口。

设计模式:
  - 外观（Facade）: Input 提供统一入口，内部委托给各组件
  - 组合（Composite）: Input 组合多个子组件，不做继承

线程模型:
  - EscapeMonitor（I/O 线程）：push 事件到 _event_queue
  - Render 线程：调用 process_events() 排空队列并分发，与渲染序列化
"""

from __future__ import annotations

import os
import sys
import select
import queue
import threading
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src._compat import dataclass

from ._buffer import InputBuffer
from ._parser import InputParser, KeyEvent
from ._cursor import CursorPositioner

if TYPE_CHECKING:
    from ..terminal.terminal import TerminalWidthCache

_logger = logging.getLogger(__name__)

# UTF-8 多字节序列读取超时（秒）
_UTF8_READ_TIMEOUT = 0.05


@dataclass(slots=True)
class InputEvent:
    """EscapeMonitor I/O 线程推送到 Input 处理队列的事件。

    字段:
        kind: 'key' | 'paste' | 'buffer_replace'
        key_event: KeyEvent 实例（kind='key' 时有效）
        text: 粘贴或缓冲区替换文本
    """
    kind: str  # 'key' | 'paste' | 'buffer_replace'
    key_event: KeyEvent | None = None
    text: str = ""


class Input:
    """统一输入管理门面类。

    组合 InputBuffer（缓冲+历史）、InputParser（ANSI 解析）、
    CursorPositioner（光标定位）、TerminalWidthCache（终端尺寸），
    为 EscapeMonitor 等消费者提供统一入口。

    接收来自 EscapeMonitor I/O 线程的事件推送，
    由 render 线程调用 process_events() 统一分发。

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
        from ..terminal.terminal import TerminalWidthCache as _TWC

        self._fd = fd
        self._term_width_cache = (
            term_width_cache if term_width_cache is not None
            else _TWC.get_default()
        )
        self._cursor_tracker = cursor_tracker

        # ── 组合子组件 ──
        self._buffer = InputBuffer(history_file)
        self._parser = InputParser()
        self._cursor = CursorPositioner(
            width_cache=self._term_width_cache,
            cursor_tracker=cursor_tracker,
        )

        # ── 事件队列（EscapeMonitor I/O 线程 → render 线程） ──
        self._event_queue: queue.Queue = queue.Queue()

        # ── 非可打印字符捕获 ──
        self._captured_input: bytearray = bytearray()
        self._captured_lock = threading.Lock()

        # ── 回调引用 ──
        self._special_key_callback = None
        self._completion_callback = None
        self._dismiss_completion_callback = None
        self._completion_navigate_callback = None
        self._auto_completion_callback = None

    # ── 事件推送（EscapeMonitor I/O 线程调用） ────────────

    def push_key_event(self, event: KeyEvent) -> None:
        """EscapeMonitor 推入按键事件到队列。"""
        self._event_queue.put(InputEvent(kind='key', key_event=event))

    def push_paste(self, text: str) -> None:
        """EscapeMonitor 推入粘贴文本到队列。"""
        self._event_queue.put(InputEvent(kind='paste', text=text))

    def push_buffer_replace(self, text: str) -> None:
        """EscapeMonitor 推入缓冲区替换（特殊键回调结果）到队列。"""
        self._event_queue.put(InputEvent(kind='buffer_replace', text=text))

    # ── 事件处理（render 线程调用） ────────────────────────

    def process_events(self) -> None:
        """排空事件队列并全部分发（render 线程调用）。

        非阻塞排空：处理当前队列中所有事件后返回。
        与 I/O 线程的 push 操作通过 queue.Queue 的线程安全保证同步。
        """
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(event)
            except Exception:
                _logger.warning("事件分发异常 kind=%s", event.kind, exc_info=True)

    def _dispatch(self, event: InputEvent) -> None:
        """分发单个输入事件。"""
        if event.kind == 'key':
            if event.key_event is not None:
                self._dispatch_key_event(event.key_event)
        elif event.kind == 'paste':
            self._buffer.handle_chars(event.text)
            self._trigger_auto_completion()
        elif event.kind == 'buffer_replace':
            self._buffer.set_buffer(event.text)
            self._buffer._echo(event.text)
            self._trigger_auto_completion()

    def _dispatch_key_event(self, event: KeyEvent) -> None:
        """根据 KeyEvent.kind 分发到对应的输入处理器。

        统一处理 ASCII 控制字符和 ESC 转义序列产生的 KeyEvent。
        interrupt 类型在 EscapeMonitor 中已内联处理，此处仅为防御。
        """
        kind = event.kind

        if kind == "enter":
            self._dismiss_completion()
            self._buffer._enter()
        elif kind == "tab":
            self._handle_tab()
        elif kind == "backspace":
            self._dismiss_completion()
            if event.modifier == 1:
                # Alt+Backspace → 删除前一个词
                self._buffer._delete_word_left()
            else:
                self._buffer._backspace()
            self._trigger_auto_completion()
        elif kind == "interrupt":
            # interrupt 应在 EscapeMonitor 中内联处理，此处仅防御日志
            _logger.debug("_dispatch_key_event: interrupt 事件到达队列（应内联处理）")
        elif kind == "home":
            self._dismiss_completion()
            self._buffer._home()
        elif kind == "end":
            self._dismiss_completion()
            self._buffer._end()
        elif kind == "delete":
            modifier = event.modifier
            if modifier == 0:
                self._dismiss_completion()
                self._buffer._delete()
                self._trigger_auto_completion()
            elif modifier == 1:
                self._dismiss_completion()
                self._buffer._delete_word_left()
                self._trigger_auto_completion()
            elif modifier == 2:
                self._dismiss_completion()
                self._buffer._kill_to_bol()
                self._trigger_auto_completion()
            elif modifier == 3:
                self._dismiss_completion()
                self._buffer._kill_to_eol()
                self._trigger_auto_completion()
        elif kind == "arrow_up":
            self._handle_arrow_up()
        elif kind == "arrow_down":
            self._handle_arrow_down()
        elif kind == "arrow_right":
            if event.modifier == 5:
                self._buffer._word_right()
            else:
                self._buffer._right()
        elif kind == "arrow_left":
            if event.modifier == 5:
                self._buffer._word_left()
            else:
                self._buffer._left()
        elif kind == "ctrl_key":
            ch = event.char
            if ch == '\x07':          # Ctrl+G → vim 编辑
                self._handle_special_key_action('vim')
            elif ch == '\x0f':        # Ctrl+O → /editmsg
                self._handle_special_key_action('editmsg')
            elif ch in ('\x0e', '\x12'):  # Ctrl+N / Ctrl+R → 切换模型
                self._handle_special_key_action('switch_model')
        elif kind == "unknown":
            self._dismiss_completion()
            if event.raw:
                with self._captured_lock:
                    self._captured_input.append(event.raw[0])
        elif kind == "char":
            # 可打印字符（含 CSI u Shift+Enter / Alt+Enter 的换行）
            if event.char:
                self._buffer.handle_char(event.char)
                self._trigger_auto_completion()

    # ── 辅助分发方法 ──────────────────────────────────────

    def _handle_tab(self) -> None:
        """处理 Tab 键：调用补全回调，失败则插入制表符。"""
        cb = self._completion_callback
        if cb is None:
            self._buffer.handle_char('\t')
            return
        text = self._buffer.get_current_text()
        try:
            result = cb(text)
        except Exception:
            _logger.debug("补全回调异常", exc_info=True)
            result = None
        if result is None:
            self._buffer.handle_char('\t')
        else:
            self._buffer.set_buffer(result)
            self._buffer._echo(result)
            self._trigger_auto_completion()

    def _handle_arrow_up(self) -> None:
        """处理上箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self._buffer.get_current_text()
                result = cb(-1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    self._buffer.set_buffer(result)
                    self._buffer._echo(result)
                    self._trigger_auto_completion()
                return
        self._buffer._up()

    def _handle_arrow_down(self) -> None:
        """处理下箭头：补全弹窗可见时仅移动高亮，否则历史浏览。"""
        cb = self._completion_navigate_callback
        if cb is not None:
            try:
                text = self._buffer.get_current_text()
                result = cb(1, text)
            except Exception:
                _logger.debug("补全导航回调异常", exc_info=True)
                result = None
            if result is not None:
                if result != text:
                    self._buffer.set_buffer(result)
                    self._buffer._echo(result)
                    self._trigger_auto_completion()
                return
        self._buffer._down()

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
        text = self._buffer.get_current_text()
        try:
            cb(text)
        except Exception:
            _logger.debug("自动补全回调异常", exc_info=True)

    def _handle_special_key_action(self, action: str) -> None:
        """Ctrl+G/O/N/R 等特殊按键：仅调用回调，不涉及终端模式切换。

        终端模式切换由 EscapeMonitor._handle_special_key() 在 I/O 线程中完成。
        此方法在 render 线程中执行，仅负责调用回调并更新缓冲区。
        """
        cb = self._special_key_callback
        if cb is None:
            return
        text = self._buffer.get_current_text()
        try:
            result = cb(action, text)
        except Exception:
            _logger.warning("特殊按键回调异常 (action=%s)", action, exc_info=True)
            return
        if result is not None and result != text:
            self._buffer.reset()
            self._buffer.handle_chars(result)

        if action == 'editmsg':
            self._buffer._enter()

    # ── I/O 方法 ──────────────────────────────────────────

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
        """使用 select + os.read 读取单个字节，超时返回 None。

        Args:
            timeout: select 超时时间（秒）。

        Returns:
            读取到的单字节 bytes 对象，或超时/错误返回 None。
        """
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

    # ── 解析方法（委托 InputParser） ──────────────────────

    def parse_sequence(self, fd_override: int | None = None) -> KeyEvent:
        """解析 ESC 转义序列（含 I/O）。

        在首字节已确认为 0x1b 后调用。委托 InputParser.parse_escape_sequence()。

        Args:
            fd_override: 可选 fd 覆盖，默认使用 self._fd。

        Returns:
            解析后的 KeyEvent。
        """
        return self._parser.parse_escape_sequence(
            fd_override if fd_override is not None else self._fd,
        )

    def feed_byte(self, byte: int) -> KeyEvent | None:
        """单字节推入解析状态机。

        对于非 ESC 字节立即返回 KeyEvent；ESC 返回 None 表示需走
        parse_sequence() 读取完整序列。

        Args:
            byte: 单字节整数值 (0-255)。

        Returns:
            KeyEvent 或 None。
        """
        return self._parser.feed_byte(byte)

    # ── 粘贴检测 ──────────────────────────────────────────

    def try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（退避 select 检测突发字符流）。

        从 EscapeMonitor._try_read_paste 提取。

        在已读取单个字符后调用，用递增超时 select（1ms→2ms→3ms）
        检测 stdin 上是否有连续快速到达的字符序列。有则一次性批量
        读取所有可用数据并解码。

        Args:
            fd: stdin 文件描述符。
            first_chars: 已读取的首个字符序列。

        Returns:
            first_chars（非粘贴/单字符）或完整的粘贴文本（多字符）。
        """
        for delay in (0.001, 0.002, 0.003):
            try:
                has_more, _, _ = select.select([fd], [], [], delay)
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
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

    # ── UTF-8 多字节序列读取 ──────────────────────────────

    def read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列。

        从 EscapeMonitor._read_utf8_char 提取。
        无效/不完整序列返回 None（调用方负责捕获原始字节）。

        Args:
            fd: stdin 文件描述符。
            first_byte: 已读取的首字节（高位为 1，即 >= 0x80）。

        Returns:
            解码后的 Unicode 字符，或 None（无效/不完整序列）。
        """
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

    # ── 属性委托 ──────────────────────────────────────────

    @property
    def buffer(self) -> InputBuffer:
        """返回 InputBuffer 实例引用。"""
        return self._buffer

    @property
    def parser(self) -> InputParser:
        """返回 InputParser 实例引用。"""
        return self._parser

    @property
    def width(self) -> int:
        """终端宽度（列数），TTL 缓存。"""
        return self._term_width_cache.get_width()

    @property
    def height(self) -> int:
        """终端高度（行数），TTL 缓存。"""
        return self._term_width_cache.get_height()

    def compute_cursor(
        self,
        text: str,
        cursor_pos: int,
        bottom_lines: int,
        subagent_lines: int,
        completion_height: int,
    ) -> tuple[int, int, int, int]:
        """计算光标在终端上的位置。委托 CursorPositioner.compute()。

        Args:
            text: 输入文本（含 \\n）。
            cursor_pos: 光标在文本中的偏移位置。
            bottom_lines: 底部栏总行数。
            subagent_lines: subagent 面板行数。
            completion_height: 补全弹窗高度。

        Returns:
            (r_cursor, cursor_col, vis_row, vis_col) 四元组。
        """
        return self._cursor.compute(
            text, cursor_pos, bottom_lines,
            subagent_lines, completion_height,
        )

    # ── 回调接口 ──────────────────────────────────────────

    def set_echo_callback(self, cb) -> None:
        """设置流式输入回显回调。委托 InputBuffer。

        cb 签名: (display_text: str, cursor_pos: int) -> None
        """
        self._buffer.set_echo_callback(cb)

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

    # ── 委托方法（对 InputBuffer 的薄委托） ───────────────

    def drain_all(self) -> tuple[str | None, str]:
        """排出所有流式输入状态：返回 (submitted_text, buffer_text)。"""
        return self._buffer.drain_all()

    def get_queued_input(self) -> str | None:
        """获取排队输入。委托 InputBuffer。"""
        return self._buffer.get_queued_input()

    def has_queued_input(self) -> bool:
        """是否有排队输入等待处理。委托 InputBuffer。"""
        return self._buffer.has_queued_input()

    def set_buffer(self, text: str) -> None:
        """设置缓冲区文本（用于预填），光标移到末尾。委托 InputBuffer。"""
        self._buffer.set_buffer(text)

    def reset(self) -> None:
        """清空所有流式输入状态。委托 InputBuffer。"""
        self._buffer.reset()

    def load_history(self) -> None:
        """加载历史文件。委托 InputBuffer。"""
        self._buffer.load_history()

    def echo(self, text: str = "") -> None:
        """调用回显回调，自动获取当前文本如果未提供。"""
        if not text:
            text = self._buffer.get_current_text()
        self._buffer._echo(text)

    def get_current_text(self) -> str:
        """获取当前正在输入的文本。委托 InputBuffer。"""
        return self._buffer.get_current_text()

    def reset_and_echo(self) -> None:
        """重置缓冲区并回显空字符串（清空输入行视觉）。"""
        self._buffer.reset()
        self._buffer._echo("")

    # ── 非可打印字符捕获 ────────────────────────────────

    def capture_bytes(self, data: bytes) -> None:
        """追加原始字节到捕获缓冲区。EscapeMonitor 中调用，线程安全。"""
        with self._captured_lock:
            self._captured_input.extend(data)

    def drain_captured(self) -> str:
        """排出并返回捕获的非可打印字符。

        返回所有非 ESC/Ctrl+C 字符的 UTF-8 解码文本，并清空缓冲区。
        """
        with self._captured_lock:
            data = bytes(self._captured_input).decode("utf-8", errors="replace")
            self._captured_input.clear()
        return data


# ── 模块导出 ──────────────────────────────────────────────

__all__ = ["Input", "InputEvent"]
