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
        """时间推进（跨桶）可观察到值变化；正常 period 下结果稳定在区间。"""
        from unittest.mock import patch

        # 跨桶采样（PERF-5：同桶缓存返回同值；跨桶重新计算）：时间推进出现不同值
        with patch(
            "src.tui.app._theme.time.monotonic",
            side_effect=[0.0, 0.15, 0.30, 0.45, 0.60, 0.75],
        ):
            values = {time_glow(32, 49, period=1.0) for _ in range(6)}
        assert all(32 <= v <= 49 for v in values)
        assert len(values) > 1, "时间基 glow 跨桶应随时间变化"

        # 正常周期：结果始终在区间内
        for _ in range(20):
            v = time_glow(32, 49, period=12.0)
            assert 32 <= v <= 49


class TestTimeGlowBucketCache:
    """PERF-5 — time_glow 0.1s 时间桶缓存。"""

    def test_time_glow_bucket_cache_regression(self) -> None:
        """同桶（同 int(t/0.1) 且同参数）返回缓存色号；跨桶重新计算。"""
        from unittest.mock import patch
        import src.tui.app._theme as theme

        theme._glow_cache = (0, 0, 0, 0, 0)
        # 同桶：两次调用返回同值
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            v1 = time_glow(32, 49)
            v2 = time_glow(32, 49)
        assert v1 == v2
        # 跨桶（+0.2s）：重新计算（可能不同值，但不越界）
        with patch("src.tui.app._theme.time.monotonic", return_value=100.2):
            v3 = time_glow(32, 49)
        assert 32 <= v3 <= 49
        # 不同 lo/hi 参数即使同桶也分别计算（不互相污染缓存）
        theme._glow_cache = (0, 0, 0, 0, 0)
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            a = time_glow(10, 20)
            b = time_glow(200, 210)
        assert 10 <= a <= 20
        assert 200 <= b <= 210


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


class TestPaletteRegistry:
    """Claude TUI parity 步骤 1.1 — 语义化调色板注册表。"""

    def test_dark_values_match_existing_constants(self) -> None:
        """dark 各槽值与现有 _S_* 常量逐一相等（零视觉回归）。"""
        from src.tui.app._theme import Palette, ThemeRegistry, resolve_theme
        dark = resolve_theme("dark")
        assert dark.accent == _S_ACCENT
        assert dark.accent_bold == _S_ACCENT_BOLD
        assert dark.dim == _S_DIM
        assert dark.sep == _S_SEP
        assert dark.time == _S_TIME
        # 兜底默认 Palette() 即 dark
        assert Palette() == dark

    def test_light_high_contrast_slots_exist(self) -> None:
        """light / high-contrast 主题注册且全部槽位存在。"""
        from src.tui.app._theme import _PALETTE_SLOTS, ThemeRegistry
        for name in ("light", "high-contrast"):
            pal = ThemeRegistry.get(name)
            assert pal is not None
            for slot in _PALETTE_SLOTS:
                assert getattr(pal, slot) is not None, f"{name}.{slot} 缺失"

    def test_unknown_theme_falls_back_to_dark(self) -> None:
        """未知名主题回退 dark（零回归安全侧）。"""
        from src.tui.app._theme import resolve_theme
        assert resolve_theme("不存在的主题") == resolve_theme("dark")

    def test_theme_names_registered(self) -> None:
        """dark/light/high-contrast 三套主题均已注册。"""
        from src.tui.app._theme import ThemeRegistry
        assert set(ThemeRegistry.names()) == {"dark", "light", "high-contrast"}

    def test_get_active_palette_default_dark(self) -> None:
        """get_active_palette 默认返回 dark（config 不可读时）。"""
        from src.tui.app._theme import get_active_palette, resolve_theme, _invalidate_palette_cache
        _invalidate_palette_cache()
        assert get_active_palette() == resolve_theme("dark")
