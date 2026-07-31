"""OutputAdapter — 统一终端输出接口，仅用 Rich Console，单锁线程安全。

消除原实现中 print() / sys.stdout.write() / Rich console.print() 混合路径。
所有输出走 Rich Console，确保色彩一致、终端宽度自适应。

★ 锁设计（单锁简化）：
  所有 I/O 操作统一使用全局 render_lock。
  width 属性为无锁读取（GIL 保护简单属性），消除所有「锁前锁」链。

★ 锁外预渲染（v2026-07-27）：
  write() 将 Rich 渲染（console.print）移到 render_lock 之外执行，
  锁内仅做 file.write(pre_rendered_ansi) + flush。
  消除并发工具输出时因锁竞争（>1s timeout）导致 Rich Markdown 格式丢失的问题。

v2026-07-24 新增 captured_output 可选捕获，支持将渲染后的富文本输出
捕获为 ANSI 字符串列表，供 RenderBuffer 集成使用。
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text
from rich.style import Style

import logging
import time
from io import StringIO
from ._locks import render_lock, _try_acquire_output_lock

_logger = logging.getLogger(__name__)


def _new_render_console(width: int, real_console: Console) -> Console:
    """创建用于预渲染的 StringIO Console（与真实 Console 共享颜色系统）。"""
    return Console(
        file=StringIO(),
        width=width,
        force_terminal=True,
        color_system=real_console.color_system,
        soft_wrap=True,
        markup=True,
        emoji=True,
        highlight=True,
    )


class OutputAdapter:
    """统一终端输出适配器 — 单锁线程安全，简化缓冲。

    所有 write(Text) 直接输出到 Rich Console，不做中间缓冲。
    线程安全由全局 render_lock 保证。

    可选捕获模式（captured_output）：
      传入一个外部 list，每个 write() 调用同时将渲染后的 ANSI 文本
      追加到该列表，供 RenderBuffer 或后续读取使用。
    """

    def __init__(self, console: Console, *, captured_output: list[str] | None = None):
        self._console = console
        self._width = self._get_terminal_width()
        self._last_width_refresh = 0.0
        # ── 可选捕获 ──
        self._captured_output = captured_output
        self._capture_console: Console | None = None

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

    # ── 捕获 ────────────────────────────────────────────

    def _ensure_capture_console(self) -> None:
        """惰性初始化捕获控制台，与真实控制台共享颜色系统和宽度。"""
        if self._capture_console is not None or self._captured_output is None:
            return
        self._capture_file = StringIO()
        self._capture_console = Console(
            file=self._capture_file,
            width=self._width,
            force_terminal=True,
            color_system=self._console.color_system,
            soft_wrap=True,
            markup=True,
            emoji=True,
            highlight=True,
        )

    def _capture_write(self, renderable) -> None:
        """将 renderable 渲染到捕获控制台，追加到 captured_output 列表。"""
        if self._captured_output is None or not renderable:
            return
        try:
            self._ensure_capture_console()
            self._capture_file.truncate(0)
            self._capture_file.seek(0)
            if isinstance(renderable, str) and "\x1b" in renderable:
                renderable = Text.from_ansi(renderable)
            self._capture_console.print(renderable)
            self._captured_output.append(self._capture_file.getvalue())
        except Exception:
            _logger.debug("捕获渲染输出异常", exc_info=True)

    # ── 锁外预渲染 ────────────────────────────────────

    def _render_to_ansi(self, renderable) -> str:
        """将 Rich renderable 预渲染为 ANSI 字符串（锁外执行，线程安全）。

        每次调用创建独立的 StringIO Console，无共享状态，天然线程安全。
        Console 创建开销远小于 Rich 渲染本身，对性能影响可忽略。
        """
        if isinstance(renderable, str):
            return (renderable + "\n") if not renderable.endswith("\n") else renderable

        rc = _new_render_console(self._width, self._console)
        # ★ 继承真实 Console 的全局样式（如推理渲染器的 style="dim"）
        if self._console.style:
            rc.style = self._console.style
        f: StringIO = rc.file  # type: ignore[assignment]
        rc.print(renderable)
        result = f.getvalue()
        f.close()
        return result

    # ── 公共接口 ────────────────────────────────────────

    def write(self, renderable) -> None:
        """流式输出 Rich renderable — 锁外预渲染 + 锁内快速写入。

        ★ 关键优化（v2026-07-27）：
          Rich 渲染（console.print → ANSI 序列化）在 render_lock 之外执行，
          锁内仅做 file.write(pre_rendered_ansi) + flush。
          消除多并发工具输出时的锁竞争 → 超时降级 → Markdown 格式丢失问题。

        ANSI 安全：纯字符串中的 \\x1b ANSI 转义序列自动转换为 Rich Text
        对象，确保 console.print() 正确渲染而非按 markup 解析。
        """
        if not renderable:
            return
        self._refresh_width()

        # 纯字符串含 ANSI 转义序列 → 转换为 Rich Text 对象
        # 避免 Rich Console 将 [38;5;... 解析为 markup 标签
        if isinstance(renderable, str) and "\x1b" in renderable:
            renderable = Text.from_ansi(renderable)

        # ★ 锁外预渲染：Rich → ANSI（不占用 render_lock）
        ansi_str = self._render_to_ansi(renderable)

        # ★ 锁内仅快速写入（file.write + flush，无 Rich 渲染开销）
        with _try_acquire_output_lock(name="output_adapter.write", timeout=1.0) as locked:
            if locked:
                self._console.file.write(ansi_str)
            else:
                # 锁超时降级：仍然写入预渲染的 ANSI（保留全部 Rich 格式）
                self._console.file.write(ansi_str)
            self._console.file.flush()

        # 捕获：追加预渲染的 ANSI 文本（无需再调用 _capture_write 重复渲染）
        if self._captured_output is not None:
            self._captured_output.append(ansi_str)

    def batch_write(self, renderables: list) -> None:
        """批量输出多个 Rich renderable — 锁外预渲染 + 锁内合并写入。

        将多个 renderable 在锁外逐个预渲染为 ANSI 字符串，然后在单个
        render_lock 块内一次性写入 + flush。减少锁竞争和 Rich Console
        调用次数，显著优化流式场景的批量渲染性能。

        Args:
            renderables: Rich renderable 列表（支持 Text / str / Rich renderable）

        渲染顺序与输入列表顺序一致。空列表快速返回，不做任何 I/O。
        """
        if not renderables:
            return

        self._refresh_width()

        # ★ 锁外预渲染：逐个渲染所有 renderable（不占用 render_lock）
        ansi_parts: list[str] = []
        for renderable in renderables:
            if not renderable:
                continue
            # 纯字符串含 ANSI 转义序列 → 转换为 Rich Text 对象
            if isinstance(renderable, str) and "\x1b" in renderable:
                renderable = Text.from_ansi(renderable)
            ansi_str = self._render_to_ansi(renderable)
            ansi_parts.append(ansi_str)

        if not ansi_parts:
            return

        all_ansi = "".join(ansi_parts)

        # ★ 锁内仅快速合并写入（file.write + flush，单次锁获取）
        with _try_acquire_output_lock(name="output_adapter.batch_write", timeout=1.0) as locked:
            if locked:
                self._console.file.write(all_ansi)
            else:
                self._console.file.write(all_ansi)
            self._console.file.flush()

        # 捕获：追加所有预渲染的 ANSI 文本
        if self._captured_output is not None:
            for part in ansi_parts:
                self._captured_output.append(part)

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
            self._capture_write("\n")
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
        self._capture_write(text + "\n")


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
        self._capture_write(text)

    def print(self, *args, **kwargs):
        """直接代理 console.print，适用于整块渲染（Table/Syntax 等）。"""
        self._refresh_width()
        with _try_acquire_output_lock(name="output_adapter.print", timeout=1.0) as locked:
            if locked:
                self._console.print(*args, **kwargs)
                self._console.file.flush()
            else:
                # 降级：直接写入文件（解析 sep/end 保证格式正确）
                sep = kwargs.get('sep', ' ')
                end = kwargs.get('end', '\n')
                out = sep.join(str(a) for a in args)
                self._console.file.write(out + end)
                self._console.file.flush()
        # ── 捕获第一个参数（主 renderable） ──
        if args:
            self._capture_write(args[0])

    def flush(self) -> None:
        """刷出 sys.stdout。"""
        with _try_acquire_output_lock(name="output_adapter.flush", timeout=1.0) as locked:
            if locked:
                self._console.file.flush()
            else:
                # 锁超时降级：直接 flush，不静默跳过
                self._console.file.flush()
