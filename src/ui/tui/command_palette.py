"""命令面板 — 搜索和执行可用命令。

Ctrl+P 触发，在底部栏补全弹窗中显示命令列表。
"""

from __future__ import annotations

from typing import Optional

from ...core.commands import get_registered_command_names
from ...core.constants import DIM, RESET
from ._selector_base import BaseBottomBarSelector


# ── 命令描述映射 — 带 Emoji 图标，为用户提供直观的操作提示 ──
_COMMAND_DESC: dict[str, str] = {
    '/model': '切换模型',
    '/theme': '切换主题',
    '/load': '加载会话',
    '/editmsg': '编辑消息',
    '/help': '查看帮助',
    '/clear': '清空对话',
    '/system': '修改系统提示词',
    '/cost': '查看当前会话费用',
    '/init': '生成项目摘要文件',
    '/sessions': '管理已保存会话',
    '/loop': '循环执行提词',
    '/compress': '压缩会话上下文',
    '/pin': '标记重要消息',
    '/undo': '撤销上一轮',
    '/retry': '重试上一轮',
    '/edit': '编辑上条输入并重发',
    '/changes': '查看文件变更',
    '/r': '重新生成回复',
}


class CommandPalette(BaseBottomBarSelector[str, Optional[str]]):
    """命令面板 — 搜索并快速执行命令。

    继承 BaseBottomBarSelector，复用 TTLCache + run_bottom_bar_selection 通用流程。

    用法：
        palette = CommandPalette()
        result = palette.show(bottom_bar=chat_ui.bottom_bar)
    """

    def _fetch_items(self) -> list[str]:
        """获取所有注册的命令名列表（TTLCache 从 get_registered_command_names 获取）。"""
        return get_registered_command_names()

    def _format_display(self, items: list[str]) -> list[str]:
        """格式化命令列表为带描述的显示文本 — 美化展示。

        对已知命令附加描述信息，未知命令保持原样。
        """
        from ...core.constants import BRIGHT_CYAN, BRIGHT_GREEN
        labels: list[str] = []
        for cmd in items:
            desc = _COMMAND_DESC.get(cmd, '')
            if desc:
                # ★ 美化：命令加粗 + 描述带箭头装饰
                label = f"{BRIGHT_GREEN}{cmd}{RESET}  {DIM}\u279c{RESET} {DIM}{desc}{RESET}"
            else:
                label = f"{BRIGHT_CYAN}{cmd}{RESET}  {DIM}\u279c{RESET}"
            labels.append(label)
        return labels

    def _on_selected(self, item: str) -> str | None:
        """用户选中命令后原样返回（带 "/" 前缀）。"""
        return item

    def _get_title(self) -> str:
        items = self._cache.get()
        count = len(items) if items else 0
        return f"\u547d\u4ee4\u9762\u677f {count}\u6761"  # 命令面板 N条


__all__ = ["CommandPalette"]
