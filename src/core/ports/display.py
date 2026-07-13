"""显示端口 — 核心层与 UI 显示之间的抽象协议

定义 DisplayPort 抽象基类，覆盖 DefaultDisplayAdapter 全部公有方法签名。
核心层通过此端口访问 UI 显示功能，不直接依赖 ui/ 具体实现模块。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class DisplayPort(ABC):
    """显示端口 — 核心层显示功能的抽象接口

    定义核心层向 UI 层报告状态、工具执行进度、代理状态等显示操作的契约。
    所有适配器实现应继承此抽象类并实现全部抽象方法。
    """

    # ── 工具调用 ────────────────────────────────────────

    @abstractmethod
    def tool_start(self, tool_label: str, tool_name: str, detail: str,
                   metadata: Optional[dict] = None) -> None:
        """工具调用开始"""
        ...

    @abstractmethod
    def tool_done(self, tool_label: str, tool_name: str = "",
                  success: bool = True, metadata: Optional[dict] = None) -> None:
        """工具调用结束"""
        ...

    @abstractmethod
    def capture_and_print(self, display_func) -> str:
        """捕获显示函数的输出并打印"""
        ...

    @abstractmethod
    async def capture_and_print_async(self, display_func) -> str:
        """异步捕获显示函数的输出并打印"""
        ...

    @abstractmethod
    def update_status(self, label: str, status: str) -> None:
        """更新状态"""
        ...

    @abstractmethod
    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        """工具解析中"""
        ...

    @abstractmethod
    def tool_batch_start(self, label: str, names: list[str]) -> None:
        """批量工具开始"""
        ...

    @abstractmethod
    def update_parse_info(self, label: str, tool_name: str, tokens: int,
                          elapsed: float) -> None:
        """更新解析信息"""
        ...

    @abstractmethod
    def parse_info_done(self, label: str) -> None:
        """解析完成"""
        ...

    # ── 代理状态与实时指标 ──────────────────────────────

    @abstractmethod
    def update_model_phase(self, label: str, phase: str, message: str = "") -> None:
        """更新模型阶段"""
        ...

    @abstractmethod
    def update_usage(self, label: str, usage: dict, replace: bool = False) -> None:
        """更新使用量"""
        ...

    @abstractmethod
    def update_speed(self, label: str, speed: float) -> None:
        """更新速度"""
        ...

    @abstractmethod
    def update_live_input(self, label: str, tokens: int) -> None:
        """更新实时输入 token 数"""
        ...

    @abstractmethod
    def update_live_output(self, label: str, tokens: int) -> None:
        """更新实时输出 token 数"""
        ...

    @abstractmethod
    def update_agent_status(self, label: str, status: str) -> None:
        """更新代理状态"""
        ...

    @abstractmethod
    def add_agent(self, label: str, description: str, status: str = "running") -> None:
        """添加代理"""
        ...

    # ── 生命周期 ────────────────────────────────────────

    @abstractmethod
    def start(self) -> None:
        """启动显示"""
        ...

    @abstractmethod
    def stop(self) -> None:
        """停止显示"""
        ...
