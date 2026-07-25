"""
显示抽象基类 (Base Display)

设计意图：
---------
BaseDisplay 是所有显示层的抽象基类，统一核心层与显示层实现接口。
子类可按需覆盖以下可选方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.output_target import IOutputTarget


class BaseDisplay(ABC):
    """显示抽象基类，定义核心层显示契约。子类可按需覆盖以下可选方法。"""

    def __init__(self, output_target: Optional["IOutputTarget"] = None):
        """初始化显示基类。

        Args:
            output_target: 输出目标实例。默认为 None，子类可自行创建默认输出。
        """
        self._output_target = output_target

    @property
    def output_target(self) -> Optional["IOutputTarget"]:
        """获取当前输出目标。"""
        return self._output_target

    @abstractmethod
    def update_status(self, label: str, status: str) -> None:
        """更新状态。

        更新指定 label 对应显示单元的当前状态文本。

        Args:
            label:  工具标识或 Agent 标识
            status: 状态描述文本（如"等待中"、"运行中"、"已完成"）
        """
        ...

    def capture_and_print(self, display_func) -> str:
        """捕获显示函数的输出并打印。

        默认回退到简单字符串捕获。子类可覆盖以提供特定实现。

        Args:
            display_func: 可调用对象，接收一个列表参数用于追加输出行。

        Returns:
            str: 捕获的输出字符串。
        """
        lines: list[str] = []
        display_func(lines)
        return "\n".join(lines)

    def capture_and_print_async(self, display_func) -> str:
        """异步捕获显示函数的输出并打印。

        默认回退到同步的 capture_and_print，子类可覆盖以提供异步实现。
        """
        return self.capture_and_print(display_func)

    def tool_batch_start(self, label: str, names: list[str]) -> None:
        """批量工具开始。可选覆盖。"""
        pass

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        """工具解析中。可选覆盖。"""
        pass

    def update_parse_info(self, label: str, tool_name: str, tokens: int, elapsed: float) -> None:
        """更新解析信息。可选覆盖。"""
        pass

    def parse_info_done(self, label: str) -> None:
        """解析信息完成。可选覆盖。"""
        pass

    def add_agent(self, label: str, description: str, status: str = "running") -> None:
        """添加代理。可选覆盖。"""
        pass

    def update_agent_status(self, label: str, status: str) -> None:
        """更新代理状态。可选覆盖。"""
        pass
