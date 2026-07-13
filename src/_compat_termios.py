"""_compat_termios — Windows 兼容的 termios/tty 封装

统一管理 termios 和 tty 模块的跨平台兼容性。

Unix/Cygwin (sys.platform != 'win32'): 直接 re-export 标准库，零开销。
Windows 原生 Python (sys.platform == 'win32'): 提供 stub 实现，
操作函数抛出 ImportError（现有 try/except ImportError 路径自动捕获），
常量保持真实值（作为参数传递时不中断）。

用法:
    from src._compat_termios import HAS_TERMIOS, termios, tty
    if HAS_TERMIOS:
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    else:
        # Windows 降级路径
"""

from __future__ import annotations

import logging
import sys

_logger = logging.getLogger(__name__)

_IS_NATIVE_WIN = sys.platform == 'win32'

if _IS_NATIVE_WIN:
    HAS_TERMIOS: bool = False

    class _SimTermios:
        """Windows stub — 操作抛出 ImportError，常量保持真实值。"""

        # ── 常量（类属性，访问不抛异常） ──
        TCSANOW: int = 0
        TCSADRAIN: int = 1
        TCIFLUSH: int = 0
        TCOFLUSH: int = 1
        TCIOFLUSH: int = 2
        ECHO: int = 0x0008
        # TIOCGWINSZ / TIOCSWINSZ — 平台相关，提供常见值
        TIOCGWINSZ: int = 0x5413
        TIOCSWINSZ: int = 0x5414

        def tcgetattr(self, fd: int) -> list:
            raise ImportError("termios 在当前平台（Windows）不可用")

        def tcsetattr(self, fd: int, when: int, attrs: object) -> None:
            raise ImportError("termios 在当前平台（Windows）不可用")

        def tcflush(self, fd: int, queue: int) -> None:
            raise ImportError("termios 在当前平台（Windows）不可用")

        # ── 不太常用但也提供 ──
        def tcsendbreak(self, fd: int, duration: int) -> None:
            raise ImportError("termios 在当前平台（Windows）不可用")

        def tcflow(self, fd: int, action: int) -> None:
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


__all__ = ["HAS_TERMIOS", "termios", "tty"]
