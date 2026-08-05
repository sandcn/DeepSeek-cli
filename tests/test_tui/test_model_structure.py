"""test_model_structure — AppModel 分层拆分架构固化。

架构决策（2026-08-05 重构）：``src/tui/app/model.py``（1135 行）按职责拆分：
  - ``app/_state_types.py`` — 纯状态 dataclass（ChatBlock/CompletionState/...，Layer 0 零依赖）
  - ``app/_model_helpers.py`` — 模块级渲染辅助（角色头/用户前缀/修剪阈值）
  - ``model.py`` — AppModel 行为（块管理/通道/工具 box），re-export 保持兼容

本测试固化模块边界与 re-export 兼容性。
"""

from __future__ import annotations

import ast
from pathlib import Path


def _app_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "tui" / "app"


class TestStateTypesModule:
    """_state_types.py 纯状态模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_app_dir() / "_state_types.py").is_file()

    def test_direct_import_works(self) -> None:
        from src.tui.app import _state_types
        assert _state_types.ChatBlock is not None
        assert _state_types.CompletionState is not None

    def test_zero_dependency(self) -> None:
        """_state_types 不依赖 TUI 运行时模块（Layer 0 纯数据）。"""
        source = (_app_dir() / "_state_types.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                m = node.module or ""
                if m.startswith("src.tui") and "._compat" not in m:
                    assert False, f"_state_types 不应依赖 {m}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("src.tui"):
                        assert False, f"_state_types 不应依赖 {a.name}"


class TestModelHelpersModule:
    """_model_helpers.py 渲染辅助模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_app_dir() / "_model_helpers.py").is_file()

    def test_direct_import_works(self) -> None:
        from src.tui.app import _model_helpers
        assert callable(_model_helpers._role_header_line)
        assert _model_helpers._TOOL_INCREMENTAL_THRESHOLD == 64


class TestModelReexportCompatibility:
    """model.py re-export 保持旧导入路径兼容（测试/外部调用面大量锁定）。"""

    _REEXPORT_SYMBOLS = [
        # 状态类型
        "AppModel", "ChatBlock", "CompletionState", "UserSelectState",
        "StatusState", "HistorySearchState", "ReasoningState",
        # 辅助函数/常量
        "_single_line_detail", "_role_header_runs", "_role_header_line",
        "_user_marker_styled_lines", "_TOOL_INCREMENTAL_THRESHOLD",
        "_BASH_OUTPUT_TAIL_LINES", "_TOOL_HEAD_TOOLS", "_TOOL_HEAD_LINES",
    ]

    def test_reexport_symbols_available(self) -> None:
        import src.tui.app.model as m
        missing = [s for s in self._REEXPORT_SYMBOLS if not hasattr(m, s)]
        assert not missing, f"model.py re-export 缺失: {missing}"

    def test_reexport_identity_state_types(self) -> None:
        """状态类 re-export 与 _state_types 同对象（单一真源）。"""
        from src.tui.app import model, _state_types
        assert model.ChatBlock is _state_types.ChatBlock
        assert model.CompletionState is _state_types.CompletionState
        assert model.UserSelectState is _state_types.UserSelectState
        assert model.ReasoningState is _state_types.ReasoningState

    def test_reexport_identity_helpers(self) -> None:
        """辅助函数 re-export 与 _model_helpers 同对象（单一真源）。"""
        from src.tui.app import model, _model_helpers
        assert model._role_header_runs is _model_helpers._role_header_runs
        assert model._role_header_line is _model_helpers._role_header_line
        assert model._TOOL_INCREMENTAL_THRESHOLD is _model_helpers._TOOL_INCREMENTAL_THRESHOLD

    def test_all_exports_complete(self) -> None:
        import src.tui.app.model as m
        for name in m.__all__:
            assert hasattr(m, name), f"__all__ 引用不存在的符号: {name}"


class TestModelNoLocalDuplicate:
    """model.py 无本地重复实现（状态类/辅助已迁移）。"""

    def test_no_local_state_dataclass(self) -> None:
        """model.py 不应再本地定义状态 dataclass。"""
        source = (_app_dir() / "model.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                assert node.name not in (
                    "ChatBlock", "CompletionState", "UserSelectState",
                    "StatusState", "HistorySearchState",
                ), f"model.py 不应本地定义 {node.name}"

    def test_no_local_helper_function(self) -> None:
        """model.py 不应再本地定义渲染辅助函数。"""
        source = (_app_dir() / "model.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                assert node.name not in (
                    "_role_header_runs", "_role_header_line",
                    "_user_marker_styled_lines",
                ), f"model.py 不应本地定义 {node.name}"


class TestModelBehaviourSmoke:
    """AppModel 核心行为冒烟（确认拆分后行为不变）。"""

    def test_append_committed_smoke(self) -> None:
        from src.tui.app.model import AppModel
        model = AppModel()
        block = model.append_committed("user", [])
        assert block.kind == "user"
        assert model.blocks == [block]
        assert model.committed_count == 1
