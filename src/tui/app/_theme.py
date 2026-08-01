"""app/_theme — app 组件共享样式与时间基 glow（差异封装）。

从 status_bar.py / input_area.py 提取的公共样式常量与动画时钟：

  - 共享不可变样式常量池（享元模式）：_S_ACCENT / _S_ACCENT_BOLD /
    _S_DIM / _S_SEP / _S_TIME，跨组件复用零拷贝。
  - time_glow()：时间基正弦插值呼吸色号，替代 AnimatorContext 帧基
    sine_color（原帧计数恒为 0 → 观感静态；时间基才能产生真实呼吸，
    对齐 Claude Code React Ink 观感）。

依赖约束：仅依赖 src/tui/core.style.Style 与标准库（math/time），
不依赖 _animator、不依赖任何 app 组件，可独立导入。
"""

from __future__ import annotations

import math
import time

from src.tui.core.style import Style

# ── 共享样式常量池 ────────────────────────────────────────────
_S_ACCENT = Style(fg=45)                 # 强调色（亮青）
_S_ACCENT_BOLD = Style(fg=45, bold=True)  # 强调色加粗
_S_DIM = Style(fg=242)                   # 弱化色（暗灰）
_S_SEP = Style(fg=237)                   # 分隔线色（深灰）
_S_TIME = Style(fg=110)                  # 时间戳色（浅蓝）


def time_glow(lo: int, hi: int, period: float = 12.0) -> int:
    """时间基正弦插值呼吸色号。

    基于 ``time.monotonic()`` 计算正弦插值，返回值钳制在 [lo, hi] 区间。
    与 AnimatorContext 帧基 glow 不同：时间基与渲染帧率无关，
    即使帧计数恒为 0 也能产生连续呼吸观感。

    PERF-5：0.1s 时间桶缓存——同一时间桶（``int(t/0.1)``）且同 (lo,hi,period)
    参数时返回缓存色号（每帧调用不重复计算正弦）。

    Args:
        lo: 呼吸下限色号。
        hi: 呼吸上限色号。
        period: 呼吸周期（秒），默认 12 秒。

    Returns:
        [lo, hi] 区间内的 256 色号整数。
    """
    t = time.monotonic()
    bucket = int(t / 0.1)
    global _glow_cache
    if (
        _glow_cache[0] == bucket
        and _glow_cache[1] == lo
        and _glow_cache[2] == hi
        and _glow_cache[3] == period
    ):
        return _glow_cache[4]
    ratio = (math.sin(2 * math.pi * t / period) + 1) / 2
    color = max(lo, min(hi, lo + int((hi - lo) * ratio)))
    _glow_cache = (bucket, lo, hi, period, color)
    return color


#: time_glow 时间桶缓存 (bucket, lo, hi, period, color)
_glow_cache: tuple = (0, 0, 0, 0, 0)


__all__ = [
    "_S_ACCENT",
    "_S_ACCENT_BOLD",
    "_S_DIM",
    "_S_SEP",
    "_S_TIME",
    "time_glow",
]
