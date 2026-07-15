"""
语义化主题颜色映射 — 支持多主题切换。

每个主题预设为 {语义键: ANSI颜色码} 映射。
通过 set_theme(name) 动态切换，THEME 始终保持为当前活动主题的引用。

所有颜色值已升级为 256 色 ANSI 码（格式 \033[38;5;Nm 或 \033[48;5;Nm），
保持与 src/core/constants.py 中 _256 后缀常量一致。

从 src/ui/theme.py 提取 — TUI 核心主题系统。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# ══════════════════════════════════════════════════════════
# 层次化 Theme 类
# ══════════════════════════════════════════════════════════


class Theme:
    """层次化主题类 — 支持链式继承、覆盖和语义键查找。

    设计模式: 装饰器 — ``with_overrides`` 返回增强 Theme（以当前实例为 parent）。

    支持控件级主题覆盖和主题继承链，三级覆盖：
    ``widget theme → parent theme → ... → global theme``

    Args:
        name: 主题名称。
        parent: 父主题实例（可选），未命中键时递归查找。
        colors: 语义键 → ANSI 颜色码映射。
        styles: 样式键 → 样式值映射（预留扩展）。

    Raises:
        ValueError: 检测到循环继承链。
    """

    def __init__(
        self,
        name: str,
        parent: Optional["Theme"] = None,
        colors: Optional[Dict[str, str]] = None,
        styles: Optional[Dict[str, str]] = None,
    ) -> None:
        self._name = name
        self._colors: Dict[str, str] = dict(colors) if colors else {}
        self._styles: Dict[str, str] = dict(styles) if styles else {}
        if parent is not None:
            self._check_ancestor(parent)
        self._parent = parent

    # ── 属性 ─────────────────────────────────────────

    @property
    def name(self) -> str:
        """主题名称。"""
        return self._name

    @property
    def parent(self) -> Optional["Theme"]:
        """父主题（可能为 None）。"""
        return self._parent

    @property
    def colors(self) -> Dict[str, str]:
        """返回自身颜色字典的副本（不含继承）。"""
        return dict(self._colors)

    @property
    def styles(self) -> Dict[str, str]:
        """返回自身样式字典的副本（不含继承）。"""
        return dict(self._styles)

    @property
    def all_keys(self) -> set:
        """返回所有可访问的语义键（含继承链）。"""
        keys: set = set(self._colors.keys())
        if self._parent is not None:
            keys.update(self._parent.all_keys)
        return keys

    # ── 查找方法 ─────────────────────────────────────

    def get(self, key: str, default: str = "") -> str:
        """获取颜色码。

        先在自身 colors 中查找，未命中则递归查找 parent。
        整条链均未命中返回 default。

        Args:
            key: 语义键。
            default: 未找到时的回退值。

        Returns:
            ANSI 颜色码字符串。
        """
        if key in self._colors:
            return self._colors[key]
        if self._parent is not None:
            return self._parent.get(key, default)
        return default

    def get_style(self, key: str, default: str = "") -> str:
        """获取样式值（查找逻辑同 ``get()``）。

        Args:
            key: 样式键。
            default: 未找到时的回退值。

        Returns:
            样式值字符串。
        """
        if key in self._styles:
            return self._styles[key]
        if self._parent is not None:
            return self._parent.get_style(key, default)
        return default

    # ── 字典协议 ─────────────────────────────────────

    def __getitem__(self, key: str) -> str:
        """字典式访问 — 未命中抛出 KeyError。"""
        result = self.get(key)
        if not result:
            raise KeyError(key)
        return result

    def __contains__(self, key: str) -> bool:
        """支持 ``key in theme`` 检查（含继承链）。"""
        if key in self._colors:
            return True
        if self._parent is not None:
            return key in self._parent
        return False

    def __repr__(self) -> str:
        chain = self._name
        p = self._parent
        while p is not None:
            chain += f" → {p._name}"
            p = p._parent
        return f"Theme({chain})"

    # ── 覆盖与工厂 ──────────────────────────────────

    def with_overrides(
        self,
        colors: Optional[Dict[str, str]] = None,
        styles: Optional[Dict[str, str]] = None,
    ) -> "Theme":
        """创建新 Theme 实例，以当前实例为 parent，覆盖部分颜色/样式。

        原实例不受影响（不可变模式）。

        Args:
            colors: 要覆盖的颜色映射。
            styles: 要覆盖的样式映射。

        Returns:
            新 Theme 实例（parent 指向 self）。
        """
        return Theme(
            name=f"{self._name} (overridden)",
            parent=self,
            colors=colors,
            styles=styles,
        )

    @classmethod
    def from_dict(
        cls,
        name: str,
        d: Dict[str, str],
        parent: Optional["Theme"] = None,
    ) -> "Theme":
        """从字典创建 Theme 实例 — 兼容现有 ``THEMES`` dict 格式。

        Args:
            name: 主题名称。
            d: 语义键 → ANSI 颜色码映射。
            parent: 父主题（可选）。

        Returns:
            新 Theme 实例。
        """
        return cls(name=name, parent=parent, colors=d)

    # ── 内部方法 ─────────────────────────────────────

    def _check_ancestor(self, candidate: "Theme") -> None:
        """防循环继承链检测。

        沿 candidate 的 parent 链向上查找，若发现 self 则抛出 ValueError。

        Raises:
            ValueError: 检测到循环继承链。
        """
        visited: set = {id(self)}
        current: Optional[Theme] = candidate
        while current is not None:
            if id(current) in visited:
                raise ValueError(
                    f"检测到循环主题继承链: {self._name} → {current._name}"
                )
            visited.add(id(current))
            current = current._parent


# ══════════════════════════════════════════════════════════
# 主题预设
# ══════════════════════════════════════════════════════════

THEMES: Dict[str, Dict[str, str]] = {
    # ── 深色主题（默认）────────────────────────────────
    "dark": {
        "title": "\033[38;5;45m",          # 青色
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;45m",         # 青色
        "user": "\033[38;5;45m",           # 青色
        "assistant": "\033[38;5;41m",      # 绿色
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;41m",        # 绿色
        "warning": "\033[38;5;221m",       # 琥珀黄
        "error": "\033[38;5;196m",         # 红色
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 暗灰
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;221m",        # 琥珀黄
        "border": "\033[38;5;239m",        # 暗灰
        "highlight": "\033[38;5;45m",      # 青色
        "muted": "\033[38;5;237m",         # 深灰
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 暗灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;41m",   # 绿色
        "progress_empty": "\033[38;5;236m",   # 深灰
        "diff_add": "\033[38;5;41m",          # 绿色
        "diff_del": "\033[38;5;196m",         # 红色
        "diff_ctx": "\033[38;5;242m",         # 中灰
        "border_active": "\033[38;5;45m",     # 青色
        "border_inactive": "\033[38;5;237m",  # 深灰
        "overlay_bg": "\033[48;5;235m",       # 暗色背景
        "tag_code": "\033[38;5;221m",         # 琥珀黄
        "prompt_glow": "\033[38;5;45m",       # 青色 — 提示符发光
        "border_glow": "\033[38;5;40m",       # 中青 — 边框发光
        "status_pulse": "\033[38;5;214m",     # 琥珀 — 状态脉动
        "breathing_base": "\033[38;5;32m",    # 暗青 — 呼吸基准
        "pulse_highlight": "\033[38;5;81m",   # 亮青 — 脉冲高亮
        "separator_glow": "\033[38;5;45m",    # 青色 — 分隔线发光
        "tag_glow": "\033[38;5;45m",          # 青色 — 标签发光
        # ── 动效语义键（Phase 5） ──
        "effect_bounce": "\033[38;5;45m",     # 弹入动效主题色（青色）
        "effect_wave": "\033[38;5;44m",       # 波动动效主题色（中青）
        "effect_sparkle": "\033[38;5;81m",    # 闪烁高亮色（亮青）
        "effect_glow": "\033[38;5;221m",      # 辉光色（琥珀黄）
        "effect_shimmer": "\033[38;5;195m",   # 流光色（亮白青）
        # ── 新增美化语义键（2026-07-12 TUI 美化） ──
        "user_glow": "\033[38;5;81m",         # 亮青 — 用户消息前缀闪烁
        "placeholder_glow": "\033[38;5;45m",  # 青色 — 底部栏占位符辉光
        "border_breath": "\033[38;5;23m",     # 暗青 — 呼吸边框基准
        "model_breath": "\033[38;5;45m",      # 青色 — 模型名呼吸色
        "tag_breath": "\033[38;5;45m",        # 青色 — 角色标签呼吸色
        "effect_border": "\033[38;5;40m",     # 中青 — 边框动效色
        "deco_glow": "\033[38;5;221m",        # 琥珀黄 — 装饰辉光
        "icon_glow": "\033[38;5;81m",         # 亮青 — 图标辉光
        "accent_pulse": "\033[38;5;214m",     # 琥珀 — 强调脉动
    },

    # ── 亮色主题（浅色背景用）──────────────────────────
    "light": {
        "title": "\033[38;5;33m",          # 蓝色
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;33m",         # 蓝色
        "user": "\033[38;5;33m",           # 蓝色
        "assistant": "\033[38;5;41m",      # 绿色
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;41m",        # 绿色
        "warning": "\033[1;38;5;221m",     # BOLD + 琥珀黄
        "error": "\033[38;5;196m",         # 红色
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 暗灰
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[1;38;5;221m",      # BOLD + 琥珀黄
        "border": "\033[38;5;239m",        # 暗灰
        "highlight": "\033[38;5;33m",      # 蓝色
        "muted": "\033[38;5;242m",         # 中灰
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 暗灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;41m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;41m",
        "diff_del": "\033[38;5;196m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;33m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;221m",
        "prompt_glow": "\033[38;5;33m",       # 蓝色
        "border_glow": "\033[38;5;32m",       # 中蓝
        "status_pulse": "\033[38;5;220m",     # 黄
        "breathing_base": "\033[38;5;26m",    # 暗蓝
        "pulse_highlight": "\033[38;5;75m",   # 亮蓝
        "separator_glow": "\033[38;5;33m",    # 蓝色
        "tag_glow": "\033[38;5;33m",          # 蓝色
        # ── 动效语义键（Phase 5） ──
        "effect_bounce": "\033[38;5;33m",     # 蓝色
        "effect_wave": "\033[38;5;32m",       # 中蓝
        "effect_sparkle": "\033[38;5;75m",    # 亮蓝
        "effect_glow": "\033[38;5;220m",      # 金色
        "effect_shimmer": "\033[38;5;117m",   # 亮天蓝
        # ── 新增美化语义键（2026-07-12 TUI 美化） ──
        "user_glow": "\033[38;5;75m",         # 亮蓝 — 用户消息前缀闪烁
        "placeholder_glow": "\033[38;5;33m",  # 蓝色 — 底部栏占位符辉光
        "border_breath": "\033[38;5;25m",     # 暗蓝 — 呼吸边框基准
        "model_breath": "\033[38;5;33m",      # 蓝色 — 模型名呼吸色
        "tag_breath": "\033[38;5;33m",        # 蓝色 — 角色标签呼吸色
        "effect_border": "\033[38;5;32m",     # 中蓝 — 边框动效色
        "deco_glow": "\033[38;5;220m",        # 金色 — 装饰辉光
        "icon_glow": "\033[38;5;75m",         # 亮蓝 — 图标辉光
        "accent_pulse": "\033[38;5;214m",     # 琥珀 — 强调脉动
    },

    # ── 高对比主题（高可读性）──────────────────────────
    "high-contrast": {
        "title": "\033[38;5;81m",          # 亮青
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;81m",         # 亮青
        "user": "\033[38;5;81m",           # 亮青
        "assistant": "\033[38;5;47m",      # 亮绿
        "thinking": "\033[38;5;15m",       # 白
        "tool": "\033[38;5;15m",           # 白
        "success": "\033[38;5;47m",        # 亮绿
        "warning": "\033[38;5;227m",       # 亮黄
        "error": "\033[1;38;5;196m",       # BOLD + 红
        "info": "\033[38;5;255m",          # 亮白
        "cost": "\033[38;5;255m",          # 亮白
        "separator": "\033[38;5;242m",     # 中灰
        "meta": "\033[38;5;255m",          # 亮白
        "accent": "\033[38;5;227m",        # 亮黄
        "border": "\033[38;5;255m",        # 亮白
        "highlight": "\033[38;5;81m",      # 亮青
        "muted": "\033[38;5;250m",         # 浅灰
        "code": "\033[38;5;255m",          # 亮白
        "divider": "\033[38;5;242m",       # 中灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;47m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;47m",
        "diff_del": "\033[1;38;5;196m",
        "diff_ctx": "\033[38;5;255m",
        "border_active": "\033[38;5;81m",
        "border_inactive": "\033[38;5;242m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;227m",
        "prompt_glow": "\033[38;5;81m",       # 亮青
        "border_glow": "\033[38;5;81m",       # 亮青
        "status_pulse": "\033[38;5;227m",     # 亮黄
        "breathing_base": "\033[38;5;32m",    # 暗青
        "pulse_highlight": "\033[38;5;81m",   # 亮青
        "separator_glow": "\033[38;5;81m",    # 亮青
        "tag_glow": "\033[38;5;81m",          # 亮青
        # ── 动效语义键（Phase 5） ──
        "effect_bounce": "\033[38;5;81m",     # 亮青
        "effect_wave": "\033[38;5;75m",       # 中亮蓝
        "effect_sparkle": "\033[38;5;51m",    # 最亮青
        "effect_glow": "\033[38;5;227m",      # 亮黄
        "effect_shimmer": "\033[38;5;255m",   # 亮白
        # ── 新增美化语义键（2026-07-12 TUI 美化） ──
        "user_glow": "\033[38;5;81m",         # 亮青 — 用户消息前缀闪烁
        "placeholder_glow": "\033[38;5;81m",  # 亮青 — 底部栏占位符辉光
        "border_breath": "\033[38;5;33m",     # 中蓝 — 呼吸边框基准
        "model_breath": "\033[38;5;81m",      # 亮青 — 模型名呼吸色
        "tag_breath": "\033[38;5;81m",        # 亮青 — 角色标签呼吸色
        "effect_border": "\033[38;5;51m",     # 最亮青 — 边框动效色
        "deco_glow": "\033[38;5;227m",        # 亮黄 — 装饰辉光
        "icon_glow": "\033[38;5;81m",         # 亮青 — 图标辉光
        "accent_pulse": "\033[38;5;227m",     # 亮黄 — 强调脉动
    },

    # ── Nord 极光主题（蓝灰冰霜风）────────────────────
    "nord": {
        # ── 基础语义键（Nord 极夜 + 冰霜配色） ──
        "title": "\033[38;5;67m",          # 冰蓝 #5E81AC
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;109m",        # 冰蓝绿 #8FBCBB
        "user": "\033[38;5;109m",          # 冰蓝绿 #8FBCBB
        "assistant": "\033[38;5;108m",     # 极光绿 #A3BE8C
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;108m",       # 极光绿 #A3BE8C
        "warning": "\033[38;5;221m",       # 极光黄 #EBCB8B
        "error": "\033[38;5;167m",         # 极光红 #BF616A
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 极夜灰 #4C566A
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;221m",        # 极光黄 #EBCB8B
        "border": "\033[38;5;239m",        # 极夜灰 #4C566A
        "highlight": "\033[38;5;109m",     # 冰蓝绿 #8FBCBB
        "muted": "\033[38;5;237m",         # 极夜灰 #434C5E
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 极夜灰 #4C566A
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;108m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;108m",
        "diff_del": "\033[38;5;167m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;109m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;221m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;109m",      # 冰蓝绿
        "border_glow": "\033[38;5;67m",       # 冰蓝
        "status_pulse": "\033[38;5;221m",     # 极光黄
        "breathing_base": "\033[38;5;32m",    # 暗青
        "pulse_highlight": "\033[38;5;81m",   # 亮青
        "separator_glow": "\033[38;5;67m",    # 冰蓝
        "tag_glow": "\033[38;5;109m",         # 冰蓝绿
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;109m",    # 冰蓝绿
        "effect_wave": "\033[38;5;67m",       # 冰蓝
        "effect_sparkle": "\033[38;5;81m",    # 亮青
        "effect_glow": "\033[38;5;221m",      # 极光黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;81m",         # 亮青
        "placeholder_glow": "\033[38;5;109m", # 冰蓝绿
        "border_breath": "\033[38;5;24m",     # 暗蓝青
        "model_breath": "\033[38;5;67m",      # 冰蓝
        "tag_breath": "\033[38;5;109m",       # 冰蓝绿
        "effect_border": "\033[38;5;67m",     # 冰蓝
        "deco_glow": "\033[38;5;221m",        # 极光黄
        "icon_glow": "\033[38;5;81m",         # 亮青
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },

    # ── Catppuccin Mocha 主题（暖暗紫柔和风）────────
    "catppuccin": {
        # ── 基础语义键（Catppuccin Mocha 调色板） ──
        "title": "\033[38;5;183m",         # 紫 #CBA6F7
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;117m",        # 蓝 #89B4FA
        "user": "\033[38;5;117m",          # 蓝 #89B4FA
        "assistant": "\033[38;5;120m",     # 绿 #A6E3A1
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;120m",       # 绿 #A6E3A1
        "warning": "\033[38;5;222m",       # 黄 #F9E2AF
        "error": "\033[38;5;210m",         # 红 #F38BA8
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;237m",     # surface1 #45475A
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;183m",        # 紫 #CBA6F7
        "border": "\033[38;5;237m",        # surface1 #45475A
        "highlight": "\033[38;5;117m",     # 蓝 #89B4FA
        "muted": "\033[38;5;236m",         # surface0 #313244
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;237m",       # surface1 #45475A
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;120m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;120m",
        "diff_del": "\033[38;5;210m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;117m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;222m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;117m",      # 蓝
        "border_glow": "\033[38;5;183m",      # 紫
        "status_pulse": "\033[38;5;222m",     # 黄
        "breathing_base": "\033[38;5;26m",    # 暗蓝
        "pulse_highlight": "\033[38;5;75m",   # 亮蓝
        "separator_glow": "\033[38;5;183m",   # 紫
        "tag_glow": "\033[38;5;117m",         # 蓝
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;183m",    # 紫
        "effect_wave": "\033[38;5;140m",      # 中紫
        "effect_sparkle": "\033[38;5;75m",    # 亮蓝
        "effect_glow": "\033[38;5;222m",      # 黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;75m",         # 亮蓝
        "placeholder_glow": "\033[38;5;183m", # 紫
        "border_breath": "\033[38;5;26m",     # 暗蓝
        "model_breath": "\033[38;5;183m",     # 紫
        "tag_breath": "\033[38;5;117m",       # 蓝
        "effect_border": "\033[38;5;140m",    # 中紫
        "deco_glow": "\033[38;5;222m",        # 黄
        "icon_glow": "\033[38;5;75m",         # 亮蓝
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },

    # ── Solarized Dark 主题（深褐温和对比风）──────────
    "solarized-dark": {
        # ── 基础语义键（Solarized 调色板） ──
        "title": "\033[38;5;33m",          # 蓝 #268BD2
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;37m",         # 青 #2AA198
        "user": "\033[38;5;37m",           # 青 #2AA198
        "assistant": "\033[38;5;64m",      # 绿 #859900
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;64m",        # 绿 #859900
        "warning": "\033[38;5;136m",       # 黄 #B58900
        "error": "\033[38;5;160m",         # 红 #DC322F
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;235m",     # base02 #073642
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;136m",        # 黄 #B58900
        "border": "\033[38;5;235m",        # base02 #073642
        "highlight": "\033[38;5;37m",      # 青 #2AA198
        "muted": "\033[38;5;234m",         # base03 #002B36
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;235m",       # base02 #073642
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;64m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;64m",
        "diff_del": "\033[38;5;160m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;37m",
        "border_inactive": "\033[38;5;235m",
        "overlay_bg": "\033[48;5;234m",
        "tag_code": "\033[38;5;136m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;37m",       # 青
        "border_glow": "\033[38;5;33m",       # 蓝
        "status_pulse": "\033[38;5;136m",     # 黄
        "breathing_base": "\033[38;5;25m",    # 暗蓝
        "pulse_highlight": "\033[38;5;69m",   # 亮蓝
        "separator_glow": "\033[38;5;33m",    # 蓝
        "tag_glow": "\033[38;5;37m",          # 青
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;37m",     # 青
        "effect_wave": "\033[38;5;33m",       # 蓝
        "effect_sparkle": "\033[38;5;69m",    # 亮蓝
        "effect_glow": "\033[38;5;136m",      # 黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;69m",         # 亮蓝
        "placeholder_glow": "\033[38;5;37m",  # 青
        "border_breath": "\033[38;5;23m",     # 暗青
        "model_breath": "\033[38;5;33m",      # 蓝
        "tag_breath": "\033[38;5;37m",        # 青
        "effect_border": "\033[38;5;33m",     # 蓝
        "deco_glow": "\033[38;5;136m",        # 黄
        "icon_glow": "\033[38;5;69m",         # 亮蓝
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },

    # ── Monokai 主题（高饱和荧光风）──────────────────
    "monokai": {
        # ── 基础语义键（Monokai Pro 调色板） ──
        "title": "\033[38;5;141m",         # 紫 #AE81FF
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;81m",         # 蓝 #66D9EF
        "user": "\033[38;5;81m",           # 蓝 #66D9EF
        "assistant": "\033[38;5;83m",      # 绿 #A6E22E
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;83m",        # 绿 #A6E22E
        "warning": "\033[38;5;228m",       # 黄 #E6DB74
        "error": "\033[38;5;197m",         # 粉红 #F92672
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;237m",     # 背景 #272822
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;228m",        # 黄 #E6DB74
        "border": "\033[38;5;237m",        # 背景 #272822
        "highlight": "\033[38;5;81m",      # 蓝 #66D9EF
        "muted": "\033[38;5;236m",         # 选择 #49483E
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;237m",       # 背景 #272822
        # ── progress / diff / border / overlay ──
        "progress_filled": "\033[38;5;83m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;83m",
        "diff_del": "\033[38;5;197m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;81m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;228m",
        # ── glow / pulse 发光语义 ──
        "prompt_glow": "\033[38;5;81m",       # 蓝
        "border_glow": "\033[38;5;141m",      # 紫
        "status_pulse": "\033[38;5;228m",     # 黄
        "breathing_base": "\033[38;5;32m",    # 暗青
        "pulse_highlight": "\033[38;5;81m",   # 亮青
        "separator_glow": "\033[38;5;141m",   # 紫
        "tag_glow": "\033[38;5;81m",          # 蓝
        # ── 动效语义键 ──
        "effect_bounce": "\033[38;5;141m",    # 紫
        "effect_wave": "\033[38;5;81m",       # 蓝
        "effect_sparkle": "\033[38;5;51m",    # 最亮青
        "effect_glow": "\033[38;5;228m",      # 黄
        "effect_shimmer": "\033[38;5;195m",   # 亮白青
        # ── 新增美化语义键 ──
        "user_glow": "\033[38;5;81m",         # 蓝
        "placeholder_glow": "\033[38;5;141m", # 紫
        "border_breath": "\033[38;5;23m",     # 暗青
        "model_breath": "\033[38;5;141m",     # 紫
        "tag_breath": "\033[38;5;81m",        # 蓝
        "effect_border": "\033[38;5;81m",     # 蓝
        "deco_glow": "\033[38;5;228m",        # 黄
        "icon_glow": "\033[38;5;51m",         # 最亮青
        "accent_pulse": "\033[38;5;214m",     # 琥珀
    },
}

# ══════════════════════════════════════════════════════════
# 内置主题备份（用户主题加载后可恢复）
# ══════════════════════════════════════════════════════════

_BUILTIN_THEMES: Dict[str, Dict[str, str]] = {k: dict(v) for k, v in THEMES.items()}


# ══════════════════════════════════════════════════════════
# Theme 实例缓存与全局主题
# ══════════════════════════════════════════════════════════

_theme_instances: Dict[str, Theme] = {}


def _get_theme_instance(name: str) -> Theme:
    """获取或创建 Theme 实例（惰性缓存）。

    Args:
        name: 主题名称。

    Returns:
        Theme 实例。

    Raises:
        ValueError: 主题名称不存在。
    """
    if name not in _theme_instances:
        if name not in THEMES:
            raise ValueError(
                f"未知主题: {name}，可用主题: {', '.join(THEMES.keys())}"
            )
        _theme_instances[name] = Theme.from_dict(name, THEMES[name])
    return _theme_instances[name]


def _invalidate_theme_cache() -> None:
    """清空 Theme 实例缓存（在重载主题时调用）。"""
    _theme_instances.clear()


# ── 预置主题实例 ──
default_dark: Theme = _get_theme_instance("dark")
default_light: Theme = _get_theme_instance("light")

# ── 当前活动主题 ──
_ACTIVE_NAME: str = "dark"
THEME: Theme = default_dark


def set_theme(name: str) -> None:
    """切换到指定主题。

    Args:
        name: 主题名称（"dark" / "light" / "high-contrast" / "nord" /
              "catppuccin" / "solarized-dark" / "monokai"）

    Raises:
        ValueError: 主题名称不存在。
    """
    global _ACTIVE_NAME, THEME
    THEME = _get_theme_instance(name)
    _ACTIVE_NAME = name


def get_active_theme() -> str:
    """返回当前主题名称。"""
    return _ACTIVE_NAME


def list_themes() -> List[str]:
    """返回所有可用主题名称列表。"""
    return list(THEMES.keys())


def get_theme_names_with_desc() -> List[tuple[str, str]]:
    """返回 (主题名, 简短描述) 列表。"""
    return [
        ("dark", "深色主题（默认）"),
        ("light", "亮色主题（浅色背景用）"),
        ("high-contrast", "高对比主题（高可读性）"),
        ("nord", "Nord 极光主题（蓝灰冰霜风）"),
        ("catppuccin", "Catppuccin Mocha 主题（暖暗紫柔和风）"),
        ("solarized-dark", "Solarized Dark 主题（深褐温和对比风）"),
        ("monokai", "Monokai 主题（高饱和荧光风）"),
    ]


def load_user_themes() -> None:
    """从 ``~/.deepseek/themes/*.yaml`` 加载用户自定义主题。

    加载后合并到 ``THEMES`` 字典和 ``_theme_instances`` 缓存中。
    用户主题与内置主题同名时，用户主题覆盖内置主题。

    若当前活动主题被用户主题覆盖，``THEME`` 自动更新。
    目录不存在时静默跳过（不报错）。
    """
    from . import theme_loader
    user_themes = theme_loader.load_user_themes_from_dir()
    if user_themes:
        for name, theme_instance in user_themes.items():
            THEMES[name] = theme_instance.colors  # 以 dict 形式存储到 THEMES
            _theme_instances[name] = theme_instance  # 缓存 Theme 实例
        # 若当前活动主题被用户主题覆盖，刷新 THEME
        global THEME
        if _ACTIVE_NAME in user_themes:
            THEME = _theme_instances[_ACTIVE_NAME]


def reload_themes() -> None:
    """重置为基础内置主题并重新加载用户主题。

    适用场景：用户在运行时修改了 ``~/.deepseek/themes/``
    下的 YAML 文件，调用此函数刷新主题列表。

    重置后若当前活动主题不存在（如已被删除的用户主题），
    自动回退为 ``"dark"``。
    """
    global THEMES, _ACTIVE_NAME, THEME
    _invalidate_theme_cache()
    THEMES.clear()
    THEMES.update(_BUILTIN_THEMES)
    # 重建内置主题实例缓存
    _theme_instances["dark"] = Theme.from_dict("dark", THEMES["dark"])
    _theme_instances["light"] = Theme.from_dict("light", THEMES["light"])
    global default_dark, default_light
    default_dark = _theme_instances["dark"]
    default_light = _theme_instances["light"]
    load_user_themes()
    # 确保当前活动主题在重置后仍有效
    if _ACTIVE_NAME not in THEMES:
        _ACTIVE_NAME = "dark"
    THEME = _get_theme_instance(_ACTIVE_NAME)


# ── 模块加载时自动加载用户主题 ──
try:
    load_user_themes()
except Exception:
    import logging
    _theme_logger = logging.getLogger(__name__)
    _theme_logger.warning("加载用户主题失败，使用内置主题", exc_info=True)


__all__ = [
    "Theme",
    "default_dark", "default_light",
    "THEME", "THEMES",
    "set_theme", "get_active_theme", "list_themes",
    "get_theme_names_with_desc",
    "load_user_themes", "reload_themes",
]
