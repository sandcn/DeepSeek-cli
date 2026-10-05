"""admonition — 告示块（Admonition）样式配置与渲染逻辑。

告示类型包括：NOTE、TIP、WARNING、CAUTION、IMPORTANT 等。
每种类型可配置颜色、图标和标签文字。

「一切皆插件」：样式表已上移为表现层数据注册表（``presentation_data`` →
``admonition_style`` 表），由清单中的独立插件条目注册，可按 Profile/Patch/
Overlay 覆盖（整表替换）或禁用；``ADMONITION_STYLES`` 为**实时委托视图**，
``get_admonition_config`` 实时查询（未知类型降级为 NOTE）。
"""

from __future__ import annotations

from src.presentation_data import LiveMapping, admonition_style

ADMONITION_STYLES = LiveMapping("admonition_style")


def get_admonition_config(adm_type: str) -> dict[str, str]:
    """获取告示类型的样式配置，未知类型默认降级为 NOTE。"""
    return admonition_style(adm_type)


__all__ = ["ADMONITION_STYLES", "get_admonition_config"]
