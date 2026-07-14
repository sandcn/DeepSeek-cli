"""
UI 层输出同步锁 — render_lock（渲染）+ io_lock（I/O）

细粒度拆分方案（步骤 6）：
  render_lock — 保护渲染管线（_drain_queue → _phase_render → _phase_redraw_bottom）
  io_lock     — 保护终端 I/O（locked_print / LockedTerminal / 裸终端写入）
  output_lock — 保留为 render_lock 的 @deprecated 兼容别名

互斥事件：
  diff_active: diff 渲染标记，ParallelDisplay 刷新循环检查此标记，
  实现 render_diff 与帧刷新的互斥。

输出锁死防护：
  OUTPUT_LOCK_TIMEOUT: render_lock/io_lock 获取的超时阈值（1.0s）。
  防止 PTY 缓冲区满时锁被永久持有，冻结整个输出管线。

★ 锁设计原则（防死锁，必须遵守）：
  1. render_lock 保护渲染管线
  2. io_lock 保护终端 I/O（print/write_line/LockedTerminal）
  3. render_frame 使用非阻塞 try-lock（超时 0.1s），跳过不可获取时的帧
  4. 所有锁获取必须设超时

★ 锁获取顺序规则（防 ABBA 死锁）：
  render_lock → io_lock（render_lock 必须是先获取的锁）
  持有 io_lock 期间禁止再获取 render_lock。
  _try_acquire_output_lock 优先尝试 render_lock，降级到 io_lock。

★ 关键路径锁关系图：
  refresh loop:     (try render_lock) → render_frame（I/O）
  _DiffGuard:       _diff_lock → _render_lock（快照）→ _diff_lock → render_lock（I/O）
  print_output:     _render_lock（快照）→ render_lock（I/O）
  tool print:       仅 io_lock（locked_print）
  TerminalTarget:   仅 io_lock（通过 _try_acquire_io_lock 或裸 io_lock）
  LockedTerminal:   仅 io_lock
  _BottomBar:       仅 io_lock
  所有锁获取均带超时兜底
"""
import threading
import logging
from contextlib import contextmanager
from typing import Generator

# ── 锁实例 ────────────────────────────────────────

render_lock = threading.RLock()     # 渲染管线锁
io_lock = threading.Lock()          # 终端 I/O 锁

# @deprecated — 使用 render_lock/io_lock 替代，v1.3+ 将移除
output_lock = render_lock

diff_active = threading.Event()

# ★ P0 防递归保护：locked_print → chat_ui.write_line → locked_print 递归
_locked_print_reentrant = threading.local()

# ★ P2 锁调试追踪 — 记录当前线程的锁持有栈
# 仅用于调试，不影响运行时行为。
# held_stack 是一个 list[str]，记录当前线程获取的锁名称。
# 在 _try_acquire_*_lock 中维护。
_lock_debug = threading.local()
_lock_debug.held_stack = []  # type: list[str]

# 终端写锁超时阈值（秒）— PTY 缓冲区满时防止锁被长期持有
OUTPUT_LOCK_TIMEOUT = 1.0

_logger = logging.getLogger(__name__)


# ── 锁获取上下文管理器 ────────────────────────────


@contextmanager
def _try_acquire_io_lock(
    timeout: float = OUTPUT_LOCK_TIMEOUT,
    name: str = "io",
) -> Generator[bool, None, None]:
    """尝试超时获取 io_lock，超时时 yield False 并降级。

    终端 I/O 操作（locked_print / LockedTerminal / 裸终端写入）
    应使用此上下文管理器替代裸 ``with io_lock:``。

    Args:
        timeout: 等待锁的超时秒数。默认 OUTPUT_LOCK_TIMEOUT (1.0s)
        name: 锁名称标识（仅调试用）

    Yields:
        bool - True 表示成功获取锁，False 表示超时
    """
    if not hasattr(_lock_debug, 'held_stack'):
        _lock_debug.held_stack = []
    _lock_debug.held_stack.append("io_lock")
    acquired = io_lock.acquire(timeout=timeout)
    if acquired:
        try:
            yield True
        finally:
            _lock_debug.held_stack.pop()
            io_lock.release()
    else:
        _lock_debug.held_stack.pop()
        _logger.warning("io_lock 超时（%s, %.1fs），降级为直写", name, timeout)
        yield False


