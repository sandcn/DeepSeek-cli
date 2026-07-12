"""测试 _effects.py 动效原语模块。

覆盖所有核心纯函数的数学性质、边界条件和值域。
"""

from __future__ import annotations

import math
import pytest
from src.ui.tui._effects import (
    sine_breath_t, sine_color, sine_color_range,
    bounce_easing, bounce_frame_color,
    wave_offset, apply_wave,
    sparkle_brightness, sparkle_color,
    shimmer_position, shimmer_apply,
    build_fade_in_ansi_enhanced,
    build_fg_breath_ansi, build_bg_breath_ansi,
)


class TestSineBreath:
    """正弦波呼吸函数测试。"""

    def test_value_range(self):
        """正弦波值域始终在 [0.0, 1.0] 范围内。"""
        for frame in range(100):
            val = sine_breath_t(frame, period=12)
            assert 0.0 <= val <= 1.0, f"frame={frame}: val={val} 超出 [0,1]"

    def test_period_cycle(self):
        """周期为 period 时，frame 和 frame+period 的值相同。"""
        period = 12
        for frame in range(period):
            v1 = sine_breath_t(frame, period)
            v2 = sine_breath_t(frame + period, period)
            assert abs(v1 - v2) < 1e-10, f"frame={frame}: {v1} != {v2}"

    def test_min_at_frame_0(self):
        """frame=0 时值为 0.0（最小值）。"""
        val = sine_breath_t(0, 12)
        assert abs(val) < 1e-10, f"frame=0: val={val} 应为 0"

    def test_max_at_half_period(self):
        """frame=period/2 时值为 1.0（最大值）。"""
        val = sine_breath_t(6, 12)
        assert abs(val - 1.0) < 1e-10, f"frame=6: val={val} 应为 1.0"

    def test_smooth_transition(self):
        """值在连续帧间平滑变化（相邻帧差值 < 0.5）。"""
        period = 12
        for frame in range(period - 1):
            v1 = sine_breath_t(frame, period)
            v2 = sine_breath_t(frame + 1, period)
            assert abs(v2 - v1) < 0.5, f"跳变过大: frame {frame}->{frame+1}: {v1}->{v2}"

    def test_different_periods(self):
        """不同周期长度的归一化值仍保持在 [0,1] 内。"""
        for period in [2, 3, 4, 6, 8, 12, 16, 24]:
            for frame in range(period * 2):
                val = sine_breath_t(frame, period)
                assert 0.0 <= val <= 1.0, f"period={period}, frame={frame}: val={val}"


class TestSineColor:
    """正弦波插值色号测试。"""

    def test_color_range(self):
        """色号在 [color_low, color_high] 范围内。"""
        for frame in range(50):
            c = sine_color(frame, 30, 50, period=12)
            assert 30 <= c <= 50, f"frame={frame}: color={c} 超出 [30,50]"

    def test_min_at_frame_0(self):
        """frame=0 时返回 color_low。"""
        c = sine_color(0, 30, 50, 12)
        assert c == 30, f"frame=0: color={c} 应为 30"

    def test_max_at_half_period(self):
        """frame=period/2 时返回 color_high。"""
        c = sine_color(6, 30, 50, 12)
        assert c == 50, f"frame=6: color={c} 应为 50"

    def test_integer_result(self):
        """结果始终为整数（0-255）。"""
        for frame in range(100):
            c = sine_color(frame, 10, 200, period=12)
            assert isinstance(c, int), f"frame={frame}: {type(c)}"
            assert 0 <= c <= 255


class TestSineColorRange:
    """任意颜色列表正弦插值测试。"""

    def test_empty_list_returns_default(self):
        """空列表返回兜底色 45。"""
        assert sine_color_range(0, []) == 45

    def test_single_color(self):
        """单色列表始终返回该色号。"""
        for frame in range(20):
            assert sine_color_range(frame, [100]) == 100

    def test_basic_interpolation(self):
        """基本插值在列表范围内。"""
        colors = [30, 40, 50, 60]
        for frame in range(24):
            c = sine_color_range(frame, colors, period=12)
            assert 30 <= c <= 60, f"frame={frame}: color={c} 超出范围"


