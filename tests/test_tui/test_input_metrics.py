"""test_input_metrics — 输入区布局度量独立模块（_input_metrics.py）架构固化。

架构决策（2026-08-05 重构，方向A：ink 依赖净化）：补全弹窗高度计算
（``_completion_height``）与反向历史搜索判定（``_is_search_active``）等
「输入区布局度量」原在 ``app/input_area.py``，被 ``ink/session.py`` 的
``_position_cursor`` 引用 → 造成 **底层 ink 框架反向依赖上层 app 组件**
（分层倒置）。现迁至独立顶层模块 ``_input_metrics.py``，ink 层与 app 层
统一向上层导入。本测试固化模块边界：
  - _input_metrics 独立可导入，不反向依赖 app/ink
  - app/input_area re-export 保持旧导入路径兼容（单一真源）
  - app/input_area 无本地重复实现（防双实现漂移）
  - ink/session 不再依赖 app（import 区净化）
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _src_tui() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "tui"


def _import_names(source: str) -> list[str]:
    """提取模块源码 import 区段的完整模块名（含 ImportFrom/Import）。"""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


class TestInputMetricsModuleIndependent:
    """_input_metrics.py 模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_src_tui() / "_input_metrics.py").is_file()

    def test_direct_import_works(self) -> None:
        """_input_metrics 独立导入。"""
        from src.tui import _input_metrics
        assert callable(_input_metrics._completion_height)
        assert callable(_input_metrics._is_search_active)

    def test_no_app_dependency(self) -> None:
        """_input_metrics 不依赖 app/ink（防分层倒置/循环）。"""
        source = (_src_tui() / "_input_metrics.py").read_text(encoding="utf-8")
        imports = _import_names(source)
        assert not any("src.tui.app" in i for i in imports), (
            f"_input_metrics 不应依赖 app: {imports}"
        )
        assert not any("src.tui.ink" in i for i in imports), (
            f"_input_metrics 不应依赖 ink: {imports}"
        )

    def test_wrap_by_width_single_source(self) -> None:
        """_wrap_by_width 从 _input_layout 导入（单一真源，不本地复制）。

        2026-08-05 循环依赖消除后：真源归位 ``_input_layout``，``_input``
        re-export 保持旧导入路径兼容（两者为同一函数对象）。
        """
        from src.tui import _input_metrics
        from src.tui import _input
        from src.tui import _input_layout
        assert _input_metrics._wrap_by_width is _input_layout._wrap_by_width
        assert _input._wrap_by_width is _input_layout._wrap_by_width


