"""轨迹视图共享样式常量（从 trace_view 拆分）。

承载 TraceView 台账/检查器/树渲染共用的样式（Style），供 ``trace_view``
（组件与台账/检查器渲染）与 ``trace_tree``（树渲染）共享，避免重复定义与
循环依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style

_S_TITLE = Style(fg=45, bold=True)        # 视图标题前缀（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/分隔弱化（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 轮次分隔行（深灰）
_S_INDEX = Style(fg=242)                   # #N 记录号（暗灰）
_S_TIME = Style(fg=110)                    # 耗时（浅蓝）
_S_TEXT = Style(fg=252)                    # 摘要/内容文本（亮白）
_S_DIM = Style(fg=242)                     # 推理摘要/元信息（暗灰）
_S_SEL_BG = Style(bg=237)                  # 选中行背景
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）
_S_SECTION = Style(fg=110, bold=True)      # 检查器小节标题（浅蓝加粗）
_S_TREE_KEY = Style(fg=75)                 # 树节点键（浅紫蓝——键值分色）
_S_TREE_VAL = Style(fg=252)                # 树节点标量值（亮白——键值分色）
_S_INSP_BG = Style(bg=237)                 # 检查器光标行背景
_S_SEARCH_BG = Style(bg=236)               # 搜索匹配行背景
_S_SEARCH_CUR_BG = Style(bg=25)            # 当前匹配行背景
_S_SEARCH_PROMPT = Style(fg=45, bold=True)  # 搜索输入行提示
_SEARCH_QUERY_MAX = 200                    # 搜索 query 长度上限

__all__ = [
    "_S_TITLE", "_S_HINT", "_S_SEP_ROW", "_S_INDEX", "_S_TIME", "_S_TEXT",
    "_S_DIM", "_S_SEL_BG", "_S_SEL_MARK", "_S_SECTION", "_S_TREE_KEY",
    "_S_TREE_VAL", "_S_INSP_BG", "_S_SEARCH_BG", "_S_SEARCH_CUR_BG",
    "_S_SEARCH_PROMPT", "_SEARCH_QUERY_MAX",
]
