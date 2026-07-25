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
from ..core.text_utils import build_border_breath_ansi
from ..terminal.terminal import is_narrow
from ._base import TuiComponent, _estimate_content_lines

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
        # 构建左边缘呼吸边框（统一使用 build_border_breath_ansi）
        edge_ansi = build_border_breath_ansi(frame, 23, 24)

        if '\033[' in text:
            # ANSI 转义路径
            try:
                if is_narrow():
                    adapter.write(Text.from_ansi(text))
                else:
                    adapter.write(Text.from_ansi(f"  {edge_ansi} {text}"))
            except Exception:
                _logger.debug("write_line ANSI 解析失败, 回退 raw 输出", exc_info=True)
                adapter.write_raw(text + "\n")
                return _estimate_content_lines(text)
            return _estimate_content_lines(text)
        else:
            # 纯文本路径：通过 render(buffer) 获取内容
            if is_narrow():
                output = text
                adapter.write_raw(output + "\n")
            else:
                output = f"  {edge_ansi} {text}"
                adapter.write_raw(output + "\n")
            return _estimate_content_lines(text)

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        if buffer is not None:
            buffer.write(0, 0, self.text)
            return None
        return self.text
