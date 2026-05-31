"""
TerminalAdapter — 终端 I/O 抽象层

封装终端写入、ANSI 控制、尺寸获取等操作。
职责单一：仅处理「如何输出到终端」，不关心输出什么内容。

可替换性：
- 实现相同接口即可替换为标准输出、日志文件、WebSocket 等目标
- 测试时可注入 MockTerminalAdapter 验证输出行为
"""

from __future__ import annotations

import logging
import sys
import shutil
import signal as _signal
import weakref
from typing import Callable, List, Optional

_logger = logging.getLogger(__name__)


# ── SIGWINCH 注册表 ─────────────────────────────────────
# 使用 WeakSet 替代 list，避免 __del__ 不可靠调用导致的引用泄漏。
# 当 TerminalAdapter 实例被 GC 回收时，WeakSet 自动移除该引用。
_resize_instances = weakref.WeakSet()  # 所有注册了回调的 TerminalAdapter 实例


# ── 模块级终端尺寸查询（ioctl 优先，避免 shutil env var 回退） ──

def query_terminal_size() -> tuple[int, int]:
    """通过 ioctl 直接获取终端尺寸，不使用 shutil（避免 env var 回退）。

    shutil.get_terminal_size() 在 sys.stdout 非 tty 时回退到
    $LINES/$COLUMNS 环境变量。Android/Termux 上这些变量不随 resize
    更新，返回 stale 值。

    策略：
    1. 首选 /dev/tty — 直接查询控制终端
    2. 回退：逐个尝试 stdout/stderr/stdin 的 fileno
    3. 最终回退：shutil（仅在所有 fd 都不可用时）

    Returns:
        (columns, rows) 元组，与 shutil.get_terminal_size() 返回值顺序一致。
    """
    import os as _os, fcntl, termios, struct

    def _try_ioctl(_fd: int) -> tuple[int, int] | None:
        try:
            _data = fcntl.ioctl(_fd, termios.TIOCGWINSZ,
                                struct.pack("HHHH", 0, 0, 0, 0))
            _rows, _cols, _, _ = struct.unpack("HHHH", _data)
            return _cols, _rows
        except Exception:
            return None

    # /dev/tty
    try:
        _fd = _os.open("/dev/tty", _os.O_RDONLY)
    except Exception:
        _logger.debug("打开 /dev/tty 失败（非关键）")
    else:
        try:
            _result = _try_ioctl(_fd)
            if _result is not None:
                return _result
        finally:
            _os.close(_fd)

    # 标准流 fd
    for _stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            _result = _try_ioctl(_stream.fileno())
            if _result is not None:
                return _result
        except Exception:
            _logger.debug("ioctl 获取终端大小失败（std流 %s）", type(_stream).__name__)

    # 所有 ioctl 失败，回退到 shutil
    try:
        _sz = shutil.get_terminal_size()
        return _sz.columns, _sz.lines
    except Exception:
        return 80, 24