class TestBounceEasing:
    """弹入缓动曲线测试。"""

    def test_starts_at_zero(self):
        """t=0 时返回 0。"""
        assert bounce_easing(0.0) == 0.0

    def test_ends_at_one(self):
        """t=1 时返回 1。"""
        assert bounce_easing(1.0) == 1.0

    def test_value_range(self):
        """值域始终在 [0.0, 1.1] 范围内（允许轻微超调）。"""
        for i in range(101):
            t = i / 100.0
            val = bounce_easing(t)
            assert 0.0 <= val <= 1.15, f"t={t}: val={val}"

    def test_overshoot_exists(self):
        """存在超调峰值（>1.0 的点）。"""
        has_overshoot = any(bounce_easing(i / 100.0) > 1.0 for i in range(1, 100))
        assert has_overshoot, "弹入曲线应存在超调"

    def test_monotonic_in_middle(self):
        """中段（t=0.2~0.8）单调递增。"""
        prev = bounce_easing(0.2)
        for i in range(21, 81):
            t = i / 100.0
            val = bounce_easing(t)
            # 允许轻微波动（弹跳超调的回落）
            assert val >= prev - 0.05, f"非单调: t={t}: {prev} -> {val}"
            prev = val


class TestBounceFrameColor:
    """弹入帧色号测试。"""

    def test_completed_frame_returns_full_bright(self):
        """超过 total_frames 返回 255（全亮）。"""
        assert bounce_frame_color(10, 8) == 255

    def test_first_frame_dim(self):
        """第0帧为暗色（接近238）。"""
        c = bounce_frame_color(0, 8)
        assert c <= 240, f"frame=0: color={c} 应较暗"

    def test_last_frame_bright(self):
        """最后一帧接近255。"""
        c = bounce_frame_color(7, 8)
        assert c >= 250, f"frame=7: color={c} 应接近255"


class TestWaveOffset:
    """波动偏移测试。"""

    def test_symmetric_range(self):
        """偏移量在 [-amplitude, amplitude] 范围内。"""
        for idx in range(20):
            for frame in range(20):
                off = wave_offset(idx, frame, amplitude=3.0)
                assert -3.0 <= off <= 3.0, f"idx={idx}, frame={frame}: offset={off}"

    def test_zero_amplitude(self):
        """amplitude=0 时偏移始终为 0。"""
        for idx in range(10):
            for frame in range(10):
                assert abs(wave_offset(idx, frame, 0.0)) < 1e-10


class TestApplyWave:
    """波动应用测试。"""

    def test_preserves_length(self):
        """波动后列表长度不变。"""
        colors = [30, 40, 50, 60, 70]
        for frame in range(10):
            result = apply_wave(colors, frame)
            assert len(result) == len(colors)

    def test_color_clamped(self):
        """色号在 [0, 255] 范围内。"""
        colors = [0, 128, 255]
        for frame in range(20):
            result = apply_wave(colors, frame, amplitude=10)
            for c in result:
                assert 0 <= c <= 255

    def test_different_frames_different_results(self):
        """不同帧号产生不同的波动结果。"""
        colors = [100] * 10
        results = {tuple(apply_wave(colors, frame)) for frame in range(10)}
        assert len(results) > 1, "多帧应产生不同的波动结果"


class TestSparkleBrightness:
    """闪烁亮度测试。"""

    def test_value_range(self):
        """亮度值在 [0.0, 1.0] 范围内。"""
        for frame in range(50):
            val = sparkle_brightness(frame, period=6)
            assert 0.0 <= val <= 1.0, f"frame={frame}: val={val}"

    def test_rapid_rise(self):
        """0→0.3t 段快速上升（t=0.1 时亮度 > 0.3）。"""
        val = sparkle_brightness(0, 6)  # t=0
        assert abs(val) < 1e-10, f"t=0: val={val}"
        val_at_1 = sparkle_brightness(1, 6)  # t≈0.167
        assert val_at_1 > 0.3, f"快速上升: t≈0.167: {val_at_1}"

    def test_slow_fade(self):
        """0.3→1.0t 段缓慢下降。"""
        val_mid = sparkle_brightness(2, 6)  # t≈0.33
        val_end = sparkle_brightness(5, 6)  # t≈0.83
        assert val_end < val_mid, f"应缓慢下降: mid={val_mid}, end={val_end}"