@contextmanager
def _try_acquire_output_lock(
    timeout: float = OUTPUT_LOCK_TIMEOUT,
    name: str = "render",
) -> Generator[bool, None, None]:
    """尝试超时获取 render_lock（优先），超时时降级到 io_lock。

    用于渲染管线（_drain_queue → _phase_render → _phase_redraw_bottom）。
    优先尝试 render_lock；若不可获取则降级到 io_lock，确保至少
    终端 I/O 不被阻塞。

    Args:
        timeout: 等待锁的超时秒数。默认 OUTPUT_LOCK_TIMEOUT (1.0s)
        name: 锁名称标识（仅调试用）

    Yields:
        bool - True 表示成功获取锁，False 表示超时
    """
    if not hasattr(_lock_debug, 'held_stack'):
        _lock_debug.held_stack = []

    # ★ 优先尝试 render_lock（渲染管线锁）
    _lock_debug.held_stack.append("render_lock")
    acquired = render_lock.acquire(timeout=timeout)
    if acquired:
        try:
            yield True
        finally:
            _lock_debug.held_stack.pop()
            render_lock.release()
    else:
        _lock_debug.held_stack.pop()

        # ★ 降级到 io_lock（终端 I/O 锁）
        _lock_debug.held_stack.append("io_lock")
        acquired = io_lock.acquire(timeout=timeout)
        if acquired:
            try:
                yield True
            finally:
                _lock_debug.held_stack.pop()
                io_lock.release()
        else:
            _lock_debug.held_stack.pop()
            _logger.warning(
                "render_lock 和 io_lock 均超时（%s, %.1fs），降级为直写",
                name, timeout,
            )
            yield False


def locked_print(*args, sep: str = " ", end: str = "\n", **kwargs):
    """带 io_lock 保护的 print()，超时时降级直写 sys.__stdout__。

    所有工具路径的 print() 调用应统一使用此函数替代裸 print()。
    提供的语义保证：
      - 优先路由到 ChatUI 上屏（chat_ui.write_line），确保输出经过统一渲染管线
      - ChatUI 不可用时降级为 print()
      - 获取 io_lock（带超时）→ 输出 → 释放锁
      - 超时后直接写 sys.__stdout__（不持锁），保证工具执行不阻塞

    Args:
        *args: 传给 print() 的位置参数
        sep: 分隔符（默认空格）
        end: 结尾符（默认换行）
        **kwargs: 传给 print() 的关键字参数
    """
    # 显式指定了 file → 不路由到 chat_ui，按原始路径输出
    if "file" in kwargs:
        with _try_acquire_io_lock(name="locked_print"):
            print(*args, sep=sep, end=end, **kwargs)
        return

    # ★ P0 防递归保护：chat_ui.write_line 内部可能调 locked_print，
    #    检查线程本地标志防止递归。
    if getattr(_locked_print_reentrant, 'is_active', False):
        # 递归调用时直写 stdout，不经过 ChatUI
        with _try_acquire_io_lock(name="locked_print_recursive"):
            print(*args, sep=sep, end=end, **kwargs)
        return

    # 尝试路由到 ChatUI 上屏
    try:
        from ..consumer.state import get_active_chat_ui  # noqa: PLC0415
        chat_ui = get_active_chat_ui()
        if chat_ui is not None:
            text = sep.join(str(a) for a in args)
            if text:
                _locked_print_reentrant.is_active = True
                try:
                    chat_ui.write_line(text + end.rstrip("\n"))
                finally:
                    _locked_print_reentrant.is_active = False
            elif end.strip():
                _locked_print_reentrant.is_active = True
                try:
                    chat_ui.write_line(end.rstrip("\n"))
                finally:
                    _locked_print_reentrant.is_active = False
            return
    except Exception:
        pass

    # ChatUI 不可用 → 降级为 print()
    with _try_acquire_io_lock(name="locked_print"):
        print(*args, sep=sep, end=end, **kwargs)
