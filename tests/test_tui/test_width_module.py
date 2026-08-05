"""test_width_module — 字符宽度计算独立模块（_width.py）架构固化。

架构决策（2026-08-05 重构）：字符显示宽度计算（CJK/Emoji/零宽/ANSI 跳过/
单字符缓存）从 ``_screen.py`` 拆分至 ``_width.py``（纯计算职责，Layer 0
零依赖）。本测试固化模块边界：
  - _width 独立可导入，不依赖 _screen（方向：_screen → _width）
  - _screen re-export 保持旧导入路径兼容（from src.tui._screen import wcswidth_simple）
"""

from __future__ import annotations

import ast
from pathlib import Path


def _src_tui() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "tui"


class TestWidthModuleIndependent:
    """_width.py 模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_src_tui() / "_width.py").is_file()

    def test_direct_import_works(self) -> None:
        """_width 独立导入（不触发 _screen 依赖）。"""
        from src.tui import _width
        assert callable(_width.wcswidth_simple)

    def test_width_does_not_import_screen(self) -> None:
        """_width 不反向依赖 _screen（Layer 0 零依赖约束）。"""
        source = (_src_tui() / "_width.py").read_text(encoding="utf-8")
        assert "from ._screen" not in source
        assert "import _screen" not in source

    def test_reexport_identity(self) -> None:
        """_screen.wcswidth_simple 与 _width.wcswidth_simple 为同一函数。"""
        from src.tui import _screen
        from src.tui import _width
        assert _screen.wcswidth_simple is _width.wcswidth_simple

    def test_reexport_ranges_identity(self) -> None:
        """_screen 区间表 re-export 与 _width 同对象（单一真源）。"""
        from src.tui import _screen
        from src.tui import _width
        assert _screen._CJK_RANGES is _width._CJK_RANGES
        assert _screen._ZERO_WIDTH_RANGES is _width._ZERO_WIDTH_RANGES
        assert _screen._FULLWIDTH_RANGES is _width._FULLWIDTH_RANGES
        assert _screen._EMOJI_WIDE_RANGES is _width._EMOJI_WIDE_RANGES


class TestWidthModuleBehaviour:
    """_width.wcswidth_simple 核心语义（与 test_screen.TestWcswidth 对齐）。"""

    def test_ascii(self) -> None:
        from src.tui import _width
        assert _width.wcswidth_simple("hello") == 5
        assert _width.wcswidth_simple("") == 0

    def test_cjk(self) -> None:
        from src.tui import _width
        assert _width.wcswidth_simple("中文") == 4
        assert _width.wcswidth_simple("a中b") == 4  # 1 + 2 + 1

    def test_control(self) -> None:
        from src.tui import _width
        assert _width.wcswidth_simple("\t") == 0

    def test_zero_width(self) -> None:
        from src.tui import _width
        assert _width.wcswidth_simple("a\u200bb") == 2  # ZWSP

    def test_ansi_sequence_width_zero(self) -> None:
        from src.tui import _width
        assert _width.wcswidth_simple("\x1b[31m红\x1b[0m") == 2  # 红(CJK 宽 2)

    def test_fullwidth(self) -> None:
        from src.tui import _width
        assert _width.wcswidth_simple("\uff21") == 2  # Fullwidth A


class TestScreenShimClean:
    """_screen.py 拆分后无残留宽度实现（避免双实现漂移）。"""

    def test_screen_has_no_local_width_impl(self) -> None:
        """_screen 不再本地定义 wcswidth_simple/_CJK_RANGES。"""
        source = (_src_tui() / "_screen.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                assert node.name != "wcswidth_simple", (
                    "_screen 不应本地定义 wcswidth_simple（应 re-export）"
                )
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assert node.target.id != "_CJK_RANGES", (
                    "_screen 不应本地定义 _CJK_RANGES（应 re-export）"
                )

    def test_old_dead_symbols_removed(self) -> None:
        """拆分时清理的死代码符号不应回归（单词边界匹配）。"""
        import re
        source = (_src_tui() / "_screen.py").read_text(encoding="utf-8")
        for dead in (
            "bg_truecolor", "fg_truecolor", "clear_line_full",
            "clear_screen_to_cursor", "cursor_back", "cursor_hide",
            "cursor_show", "unregister_sigwinch_callback", "write_stdout",
            "_in_ranges",
        ):
            assert not re.search(rf"\b{re.escape(dead)}\b", source), (
                f"死代码 {dead} 不应回归"
            )
