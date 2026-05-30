"""renderer 包 — 精简版增量流式 Markdown 渲染器。

所有渲染走统一路径：Parser → TokenPipeline → RenderEngine → OutputAdapter

架构（精简后）：
  IncrementalRenderer（直接使用 Token 管道）
    ├── RecursiveDescentParser  — 增量 Markdown 分词器
    ├── TokenPipeline           — 过滤器链（代码块批处理/标题锚点/流优化）
    ├── RenderEngine            — Token 消费者，输出 Rich renderable
    └── OutputAdapter           — 统一 Rich Console 输出接口

★ 锁设计：无实例级锁。
  IncrementalRenderer 本身不持有任何线程锁，原因：
  - 每个渲染器实例由单一线程/任务专用，不存在并发竞争
  - OutputAdapter 内部使用全局 output_lock 保护所有 I/O 操作
  - 移除实例锁消除了「实例锁 → output_lock」的 ABBA 死锁风险
  （该死锁曾出现在 spinner/parallel display 与流式渲染并发时）

使用方式：
  renderer = IncrementalRenderer()
  renderer.write("Hello **world**")
  renderer.close()

快速集成：
  from .renderer import IncrementalRenderer
"""

from __future__ import annotations

import logging
import time

_logger = logging.getLogger(__name__)

from rich.console import Console

from .output import OutputAdapter
from .indicator import StreamingIndicator
from .recursive_parser import RecursiveDescentParser
from .types import RenderContext, TokenType
from .engine import RenderEngine
from .pipeline import TokenPipeline, CodeBlockBatcher
from .pipeline_filters import HeadingAnchorFilter, TokenStreamOptimizer

from ...terminal import get_safe_console_config
from ._rendering import render_toc, render_render_summary


class IncrementalRenderer:
    """增量流式 Markdown 渲染器 — 直接使用 Token 管道渲染路径。

    简化架构，移除多策略模式间接层。
    所有渲染走统一路径：Parser → TokenPipeline → RenderEngine → OutputAdapter
    """

    def __init__(self, code_theme: str = "monokai", style: str = "",
                 show_indicator: bool = True, typing_speed: int = 1000,
                 show_summary: bool = False, _file=None, width: int | None = None):
        self._closed = False
        self._ctx = RenderContext()
        # 标题自动编号（默认关闭，开启后会在标题前显示如 "1.2.3  " 编号）
        self._ctx.heading_numbering = False
        self._show_summary = show_summary

        console_config = get_safe_console_config()
        if style:
            console_config["style"] = style
        if _file is not None:
            console_config["file"] = _file
        if width is not None:
            console_config["width"] = width
        console = Console(**console_config)
        self._output = OutputAdapter(console)

        self._parser = RecursiveDescentParser(ctx=self._ctx)
        self._indicator = StreamingIndicator(self._output)

        # 内置 Token 过滤器链
        self._pipeline = TokenPipeline()
        self._pipeline.add_filter(CodeBlockBatcher())
        self._pipeline.add_filter(HeadingAnchorFilter(collect_toc=True))
        self._pipeline.add_filter(TokenStreamOptimizer())

        # 渲染引擎
        self._engine = RenderEngine(
            self._output, ctx=self._ctx,
            code_theme=code_theme, typing_speed=typing_speed,
        )

        self._has_content = False
        self._indicator_started = False
        self._show_indicator = show_indicator
        # 记录渲染开始时间（用于统计摘要）
        self._ctx.start_time = time.monotonic()

    def write(self, text: str):
        if not text or self._closed:
            return
        if not self._indicator_started:
            stripped = text.strip()
            if stripped:
                if self._show_indicator:
                    self._indicator.start()
                self._indicator_started = True

        tokens = self._parser.feed(text)
        tokens = self._pipeline.process(tokens, self._ctx)
        for token in tokens:
            if not self._has_content and token.type is not TokenType.EMPTY_LINE:
                self._indicator.on_first_content()
                self._has_content = True
            self._engine.render(token)

    def close(self):
        if self._closed:
            return
        self._closed = True

        # ★ 先停止指示器动画，再输出任何 flush 内容，
        #   防止指示器光标（\r\033[K）在 flush 内容输出期间交叠覆盖。
        self._indicator.stop()

        # 刷出解析器缓冲区
        tokens = self._parser.flush()
        tokens = self._pipeline.process(tokens, self._ctx)
        for token in tokens:
            if not self._has_content and token.type is not TokenType.EMPTY_LINE:
                self._indicator.on_first_content()
                self._has_content = True
            self._engine.render(token)

        # ★ 最终刷出 Todo 进度条（防止 flush 最后 token 是 LIST_ITEM 时进度条丢失）
        self._engine.emit_todo_progress()

        # 脚注
        footnotes = self._engine.render_footnotes()
        if footnotes:
            for fn_text in footnotes:
                self._output.write(fn_text)

        # 引用链接列表
        ref_map = getattr(self._ctx, 'ref_map', None)
        if ref_map and len(ref_map) > 0:
            try:
                from rich.text import Text
                from rich.style import Style
                ref_text = Text("\n", style=Style(dim=True))
                ref_text.append("🔗 引用链接\n", style=Style(bold=True, color="bright_cyan"))
                ref_text.append(f"{'─' * self._output.width}\n", style=Style(dim=True))
                for ref_id, (url, title) in sorted(ref_map.items()):
                    line = f"  [{ref_id}] {url}"
                    ref_text.append(line, style=Style(color="cyan", underline=True))
                    if title:
                        ref_text.append(f" \"{title}\"", style=Style(dim=True, italic=True))
                    ref_text.append("\n")
                self._output.write(ref_text)
            except Exception:
                _logger.debug("引用链接列表渲染异常", exc_info=True)

        # TOC — 使用共享的 render_toc()
        toc = getattr(self._ctx, 'toc', None)
        if toc:
            try:
                self._output.write(render_toc(toc, self._output.width))
            except Exception:
                _logger.debug("TOC 渲染异常", exc_info=True)

        # 渲染统计摘要（新特性）
        if self._show_summary and self._ctx.token_count > 0:
            try:
                elapsed = time.monotonic() - self._ctx.start_time
                summary = render_render_summary(
                    self._ctx.metrics, self._ctx.token_count,
                    elapsed, self._output.width,
                )
                if summary and summary.plain.strip():
                    self._output.write(summary)
            except Exception:
                _logger.debug("渲染统计摘要异常", exc_info=True)

        self._output.flush()

    @property
    def pipeline(self):
        return self._pipeline
