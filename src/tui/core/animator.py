"""统一动画基础设施 — 集中动画时钟管理 + 呼吸颜色注册表。

提供：
  - AnimatorContext: 全局单例动画时钟管理器，统一推进所有动画帧号
  - BreathPalette:   呼吸颜色注册表，集中管理所有呼吸颜色序列

模块加载时自动注册所有预定义调色板。

增强（2026-07-12）：
  - 添加正弦波呼吸属性（sine_breath/sine_pulse/sine_color）
    替代纯线性步进，实现平滑的缓入缓出呼吸效果
  - 所有现有属性（breath_frame/pulse_frame）保持向后兼容
"""

from __future__ import annotations

import math
from typing import Optional

from .palettes import BREATH_CYAN, BREATH_GREEN, BREATH_PURPLE, BREATH_GOLD, BREATH_ROSE
from .gradient import gradient_range

__all__ = [
    "AnimatorContext",
    "BreathPalette",
]


class AnimatorContext:
    """集中动画时钟管理器 — 统一推进所有动画帧号。

    单例模式，所有组件通过 get_default() 获取同一实例。
    由 render 线程（10Hz）定期调用 tick() 推进帧号。

    新增正弦波属性（2026-07-12）：
      - sine_breath: [0.0, 1.0] 正弦呼吸值，12帧周期
      - sine_pulse:  [0.0, 1.0] 正弦脉动值，4帧周期
      - sine_color(low, high, period): 正弦波插值色号
      所有现有属性（breath_frame/pulse_frame）保持向后兼容。
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

    # ── 向后兼容的阶梯帧号属性 ──────────────────────────

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

    # ── 新增：正弦波呼吸属性（2026-07-12） ──────────────

    @property
    def sine_breath(self) -> float:
        """正弦波呼吸值 [0.0, 1.0]，12帧周期缓入缓出。

        使用 sin(phase - π/2) 将 -1→1→-1 映射为 0→1→0，
        在 min/max 处有自然减速（导数趋近0），比阶梯跳变更平滑。
        """
        phase = (self.frame % self.breath_cycle_len) / self.breath_cycle_len * 2.0 * math.pi
        return (math.sin(phase - math.pi / 2.0) + 1.0) / 2.0

    @property
    def sine_pulse(self) -> float:
        """正弦波脉动值 [0.0, 1.0]，4帧周期高频脉动。"""
        phase = (self.frame % self.pulse_cycle_len) / self.pulse_cycle_len * 2.0 * math.pi
        return (math.sin(phase - math.pi / 2.0) + 1.0) / 2.0

    def sine_color(self, color_low: int, color_high: int, period: int = 12, frame: int | None = None) -> int:
        """正弦波插值色号，在 color_low ↔ color_high 间平滑过渡。

        Args:
            color_low: 最暗色号（0-255）。
            color_high: 最亮色号（0-255）。
            period: 呼吸周期帧数，默认 12。
            frame: 显式帧号，None 时使用 self.frame（全局帧）。

        Returns:
            四舍五入后的插值色号。
        """
        f = self.frame if frame is None else frame
        if period == self.breath_cycle_len and frame is None:
            t = self.sine_breath
        elif period == self.pulse_cycle_len and frame is None:
            t = self.sine_pulse
        else:
            phase = (f % period) / period * 2.0 * math.pi
            t = (math.sin(phase - math.pi / 2.0) + 1.0) / 2.0
        return round(color_low + t * (color_high - color_low))

    def sine_color_seq(self, colors: list[int], period: int | None = None) -> int:
        """对任意长度颜色列表做正弦波插值取色。

        相比取模索引（线性跳变），正弦波在列表两端有缓入缓出。

        Args:
            colors: 颜色列表。
            period: 呼吸周期，None 时自动设为 len(colors)。

        Returns:
            插值后的色号。
        """
        n = len(colors)
        if n == 0:
            return 45
        if n == 1:
            return colors[0]
        p = period if period is not None else n
        phase = (self.frame % p) / p * 2.0 * math.pi
        t_val = (math.sin(phase - math.pi / 2.0) + 1.0) / 2.0
        idx_f = t_val * (n - 1)
        idx_low = int(idx_f)
        idx_high = min(idx_low + 1, n - 1)
        frac = idx_f - idx_low
        return round(colors[idx_low] + frac * (colors[idx_high] - colors[idx_low]))

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

    增强（2026-07-12）：
      - get_sine_color(): 基于正弦波插值的颜色获取，更平滑
      - 支持 low_high 参数，动态计算而非查表
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
    def get_sine_color(cls, name: str, frame: int = 0, period: int | None = None) -> int:
        """基于正弦波插值的呼吸色号，比 get_color() 更平滑。

        使用正弦波在调色板色号间插值，两端有自然减速。
        适合需要"有机呼吸"感的场景（分隔线、标签等）。

        Args:
            name: 调色板名称。
            frame: 当前帧号。
            period: 呼吸周期，None 时使用调色板长度。

        Returns:
            插值后的色号（0-255）。
        """
        from .effects import sine_color_range
        colors = cls._palettes.get(name)
        if not colors:
            return 45
        return sine_color_range(frame, colors, period)

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

    # ── 呼吸调色板（2026-07-12 新增） ──
    "breath_cyan": BREATH_CYAN,
    "breath_green": BREATH_GREEN,
    "breath_purple": BREATH_PURPLE,
    "breath_gold": BREATH_GOLD,
    "breath_rose": BREATH_ROSE,
})
