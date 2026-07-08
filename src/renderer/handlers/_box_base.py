"""_box_base — 框线渲染 Mixin，消除 Admonition/FencedDiv 的重复代码。

BaseBoxMixin 提供统一的 _handle_box_open/line/close 实现，
AdmonitionHandler 和 FencedDivHandler 通过多重继承复用此 Mixin，
仅需覆盖 _get_color() 和 _get_prefix() 方法定制各自的颜色/前缀逻辑。
"""

from __future__ import annotations

import logging

from rich.text import Text

from ..types import Token
from .._rendering import render_box_open, render_box_line_prefix, render_box_close


_logger = logging.getLogger(__name__)


class BaseBoxMixin:
    """框线渲染 Mixin — 提供统一的 box_open/line/close 处理。

    子类需实现：
      - _get_color(box_type: str) -> str: 根据类型获取框线颜色
      - _get_prefix(box_type: str) -> str: 获取框线前缀文本
    """

    def _get_box_type(self, token: Token) -> str:
        """从 token.meta 提取框类型。子类可覆写。"""
        return token.meta.get("type", "NOTE").upper()

    def _get_color(self, box_type: str) -> str:
        """获取框线颜色。子类必须覆写。"""
        return "bright_black"

    def _get_prefix(self, box_type: str) -> str:
        """获取框线前缀文本。子类必须覆写。"""
        return box_type

    def _handle_box_open(self, token: Token, engine) -> None:
        """框线打开：渲染带颜色的标题行。"""
        try:
            box_type = self._get_box_type(token)
            color = self._get_color(box_type)
            prefix = self._get_prefix(box_type)
            t = render_box_open(prefix, token.content, color, engine.output_width)
            engine.write(t)
        except Exception:
            _logger.debug("Box打开渲染异常，跳过", exc_info=True)

    def _handle_box_line(self, token: Token, engine) -> None:
        """框线内容行：带颜色竖线前缀。"""
        try:
            box_type = self._get_box_type(token)
            color = self._get_color(box_type)
            # 空行在框内渲染为空行前缀（仅竖线）
            if token.meta.get("empty"):
                engine.write_line()
                return
            content = engine.render_inline(token.content)
            assembled = Text.assemble(
                render_box_line_prefix(color),
                content,
            )
            engine.output_assembled(assembled)
        except Exception:
            _logger.debug("Box行渲染异常，跳过", exc_info=True)

    def _handle_box_close(self, token: Token, engine) -> None:
        """框线关闭：绘制底部框线。"""
        try:
            box_type = self._get_box_type(token)
            color = self._get_color(box_type)
            t = render_box_close(color, engine.output_width)
            engine.write(t)
        except Exception:
            _logger.debug("Box关闭渲染异常，跳过", exc_info=True)
