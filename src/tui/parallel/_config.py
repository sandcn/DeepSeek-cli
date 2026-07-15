"""并行显示常量与自适应配置 — Claude Code 风格

Spinner 动画集（通过 get_spinner_frames() 按名称获取）：
  - braille:   Braille 点阵动画（12 帧，基准速度 0.08s）
  - pulse:     脉冲动画（14 帧，▁→█→▁ 呼吸式脉冲，速度 0.05s）
  - circle:    圆周旋转动画（8 帧，速度 0.10s）
  - dots:      点阵呼吸动画（15 帧，⡀→⣿→⡀ 脉动，速度 0.06s）
  - wave:      波浪动画（12 帧，⢀→⢠→⢸→⢻ 波浪起伏，速度 0.08s）
  - typing:    打字点动画（8 帧，⠁→⠈→⠐→⠠→⢀→⡀→⠄→⠂ 逐位点亮旋转，速度 0.12s）
  - heart:     爱心跳动动画（8 帧，心跳式 ♡→♥，速度 0.12s）
  - bounce:    弹跳球动画（8 帧，Braille 垂直弹跳，速度 0.10s）
  - clock:     时钟旋转动画（12 帧，Braille 旋转扫描，速度 0.08s）
  - matrix:    矩阵雨动画（10 帧，Braille 模拟数字雨下落，速度 0.08s）
  - glow:      发光脉冲动画（12 帧，Braille 点从少到多再到少，速度 0.10s）
"""

from __future__ import annotations

from ..core.gradient import gradient_range

# ── 刷新率常量 ──────────────────────────────────────

# 并行面板刷新率（每秒25次，高帧率响应）
PARALLEL_REFRESH_HZ = 25
MIN_REFRESH_INTERVAL = 1.0 / max(1, PARALLEL_REFRESH_HZ)  # 最小刷新间隔

# ── 显示模式阈值 ────────────────────────────────────

DISPLAY_MODE_THRESHOLDS = {
    "compact": 100,   # 终端宽度 < 100 时使用紧凑模式
    "normal": 140,    # 终端宽度 < 140 时使用正常模式
    "detailed": float('inf'),  # 否则使用详细模式
}

# ── 各模式配置 ──────────────────────────────────────

COMPACT_MODE_CONFIG = {
    "max_tool_history_items": 2,      # 紧凑模式下显示最近2个工具
}

NORMAL_MODE_CONFIG = {
    "max_tool_history_items": 3,      # 正常模式显示3个工具（保持默认）
}

DETAILED_MODE_CONFIG = {
    "max_tool_history_items": 8,      # 详细模式显示8个工具
}

# ── 摘要行样式 ──────────────────────────────────────

SUMMARY_SEPARATOR = "·"
SUMMARY_ICON_RUNNING = "⬡"
SUMMARY_ICON_DONE = "✔"

# ── Spinner 动画帧集（多套动画） ──────────────────────

# Braille 点阵动画 — 12 帧（完整序列化）
SPINNER_BRAILLE = [
    "⡉", "⡊", "⡌", "⡆", "⡇", "⡕",
    "⡑", "⡋", "⡓", "⡒", "⡐", "⡄",
]

# 脉冲动画 — 14 帧（▁→█→▁ 呼吸式脉冲）
SPINNER_PULSE = [
    "▁", "▂", "▃", "▄", "▅", "▆", "▇",
    "█",
    "▇", "▆", "▅", "▄", "▃", "▂",
]

# 圆周旋转动画 — 8 帧
SPINNER_CIRCLE = [
    "⢀", "⡀", "⠄", "⠂", "⠁", "⠈", "⠐", "⠠",
]

# 点阵呼吸动画 — 15 帧（⡀→⣿→⡀ 脉动）
SPINNER_DOTS = [
    "⡀", "⣀", "⣄", "⣤", "⣦", "⣶", "⣾", "⣿",
    "⣾", "⣶", "⣦", "⣤", "⣄", "⣀", "⡀",
]

# 波浪动画 — 12 帧（⢀→⢠→⢸→⢻ 波浪起伏）
SPINNER_WAVE = [
    "⢀", "⢠", "⢰", "⢸", "⢹", "⢺",
    "⢻", "⢺", "⢹", "⢸", "⢰", "⢠",
]

# 打字点动画 — 8 帧（⠁→⠈→⠐→⠠→⢀→⡀→⠄→⠂ 逐位点亮旋转）
SPINNER_TYPING = [
    "⠁", "⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂",
]

# 爱心跳动动画 — 8 帧（♡→♥ 呼吸）
SPINNER_HEART = ["♡", "♥", "♥", "♥", "♡", "♥", "♥", "♥"]

# 弹跳球动画 — 8 帧（Braille 垂直弹跳）
SPINNER_BOUNCE = [
    "⡀", "⠄", "⠂", "⠁", "⠉", "⠘", "⠰", "⠴",
]

# 时钟旋转动画 — 12 帧（Braille 旋转扫描）
SPINNER_CLOCK = [
    "⢀", "⡀", "⠄", "⠂", "⠁", "⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂",
]

# 矩阵雨动画 — 10 帧 braille（模拟数字雨下落效果）
SPINNER_MATRIX = [
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
]

# 发光脉冲动画 — 12 帧（Braille 点从少到多再到少，模拟发光扩散）
SPINNER_GLOW = [
    "⣀", "⣠", "⣤", "⣦", "⣶", "⣷", "⣿", "⣷", "⣶", "⣦", "⣤", "⣠",
]

