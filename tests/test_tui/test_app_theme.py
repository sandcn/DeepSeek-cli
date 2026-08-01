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

    @staticmethod
    def _reset_cache():
        """清空 _glow_bucket lru_cache（多桶缓存，方向6）。"""
        import src.tui.app._theme as theme
        theme._glow_bucket.cache_clear()

    def test_time_glow_bucket_cache_regression(self) -> None:
        """同桶（同 int(t/0.1) 且同参数）返回缓存色号；跨桶重新计算。"""
        from unittest.mock import patch
        import src.tui.app._theme as theme

        self._reset_cache()
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
        self._reset_cache()
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            a = time_glow(10, 20)
            b = time_glow(200, 210)
        assert 10 <= a <= 20
        assert 200 <= b <= 210

    def test_time_glow_multi_param_no_overwrite_regression(self) -> None:
        """方向6 — 不同 (lo,hi,period) 参数多桶互不覆盖（同桶同参命中缓存）。"""
        from unittest.mock import patch
        import src.tui.app._theme as theme

        self._reset_cache()
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            # input_area 参数 (32,49,12) 与 status_bar 参数 (36,45,4) 交替调用
            a1 = time_glow(32, 49, 12.0)
            b1 = time_glow(36, 45, 4.0)
            a2 = time_glow(32, 49, 12.0)
            b2 = time_glow(36, 45, 4.0)
        # 同参数同桶命中缓存 → 返回值一致（修复前单桶互相覆盖 → 频繁重算）
        assert a1 == a2
        assert b1 == b2
        assert 32 <= a1 <= 49
        assert 36 <= b1 <= 45
        # lru 命中路径：同参数同桶不再触发内部计算
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            cache_info = theme._glow_bucket.cache_info()
            hits_before = cache_info.hits
            time_glow(32, 49, 12.0)
            cache_info = theme._glow_bucket.cache_info()
            assert cache_info.hits > hits_before, "lru_cache 应命中（多桶缓存）"


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

    def test_palette_invalidate_ttl_boundary_regression(self) -> None:
        """方向2 — 失效后 TTL 边界（进程启动早期 now<TTL）不误返回 dark。

        修复前失效置 (0.0, dark) → now-0<TTL 窗口内 get 误返回 dark；修复后
        缓存值保持当前活动 palette。
        """
        from unittest.mock import patch
        import src.tui.app._theme as theme
        from src.tui.app._theme import (
            _invalidate_palette_cache, get_active_palette, resolve_theme,
        )

        try:
            # 模拟：活动调色板为 light（缓存新鲜，时间戳 10.0）+ 进程启动早期
            # （mock monotonic=0.5 → now-0<TTL 边界）
            theme._active_palette_cache = (10.0, resolve_theme("light"))
            with patch("src.tui.app._theme.time.monotonic", return_value=0.5):
                _invalidate_palette_cache()
                # 失效后缓存值保持 light（修复前硬编码 dark）
                assert theme._active_palette_cache[1] == resolve_theme("light")
                # TTL 边界内 get 返回 light（不误回 dark）
                assert get_active_palette() == resolve_theme("light")
        finally:
            # 恢复全局缓存为已知状态（防测试间污染）
            theme._active_palette_cache = (0.0, theme.ThemeRegistry.resolve("dark"))


