"""test_app_theme — app/_theme.py 共享样式 + 时间基 glow 测试。

覆盖：
  - time_glow 返回值始终在 [lo, hi] 区间（含 lo == hi 边界）
  - 时间基周期性（极小 period 下可观察到变化；正常 period 下稳定在区间）
  - 共享样式常量可导入且 fg/bold 值正确

原则：时间基函数测试不依赖具体时间值，仅断言区间与存在性，避免 flaky。
"""

from __future__ import annotations

from src.tui.app._theme import (
    _S_ACCENT,
    _S_ACCENT_BOLD,
    _S_DIM,
    _S_SEP,
    _S_TIME,
    time_glow,
)


class TestTimeGlowRange:
    """time_glow 返回值边界。"""

    def test_time_glow_range_regression(self) -> None:
        """lo=32, hi=49 时多次调用返回值始终在 [32, 49] 区间。"""
        for _ in range(50):
            v = time_glow(32, 49)
            assert 32 <= v <= 49

    def test_time_glow_equal_bounds_regression(self) -> None:
        """lo == hi 时返回值恒等于该值（不越界）。"""
        assert time_glow(242, 242) == 242


class TestTimeGlowPeriodic:
    """time_glow 时间基周期性。"""

    def test_time_glow_periodic_regression(self) -> None:
        """极小 period 下时间推进可观察到值变化；正常 period 下结果稳定在区间。"""
        # 极小周期：连续采样中时间推进导致相位跨越 → 出现不同值（非恒定）
        values = {time_glow(32, 49, period=1e-6) for _ in range(60)}
        assert all(32 <= v <= 49 for v in values)
        assert len(values) > 1, "时间基 glow 在极小 period 下应随时间变化"

        # 正常周期：结果始终在区间内
        for _ in range(20):
            v = time_glow(32, 49, period=12.0)
            assert 32 <= v <= 49


class TestThemeConstants:
    """_theme 共享样式常量。"""

    def test_theme_constants_import_regression(self) -> None:
        """共享常量可导入且 fg/bold 值正确。"""
        assert _S_ACCENT.fg == 45
        assert _S_ACCENT.bold is False
        assert _S_ACCENT_BOLD.fg == 45
        assert _S_ACCENT_BOLD.bold is True
        assert _S_DIM.fg == 242
        assert _S_SEP.fg == 237
        assert _S_TIME.fg == 110
