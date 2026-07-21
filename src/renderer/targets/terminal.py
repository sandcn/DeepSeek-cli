"""targets.terminal — TerminalRenderTarget：终端渲染目标。

封装 OutputAdapter + Rich Console，提供 RenderTarget 接口。
所有 Rich 特有的渲染逻辑集中于此，VNodePatcher 通过 RenderTarget 接口消费。

与 OutputAdapter 的关系：
  - TerminalRenderTarget 包装 OutputAdapter 并扩展渲染能力
  - 向后兼容：接受 OutputAdapter 或 Console 构造
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


from ..output import OutputAdapter
from .._utils import get_code_style
from .._utils import decode_html_entities
from ..emoji_map import resolve_emoji
from ..math_renderer import MathRenderer
from ..mermaid_renderer import MermaidRenderer
from ..inline_renderer import render_inline
from .._rendering import build_rich_table, render_code_block_syntax
from ..types import RenderContext as EngineRenderContext

from .base import RenderTarget

logger = logging.getLogger(__name__)


class TerminalRenderTarget(RenderTarget):
    """终端渲染目标——基于 Rich Console 的输出实现。

    封装输出适配 + 内联格式渲染 + 特殊块渲染（数学/Mermaid/表格等）。

    用法：
      target = TerminalRenderTarget(output_adapter)
      target.write(rich_text)
      html = target.render_inline("Hello **world**")  # 返回 Rich Text
    """

    def __init__(self, output: OutputAdapter, code_theme: str = "monokai",
                 typing_speed: int = 0):
        logger.debug("TerminalRenderTarget 已不活跃，当前主渲染路径不直接使用此接口")
        self._output = output
        self._code_theme = code_theme
        self._typing_speed = typing_speed

        # 特殊渲染器
        self._math_renderer = MathRenderer()
        self._mermaid_renderer = MermaidRenderer()

        # 代码高亮缓存（并发保护）
        self._lock = threading.Lock()
        self._theme = None

        # 渲染上下文（用于引用链接/脚注等）
        self._ctx = EngineRenderContext()

    # ═══════════════════════════════════════════════════════
    # RenderTarget 接口实现
    # ═══════════════════════════════════════════════════════

    def write(self, renderable: Any) -> None:
        """输出 Rich renderable 对象。"""
        self._output.write(renderable)

    def write_line(self, text: str = "") -> None:
        """输出纯文本行。"""
        self._output.write_line(text)

    def write_raw(self, text: str) -> None:
        """快速输出纯文本。"""
        self._output.write_raw(text)

    def write_typing(self, renderable: Any, speed: int = 80,
                     end: str = "\n") -> None:
        """打字机效果输出。"""
        if isinstance(renderable, Text):
            self._output.write_typing(renderable, speed or self._typing_speed, end)
        else:
            # 非 Text 对象（如 Syntax/Table）直接输出
            self._output.write(renderable)

    def clear_line(self) -> None:
        """清除当前行。"""
        self._output.clear_line()

    @property
    def width(self) -> int:
        return self._output.width

    def flush(self) -> None:
        pass  # Rich Console 实时输出，无需 flush

    # ═══════════════════════════════════════════════════════
    # 内联 Markdown 渲染（委托给共享的 InlineRenderer）
    # ═══════════════════════════════════════════════════════

    def render_inline(self, text: str) -> Text:
        """将内联 Markdown 渲染为 Rich Text。

        支持：粗体、斜体、代码、链接、图片、HTML标签、Emoji、URL自动链接等。

        Args:
            text: 含内联 Markdown 的文本

        Returns:
            带样式的 Rich Text
        """
        if not text:
            return Text()

        text = resolve_emoji(text)
        text = decode_html_entities(text)
        return render_inline(text, self._ctx)

    # ═══════════════════════════════════════════════════════
    # 代码块语法高亮
    # ═══════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════

    def _ensure_theme(self):
        if self._theme is None:
            with self._lock:
                if self._theme is None:  # 双重检查锁定
                    self._theme = Syntax.get_theme(get_code_style(self._code_theme))

    def _get_lexer(self, lang: str):
        from .._rendering import get_lexer as _shared_get_lexer
        return _shared_get_lexer(lang)


