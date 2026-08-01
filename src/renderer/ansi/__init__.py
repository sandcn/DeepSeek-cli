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
        width: 终端宽度（TOC 边框用；可由 set_width 更新）。
    """

    def __init__(self, code_theme: str = "monokai", width: int = 80):
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
        self._width = width

    def set_width(self, width: int) -> None:
        """更新终端宽度（TOC 边框用）。"""
        self._width = width

    def write(self, text: str) -> None:
        """流式写入内容块（解析 + 渲染 + 追加）。"""
        if self._closed or not text:
            return
        tokens = self._parser.feed(text)
        tokens = self._pipeline.process(tokens, self._ctx)
        for token in tokens:
            self._lines.extend(self._engine.render(token))

    def close(self) -> None:
        """关闭渲染器：flush 解析器残差并渲染（幂等）。

        流式 markdown 结束时在末尾渲染 TOC（目录，若 ctx.toc 有标题）。
        """
        if self._closed:
            return
        self._closed = True
        try:
            tokens = self._parser.flush()
            tokens = self._pipeline.process(tokens, self._ctx)
            for token in tokens:
                self._lines.extend(self._engine.render(token))
            # 末尾渲染目录
            toc = getattr(self._ctx, "toc", None)
            if toc:
                from .toc import render_toc
                self._lines.extend(render_toc(toc, self._width))
        finally:
            self._engine.reset()

    def take_lines(self) -> list[AnsiLine]:
        """取出全部已渲染行（消费缓冲）。

        ★ 消毒残留原始 ANSI：markdown 源文本可能透传输入里的原始转义序列
        （如子代理结果内嵌 read_file 高亮、模型原文）。保留进 ``Run.text``
        会让宽度测量把转义码当可见字符（宽度膨胀 → 误触发 wrap），
        ``wrap_line`` 逐字符截断把转义序列拦腰截断（残留 ``;49;00m``）渲染
        错乱。输出统一消毒——先剥完整合法序列，再移除残留孤立 ESC（防注入）；
        无 ESC 时零拷贝原样返回（fast path）。
        """
        lines = self._lines
        self._lines = []
        if not any("\x1b" in (r.text or "") for line in lines for r in line.runs):
            return lines
        from .helpers import strip_ansi as _strip_ansi
        out: list[AnsiLine] = []
        for line in lines:
            if not any("\x1b" in (r.text or "") for r in line.runs):
                out.append(line)
                continue
            clean = AnsiLine()
            for r in line.runs:
                t = _strip_ansi(r.text or "").replace("\x1b", "")
                if t:
                    clean.append(t, r.style)
            out.append(clean)
        return out

    @property
    def lines(self) -> list[AnsiLine]:
        """当前已渲染行（不消费）。"""
        return self._lines

    @property
    def is_closed(self) -> bool:
        return self._closed