class TerminalAdapter:
    """终端 I/O 抽象层。

    提供终端写入、ANSI 清行、尺寸获取等基础操作。
    不依赖全局锁或外部状态，线程安全由调用方保证。
    """

    def __init__(self, stdout: Optional = None):
        self._stdout = stdout or sys.stdout
        self._on_resize = None  # 终端尺寸变化回调: (new_width, new_height) -> None
        _resize_instances.add(self)

    # ── 回调注册 ────────────────────────────────────────

    def set_resize_callback(self, callback: Callable[[int, int], None]) -> None:
        """设置终端尺寸变化回调。callback 签名: (width: int, height: int) -> None"""
        self._on_resize = callback

    def get_resize_callback(self):
        """获取当前终端尺寸变化回调。返回 None 表示无回调。"""
        return self._on_resize

    # ── 终端尺寸 ────────────────────────────────────────

    @staticmethod
    def _query_terminal_size() -> tuple[int, int]:
        """委托给模块级 query_terminal_size()，保持向后兼容。"""
        return query_terminal_size()

    @property
    def terminal_width(self) -> int:
        """获取终端宽度（列数）。"""
        return self._query_terminal_size()[0]

    @property
    def terminal_height(self) -> int:
        """获取终端高度（行数）。"""
        return self._query_terminal_size()[1]

    def get_terminal_size(self) -> tuple[int, int]:
        """获取终端尺寸 (列数, 行数)。"""
        return self._query_terminal_size()

    # ── 基础 I/O ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入文本到终端（含 flush）。"""
        self._stdout.write(text)
        self._stdout.flush()

    def write_raw(self, text: str) -> None:
        """写入原始文本到终端（不含 flush），供 LockedTerminal 等批量 flush 场景使用。

        与 write() 的区别：
        - write(): 每次调用后立即 flush，适合独立输出
        - write_raw(): 不 flush，由调用方在适当时机统一 flush
        """
        self._stdout.write(text)

    def flush(self) -> None:
        """强制刷新终端输出缓冲区。"""
        self._stdout.flush()

    def write_line(self, text: str = "") -> None:
        """写入一行文本（追加换行符）。"""
        self._stdout.write(text + "\n")
        self._stdout.flush()

    # ── ANSI 控制 ───────────────────────────────────────

    @staticmethod
    def set_window_title(title: str) -> None:
        """设置终端窗口标题（OSC 0 escape sequence: \\033]0;title\\007）。

        大多数终端模拟器支持此序列设置标签/窗口标题。
        Termux / GNOME Terminal / iTerm2 / Windows Terminal 等均兼容。

        Args:
            title: 要显示的标题文本（纯文本，无需 ANSI 样式）。
        """
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

    @staticmethod
    def clear_lines_code(n: int) -> str:
        """生成清除 n 行的 ANSI 控制码。

        向上移动 n 行，逐行清除，再回到起始位置。
        返回空字符串（当 n <= 0 时）或 ANSI 转义序列。
        """
        if n <= 0:
            return ''
        # ★ \033[u 恢复 render_frame 保存的光标位置（SCOSC），
        #   防止 _drain_queue → _position_cursor 移动光标后
        #   帧清除偏移（详见 204fb14e1 + cursor race 修复）
        buf = '\033[u'
        buf += f'\033[{n}A'
        for _ in range(n):
            buf += '\r\033[K\n'
        buf += f'\033[{n}A'
        return buf

    def set_scrolling_region(self, top: int, bottom: int) -> None:
        """设置终端滚动区域（DECSTBM）。

        设置后，终端仅在 [top, bottom] 行范围内滚动，
        区域外的内容保持固定。

        Args:
            top: 滚动区域起始行（1-based）
            bottom: 滚动区域结束行（1-based），0 表示屏幕底部
        """
        if bottom == 0:
            bottom_str = ""
        else:
            bottom_str = str(bottom)
        self._stdout.write(f"\033[{top};{bottom_str}r")
        self._stdout.flush()

    def reset_scrolling_region(self) -> None:
        """重置终端滚动区域为全屏。"""
        self._stdout.write("\033[r")
        self._stdout.flush()

    def move_cursor_to(self, row: int, col: int = 1) -> None:
        """移动光标到指定位置（CUP）。

        Args:
            row: 目标行（1-based）
            col: 目标列（1-based）
        """
        self._stdout.write(f"\033[{row};{col}H")
        self._stdout.flush()

    def scroll_up(self, n: int = 1) -> None:
        """向上滚动 n 行（在滚动区域内）。"""
        if n <= 0:
            return
        self._stdout.write(f"\033[{n}S")
        self._stdout.flush()

    def clear_screen_from_cursor(self) -> None:
        """清除光标位置到屏幕末尾（ED=0）。"""
        self._stdout.write("\033[0J")
        self._stdout.flush()

    # ── 帧渲染 ──────────────────────────────────────────

    def render_frame(self, lines: List[str], last_lines: int) -> int:
        """将行列表渲染到终端。

        处理 ANSI 清行定位 + 逐行写入 + 多余行清除。
        不处理锁，由调用方保证线程安全。

        Args:
            lines: 要渲染的行列表
            last_lines: 上一帧的行数（用于增量更新）

        Returns:
            渲染覆盖的最大行数（last_lines 与 total 的较大者），
            供下一帧 last_lines 使用。保留峰值确保帧缩小时
            \\033[{last_lines}A 仍能回退覆盖历史最大区域，
            防止终端残留行。
        """
        total = len(lines)

        buf = ""
        if last_lines > 0:
            # ★ \033[u 恢复上一帧保存的光标位置（SCOSC），
            #   防止 ChatUI._drain_queue → _position_cursor 将光标
            #   移到输入行后，\033[{n}A 从错误位置起算导致帧重叠/错位。
            buf += "\033[u"
            buf += f"\033[{last_lines}A"

        for i, line in enumerate(lines):
            buf += f"\r\033[K{line}"
            if i < total - 1:
                buf += "\n"

        extra = last_lines - total
        if extra > 0:
            for _ in range(extra):
                buf += "\n\033[K"
            buf += f"\033[{extra}A"

        # ★ \033[s 保存光标位置（SCOSC），供下一帧/clear_lines_code 恢复。
        #    注意 \0337 (DECSC，_BottomBar 使用) 在绝大多数终端中与
        #    \033[s 共享同一保存槽，故 render_frame 必须持 output_lock
        #    与 _BottomBar 串行化，防止保存槽被覆盖导致帧错位。
        self._stdout.write(buf + "\n\033[s")
        self._stdout.flush()
        # ★ 返回峰值行数（max(last_lines, total)）而非仅当前 total。
        #    帧缩小时（Agent running→done，工具历史行减少），若只返回
        #    新的 total，下一帧 \033[{last_lines}A 会因行数不足而遗漏
        #    旧帧顶部行，形成终端残留。保留峰值确保帧定位始终覆盖
        #    历史最大区域。
        return max(last_lines, total)


