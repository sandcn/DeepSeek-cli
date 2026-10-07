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
from .style import Style
from .inline import render_inline, use_render_context
from .engine import AnsiRenderEngine
from ._preview_cache import LinePreviewCache
from .table import TablePreviewCache
from src.renderer.types import TokenType, Token

#: 预览单行字符上限——活动行（每次 write 变化的未换行行）超过该长度时只渲染
#: 尾部窗口，把单帧渲染成本封顶（避免超长无换行行逐字符增长导致的累计 O(n²)）。
#: 历史行经 ``LinePreviewCache`` 前缀复用只渲染一次，不受此影响。
_PREVIEW_MAX_LINE_CHARS = 4096

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
        self._engine = AnsiRenderEngine(code_theme=code_theme, width=width, ctx=self._ctx)
        self._code_theme = code_theme
        # ★ 流式预览：未闭合块（段落/代码块/表格/引用等）每次 write 后整块
        #   重渲染为预览行。使用**独立引擎实例**——预览渲染不污染主引擎的
        #   流式缓冲状态（引用/告示/代码等的 OPEN-LINE 缓冲）。
        self._preview_engine = AnsiRenderEngine(code_theme=code_theme, width=width, ctx=self._ctx)
        self._preview_lines: list[AnsiLine] = []
        # ★ 代码块预览增量高亮缓存（键 = (lang, theme/skip)；流式只追加时仅
        #   渲染新增行，显示侧再按 _PREVIEW_MAX_LINES 截断并给出省略提示）。
        self._code_preview_key: tuple | None = None
        self._code_preview_rows: list[AnsiLine] = []
        # 代码块预览的增量 split 缓存：上次完整 content + 完整行列表——
        # 流式只追加时只 split 新增片段（避免每帧 O(全文) split/比较）。
        self._code_preview_content: str = ""
        self._code_preview_full_src: list[str] = []
        # ★ 表格预览的行级增量缓存（列宽 + 行渲染复用）。
        self._table_preview_cache = TablePreviewCache()
        # ★ 段落/引用/告示预览的行级增量缓存（对齐代码块按行缓存策略）：
        #   未变化的历史行渲染结果跨帧复用，仅渲染新增/变化的行。按块类型
        #   分实例（同一帧预览可能同时含段落与引用 token，共用实例会互相
        #   重置缓存）。
        self._line_preview_caches: dict = {}
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
        with use_render_context(self._ctx):
            return list(render_toc(toc, self._width))

    def _refresh_preview(self) -> None:
        """刷新未闭合块预览行（独立引擎，互不污染）。

        解析器 ``peek_pending`` 返回当前未闭合状态的自包含 Token 序列；渲染为
        ``_preview_lines`` 供 UI 整体替换（块闭合后由 committed 行替换，预览清空）。

        ★ 增量渲染：代码块走 ``_render_code_preview``（按行高亮缓存）；段落/
        引用/告示走 ``LinePreviewCache``（按行前缀复用）——均不整块重渲染，
        消除长块流式期间每帧 O(预览行数) 的重复开销。其余 token 由独立预览
        引擎渲染（引擎状态隔离，不污染主引擎）。
        """
        with use_render_context(self._ctx):
            self._refresh_preview_impl()

    def _refresh_preview_impl(self) -> None:
        try:
            ptokens = self._parser.peek_pending()
        except Exception:
            self._clear_preview()
            return
        if not ptokens:
            self._clear_preview()
            return
        eng = self._preview_engine
        eng.reset()
        # 常见路径：单一预览 token → 直接采用其输出列表（免一次 O(行数) 复制）
        if len(ptokens) == 1:
            self._preview_lines = self._render_preview_token(ptokens[0], eng)
            return
        lines: list[AnsiLine] = []
        for tok in ptokens:
            lines.extend(self._render_preview_token(tok, eng))
        self._preview_lines = lines

    def _render_preview_token(self, tok, eng) -> list[AnsiLine]:
        """渲染单个预览 token（增量分派；非增量类型走预览引擎）。"""
        if tok.meta.get("preview"):
            t = tok.type
            if t is TokenType.CODE_BLOCK:
                return self._render_code_preview(tok)
            if t is TokenType.TABLE:
                return self._render_table_preview(tok)
            if t is TokenType.PARAGRAPH:
                return self._render_paragraph_preview(tok)
            if t is TokenType.BLOCKQUOTE_LINE:
                return self._render_blockquote_preview(tok)
            if t is TokenType.ADMONITION_CLOSE:
                return self._render_admonition_preview(tok)
        return eng.render(tok)

    def _clear_preview(self) -> None:
        """清空预览行与所有增量缓存（块闭合/预览为空时）。"""
        self._preview_lines = []
        self._reset_code_preview_cache()
        self._table_preview_cache.reset()
        for cache in self._line_preview_caches.values():
            cache.reset()

    def _line_cache(self, kind: str) -> LinePreviewCache:
        """取（或建）指定块类型的行级预览缓存（key 固定，不累积）。"""
        cache = self._line_preview_caches.get(kind)
        if cache is None:
            cache = LinePreviewCache()
            self._line_preview_caches[kind] = cache
        return cache

    @staticmethod
    def _window_preview_line(line: str) -> str:
        """活动行尾部窗口化（超长时只保留尾部，封顶单帧渲染成本）。"""
        limit = _PREVIEW_MAX_LINE_CHARS
        if limit > 0 and len(line) > limit:
            return line[-limit:]
        return line

    def _preview_src_lines(self, content: str) -> list[str]:
        """预览源行：仅对**活动行**（最后一行）做尾部窗口化。

        历史行经 ``LinePreviewCache`` 前缀复用只渲染一次，无需窗口；活动行
        每次 write 变化，窗口化把单帧成本封顶（超长无换行行不再逐字符重解析）。

        ★ 超长内容 O(窗口) 化：修复前对整段 ``content.split("\\n")``——每帧
        复制整段（超长活动行 200k 字符时单帧 ~0.06ms、且随行增长线性上升）。
        现在只在**尾部窗口**内定位换行，历史行部分按需切分：单行超长内容
        直接返回窗口切片（O(窗口)），不再全文复制。
        """
        if not content:
            return []
        limit = _PREVIEW_MAX_LINE_CHARS
        if limit <= 0 or len(content) <= limit:
            # 短内容：整体切分（最后一行本来就 <= 窗口上限）
            return content.split("\n")
        # 超长内容：窗口内定位最后一个换行，窗口之前的内容按需切分
        window = content[-limit:]
        nl = window.rfind("\n")
        if nl < 0:
            # 窗口内无换行 → 最后一行长度超过窗口上限，窗口即活动行尾部
            # （无分配定位更早的换行，确认是否存在历史行）
            prev_nl = content.rfind("\n", 0, len(content) - limit)
            if prev_nl < 0:
                return [window]
            parts = content[:prev_nl].split("\n")
            parts.append(window)
            return parts
        head = content[:len(content) - limit + nl]
        parts = head.split("\n")
        parts.append(window[nl + 1:])
        return parts

    def _render_paragraph_preview(self, token) -> list[AnsiLine]:
        from . import blocks as _blocks
        src = self._preview_src_lines(token.content or "")
        if self._paragraph_needs_multiline_inline(src):
            # ★ 一致性修复（跨软换行行内标记）：多行段落在流式预览期按**整段**
            #   解析行内标记后拆行——逐行渲染无法配对跨行标记（``**粗体\n
            #   跨行**`` 会泄漏 ``**`` 标记），此路径与提交路径
            #   （``render_paragraph`` 整段解析）语义一致，消除「预览泄漏 →
            #   提交配对」的视觉跳变。
            rows = _blocks.render_paragraph(
                Token(TokenType.PARAGRAPH, "\n".join(src))
            )
        else:
            rows = self._line_cache("paragraph").render(
                ("paragraph",), src,
                lambda text: [_blocks.render_paragraph_line(text)],
            )
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        if dropped:
            return [self._omitted_line(dropped)] + rows
        return rows

    #: 行内标记起始字符——多行段落含任一即整段解析（跨行标记可能配对）
    #: 行内标记起始字符——多行段落含任一即整段解析（跨行标记可能配对）。
    #: 覆盖全部可跨软换行配对的定界符（含新增语法：高亮/上下标/下划线/
    #: 剧透/数学/着色容器/行内注释）；不含 emoji/URL/邮箱等不会跨行的触发符。
    _INLINE_MARKER_CHARS = ("*", "_", "`", "~", "[", "<", "=", "^", "+", "|", "$", "{", "%")

    @classmethod
    def _paragraph_needs_multiline_inline(cls, src_lines: list) -> bool:
        """多行段落是否含可能跨软换行的行内标记（含则需整段解析）。"""
        if len(src_lines) < 2:
            return False
        text = "\n".join(src_lines)
        for ch in cls._INLINE_MARKER_CHARS:
            if ch in text:
                return True
        return False

    @staticmethod
    def _omitted_line(dropped: int) -> AnsiLine:
        """预览截断提示行（与代码块预览同一真源）。"""
        from .code import render_omitted_line
        return render_omitted_line(dropped)

    def _render_blockquote_preview(self, token) -> list[AnsiLine]:
        from . import blocks as _blocks
        depth = max(1, int(token.meta.get("depth", 1))) - 1
        src = self._preview_src_lines(token.content or "")
        rows = self._line_cache("blockquote").render(
            ("blockquote", depth), src,
            lambda text: [_blocks.render_blockquote_line(text, depth)],
        )
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        if dropped:
            return [self._omitted_line(dropped)] + rows
        return rows

    def _render_admonition_preview(self, token) -> list[AnsiLine]:
        from . import blocks as _blocks
        atype = str(token.meta.get("type", "NOTE")).upper()
        body = list(token.meta.get("body_lines") or [])
        if token.content:
            body = str(token.content).split("\n") + body
        if not body:
            return []
        head = _blocks.render_admonition_head(atype, body[0])
        rest = [self._window_preview_line(seg) for seg in body[1:]]
        rows = self._line_cache("admonition").render(
            ("admonition", atype), rest,
            lambda text: [_blocks.render_admonition_body(text)],
        )
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        if dropped:
            return [head, self._omitted_line(dropped)] + rows
        return [head] + rows

    def _render_table_preview(self, token) -> list[AnsiLine]:
        """表格预览：列宽与行渲染的行级增量（``TablePreviewCache``）。

        未闭合表格每次 write 都整表重渲染（列宽对每单元格做行内解析 +
        每行重排）；缓存后列宽不变时只渲染新增行、历史行对象复用
        （UI 侧 styled 缓存因此可命中）。
        """
        rows = token.meta.get("rows") or []
        aligns = token.meta.get("alignments") or []
        # ★ 契约显式化：缓存键用表头元组区分表格实例——修复前恒传 ``()``，
        #   键实际失效、完全依赖内部 ``_header`` 判断（隐性契约，新增字段/
        #   复用实例时易误复用）。表头相同即同一表格的连续预览（键稳定）。
        key = tuple(rows[0]) if rows else ()
        return self._table_preview_cache.render(key, rows, aligns, self._width)

    def _reset_code_preview_cache(self) -> None:
        """清空代码块预览增量缓存（块闭合/预览清空时调用）。"""
        self._code_preview_key = None
        self._code_preview_rows = []
        self._code_preview_content = ""
        self._code_preview_full_src = []

    def _code_lines_from_content(self, src: str) -> tuple[list[str], bool]:
        """兼容路径：从 ``content`` 字符串增量 split 出完整行列表。

        返回 ``(full_lines, reset_rows)``——``reset_rows=True`` 表示本次内容与
        缓存无前缀关系（首次 / 分歧），调用方需重置高亮缓存。

        缓存上次 content 与行列表：新 content 以旧为前缀时只 split 增量片段并
        就地扩展（避免每帧 O(全文) split）。解析器预览 token 走 ``meta["lines"]``
        （不经此路径）；此路径服务测试 / 旧接口。
        """
        if not src:
            self._code_preview_content = ""
            self._code_preview_full_src = []
            return [], True
        old_content = self._code_preview_content
        if old_content and src.startswith(old_content):
            delta = src[len(old_content):]
            full_lines = self._code_preview_full_src
            if delta:
                parts = delta.split("\n")
                if full_lines:
                    full_lines[-1] = full_lines[-1] + parts[0]
                    if len(parts) > 1:
                        full_lines.extend(parts[1:])
                else:
                    full_lines = parts
                    self._code_preview_full_src = full_lines
            self._code_preview_content = src
            return full_lines, False
        full_lines = src.split("\n")
        self._code_preview_content = src
        self._code_preview_full_src = full_lines
        return full_lines, True

    def _render_code_preview(self, token) -> list[AnsiLine]:
        """代码块流式预览：按行增量高亮缓存 + 尾部截断省略提示。

        逐行高亮对「只追加」的流式输入可安全缓存（``highlight_code_lines``
        本身逐行处理、无跨行状态），仅渲染新增行——修复前每次 write 对整段
        预览（最多 ``_PREVIEW_MAX_LINES`` 行）重新词法高亮，600 行代码流式
        输出实测约 27s，渲染线程在 30Hz 下近乎满载，进而造成命令队列背压。
        """
        from . import code as _code
        from .._block_parser import RegexFreeBlockParser

        lang = token.meta.get("lang", "")
        title = token.meta.get("title", "")
        closed = bool(token.meta.get("closed", True))
        dropped = int(token.meta.get("preview_dropped", 0) or 0)

        # 解析器预览 token 直接携带行列表（``meta["lines"]``，尾部活动行为空
        # 时零拷贝复用内部缓冲）——免除每帧 ``"\n".join`` + 渲染层 ``split``
        # 的 O(全文) 往返；仅兼容路径（测试 / 旧接口）才从 ``content`` 增量 split。
        provided = token.meta.get("lines")
        if provided is not None:
            full_lines = provided
            reset_rows = False
        else:
            full_lines, reset_rows = self._code_lines_from_content(token.content or "")

        # 超长代码块的分段提交：已提交的前 skip 行由 committed 显示，预览只
        # 呈现尚未提交的尾部（否则同一批行在 committed 与 preview 中重复显示）。
        skip = min(self._committed_code_lines, len(full_lines))
        src_lines = full_lines[skip:] if skip else full_lines
        key = (lang, self._code_theme, skip, dropped)
        if reset_rows or key != self._code_preview_key:
            self._code_preview_key = key
            self._code_preview_rows = []
        rows = self._code_preview_rows
        # 增量高亮：行列表前缀稳定（解析器只追加；drop / 内容分歧已重置缓存）
        # → 只高亮 ``rows`` 未覆盖的尾部行（O(新增行)）。
        n = len(src_lines)
        if n < len(rows):
            del rows[n:]
        if n > len(rows):
            rows.extend(
                _code.highlight_code_lines(
                    src_lines[len(rows):], lang, self._code_theme,
                    start_index=skip + len(rows) + 1,
                )
            )
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

        ★ 不再复制列表：调用方（apply/model）本就按替换语义复制（``list(...)``）
        ——避免每帧对整段预览行多一次 O(行数) 复制。**调用方不得原地修改**
        返回列表。
        """
        return self._sanitize_lines(self._preview_lines)

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
            # ★ 文档尾附录：脚注定义列表 + 参考式链接列表（与 Rich 路径同源语义）。
            #   仅当文档实际定义过脚注 / 参考链接时才输出（无定义零额外行）。
            self._lines.extend(self._render_footnotes())
            self._lines.extend(self._render_ref_links())
        finally:
            self._engine.reset()
            self._clear_preview()

    # ── 文末附录（脚注 / 参考链接） ───────────────────

    def _render_footnotes(self) -> list[AnsiLine]:
        """渲染脚注定义列表（按引用顺序，未引用者按字母序排末尾）。"""
        fn_map = getattr(self._ctx, "fn_map", None)
        if not fn_map:
            return []
        fn_order = list(getattr(self._ctx, "fn_order", ()) or ())
        ordered = [r for r in fn_order if r in fn_map]
        ordered.extend(sorted(set(fn_map.keys()) - set(ordered)))
        lines: list[AnsiLine] = [
            AnsiLine.of("\u2500" * max(1, self._width), Style(fg=240)),
        ]
        for i, ref_id in enumerate(ordered, 1):
            content = fn_map.get(ref_id)
            if content is None:
                continue
            line = AnsiLine.of(f"  [{i}] ", Style(fg=45))
            for run in render_inline(content, ctx=self._ctx):
                line.append_run(run)
            line.append(" \u21a9", Style(fg=45, dim=True))
            lines.append(line)
        return lines

    def _render_ref_links(self) -> list[AnsiLine]:
        """渲染参考式链接定义列表（``[id]: url "title"``）。"""
        ref_map = getattr(self._ctx, "ref_map", None)
        if not ref_map:
            return []
        lines: list[AnsiLine] = [
            AnsiLine.of(""),
            AnsiLine.of("\U0001f517 \u5f15\u7528\u94fe\u63a5", Style(fg=45, bold=True)),
            AnsiLine.of("\u2500" * max(1, self._width), Style(fg=240)),
        ]
        for ref_id, pair in sorted(ref_map.items()):
            try:
                url, title = pair
            except (TypeError, ValueError):
                url, title = pair, ""
            line = AnsiLine.of(f"  [{ref_id}] {url}", Style(fg=45, underline=True))
            if title:
                line.append(f' "{title}"', Style(fg=240, italic=True))
            lines.append(line)
        return lines

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
        """ANSI 消毒：合法 SGR → Run 样式（保留颜色），其余控制序列移除。

        ★ 行级缓存（``AnsiLine._esc_checked``）：预览/已渲染行跨帧复用同一
        AnsiLine 对象，已确认无转义序列的行直接跳过扫描——避免每帧对全部行
        重扫（大预览下 ``_has_esc`` 曾占预览刷新耗时一半以上）。
        """
        def _has_esc(runs) -> bool:
            return any(
                "\x1b" in (r.text or "") or "\x07" in (r.text or "")
                for r in runs
            )

        # 定位首个未检查行（已检查行恒为前缀——预览行按前缀复用）
        idx = 0
        n = len(lines)
        while idx < n and getattr(lines[idx], "_esc_checked", False):
            idx += 1
        if idx == n:
            return lines

        # 只扫描未检查的尾部：全部干净则打标记并零构建返回原 list
        dirty = False
        for i in range(idx, n):
            line = lines[i]
            if getattr(line, "_esc_checked", False):
                continue
            if _has_esc(line.runs):
                dirty = True
                break
            line._esc_checked = True
        if not dirty:
            return lines

        # 有原始转义序列（罕见）→ 全量消毒
        from .helpers import ansi_to_runs, strip_ansi
        out: list[AnsiLine] = []
        for line in lines:
            if getattr(line, "_esc_checked", False):
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
            new_line._esc_checked = True
            out.append(new_line)
        return out

    @property
    def lines(self) -> list[AnsiLine]:
        """当前已渲染行（不消费）。"""
        return self._lines

    @property
    def is_closed(self) -> bool:
        return self._closed
