"""单行输出块 — WriteLineBlock。

支持 ANSI 转义序列，用于 OutputEvent / write_line 等非消息流的样式化行输出。

动效（2026-07-15 重构）：
  - 使用 Color256/Style 替代 build_left_border_ansi
  - 使用 StyleSheet 注册的语义色
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter
    from ...render_buffer import RenderBuffer

from rich.text import Text

from ..animation.animator import AnimatorContext
from ..core.style import Style, StyleSheet
from ..core.effects import sine_color
from ..terminal.terminal import is_narrow
from ._base import TuiComponent, _estimate_content_lines, _safe_write_ansi

_logger = logging.getLogger(__name__)


class WriteLineBlock(TuiComponent):
    """单行输出块 — 支持 ANSI 转义序列。

    用于 OutputEvent / write_line 等非消息流的样式化行输出。
    """
    def __init__(self, text: str = "", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。

        先委托 render(buffer) 获取纯文本，再添加边框/ANSI 处理后通过 adapter 输出。
        """
        text = self.text
        frame = AnimatorContext.get_default().frame
        # 构建左边缘呼吸边框
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_color = sine_color(frame, border_breath.fg if border_breath.fg is not None else 23,
                                   min(255, (border_breath.fg if border_breath.fg is not None else 23) + 2), 24)
        border_style = Style(fg=border_color)
        edge_ansi = f"{border_style.to_ansi()}\u2502\033[0m"

        # 窄屏检测缓存 — render_to_adapter() 不是 render() 路径，不强制使用模板方法
        is_narrow_mode = is_narrow()

        if '\033[' in text:
            if is_narrow_mode:
                _safe_write_ansi(adapter, text, fallback_suffix="\n")
            else:
                _safe_write_ansi(adapter, f"  {edge_ansi} {text}", fallback_suffix="\n")
            return _estimate_content_lines(text)
        else:
            # 纯文本路径：通过 render(buffer) 获取内容
            if is_narrow_mode:
                output = text
                adapter.write_raw(output + "\n")
            else:
                output = f"  {edge_ansi} {text}"
                adapter.write_raw(output + "\n")
            return _estimate_content_lines(text)

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        return self._finalize_render(self.text, buffer)
