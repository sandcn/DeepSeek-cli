"""OutputAdapter — 统一终端输出接口，仅用 Rich Console，单锁线程安全。

消除原实现中 print() / sys.stdout.write() / Rich console.print() 混合路径。
所有输出走 Rich Console，确保色彩一致、终端宽度自适应。

★ 锁设计（单锁简化）：
  所有 I/O 操作统一使用全局 render_lock。
  width 属性为无锁读取（GIL 保护简单属性），消除所有「锁前锁」链。
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text
from rich.style import Style

import logging
import time
from ..tui.widgets.lock import render_lock, _try_acquire_output_lock

_logger = logging.getLogger(__name__)
from .output_strategies import get_strategy


class OutputAdapter:
    """统一终端输出适配器 — 单锁线程安全，简化缓冲。

    所有 write(Text) 直接输出到 Rich Console，不做中间缓冲。
    线程安全由全局 render_lock 保证。
    """

    def __init__(self, console: Console):
        self._console = console
        self._width = self._get_terminal_width()
        self._last_width_refresh = 0.0

    def _refresh_width(self):
        """刷新终端宽度缓存（线程安全），带 5 秒 TTL 避免高频系统调用。"""
        now = time.monotonic()
        if now - self._last_width_refresh < 5.0:
            return
        self._last_width_refresh = now
        self._width = self._get_terminal_width()

    @staticmethod
    def _get_terminal_width() -> int:
        try:
            import shutil
            return shutil.get_terminal_size().columns
        except Exception:
            return 80

    def force_refresh_width(self) -> None:
        """强制刷新终端宽度缓存，绕过 5 秒 TTL。

        供 resize 检测路径调用——当终端大小变化时立即更新宽度，
        无需等待下一次 _refresh_width() 的 TTL 自然过期。
        调用方可能已持有 render_lock，本方法不重复获取锁。
        直接读写 self._width / self._last_width_refresh，在
        CPython GIL 下对简单属性是原子操作。
        """
        self._last_width_refresh = time.monotonic()
        self._width = self._get_terminal_width()

    @property
    def width(self) -> int:
        """终端宽度（无锁读取，GIL 保护简单属性安全，自动刷新缓存）。"""
        self._refresh_width()
        return self._width

    # ── 公共接口 ────────────────────────────────────────

    def write(self, renderable) -> None:
        """直接输出（无中间缓冲，简化路径）。

        锁超时降级：renderable 为 Text 时输出纯文本至文件，
        否则尝试 str() 直写，不调用 console.print（无法获取锁时）。
        """
        if not renderable:
            return
        self._refresh_width()
        with _try_acquire_output_lock(name="output_adapter.write", timeout=1.0) as locked:
            if locked:
                self._console.print(renderable)
            else:
                # 锁超时降级：直写终端，不静默丢弃数据
                # Text 类型可直接输出 plain 文本，跳过 Rich 渲染管线
                if hasattr(renderable, 'plain') and renderable.plain:
                    self._console.file.write(renderable.plain + "\n")
                    self._console.file.flush()
                else:
                    # 锁超时降级：不调用 console.print（无法获取锁），
                    # 复杂 renderable 尝试 str() 直写，降级路径可接受纯文本
                    try:
                        text_repr = str(renderable)
                        if text_repr and not text_repr.startswith('<'):
                            self._console.file.write(text_repr.rstrip() + "\n")
                            self._console.file.flush()
                    except Exception:
                        _logger.debug("降级输出 renderable 失败")

    def write_raw(self, text: str) -> None:
        """快速输出纯文本（跳过 Rich 处理，极致性能路径）。

        仅适用于：
        - 流式光标指示器（不需要 Rich 样式）
        - 纯文本行（无 Markdown 格式）

        锁超时时 fallback 直写（与 write() 行为一致），不静默丢弃数据。
        """
        if not text:
            return
        with _try_acquire_output_lock(name="output_adapter.write_raw", timeout=1.0) as locked:
            if locked:
                self._console.file.write(text)
                self._console.file.flush()
            else:
                # 锁超时降级：直写终端，不静默丢弃数据
                self._console.file.write(text)
                self._console.file.flush()

    def write_raw_buffered(self, text: str) -> None:
        """纯文本写入（跳过 Rich 处理），不执行 flush。

        与 write_raw 的唯一区别：省略末尾的 .flush() 调用。
        适用于高频面板帧输出——多行 ANSI 只需末尾一次 flush。

        锁获取逻辑与 write_raw 一致，锁超时时亦直接写入。
        """
        if not text:
            return
        with _try_acquire_output_lock(name="output_adapter.write_raw_buffered", timeout=1.0) as locked:
            if locked:
                self._console.file.write(text)
            else:
                self._console.file.write(text)

    def write_line(self, text: str = "") -> None:
        """输出纯文本行。

        空字符串快速路径：直接输出换行符，
        避免 Console.print("") 的完整渲染管线开销。
        """
        if not text:
            with _try_acquire_output_lock(name="output_adapter.write_line.empty", timeout=1.0) as locked:
                if locked:
                    self._console.file.write("\n")
                    self._console.file.flush()
                else:
                    self._console.file.write("\n")
                    self._console.file.flush()
            return
        self._refresh_width()
        with _try_acquire_output_lock(name="output_adapter.write_line.print", timeout=1.0) as locked:
            if locked:
                self._console.print(text)
                self._console.file.flush()
            else:
                # 降级直写，不丢数据
                self._console.file.write(text + "\n")
                self._console.file.flush()

    def write_typing(self, text: Text, speed: int = 80, end: str = "\n",
                     fill_style: Style | None = None,
                     mode: str = "char") -> None:
        """Write Text with typewriter effect, delegating to current strategy.

        Args:
            text: Styled Rich Text object to stream out
            speed: Characters per second (0 = instant, no delay)
            end: Trailing string after all characters (default newline)
            fill_style: If set, fill each line's remaining space with this style
                        (used for code block background)
            mode: Typewriter mode — "char" (逐字符), "line" (逐行), "instant" (即时)
        """
        if not text or not text.plain:
            return
        self._refresh_width()
        strategy = get_strategy(speed, mode)
        strategy.write(text, self._console, speed, end, fill_style,
                       render_lock, self.width)

    def clear_line(self) -> None:
        """清除当前行（用于光标/进度覆盖）。"""
        with _try_acquire_output_lock(name="output_adapter.clear_line", timeout=1.0) as locked:
            if locked:
                self._console.file.write("\r\033[K")
                self._console.file.flush()
            else:
                self._console.file.write("\r\033[K")
                self._console.file.flush()

    def write_inline(self, text: Text) -> None:
        """线程安全输出 Rich Text 到当前行（不换行）。

        Args:
            text: Rich 带样式文本
        """
        if not text:
            return
        self._refresh_width()
        with _try_acquire_output_lock(name="output_adapter.write_inline", timeout=1.0) as locked:
            if locked:
                self._console.print(text, end='')
                self._console.file.flush()
            else:
                # 锁超时降级：直写终端（plain 文本），不调用 console.print
                self._console.file.write(text.plain)
                self._console.file.flush()

    def print(self, *args, **kwargs):
        """直接代理 console.print，适用于整块渲染（Table/Syntax 等）。"""
        self._refresh_width()
        with _try_acquire_output_lock(name="output_adapter.print", timeout=1.0) as locked:
            if locked:
                self._console.print(*args, **kwargs)
            else:
                # 降级：直接写入文件（解析 sep/end 保证格式正确）
                sep = kwargs.get('sep', ' ')
                end = kwargs.get('end', '\n')
                out = sep.join(str(a) for a in args)
                self._console.file.write(out + end)
                self._console.file.flush()

    def flush(self) -> None:
        """刷出 sys.stdout。"""
        with _try_acquire_output_lock(name="output_adapter.flush", timeout=1.0) as locked:
            if locked:
                self._console.file.flush()
            else:
                # 锁超时降级：直接 flush，不静默跳过
                self._console.file.flush()
