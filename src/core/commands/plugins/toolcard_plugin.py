"""ToolcardPlugin — 折叠/展开工具卡片 (/toolcard)

2026-08-15 用户需求：工具完成后默认自动折叠为单行（close_tool_box 置
``block.tool_collapsed``，渲染层 tool_card_lines 只显示标题行）。本命令
手动切换折叠状态——展开查看工具输出后再次折叠，屏幕不被大段输出占满。

用法：
  /toolcard            切换最后一张已关闭工具卡（最近完成，最常需要展开）
  /toolcard all        切换全部已关闭工具卡
  /toolcard expand     展开最后一张（等价 /toolcard e）
  /toolcard collapse   折叠最后一张（等价 /toolcard c）
  /toolcard expand all 展开全部（等价 /toolcard all expand）

实现：经 ``chat_ui.fold_tool_cards`` 投递 ``ToolFoldCmd`` 到渲染命令队列——
折叠切换（``model.fold_tool_cards`` → ``set_tool_collapsed`` → 重建
``committed_lines``）在渲染线程执行，避免与渲染循环跨线程竞争；通知反馈
同样经命令队列（``on_notification``）在渲染线程显示。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry

_logger = logging.getLogger(__name__)


class ToolcardPlugin(InteractiveCommandPlugin):
    """折叠/展开工具卡片 (/toolcard)。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="toolcard",
            aliases=["fold", "expand"],
            description="折叠/展开工具卡片（无参=切换最后一张；all=全部）",
        )

    def execute(self, ctx: Any) -> bool:
        """执行 /toolcard 命令（同步——操作入渲染队列，无需异步）。"""
        loop = self._loop
        if loop is None:
            _logger.error("ToolcardPlugin 未绑定 InteractiveLoop")
            return True
        chat_ui = getattr(loop, "_chat_ui", None)
        if chat_ui is None or not hasattr(chat_ui, "fold_tool_cards"):
            _logger.error("ToolcardPlugin: 未找到 ChatUI（fold_tool_cards 不可用）")
            return True

        # 参数解析（顺序无关）：all=全部工具卡；expand/collapse=目标折叠状态
        tool_id = ""
        collapsed = None
        for part in (ctx.arg or "").split():
            p = part.strip().lower()
            if p in ("all", "-a", "--all"):
                tool_id = "all"
            elif p in ("expand", "e", "open", "o"):
                collapsed = False
            elif p in ("collapse", "c", "fold", "f"):
                collapsed = True
            else:
                chat_ui.on_notification(f"+ 未知参数: {part}（用法: /toolcard [all] [expand|collapse]）")
                return True

        chat_ui.fold_tool_cards(tool_id=tool_id, collapsed=collapsed)
        scope = "全部" if tool_id == "all" else "最后一张"
        if collapsed is True:
            action = "折叠"
        elif collapsed is False:
            action = "展开"
        else:
            action = "切换折叠状态"
        chat_ui.on_notification(f"+ 已{action}工具卡片（{scope}）")
        return True


# 模块级自注册（与其他插件一致）
get_plugin_registry().register(ToolcardPlugin())
