"""TUI 统一配置 — 全局可调参数集中管理。

所有TUI模块的可调参数统一在此处定义，取代各模块的硬编码魔数常量。
使用 ``frozen=True`` 确保配置不可变，可安全跨线程共享。

用法::

    from src.tui.config import TuiConfig
    cfg = TuiConfig.defaults()
    print(cfg.render_interval)  # 0.1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


__all__: list[str] = ["ConfigBase", "TuiConfig"]


class ConfigBase:
    """Frozen dataclass 工厂方法基类 — 提供 defaults() 和 with_overrides()。"""

    @classmethod
    def defaults(cls) -> "ConfigBase":
        """返回默认配置实例。"""
        return cls()

    def with_overrides(self, **kwargs: Any) -> "ConfigBase":
        """返回覆盖指定字段的新实例，原实例不变。"""
        return type(self)(**{**self.__dict__, **kwargs})


@dataclass(frozen=True)
class TuiConfig(ConfigBase):
    """TUI 统一配置 — 所有可调参数集中管理。

    所有属性均为不可变（frozen=True），线程安全。
    通过 ``TuiConfig.defaults()`` 获取默认实例。
    """

    # ── 渲染引擎参数 ──────────────────────────────────
    render_interval: float = 0.1            # render 线程刷新间隔（秒），固定 10Hz
    max_batch_size: int = 50                # 单帧最大批处理命令数，防止 UI 冻结
    drain_lock_timeout: float = 0.1         # drain 锁超时（秒），与 render_interval 对齐
    cmd_queue_maxsize: int = 10000          # 命令队列最大容量
    consecutive_full_threshold: int = 10    # 连续满队列告警阈值
    bottom_redraw_interval: float = 0.1     # 底部栏重绘间隔（秒），对应 10Hz

    # ── 动画参数 ──────────────────────────────────────
    breath_cycle_len: int = 12              # 呼吸周期长度（帧数）
    pulse_cycle_len: int = 4                # 脉动周期长度（帧数）

    # ── 截断参数 ──────────────────────────────────────
    max_error_length: int = 200             # 错误消息截断长度（字符）
    # ── FadeIn 动效参数 ───────────────────────────────
    fade_total_frames: int = 6              # FadeIn 渐显帧数
    fade_start_color: int = 238             # FadeIn 起始暗色（256 色号）

    # ── EventBus 参数 ──────────────────────────────────
    eventbus_throttle: float = 0.3          # EventBus 发布频率阈值（秒），对应 300ms
    default_history: int = 3                # 默认工具历史显示条数

    # ── 测试相关 ──────────────────────────────────────
    mock_terminal_width: int = 120          # MockTerminal 默认宽度
    mock_terminal_height: int = 40          # MockTerminal 默认高度

    # ── 崩溃恢复 ──────────────────────────────────────
    max_recover_attempts: int = 3           # render 线程最大重建次数
    recover_delay: float = 0.5              # 崩溃后重建等待（秒）


