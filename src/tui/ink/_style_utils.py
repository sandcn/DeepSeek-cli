"""TEXT shorthand 样式解析 — color/bold/transform → Style。

模块边界（2026-08-05 架构优化）：从 ``ink/helpers.py`` 拆分——样式/变换
解析为独立职责（纯函数，依赖 ``core.style.Style``），供 ``_layout_measure``
（TEXT 测量）与 ``helpers`` 门面共享。
"""

from __future__ import annotations

from src.tui.core.style import Style


def _parse_color(value):
    """将 shorthand 颜色 prop 归一化为 Style.fg/bg 可用的颜色值。

    react-ink 允许颜色名（'red'/'green' 等）与 256 色号 int。支持常见
    X11 颜色名 → 256 色号映射（简化集）；非 int 且无映射时返回 None
    （调用方保留既有样式）。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        named = {
            "black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4,
            "magenta": 5, "cyan": 6, "white": 7, "brightBlack": 8,
            "gray": 8, "brightRed": 9, "brightGreen": 10, "brightYellow": 11,
            "brightBlue": 12, "brightMagenta": 13, "brightCyan": 14,
            "brightWhite": 15, "grey": 8,
        }
        return named.get(value.lower())
    return None


def resolve_text_style(props) -> Style | None:
    """解析 TEXT shorthand 样式属性（react-ink 语义）为 Style。

    支持：``color``（fg 别名）、``backgroundColor``（bg 别名）、
    ``bold``/``italic``/``underline``/``strikethrough``/``inverse``/``dim``/``dimColor``。
    显式 ``style`` prop 与 shorthand 合并——shorthand 覆盖 style 对应字段
    （color/bold 等显式存在时优先）。

    ``dimColor``（react-ink 特有）：布尔 prop——True 时以更暗的 dim 颜色
    渲染文本（``dim=True`` + 指定暗色 fg，若未指定其它 fg 则用暗灰 238）。
    False/缺省无效果。与 ``dim`` 并存时 ``dim`` 控制 dim 属性、``dimColor``
    控制颜色（react-ink 语义：dimColor 主要影响颜色）。

    Args:
        props: TEXT fiber props。

    Returns:
        合并后的 Style；无任何样式属性时返回 None。
    """
    base = props.get("style")
    color = _parse_color(props.get("color"))
    bg = _parse_color(props.get("backgroundColor"))
    bold = props.get("bold")
    italic = props.get("italic")
    underline = props.get("underline")
    strikethrough = props.get("strikethrough")
    inverse = props.get("inverse")
    dim = props.get("dim")
    dim_color = props.get("dimColor")
    has_any = (
        color is not None or bg is not None
        or bold is not None or italic is not None
        or underline is not None or strikethrough is not None
        or inverse is not None or dim is not None
        or dim_color is not None
        or base is not None
    )
    if not has_any:
        return None
    # dimColor 指定暗色 fg：仅当未显式设置 color 时生效（否则 color 优先）
    dim_fg = 238 if dim_color else None
    merged = Style(
        fg=color if color is not None else (
            dim_fg if dim_color else (base.fg if base is not None else None)
        ),
        bg=bg if bg is not None else (base.bg if base is not None else None),
        bold=bool(bold) if bold is not None else (base.bold if base is not None else False),
        italic=bool(italic) if italic is not None else (base.italic if base is not None else False),
        underline=bool(underline) if underline is not None else (base.underline if base is not None else False),
        strikethrough=bool(strikethrough) if strikethrough is not None else (
            base.strikethrough if base is not None else False
        ),
        inverse=bool(inverse) if inverse is not None else (
            base.inverse if base is not None else False
        ),
        dim=bool(dim) if dim is not None else (
            True if dim_color else (base.dim if base is not None else False)
        ),
    )
    if not merged:
        return None
    return merged


def apply_text_transform(text: str, transform: str | None) -> str:
    """应用 TEXT ``transform`` prop（react-ink 语义）。

    ``uppercase``/``lowercase``/``capitalize``；未知值或 None 原样返回。

    Args:
        text: 原始文本。
        transform: 变换模式。

    Returns:
        变换后的文本。
    """
    if not transform:
        return text
    if transform == "uppercase":
        return text.upper()
    if transform == "lowercase":
        return text.lower()
    if transform == "capitalize":
        return text.capitalize()
    return text


__all__ = ["_parse_color", "resolve_text_style", "apply_text_transform"]
