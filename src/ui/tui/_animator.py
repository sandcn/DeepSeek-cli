"""统一动画基础设施 — 集中动画时钟管理 + 呼吸颜色注册表。

提供：
  - AnimatorContext: 全局单例动画时钟管理器，统一推进所有动画帧号
  - BreathPalette:   呼吸颜色注册表，集中管理所有呼吸颜色序列

模块加载时自动注册所有预定义调色板。
"""

from __future__ import annotations

from typing import Optional

from ..colors import gradient_range

__all__ = [
    "AnimatorContext",
    "BreathPalette",
]


class AnimatorContext:
    """集中动画时钟管理器 — 统一推进所有动画帧号。

    单例模式，所有组件通过 get_default() 获取同一实例。
    由 render 线程（10Hz）定期调用 tick() 推进帧号。
    """

    _default_instance: Optional["AnimatorContext"] = None

    def __init__(self) -> None:
        self.frame: int = 0                 # 全局帧号（单调递增）
        self.breath_cycle_len: int = 12     # 呼吸周期长度
        self.pulse_cycle_len: int = 4       # 脉动周期长度
        self.progress_breath_period: int = 8   # 进度条呼吸周期
        self.agent_breath_period: int = 12     # Agent标题呼吸周期

    def tick(self, delta: int = 1) -> None:
        """推进全局帧号。"""
        self.frame += delta

    @property
    def breath_frame(self) -> int:
        """呼吸帧号（0-based，自动取模）。"""
        return self.frame % self.breath_cycle_len

    @property
    def pulse_frame(self) -> int:
        """脉动帧号（0-based，自动取模）。"""
        return self.frame % self.pulse_cycle_len

    @property
    def progress_breath_offset(self) -> int:
        """进度条呼吸偏移量。"""
        return self.frame % self.progress_breath_period

    @property
    def agent_breath_offset(self) -> int:
        """Agent标题呼吸偏移量。"""
        return self.frame % self.agent_breath_period

    @classmethod
    def get_default(cls) -> "AnimatorContext":
        """获取全局默认实例（单例）。"""
        if cls._default_instance is None:
            cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        """重置默认实例（供测试使用）。"""
        cls._default_instance = None


class BreathPalette:
    """呼吸调色板注册表 — 所有呼吸颜色序列集中管理。

    使用命名查找，消除 12+ 处重复定义。
    模块加载时自动注册所有预定义调色板。
    线程安全：所有操作为只读字典访问 + 纯函数。
    """

    _palettes: dict[str, list[int]] = {}

    @classmethod
    def register(cls, name: str, colors: list[int]) -> None:
        """注册命名调色板。"""
        cls._palettes[name] = list(colors)  # 防御性拷贝

    @classmethod
    def register_many(cls, palettes: dict[str, list[int]]) -> None:
        """批量注册。"""
        for name, colors in palettes.items():
            cls._palettes[name] = list(colors)

    @classmethod
    def get(cls, name: str) -> list[int]:
        """获取调色板颜色列表。不存在时返回空列表。"""
        return cls._palettes.get(name, [])

    @classmethod
    def get_color(cls, name: str, frame: int = 0) -> int:
        """获取指定调色板的当前帧色号。自动取模。"""
        colors = cls._palettes.get(name)
        if not colors:
            return 45  # 兜底色 = CYAN_256
        return colors[frame % len(colors)]

    @classmethod
    def has(cls, name: str) -> bool:
        """检查调色板是否存在。"""
        return name in cls._palettes


# ════════════════════════════════════════════════════════
# 预注册调色板（模块加载时自动注册）
# ════════════════════════════════════════════════════════

BreathPalette.register_many({
    # ── 呼吸分隔线（思考/消息/提示符共享同一序列） ──
    "think":      gradient_range(24, 87, 6) + gradient_range(87, 24, 6),
    "sep_msg":    gradient_range(24, 87, 6) + gradient_range(87, 24, 6),
    "prompt":     gradient_range(24, 87, 6) + gradient_range(87, 24, 6),

    # ── 角色呼吸 ──
    "role_user":  gradient_range(45, 81, 4) + gradient_range(81, 45, 4),
    "role_asst":  gradient_range(41, 47, 4) + gradient_range(47, 41, 4),
    "role_tool":  gradient_range(221, 227, 4) + gradient_range(227, 221, 4),

    # ── 底部栏分隔线呼吸 ──
    "sep_bar":    [45, 44, 43, 42, 41, 40, 41, 42, 43, 44],

    # ── 补全弹窗背景呼吸 ──
    "breath_bg":  [235, 236, 237, 238, 239, 240, 239, 238, 237, 236],

    # ── 工具图标脉动 ──
    "tool_pulse": ([214, 216, 218, 220, 218, 216] * 2),

    # ── Agent标题呼吸偏移 ──
    "agent_breath": ([0, 1, 2, 3, 2, 1] * 2),

    # ── 进度条渐变 ──
    "progress_amber_green": gradient_range(214, 41, 8),

    # ── 状态栏脉动 ──
    "pulse":      gradient_range(36, 45, 3) + [40],

    # ── 模型名呼吸 ──
    "model":      [32, 45, 40, 45],

    # ── 错误/告警脉冲 ──
    "error_pulse":  gradient_range(196, 9, 3) + gradient_range(9, 196, 3),
    "warn_pulse":   gradient_range(214, 11, 3) + gradient_range(11, 214, 3),

    # ── 状态栏脉动 ──
    "status_pulse": gradient_range(45, 81, 4) + gradient_range(81, 45, 4),
})