class TestAppInputAreaReexport:
    """app/input_area.py re-export 保持旧导入路径兼容。"""

    def test_reexport_identity(self) -> None:
        """input_area._completion_height 与 _input_metrics 为同一函数（单一真源）。"""
        from src.tui.app import input_area
        from src.tui import _input_metrics
        assert input_area._completion_height is _input_metrics._completion_height
        assert input_area._is_search_active is _input_metrics._is_search_active
        assert input_area._desc_column_width is _input_metrics._desc_column_width
        assert input_area._completion_item_rows is _input_metrics._completion_item_rows
        assert input_area._LOCKED_PAD_LIMIT is _input_metrics._LOCKED_PAD_LIMIT

    def test_input_area_has_no_local_impl(self) -> None:
        """input_area 不再本地定义度量函数（应 re-export，防双实现漂移）。"""
        source = (_src_tui() / "app" / "input_area.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        local_defs = [
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        ]
        for fn in ("_completion_height", "_is_search_active",
                   "_desc_column_width", "_completion_item_rows"):
            assert fn not in local_defs, (
                f"input_area 不应本地定义 {fn}（应 re-export）"
            )
        # _LOCKED_PAD_LIMIT 也不应本地赋值（AST 顶层赋值检查）
        tree2 = ast.parse(source)
        top_assigns = []
        for node in tree2.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        top_assigns.append(t.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                top_assigns.append(node.target.id)
        assert "_LOCKED_PAD_LIMIT" not in top_assigns, (
            "input_area 不应本地赋值 _LOCKED_PAD_LIMIT（应 re-export）"
        )

    def test_input_area_public_all_kept(self) -> None:
        """__all__ 导出面保持（_completion_height/_is_search_active 仍导出）。"""
        from src.tui.app import input_area
        for name in ("_completion_height", "_is_search_active",
                     "_compute_input_layout", "_cursor_visual_from_layout"):
            assert name in input_area.__all__, f"__all__ 缺失 {name}"


class TestInkSessionPurified:
    """ink/session.py 不再依赖 app 层（依赖净化核心断言）。"""

    def test_session_imports_no_app(self) -> None:
        """session.py import 区无 src.tui.app.*（仅注释可提及迁移背景）。"""
        source = (_src_tui() / "ink" / "session.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.tui.app"), (
                    f"ink/session 不应依赖 app: {node.module}"
                )

    def test_session_imports_input_metrics(self) -> None:
        """光标度量依赖已收敛至 _cursor 模块（session 经 _cursor 间接使用）。

        方向B（2026-08-05）：_position_cursor 布局计算迁至 ink/_cursor.py，
        _input_metrics 依赖随之下沉到 _cursor——ink 框架内部按职责分层。
        """
        source = (_src_tui() / "ink" / "session.py").read_text(encoding="utf-8")
        assert "from src.tui._input_metrics import" not in source
        cursor_source = (_src_tui() / "ink" / "_cursor.py").read_text(
            encoding="utf-8",
        )
        assert "from src.tui._input_metrics import" in cursor_source


class TestInputMetricsBehaviour:
    """度量辅助行为冒烟（迁移后行为不变）。"""

    @staticmethod
    def _completion(visible=True, items=None, descriptions=None,
                    selected=0, split_desc=False, locked_height=0):
        class C:
            pass
        c = C()
        c.visible = visible
        c.items = items if items is not None else []
        c.descriptions = descriptions or []
        c.selected = selected
        c.split_desc = split_desc
        c.locked_height = locked_height
        return c

    def test_completion_height_basic(self) -> None:
        """3 项 → 标题 + 3 候选项 + 提示行 = 5。"""
        from src.tui import _input_metrics as m
        c = self._completion(items=["a", "b", "c"])
        assert m._completion_height(c, 80) == 5

    def test_completion_height_invisible_zero(self) -> None:
        from src.tui import _input_metrics as m
        assert m._completion_height(None, 80) == 0
        assert m._completion_height(self._completion(visible=False, items=["a"]), 80) == 0
        assert m._completion_height(self._completion(visible=True, items=[]), 80) == 0

    def test_completion_height_locked(self) -> None:
        """items 小幅减少（补白 ≤ 限制）高度保持（防闪烁）。"""
        from src.tui import _input_metrics as m
        c = self._completion(items=["a", "b", "c", "d", "e"], locked_height=0)
        h1 = m._completion_height(c, 80)
        assert h1 == 7  # 5 项 + 2
        c.items = ["a", "b"]  # 补白 = 7-4 = 3 ≤ _LOCKED_PAD_LIMIT
        assert m._completion_height(c, 80) == 7  # 高度保持

    def test_completion_height_shrink_on_large_drop(self) -> None:
        """items 大幅减少（补白 > 限制）允许缩小（避免大片空白）。"""
        from src.tui import _input_metrics as m
        c = self._completion(items=list(range(20)), locked_height=0)
        h20 = m._completion_height(c, 80)
        assert h20 > 3  # 候选项行数受终端高度约束，但必 > 少量项高度
        c.items = ["a"]  # 大幅减少 → 允许缩小
        assert m._completion_height(c, 80) == 3  # 1 项 + 2

    def test_completion_height_increase_follows(self) -> None:
        """items 增加 → 高度跟随（增长滚动自然）。"""
        from src.tui import _input_metrics as m
        c = self._completion(items=["a"], locked_height=0)
        m._completion_height(c, 80)
        c.items = ["a", "b", "c", "d", "e", "f", "g", "h"]
        assert m._completion_height(c, 80) == 10  # 8 项 + 2

    def test_completion_height_split_desc(self) -> None:
        """分栏说明模式：高度取选项数与说明换行行数较大值。"""
        from src.tui import _input_metrics as m
        long_desc = "这是一段很长的说明" * 5
        c = self._completion(
            items=["a", "b"], descriptions=["说明", long_desc],
            selected=1, split_desc=True,
        )
        h = m._completion_height(c, 40)
        assert h > 4  # 2 项 + 2 = 4，说明换行后更高

    def test_is_search_active(self) -> None:
        from src.tui import _input_metrics as m
        assert m._is_search_active(None) is False

        class S:
            pass
        s = S()
        s.active = True
        assert m._is_search_active(s) is True
        s.active = False
        assert m._is_search_active(s) is False

    def test_desc_column_width_bounds(self) -> None:
        """分栏宽度钳制边界。"""
        from src.tui import _input_metrics as m
        assert m._desc_column_width(80) == 26  # 80//3 = 26（钳制到 [8,40]）
        assert m._desc_column_width(10) == 5   # 极窄：min(9, 5)
        assert m._desc_column_width(1000) == 40  # 上限 40
        assert m._desc_column_width(15) == 7   # 极窄：min(14, 7)

    def test_completion_item_rows_bounded(self) -> None:
        """候选项最大行数至少 6（终端高度约束防御）。"""
        from src.tui import _input_metrics as m
        rows = m._completion_item_rows()
        assert rows >= 6
