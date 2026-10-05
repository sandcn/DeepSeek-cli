"""targets.terminal — 终端渲染目标。

经 Rich Console 把渲染对象写到终端（标准输出或指定文件对象）。作为可替换的
渲染目标，由清单条目（``renderer_target``）声明与注册。
"""

from __future__ import annotations

import shutil
import sys
from typing import Any, Optional

from rich.console import Console
from rich.text import Text

from .base import RenderTarget


class TerminalRenderTarget(RenderTarget):
    """终端渲染目标 — 经 Rich Console 输出到终端。"""

    label = "terminal"

    def __init__(self, width: Optional[int] = None, file: Any = None):
        self._file = file if file is not None else sys.stdout
        self._width = int(width) if width else self._detect_width()
        self._console = Console(
            file=self._file,
            width=self._width,
            soft_wrap=True,
            markup=True,
            emoji=True,
            highlight=True,
        )

    @staticmethod
    def _detect_width() -> int:
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return 80

    def write(self, renderable: Any) -> None:
        if renderable is None:
            return
        if isinstance(renderable, str) and "\x1b" in renderable:
            renderable = Text.from_ansi(renderable)
        self._console.print(renderable)

    def write_line(self, text: str = "") -> None:
        if text:
            self._console.print(text)
        else:
            self._console.print()
        self._console.file.flush()

    def clear_line(self) -> None:
        self._file.write("\r\033[K")
        self._file.flush()

    def write_raw(self, text: str) -> None:
        if not text:
            return
        self._file.write(text)
        self._file.flush()

    @property
    def width(self) -> int:
        return self._width

    def flush(self) -> None:
        self._console.file.flush()

    def close(self) -> None:
        try:
            self._console.file.flush()
        except Exception:
            pass


__all__ = ["TerminalRenderTarget"]
