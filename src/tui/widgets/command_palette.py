"""命令面板 — 搜索和执行可用命令。

Ctrl+P 触发，在底部栏补全弹窗中显示命令列表。
支持增量过滤与匹配高亮（子步骤 6.2）。
"""

from __future__ import annotations

import re
from typing import Optional

from ...core.commands import get_registered_command_names
from ...core.constants import RESET, BOLD, CYAN_256, DIM_256, BRIGHT_CYAN_256, BRIGHT_GREEN_256
from ..core.ansi_utils import strip_ansi
from .selector_base import BaseBottomBarSelector
from ..core.text_utils import truncate
from ..terminal.terminal import is_narrow, get_terminal_width


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
        颜色使用 256 色体系增强视觉效果。
        """
        labels: list[str] = []
        for cmd in items:
            desc = _COMMAND_DESC.get(cmd, '')
            if desc:
                # ★ 美化：已注册命令用亮绿色 + 描述带 DIM_256 箭头装饰
                label = f"{BRIGHT_GREEN_256}{cmd}{RESET}  {DIM_256}\u279c{RESET} {DIM_256}{desc}{RESET}"
            else:
                # 未知命令使用 CYAN_256 替代 BRIGHT_CYAN
                label = f"{CYAN_256}{cmd}{RESET}  {DIM_256}\u279c{RESET}"
            labels.append(label)
        return labels

    def _on_selected(self, item: str) -> str | None:
        """用户选中命令后原样返回（带 "/" 前缀）。"""
        return item

    def _get_title(self) -> str:
        items = self._cache.get()
        count = len(items) if items else 0
        return f"\u547d\u4ee4\u9762\u677f {count}\u6761"  # 命令面板 N条

    # ── 增量过滤与匹配高亮（步骤 6） ────────────────────

    def filter_items(self, items: list[str], query: str) -> list[str]:
        """根据查询字符串增量过滤命令列表。

        大小写不敏感的 substring 匹配。空查询时返回全部。

        Args:
            items: 全部注册命令名列表。
            query: 用户输入的查询字符串。

        Returns:
            过滤后的命令名列表。
        """
        if not query or not query.strip():
            return items
        query_lower = query.strip().lower()
        return [cmd for cmd in items if query_lower in cmd.lower()]

    def highlight_match(self, display_text: str, query: str) -> str:
        """在显示文本中用亮青色 + 粗体高亮匹配部分。

        先剥离 ANSI 码获得纯文本，在纯文本中定位 query（大小写不敏感），
        然后遍历原始 ANSI 显示文本，在匹配区域插入高亮色码。
        高亮完成后进行窄屏截断（确保高亮色码不影响截断宽度计算）。

        Args:
            display_text: _format_display 输出的 ANSI 彩色文本。
            query: 用户输入的查询字符串。

        Returns:
            高亮后的显示文本（匹配部分包裹 BRIGHT_CYAN_256 + BOLD），
            窄屏时额外截断。
        """
        if not query or not query.strip():
            return display_text

        plain_text = strip_ansi(display_text)
        query_lower = query.strip().lower()
        plain_lower = plain_text.lower()

        start = plain_lower.find(query_lower)
        if start == -1:
            return display_text

        end = start + len(query.strip())

        # ANSI SGR 转义序列正则（与 ansi.py 保持一致）
        _ANSI_SGR_RE = re.compile(r'\033\[[\d;]*[a-zA-Z]')

        result: list[str] = []
        visual_pos = 0
        i = 0
        in_highlight = False

        while i < len(display_text):
            m = _ANSI_SGR_RE.match(display_text, i)
            if m:
                result.append(display_text[i:m.end()])
                i = m.end()
                continue

            # 进入匹配区域前插入高亮起始
            if visual_pos == start and not in_highlight:
                result.append(BRIGHT_CYAN_256)
                result.append(BOLD)
                in_highlight = True

            result.append(display_text[i])
            visual_pos += 1
            i += 1

            # 离开匹配区域后插入 RESET 关闭高亮
            if visual_pos == end and in_highlight:
                result.append(RESET)
                in_highlight = False

        # 若匹配区域延伸到文本末尾，确保关闭高亮
        if in_highlight:
            result.append(RESET)

        highlighted = ''.join(result)

        # ── 窄屏截断（高亮之后，确保高亮色码不影响截断宽度计算） ──
        if is_narrow():
            max_width = max(get_terminal_width() - 4, 20)
            highlighted = truncate(highlighted, max_width)

        return highlighted


__all__ = ["CommandPalette"]
