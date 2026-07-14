"""组件基类 — TuiComponent + _estimate_content_lines。

从 _components.py 拆分，包含所有组件共用的基类和辅助函数。
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from rich.text import Text

_logger = logging.getLogger(__name__)


class TuiComponent:
    """React Ink-like 渲染组件基类。

    所有子类必须实现 render() 方法，可选重写 render_to_adapter()。

    ## 两种渲染路径

    路径 A（默认——适用于大部分组件）：
        子类仅实现 render() → str | Text。
        基类 render_to_adapter() 自动调用 render() 获取输出，
        再将结果通过 adapter.write() 写入 OutputAdapter。

        适用场景：UserMsgBlock、ErrorBlock、NotificationBlock 等
        输出格式固定、无需直接操作 adapter 的组件。

    路径 B（高级——需要直接操作 OutputAdapter）：
        子类重写 render_to_adapter()，完全绕过 render()，
        直接对 OutputAdapter 进行操作（如分段写入、ANSI 处理等）。

        适用场景：ToolOutputBlock（需要处理 \\r 回车/ANSI 转义）、
        ToolSummaryBlock（需要根据成功/失败组合多次写入）等
        输出逻辑复杂的组件。重写 render_to_adapter() 时仍应实现
        render() 作为降级/调试用途。
    """
    @abstractmethod
    def render(self) -> str | Text:
        """渲染组件内容。

        子类必须实现此方法，返回 str 或 rich.text.Text 对象。

        Returns:
            str | Text: 渲染后的文本内容，供 adapter.write() 输出。
        """

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染组件，返回估计行数。

        默认实现（路径 A）：
            调用 self.render() 获取输出，通过 adapter.write() 写入
            OutputAdapter，最后调用 _estimate_content_lines() 返回行数。

        重写场景（路径 B）：
            子类可重写此方法以绕过 render() 直接操作 adapter，
            实现分段写入、ANSI 转义处理等高级渲染逻辑。
            重写时仍建议实现 render() 作为降级/调试用途。

        Args:
            adapter: OutputAdapter 实例，用于将内容写入终端。

        Returns:
            int: 渲染内容的估计行数。
        """
        output = self.render()
        if isinstance(output, (str, Text)):
            adapter.write(output)
            return _estimate_content_lines(str(output))
        return 0


# ═══════════════════════════════════════════════════════════
# 行数估算辅助（内部使用）
# ═══════════════════════════════════════════════════════════

def _estimate_content_lines(text: str) -> int:
    """估算文本内容的终端行数。

    按文本中的换行符数量 + 1 计算行数。
    不处理终端换行（word wrapping），仅适用于粗略估计。

    Args:
        text: 要估算的文本。

    Returns:
        int: 估算的行数，至少为 1。
    """
    if not text:
        return 1
    return text.count('\n') + 1
