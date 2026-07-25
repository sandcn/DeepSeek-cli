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

from ._base import TuiComponent, _estimate_content_lines, _safe_write_ansi

_logger = logging.getLogger(__name__)


def _normalize_carriage_return(text: str) -> tuple[str, bool]:
    """规范化 \\r 回车字符，返回处理后的文本和是否有 \\r 的标志。

    Args:
        text: 原始文本。

    Returns:
        tuple[str, bool]: (处理后的文本, 是否包含 \\r)。
            - 包含 ANSI 序列时：移除所有 \\r
            - 纯文本时：取最后一个 \\r 之后的部分
            - 无 \\r 时：返回原文本
    """
    has_carriage = '\r' in text
    if has_carriage:
        if '\033[' in text:
            clean = text.replace('\r', '')
        else:
            clean = text.split('\r')[-1]
    else:
        clean = text
    return clean, has_carriage


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
        clean, _ = _normalize_carriage_return(text)
        return self._finalize_render(clean, buffer)

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。

        \\r 实时输出流保留原处理逻辑（无换行等待后续覆盖）；
        非 \\r 路径委托 render(buffer) 获取内容再通过 adapter 输出。
        """
        text = self.text
        clean, has_carriage = _normalize_carriage_return(text)

        if has_carriage:
            if '\033[' in text:
                _safe_write_ansi(adapter, clean)
            else:
                adapter.write_raw(clean)
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                return _estimate_content_lines(clean)
            return 0
        else:
            _safe_write_ansi(adapter, text)
            return _estimate_content_lines(text)
