"""Style 不可变样式描述器 — 统一样式管理和 ANSI 序列构建。

提供：
  - Style:        不可变样式描述器，封装前景色/背景色/加粗/斜体/暗淡/下划线
  - StyledText:   带样式的文本片段，一件渲染
  - StyleSheet:   命名样式注册表，类似 BreathPalette 的集中管理模式

设计原则：
  - 不可变：Style 为冻结 dataclass，所有合并操作返回新实例
  - 纯函数：to_ansi()/apply() 无副作用，结果可缓存
  - 延迟导入：from_theme() 方法体内延迟加载 THEME，避免模块加载顺序依赖
  - 零依赖：仅使用标准库，不依赖 src/tui/ 上层模块
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


__all__: list[str] = [
    "Style",
    "StyledText",
    "StyleSheet",
]


# ═══════════════════════════════════════════════════════════
# Style — 不可变样式描述器
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Style:
    """不可变样式描述器，封装文本样式属性并构建 ANSI 转义序列。

    所有样式属性有明确的默认值（None/False），
    合并操作时 non-None/True 字段覆盖当前值。

    Args:
        fg: 前景色 256 色号（0-255），None 为不设置。
        bg: 背景色 256 色号（0-255），None 为不设置。
        bold: 是否加粗。
        italic: 是否斜体。
        dim: 是否暗淡（减弱亮度）。
        underline: 是否下划线。
    """

    fg: int | None = None
    bg: int | None = None
    bold: bool = False
    italic: bool = False
    dim: bool = False
    underline: bool = False

    def to_ansi(self) -> str:
        """构建 ANSI 转义序列。

        按 ``bold → dim → italic → underline → fg → bg`` 顺序组装，
        RESET 由调用方（apply）统一追加。

        Returns:
            ANSI 转义序列，不含 RESET。所有属性均为默认值时返回空字符串。
        """
        parts: list[str] = []
        if self.bold:
            parts.append("\033[1m")
        if self.dim:
            parts.append("\033[2m")
        if self.italic:
            parts.append("\033[3m")
        if self.underline:
            parts.append("\033[4m")
        if self.fg is not None:
            parts.append(f"\033[38;5;{self.fg}m")
        if self.bg is not None:
            parts.append(f"\033[48;5;{self.bg}m")
        return "".join(parts)

    def apply(self, text: str) -> str:
        """对文本应用样式，返回带 ANSI 包裹的字符串。

        格式：``{to_ansi()}{text}\033[0m``
        无样式属性时原样返回 text（不添加空转义序列）。

        Args:
            text: 要应用样式的文本。

        Returns:
            带 ANSI 样式包裹的字符串。
        """
        ansi = self.to_ansi()
        if not ansi:
            return text
        return f"{ansi}{text}\033[0m"

    def merge(self, other: Style) -> Style:
        """合并两个样式，返回新 Style 实例。

        合并规则：other 的 non-None/True 字段覆盖当前值。
        fg/bg 为 None 时保留当前值，非 None 时覆盖。
        bold/italic/dim/underline 为 True 时覆盖，False 保留当前值。

        Args:
            other: 要合并的样式（优先级高于当前）。

        Returns:
            合并后的新 Style 实例（不修改原实例）。
        """
        return Style(
            fg=other.fg if other.fg is not None else self.fg,
            bg=other.bg if other.bg is not None else self.bg,
            bold=other.bold or self.bold,
            italic=other.italic or self.italic,
            dim=other.dim or self.dim,
            underline=other.underline or self.underline,
        )

    @classmethod
    def from_theme(cls, key: str) -> Style:
        """从 THEME 语义键解析构建 Style。

        解析 THEME 字典中的 ANSI 前景色/背景色码，
        提取 256 色号构建 Style。支持 ``38;5;N`` 和 ``48;5;N`` 格式。

        Args:
            key: THEME 字典中的语义键名（如 "border_breath" / "user"）。

        Returns:
            解析后的 Style 实例。键不存在或解析失败时返回空 Style。
        """
        import re
        # 方法体内延迟导入，避免模块加载循环
        from .theme import THEME

        color_str = THEME.get(key, "")
        if not color_str:
            return cls()

        fg: int | None = None
        bg: int | None = None

        fg_match = re.search(r"38;5;(\d+)", color_str)
        if fg_match:
            fg = int(fg_match.group(1))

        bg_match = re.search(r"48;5;(\d+)", color_str)
        if bg_match:
            bg = int(bg_match.group(1))

        # 检测粗体/暗淡等 SGR 参数
        bold = "1m" in color_str and "38;5" not in color_str[:color_str.index("1m") - 2] if "1m" in color_str else False
        dim = "2m" in color_str
        italic = "3m" in color_str
        underline = "4m" in color_str

        return cls(fg=fg, bg=bg, bold=bold, italic=italic, dim=dim, underline=underline)

    def __bool__(self) -> bool:
        """判断样式是否非空（至少有一个属性被设置）。"""
        return (
            self.fg is not None
            or self.bg is not None
            or self.bold
            or self.italic
            or self.dim
            or self.underline
        )


# ═══════════════════════════════════════════════════════════
# StyledText — 带样式的文本片段
# ═══════════════════════════════════════════════════════════


@dataclass
class StyledText:
    """带样式的文本片段。

    封装文本内容和样式，提供渲染方法。
    无样式时直接返回原文本，不引入额外 ANSI 序列。

    Args:
        text: 文本内容。
        style: 样式描述器，None 表示无样式。
    """

    text: str
    style: Style | None = None

    def render(self) -> str:
        """渲染为 ANSI 字符串。

        有 style 时调用 ``style.apply(text)``，
        无 style 时直接返回 text（零开销）。

        Returns:
            渲染后的字符串。
        """
        if self.style:
            return self.style.apply(self.text)
        return self.text

    def __bool__(self) -> bool:
        """判断是否包含非空文本。"""
        return bool(self.text)


# ═══════════════════════════════════════════════════════════
# StyleSheet — 命名样式注册表
# ═══════════════════════════════════════════════════════════


class StyleSheet:
    """命名样式注册表 — 集中管理所有命名 Style 对象。

    模式参考 ``BreathPalette``，但注册 Style 对象而非颜色列表。
    模块加载时自动注册一组预定义基本样式。

    线程安全：所有操作为只读字典访问 + 纯函数。
    """

    _registry: ClassVar[dict[str, Style]] = {}

    @classmethod
    def register(cls, name: str, style: Style) -> None:
        """注册命名样式。

        Args:
            name: 样式名称（如 "dim"、"bold"）。
            style: Style 实例。
        """
        cls._registry[name] = style

    @classmethod
    def register_many(cls, styles: dict[str, Style]) -> None:
        """批量注册样式。

        Args:
            styles: 名称到 Style 的映射字典。
        """
        cls._registry.update(styles)

    @classmethod
    def get(cls, name: str) -> Style | None:
        """获取命名样式，不存在时返回 None。

        Args:
            name: 样式名称。

        Returns:
            Style 实例，不存在时返回 None。
        """
        return cls._registry.get(name)

    @classmethod
    def resolve(cls, name: str, default: Style | None = None) -> Style:
        """获取命名样式，不存在时返回兜底样式。

        Args:
            name: 样式名称。
            default: 兜底样式，默认 None（返回空 Style）。

        Returns:
            Style 实例。不存在时返回 default 或空 Style。
        """
        result = cls._registry.get(name)
        if result is not None:
            return result
        return default if default is not None else Style()

    @classmethod
    def has(cls, name: str) -> bool:
        """检查命名样式是否已注册。

        Args:
            name: 样式名称。

        Returns:
            是否已注册。
        """
        return name in cls._registry

    @classmethod
    def clear(cls) -> None:
        """清空注册表（供测试使用）。"""
        cls._registry.clear()

    @classmethod
    def all_names(cls) -> list[str]:
        """获取所有已注册的样式名称列表。

        Returns:
            样式名称列表。
        """
        return list(cls._registry.keys())


# ════════════════════════════════════════════════════════
# 预注册基本样式（模块加载时自动注册）
# ════════════════════════════════════════════════════════

StyleSheet.register_many({
    "dim":       Style(dim=True),
    "bold":      Style(bold=True),
    "italic":    Style(italic=True),
    "underline": Style(underline=True),
    "bold_dim":  Style(bold=True, dim=True),
    "dim_italic": Style(dim=True, italic=True),
})
