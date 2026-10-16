"""表现层数据注册表 — 渲染/TUI 数据表的单一来源（一切皆插件）。

「一切皆插件」：渲染与 TUI 的纯数据表——Emoji 短代码、上下标/圈号 Unicode、
HTML 块标签配色、无序列表符号、轨迹视图 KIND/STATUS 图标配色、主 Agent 运行
模式文本样式、工具显示名、告示块样式、Spinner 帧、配置项说明/选项——不再是
散落在各模块里的硬编码字典，而是注册到本模块的数据表；
每张表由清单中的**独立插件条目**（``presentation_data``）显式注册，因而可被
Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖（整表替换）或
替换。

**清单接管**：``presentation_data`` 聚合插件收到组合根注入的
``managed_presentation_data``（清单已接管的 id，含被禁用的）时经
``set_managed_builtin_data`` 声明这些 id 由清单条目负责——对应内置表不再走
默认装配；被禁用（未挂载）的条目因此真正缺席。无清单（单元测试、独立调用）
时无接管，全部内置表默认生效。

本模块为**叶子模块**（仅依赖标准库），供 ``src.renderer`` 与 ``src.tui`` 消费，
避免引入重依赖。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

_lock = threading.RLock()
_ABSENT = object()


class LiveMapping(Mapping):
    """只读映射视图 — 实时委托到注册表（overlay 变更即时可见）。

    供消费方以「模块级映射常量」形态保留旧调用面（``M[x]`` / ``x in M`` /
    ``M.get`` / 迭代），同时底层数据取自注册表当前生效表。
    """

    def __init__(self, name: str, default=None):
        self._name = name
        self._default = {} if default is None else default

    def _data(self):
        data = data_table(self._name)
        return data if data is not None else self._default

    def __getitem__(self, key):
        return self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def get(self, key, default=None):
        return self._data().get(key, default)



@dataclass(frozen=True)
class DataTable:
    """一张表现层数据表。"""

    id: str
    name: str
    data: Any

    def to_dict(self) -> dict:
        size = len(self.data) if hasattr(self.data, "__len__") else 0
        return {"id": self.id, "name": self.name, "size": size}


# ── 数据表内容（纯数据定义，已抽至独立叶子模块） ──────────────
# 本模块只保留注册表机制与访问器；数据常量的单一来源是
# ``src.presentation_data_tables`` —— 此处按名 re-export，保持既有导入路径
# （``from src.presentation_data import CONFIG_ENTRY_DESC_MAP`` 等）不变。
from .presentation_data_tables import (  # noqa: E402
    ADMONITION_STYLE_MAP,
    BADGE_METRICS_DATA,
    BILLING_DEFAULT_DATA,
    BORDER_CHARS_MAP,
    BORDER_OBJECT_DEFAULT,
    BULLETS,
    CIRCLED_DIGITS,
    CONFIG_ENTRY_DESC_MAP,
    CONFIG_ENTRY_OPTION_MAP,
    CONFIG_KEYMAP_DATA,
    DIFF_STYLE_DATA,
    EMOJI_MAP,
    GRADIENT_STOPS_DATA,
    HTML_TAG_COLORS,
    HTTP_ERROR_HINT_DATA,
    INLINE_SPINNER_FRAMES,
    KITTY_PROTOCOL_DATA,
    MESSAGE_ROLE_ICON_MAP,
    METRIC_DEFAULTS_DATA,
    MODE_STYLE_FG,
    MODE_TEXT,
    MODEL_KEYMAP_DATA,
    MODEL_PATTERNS_DATA,
    NESTED_BULLET_SYMBOLS,
    PLUGIN_KEYMAP_DATA,
    SEMANTIC_COLOR_DATA,
    SHELL_DETECT_DATA,
    SPINNER_PRESET_FRAMES,
    SUBSCRIPT_MAP,
    SUPERSCRIPT_MAP,
    TOOL_DISPLAY_NAME_MAP,
    TRACE_BLOCK_KIND_MAP,
    TRACE_KIND,
    TRACE_KIND_ORDER_DATA,
    TRACE_KEYMAP_DATA,
    TRACE_STATUS,
    TRACE_STYLE_DATA,
    UI_DEFAULTS_DATA,
)

#: 内置数据表声明
_BUILTIN_SPECS: Tuple[DataTable, ...] = (
    DataTable("emoji", "emoji", EMOJI_MAP),
    DataTable("inline_subscript", "inline_subscript", SUBSCRIPT_MAP),
    DataTable("inline_superscript", "inline_superscript", SUPERSCRIPT_MAP),
    DataTable("circled_digits", "circled_digits", CIRCLED_DIGITS),
    DataTable("html_tag_color", "html_tag_color", HTML_TAG_COLORS),
    DataTable("bullet", "bullet", BULLETS),
    DataTable("trace_kind", "trace_kind", TRACE_KIND),
    DataTable("trace_status", "trace_status", TRACE_STATUS),
    DataTable("mode_text", "mode_text", MODE_TEXT),
    DataTable("mode_style", "mode_style", MODE_STYLE_FG),
    DataTable("tool_display_name", "tool_display_name", TOOL_DISPLAY_NAME_MAP),
    DataTable("admonition_style", "admonition_style", ADMONITION_STYLE_MAP),
    DataTable("spinner_frames", "spinner_frames", SPINNER_PRESET_FRAMES),
    DataTable("inline_spinner_frames", "inline_spinner_frames", INLINE_SPINNER_FRAMES),
    DataTable("config_entry_desc", "config_entry_desc", CONFIG_ENTRY_DESC_MAP),
    DataTable("config_entry_option", "config_entry_option", CONFIG_ENTRY_OPTION_MAP),
    DataTable("trace_kind_order", "trace_kind_order", TRACE_KIND_ORDER_DATA),
    DataTable("trace_block_kind", "trace_block_kind", TRACE_BLOCK_KIND_MAP),
    DataTable("message_role_icon", "message_role_icon", MESSAGE_ROLE_ICON_MAP),
    DataTable("border_chars", "border_chars", BORDER_CHARS_MAP),
    DataTable("border_object_default", "border_object_default", BORDER_OBJECT_DEFAULT),
    DataTable("billing_default", "billing_default", BILLING_DEFAULT_DATA),
    DataTable("metric_defaults", "metric_defaults", METRIC_DEFAULTS_DATA),
    DataTable("ui_defaults", "ui_defaults", UI_DEFAULTS_DATA),
    DataTable("semantic_color", "semantic_color", SEMANTIC_COLOR_DATA),
    DataTable("gradient_stops", "gradient_stops", GRADIENT_STOPS_DATA),
    DataTable("shell_detect", "shell_detect", SHELL_DETECT_DATA),
    DataTable("http_error_hint", "http_error_hint", HTTP_ERROR_HINT_DATA),
    DataTable("badge_metrics", "badge_metrics", BADGE_METRICS_DATA),
    DataTable("kitty_protocol", "kitty_protocol", KITTY_PROTOCOL_DATA),
    DataTable("nested_bullet", "nested_bullet", NESTED_BULLET_SYMBOLS),
    DataTable("diff_style", "diff_style", DIFF_STYLE_DATA),
    DataTable("trace_style", "trace_style", TRACE_STYLE_DATA),
    DataTable("trace_keymap", "trace_keymap", TRACE_KEYMAP_DATA),
    DataTable("plugin_keymap", "plugin_keymap", PLUGIN_KEYMAP_DATA),
    DataTable("config_keymap", "config_keymap", CONFIG_KEYMAP_DATA),
    DataTable("model_keymap", "model_keymap", MODEL_KEYMAP_DATA),
    DataTable("model_patterns", "model_patterns", MODEL_PATTERNS_DATA),
)

_builtin_specs: Dict[str, DataTable] = {spec.id: spec for spec in _BUILTIN_SPECS}

_registered_builtin: Dict[str, DataTable] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, DataTable] = {}

_cache: Optional[dict] = None


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置数据表: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


def builtin_data_ids() -> list[str]:
    return list(_builtin_specs)


def default_data_table(spec_id: str) -> DataTable:
    try:
        return _builtin_specs[spec_id]
    except KeyError:
        raise KeyError(f"未知内置数据表: {spec_id!r}（可用: {list(_builtin_specs)}）") from None


def active_data_tables() -> Dict[str, DataTable]:
    with _lock:
        result: Dict[str, DataTable] = {}
        for spec_id, default in _builtin_specs.items():
            if spec_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(spec_id)
            if override is not None:
                result[spec_id] = override
                continue
            if spec_id in _managed_builtin:
                continue
            result[spec_id] = default
        return result


def _invalidate() -> None:
    global _cache
    _cache = None


def register_builtin_data(spec_id: str, spec: Optional[DataTable] = None) -> Callable[[], None]:
    if spec_id not in _builtin_specs:
        raise KeyError(f"未知内置数据表: {spec_id!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = spec if spec is not None else _builtin_specs[spec_id]
        _invalidate()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous
            _invalidate()

    return _undo


def unregister_builtin_data(spec_id: str) -> bool:
    with _lock:
        removed = _registered_builtin.pop(spec_id, None) is not None
        if removed:
            _invalidate()
    return removed


def set_managed_builtin_data(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)
        _invalidate()

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)
            _invalidate()

    return _undo


def managed_data_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_data(ids) -> Callable[[], None]:
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)
        _invalidate()

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)
            _invalidate()

    return _undo


def register_data_table(spec: DataTable) -> Callable[[], None]:
    if not isinstance(spec, DataTable):
        raise TypeError(f"扩展数据表必须是 DataTable: {spec!r}")
    with _lock:
        previous = _extension.get(spec.id, _ABSENT)
        _extension[spec.id] = spec
        _invalidate()

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(spec.id, None)
            else:
                _extension[spec.id] = previous
            _invalidate()

    return _undo


def unregister_data_table(spec_id: str) -> bool:
    with _lock:
        removed = _extension.pop(spec_id, None) is not None
        if removed:
            _invalidate()
    return removed


def _active_by_name() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        by_name: Dict[str, Any] = {}
        for spec in active_data_tables().values():
            by_name[spec.name] = spec.data
        for spec in _extension.values():
            by_name[spec.name] = spec.data
        _cache = by_name
        return _cache


def data_table(name: str, default=None):
    """按表名取当前生效的数据（缺席返回 ``default``）。"""
    return _active_by_name().get(name, default)


# ── 消费方查询辅助 ───────────────────────────────────────


def emoji_map() -> dict:
    return data_table("emoji", {}) or {}


def subscript_map() -> dict:
    return data_table("inline_subscript", {}) or {}


def superscript_map() -> dict:
    return data_table("inline_superscript", {}) or {}


def circled_digits() -> list:
    return list(data_table("circled_digits", []) or [])


def bullets() -> list:
    return list(data_table("bullet", []) or [])


def html_tag_colors() -> dict:
    return data_table("html_tag_color", {}) or {}


def html_tag_color(tag: str, default: str = "bright_black") -> str:
    return html_tag_colors().get(tag, default)


def trace_kind(kind: str) -> dict:
    return (data_table("trace_kind", {}) or {}).get(kind, {})


def trace_kind_icon(kind: str, default: str = "\u00b7") -> str:
    return trace_kind(kind).get("icon", default)


def trace_kind_name(kind: str, default: str = "") -> str:
    return trace_kind(kind).get("name", default or kind)


def trace_kind_fg(kind: str, default: int = 242) -> int:
    return trace_kind(kind).get("fg", default)


def trace_status(status: str) -> dict:
    return (data_table("trace_status", {}) or {}).get(status, {})


def trace_status_icon(status: str, default: str = "\u00b7") -> str:
    return trace_status(status).get("icon", default)


def trace_status_fg(status: str, default: int = 242) -> int:
    return trace_status(status).get("fg", default)


def mode_text(mode: str, default: str = "") -> str:
    return (data_table("mode_text", {}) or {}).get(mode, default)


def mode_style_fg(mode: str, default: int = 242) -> int:
    return (data_table("mode_style", {}) or {}).get(mode, default)


def tool_display_name_map() -> dict:
    return data_table("tool_display_name", {}) or {}


def tool_display_name(tool_name: str, default: str = "") -> str:
    return tool_display_name_map().get(tool_name, default or tool_name)


def admonition_styles() -> dict:
    return data_table("admonition_style", {}) or {}


def admonition_style(adm_type: str, default: dict | None = None) -> dict:
    styles = admonition_styles()
    fallback = styles.get("NOTE", {}) if default is None else default
    return styles.get(str(adm_type or "").upper(), fallback)


def spinner_presets() -> dict:
    return data_table("spinner_frames", {}) or {}


def spinner_preset(name: str, default: str = "") -> str:
    return spinner_presets().get(name, default)


def inline_spinner_frames() -> str:
    frames = data_table("inline_spinner_frames", "")
    if isinstance(frames, str) and frames:
        return frames
    return INLINE_SPINNER_FRAMES


def config_entry_descs() -> dict:
    return data_table("config_entry_desc", {}) or {}


def config_entry_options() -> dict:
    return data_table("config_entry_option", {}) or {}


def trace_kind_order() -> list:
    return list(data_table("trace_kind_order", []) or [])


def trace_block_kind() -> dict:
    return data_table("trace_block_kind", {}) or {}


def message_role_icons() -> dict:
    return data_table("message_role_icon", {}) or {}


def border_chars() -> dict:
    return data_table("border_chars", {}) or {}


def border_object_default() -> dict:
    return data_table("border_object_default", {}) or {}


def billing_defaults() -> dict:
    return data_table("billing_default", {}) or {}


def metric_defaults() -> dict:
    return data_table("metric_defaults", {}) or {}


def ui_defaults() -> dict:
    return data_table("ui_defaults", {}) or {}


def ui_default(key: str, default=None):
    """按 key 取 UI 默认参数（缺席返回 ``default``）。"""
    return ui_defaults().get(key, default)


def semantic_colors() -> dict:
    return data_table("semantic_color", {}) or {}


def semantic_color(name: str, default=None):
    """按槽位名取语义色 256 色号（缺席返回 ``default``）。"""
    return semantic_colors().get(name, default)


def gradient_params() -> dict:
    return data_table("gradient_stops", {}) or {}


def gradient_param(name: str, default=None):
    return gradient_params().get(name, default)


def shell_detect() -> dict:
    return data_table("shell_detect", {}) or {}


def shell_detect_table(name: str, default=None):
    return shell_detect().get(name, default)


def http_error_hints() -> dict:
    return data_table("http_error_hint", {}) or {}


def http_error_hint(status_code, default=None):
    """按状态码取用户可操作提示（int/str 键兼容）。"""
    hints = http_error_hints()
    return hints.get(str(status_code), hints.get(status_code, default))


def badge_metrics() -> dict:
    return data_table("badge_metrics", {}) or {}


def kitty_protocol() -> dict:
    return data_table("kitty_protocol", {}) or {}


def nested_bullets() -> list:
    """渲染器嵌套列表符号（按深度取；缺席时回退内置快照）。"""
    value = data_table("nested_bullet", None)
    if isinstance(value, (list, tuple)) and value:
        return list(value)
    return list(NESTED_BULLET_SYMBOLS)


def diff_style(key: str, default=None):
    """按 key 取 diff 渲染样式规格（缺席返回 ``default``）。"""
    return (data_table("diff_style", {}) or {}).get(key, default)


def trace_style(key: str, default=None):
    """按 key 取轨迹视图样式规格（缺席返回 ``default``）。"""
    return (data_table("trace_style", {}) or {}).get(key, default)


def trace_keymap() -> list:
    """轨迹视图快捷键速查表（帮助面板内容；缺席回退空列表）。"""
    return list(data_table("trace_keymap", []) or [])


def plugin_keymap() -> list:
    """插件视图快捷键速查表（``?`` 帮助面板内容；缺席回退空列表）。"""
    return list(data_table("plugin_keymap", []) or [])


def config_keymap() -> list:
    """配置中心快捷键速查表（``?`` 帮助面板内容；缺席回退空列表）。"""
    return list(data_table("config_keymap", []) or [])


def model_keymap() -> list:
    """模型选择器快捷键速查表（``?`` 帮助面板内容；缺席回退空列表）。"""
    return list(data_table("model_keymap", []) or [])


def model_patterns() -> dict:
    return data_table("model_patterns", {}) or {}


def model_pattern(key: str, default=None):
    """按 key 取模型匹配模式表项（缺席返回 ``default``）。"""
    return model_patterns().get(key, default)


def clear() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _invalidate()


def reset() -> None:
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()
        _invalidate()


__all__ = [
    "DataTable",
    "LiveMapping",
    "builtin_data_ids",
    "default_data_table",
    "active_data_tables",
    "register_builtin_data",
    "unregister_builtin_data",
    "set_managed_builtin_data",
    "managed_data_ids",
    "disable_builtin_data",
    "register_data_table",
    "unregister_data_table",
    "data_table",
    "emoji_map",
    "subscript_map",
    "superscript_map",
    "circled_digits",
    "bullets",
    "html_tag_colors",
    "html_tag_color",
    "trace_kind",
    "trace_kind_icon",
    "trace_kind_name",
    "trace_kind_fg",
    "trace_status",
    "trace_status_icon",
    "trace_status_fg",
    "mode_text",
    "mode_style_fg",
    "tool_display_name_map",
    "tool_display_name",
    "admonition_styles",
    "admonition_style",
    "spinner_presets",
    "spinner_preset",
    "inline_spinner_frames",
    "config_entry_descs",
    "config_entry_options",
    "trace_kind_order",
    "trace_block_kind",
    "message_role_icons",
    "border_chars",
    "border_object_default",
    "billing_defaults",
    "metric_defaults",
    "ui_defaults",
    "ui_default",
    "semantic_colors",
    "semantic_color",
    "gradient_params",
    "gradient_param",
    "shell_detect",
    "shell_detect_table",
    "http_error_hints",
    "http_error_hint",
    "badge_metrics",
    "kitty_protocol",
    "nested_bullets",
    "diff_style",
    "trace_style",
    "trace_keymap",
    "plugin_keymap",
    "config_keymap",
    "model_keymap",
    "model_patterns",
    "model_pattern",
    "TRACE_KIND_ORDER_DATA",
    "TRACE_BLOCK_KIND_MAP",
    "TRACE_KEYMAP_DATA",
    "MODEL_KEYMAP_DATA",
    "MESSAGE_ROLE_ICON_MAP",
    "BORDER_CHARS_MAP",
    "BORDER_OBJECT_DEFAULT",
    "TOOL_DISPLAY_NAME_MAP",
    "ADMONITION_STYLE_MAP",
    "SPINNER_PRESET_FRAMES",
    "INLINE_SPINNER_FRAMES",
    "CONFIG_ENTRY_DESC_MAP",
    "CONFIG_ENTRY_OPTION_MAP",
    "clear",
    "reset",
]
