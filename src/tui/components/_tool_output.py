"""工具执行输出块 — ToolOutputBlock。

处理工具执行的标准输出/错误，支持 \\r 回车叠加和 ANSI 转义序列。

动效（2026-07-12）：
  - 宽屏：左侧添加极淡青色呼吸边框字符 │（使用 build_glow_ansi 微呼吸，色号 23↔24）
  - 窄屏：降级为无左边缘的纯文本（与原始行为一致）
  - \\r 分支（实时工具输出流）不受动效影响，保持原始行为
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from rich.text import Text

from ._base import TuiComponent, _estimate_content_lines

_logger = logging.getLogger(__name__)

class ToolOutputBlock(TuiComponent):
    """工具执行输出块。"""
    def __init__(self, text: str = "", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self.text = text

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染工具输出内容。

        支持 buffer 参数：传入 buffer 时写入并返回 None；否则返回字符串。
        """
        text = self.text
        has_carriage = '\r' in text

        if has_carriage:
            if '\033[' in text:
                clean = text.replace('\r', '')
            else:
                clean = text.split('\r')[-1]
            result = clean
        else:
            result = text
        return self._finalize_render(result, buffer)

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。

        \\r 实时输出流保留原处理逻辑（无换行等待后续覆盖）；
        非 \\r 路径委托 render(buffer) 获取内容再通过 adapter 输出。
        """
        text = self.text
        has_carriage = '\r' in text

        if has_carriage:
            if '\033[' in text:
                clean = text.replace('\r', '')
                try:
                    adapter.write(Text.from_ansi(clean))
                except Exception:
                    _logger.debug("tool_output ANSI 解析失败, 回退 raw 输出", exc_info=True)
                    adapter.write_raw(clean)
            else:
                clean = text.split('\r')[-1]
                adapter.write_raw(clean)
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                return _estimate_content_lines(clean)
            return 0
        else:
            adapter.write(Text.from_ansi(text))
            return _estimate_content_lines(text)
