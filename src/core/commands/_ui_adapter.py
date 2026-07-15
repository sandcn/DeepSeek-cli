"""CommandUiAdapter — 命令系统的 UI 适配器（依赖倒置）

封装命令函数中需要的 UI 交互操作（底部栏选择、主题切换、diff 渲染、
消息显示、消息编辑等），所有 ui/ 包的导入被限制在此适配器内部，
通过延迟导入（函数体内 import）确保 core/ 层不直接依赖 ui/ 基础设施。

使用方式：
    adapter = CommandUiAdapter()
    result = adapter.run_bottom_bar_selection(items, display_items, ...)
    adapter.set_theme("dark")

设计原则：
    - 鸭子类型：无基类继承，所有方法按名称匹配
    - 延迟导入：所有 ui/ 模块在方法体内导入，避免模块级加载副作用
    - 无副作用：__init__ 不触发任何 ui/ 模块导入
    - 纯同步：不引入异步方法，与命令函数的同步执行模型一致
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


class CommandUiAdapter:
    """命令 UI 适配器 — 封装命令函数需要的所有 UI 操作

    每个方法延迟导入对应的 ui/ 模块，在首次调用时加载。
    所有方法签名与 ui/ 模块的原始函数保持一致。

    方法清单：
        run_bottom_bar_selection   — 底部栏交互选择
        get_theme_names_with_desc  — 获取所有主题名称和描述
        get_active_theme           — 获取当前主题名称
        set_theme                  — 设置活动主题
        render_diff_to_ansi        — 文件差异 ANSI 渲染
        display_messages           — 消息列表全量显示
        edit_current_messages      — 编辑当前消息列表
    """

    def run_bottom_bar_selection(
        self,
        items: list[str],
        display_items: list[str],
        initial_idx: int = 0,
        title: str = "选择",
        bottom_bar: Any = None,
    ) -> dict:
        """在底部栏补全弹窗中运行交互式选择，返回选中结果。

        返回值: {"action": "confirmed"|"cancel"|"error", "index": int | None}
        """
        from ...tui.widgets.bottom_bar.selection import run_bottom_bar_selection as _select
        return _select(items, display_items, initial_idx, title, bottom_bar)

    def get_theme_names_with_desc(self) -> list[tuple[str, str]]:
        """获取所有主题名称和描述。"""
        from ...tui.core.theme import get_theme_names_with_desc as _fn
        return _fn()

    def get_active_theme(self) -> str:
        """获取当前主题名称。"""
        from ...tui.core.theme import get_active_theme as _fn
        return _fn()

    def set_theme(self, name: str) -> None:
        """设置活动主题。"""
        from ...tui.core.theme import set_theme as _fn
        _fn(name)

    def render_diff_to_ansi(self, path: str, old_content: str, new_content: str) -> str:
        """将文件差异渲染为带 ANSI 颜色的纯文本字符串。"""
        from ...tui.consumer.diff_renderer import render_diff_to_ansi as _fn
        return _fn(path, old_content, new_content)

    def display_messages(
        self,
        data: list[dict],
        agent: Any = None,
        idx_map: list[int] | None = None,
        speed: int = 0,
    ) -> None:
        """恢复会话后展示所有消息内容。"""
        from ...tui.pipeline.message_display import _display_messages as _fn
        _fn(data, agent, idx_map, speed)

    def edit_current_messages(self, agent: Any, state: dict) -> bool:
        """编辑当前消息列表，返回是否成功。"""
        from ...tui.pipeline.message_editor import edit_current_messages as _fn
        return _fn(agent, state)


__all__ = ["CommandUiAdapter"]
