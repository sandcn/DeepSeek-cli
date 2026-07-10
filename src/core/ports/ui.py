"""UI 端口接口 — 核心层访问 UI 功能的抽象协议

定义主题管理、颜色常量、底部栏交互、差异渲染、消息编辑等 UI 功能的抽象接口，
使 core/ 层在不直接依赖 ui/ 包的前提下使用这些功能。

适配器实现移至 src.core.adapters.ui。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ThemePort(ABC):
    """主题管理端口"""

    @abstractmethod
    def set_theme(self, name: str) -> None:
        """设置活动主题"""
        ...

    @abstractmethod
    def get_active_theme(self) -> str:
        """获取当前主题名称"""
        ...

    @abstractmethod
    def get_theme_names_with_desc(self) -> list[tuple[str, str]]:
        """获取所有主题名称和描述"""
        ...


class BottomBarPort(ABC):
    """底部栏交互端口 — 终端底部交互选择"""

    @abstractmethod
    def run_bottom_bar_selection(
        self,
        items: list[Any],
        display_items: list[str] | None = None,
        title: str = "",
        bottom_bar: Any = None,
        active_theme: str | None = None,
    ) -> Any | None:
        """运行底部栏交互选择，返回选中的 item"""
        ...


class DiffRendererPort(ABC):
    """差异渲染端口 — 文件 diff 的 ANSI 渲染"""

    @abstractmethod
    def render_diff_to_ansi(self, file_path: str, before: str, after: str) -> str:
        """将文件变更渲染为 ANSI 字符串"""
        ...


class MsgEditPort(ABC):
    """消息编辑端口 — 通过 TUI 编辑消息"""

    @abstractmethod
    def edit_current_messages(
        self,
        messages: list[dict],
        system_messages: list[dict],
        agent_name: str | None = None,
    ) -> list[dict] | None:
        """编辑当前消息列表，返回修改后的消息列表"""
        ...


class ParallelDisplayPort(ABC):
    """并行显示端口 — 并行 Agent 的 UI 显示"""

    @abstractmethod
    def create_parallel_session(
        self,
        session_id: str,
        agent_type: str,
        callback: Any = None,
    ) -> Any:
        """创建并行 Agent 显示会话"""
        ...

    @abstractmethod
    def get_active_chat_ui(self) -> Any:
        """获取当前活跃的 ChatUI 实例"""
        ...

    @abstractmethod
    def get_agent_type_abbrev(self, agent_type: str) -> str:
        """获取 Agent 类型缩写"""
        ...
