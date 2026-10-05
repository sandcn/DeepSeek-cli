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
from src.renderer.types import TokenType

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
        from src.renderer.pipeline import TokenPipeline
        from src.renderer.extensions import builtin_filter_factories, filter_factories

        self._ctx = RenderContext()
        self._parser = RecursiveDescentParser(ctx=self._ctx)
        self._pipeline = TokenPipeline()
        # 内置过滤器（渲染扩展注册表提供，可被禁用/替换）
        for _factory in builtin_filter_factories():
            try:
                self._pipeline.add_filter(_factory())
            except Exception:
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "内置 ANSI 渲染过滤器装配失败: %r", _factory, exc_info=True
                )
        # 扩展过滤器（插件经 ctx.renderer.register_filter 注册）
        for _factory in filter_factories():
            try:
                self._pipeline.add_filter(_factory())
            except Exception:
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "扩展 ANSI 渲染过滤器注册失败: %r", _factory, exc_info=True
                )
        self._engine = AnsiRenderEngine(code_theme=code_theme, width=width)
        # ★ 流式预览：未闭合块（段落/代码块/表格/引用等）每次 write 后整块
        #   重渲染为预览行。使用**独立引擎实例**——预览渲染不污染主引擎的
        #   流式缓冲状态（引用/告示/代码等的 OPEN-LINE 缓冲）。
        self._preview_engine = AnsiRenderEngine(code_theme=code_theme, width=width)
        self._preview_lines: list[AnsiLine] = []
        self._lines: list[AnsiLine] = []
        self._closed = False
        self._width = width

    def set_width(self, width: int) -> None:
        """更新终端宽度（TOC 边框 + 表格宽度自适应用）。"""
        self._width = width
        self._engine.set_width(width)
        self._preview_engine.set_width(width)

    def write(self, text: str) -> None:
        """流式写入内容块（解析 + 渲染 + 追加）。

        渲染已确定的 committed 行后刷新未闭合块预览（``_refresh_preview``）。
        """
        if self._closed or not text:
            return
        tokens = self._parser.feed(text)
        tokens = self._pipeline.process(tokens, self._ctx)
        for token in tokens:
            if token.type is TokenType.TOC_MARKER:
                self._lines.extend(self._render_toc())
                continue
            self._lines.extend(self._engine.render(token))
        self._refresh_preview()

    def _render_toc(self) -> list[AnsiLine]:
        """渲染 ``[TOC]`` 标记处的目录（当前已收集标题）。

        ★ 问题7（位置修复）：TOC 仅由文档中的 ``[TOC]`` 标记触发、在标记
        位置渲染；不再于 ``close()`` 时无条件追加到内容末尾（修复前每条
        subagent markdown / 历史回放消息末尾都会追加一个目录框，位置错误）。
        """
        toc = getattr(self._ctx, "toc", None)
        if not toc:
            return []
        from .toc import render_toc
        return list(render_toc(toc, self._width))

    def _refresh_preview(self) -> None:
        """刷新未闭合块预览行（整块重渲染，独立引擎互不污染）。

        解析器 ``peek_pending`` 返回当前未闭合状态的自包含 Token 序列；
        独立预览引擎每次 reset 后渲染，产出 ``_preview_lines`` 供 UI 替换
        上一帧预览（块闭合后由 committed 行替换，预览清空）。
        """
        try:
            ptokens = self._parser.peek_pending()
        except Exception:
            self._preview_lines = []
            return
        if not ptokens:
            self._preview_lines = []
            return
        eng = self._preview_engine
        eng.reset()
        lines: list[AnsiLine] = []
        for tok in ptokens:
            lines.extend(eng.render(tok))
        self._preview_lines = lines

    def take_preview_lines(self) -> list[AnsiLine]:
        """返回当前未闭合块预览行（全量，不消费——UI 每次整体替换）。

        与 ``take_lines`` 不同：预览是「当前未闭合状态的整体快照」，UI 侧
        以**替换**语义使用（每次 write 后用最新预览替换上一帧预览），因此
        本方法不清空缓冲；``close()`` 后预览恒为空列表。
        """
        return self._sanitize_lines(list(self._preview_lines))

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
                # [TOC] 标记在 flush 残差中同样就地渲染
                if token.type is TokenType.TOC_MARKER:
                    self._lines.extend(self._render_toc())
                    continue
                self._lines.extend(self._engine.render(token))
        finally:
            self._engine.reset()
            self._preview_lines = []

    def take_lines(self) -> list[AnsiLine]:
        """取出全部已渲染行（消费缓冲）。

        ★ 消毒残留原始 ANSI：markdown 源文本可能透传输入里的原始转义序列
        （如子代理结果内嵌 read_file 高亮、模型原文）。保留进 ``Run.text``
        会让宽度测量把转义码当可见字符（宽度膨胀 → 误触发 wrap），
        ``wrap_line`` 逐字符截断把转义序列拦腰截断（残留 ``;49;00m``）渲染
        错乱。输出统一消毒——**合法 SGR 解析为 Run 样式保留颜色**（问题8：
        修复前一律剥离，模型/工具的合法高亮被误伤），非 SGR 控制序列/孤立
        ESC 移除（防注入）；无 ESC 时零拷贝原样返回（fast path）。
        """
        lines = self._lines
        self._lines = []
        return self._sanitize_lines(lines)

    @staticmethod
    def _sanitize_lines(lines: list[AnsiLine]) -> list[AnsiLine]:
        """ANSI 消毒：合法 SGR → Run 样式（保留颜色），其余控制序列移除。"""
        def _has_esc(runs) -> bool:
            return any(
                "\x1b" in (r.text or "") or "\x07" in (r.text or "")
                for r in runs
            )

        if not any(_has_esc(line.runs) for line in lines):
            return lines
        from .helpers import ansi_to_runs, strip_ansi
        out: list[AnsiLine] = []
        for line in lines:
            if not _has_esc(line.runs):
                out.append(line)
                continue
            new_line = AnsiLine()
            for r in line.runs:
                text = r.text or ""
                if "\x1b" not in text and "\x07" not in text:
                    new_line.append(text, r.style)
                    continue
                for sub in ansi_to_runs(text, r.style):
                    clean = strip_ansi(sub.text).replace("\x1b", "").replace("\x07", "")
                    if clean:
                        new_line.append(clean, sub.style)
            out.append(new_line)
        return out

    @property
    def lines(self) -> list[AnsiLine]:
        """当前已渲染行（不消费）。"""
        return self._lines

    @property
    def is_closed(self) -> bool:
        return self._closed
