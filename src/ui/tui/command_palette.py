"""命令面板 — 搜索和执行可用命令。

Ctrl+P 触发，在底部栏补全弹窗中显示命令列表。
"""

from __future__ import annotations

from typing import Optional

from ...core.commands import get_registered_command_names
from ..colors import RESET
from ..colors import CYAN_256, DIM_256
from ..colors import BRIGHT_CYAN_256, BRIGHT_GREEN_256
from ._selector_base import BaseBottomBarSelector
from ._animator import AnimatorContext, BreathPalette
from ._terminal import is_narrow
from ._text_utils import build_scan_highlight_ansi, build_glow_ansi, build_sparkle_ansi


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
}


class CommandPalette(BaseBottomBarSelector[str, Optional[str]]):
    """命令面板 — 搜索并快速执行命令。

    继承 BaseBottomBarSelector，复用 TTLCache + run_bottom_bar_selection 通用流程。

    用法：
        palette = CommandPalette()
        result = palette.show()
    """

    def _fetch_items(self) -> list[str]:
        """获取所有注册的命令名列表（TTLCache 从 get_registered_command_names 获取）。"""
        return get_registered_command_names()

    def _format_display(self, items: list[str]) -> list[str]:
        """格式化命令列表为带描述的显示文本 — 美化展示。

        对已知命令附加描述信息，未知命令保持原样。
        命令名使用呼吸色（BreathPalette）+ sparkle 闪烁，增强动效感。
        描述箭头和文字使用辉光呼吸（glow）。
        颜色使用 256 色体系增强视觉效果。
        """
        frame = AnimatorContext.get_default().breath_frame
        labels: list[str] = []
        for idx, cmd in enumerate(items):
            desc = _COMMAND_DESC.get(cmd, '')
            if desc:
                # ★ 美化：已注册命令用呼吸绿色 + sparkle 闪烁
                if frame > 0:
                    sparkle_ansi = build_sparkle_ansi(frame, 45, 6)
                else:
                    sparkle_ansi = BRIGHT_GREEN_256
                # 描述箭头和文字使用辉光呼吸
                glow_ansi = build_glow_ansi(frame, 110, 12) if frame > 0 else DIM_256
                label = (f"{sparkle_ansi}{cmd}{RESET}  "
                         f"{glow_ansi}\u279c{RESET} "
                         f"{glow_ansi}{desc}{RESET}")
            else:
                # 未知命令使用 CYAN_256 + sparkle
                sparkle_ansi = build_sparkle_ansi(frame, 45, 6) if frame > 0 else CYAN_256
                label = f"{sparkle_ansi}{cmd}{RESET}  {DIM_256}\u279c{RESET}"
            # ★ 扫描高亮：宽屏时对选中行添加周期性扫描高亮背景
            if not is_narrow() and frame > 0:
                label = build_scan_highlight_ansi(idx, frame, len(items), label, scan_period=20)
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
