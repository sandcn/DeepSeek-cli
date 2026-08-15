"""core/_terminal — 共享终端宽度查询（差异封装）。

从 ``parallel_executor._get_terminal_width`` 迁移的 ioctl 策略，
成为终端宽度查询的唯一真源（BUG-A3 消费方复用）。

实现要点：
  - 优先通过 /dev/tty ioctl 查询真实宽度（Android Termux 上
    shutil.get_terminal_size() 返回陈旧环境变量值，不可依赖）
  - /dev/tty 不可用时静默回退 shutil → 80 兜底（与现状一致）
  - 模块命名避免与 TUI ``_screen._get_terminal_size`` 混淆
"""

from __future__ import annotations


def get_terminal_width() -> int:
    """获取终端宽度（列数），优先通过 /dev/tty ioctl 查询。

    Returns:
        终端列数；ioctl 与 shutil 均失败时返回 80 兜底。
    """
    import os
    import struct

    try:
        import fcntl
        import termios

        fd = os.open("/dev/tty", os.O_RDONLY)
        try:
            data = fcntl.ioctl(fd, termios.TIOCGWINSZ,
                               struct.pack("HHHH", 0, 0, 0, 0))
            rows, cols, _, _ = struct.unpack("HHHH", data)
            return cols if cols > 0 else 80
        finally:
            os.close(fd)
    except Exception:
        pass
    # 回退
    try:
        import shutil
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


__all__ = ["get_terminal_width"]
