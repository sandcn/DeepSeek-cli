"""输出目标抽象 — IOutputTarget Protocol 与内置实现。

提供终端输出的统一接口，支持不同输出策略（终端/Buffer/Null/Inline）。
框架版本：独立于 src/tui/ 的 IOutputTarget 定义。

使用方式：
    from tui_framework.terminal.output_target import IOutputTarget, BufferTarget
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IOutputTarget(Protocol):
    """输出目标协议 — 定义终端输出的最小接口。

    所有输出目标（TerminalTarget / BufferTarget / NullTarget / InlineOutputTarget）
    须实现此协议中的属性和方法。

    Attributes:
        terminal_width: 终端宽度（列数）。
        supports_inline: 是否支持 inline 模式（非全屏逐行输出）。
    """

    @property
    def terminal_width(self) -> int:
        """获取终端宽度（列数）。"""
        ...

    @property
    def supports_inline(self) -> bool:
        """是否支持 inline 模式。

        True:  输出为纯文本流，无帧覆盖，适合管道/日志/测试。
        False: 输出为全屏帧覆盖模式（使用 SCOSC/DECRC 等 ANSI 序列）。
        """
        ...

    def write(self, text: str) -> None:
        """写入文本到输出目标。"""
        ...

    def write_line(self, text: str = "") -> None:
        """写入一行文本（追加换行符）。"""
        ...

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        """渲染一帧内容。

        Args:
            lines: 要渲染的行列表（每行可含 ANSI 样式）。
            last_lines: 上一帧的行数（用于增量更新/清除残留行）。

        Returns:
            当前帧覆盖的行数，供下一帧 last_lines 使用。
        """
        ...

    def clear_last_lines(self, n: int) -> None:
        """清除最后 n 行输出（可选方法）。

        Inline 模式专用：向上清除已输出的行。
        全屏模式实现可为空操作。
        """
        ...

    def flush(self) -> None:
        """刷新输出缓冲区。"""
        ...


# ═══════════════════════════════════════════════════════════
# 内置实现
# ═══════════════════════════════════════════════════════════


class BufferTarget:
    """内存缓冲输出目标 — 用于测试和验证。

    所有输出写入内存缓冲区（StringIO），可通过 get_output() 获取完整输出。
    supports_inline=True，输出无 ANSI 光标控制序列。
    """

    def __init__(self, width: int = 80) -> None:
        import io
        self._buffer = io.StringIO()
        self._width = width
        self._lines: list[str] = []

    @property
    def terminal_width(self) -> int:
        return self._width

    @property
    def supports_inline(self) -> bool:
        return True

    def write(self, text: str) -> None:
        self._buffer.write(text)

    def write_line(self, text: str = "") -> None:
        self._buffer.write(text + "\n")

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        """渲染帧：直接逐行追加，无帧覆盖逻辑。"""
        for line in lines:
            self._buffer.write(line + "\n")
        self._lines = lines
        return len(lines)

    def clear_last_lines(self, n: int) -> None:
        """清除最后 n 行（inline 模式兼容）。BufferTarget 为追加模式，此为空操作。"""
        pass

    def flush(self) -> None:
        pass

    def get_output(self) -> str:
        """获取完整输出内容。"""
        return self._buffer.getvalue()

    def clear(self) -> None:
        """清空缓冲区。"""
        import io
        self._buffer = io.StringIO()
        self._lines = []


class NullTarget:
    """空输出目标 — 静默丢弃所有输出。

    用于不需要输出的场景（如后台任务）。
    """

    def __init__(self, width: int = 80) -> None:
        self._width = width

    @property
    def terminal_width(self) -> int:
        return self._width

    @property
    def supports_inline(self) -> bool:
        return True

    def write(self, text: str) -> None:
        pass

    def write_line(self, text: str = "") -> None:
        pass

    def clear_last_lines(self, n: int) -> None:
        pass

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        return len(lines)

    def flush(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════
# InlineOutputTarget — 非全屏逐行输出模式
# ═══════════════════════════════════════════════════════════


class InlineOutputTarget:
    """Inline 输出目标 — 非全屏逐行输出模式。

    不使用 DECSTBM/SCOSC/DECRC 等非标准 ANSI 序列。
    输出为纯文本流，通过 ``\\r\\033[K`` 逐行清行覆盖，
    兼容性优于全屏帧覆盖模式。

    实现 IOutputTarget 协议，支持：
    - write / write_line: 直接写入 stdout
    - render_frame: 回退 last_lines 行后逐行输出，通过 \\r\\033[K 清行
    - clear_last_lines: 向上清除已输出的行
    - terminal_width: 委托 TerminalAdapter 查询

    使用方式：
        from tui_framework.terminal.output_target import InlineOutputTarget
        target = InlineOutputTarget()
        target.write_line("hello")
        target.render_frame(["line1", "line2"], 0)
    """

    def __init__(self, stdout=None, terminal_adapter=None):
        import sys
        from .adapter import TerminalAdapter
        self._stdout = stdout or sys.stdout
        self._adapter = terminal_adapter or TerminalAdapter(stdout=self._stdout)
        self._last_lines = 0

    @property
    def terminal_width(self) -> int:
        """终端宽度（列数），委托 TerminalAdapter 查询。"""
        return self._adapter.terminal_width

    @property
    def supports_inline(self) -> bool:
        """Inline 模式始终返回 True。"""
        return True

    def write(self, text: str) -> None:
        """写入文本到 stdout（含 flush）。"""
        self._stdout.write(text)
        self._stdout.flush()

    def write_line(self, text: str = "") -> None:
        """写入一行文本（追加换行符）。"""
        self._stdout.write(text + "\n")
        self._stdout.flush()

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        """渲染一帧内容（inline 模式）。

        不使用 DECSTBM/SCOSC/DECRC，改为纯文本逐行输出：
        1. 先回退 last_lines 行到上一帧起始位置
        2. 逐行写入，每行前用 ``\\r\\033[K`` 清行
        3. 多余行用 ``\\n\\033[K`` 清除后回退

        Args:
            lines: 要渲染的行列表（每行可含 ANSI 样式）。
            last_lines: 上一帧的行数（用于回退定位）。

        Returns:
            本次渲染覆盖的行数（峰值），供下一帧 last_lines 使用。
        """
        total = len(lines)
        buf = ""

        # 回退到上一帧起始位置
        if last_lines > 0:
            buf += f"\033[{last_lines}A"

        # 逐行输出，每行先清行再写入
        for i, line in enumerate(lines):
            buf += f"\r\033[K{line}"
            if i < total - 1:
                buf += "\n"

        # 清除多余行（帧缩小时）
        extra = last_lines - total
        if extra > 0:
            for _ in range(extra):
                buf += "\n\033[K"
            buf += f"\033[{extra}A"

        self._stdout.write(buf + "\n")
        self._stdout.flush()
        self._last_lines = max(last_lines, total)
        return self._last_lines

    def clear_last_lines(self, n: int) -> None:
        """清除最近输出的 n 行。

        向上移动 n 行，逐行用 ``\\r\\033[K`` 清除。

        Args:
            n: 要清除的行数。n <= 0 时为空操作。
        """
        if n <= 0:
            return
        buf = ""
        for _ in range(n):
            buf += "\033[A\r\033[K"
        self._stdout.write(buf)
        self._stdout.flush()

    def batch_write(self, lines: list[str]) -> None:
        """批量写入多行文本，统一 flush。

        对比逐行 ``write()``（每次 flush），本方法将所有行写入后
        仅 flush 一次，减少 I/O 开销，适合批量输出场景。

        Args:
            lines: 要写入的文本行列表（每行不含换行符）。
        """
        for line in lines:
            self._stdout.write(line)
        self._stdout.flush()

    def flush(self) -> None:
        """刷新输出缓冲区。"""
        self._stdout.flush()


__all__ = [
    "IOutputTarget",
    "BufferTarget",
    "NullTarget",
    "InlineOutputTarget",
]
