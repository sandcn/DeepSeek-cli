"""test_render_api_module — React Ink render() 轻量入口独立模块架构固化。

架构决策（2026-08-05 架构优化）：``render()`` / ``_SimpleModel`` 自
``ink/session.py`` 拆分至 ``ink/_render_api.py``（React Ink render() 轻量入口
独立职责，与 InkSession 会话类解耦）。本测试固化模块边界：
  - _render_api 独立可导入
  - session.py re-export 保持旧导入路径兼容（同一函数/类对象）
  - session.py 不本地定义 render/_SimpleModel（防双实现漂移）
  - _render_api 不反向依赖 session（模块级；惰性 import 仅函数体内）
"""

from __future__ import annotations

import ast
from pathlib import Path


def _ink_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "src" / "tui" / "ink"


class TestRenderApiModuleIndependent:
    """_render_api.py 模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_ink_dir() / "_render_api.py").is_file()

    def test_direct_import_works(self) -> None:
        """_render_api 独立导入（render/_SimpleModel 存在）。"""
        from src.tui.ink import _render_api
        assert callable(_render_api.render)
        assert hasattr(_render_api, "_SimpleModel")

    def test_no_module_level_session_dependency(self) -> None:
        """_render_api 模块级不依赖 session（避免循环；惰性 import 仅函数内）。"""
        source = (_ink_dir() / "_render_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                m = node.module or ""
                assert "session" not in m, f"_render_api 模块级不应依赖 {m}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert "session" not in a.name, (
                        f"_render_api 模块级不应依赖 {a.name}"
                    )


class TestSessionReexportCompatibility:
    """session.py re-export 保持旧导入路径兼容（测试锁定）。"""

    def test_reexport_identity(self) -> None:
        """session.render 与 _render_api.render 为同一函数（单一真源）。"""
        from src.tui.ink import session
        from src.tui.ink import _render_api
        assert session.render is _render_api.render
        assert session._SimpleModel is _render_api._SimpleModel

    def test_session_has_no_local_render_impl(self) -> None:
        """session.py 不本地定义 render/_SimpleModel（防双实现漂移）。"""
        source = (_ink_dir() / "session.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                assert node.name not in ("render", "_SimpleModel"), (
                    f"session.py 不应本地定义 {node.name}（应 re-export）"
                )


class TestRenderApiSmoke:
    """_render_api.render 行为冒烟（确认拆分后行为不变）。"""

    def test_render_returns_control_object(self) -> None:
        """render() 返回控制对象（waitUntilExit/unmount/cleanup/rerender/clear）。"""
        from src.tui.ink._render_api import render
        from src.tui.ink.element import h, TEXT

        ctrl = render(h(TEXT, {"children": "X"}), stream=None, width=10, height=5)
        for key in ("waitUntilExit", "unmount", "cleanup", "rerender", "clear"):
            assert key in ctrl, f"控制对象缺少 {key}"
        assert callable(ctrl["unmount"])
        assert callable(ctrl["rerender"])
        assert callable(ctrl["clear"])
