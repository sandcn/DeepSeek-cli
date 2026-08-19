"""core/_fx — 时间基动效助手（Layer 0，公共工具层）。

从 ``app/_fx.py`` 下沉（2026-08-05 重构：公共动画/样式工具归位 core 层）：
``fade_color`` / ``spinner_frame`` / ``spinner_char`` / ``SPINNER_FRAMES`` 为
纯时间基（``time.monotonic()``）动效工具，无 app 组件依赖——``app/_fx.py``
保持 re-export 存根（旧导入路径 + 测试 patch 路径兼容）；``_subagent_render``
（被 core/parallel_executor 依赖）改从本模块引用，消除「subagent 渲染 →
app 域」的分层倒置。

提供统一的纯时间基（``time.monotonic()``）动效入口：
  - fade_color(): FadeIn 渐显颜色插值（复用 core.color.lerp_color 512 缓存）
  - spinner_frame(): spinner 帧号按时间推进（非帧计数）
  - spinner_char(): 当前 spinner 帧字符（唯一真源）

设计模式：模板方法（Template Method）— 统一动效时间基入口，
供 BEAUTY-1/2/3 消费。

设计约束（BEAUTY-5）：所有函数零渲染帧计数依赖；空闲时不触发重绘
由消费方（如 _panel_refresh）依据 ``StateStore.needs_animation`` 短路保证
（``_fx.needs_animation`` 无生产调用方，已随 2026-08-05 死代码清理移除）。
"""

from __future__ import annotations

import math
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


#: 模块级默认参数快照（对齐 TuiConfig 配置默认值，兼容外部导入引用——
#: ``app/_fx.py`` re-export、``app/input_area.py`` 引用）。
#: ★ P3-21：模块导入时固化的快照仅供外部引用；函数内部**惰性读取**
#:   ``TuiConfig.defaults()``（运行期修改配置即时生效），不依赖本快照。
_DEFAULT_FADE_DURATION, _DEFAULT_SPINNER_HZ = _default_fx_params()

#: spinner 帧序列**唯一真源**（BEAUTY 动效收敛，方向4）——ASCII braille 帧
#: ``⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏``（10 帧，10Hz 推进时 1s 循环）。修复前
#: ``_ParseLine``（chat_view.py，原 app.py）、``status_bar``
#: （``\\u280b...`` 转义串）、``_subagent_render``（字符列表）三处各自内联
#: 同一组字符（表示形式不同：字符串/转义串/列表）——收敛为本常量，消费方
#: 统一引用（保留各自模块级别名/列表形态以兼容测试导入路径）。
SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


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
        # ★ P3-21：惰性读取 TuiConfig 默认值——修复前用模块导入时固化的
        #   ``_DEFAULT_FADE_DURATION``，运行期修改 TuiConfig 不影响。
        duration = _default_fx_params()[0]
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
    if not math.isfinite(tick_hz) or tick_hz <= 0:
        # ★ 修复（P2-3）：NaN/inf tick_hz 防御——修复前 ``tick_hz <= 0`` 对
        #   NaN 恒为 False（未触发默认值回退），``int(monotonic * NaN)`` 抛
        #   ValueError 中断渲染。非有限或 <=0 一律回退配置默认值。
        # ★ P3-21：惰性读取 TuiConfig 默认值——修复前用模块导入时固化的
        #   ``_DEFAULT_SPINNER_HZ``，运行期修改 TuiConfig 不影响。
        tick_hz = _default_fx_params()[1]
    hz = max(tick_hz, 1e-6)
    return int(time.monotonic() * hz) % n


def spinner_char(tick_hz: float = 0.0) -> str:
    """返回当前时间基 spinner 帧**字符**（通用动效助手，唯一真源）。

    chat_view.py ``_ParseLine``（原 app.py）、status_bar、_subagent_render
    三处此前各自 ``SPINNER_FRAMES[spinner_frame(hz, SPINNER_FRAMES)]`` 内联
    同一逻辑——收敛为本 helper（帧序列统一取 ``SPINNER_FRAMES``）。

    Args:
        tick_hz: 每秒帧切换次数；<=0 时用 ``spinner_frame`` 默认（10Hz）。

    Returns:
        当前 spinner 帧字符（如 ``⠋``）。
    """
    return SPINNER_FRAMES[spinner_frame(tick_hz, SPINNER_FRAMES)]


__all__ = ["fade_color", "spinner_frame", "spinner_char", "SPINNER_FRAMES"]
