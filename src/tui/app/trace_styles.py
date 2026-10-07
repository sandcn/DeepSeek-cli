"""轨迹视图共享样式常量（从 trace_view 拆分）。

承载 TraceView 台账/检查器/树渲染共用的样式（Style），供 ``trace_view``
（组件与台账/检查器渲染）与 ``trace_tree``（树渲染）共享，避免重复定义与
循环依赖。

「一切皆插件」：样式数据来自表现层数据注册表（``presentation_data`` →
``trace_style`` 表），可按 Patch/Overlay 覆盖或禁用（非法/缺席回退本模块
兜底字面量）；``trace_style_map()`` 为实时派生入口，模块级 ``_S_*`` 为
派生后的快照（与 ``_theme.Palette`` 同模式——表变更后经热重载/重启生效）。
"""

from __future__ import annotations

from src.tui.core.style import Style


def _style_from_spec(spec, fallback: Style) -> Style:
    """数据表样式规格 dict → Style（非法/缺席回退 fallback）。"""
    if not isinstance(spec, dict):
        return fallback

    def _color(value, default):
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return Style(
        fg=_color(spec.get("fg"), fallback.fg),
        bg=_color(spec.get("bg"), fallback.bg),
        bold=bool(spec.get("bold", fallback.bold)),
        italic=bool(spec.get("italic", fallback.italic)),
        dim=bool(spec.get("dim", fallback.dim)),
        underline=bool(spec.get("underline", fallback.underline)),
        strikethrough=bool(spec.get("strikethrough", fallback.strikethrough)),
        inverse=bool(spec.get("inverse", fallback.inverse)),
    )


#: (常量名, 数据表键, 兜底样式)
_STYLE_SPECS = (
    ("_S_TITLE", "title", Style(fg=45, bold=True)),
    ("_S_HINT", "hint", Style(fg=242)),
    ("_S_SEP_ROW", "sep_row", Style(fg=238)),
    ("_S_INDEX", "index", Style(fg=242)),
    ("_S_TIME", "time", Style(fg=110)),
    ("_S_TEXT", "text", Style(fg=252)),
    ("_S_DIM", "dim", Style(fg=242)),
    ("_S_SEL_BG", "sel_bg", Style(bg=237)),
    ("_S_SEL_MARK", "sel_mark", Style(fg=45, bold=True)),
    ("_S_SECTION", "section", Style(fg=110, bold=True)),
    ("_S_TREE_KEY", "tree_key", Style(fg=75)),
    ("_S_TREE_VAL", "tree_val", Style(fg=252)),
    ("_S_INSP_BG", "insp_bg", Style(bg=237)),
    ("_S_SEARCH_BG", "search_bg", Style(bg=236)),
    ("_S_SEARCH_CUR_BG", "search_cur_bg", Style(bg=25)),
    ("_S_SEARCH_PROMPT", "search_prompt", Style(fg=45, bold=True)),
    # 增强（2026-10-07）：失败高亮 / 状态提示 / 匹配计数 / 帮助与统计面板
    ("_S_ERROR", "error", Style(fg=196, bold=True)),
    ("_S_WARN", "warn", Style(fg=214, bold=True)),
    ("_S_STATUS", "status", Style(fg=221)),
    ("_S_COUNT", "count", Style(fg=214)),
    ("_S_HELP_KEY", "help_key", Style(fg=214)),
    ("_S_HELP_GROUP", "help_group", Style(fg=110, bold=True)),
    ("_S_HELP_DESC", "help_desc", Style(fg=252)),
    ("_S_STATS_LABEL", "stats_label", Style(fg=110)),
    ("_S_STATS_VALUE", "stats_value", Style(fg=252)),
    ("_S_STATS_BAR", "stats_bar", Style(fg=45)),
)

#: 搜索 query 长度上限兜底
_SEARCH_QUERY_MAX_FALLBACK = 200


def trace_style_map() -> dict:
    """当前生效的轨迹样式（实时从数据表派生；供自省/测试）。"""
    from src.presentation_data import trace_style

    return {
        name: _style_from_spec(trace_style(key, None), fallback)
        for name, key, fallback in _STYLE_SPECS
    }


def search_query_max() -> int:
    """搜索 query 长度上限（数据表优先，非法时回退兜底）。"""
    from src.presentation_data import trace_style

    try:
        return int(trace_style("search_query_max", _SEARCH_QUERY_MAX_FALLBACK))
    except (TypeError, ValueError):
        return _SEARCH_QUERY_MAX_FALLBACK


_MAP = trace_style_map()
_S_TITLE = _MAP["_S_TITLE"]
_S_HINT = _MAP["_S_HINT"]
_S_SEP_ROW = _MAP["_S_SEP_ROW"]
_S_INDEX = _MAP["_S_INDEX"]
_S_TIME = _MAP["_S_TIME"]
_S_TEXT = _MAP["_S_TEXT"]
_S_DIM = _MAP["_S_DIM"]
_S_SEL_BG = _MAP["_S_SEL_BG"]
_S_SEL_MARK = _MAP["_S_SEL_MARK"]
_S_SECTION = _MAP["_S_SECTION"]
_S_TREE_KEY = _MAP["_S_TREE_KEY"]
_S_TREE_VAL = _MAP["_S_TREE_VAL"]
_S_INSP_BG = _MAP["_S_INSP_BG"]
_S_SEARCH_BG = _MAP["_S_SEARCH_BG"]
_S_SEARCH_CUR_BG = _MAP["_S_SEARCH_CUR_BG"]
_S_SEARCH_PROMPT = _MAP["_S_SEARCH_PROMPT"]
_S_ERROR = _MAP["_S_ERROR"]
_S_WARN = _MAP["_S_WARN"]
_S_STATUS = _MAP["_S_STATUS"]
_S_COUNT = _MAP["_S_COUNT"]
_S_HELP_KEY = _MAP["_S_HELP_KEY"]
_S_HELP_GROUP = _MAP["_S_HELP_GROUP"]
_S_HELP_DESC = _MAP["_S_HELP_DESC"]
_S_STATS_LABEL = _MAP["_S_STATS_LABEL"]
_S_STATS_VALUE = _MAP["_S_STATS_VALUE"]
_S_STATS_BAR = _MAP["_S_STATS_BAR"]
_SEARCH_QUERY_MAX = search_query_max()

__all__ = [
    "_S_TITLE", "_S_HINT", "_S_SEP_ROW", "_S_INDEX", "_S_TIME", "_S_TEXT",
    "_S_DIM", "_S_SEL_BG", "_S_SEL_MARK", "_S_SECTION", "_S_TREE_KEY",
    "_S_TREE_VAL", "_S_INSP_BG", "_S_SEARCH_BG", "_S_SEARCH_CUR_BG",
    "_S_SEARCH_PROMPT", "_SEARCH_QUERY_MAX",
    "_S_ERROR", "_S_WARN", "_S_STATUS", "_S_COUNT",
    "_S_HELP_KEY", "_S_HELP_GROUP", "_S_HELP_DESC",
    "_S_STATS_LABEL", "_S_STATS_VALUE", "_S_STATS_BAR",
    "trace_style_map", "search_query_max",
]
