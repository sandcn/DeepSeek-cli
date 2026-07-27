"""验证 effects.py 清理后的导出完整性。

清理范围：
  - build_sep_wave    别名 → 已删除
  - build_sep_shimmer 别名 → 已删除
  - build_sparkle_ansi 函数 → 已删除

回归保护：
  - 正式函数名仍可从 effects 正常导入
"""

import pytest


def test_build_sep_wave_removed_regression() -> None:
    """验证 build_sep_wave 别名已从 effects 中移除。"""
    with pytest.raises(ImportError):
        from src.tui.core.effects import build_sep_wave  # noqa: F401


def test_build_sep_shimmer_removed_regression() -> None:
    """验证 build_sep_shimmer 别名已从 effects 中移除。"""
    with pytest.raises(ImportError):
        from src.tui.core.effects import build_sep_shimmer  # noqa: F401


def test_build_sparkle_ansi_removed_regression() -> None:
    """验证 build_sparkle_ansi 函数已从 effects 中移除。"""
    with pytest.raises(ImportError):
        from src.tui.core.effects import build_sparkle_ansi  # noqa: F401


def test_real_names_still_exported_regression() -> None:
    """验证正式函数名仍可从 effects 正常导入。"""
    from src.tui.core.effects import build_wave_sep_ansi  # noqa: F401
    from src.tui.core.effects import build_shimmer_sep_ansi  # noqa: F401
    from src.tui.core.effects import sparkle_color  # noqa: F401
