"""
UI 层输出同步锁

全局可重入锁 + 互斥事件，用于同步多线程环境下的终端输出操作。
归属 UI 层而非 core 层，与输出机制同层。

互斥事件：
  diff_active: diff 渲染标记，ParallelDisplay 刷新循环检查此标记，
  实现 render_diff 与帧刷新的互斥。

输出锁死防护：
  OUTPUT_LOCK_TIMEOUT: 获取 output_lock 的超时阈值（1.0s）。
  防止 PTY 缓冲区满时锁被永久持有，冻结整个输出管线。

★ 锁设计原则（防死锁，必须遵守）：
  1. output_lock 保护所有终端 I/O（print/write_line/render_frame）
  2. render_frame 使用非阻塞 try-lock（超时 0.1s），跳过不可获取时的帧
  3. 所有 output_lock 获取必须设超时

★ 锁获取顺序规则（防 ABBA 死锁）：
  ANY_LOCK → output_lock（output_lock 必须是最后获取的锁）
  持有 output_lock 期间禁止再获取任何其他锁。
  已修复违反案例：print_output/_DiffGuard 曾持 _render_lock 时
  取 output_lock，已改为快照+释放模式。

★ 关键路径锁关系图：
  refresh loop:     (try output_lock) → render_frame（I/O）
  _DiffGuard:       _diff_lock → _render_lock（快照）→ _diff_lock → output_lock（I/O）
  print_output:     _render_lock（快照）→ output_lock（I/O）
  tool print:       仅 output_lock
  TerminalTarget:   仅 output_lock
  _BottomBar:       仅 output_lock
  所有 output_lock 均带超时兜底
"""
import threading
import logging
from contextlib import contextmanager
from typing import Callable, Generator

output_lock = threading.RLock()
diff_active = threading.Event()

# ★ P0 防递归保护：locked_print → chat_ui.write_line → locked_print 递归
_locked_print_reentrant = threading.local()

# 回调注入机制：替代 ui/ 层对 chat_ui 的直接 import
# 由 chat_ui 侧在初始化时注册，确保依赖方向为 chat_ui → ui（单向）
_write_line_callback: Callable[[str], None] | None = None
_is_chat_ui_active_callback: Callable[[], bool] | None = None


def register_write_line_callback(cb: Callable[[str], None]) -> None:
    """注册写行回调（由 chat_ui 侧在初始化时调用）。"""
    global _write_line_callback
    _write_line_callback = cb


def register_is_chat_ui_active_callback(cb: Callable[[], bool]) -> None:
    """注册 ChatUI 活跃状态查询回调（由 chat_ui 侧在初始化时调用）。"""
    global _is_chat_ui_active_callback
    _is_chat_ui_active_callback = cb

# 终端写锁超时阈值（秒）— PTY 缓冲区满时防止锁被长期持有
OUTPUT_LOCK_TIMEOUT = 1.0

_logger = logging.getLogger(__name__)


@contextmanager
def _try_acquire_output_lock(
    timeout: float = OUTPUT_LOCK_TIMEOUT,
    name: str = "output",
) -> Generator[bool, None, None]:
    """尝试超时获取 output_lock，超时时 yield False 并降级。

    所有非关键路径的 print/write 调用应使用此上下文管理器替代
    裸 ``with output_lock:``，防止 PTY 缓冲区满时锁被永久持有，
    导致整个输出管线冻结。

    Args:
        timeout: 等待锁的超时秒数。默认 OUTPUT_LOCK_TIMEOUT (0.1s)
        name: 锁名称标识（仅调试用）

    Yields:
        bool - True 表示成功获取锁，False 表示超时
    """
    acquired = output_lock.acquire(timeout=timeout)
    if acquired:
        try:
            yield True
        finally:
            output_lock.release()
    else:
        _logger.warning("output_lock 超时（%s, %.1fs），降级为直写", name, timeout)
        yield False


def locked_print(*args, sep: str = " ", end: str = "\n", **kwargs):
    """带 output_lock 保护的 print()，超时时降级直写 sys.__stdout__。

    所有工具路径的 print() 调用应统一使用此函数替代裸 print()。
    提供的语义保证：
      - 优先路由到 ChatUI 上屏（chat_ui.write_line），确保输出经过统一渲染管线
      - ChatUI 不可用时降级为 print()
      - 获取 output_lock（带超时）→ 输出 → 释放锁
      - 超时后直接写 sys.__stdout__（不持锁），保证工具执行不阻塞

    Args:
        *args: 传给 print() 的位置参数
        sep: 分隔符（默认空格）
        end: 结尾符（默认换行）
        **kwargs: 传给 print() 的关键字参数
    """
    # 显式指定了 file → 不路由到 chat_ui，按原始路径输出
    if "file" in kwargs:
        with _try_acquire_output_lock(name="locked_print"):
            print(*args, sep=sep, end=end, **kwargs)
        return

    # ★ P0 防递归保护：chat_ui.write_line 内部可能调 locked_print，
    #    检查线程本地标志防止递归。
    if getattr(_locked_print_reentrant, 'is_active', False):
        # 递归调用时直写 stdout，不经过 ChatUI
        with _try_acquire_output_lock(name="locked_print_recursive"):
            print(*args, sep=sep, end=end, **kwargs)
        return

    # 尝试通过回调路由到 ChatUI 上屏（回调由 chat_ui 侧在初始化时注册）
    if _write_line_callback is not None:
        text = sep.join(str(a) for a in args)
        if text:
            _locked_print_reentrant.is_active = True
            try:
                _write_line_callback(text + end.rstrip("\n"))
            finally:
                _locked_print_reentrant.is_active = False
        elif end.strip():
            _locked_print_reentrant.is_active = True
            try:
                _write_line_callback(end.rstrip("\n"))
            finally:
                _locked_print_reentrant.is_active = False
        return

    # ChatUI 不可用 → 降级为 print()
    with _try_acquire_output_lock(name="locked_print"):
        print(*args, sep=sep, end=end, **kwargs)
