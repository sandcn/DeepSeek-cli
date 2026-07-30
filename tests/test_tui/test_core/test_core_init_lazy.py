"""测试 core/__init__.py 精简导出后的导入完整性。

覆盖 1 类场景：
  1. __all__ 中所有 13 个符号均可正常导入

已删除的符号（Color256、RGB、ansi_utils、text_utils、TTLCache、AnimatorContext
等）已随 core/__init__.py 精简移除，不再测试。
"""

from src.tui.core import __all__


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
