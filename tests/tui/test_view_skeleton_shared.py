"""全屏视图共用骨架守卫。

背景：TUI 的全屏视图（轨迹 / 配置 / 变更 / 插件 / 模型 …）曾各自实现双栏
布局、光标导航、帮助面板与模态退出逻辑，导致每个视图 300~550 行的巨型组件
与重复实现。现状是已抽出并复用的共用骨架：

- ``_inspector_pane``：右栏检查器的 ``PaneState`` + 导航（``handle_nav`` /
  ``resolve`` / ``scroll_for_cursor``）；
- ``_keymap_pane``：帮助面板（键位速查）渲染；
- ``_modal_view``：模态框框架与关闭键判定（``use_modal_scope`` 等）；
- ``_view_common``：分隔线 / 视口行 / 搜索辅助 / 头部与状态行。

本测试固化「新视图必须复用骨架」这一约束：视图模块若自带一套 pane / 键位表
/ 模态退出逻辑，即视为回归（同时保证 `?` 帮助面板与 Esc 语义全视图一致）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._paths import src_dir

_APP_DIR = src_dir() / "tui" / "app"

#: 全屏视图模块 → 期望至少复用的骨架模块数
#: （``help_view`` 自身就是键位速查视图，只复用模态框架，故单列放宽为 1）
_FULLSCREEN_VIEWS = (
    "trace_view.py",
    "config_view.py",
    "changes_view.py",
    "plugin_view.py",
    "model_view.py",
    "logs_view.py",
    "sandbox_stats_view.py",
    "sandbox_history_view.py",
    "sandbox_records_view.py",
    "sessions_view.py",
    "usage_view.py",
    "skill_view.py",
    "mcp_view.py",
    "help_view.py",
    "notify_view.py",
    "export_view.py",
    "search_view.py",
    "keymap_view.py",
    "theme_view.py",
)

#: 单模块复用下限（仅 keymap/help 类视图例外）
_MIN_SHARED = 2
_MIN_SHARED_OVERRIDES = {"help_view.py": 1, "keymap_view.py": 1}

_SHARED_MODULES = ("_inspector_pane", "_keymap_pane", "_modal_view", "_view_common")

#: 视图侧帮助面板代理模块（自身不再实现键位表渲染，而是委托 _keymap_pane）
_HELP_PROXY_MODULES = {
    "trace_view.py": "trace_help.py",
}


def _imported_modules(path: Path) -> set[str]:
    """模块与其导入的符号名（``from x import y`` 会同时收 ``x`` 与 ``y``）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.add(node.module.split(".")[-1])
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[-1])
    return modules


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("view", _FULLSCREEN_VIEWS)
def test_fullscreen_view_exists(view):
    assert (_APP_DIR / view).is_file(), f"全屏视图 {view} 不存在（清单需同步）"


@pytest.mark.parametrize("view", _FULLSCREEN_VIEWS)
def test_fullscreen_view_reuses_shared_skeleton(view):
    imported = _imported_modules(_APP_DIR / view)
    used = [m for m in _SHARED_MODULES if m in imported]
    minimum = _MIN_SHARED_OVERRIDES.get(view, _MIN_SHARED)
    assert len(used) >= minimum, (
        f"{view} 未复用共用视图骨架（仅用到 {used}）；双栏/帮助面板/模态退出"
        f"应分别取自 {_SHARED_MODULES}，避免各视图重复实现"
    )


@pytest.mark.parametrize("view", _FULLSCREEN_VIEWS)
def test_view_does_not_redefine_pane_state(view):
    """``PaneState`` 等骨架类型只允许在骨架模块中定义。"""
    defined = _defined_names(_APP_DIR / view)
    for forbidden in ("PaneState", "keymap_panel_rows", "use_modal_scope"):
        assert forbidden not in defined, (
            f"{view} 自行定义了 {forbidden}；应改用共用骨架模块"
        )


def test_shared_skeleton_modules_exist():
    for name in _SHARED_MODULES:
        assert (_APP_DIR / f"{name}.py").is_file(), f"缺少共用骨架模块 {name}"


#: 可接受的帮助面板渲染入口（均最终委托 ``_keymap_pane.keymap_panel_rows``）
_HELP_ROW_ENTRIES = ("keymap_panel_rows", "help_panel_rows")


def test_no_view_defines_its_own_help_toggle_state_type():
    """帮助面板开关应统一用 ``_state_types`` 中的视图状态字段（help_open）。

    渲染侧允许三种形态，且都必须最终落到 ``keymap_panel_rows``：
    直接导入 ``keymap_panel_rows``、经 ``_view_common.help_panel_rows`` 代理、
    或经视图自有代理模块（如 ``trace_help``）。
    """
    for view in _FULLSCREEN_VIEWS:
        text = (_APP_DIR / view).read_text(encoding="utf-8")
        if "help_open" not in text:
            continue
        imported = _imported_modules(_APP_DIR / view)
        if any(entry in imported for entry in _HELP_ROW_ENTRIES):
            continue
        proxy = _HELP_PROXY_MODULES.get(view)
        assert proxy is not None, (
            f"{view} 有 help_open 状态但既未复用 {_HELP_ROW_ENTRIES}，也未登记代理模块"
        )
        proxy_text = (_APP_DIR / proxy).read_text(encoding="utf-8")
        assert "from ._keymap_pane import keymap_panel_rows" in proxy_text, (
            f"{view} 的代理模块 {proxy} 未委托 _keymap_pane"
        )


def test_keymap_pane_is_single_source_for_panel_rows():
    """``_view_common.help_panel_rows`` 必须委托 ``_keymap_pane``（防双实现）。"""
    text = (_APP_DIR / "_view_common.py").read_text(encoding="utf-8")
    assert "from ._keymap_pane import keymap_panel_rows" in text
