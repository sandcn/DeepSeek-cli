"""测试 core/__init__.py 懒加载精简后的导入完整性。

覆盖三类场景：
  1. 直接导入（原懒加载 → 改为立即加载）的符号可正常访问
  2. 仍保持懒加载的符号不受影响，仍可正常访问
  3. __all__ 中所有符号均可正常导入
"""

from src.tui.core import __all__


class TestDirectImportsAccessible:
    """验证改为直接导入的 4 组基础工具符号可正常访问。"""

    def test_color_symbols_direct_import(self) -> None:
        from src.tui.core import (
            Color256, RGB, TrueColor, GradientDescriptor,
            ColorValue, to_ansi_fg, to_ansi_bg, to_256, auto_color,
        )
        assert Color256.__name__ == "Color256"
        assert RGB.__name__ == "RGB"
        assert TrueColor.__name__ == "TrueColor"

    def test_style_symbols_direct_import(self) -> None:
        from src.tui.core import Style, StyledText, StyleSheet
        assert Style.__name__ == "Style"
        assert StyledText.__name__ == "StyledText"
        assert StyleSheet.__name__ == "StyleSheet"

    def test_ansi_utils_symbols_direct_import(self) -> None:
        from src.tui.core import (
            strip_ansi, visual_width, truncate_ansi_visual,
            skip_ansi_sgr, truncate_ansi_sgr, truncate_ansi_line,
        )
        assert callable(strip_ansi)
        assert callable(visual_width)

    def test_text_utils_symbols_direct_import(self) -> None:
        from src.tui.core import (
            truncate, build_gradient_ansi, build_gradient_ansi_frame,
            build_warning_pulse_ansi, make_sep_gradient,
            build_bounce_ansi, build_left_border_ansi,
            parse_theme_color, make_sep_gradient_enhanced,
            build_gradient,
        )
        assert callable(truncate)
        assert callable(build_gradient_ansi)


class TestLazyImportsStillWork:
    """验证仍保持懒加载的模块符号不受影响。"""

    def test_ttl_cache_lazy_import(self) -> None:
        from src.tui.core import TTLCache
        assert TTLCache.__name__ == "TTLCache"

    def test_animator_context_lazy_import(self) -> None:
        from src.tui.core import AnimatorContext
        assert AnimatorContext.__name__ == "AnimatorContext"

    def test_format_elapsed_lazy_import(self) -> None:
        from src.tui.core import format_elapsed
        assert callable(format_elapsed)

    def test_ui_session_state_lazy_import(self) -> None:
        from src.tui.core import UISessionState
        assert UISessionState.__name__ == "UISessionState"

    def test_breath_palette_lazy_import(self) -> None:
        from src.tui.core import BreathPalette
        assert BreathPalette.__name__ == "BreathPalette"

    def test_effect_registry_lazy_import(self) -> None:
        from src.tui.core import EffectRegistry
        assert EffectRegistry.__name__ == "EffectRegistry"


class TestAllExportedSymbolsAccessible:
    """遍历 __all__ 验证每个符号均可正常从 src.tui.core 导入。"""

    def test_all_exported_symbols_importable(self) -> None:
        """验证 __all__ 中每个符号均可导入。

        注：pre-existing bug（load_user_themes_into_themes 在 theme.py 中未实现）
        已在 core/__init__.py 中修复——该符号已从 __all__ 和 _SYMBOL_MAP 中移除。
        KNOWN_BROKEN 为空集，所有 __all__ 符号均应可正常导入。
        """
        import importlib

        KNOWN_BROKEN: set[str] = set()
        core_mod = importlib.import_module("src.tui.core")
        failed: list[str] = []
        for name in __all__:
            if name in KNOWN_BROKEN:
                continue
            try:
                obj = getattr(core_mod, name)
                assert obj is not None, f"{name} resolved to None"
            except (ImportError, AttributeError) as exc:
                failed.append(f"{name}: {exc}")
        assert not failed, f"以下符号导入失败:\n" + "\n".join(failed)