class TestSparkleColor:
    """闪烁色号测试。"""

    def test_color_range(self):
        """色号在 [base, base+bright_boost] 范围内。"""
        for frame in range(30):
            c = sparkle_color(frame, base_color=45, bright_boost=20, period=6)
            assert 45 <= c <= 65, f"frame={frame}: color={c}"


class TestShimmerPosition:
    """流光位置测试。"""

    def test_position_bounds(self):
        """位置在 [0, total_width) 范围内。"""
        for frame in range(100):
            pos = shimmer_position(frame, total_width=40, speed=0.5)
            assert 0 <= pos < 40, f"frame={frame}: pos={pos}"

    def test_cyclic(self):
        """位置在 total_width*speed 帧后循环。"""
        total_width = 40
        speed = 0.5
        cycle_frames = int(total_width / speed)
        p1 = shimmer_position(0, total_width, speed)
        p2 = shimmer_position(cycle_frames, total_width, speed)
        assert abs(p1 - p2) < 1e-10, f"循环后位置不一致: {p1} != {p2}"


class TestShimmerApply:
    """流光应用测试。"""

    def test_preserves_length(self):
        """流光后列表长度不变。"""
        colors = [30, 40, 50, 60, 70]
        for frame in range(10):
            result = shimmer_apply(colors, frame)
            assert len(result) == len(colors)

    def test_color_clamped(self):
        """色号在 [0, 255] 范围内。"""
        colors = [250] * 10
        result = shimmer_apply(colors, 5, boost=40)
        for c in result:
            assert 0 <= c <= 255, f"color={c} 超出范围"

    def test_boost_increases_brightness(self):
        """亮带内的色号 ≥ 原始色号。"""
        colors = [100] * 20
        for frame in range(10):
            result = shimmer_apply(colors, frame, width=5, boost=30)
            for i in range(20):
                assert result[i] >= colors[i], f"frame={frame}, idx={i}: {result[i]} < {colors[i]}"


class TestBuildFadeInEnhanced:
    """增强版渐显 ANSI 序列测试。"""

    def test_completed_returns_empty(self):
        """≥ total_frames 返回空字符串。"""
        assert build_fade_in_ansi_enhanced(5, 4) == ""

    def test_not_empty_during_fade(self):
        """在渐显过程中返回非空字符串。"""
        result = build_fade_in_ansi_enhanced(0, 4, style="linear")
        assert result != ""
        result = build_fade_in_ansi_enhanced(2, 4, style="linear")
        assert result != ""

    def test_first_frame_dim(self):
        """第0帧包含暗色（238 附近）。"""
        result = build_fade_in_ansi_enhanced(0, 4, style="linear")
        assert "238" in result or "239" in result or "240" in result


class TestBuildFgBgBreath:
    """前景/背景呼吸 ANSI 序列测试。"""

    def test_fg_format(self):
        """前景序列格式为 \033[38;5;Nm。"""
        result = build_fg_breath_ansi(0, 30, 50, 12)
        assert result.startswith("\033[38;5;")
        assert result.endswith("m")

    def test_bg_format(self):
        """背景序列格式为 \033[48;5;Nm。"""
        result = build_bg_breath_ansi(0, 30, 50, 12)
        assert result.startswith("\033[48;5;")
        assert result.endswith("m")

    def test_color_in_range(self):
        """色号在 [low, high] 范围内。"""
        for frame in range(24):
            for fn in [build_fg_breath_ansi, build_bg_breath_ansi]:
                result = fn(frame, 30, 50, 12)
                color_str = result.replace("\033[38;5;", "").replace("\033[48;5;", "").replace("m", "")
                c = int(color_str)
                assert 30 <= c <= 50, f"frame={frame}: color={c}"
