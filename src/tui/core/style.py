"""Style 不可变样式描述器 — 统一样式管理和 ANSI 序列构建。

提供：
  - Style:        不可变样式描述器，封装前景色/背景色/加粗/斜体/暗淡/下划线
  - StyledText:   带样式的文本片段，一件渲染
  - StyleSheet:   命名样式注册表，类似 BreathPalette 的集中管理模式

设计原则：
  - 不可变：Style 为冻结 dataclass，所有合并操作返回新实例
  - 纯函数：to_ansi()/apply() 无副作用，结果可缓存
  - 最小依赖：仅依赖 src/tui/_const.py（Layer 0 常量/枚举），不依赖上层 TUI 模块
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import ClassVar
# TrueColor 用于 dataclass 字段类型注解（from __future__ import annotations
# 下为字符串，不求值——显式导入消除 pyflakes 未定义名告警；color 模块不反向
# 依赖 style，无循环导入）。
from .color import TrueColor
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import rich  # noqa: F401  # 字符串注解 "rich.style.Style" 的类型检查
# 紧急路径 ANSI 序列常量 — 唯一真源在 _const.py（Layer 0），此处 re-export 保持兼容
from src.tui._const import (
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_YELLOW,
    ANSI_EMERGENCY_RESET,
    ANSI_EMERGENCY_CURSOR_BOTTOM,
    _SEMANTIC_COLOR,
)


__all__: list[str] = [
    "Style",
    "StyledText",
    "StyleSheet",
    # 命名色号常量
    "FADE_COLOR_DARK",
    "FADE_COLOR_MID",
    "SEP_COLOR_START",
    "SEP_COLOR_END",
    # 紧急路径 ANSI 序列常量
    "ANSI_EMERGENCY_RED",
    "ANSI_EMERGENCY_YELLOW",
    "ANSI_EMERGENCY_RESET",
    "ANSI_EMERGENCY_CURSOR_BOTTOM",
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
        strikethrough: 是否删除线（React Ink `<Text strikethrough>`）。
        inverse: 是否反转前景/背景色（React Ink `<Text inverse>`）。
    """

    fg: int | TrueColor | None = None
    bg: int | TrueColor | None = None
    bold: bool = False
    italic: bool = False
    dim: bool = False
    underline: bool = False
    strikethrough: bool = False
    inverse: bool = False

    @lru_cache(maxsize=1024)
    def to_ansi(self) -> str:
        """构建 ANSI 转义序列。

        按 ``bold → dim → italic → underline → inverse → strikethrough → fg → bg``
        顺序组装，RESET 由调用方（apply）统一追加。

        fg/bg 为 TrueColor 时使用 24-bit ANSI 序列（38;2 / 48;2），
        为 int 时使用 256 色序列（38;5 / 48;5），保持向后兼容。

        ★ 性能（PERF-7）：frozen dataclass 确定性输出 → lru_cache 缓存。
        Style 为不可变值对象（fg/bg 为 int/TrueColor/None + 6 布尔），
        实例数量有界（颜色组合 + 字型组合），maxsize=1024 防无限增长；
        缓存命中免每帧重复构建 ANSI 序列（live 区渲染热路径）。
        返回纯函数结果，无副作用，行为与未缓存完全一致。

        Returns:
            ANSI 转义序列，不含 RESET。所有属性均为默认值时返回空字符串。
        """
        # 延迟导入避免模块加载循环（仅导入一次）
        from .color import TrueColor as _TC
        parts: list[str] = []
        if self.bold:
            parts.append("\033[1m")
        if self.dim:
            parts.append("\033[2m")
        if self.italic:
            parts.append("\033[3m")
        if self.underline:
            parts.append("\033[4m")
        if self.inverse:
            parts.append("\033[7m")
        if self.strikethrough:
            parts.append("\033[9m")
        if self.fg is not None:
            if isinstance(self.fg, _TC):
                parts.append(self.fg.to_ansi_fg())
            else:
                parts.append(f"\033[38;5;{self.fg}m")
        if self.bg is not None:
            if isinstance(self.bg, _TC):
                parts.append(self.bg.to_ansi_bg())
            else:
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
        bold/italic/dim/underline/strikethrough/inverse 为 True 时覆盖，
        False 保留当前值。

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
            strikethrough=other.strikethrough or self.strikethrough,
            inverse=other.inverse or self.inverse,
        )

    @classmethod
    def with_props(cls, fg=None, bg=None, bold=None, italic=None, dim=None, underline=None,
                   strikethrough=None, inverse=None):
        """仅设置非 None 参数创建新 Style。

        Args:
            fg: 前景色（可选）。
            bg: 背景色（可选）。
            bold: 是否加粗（可选）。
            italic: 是否斜体（可选）。
            dim: 是否暗淡（可选）。
            underline: 是否下划线（可选）。
            strikethrough: 是否删除线（可选）。
            inverse: 是否反转（可选）。

        Returns:
            新的 Style 实例。
        """
        return cls(
            fg=fg if fg is not None else None,
            bg=bg if bg is not None else None,
            bold=bold if bold is not None else False,
            italic=italic if italic is not None else False,
            dim=dim if dim is not None else False,
            underline=underline if underline is not None else False,
            strikethrough=strikethrough if strikethrough is not None else False,
            inverse=inverse if inverse is not None else False,
        )

    @classmethod
    def from_dict(cls, d: dict) -> Style:
        """从字典创建 Style。

        支持的键: fg, bg, bold, italic, dim, underline, strikethrough, inverse。
        忽略字典中不存在的键。

        Args:
            d: 属性字典。

        Returns:
            新的 Style 实例。
        """
        return cls(
            fg=d.get('fg'),
            bg=d.get('bg'),
            bold=d.get('bold', False),
            italic=d.get('italic', False),
            dim=d.get('dim', False),
            underline=d.get('underline', False),
            strikethrough=d.get('strikethrough', False),
            inverse=d.get('inverse', False),
        )

    def extend(self, **overrides) -> Style:
        """扩展样式，返回新实例。

        类似 merge() 但使用命名参数风格。

        Args:
            **overrides: 要覆盖的样式属性（fg/bg/bold/italic/dim/underline/
                strikethrough/inverse）。

        Returns:
            新的 Style 实例。
        """
        return Style(
            fg=overrides.get('fg', self.fg),
            bg=overrides.get('bg', self.bg),
            bold=overrides.get('bold', self.bold),
            italic=overrides.get('italic', self.italic),
            dim=overrides.get('dim', self.dim),
            underline=overrides.get('underline', self.underline),
            strikethrough=overrides.get('strikethrough', self.strikethrough),
            inverse=overrides.get('inverse', self.inverse),
        )

    # ── Rich 桥接方法 ──

    def to_rich(self) -> "rich.style.Style":
        """将 tui.core.Style 转换为 rich.style.Style（桥接方法）。

        桥接方法 — 用于需要 ``rich.style.Style`` 的旧代码路径。
        延迟导入 rich 和 TrueColor，避免模块级 third-party 依赖。

        Returns:
            等效的 ``rich.style.Style`` 实例。
        """
        # 方法体内延迟导入，避免模块级 third-party 依赖
        from rich.style import Style as RichStyle
        from rich.color import Color as RichColor
        from .color import TrueColor as _TC

        kwargs: dict = {}

        # fg: int(256色号) / TrueColor(r,g,b) / None
        if self.fg is not None:
            if isinstance(self.fg, _TC):
                kwargs["color"] = RichColor.from_rgb(self.fg.r, self.fg.g, self.fg.b)
            else:
                # int 256 色号 → 使用 Rich Color.from_ansi
                kwargs["color"] = RichColor.from_ansi(self.fg)

        # bg: 同上
        if self.bg is not None:
            if isinstance(self.bg, _TC):
                kwargs["bgcolor"] = RichColor.from_rgb(self.bg.r, self.bg.g, self.bg.b)
            else:
                kwargs["bgcolor"] = RichColor.from_ansi(self.bg)

        # 布尔属性直接映射
        if self.bold:
            kwargs["bold"] = True
        if self.dim:
            kwargs["dim"] = True
        if self.italic:
            kwargs["italic"] = True
        if self.underline:
            kwargs["underline"] = True
        if self.strikethrough:
            kwargs["strike"] = True
        if self.inverse:
            kwargs["reverse"] = True

        return RichStyle(**kwargs)

    @classmethod
    def from_rich(cls, style: "rich.style.Style") -> Style:
        """从 rich.style.Style 创建 tui.core.Style（桥接方法）。

        桥接方法 — 从 ``rich.style.Style`` 创建 tui.core.Style。
        延迟导入，避免模块级 third-party 依赖。

        Args:
            style: ``rich.style.Style`` 实例。

        Returns:
            等效的 ``tui.core.Style`` 实例。
        """
        # 方法体内延迟导入
        from .color import TrueColor as _TC

        # ── 前景色 ──
        fg = None
        if style.color is not None:
            if style.color.triplet is not None:
                # TrueColor
                fg = _TC(style.color.triplet.red, style.color.triplet.green, style.color.triplet.blue)
            elif style.color.number is not None:
                # 256 色号
                fg = style.color.number

        # ── 背景色 ──
        bg = None
        if style.bgcolor is not None:
            if style.bgcolor.triplet is not None:
                bg = _TC(style.bgcolor.triplet.red, style.bgcolor.triplet.green, style.bgcolor.triplet.blue)
            elif style.bgcolor.number is not None:
                bg = style.bgcolor.number

        # ── 布尔属性（rich 的布尔属性可能为 None，bool(None) → False） ──
        return cls(
            fg=fg,
            bg=bg,
            bold=bool(style.bold),
            italic=bool(style.italic),
            dim=bool(style.dim),
            underline=bool(style.underline),
            strikethrough=bool(getattr(style, "strike", False)),
            inverse=bool(getattr(style, "reverse", False)),
        )

    def __bool__(self) -> bool:
        """判断样式是否非空（至少有一个属性被设置）。"""
        return (
            self.fg is not None
            or self.bg is not None
            or self.bold
            or self.italic
            or self.dim
            or self.underline
            or self.strikethrough
            or self.inverse
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
# 方向3 步骤15：StyleSheet 为独立命名样式注册表（语义词汇与 _SEMANTIC_COLOR
# 槽位不同源——如 "success"=47 与槽 tool_ok=41 语义不同不强制合并）；仅
# "error"（196）与槽 tool_fail 同值同语义，引用槽位防漂移（零视觉变化）。

StyleSheet.register_many({
    # ── 基础字型 ──
    "dim":       Style(dim=True),
    "bold":      Style(bold=True),
    "italic":    Style(italic=True),
    "underline": Style(underline=True),
    "bold_dim":  Style(bold=True, dim=True),
    "dim_italic": Style(dim=True, italic=True),
    "bold_italic": Style(bold=True, italic=True),
    # ── 语义色 ──
    "error":     Style(fg=_SEMANTIC_COLOR["tool_fail"], bold=True),
    "success":   Style(fg=47),
    "warn":      Style(fg=220),
    "info":      Style(fg=45),
    "muted":     Style(fg=244),
    "border_breath": Style(fg=23),
    # ── 差异渲染语义色 ──
    "diff_add":  Style(fg=41),
    "diff_del":  Style(fg=196),
    "diff_ctx":  Style(fg=244),
    # ── 消息角色图标色 ──
    "user_icon": Style(fg=81),
    "asst_icon": Style(fg=47),
    "tool_icon": Style(fg=220),
    # ── 工具输出文本色 ──
    "tool_txt":  Style(fg=242),
    # ── 装饰色 ──
    "separator": Style(fg=239),
    "highlight": Style(fg=45),
    "accent":    Style(fg=221),
    "deco":      Style(fg=242),
    "neon":      Style(fg=51, bold=True),
    # ── 树视图色 ──
    "tree_branch": Style(fg=239),   # 树分支灰色
    "tree_leaf":   Style(fg=45),    # 树叶青色
})

# ════════════════════════════════════════════════════════
# 命名色号常量（消除魔法数字 — 供 text_utils 等模块引用）
# ════════════════════════════════════════════════════════

FADE_COLOR_DARK: int = 238      # 渐显暗灰（build_fade_in_ansi 第 0 帧）
FADE_COLOR_MID: int = 244       # 渐显中灰（build_fade_in_ansi 第 1 帧）
SEP_COLOR_START: int = 45       # 分隔线起始色 — 亮青（make_sep_gradient 默认起始）
SEP_COLOR_END: int = 237        # 分隔线结束色 — 深灰（make_sep_gradient 默认结束）

# ════════════════════════════════════════════════════════
# 紧急路径 ANSI 序列常量（直写终端，绕过 Rich 管线）
# ════════════════════════════════════════════════════════
# 用于队列满/render 崩溃等无法通过正常渲染管线输出的场景。
# 唯一真源在 src/tui/_const.py（Layer 0），本文件顶部导入并 re-export（步骤 4.5）。
