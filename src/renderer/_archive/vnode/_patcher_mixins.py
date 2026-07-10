"""VNodePatcher handler mixins — 按逻辑分组的核心 Handler 实现。

将 VNodePatcher 中所有 _handle_* 和 _render_* 方法提取到以下 Mixin 类：
  - _PatchDispatchMixin: 补丁调度（INSERT/UPDATE/DELETE/REORDER）
  - _RenderHandlersMixin: 渲染 handler（按 VNodeType 组织）

这些 Mixin 依赖宿主类 VNodePatcher 提供以下属性/方法：
  - self._output: OutputAdapter
  - self._typing_speed: int
  - self._code_theme: str
  - self._math_renderer: MathRenderer
  - self._mermaid_renderer: MermaidRenderer
  - self._inline_engine: ASTRenderer（用于内联渲染）
  - self._rendered_cache: dict[str, bool]
  - self._output_assembled(Text): 统一输出方法
  - self._write_vnode(renderable): 输出 renderable

★ 本模块修复索引：
  Bug1: _render_code_line lexer=None 崩溃 → 前置判空降级
  Bug2: _render_code_line theme 未缓存 → 共享缓存
  Bug3: _handle_update 只处理 CODE_LINE → 扩展支持 PARAGRAPH/HEADING 等内容追加
  Bug4: _render_heading 多余空行 → 2 newlines 改为 1 blank line
  Bug5: _render_paragraph 缺少段落间距 → 追加 blank line
  Bug6: _render_code_line 空行不输出 → 显式输出空白行
  Bug7: _render_mermaid 缺异常保护 → try-except 兜底
"""

from __future__ import annotations

import logging
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

from ..._rendering import (
    is_todo,
    render_code_title_bar,
    render_code_fence_open,
    render_code_fence_close,
    render_html_block_open,
    render_html_block_close,
    highlight_line,
    style_heading,
    build_rich_table,
    render_blockquote_prefix,
    render_code_block_syntax,
    BULLET_SYMBOLS,
    render_heading as _render_heading_shared,
    render_blockquote as _render_blockquote_shared,
    render_list_item as _render_list_item_shared,
    render_definition_item as _render_definition_item_shared,
    render_todo_progress_bar as _render_todo_progress_bar_shared,
    render_details_header as _render_details_header_shared,
    render_details_footer as _render_details_footer_shared,
    render_admonition_header as _render_admonition_header_shared,
    render_mermaid_block as _render_mermaid_block_shared,
    render_mermaid_close as _render_mermaid_close_shared,
    render_hr as _render_hr_shared,
)

from .types import VNode, VNodeType, VPatch, PatchType


logger = logging.getLogger(__name__)


