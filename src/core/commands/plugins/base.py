"""交互式命令插件基类

InteractiveCommandPlugin 继承 CommandPlugin（core/commands/base.py），
新增 bind_loop() 方法将插件与 InteractiveLoop 实例绑定，
以及 async_execute() 异步执行方法供子类覆写。

子类可访问 loop._chat_ui、loop._monitor、loop._loop_state 等
内部状态以执行 suspend/resume/stop/start 等编排操作。
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from ..base import CommandPlugin, CommandMeta

if TYPE_CHECKING:
    pass  # 延迟导入避免循环

_logger = logging.getLogger(__name__)


class InteractiveCommandPlugin(CommandPlugin):
    """交互式命令插件基类

    通过 bind_loop() 注入 InteractiveLoop 引用，
    子类可获取 _chat_ui/_monitor/_loop_state 等内部状态。

    提供 async_execute() 异步方法，供需要 await 异步操作的子类覆写。
    默认实现委托到同步 execute() 方法。
    """

    def __init__(self):
        super().__init__()
        self._loop: Any = None

    @property
    def loop(self) -> Any:
        """绑定的 InteractiveLoop 实例"""
        return self._loop

    @property
    def name(self) -> str:
        """命令名称（从元数据获取）"""
        return self.meta.name

    def bind_loop(self, loop: Any) -> None:
        """绑定 InteractiveLoop 实例

        Args:
            loop: InteractiveLoop 实例（来自 app_loop.py）
        """
        self._loop = loop

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行命令

        默认实现调用同步 execute() 方法。
        需要异步操作的子类应覆写此方法为 async def。

        Args:
            ctx: CommandContext 实例

        Returns:
            True 表示命令已处理
        """
        return self.execute(ctx)

    @abstractmethod
    def execute(self, ctx: Any) -> bool:
        """执行命令

        Args:
            ctx: CommandContext 实例

        Returns:
            True 表示命令已处理
        """
        ...
