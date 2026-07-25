"""
输出目标抽象接口 (IOutputTarget)

定义显示层与输出后端之间的契约。
任何输出目标（终端、WebSocket、文件日志等）实现此接口即可接入。

设计原则：
- 单一职责：只关注"如何输出"，不关心"输出什么"
- 可替换性：终端/WebSocket/日志可互换
- 无业务逻辑：接口中不包含任何渲染或格式化逻辑
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class IOutputTarget(Protocol):
    """输出目标抽象接口。

    实现此接口的类必须提供以下方法和属性。
    使用 Protocol 而非 ABC，允许结构类型匹配（duck typing）。
    """

    def write(self, text: str) -> None:
        """写入文本（不追加换行符）。"""
        ...

    def write_line(self, text: str = "") -> None:
        """写入一行文本（追加换行符）。"""
        ...

    def render_frame(self, lines: List[str], last_lines: int) -> int:
        """增量渲染帧。

        Args:
            lines: 要渲染的行列表
            last_lines: 上一帧的行数（用于增量更新）

        Returns:
            本次渲染的行数（供下一帧的 last_lines 使用）
        """
        ...

    @property
    def terminal_width(self) -> int:
        """输出目标的宽度（列数）。"""
        ...




class TerminalTarget:
    """终端输出目标 — IOutputTarget 的标准实现。

    将 IOutputTarget 接口适配到终端 stdout。
    封装 TerminalAdapter 的行为，提供一致的输出目标契约。
    """

    def __init__(self, stdout=None):
        import sys
        from ..terminal.adapter import TerminalAdapter
        # 默认使用 sys.__stdout__（真实终端），而非 sys.stdout。
        # 当其他工具并发执行 start_capture() 时，sys.stdout 可能被替换为
        # SharedCapture 实例，若此时创建 TerminalTarget 会捕获到
        # SharedCapture，导致 ParallelDisplay 面板帧被写入事件总线
        # 而非真实终端，子 Agent UI 不可见。
        self._adapter = TerminalAdapter(
            stdout=stdout if stdout is not None else sys.__stdout__,
        )

    # ── 内部锁工具 ──

    def _with_lock_or_fallback(self, locked_write, fallback_text):
        """持锁执行写入，超时时降级直写 stdout。

        Args:
            locked_write: 持锁后执行的回调（无参数）。
            fallback_text: 锁超时时直写到 sys.__stdout__ 的文本。
        """
        from ..widgets.lock import render_lock, OUTPUT_LOCK_TIMEOUT
        acquired = render_lock.acquire(timeout=OUTPUT_LOCK_TIMEOUT)
        try:
            if acquired:
                locked_write()
            else:
                # 超时降级：不持锁直写，防止 PTY 缓冲区满时静默丢弃输出
                import sys as _sys
                _sys.__stdout__.write(fallback_text)
                _sys.__stdout__.flush()
        finally:
            if acquired:
                render_lock.release()

    def write(self, text: str) -> None:
        self._with_lock_or_fallback(
            lambda: self._adapter.write(text), text,
        )

    def write_line(self, text: str = "") -> None:
        self._with_lock_or_fallback(
            lambda: self._adapter.write_line(text), text + "\n",
        )

    def render_frame(self, lines: List[str], last_lines: int) -> int:
        """增量渲染帧 — 持 render_lock 串行化终端 I/O。

        ★ 串行化保证：\033[s/\033[u（SCOSC，render_frame 使用）与
          \0337/\0338（DECSC，_BottomBar 使用）在绝大多数终端中是
          同一保存槽的别名。若两侧不加锁交替写入，_BottomBar 的
          \0337 会覆盖 render_frame 的 \033[s，导致下一帧 \033[u
          恢复到错误光标位置，造成帧重叠/错位等显示异常。

        ★ 防死锁：使用非阻塞 try-lock（超时 0.1s），不可获取时跳过
          本帧（返回 last_lines），由定时器在下一跳（100ms 后）重试。
        """
        from ..widgets.lock import _try_acquire_output_lock
        with _try_acquire_output_lock(name="terminal.render_frame", timeout=0.1) as locked:
            if locked:
                return self._adapter.render_frame(lines, last_lines)
            else:
                return last_lines

    @staticmethod
    def clear_lines_code(n: int) -> str:
        from ..terminal.adapter import TerminalAdapter
        return TerminalAdapter.clear_lines_code(n)

    @property
    def terminal_width(self) -> int:
        return self._adapter.terminal_width




# 别名：TerminalAdapter 本身也符合 IOutputTarget 接口
# 可直接作为 IOutputTarget 使用


# === 多通道输出目标实现 ===

import threading


class BufferTarget:
    """内存缓冲区输出目标 — 捕获所有输出到内存列表。

    主要用于测试：
        buf = BufferTarget()
        display = ParallelDisplay(output_target=buf)
        # ... 执行渲染 ...
        assert "✔完成" in buf.lines[-1]

    线程安全（使用 threading.Lock）。
    """

    def __init__(self):
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)

    def write_line(self, text: str = "") -> None:
        with self._lock:
            self._lines.append(text)

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        """将 lines 逐条写入缓冲区，返回 lines 长度。"""
        with self._lock:
            self._lines.extend(lines)
        return len(lines)

    @property
    def terminal_width(self) -> int:
        return 120

    @property
    def lines(self) -> list[str]:
        """获取所有写入的行（线程安全快照）。"""
        with self._lock:
            return list(self._lines)

    def clear(self) -> None:
        """清空缓冲区。"""
        with self._lock:
            self._lines.clear()

    def __len__(self) -> int:
        """缓冲区行数。"""
        with self._lock:
            return len(self._lines)

    def __getitem__(self, index):
        """按索引或切片访问行。"""
        with self._lock:
            return self._lines[index]


class NullTarget:
    """空输出目标 — 丢弃所有写入，不产生任何 I/O。

    用于静默模式、后台运行、测试等场景。
    所有 write/write_line/render_frame 均为无操作。
    """

    def write(self, text: str) -> None:
        pass

    def write_line(self, text: str = "") -> None:
        pass

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        return 0

    @property
    def terminal_width(self) -> int:
        return 120


__all__ = [
    "IOutputTarget",
    "TerminalTarget",
    "BufferTarget",
    "NullTarget",
]
