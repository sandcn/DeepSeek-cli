"""RenderEngine — 消费 Token 流，生成 Rich renderable 输出。

职责：
1. 消费 RecursiveDescentParser 产出的 Token
2. 处理内联 Markdown 格式（bold/italic/code/links/…）
3. Emoji 短代码 → Unicode 替换
4. LaTeX 数学公式 → Unicode 近似
5. 代码块 → Rich Syntax 逐行语法高亮
6. 表格 → Rich Table 整块渲染
7. 委托 OutputAdapter 进行终端输出

与旧实现的差异：所有输出统一走 Rich Console，消除 print/ANSI 混合路径。
"""

from __future__ import annotations

import logging
from rich.text import Text

logger = logging.getLogger(__name__)
from rich.style import Style
from rich.syntax import Syntax

from .output import OutputAdapter
from .types import Token, TokenType, RenderContext
from .inline_renderer import InlineRenderer
from ._utils import get_code_style, parse_highlight_lines
from .protocols import RenderEngineAPI
from .handlers import HandlerRegistry, InlineHandler, CodeHandler, MathHandler, \
    MermaidHandler, DetailsHandler, AdmonitionHandler, HtmlBlockHandler, TableHandler, \
    FencedDivHandler

from .states import _CodeBlockState, _DetailsState, _TodoState
from ._rendering import render_todo_progress_bar as _render_todo_progress_bar
from ._rendering import render_toc, get_lexer as _shared_get_lexer


# ═══════════════════════════════════════════════════════════
# LaTeX 环境结构检测 & 数学块样式
# ═══════════════════════════════════════════════════════════

# 行尾的 \\ 换行符（仅用于关闭时清洗源码）



# ═══════════════════════════════════════════════════════════
# RenderEngine
# ═══════════════════════════════════════════════════════════

