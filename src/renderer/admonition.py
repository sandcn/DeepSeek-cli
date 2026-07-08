"""admonition — 告示块（Admonition）样式配置与渲染逻辑。

告示类型包括：NOTE、TIP、WARNING、CAUTION、IMPORTANT 等。
每种类型可配置颜色、图标和标签文字。
"""

from __future__ import annotations


# ═══════════════════════════════════════════════════════════
# 告示样式配置
# ═══════════════════════════════════════════════════════════

ADMONITION_STYLES: dict[str, dict[str, str]] = {
    "NOTE":      {"color": "blue",       "icon": "ℹ️",  "label": "NOTE"},
    "TIP":       {"color": "green",      "icon": "💡",  "label": "TIP"},
    "WARNING":   {"color": "yellow",     "icon": "⚠️",  "label": "WARNING"},
    "CAUTION":   {"color": "red",        "icon": "⚡",  "label": "CAUTION"},
    "IMPORTANT": {"color": "magenta",    "icon": "❗",  "label": "IMPORTANT"},
    "INFO":      {"color": "cyan",       "icon": "ℹ️",  "label": "INFO"},
    "SUCCESS":   {"color": "green",      "icon": "✅",  "label": "SUCCESS"},
    "QUESTION":  {"color": "bright_blue","icon": "❓",  "label": "QUESTION"},
    "BUG":       {"color": "red",        "icon": "🐛",  "label": "BUG"},
    "DANGER":    {"color": "red",        "icon": "🔥",  "label": "DANGER"},
    "CITE":      {"color": "bright_black","icon": "📖",  "label": "CITE"},
}


def get_admonition_config(adm_type: str) -> dict[str, str]:
    """获取告示类型的样式配置，未知类型默认降级为 NOTE。"""
    return ADMONITION_STYLES.get(adm_type.upper(), ADMONITION_STYLES["NOTE"])
