"""_compat_termios — Windows 兼容的 termios/tty 封装 + 终端属性互斥。

统一管理 termios 和 tty 模块的跨平台兼容性，并提供**终端属性（termios）
读-改-写序列的进程级互斥**。

Unix/Cygwin (sys.platform != 'win32'): 直接 re-export 标准库，零开销。
Windows 原生 Python (sys.platform == 'win32'): 提供 stub 实现，
操作函数抛出 ImportError（现有 try/except ImportError 路径自动捕获），
常量保持真实值（作为参数传递时不中断）。

终端属性互斥（``TERMIOS_LOCK`` / ``termios_lock`` / ``termios_noncanonical``）
的由来：termios 是「整台终端一份」的共享状态，多个持有者各自执行
「保存快照 → 临时修改 → 恢复快照」序列（如首帧 CPR 光标行查询临时 raw、
EscapeMonitor 进入 cbreak、RawModeController、kitty 能力查询）。序列交错时，
后完成者的「恢复」会用**陈旧快照**覆盖先完成者的设置——实测：启动首帧的
光标行查询（超时窗口 0.25s）与 EscapeMonitor 的 cbreak 设置交错，查询结束
时把终端恢复回**规范模式**，导致字符级输入失效（输入不逐键回显、Tab 补全
不弹出）且按键被内核回显到错误位置污染界面。所有 termios 读-改-写序列必须
经 ``termios_lock()`` 串行化（同线程可重入）。

用法:
    from src._compat_termios import HAS_TERMIOS, termios, tty

    if HAS_TERMIOS:
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    else:
        # Windows 降级路径

    # 需要「临时非规范模式读取终端响应」时：
    from src._compat_termios import termios_noncanonical
    with termios_noncanonical(fd) as modified:
        ...  # 读取 DSR/CPR 等响应
"""

from __future__ import annotations

import logging
import sys
import threading
from contextlib import contextmanager
from typing import Iterator

_logger = logging.getLogger(__name__)

#: termios 读-改-写序列的进程级互斥锁（可重入——同一线程内的嵌套序列
#: 如「RawModeController.disable 内部读属性」不会自锁）。
TERMIOS_LOCK = threading.RLock()


@contextmanager
def termios_lock() -> Iterator[None]:
    """串行化 termios 读-改-写序列（可重入）。

    所有「保存属性 → 修改 → 恢复属性」的序列都必须在此锁内执行，避免
    交错恢复用陈旧快照覆盖其它持有者的设置（详见模块 docstring）。
    """
    with TERMIOS_LOCK:
        yield


@contextmanager
def termios_noncanonical(fd: int) -> Iterator[bool]:
    """确保 fd 处于**非规范模式**（可立即读取终端响应）并在退出时恢复。

    进入时若 fd 仍处于规范模式（``ICANON`` 或 ``ECHO`` 置位）则临时关闭这
    两位；**已是非规范模式（cbreak/raw）时不改动 termios**（零副作用，避免
    与 EscapeMonitor 的设置交错）。退出时恢复进入前的完整属性快照。

    整个「读取属性 →（必要时）修改 → 读取响应 → 恢复」序列持
    ``TERMIOS_LOCK``——调用方须把实际读取逻辑放在 ``with`` 块内。

    Args:
        fd: 终端文件描述符。

    Yields:
        True — 本次修改了 termios（退出时已恢复原属性）；
        False — fd 已是非规范模式（未改动）或读取属性失败（termios 不可用）。
    """
    icano = getattr(termios, "ICANON", 0)
    echo = getattr(termios, "ECHO", 0)
    tcsadrain = getattr(termios, "TCSADRAIN", 1)
    with TERMIOS_LOCK:
        try:
            saved = termios.tcgetattr(fd)
        except Exception:
            _logger.debug("termios_noncanonical: tcgetattr 失败", exc_info=True)
            yield False
            return
        modified = False
        try:
            lflag = saved[3]
            if lflag & (icano | echo):
                mode = list(saved)
                mode[3] = lflag & ~(icano | echo)
                termios.tcsetattr(fd, tcsadrain, mode)
                modified = True
        except Exception:
            _logger.debug("termios_noncanonical: 进入非规范模式失败", exc_info=True)
            modified = False
        try:
            yield modified
        finally:
            if modified:
                try:
                    termios.tcsetattr(fd, tcsadrain, saved)
                except Exception:
                    _logger.debug("termios_noncanonical: 恢复 termios 失败", exc_info=True)


_IS_NATIVE_WIN = sys.platform == 'win32'

if _IS_NATIVE_WIN:
    HAS_TERMIOS: bool = False

    class _SimTermios:
        """Windows stub — 操作抛出 ImportError，常量保持真实值。"""

        # ── 常量（类属性，访问不抛异常） ──
        TCSADRAIN: int = 1
        TCIFLUSH: int = 0
        ECHO: int = 0x0008
        ICANON: int = 0x0100
        # TIOCGWINSZ / TIOCSWINSZ — 平台相关，提供常见值
        TIOCGWINSZ: int = 0x5413
        TIOCSWINSZ: int = 0x5414

        @staticmethod
        def tcgetattr(fd: int) -> list:
            raise ImportError("termios 在当前平台（Windows）不可用")

        @staticmethod
        def tcsetattr(fd: int, _when: int, attrs: object) -> None:
            raise ImportError("termios 在当前平台（Windows）不可用")

        @staticmethod
        def tcflush(fd: int, queue: int) -> None:
            raise ImportError("termios 在当前平台（Windows）不可用")

    class _SimTty:
        """Windows stub — 操作抛出 ImportError。"""

        @staticmethod
        def setraw(fd: int) -> None:
            raise ImportError("tty 在当前平台（Windows）不可用")

        @staticmethod
        def setcbreak(fd: int) -> None:
            raise ImportError("tty 在当前平台（Windows）不可用")

    termios = _SimTermios()
    tty = _SimTty()

else:
    # Unix / Cygwin / macOS — 直接 re-export，零开销
    import termios as termios  # type: ignore[no-redef]
    import tty as tty  # type: ignore[no-redef]

    HAS_TERMIOS: bool = True


__all__ = [
    "HAS_TERMIOS",
    "TERMIOS_LOCK",
    "termios",
    "termios_lock",
    "termios_noncanonical",
    "tty",
]
