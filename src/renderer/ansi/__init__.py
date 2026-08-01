"""ansi — 自绘 ANSI 内容引擎（零 Rich，复用解析层）。

``AnsiStreamRenderer`` 是 TUI 内容路径的入口（替代 IncrementalRenderer 角色）：
  write(chunk) → RecursiveDescentParser.feed → TokenPipeline（CodeBlockBatcher/
  HeadingAnchorFilter/TokenStreamOptimizer）→ AnsiRenderEngine → AnsiLine 追加。

子模块：
  engine.py  — AnsiRenderEngine（token → AnsiLine）
  inline.py  — 行内格式（粗体/斜体/行内码/链接）
  blocks.py  — 标题/列表/引用/告示/折叠块
  code.py    — 代码块（pygments → 256 色）
  table.py   — 表格（wcswidth 对齐 + 框线）
  mermaid.py / math.py — 首版纯文本退化（标注限制）
  helpers.py — Run/AnsiLine 模型 + 换行/截断/ANSI→Style
"""

from __future__ import annotations

from .helpers import Run, AnsiLine, wrap_line, truncate_line, ansi_to_line
from .engine import AnsiRenderEngine

__all__ = [
    "AnsiStreamRenderer",
    "AnsiRenderEngine",
    "Run",
    "AnsiLine",
    "wrap_line",
    "truncate_line",
    "ansi_to_line",
]


class AnsiStreamRenderer:
    """流式 ANSI 内容渲染器（TUI 内容路径）。

    复用解析层（RecursiveDescentParser + TokenPipeline + CodeBlockBatcher），
    渲染为 AnsiLine 追加到内部缓冲；``take_lines()`` 消费缓冲。

    Args:
        code_theme: pygments 代码主题名。
    """

    def __init__(self, code_theme: str = "monokai"):
        from src.renderer.recursive_parser import RecursiveDescentParser
        from src.renderer.types import RenderContext
        from src.renderer.pipeline import TokenPipeline, CodeBlockBatcher
        from src.renderer.pipeline_filters import HeadingAnchorFilter, TokenStreamOptimizer

        self._ctx = RenderContext()
        self._parser = RecursiveDescentParser(ctx=self._ctx)
        self._pipeline = TokenPipeline()
        self._pipeline.add_filter(CodeBlockBatcher())
        self._pipeline.add_filter(HeadingAnchorFilter(collect_toc=True))
        self._pipeline.add_filter(TokenStreamOptimizer())
        self._engine = AnsiRenderEngine(code_theme=code_theme)
        self._lines: list[AnsiLine] = []
        self._closed = False

    def write(self, text: str) -> None:
        """流式写入内容块（解析 + 渲染 + 追加）。"""
        if self._closed or not text:
            return
        tokens = self._parser.feed(text)
        tokens = self._pipeline.process(tokens, self._ctx)
        for token in tokens:
            self._lines.extend(self._engine.render(token))

    def close(self) -> None:
        """关闭渲染器：flush 解析器残差并渲染（幂等）。"""
        if self._closed:
            return
        self._closed = True
        try:
            tokens = self._parser.flush()
            tokens = self._pipeline.process(tokens, self._ctx)
            for token in tokens:
                self._lines.extend(self._engine.render(token))
        finally:
            self._engine.reset()

    def take_lines(self) -> list[AnsiLine]:
        """取出全部已渲染行（消费缓冲）。"""
        lines = self._lines
        self._lines = []
        return lines

    @property
    def lines(self) -> list[AnsiLine]:
        """当前已渲染行（不消费）。"""
        return self._lines

    @property
    def is_closed(self) -> bool:
        return self._closed
