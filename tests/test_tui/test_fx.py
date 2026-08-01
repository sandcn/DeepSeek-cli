"""test_fx — app/_fx.py 时间基动效助手测试。

覆盖：
  - fade_color: elapsed=0 返回 start、>=duration 返回 end、中间单调
  - spinner_frame: 时间基推进（mock time.monotonic），非帧计数
  - needs_animation: 空闲/活跃判定
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.app import _fx
from src.tui.core.color import Color256


class TestFadeColor:
    """渐显颜色插值边界。"""

    def test_fade_color_start_regression(self) -> None:
        """elapsed=0（含负值）返回 start_color。"""
        assert _fx.fade_color(0.0, 1.0, 238, 45) == 238
        assert _fx.fade_color(-0.5, 1.0, 238, 45) == 238

    def test_fade_color_end_regression(self) -> None:
        """elapsed>=duration 返回 end_color。"""
        assert _fx.fade_color(1.0, 1.0, 238, 45) == 45
        assert _fx.fade_color(5.0, 1.0, 238, 45) == 45

    def test_fade_color_mid_regression(self) -> None:
        """中间值在 RGB 亮度空间单调非递减（256 色号数值可跳跃，亮度单调）。"""
        def _brightness(color256_idx: int) -> float:
            r, g, b = Color256(color256_idx).to_rgb
            return 0.299 * r + 0.587 * g + 0.114 * b

        prev = _fx.fade_color(0.0, 1.0, 232, 255)
        assert prev == 232
        last = _brightness(prev)
        for elapsed in (0.1, 0.25, 0.5, 0.75, 0.9):
            v = _fx.fade_color(elapsed, 1.0, 232, 255)
            assert 0 <= v <= 255
            b = _brightness(v)
            assert b >= last - 1e-9, f"fade_color 中间值亮度应单调非递减，elapsed={elapsed}"
            last = b
        assert _fx.fade_color(1.0, 1.0, 232, 255) == 255

    def test_fade_in_time_based_no_frame_dep_regression(self) -> None:
        """BEAUTY-1：fade_color 纯时间基（elapsed 输入驱动），无帧计数依赖。"""
        # 相同 elapsed 多次调用确定性一致（与帧计数无关）
        assert _fx.fade_color(0.3, 1.0, 232, 255) == _fx.fade_color(0.3, 1.0, 232, 255)
        # 时间推进 → 插值色不同（232→255 亮度跨度足够区分 0.2 与 0.8）
        assert _fx.fade_color(0.2, 1.0, 232, 255) != _fx.fade_color(0.8, 1.0, 232, 255)


class TestSpinnerFrame:
    """spinner 时间基推进。"""

    def test_spinner_frame_time_based_regression(self) -> None:
        """帧号随时间推进而非帧计数（mock time.monotonic）。"""
        frames = list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
        with patch("src.tui.app._fx.time.monotonic", return_value=0.0) as mock_t:
            f0 = _fx.spinner_frame(10.0, frames)
            mock_t.return_value = 0.15
            f1 = _fx.spinner_frame(10.0, frames)
            mock_t.return_value = 0.30
            f2 = _fx.spinner_frame(10.0, frames)
        # int(0.00*10) % 10 = 0；int(0.15*10) % 10 = 1；int(0.30*10) % 10 = 3
        assert f0 == 0
        assert f1 == 1
        assert f2 == 3
        assert f0 != f1

    def test_spinner_frame_empty_regression(self) -> None:
        """空帧序列返回 0，不抛异常。"""
        assert _fx.spinner_frame(10.0, []) == 0


class TestNeedsAnimation:
    """动画需求判定。"""

    def test_idle_no_animation_regression(self) -> None:
        """空闲（无活跃状态）不触发动画重绘。"""
        assert _fx.needs_animation([]) is False
        assert _fx.needs_animation([False, False, False]) is False

    def test_idle_no_animation_redraw_regression(self) -> None:
        """BEAUTY-5：空闲（无活跃状态）不需要动画重绘；running 状态需要。"""
        assert _fx.needs_animation([]) is False
        assert _fx.needs_animation([False, False]) is False
        assert _fx.needs_animation([True, False]) is True
        # 非空字符串状态（如 "running"）视为活跃
        assert _fx.needs_animation(("done", "running")) is True

    def test_active_animation_regression(self) -> None:
        """任一活跃状态触发动画重绘。"""
        assert _fx.needs_animation([False, True, False]) is True
        # 非空字符串状态（如 "running"）为真
        assert _fx.needs_animation(("done", "running")) is True
