"""app/_fx — 时间基动效助手（差异封装）。

提供统一的纯时间基（``time.monotonic()``）动效入口：
  - fade_color(): FadeIn 渐显颜色插值（复用 core.color.lerp_color 512 缓存）
  - spinner_frame(): spinner 帧号按时间推进（非帧计数）
  - needs_animation(): 判定是否存在活跃/动画状态（供渲染短路判定）

设计模式：模板方法（Template Method）— 统一动效时间基入口，
供 BEAUTY-1/2/3 消费。

设计约束（BEAUTY-5）：所有函数零渲染帧计数依赖；空闲时不触发重绘
由消费方（如 _panel_refresh）依据 needs_animation 短路保证。
"""

from __future__ import annotations

import time

from src.tui.core.color import lerp_color


def _default_fx_params() -> tuple[float, float]:
    """读取 TuiConfig 动效默认参数（fade_duration_sec / spinner_tick_hz）。

    惰性导入避免循环依赖；读取失败时回退与配置默认值一致的字面量。
    """
    try:
        from src.tui._config import TuiConfig
        cfg = TuiConfig.defaults()
        return (cfg.fade_duration_sec, cfg.spinner_tick_hz)
    except Exception:
        return (0.6, 10.0)


#: 模块级默认参数（对齐 TuiConfig 配置默认值，避免每帧重复构造配置实例）
_DEFAULT_FADE_DURATION, _DEFAULT_SPINNER_HZ = _default_fx_params()


def fade_color(
    elapsed: float,
    duration: float | None = None,
    start_color: int = 238,
    end_color: int = 45,
) -> int:
    """时间基渐显颜色插值。

    Args:
        elapsed: 自出现/挂载以来的时间（秒）。
        duration: 渐显总时长（秒）；None 时用 TuiConfig.fade_duration_sec
            默认值（0.6）；<=0 时直接返回 end_color。
        start_color: 起始 256 色号（0-255）。
        end_color: 结束 256 色号（0-255）。

    Returns:
        插值后的 256 色号：elapsed<=0 → start_color；elapsed>=duration → end_color；
        中间经 ``lerp_color`` 线性插值（RGB 空间）。
    """
    if duration is None:
        duration = _DEFAULT_FADE_DURATION
    if duration <= 0.0:
        return end_color
    if elapsed <= 0.0:
        return start_color
    if elapsed >= duration:
        return end_color
    return lerp_color(start_color, end_color, elapsed / duration)


def spinner_frame(tick_hz: float, frames) -> int:
    """时间基 spinner 帧号（``int(time.monotonic() * tick_hz) % len(frames)``）。

    Args:
        tick_hz: 每秒帧切换次数（消费 TuiConfig.spinner_tick_hz 默认值 10.0）；
            <=0 时回退到配置默认值（防御）。
        frames: spinner 帧序列（可为 list/str/tuple，需支持 len/下标）。

    Returns:
        当前帧索引 [0, len(frames))；frames 为空时返回 0。
    """
    n = len(frames)
    if n <= 0:
        return 0
    if tick_hz <= 0:
        tick_hz = _DEFAULT_SPINNER_HZ
    hz = max(tick_hz, 1e-6)
    return int(time.monotonic() * hz) % n


def needs_animation(active_flags) -> bool:
    """判定是否存在活跃/动画状态（需要重绘推进动效）。

    Args:
        active_flags: 可迭代的活跃状态标志（布尔值或可判断真值的对象），
            如 ``(rec.phase in ("running", "parsing") for rec in agents)``。

    Returns:
        任一标志为真 → True（需要动画重绘）；全部为假（空闲）→ False。
    """
    return any(bool(f) for f in active_flags)


__all__ = ["fade_color", "spinner_frame", "needs_animation"]