# ── Spinner 集合字典 ────────────────────────────────
SPINNER_SETS: dict[str, list[str]] = {
    "braille": SPINNER_BRAILLE,
    "pulse": SPINNER_PULSE,
    "circle": SPINNER_CIRCLE,
    "dots": SPINNER_DOTS,
    "wave": SPINNER_WAVE,
    "typing": SPINNER_TYPING,
    "heart": SPINNER_HEART,
    "bounce": SPINNER_BOUNCE,
    "clock": SPINNER_CLOCK,
    "matrix": SPINNER_MATRIX,
    "glow": SPINNER_GLOW,
}

# 保留原 SPINNER_FRAMES 兼容别名（指向 SPINNER_BRAILLE 前 8 帧）
SPINNER_FRAMES: list[str] = SPINNER_BRAILLE[:8]

# 默认 spinner 名称
DEFAULT_SPINNER: str = "braille"

# 各 spinner 的帧间隔（秒/帧）
DEFAULT_SPINNER_SPEED: float = 0.08  # braille 帧间隔（基准速度）

SPINNER_SPEED: dict[str, float] = {
    "braille": DEFAULT_SPINNER_SPEED,
    "pulse": 0.05,
    "circle": 0.10,
    "dots": 0.06,
    "wave": 0.08,
    "typing": 0.12,
    "heart": 0.12,
    "bounce": 0.10,
    "clock": 0.08,
    "matrix": 0.08,
    "glow": 0.10,
}


def get_spinner_frames(name: str = "braille") -> tuple[list[str], float]:
    """获取指定名称的 spinner 帧列表和帧间隔。

    Args:
        name: spinner 名称
              （"braille" | "pulse" | "circle" | "dots" | "wave" | "typing"
               | "heart" | "bounce" | "clock" | "matrix" | "glow"）

    Returns:
        (帧列表, 帧间隔秒数)
        未知名称时兜底返回 braille 集。
    """
    frames = SPINNER_SETS.get(name, SPINNER_BRAILLE)
    speed = SPINNER_SPEED.get(name, DEFAULT_SPINNER_SPEED)
    return (frames, speed)


def breathing_animation(
    color_start: int,
    color_end: int,
    steps: int = 8,
) -> list[str]:
    """生成颜色在 start↔end 间呼吸的 ANSI 字符串列表。

    每个元素为带颜色条的方块字符 ``▊``，从起始色渐变到结束色再返回，
    形成完整的呼吸周期（对称版：start→end→start）。

    Args:
        color_start: 起始 256 色号（0-255）
        color_end: 结束 256 色号（0-255）
        steps: 渐变步数（单程），呼吸周期总长度 = 2 * steps

    Returns:
        ANSI 字符串列表，每格式：``\\033[38;5;{color}m▊\\033[0m``
    """
    if steps < 2:
        # P3 修复：当 color_start == color_end 时仍产生 2 帧避免平坦感知
        if color_start == color_end:
            return [
                f"\033[38;5;{color_start}m▊\033[0m",
                f"\033[38;5;{color_end}m▊\033[0m",
            ]
        mid = (color_start + color_end) // 2
        return [
            f"\033[38;5;{color_start}m▊\033[0m",
            f"\033[38;5;{mid}m▊\033[0m",
            f"\033[38;5;{color_end}m▊\033[0m",
            f"\033[38;5;{mid}m▊\033[0m",
        ]

    # 单程渐变：start → end
    forward = gradient_range(color_start, color_end, steps)
    # 对称反向完整排列（start→end→start），峰值出现两次形成对称呼吸周期
    cycle = forward + list(reversed(forward))  # 2*steps 对称版

    return [f"\033[38;5;{c}m▊\033[0m" for c in cycle]


# ── 显示配置类 ──────────────────────────────────────

class DisplayConfig:
    """显示配置类，根据终端宽度自适应显示模式"""

    def __init__(self, terminal_width: int):
        """
        初始化显示配置

        Args:
            terminal_width: 终端宽度（字符数）
        """
        self.terminal_width = terminal_width
        self.display_mode = self._get_display_mode()
        self._apply_mode_config()

    def _get_display_mode(self) -> str:
        """根据终端宽度获取显示模式"""
        if self.terminal_width < DISPLAY_MODE_THRESHOLDS["compact"]:
            return "compact"
        elif self.terminal_width < DISPLAY_MODE_THRESHOLDS["normal"]:
            return "normal"
        else:
            return "detailed"

    def _apply_mode_config(self):
        """应用当前显示模式的配置"""
        if self.display_mode == "compact":
            config = COMPACT_MODE_CONFIG
        elif self.display_mode == "normal":
            config = NORMAL_MODE_CONFIG
        else:  # detailed
            config = DETAILED_MODE_CONFIG

        self.max_tool_history_items = config.get("max_tool_history_items", 3)

    def rebuild(self, width: int) -> bool:
        """根据新宽度重建配置。宽度无变化时返回 False 跳过。

        Args:
            width: 新的终端宽度

        Returns:
            True 表示配置有变化，False 表示宽度未变
        """
        if width == self.terminal_width:
            return False
        old_mode = self.display_mode
        self.terminal_width = width
        self.display_mode = self._get_display_mode()
        if self.display_mode != old_mode:
            self._apply_mode_config()
            return True
        return False
