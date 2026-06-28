"""并行显示常量与自适应配置 — Claude Code 风格"""

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

# ── Spinner 动画帧（braille 点阵，8 帧循环） ────────────
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]


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