class _PatchDispatchMixin:
    """补丁调度 handler 集合。"""

    # ── INSERT ────────────────────────────────────────────

    def _handle_insert(self, patch: VPatch,
                       rendered_vnodes: dict[str, VNode]) -> None:
        """插入新节点：渲染并输出到终端。"""
        node = patch.node
        if node is None:
            return
        if not self._should_render(node):
            rendered_vnodes[node.key] = node
            return
        self._render_vnode(node)
        self._rendered_cache[node.key] = True
        rendered_vnodes[node.key] = node

    # ── UPDATE ────────────────────────────────────────────

    # 【Bug3 修复】需要增量重渲染的文本节点类型（流式追加内容时覆盖旧输出）
    _UPDATE_TEXT_TYPES: frozenset = frozenset({
        VNodeType.PARAGRAPH, VNodeType.CODE_LINE, VNodeType.HEADING,
        VNodeType.LIST_ITEM, VNodeType.DEFINITION_ITEM, VNodeType.HTML_LINE,
    })

    def _handle_update(self, patch: VPatch,
                       rendered_vnodes: dict[str, VNode]) -> None:
        """更新节点内容：重新渲染并覆盖旧输出。

        ★ Bug3 修复：扩展支持 PARAGRAPH/HEADING 等非 CODE_LINE 节点。
        流式场景中段落内容递增（"Hello"→"Hello world"）时，
        VNodeDiffer 生成 UPDATE 补丁，_handle_update 必须让新内容覆盖旧内容，
        否则增量文本静默丢失，用户始终只能看到旧版本。

        策略：
          - CODE_LINE：clear_line + 重渲染（单行）
          - PARAGRAPH 等文本节点：如果旧内容是前缀 → 只输出新后缀（增量追加）；
            否则 clear_line + 重渲染
          - 其余节点类型：更新缓存（终端无法撤回已输出的块级内容）

        ★ Bug1 修复 (P0)：PARAGRAPH 纯追加路径末尾追加 write_line()，
        确保更新后的段落与下一节点之间有正确的空行间距。
        ★ Bug2 修复 (P1)：HEADING 增加纯追加优化，避免流式标题每次清行重渲染导致闪烁。
        """
        node = patch.node
        if node is None:
            return

        # 段落空行去重标记（mixin 无 __init__，首次使用时初始化）
        if not hasattr(self, '_updated_paragraph_keys'):
            self._updated_paragraph_keys: set[str] = set()

        if node.type in self._UPDATE_TEXT_TYPES:
            if node.type is VNodeType.CODE_LINE:
                self._output.clear_line()
                self._render_code_line(node)
            elif node.type is VNodeType.PARAGRAPH:
                old = patch.old_content
                new = patch.new_content
                if old and new.startswith(old):
                    # 纯追加优化：只输出新增部分，无需清行
                    suffix = new[len(old):]
                    if suffix:
                        t = self._inline_engine._render_inline(suffix)
                        if self._typing_speed > 0:
                            # 【Bug1 修复】write_typing 默认 end="\n" 会导致后缀换行
                            self._output.write_typing(t, self._typing_speed, end='')
                        else:
                            # 【Bug1 修复】write() → console.print() 自带 \n，后缀会换行
                            self._output.write_inline(t)
                        # ★ 段落尾空行去重：仅在有后缀输出时才加入空行
                        if patch.key not in self._updated_paragraph_keys:
                            self._output.write_line()
                            self._updated_paragraph_keys.add(patch.key)
                else:
                    # 非追加更新：清除旧段落所占全部终端行 + 重渲染
                    # ★ P0 修复：clear_line() 只清当前行，多行段落旧内容
                    #   残留在上方行。用 _clear_paragraph_lines 清除全部旧行。
                    self._clear_paragraph_lines(patch.key)
                    self._render_paragraph(node)
            elif node.type is VNodeType.HEADING:
                # 标题空行去重标记（mixin 无 __init__，首次使用时初始化）
                if not hasattr(self, '_updated_heading_keys'):
                    self._updated_heading_keys: set[str] = set()
                old = patch.old_content
                new = patch.new_content
                if old and new.startswith(old):
                    # 【Bug2 修复】纯追加优化：只输出新增后缀，避免闪烁
                    suffix = new[len(old):]
                    if suffix:
                        t = self._inline_engine._render_inline(suffix)
                        if self._typing_speed > 0:
                            # 【Bug2 修复】write_typing 默认 end="\n" 会导致后缀换行
                            self._output.write_typing(t, self._typing_speed, end='')
                        else:
                            # 【Bug2 修复】write() → console.print() 自带 \n，后缀会换行
                            self._output.write_inline(t)
                    # ★ 标题尾空行去重
                    if patch.key not in self._updated_heading_keys:
                        self._output.write_line()
                        self._updated_heading_keys.add(patch.key)
                else:
                    # 非追加更新：清当前行 + 重渲染
                    self._output.clear_line()
                    self._render_heading(node)
            elif node.type is VNodeType.LIST_ITEM:
                self._output.clear_line()
                self._render_list_item(node)
            elif node.type is VNodeType.DEFINITION_ITEM:
                self._output.clear_line()
                self._render_definition_item(node)
            elif node.type is VNodeType.HTML_LINE:
                self._output.clear_line()
                self._render_html_line(node)

        rendered_vnodes[node.key] = node

    # ── DELETE ────────────────────────────────────────────

    def _handle_delete(self, patch: VPatch,
                       rendered_vnodes: dict[str, VNode]) -> None:
        """删除节点：终端无法撤回已输出内容，仅从缓存中移除。"""
        rendered_vnodes.pop(patch.key, None)
        self._rendered_cache.pop(patch.key, None)

    # ── REORDER / MOVE ────────────────────────────────────

    def _handle_reorder(self, patch: VPatch,
                        rendered_vnodes: dict[str, VNode]) -> None:
        """重排/移动节点：终端场景降级为 DELETE + INSERT。"""
        rendered_vnodes.pop(patch.key, None)
        self._rendered_cache.pop(patch.key, None)

        if patch.node is not None:
            if not self._rendered_cache.get(patch.node.key):
                self._render_vnode(patch.node)
                self._rendered_cache[patch.node.key] = True
            rendered_vnodes[patch.node.key] = patch.node

    # ═══════════════════════════════════════════════════════
    # ★ P0 修复：多行段落内容清除
    # ═══════════════════════════════════════════════════════

    def _estimate_paragraph_lines(self, content: str) -> int:
        """估计段落内容占用的终端行数（近似值）。

        基于内容长度和终端宽度估算，不考虑 Rich 样式标签宽度。
        对于 CJK 字符使用 2 倍宽度估算。
        """
        width = self._output.width
        if width <= 0:
            return 1
        total = 0
        for line in content.split('\n'):
            line_w = 0
            for ch in line:
                line_w += 2 if ord(ch) > 0x2e80 else 1  # 近似 CJK 检测
            total += max(1, (line_w + width - 1) // width)
        return total

    def _cache_paragraph_lines(self, key: str, content: str) -> None:
        """缓存段落占用的终端行数。"""
        if not hasattr(self, '_paragraph_lines'):
            self._paragraph_lines: dict[str, int] = {}
        self._paragraph_lines[key] = self._estimate_paragraph_lines(content)

    def _clear_paragraph_lines(self, key: str) -> None:
        """清除段落所占的全部终端行（从当前行向上清除）。"""
        if not hasattr(self, '_paragraph_lines'):
            self._paragraph_lines: dict[str, int] = {}
        old_lines = self._paragraph_lines.get(key, 1)
        for _ in range(old_lines - 1):
            self._output.write('\033[2K\033[A')  # 清除当前行 + 光标上移
        self._output.clear_line()


class _RenderHandlersMixin:
    """渲染 handler 集合（按 VNodeType 组织）。"""

    # ── ROOT ──────────────────────────────────────────────

    def _render_root(self, node: VNode) -> None:
        """根节点：递归渲染所有子节点。"""
        for child in node.children:
            self._render_vnode(child)

        # ── 任务列表统计 ────────────────────────────────
        total_todos = 0
        done_todos = 0
        for child in node.children:
            if child.type is VNodeType.LIST_ITEM:
                marker, _ = is_todo(child.content)
                if marker is not None:
                    total_todos += 1
                    if marker in 'xX':
                        done_todos += 1
        if total_todos > 0:
            progress = _render_todo_progress_bar_shared(done_todos, total_todos)
            if progress:
                self._output.write(progress)

    # ── PARAGRAPH ─────────────────────────────────────────

    def _render_paragraph(self, node: VNode) -> None:
        """段落：渲染内联格式内容。"""
        t = self._inline_engine._render_inline(node.content)
        self._output_assembled(t)
        # 【Bug5 修复】段落间插入空行分隔，避免连续段落粘连
        self._output.write_line()
        # ★ P0 修复：缓存段落行数，供 _clear_paragraph_lines 清除旧内容时使用
        if hasattr(self, '_cache_paragraph_lines'):
            self._cache_paragraph_lines(node.key, node.content)

    # ── HEADING ───────────────────────────────────────────

    def _render_heading(self, node: VNode) -> None:
        """标题：按 level 设置样式。"""
        level = node.props.get("level", 1)
        text = node.content

        t, padding = _render_heading_shared(
            text, level, self._output.width,
            self._inline_engine._render_inline,
        )
        if padding is not None:
            self._output.write_raw(" " * padding)
        self._output_assembled(t)
        # 【Bug4 修复】_output_assembled 已追加 \n，只需再补充一个空行
        # 原代码有两次 write_line() 导致 2 个空行，现改为 1 个
        self._output.write_line()

    # ── HR ────────────────────────────────────────────────

    def _render_hr(self, node: VNode) -> None:
        """分隔线。"""
        self._output.write(_render_hr_shared(self._output.width))

    # ── EMPTY ─────────────────────────────────────────────

    def _render_empty(self, node: VNode) -> None:
        """空行。"""
        self._output.write_line()

    # ── BLOCKQUOTE ────────────────────────────────────────

    def _render_blockquote(self, node: VNode) -> None:
        """嵌套引用：depth 决定 ▐ 竖线条数。"""
        depth = node.props.get("depth", 1)
        assembled = _render_blockquote_shared(
            node.content, depth, self._inline_engine._render_inline,
        )
        self._output_assembled(assembled)
        for child in node.children:
            self._render_vnode(child)

    # ── LIST_ITEM ─────────────────────────────────────────

    def _render_list_item(self, node: VNode) -> None:
        """列表项渲染（支持 Todo ☐/☑ 检测）。"""
        depth = node.props.get("depth", 1)
        is_ordered = node.props.get("ordered", False)
        number = node.props.get("number", 1)
        text = node.content
        assembled = _render_list_item_shared(
            text, depth, not is_ordered, number,
            self._inline_engine._render_inline,
        )
        self._output_assembled(assembled)

    # ── DEFINITION_ITEM ───────────────────────────────────

    def _render_definition_item(self, node: VNode) -> None:
        """定义列表项：术语 + 定义。"""
        result = _render_definition_item_shared(
            node.props.get("term", ""), node.content,
            node.props.get("indent", 0), self._inline_engine._render_inline,
        )
        self._output_assembled(result)

    # ── CODE_FENCE ────────────────────────────────────────

    def _render_code_fence(self, node: VNode) -> None:
        """代码块围栏线（```lang 打开 或 ```关闭）。"""
        lang = node.props.get("lang", "")
        indented = node.props.get("indented", False)
        attrs = node.props.get("attrs", "")

        # 关闭围栏：content 为纯 "```"
        if node.content == "```":
            t = render_code_fence_close(indented)
        else:
            # 检查是否有 title（filename）
            title = node.props.get("title", "")
            if title:
                title_bar = render_code_title_bar(title, lang, self._output.width)
                self._output.write(title_bar)
            t = render_code_fence_open(lang, indented, attrs)
        self._output_assembled(t)

    # ── CODE_LINE ─────────────────────────────────────────

    def _ensure_theme(self):
        """缓存 Pygments 主题（给 _render_code_line 共享）。

        ★ Bug4 修复 (P2)：去掉 hasattr 动态检查，改用直接判 None。
        宿主类 VNodePatcher.__init__ 中已预初始化 self._cached_theme = None，
        消除每行代码渲染时 hasattr 的性能开销。
        """
        if self._cached_theme is None:
            from ..._utils import get_code_style
            self._cached_theme = Syntax.get_theme(get_code_style(self._code_theme))
        return self._cached_theme

    def _render_code_line(self, node: VNode) -> None:
        """渲染单行代码（语法高亮）。

        ★ Bug1 修复：lexer 可能为 None → 前置判空立即降级
        ★ Bug2 修复：theme 全局缓存，避免每行重复创建
        ★ Bug6 修复：空行显式输出 dim 空白行，避免丢失换行
        """
        line = node.content
        lang = node.props.get("lang", "text")

        # 【Bug6 修复】空代码行：直接输出 dim 空白行 + 换行
        if not line:
            self._output_assembled(Text(" ", style=Style(dim=True)))
            return

        try:
            from ..._rendering import get_lexer as _get_lexer_shared
            lexer = _get_lexer_shared(lang)
            # 【Bug1 修复】lexer 为 None 时提前降级
            if lexer is None:
                raise ValueError(f"lexer not found for lang={lang}")

            theme = self._ensure_theme()
            code_text = highlight_line(line, lexer, theme)

            if self._typing_speed > 0:
                code_speed = self._typing_speed
                self._output.write_typing(code_text, code_speed)
            else:
                self._output.write(code_text)
        except Exception:
            logger.debug("代码行语法高亮失败，降级为纯文本: lang=%s", lang)
            code_text = Text(line, style=Style(dim=True))
            if self._typing_speed > 0:
                code_speed = self._typing_speed
                self._output.write_typing(code_text, code_speed)
            else:
                self._output.write(code_text)

    # ── MATH ──────────────────────────────────────────────

    def _render_math(self, node: VNode) -> None:
        """数学公式块（使用 Rich Panel 美化）。"""
        source = node.content.strip()
        if not source:
            return
        panel = self._math_renderer.render_block(source)
        self._output.write(panel)
        self._output.write_line()

    # ── MERMAID ───────────────────────────────────────────

    def _render_mermaid(self, node: VNode) -> None:
        """Mermaid 图表块。

        ★ Bug7 修复：mermaid_renderer.render() 可能抛出异常，
        用 try-except 兜底，异常时输出纯文本源。
        ★ Bug3 修复 (P1)：围栏和渲染内容之间追加空行分隔，
        避免围栏线和图表内容紧贴导致视觉粘连。
        """
        source = node.content
        lang = node.props.get("lang", "mermaid")
        t = _render_mermaid_block_shared(lang)
        if self._typing_speed > 0:
            code_speed = self._typing_speed
            self._output.write_typing(t, code_speed)
        else:
            self._output.write(t)
        # 【Bug3 修复】围栏和内容之间插入空行分隔
        self._output.write_line()
        try:
            result = self._mermaid_renderer.render(source)
            self._output.write(result)
        except Exception:
            logger.debug("Mermaid 渲染失败，降级为纯文本: lang=%s", lang, exc_info=True)
            self._output.write(Text(source, style=Style(dim=True)))
        self._output.write_line()
        t = _render_mermaid_close_shared()
        if self._typing_speed > 0:
            code_speed = self._typing_speed
            self._output.write_typing(t, code_speed)
        else:
            self._output.write(t)

    # ── TABLE ─────────────────────────────────────────────

    def _render_table(self, node: VNode) -> None:
        """表格——Rich Table 批量构造。"""
        rows = node.props.get("rows", [])
        alignments = node.props.get("alignments", [])
        if not rows or not alignments:
            return
        table = build_rich_table(
            rows, alignments,
            self._inline_engine._render_inline,
            self._output.width,
        )
        self._output.print(table)
        self._output.write_line()

    # ── DETAILS ───────────────────────────────────────────

    def _render_details(self, node: VNode) -> None:
        """折叠块（<details><summary>...）。"""
        depth = node.props.get("depth", 0)
        summary = node.props.get("summary", "")
        assembled = _render_details_header_shared(
            depth, summary, self._inline_engine._render_inline,
        )
        self._output_assembled(assembled)
        # ★ Bug1 修复：输出 body 内容（node.content 由 builder 拼接子节点内容）
        if node.content:
            body = self._inline_engine._render_inline(node.content)
            self._output_assembled(body)
        for child in node.children:
            self._render_vnode(child)
        self._output.write(_render_details_footer_shared(depth))

    # ── ADMONITION ────────────────────────────────────────

    def _render_admonition(self, node: VNode) -> None:
        """告示块（> [!NOTE/WARNING/...]）。"""
        adm_type = node.props.get("type", "NOTE").upper()
        content = node.content
        header, prefix, footer = _render_admonition_header_shared(
            adm_type, content, self._output.width, self._inline_engine._render_inline,
        )
        self._output.write(header)
        for child in node.children:
            child_t = self._inline_engine._render_inline(child.content)
            assembled = Text.assemble(prefix, child_t)
            self._output_assembled(assembled)
        self._output.write(footer)

    # ── HTML_BLOCK ────────────────────────────────────────

    def _render_html_block(self, node: VNode) -> None:
        """HTML 块级元素（含框线装饰）。"""
        tag = node.props.get("tag", "div")
        self._output.write(render_html_block_open(tag, self._output.width))

        for child in node.children:
            self._render_vnode(child)

        self._output.write(render_html_block_close(tag, self._output.width))

    # ── HTML_LINE ─────────────────────────────────────────

    def _render_html_line(self, node: VNode) -> None:
        """HTML 行内容。"""
        content = self._inline_engine._render_inline(node.content)
        assembled = Text("  ")
        assembled.append_text(content)
        self._output_assembled(assembled)
