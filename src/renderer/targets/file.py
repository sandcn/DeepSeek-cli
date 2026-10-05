"""targets.file — 文件渲染目标。

把渲染对象以纯文本（无 ANSI 颜色）写入文件，用于同时输出到终端与文件等
多目标场景。作为可替换的渲染目标，由清单条目（``renderer_target``）声明与
注册。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from rich.console import Console
from rich.text import Text

from .base import RenderTarget


class FileRenderTarget(RenderTarget):
    """文件渲染目标 — 经 Rich Console（无颜色）写入文件。"""

    label = "file"

    def __init__(self, path: str, width: int = 100, encoding: str = "utf-8"):
        if not path:
            raise ValueError("FileRenderTarget 需要非空 path")
        self._path = path
        self._width = int(width) if width else 100
        self._encoding = encoding
        self._handle = None
        self._console: Optional[Console] = None

    def _ensure_open(self) -> None:
        if self._handle is not None:
            return
        directory = os.path.dirname(os.path.abspath(self._path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._handle = open(self._path, "w", encoding=self._encoding)
        self._console = Console(
            file=self._handle,
            width=self._width,
            force_terminal=False,
            color_system=None,
            soft_wrap=True,
            markup=True,
            emoji=True,
        )

    def write(self, renderable: Any) -> None:
        if renderable is None:
            return
        self._ensure_open()
        if isinstance(renderable, str) and "\x1b" in renderable:
            renderable = Text.from_ansi(renderable)
        assert self._console is not None
        self._console.print(renderable)

    def write_line(self, text: str = "") -> None:
        self._ensure_open()
        assert self._console is not None
        if text:
            self._console.print(text)
        else:
            self._console.print()

    def clear_line(self) -> None:
        return None

    @property
    def width(self) -> int:
        return self._width

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None
                self._console = None


__all__ = ["FileRenderTarget"]
