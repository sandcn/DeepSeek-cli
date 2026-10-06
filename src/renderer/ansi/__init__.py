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
        self._code_theme = code_theme
        # ★ 流式预览：未闭合块（段落/代码块/表格/引用等）每次 write 后整块
        #   重渲染为预览行。使用**独立引擎实例**——预览渲染不污染主引擎的
        #   流式缓冲状态（引用/告示/代码等的 OPEN-LINE 缓冲）。
        self._preview_engine = AnsiRenderEngine(code_theme=code_theme, width=width)
        self._preview_lines: list[AnsiLine] = []
        # ★ 代码块预览增量高亮缓存（键 = (lang, theme)；流式只追加时仅渲染
        #   新增行，显示侧再按 _PREVIEW_MAX_LINES 截断并给出省略提示）。
        self._code_preview_key: tuple | None = None
        self._code_preview_src: list[str] = []
        self._code_preview_rows: list[AnsiLine] = []
        self._lines: list[AnsiLine] = []
        self._closed = False
        self._width = width
        # 当前未闭合代码块已提交（分段刷出）的代码行数——预览据此跳过，
        # 避免与 committed 行重复显示（见 ``_note_committed_code``）。
        self._committed_code_lines = 0

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
            self._note_committed_code(token)
            if token.type is TokenType.TOC_MARKER:
                self._lines.extend(self._render_toc())
                continue
            self._lines.extend(self._engine.render(token))
        self._refresh_preview()

    def _note_committed_code(self, token) -> None:
        """跟踪当前未闭合代码块已提交（分段刷出）的代码行数。

        超长代码块超过 ``CodeBlockBatcher`` 缓冲上限时会被分段刷出，已刷出
        的行进入 committed 行；预览若仍从第 0 行整块重渲，会与 committed 行
        **重复显示**（同一批代码行出现两次）。记录已提交行数，供
        ``_render_code_preview`` 跳过——预览只呈现尚未提交的尾部。
        """
        if token.type is TokenType.CODE_BLOCK:
            if token.meta.get("closed", True):
                self._committed_code_lines = 0
            else:
                self._committed_code_lines += token.content.count("\n") + 1
        else:
            self._committed_code_lines = 0

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

        ★ 代码块预览走 ``_render_code_preview``（按行增量高亮缓存）——不整块
        重新词法高亮，避免长代码块流式期间每帧 O(预览行数) 的重复开销。
        """
        try:
            ptokens = self._parser.peek_pending()
        except Exception:
            self._preview_lines = []
            self._reset_code_preview_cache()
            return
        if not ptokens:
            self._preview_lines = []
            self._reset_code_preview_cache()
            return
        eng = self._preview_engine
        eng.reset()
        lines: list[AnsiLine] = []
        for tok in ptokens:
            if tok.type is TokenType.CODE_BLOCK and tok.meta.get("preview"):
                lines.extend(self._render_code_preview(tok))
                continue
            lines.extend(eng.render(tok))
        self._preview_lines = lines

    def _reset_code_preview_cache(self) -> None:
        """清空代码块预览增量高亮缓存（块闭合/预览清空时调用）。"""
        self._code_preview_key = None
        self._code_preview_src = []
        self._code_preview_rows = []

    def _render_code_preview(self, token) -> list[AnsiLine]:
        """代码块流式预览：按行增量高亮缓存 + 尾部截断省略提示。

        逐行高亮对「只追加」的流式输入可安全缓存（``highlight_code_lines``
        本身逐行处理、无跨行状态），仅渲染新增行——修复前每次 write 对整段
        预览（最多 ``_PREVIEW_MAX_LINES`` 行）重新词法高亮，600 行代码流式
        输出实测约 27s，渲染线程在 10Hz 下近乎满载，进而造成命令队列背压。
        """
        from . import code as _code
        from .._block_parser import RegexFreeBlockParser

        src = token.content or ""
        lang = token.meta.get("lang", "")
        title = token.meta.get("title", "")
        closed = bool(token.meta.get("closed", True))
        src_lines = src.split("\n") if src else []
        # 超长代码块的分段提交：已提交的前 skip 行由 committed 显示，预览只
        # 呈现尚未提交的尾部（否则同一批行在 committed 与 preview 中重复显示）。
        skip = min(self._committed_code_lines, len(src_lines))
        if skip:
            src_lines = src_lines[skip:]
        key = (lang, self._code_theme, skip)
        if key != self._code_preview_key:
            self._code_preview_key = key
            self._code_preview_src = []
            self._code_preview_rows = []
        cached_src = self._code_preview_src
        rows = self._code_preview_rows
        # 最长公共前缀复用：流式只追加 → 仅渲染新增行；行内（未换行）字符
        # 增长 → 仅重渲变化的那一行。修复前按整段 split 结果做「前缀完全
        # 相等」比较，未换行的活动行每增长一个字符即判为「内容分歧」→ 整段
        # 重置重渲染（实测 800 行代码 token 级流式渲染 >100s，与按整行喂入
        # 相差 260 倍）。内容真正分歧（非前缀，如块切换/语言变化/边界修正）
        # 时仍整体重置。
        m = min(len(src_lines), len(cached_src))
        common = 0
        while common < m and src_lines[common] == cached_src[common]:
            common += 1
        if common < len(cached_src) - 1:
            del rows[:]
            base = 0
        else:
            if common < len(rows):
                del rows[common:]
            base = len(rows)
        if len(src_lines) > base:
            rows.extend(
                _code.highlight_code_lines(
                    src_lines[base:], lang, self._code_theme,
                    start_index=skip + base + 1,
                )
            )
        self._code_preview_src = src_lines
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
        omitted = max(0, len(rows) - limit) + dropped
        out: list[AnsiLine] = []
        if not skip:
            # 打开围栏/标题已随首段提交时不重复
            if title:
                out.append(_code.render_title_line(title))
            out.append(_code.render_fence_line(lang))
        if omitted:
            out.append(_code.render_omitted_line(omitted))
        out.extend(rows[-limit:] if omitted else rows)
        if closed:
            out.append(_code.render_close_fence_line())
        return out

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
            self._reset_code_preview_cache()

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
