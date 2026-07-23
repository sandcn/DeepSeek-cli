"""InlineHandler — 段落/标题/引用/列表/分隔线/定义列表"""
import logging
from ..types import Token, TokenType
from .._rendering import (
    is_todo,
    render_heading as _render_heading_shared,
    render_blockquote as _render_blockquote_shared,
    render_list_item as _render_list_item_shared,
    render_definition_item as _render_definition_item_shared,
    render_hr as _render_hr_shared,
)
from .base import TokenHandler


_logger = logging.getLogger(__name__)


class InlineHandler(TokenHandler):
    """处理行内级 Token：段落、标题、分隔线、引用、列表、定义列表。"""

    def __init__(self):
        super().__init__()
        self._bq_depth_stack: list[int] = []

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.PARAGRAPH,
            TokenType.EMPTY_LINE,
            TokenType.HEADING,
            TokenType.HR,
            TokenType.BLOCKQUOTE,
            TokenType.BLOCKQUOTE_OPEN,
            TokenType.BLOCKQUOTE_LINE,
            TokenType.BLOCKQUOTE_CLOSE,
            TokenType.LIST_ITEM,
            TokenType.DEFINITION_ITEM,
            TokenType.LINE_BREAK,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.PARAGRAPH: self._handle_paragraph,
            TokenType.EMPTY_LINE: self._handle_empty_line,
            TokenType.HEADING: self._handle_heading,
            TokenType.HR: self._handle_hr,
            TokenType.BLOCKQUOTE: self._handle_blockquote,
            TokenType.BLOCKQUOTE_OPEN: self._handle_blockquote_open,
            TokenType.BLOCKQUOTE_LINE: self._handle_blockquote_line,
            TokenType.BLOCKQUOTE_CLOSE: self._handle_blockquote_close,
            TokenType.LIST_ITEM: self._handle_list_item,
            TokenType.DEFINITION_ITEM: self._handle_definition_item,
            TokenType.LINE_BREAK: self._handle_line_break,
        }

    # ── 段落 ────────────────────────────────────────────

    def _handle_paragraph(self, token: Token, engine):
        """普通段落。"""
        try:
            t = engine.render_inline(token.content)
            engine._output_assembled(t)
        except Exception:
            _logger.warning("段落渲染异常，跳过", exc_info=True)

    # ── 空行 ────────────────────────────────────────────

    def _handle_empty_line(self, token: Token, engine):
        """空行。"""
        try:
            engine.write_line()
        except Exception:
            _logger.warning("空行渲染异常，跳过", exc_info=True)

    # ── 标题 ────────────────────────────────────────────

    def _handle_heading(self, token: Token, engine):
        """标题。"""
        try:
            level = token.meta.get("level", 1)
            text = token.content

            t, padding = _render_heading_shared(
                text, level, engine.output_width, engine.render_inline,
                ctx=engine.render_context,
            )
            if padding is not None:
                engine.write_raw(" " * padding)
            engine._output_assembled(t)
        except Exception:
            _logger.warning("标题渲染异常，跳过", exc_info=True)

    # ── 分隔线 ──────────────────────────────────────────

    def _handle_hr(self, token: Token, engine):
        """分隔线。"""
        try:
            engine.write(_render_hr_shared(engine.output_width))
        except Exception:
            _logger.warning("分隔线渲染异常，跳过", exc_info=True)

    # ── 嵌套引用 ────────────────────────────────────────

    def _handle_blockquote(self, token: Token, engine):
        """嵌套引用（兼容旧版单 Token 格式）。"""
        try:
            depth = token.meta.get("depth", 1)
            assembled = _render_blockquote_shared(token.content, depth, engine.render_inline)
            engine._output_assembled(assembled)
        except Exception:
            _logger.warning("引用渲染异常，跳过", exc_info=True)

    def _handle_blockquote_open(self, token: Token, engine):
        """引用块开始：保存当前 depth 到栈，设置新 depth。"""
        try:
            # 防御跨渲染轮次泄漏：栈残留但引擎无活跃引用 → 清空栈
            if self._bq_depth_stack and engine.bq_depth == 0:
                self._bq_depth_stack.clear()
            self._bq_depth_stack.append(engine.bq_depth)
            engine.bq_depth = token.meta.get("depth", 1)
        except Exception:
            _logger.warning("引用块打开异常，跳过", exc_info=True)

    def _handle_blockquote_line(self, token: Token, engine):
        """引用块内容行（兼容旧式行级 BLOCKQUOTE）。"""
        try:
            depth = token.meta.get("depth", engine.bq_depth)
            assembled = _render_blockquote_shared(token.content, depth, engine.render_inline)
            engine.output_assembled(assembled)
        except Exception:
            _logger.warning("引用行渲染异常，跳过", exc_info=True)

    def _handle_blockquote_close(self, token: Token, engine):
        """引用块结束：从栈恢复 depth。

        防御 malformed markdown 导致的不匹配 BLOCKQUOTE_CLOSE：
        栈空时直接恢复 depth=0，避免 pop() 引发 IndexError 后 bq_depth 残留。
        """
        try:
            if self._bq_depth_stack:
                engine.bq_depth = self._bq_depth_stack.pop()
            else:
                engine.bq_depth = 0
        except Exception:
            _logger.warning("引用块关闭异常，跳过", exc_info=True)

    # ── 列表项 ──────────────────────────────────────────

    def _handle_list_item(self, token: Token, engine):
        """列表项（支持 Todo 列表）。"""
        try:
            depth = token.meta.get("depth", 1)
            is_bullet = token.meta.get("bullet", True)
            text = token.content
            number = token.meta.get("number", depth)

            marker, content = is_todo(text)
            if marker is not None:
                engine.todo_emitted = False
                engine.todo_state.active = True
                engine.todo_state.total += 1
                if marker in 'xX':
                    engine.todo_state.done += 1

            assembled = _render_list_item_shared(text, depth, is_bullet, number, engine.render_inline)
            engine.output_assembled(assembled)
        except Exception:
            _logger.warning("列表项渲染异常，跳过", exc_info=True)

    # ── 定义列表项 ──────────────────────────────────────

    def _handle_definition_item(self, token: Token, engine):
        """定义列表项渲染：术语 + 定义，带增强视觉分隔。"""
        try:
            result = _render_definition_item_shared(
                token.meta.get("term", ""), token.content,
                token.meta.get("indent", 0), engine.render_inline,
            )
            engine.output_assembled(result)
        except Exception:
            _logger.warning("定义列表项渲染异常，跳过", exc_info=True)

    # ── 换行 ──────────────────────────────────────────

    def _handle_line_break(self, token: Token, engine) -> None:
        """LINE_BREAK → 输出换行。"""
        try:
            engine.write_line()
        except Exception:
            _logger.warning("换行渲染异常，跳过", exc_info=True)