# ── SIGWINCH 信号处理 ──────────────────────────────────

# 模块级回调列表：供非 TerminalAdapter 消费者（如 _BottomBar）注册
# 信号安全的轻量回调。回调签名: (cols: int, rows: int) -> None
_sigwinch_callbacks: list = []


def register_sigwinch_callback(cb) -> None:
    """注册 SIGWINCH 回调（模块级，信号安全）。

    cb 签名: (cols: int, rows: int) -> None。
    回调中仅做轻量操作（如设置 bool 标记），避免死锁。
    """
    if cb not in _sigwinch_callbacks:
        _sigwinch_callbacks.append(cb)


def unregister_sigwinch_callback(cb) -> None:
    """注销 SIGWINCH 回调。"""
    try:
        _sigwinch_callbacks.remove(cb)
    except ValueError:
        pass


def _handle_sigwinch(signum, frame):
    """SIGWINCH 处理：通知所有已注册的 TerminalAdapter 实例 + 模块级回调。

    注意：此为信号处理器，禁止使用 logging（非信号安全），
    禁止获取锁（可能导致死锁）。异常静默丢弃。
    """
    try:
        cols, rows = query_terminal_size()
    except Exception:
        cols, rows = 80, 24
    for inst in _resize_instances:
        if inst._on_resize:
            try:
                inst._on_resize(cols, rows)
            except Exception:
                pass  # 信号安全：不使用 logging
    for cb in _sigwinch_callbacks:
        try:
            cb(cols, rows)
        except Exception:
            pass  # 信号安全：不使用 logging


try:
    _signal.signal(_signal.SIGWINCH, _handle_sigwinch)
except (AttributeError, ValueError):
    pass  # Windows 不支持 SIGWINCH，静默忽略