class TestSingleSourceOfTruth:
    """方向3 步骤15 — 样式/颜色单一真源收敛回归。

    断言 dark Palette 各槽值 == ``_SEMANTIC_COLOR`` 槽位表 == ``_COLOR_*``
    字符串内色号（防止未来漂移；值与既有硬编码完全一致，零视觉回归）。
    """

    def test_dark_palette_slots_match_semantic_color_regression(self) -> None:
        """dark Palette 各槽 Style 色号与 _SEMANTIC_COLOR 槽位表一致。"""
        from src.tui._const import _SEMANTIC_COLOR
        from src.tui.app._theme import resolve_theme

        dark = resolve_theme("dark")
        # Palette 字段名 → _SEMANTIC_COLOR 槽位名映射（selection_bg 为背景槽）
        slot_map = {
            "accent": "accent",
            "accent_bold": "accent",
            "dim": "dim",
            "sep": "sep",
            "time": "time",
            "token": "token",
            "speed": "speed",
            "tool_ok": "tool_ok",
            "tool_fail": "tool_fail",
            "tool_running": "speed",
            "border": "border",
            "selection_bg": "select_bg",
            "selection_fg": "select_fg",
            "placeholder": "placeholder",
        }
        for palette_slot, semantic_name in slot_map.items():
            style = getattr(dark, palette_slot)
            color = style.bg if palette_slot == "selection_bg" else style.fg
            assert color == _SEMANTIC_COLOR[semantic_name], (
                f"dark.{palette_slot} 漂移：期望 {_SEMANTIC_COLOR[semantic_name]}，"
                f"实际 {color}（应与 _SEMANTIC_COLOR 槽位一致，防止样式漂移）"
            )

    def test_color_constants_derived_from_semantic_color_regression(self) -> None:
        """_COLOR_*/_C_* ANSI 字符串内色号与 _SEMANTIC_COLOR 槽位一致。"""
        from src.tui._const import (
            _SEMANTIC_COLOR,
            _COLOR_ACCENT, _COLOR_DEEP_CYAN, _COLOR_DIM, _COLOR_SEP,
            _COLOR_TIME, _COLOR_TOKEN, _COLOR_SPEED, _COLOR_TOOL_OK,
            _COLOR_TOOL_FAIL, _COLOR_SELECT_BG, _COLOR_SELECT_FG,
            _COLOR_COMPLETE_TITLE, _COLOR_COMPLETE_CMD_PREFIX, _COLOR_COMPLETE_DIR,
            _C_RUNNING, _C_FAIL,
        )
        sc = _SEMANTIC_COLOR
        # 前景派生（38;5;n）
        assert _COLOR_ACCENT == f"\033[38;5;{sc['accent']}m"
        assert _COLOR_DEEP_CYAN == f"\033[38;5;{sc['deep_cyan']}m"
        assert _COLOR_DIM == f"\033[38;5;{sc['dim']}m"
        assert _COLOR_SEP == f"\033[38;5;{sc['sep']}m"
        assert _COLOR_TIME == f"\033[38;5;{sc['time']}m"
        assert _COLOR_TOKEN == f"\033[38;5;{sc['token']}m"
        assert _COLOR_SPEED == f"\033[38;5;{sc['speed']}m"
        assert _COLOR_TOOL_OK == f"\033[38;5;{sc['tool_ok']}m"
        assert _COLOR_TOOL_FAIL == f"\033[38;5;{sc['tool_fail']}m"
        # 背景派生（48;5;n）
        assert _COLOR_SELECT_BG == f"\033[48;5;{sc['select_bg']}m"
        assert _COLOR_SELECT_FG == f"\033[38;5;{sc['select_fg']}m"
        # 加粗前缀派生
        assert _COLOR_COMPLETE_TITLE == f"\033[1;38;5;{sc['accent']}m"
        assert _COLOR_COMPLETE_CMD_PREFIX == f"\033[1;38;5;{sc['accent']}m"
        assert _COLOR_COMPLETE_DIR == f"\033[38;5;{sc['time']}m"
        # _C_* 面板系列
        assert _C_RUNNING == f"\033[38;5;{sc['speed']}m"
        assert _C_FAIL == f"\033[38;5;{sc['tool_fail']}m"

    def test_semantic_color_anchors_regression(self) -> None:
        """槽位表关键锚点（test_screen.py 硬编码锚点同步防漂移）。"""
        from src.tui._const import _SEMANTIC_COLOR
        assert _SEMANTIC_COLOR["accent"] == 45
        assert _SEMANTIC_COLOR["speed"] == 214
        assert _SEMANTIC_COLOR["tool_ok"] == 41
        assert _SEMANTIC_COLOR["tool_fail"] == 196

    def test_style_sheet_error_matches_tool_fail_regression(self) -> None:
        """StyleSheet "error" 与 _SEMANTIC_COLOR["tool_fail"] 同源（防漂移）。"""
        from src.tui._const import _SEMANTIC_COLOR
        from src.tui.core.style import StyleSheet
        error = StyleSheet.get("error")
        assert error is not None
        assert error.fg == _SEMANTIC_COLOR["tool_fail"]
