"""CommandUiAdapter — 命令系统的 UI 适配器（依赖倒置）

封装命令函数中需要的 UI 交互操作（底部栏选择、主题切换、diff 渲染、
消息显示、消息编辑等），所有 ui/ 包的导入被限制在此适配器内部，
通过延迟导入（函数体内 import）确保 core/ 层不直接依赖 ui/ 基础设施。

2026-07-29 TUI 重构适配：
  - run_bottom_bar_selection → 使用 _bottom_bar.py 内置方法
  - 主题函数 → 去除了 theme.py 依赖，返回默认值
  - diff → 移到 _diff_renderer.py
  - display_messages / edit_current_messages → 委托到 pipeline/
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


class CommandUiAdapter:
    """命令 UI 适配器 — 封装命令函数需要的所有 UI 操作"""

    def run_bottom_bar_selection(
        self,
        items: list[str],
        display_items: list[str],
        initial_idx: int = 0,
        title: str = "选择",
        bottom_bar: Any = None,
    ) -> dict:
        """在底部栏补全弹窗中运行交互式选择。

        返回: {"action": "confirmed"|"cancel"|"error", "index": int | None}
        """
        if bottom_bar is None:
            return {"action": "error", "index": None}

        display = display_items if display_items else items
        try:
            bottom_bar.show_completions(display, initial_idx,
                                        texts=items,
                                        start_pos=0,
                                        orig_prefix="",
                                        types=None,
                                        match_prefix="")
        except Exception as e:
            _logger.debug("run_bottom_bar_selection: show_completions 失败: %s", e)
            return {"action": "error", "index": None}

        import time
        deadline = time.monotonic() + 60

        while time.monotonic() < deadline:
            from ..api.escape_monitor._monitor import EscapeMonitor
            try:
                input_inst = getattr(bottom_bar, '_input', None)
                if input_inst is not None:
                    text = input_inst.get_queued_input()
                    if text is not None:
                        sel_idx = bottom_bar.get_selected_completion_index()
                        bottom_bar.hide_completions()
                        return {"action": "confirmed", "index": sel_idx}
            except Exception:
                pass

            try:
                bottom_bar.force_redraw()
            except Exception:
                pass

            time.sleep(0.05)

        bottom_bar.hide_completions()
        return {"action": "cancel", "index": None}

    def get_theme_names_with_desc(self) -> list[tuple[str, str]]:
        """获取所有主题名称和描述。

        TUI 重构后 theme.py 已删除，返回默认主题列表。
        """
        return [("default", "默认终端主题")]

    def get_active_theme(self) -> str:
        """获取当前主题名称。"""
        return "default"

    def set_theme(self, name: str) -> None:
        """设置活动主题（重构后为 no-op）。"""
        _logger.debug("set_theme(%s): 主题系统已移除，忽略", name)

    def render_diff_to_ansi(self, path: str, old_content: str, new_content: str) -> str:
        """将文件差异渲染为带 ANSI 颜色的纯文本字符串。"""
        from ...tui._diff_renderer import render_diff_to_ansi as _fn
        return _fn(path, old_content, new_content)

    def display_messages(
        self,
        data: list[dict],
        agent: Any = None,
        idx_map: list[int] | None = None,
        speed: int = 0,
    ) -> None:
        """恢复会话后展示所有消息内容。

        委托到 pipeline/message_display.py（已恢复）。
        """
        from ...tui.pipeline.message_display import display_messages as _fn
        _fn(data, agent=agent, idx_map=idx_map, speed=speed)

    def edit_current_messages(
        self, agent: Any, state: dict,
        bottom_bar: Any = None, input_: Any = None,
    ) -> bool:
        """编辑当前消息列表。

        委托到 pipeline/message_editor.py（已恢复）。
        """
        from ...tui.pipeline.message_editor import edit_current_messages as _fn
        return _fn(agent, state, bottom_bar=bottom_bar, input_=input_)


__all__ = ["CommandUiAdapter"]