class RenderEngine:
    """消费 Token 流，渲染为 Rich 输出。

    使用方式：
      engine = RenderEngine(output_adapter)
      for token in tokens:
          engine.render(token)
    """

    def __init__(self, output: OutputAdapter, ctx: RenderContext | None = None,
                 code_theme: str = "monokai", typing_speed: int = 1000):
        self._output = output
        self._ctx = ctx if ctx is not None else RenderContext()
        self._code_theme = code_theme
        self._math_renderer: MathRenderer | None = None
        self._mermaid_renderer: MermaidRenderer | None = None
        self._typing_speed = typing_speed
        # 代码高亮缓存（lazy init）
        self._theme = None
        # 代码块/Details/Todo 状态（dataclass 封装）
        self._code = _CodeBlockState()
        self._details = _DetailsState()
        self._todo = _TodoState()
        self._todo_emitted: bool = False
        self._last_todo_progress: str | None = None  # 去重缓存

        # 引用块深度跟踪（供 InlineHandler 使用）
        self._bq_depth: int = 0

        # Mermaid 代码行缓冲区
        self._mermaid_buffer: list[str] = []

        # 内联格式渲染器（委托给共享的 InlineRenderer）
        self._inline_renderer = InlineRenderer()

        # Handler 注册表 — 所有 handler 通过独立模块注册
        self._handler_registry = HandlerRegistry()
        for handler_cls in [
            InlineHandler, CodeHandler, MathHandler,
            MermaidHandler, DetailsHandler, AdmonitionHandler,
            HtmlBlockHandler, TableHandler, FencedDivHandler,
        ]:
            self._handler_registry.register(handler_cls())

        # 块级元素间距跟踪
        self._prev_token_type: TokenType | None = None
        self._has_output = False

    # 需要在块级元素前插入空行的 TokenType 集合
    _BLOCK_START_TYPES: frozenset[TokenType] = frozenset({
        TokenType.PARAGRAPH, TokenType.HEADING, TokenType.HR, TokenType.CODE_FENCE_OPEN,
        TokenType.CODE_BLOCK, TokenType.BLOCKQUOTE_OPEN,
        TokenType.LIST_ITEM, TokenType.DEFINITION_ITEM, TokenType.TABLE,
        TokenType.MATH_BLOCK_OPEN,
        TokenType.MERMAID_BLOCK_OPEN, TokenType.DETAILS_OPEN,
        TokenType.ADMONITION_OPEN, TokenType.HTML_BLOCK_OPEN,
        TokenType.FENCED_DIV_OPEN,
    })

    # 作为"前一个元素"时需要在后面跟空行的类型（块级结束类型）
    _BLOCK_END_TYPES: frozenset[TokenType] = frozenset({
        TokenType.PARAGRAPH, TokenType.HEADING, TokenType.HR,
        TokenType.CODE_FENCE_CLOSE, TokenType.CODE_BLOCK,
        TokenType.BLOCKQUOTE_CLOSE, TokenType.LIST_ITEM,
        TokenType.DEFINITION_ITEM, TokenType.TABLE,
        TokenType.MATH_BLOCK_CLOSE, TokenType.MERMAID_BLOCK_CLOSE,
        TokenType.DETAILS_CLOSE, TokenType.ADMONITION_CLOSE,
        TokenType.HTML_BLOCK_CLOSE,
        TokenType.FENCED_DIV_CLOSE,
    })

    # 代码块关闭后需要空行的后续元素类型（frozenset 常量，避免每次创建 tuple）
    _CODE_AFTER_BLOCK_FOLLOW: frozenset[TokenType] = frozenset({
        TokenType.PARAGRAPH, TokenType.LIST_ITEM,
        TokenType.BLOCKQUOTE_OPEN, TokenType.HEADING,
        TokenType.TABLE, TokenType.HTML_BLOCK_OPEN,
    })

    # 代码块关闭类型（只需 frozenset 检查一次）
    _CODE_BLOCK_DONE_TYPES: frozenset[TokenType] = frozenset({
        TokenType.CODE_FENCE_CLOSE, TokenType.CODE_BLOCK,
    })

    # ═══════════════════════════════════════════════════════
    # RenderEngineAPI 协议实现（供 Handler 使用）
    # ═══════════════════════════════════════════════════════

    @property
    def typing_speed(self) -> int:
        return self._typing_speed

    @property
    def output_width(self) -> int:
        return self._output.width

    def get_lexer(self, lang: str) -> object:
        return self._get_lexer(lang)

    def ensure_theme(self) -> None:
        self._ensure_theme()

    def code_typing_speed(self) -> int:
        return self._typing_speed

    def get_highlight_lines(self, attrs: str) -> list[int]:
        return parse_highlight_lines(attrs)

    def write(self, renderable) -> None:
        self._output.write(renderable)

    def write_line(self, text: str = "") -> None:
        self._output.write_line(text)

    def write_typing(self, text: Text, speed: int, end: str = "\n",
                     fill_style: Style | None = None) -> None:
        self._output.write_typing(text, speed, end, fill_style)

    def write_raw(self, text: str) -> None:
        """快速输出纯文本。"""
        self._output.write_raw(text)

    def print(self, *args, **kwargs) -> None:
        """打印整块 renderable。"""
        self._output.print(*args, **kwargs)

    # ── RenderEngineAPI 扩展方法（供 Handler 通过协议调用） ──

    def render_inline(self, text: str) -> Text:
        """渲染内联 Markdown 格式为 Rich Text（RenderEngineAPI 协议）。"""
        return self._render_inline(text)

    def output_assembled(self, assembled: Text) -> None:
        """统一输出 assembled Text，打字机或即时（RenderEngineAPI 协议）。"""
        self._output_assembled(assembled)

    def emit_todo_progress(self) -> None:
        """如果存在 Todo 统计，输出进度条并重置（RenderEngineAPI 协议）。"""
        self._emit_todo_progress()

    # ── 状态访问器（给 Handler 使用，替代直接读写 engine._xxx） ──

    @property
    def code_state(self) -> _CodeBlockState:
        """代码块渲染状态。"""
        return self._code

    @property
    def details_state(self) -> _DetailsState:
        return self._details

    @property
    def todo_state(self) -> _TodoState:
        return self._todo

    @todo_state.setter
    def todo_state(self, value: _TodoState) -> None:
        self._todo = value
        self._last_todo_progress = None  # ★ 重置去重缓存，新列表不受旧缓存影响

    @property
    def mermaid_buffer(self) -> list[str]:
        return self._mermaid_buffer

    @mermaid_buffer.setter
    def mermaid_buffer(self, value: list[str]) -> None:
        self._mermaid_buffer = value

    @property
    def bq_depth(self) -> int:
        """blockquote 深度。"""
        return self._bq_depth

    @bq_depth.setter
    def bq_depth(self, value: int) -> None:
        self._bq_depth = value

    @property
    def todo_emitted(self) -> bool:
        """todo 进度是否已标记。"""
        return self._todo_emitted

    @todo_emitted.setter
    def todo_emitted(self, value: bool) -> None:
        self._todo_emitted = value

    @property
    def render_context(self) -> RenderContext:
        return self._ctx

    def set_render_context(self, ctx: RenderContext) -> None:
        """设置渲染上下文（用于外部同步引用链接/脚注等状态）。"""
        self._ctx = ctx

    @property
    def math_renderer(self) -> "MathRenderer":
        if self._math_renderer is None:
            from .math_renderer import MathRenderer
            self._math_renderer = MathRenderer()
        return self._math_renderer

    @property
    def mermaid_renderer(self) -> "MermaidRenderer":
        if self._mermaid_renderer is None:
            from .mermaid_renderer import MermaidRenderer
            self._mermaid_renderer = MermaidRenderer()
        return self._mermaid_renderer

    @property
    def code_theme(self) -> str:
        return self._code_theme

    @property
    def theme(self):
        self._ensure_theme()
        return self._theme

    # ── 核心调度 ────────────────────────────────────────

    def render(self, token: Token) -> None:
        """渲染单个 Token（通过 HandlerRegistry 调度）。"""
        try:
            self._insert_block_spacing(token)

            # ── 任务列表进度提前刷出 ────────────────────────
            # ★ 修复：在 handler.handle() 之前刷出 todo 进度条，
            #   防止 EMPTY_LINE handler 重置 todo_state 后进度丢失。
            #   而对于 LIST_ITEM，列表仍在继续，无需提前刷出。
            if token.type is not TokenType.LIST_ITEM:
                self._emit_todo_progress()

            handler = self._handler_registry.get(token.type)
            if handler:
                handler.handle(token, self)

            # TOC_MARKER 占位符处理
            if token.type is TokenType.TOC_MARKER:
                self.emit_toc()

            # ── Token 计数统计 ──────────────────────────────
            self._ctx.token_count += 1

            self._prev_token_type = token.type
            self._has_output = True
        except Exception:
            if logger.isEnabledFor(logging.DEBUG):
                raise
            logger.warning("Token渲染异常，跳过: %s", token.type, exc_info=True)
            self._prev_token_type = token.type  # 保留真实 token.type，避免 PARAGRAPH 回退误导后续间距计算
            self._has_output = True  # ★ 异常路径也要更新，防止后续 _insert_block_spacing 被跳过

    # ═══════════════════════════════════════════════════════

    def _output_assembled(self, assembled: Text):
        """统一输出 assembled Text（打字机或即时）。"""
        try:
            if self._typing_speed > 0:
                self._output.write_typing(assembled, self._typing_speed)
            else:
                self._output.write(assembled)
        except Exception:
            logger.warning("assembled输出异常", exc_info=True)

    def _insert_block_spacing(self, token: Token) -> None:
        """在块级元素切换时自动插入空行分隔。"""
        try:
            if not self._has_output:
                return
            prev = self._prev_token_type
            curr = token.type

            # 连续空行不额外插入
            if curr is TokenType.EMPTY_LINE:
                return

            # 连续 LIST_ITEM 不插入空行（列表项应紧凑排列）
            if prev is TokenType.LIST_ITEM and curr is TokenType.LIST_ITEM:
                return

            code_spacing_inserted = False  # 避免双空行标志

            # 代码块关闭后紧跟段落/列表/引用：插入空行分隔
            if prev in self._CODE_BLOCK_DONE_TYPES:
                if curr in self._CODE_AFTER_BLOCK_FOLLOW:
                    self._output.write_line()
                    code_spacing_inserted = True

            # 块级元素开始：前一个是段落/块结束时插入空行
            if not code_spacing_inserted and curr in self._BLOCK_START_TYPES and prev in self._BLOCK_END_TYPES:
                self._output.write_line()
        except Exception:
            logger.warning("块间距计算异常", exc_info=True)

    def _reset_todo_state(self) -> None:
        """重置 todo 状态，确保下一批 todo 列表能正常输出。"""
        self._todo.total = 0
        self._todo.done = 0
        self._todo.active = False
        self._last_todo_progress = None
        self._todo_emitted = False

    def _emit_todo_progress(self) -> None:
        """在流式场景中实时输出 Todo 进度条，并在全部完成时输出最终统计。

        该方法在渲染 Todo 列表时被多次调用：
        - 流式渲染中每次遇到非列表项元素时触发，输出当前进度条
        - 全部完成时输出最终统计
        """
        try:
            if not self._todo.active or self._todo.total <= 0:
                return

            progress = _render_todo_progress_bar(self._todo.done, self._todo.total)

            # ★ 去重：内容相同不重复输出
            progress_plain = progress.plain if hasattr(progress, 'plain') else str(progress)
            if self._last_todo_progress == progress_plain:
                return
            self._last_todo_progress = progress_plain

            # ★ 已完成 todo 列表用 _todo_emitted 防止重复输出最终进度
            if self._todo.done == self._todo.total:
                if self._todo_emitted:
                    return
                self._todo_emitted = True

            self._output.write(progress)
            self._reset_todo_state()
        except Exception:
            logger.debug("todo进度渲染异常", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # TOC / 目录输出 — 委托给共享 render_toc()
    # ═══════════════════════════════════════════════════════

    def emit_toc(self) -> None:
        """输出 Table of Contents（如果 ctx.toc 存在且有内容）。"""
        try:
            toc = getattr(self._ctx, 'toc', None)
            if not toc:
                return
            self._output.write_line()  # 前间距
            self._output.write(render_toc(toc, self._output.width))
            self._output.write_line()  # 后间距
        except Exception:
            logger.debug("TOC 渲染异常", exc_info=True)

    # ═══════════════════════════════════════════════════════
    # 告示 / Admonitions
    # ═══════════════════════════════════════════════════════

    def render_footnotes(self) -> list[Text]:
        """渲染所有已收集的脚注定义（公开方法，供外部调用）。

        功能6：每条脚注末尾追加 ↩ 返回链接符号。
        """
        try:
            if not self._ctx.fn_map:
                return []
            result = Text()
            result.append(f"\n{'─' * self._output.width}\n", style=Style(dim=True))
            # 按首次引用顺序排序（fn_order 记录引用先后），
            # 未引用到的脚注（仅有定义无引用）按字母顺序排末尾。
            ordered_refs = list(self._ctx.fn_order)
            remaining = sorted(set(self._ctx.fn_map.keys()) - set(self._ctx.fn_order))
            ordered_refs.extend(remaining)
            for i, ref_id in enumerate(ordered_refs, 1):
                content = self._ctx.fn_map.get(ref_id)
                if content is None:
                    continue
                result.append(f"  [{i}] ", style=Style(color="bright_cyan"))
                result.append_text(self._render_inline(content))
                # ── 功能6：脚注返回链接 ───────────────────────
                result.append(" ↩", style=Style(color="bright_cyan", dim=True))
                result.append("\n")
            return [result]
        except Exception:
            logger.debug("脚注渲染异常", exc_info=True)
            return []

    def _ensure_theme(self):
        """懒加载 Pygments 主题。"""
        if self._theme is None:
            self._theme = Syntax.get_theme(get_code_style(self._code_theme))

    def _get_lexer(self, lang: str):
        """获取/缓存 Pygments 词法分析器（委托给共享 _rendering.get_lexer）。"""
        return _shared_get_lexer(lang)

    def _render_inline(self, text: str) -> Text:
        """渲染内联 Markdown 格式为 Rich Text（委托给共享 InlineRenderer）。"""
        try:
            return self._inline_renderer.render(text, self._ctx)
        except Exception:
            logger.debug("内联渲染异常，降级: %s", text[:50], exc_info=True)
            return Text(text, style=Style(dim=True, italic=True))

