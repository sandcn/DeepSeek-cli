"""显示端口 — 核心层与 UI 显示的接口"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional


class DisplayPort(ABC):
    """完整显示端口 — 核心层与 UI 显示的抽象协议

    涵盖工具调用、代理状态管理和实时指标。
    """

    # ── 工具调用 ────────────────────────────────────────

    @abstractmethod
    def tool_start(self, tool_label: str, tool_name: str, detail: str, metadata: Optional[dict] = None) -> None:
        """工具开始执行"""
        ...

    @abstractmethod
    def tool_done(self, tool_label: str, tool_name: str = "", success: bool = True, metadata: Optional[dict] = None) -> None:
        """工具执行完成"""
        ...

    @abstractmethod
    def capture_and_print(self, display_func) -> str:
        """捕获显示函数的输出并打印"""
        ...

    # ★ P0 修复: 补充生产中实际使用但接口缺失的方法
    @abstractmethod
    def capture_and_print_async(self, display_func) -> str:
        """异步捕获显示函数的输出并打印（_tool_callbacks.py 通过 hasattr 依赖此方法）"""
        ...

    @abstractmethod
    def update_status(self, label: str, status: str) -> None:
        """更新代理状态（UI 层 4 处实现均已提供此方法但接口缺失）"""
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
        """更新实时输入 token"""
        ...

    @abstractmethod
    def update_live_output(self, label: str, tokens: int) -> None:
        """更新实时输出 token"""
        ...

    @abstractmethod
    def tool_batch_start(self, label: str, names: list[str]) -> None:
        """批量工具开始"""
        ...

    @abstractmethod
    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        """工具解析中"""
        ...

    @abstractmethod
    def update_parse_info(self, label: str, tool_name: str, tokens: int, elapsed: float) -> None:
        """更新解析信息"""
        ...

    @abstractmethod
    def parse_info_done(self, label: str) -> None:
        """解析信息完成 — 参数接收完毕"""
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
        """开始显示"""
        ...

    @abstractmethod
    def stop(self) -> None:
        """停止显示"""
        ...
