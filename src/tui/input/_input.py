"""Input 门面类 — 统一 TUI 输入管理入口。

组合 InputBuffer、InputParser、CursorPositioner、TerminalWidthCache，
为 EscapeMonitor / _BottomBar / InteractiveLoop 提供统一的输入管理接口。

设计模式:
  - 外观（Facade）: Input 提供统一入口，内部委托给各组件
  - 组合（Composite）: Input 组合多个子组件，不做继承

线程安全: Input 自身不添加额外锁，各组合组件独立保证线程安全。
"""

from __future__ import annotations

import os
import sys
import select
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ._buffer import InputBuffer
from ._parser import InputParser, KeyEvent
from ._cursor import CursorPositioner

if TYPE_CHECKING:
    from ..terminal.terminal import TerminalWidthCache

_logger = logging.getLogger(__name__)

# UTF-8 多字节序列读取超时（秒）
_UTF8_READ_TIMEOUT = 0.05


class Input:
    """统一输入管理门面类。

    组合 InputBuffer（缓冲+历史）、InputParser（ANSI 解析）、
    CursorPositioner（光标定位）、TerminalWidthCache（终端尺寸），
    为 EscapeMonitor 等消费者提供统一入口。

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

        # ── 回调引用（Input 类存储，后续供 EscapeMonitor 注入） ──
        self._special_key_callback = None
        self._completion_callback = None
        self._dismiss_completion_callback = None
        self._completion_navigate_callback = None
        self._auto_completion_callback = None

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
        # 退避检测：1ms → 2ms → 3ms，任一未读到数据即判定非粘贴
        for delay in (0.001, 0.002, 0.003):
            try:
                has_more, _, _ = select.select([fd], [], [], delay)
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
                return first_chars
        # 三次退避都读到数据 → 粘贴模式：批量读取所有可用字节
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
                if len(extra) >= 262144:  # 256KB 安全上限
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
        # 根据首字节确定该字符的总字节数
        if (first_byte & 0xE0) == 0xC0:
            total_bytes = 2
        elif (first_byte & 0xF0) == 0xE0:
            total_bytes = 3
        elif (first_byte & 0xF8) == 0xF0:
            total_bytes = 4
        else:
            # 无效的 UTF-8 首字节（续字节单独出现）
            return None

        # 读取剩余续字节（with short timeout）
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

        # 解码完整序列（可能因超时不完整，用 errors="replace" 容错）
        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            # 不完整/无效序列
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


# ── 模块导出 ──────────────────────────────────────────────

__all__ = ["Input"]
