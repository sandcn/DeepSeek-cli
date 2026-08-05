"""test_cursor_module — 输入光标定位独立模块（ink/_cursor.py）架构固化。

架构决策（2026-08-05 重构，方向B：InkSession 职责拆分）：``_position_cursor``
/``_find_input_fiber`` 的布局/坐标计算从 ``ink/session.py`` 迁至独立模块
``ink/_cursor.py``（纯函数模块，独立可测）。本测试固化：
  - _cursor 独立可导入，不依赖 app（防 ink → app 反向依赖）
  - session._position_cursor 委托 _cursor（行为不变）
  - find_input_fiber / position_cursor 行为冒烟
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _ink_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "src" / "tui" / "ink"


class TestCursorModuleIndependent:
    """_cursor.py 模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_ink_dir() / "_cursor.py").is_file()

    def test_direct_import_works(self) -> None:
        from src.tui.ink import _cursor
        assert callable(_cursor.position_cursor)
        assert callable(_cursor.find_input_fiber)

    def test_no_app_dependency(self) -> None:
        """_cursor 不依赖 app 层（防 ink → app 反向依赖）。"""
        source = (_ink_dir() / "_cursor.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.tui.app"), (
                    f"_cursor 不应依赖 app: {node.module}"
                )

    def test_depends_on_metrics_and_layout(self) -> None:
        """_cursor 依赖度量层与纯布局层（依赖方向正确）。

        2026-08-05 循环依赖消除后：布局函数改从 ``_input_layout``（纯函数层）
        直接引用，不再绕经 ``_input`` 输入门面 re-export——ink 框架与输入
        门面解耦。
        """
        source = (_ink_dir() / "_cursor.py").read_text(encoding="utf-8")
        assert "from src.tui._input_metrics import" in source
        assert "from src.tui._input_layout import" in source


class TestSessionDelegation:
    """session._position_cursor / _find_input_fiber 委托 _cursor。"""

    def test_session_uses_cursor_module(self) -> None:
        """session.py 引用 _cursor 模块（委托关系）。"""
        source = (_ink_dir() / "session.py").read_text(encoding="utf-8")
        assert "_cursor.position_cursor" in source
        assert "_cursor.find_input_fiber" in source

    def test_session_position_cursor_delegates_regression(self) -> None:
        """session._position_cursor 委托后行为不变（fiber 定位 + 放置光标）。"""
        from src.tui.ink.fiber import Fiber
        from src.tui.ink.session import InkSession

        s = InkSession.__new__(InkSession)
        s._model = SimpleNamespace()
        s._input_fiber = None
        s._width_cache = SimpleNamespace(get_width=lambda: 80)
        s._ink_renderer = MagicMock()
        fiber = Fiber("host", "input-area", {
            "text": "abc", "cursor_pos": 2, "prompt": "> ", "completion": None,
        })
        fiber.layout_box = SimpleNamespace(x=1, y=0, w=30, h=4)
        s._root_fiber = fiber
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()
            mock_pc.assert_called_once()


class TestFindInputFiber:
    """find_input_fiber 查找行为。"""

    @staticmethod
    def _host(ftype, props, child=None, sibling=None):
        f = SimpleNamespace(
            is_host=True, type=ftype, props=props,
            child=child, sibling=sibling,
        )
        return f

    def test_finds_standard_data_input_area(self) -> None:
        """标准组件容器（props.dataInputArea）可被找到。"""
        from src.tui.ink import _cursor
        root = self._host("column", {"dataInputArea": True, "text": "hi"})
        assert _cursor.find_input_fiber(root) is root

    def test_finds_legacy_host(self) -> None:
        """旧 host（type == 'input-area'）可被找到。"""
        from src.tui.ink import _cursor
        root = self._host("input-area", {"text": "hi"})
        assert _cursor.find_input_fiber(root) is root

    def test_nested_search(self) -> None:
        """嵌套树中查找（递归）。"""
        from src.tui.ink import _cursor
        leaf = self._host("input-area", {"text": "x"})
        middle = self._host("column", {}, child=leaf)
        root = self._host("app", {}, child=middle)
        assert _cursor.find_input_fiber(root) is leaf

    def test_none_when_missing(self) -> None:
        from src.tui.ink import _cursor
        root = self._host("column", {}, child=self._host("text", {}))
        assert _cursor.find_input_fiber(root) is None


class TestPositionCursor:
    """position_cursor 行为冒烟（与迁移前 session._position_cursor 语义一致）。"""

    @staticmethod
    def _fiber(text="abc", cursor_pos=2, prompt="> ", completion=None,
               history_search=None, x=1, y=0, w=30, h=4):
        f = SimpleNamespace(
            is_host=True, type="column",
            props={"dataInputArea": True, "text": text, "cursor_pos": cursor_pos,
                   "prompt": prompt, "completion": completion,
                   "history_search": history_search},
            layout_box=SimpleNamespace(x=x, y=y, w=w, h=h),
            _input_layout_cache=None,
        )
        return f

    def test_places_cursor_basic(self) -> None:
        """基本光标放置：无弹窗、无搜索 → row = y + 0 + 1 + vis_row + 1。"""
        from src.tui.ink import _cursor
        renderer = MagicMock()
        fiber = self._fiber()
        _cursor.position_cursor(renderer, 80, fiber)
        renderer.place_cursor.assert_called_once()
        row, col = renderer.place_cursor.call_args[0]
        assert row >= 2
        assert 1 <= col <= 80

    def test_places_cursor_with_popup(self) -> None:
        """含补全弹窗 → 光标行计入 popup_height。"""
        from src.tui.ink import _cursor
        from src.tui.app.model import CompletionState
        renderer = MagicMock()
        completion = CompletionState(
            visible=True, items=["a", "b"], texts=["a", "b"], selected=0,
        )
        fiber = self._fiber(completion=completion)
        _cursor.position_cursor(renderer, 80, fiber)
        row, _ = renderer.place_cursor.call_args[0]
        assert row >= 5  # y(0) + popup(4) + 1 + vis_row + 1

    def test_missing_completion_attr_guard(self) -> None:
        """缺 items 属性的 completion → 不抛异常、popup_height 回退 0。"""
        from src.tui.ink import _cursor
        renderer = MagicMock()

        class _Missing:
            visible = True

            def __getattr__(self, name):
                raise AttributeError(f"missing: {name}")

        fiber = self._fiber(completion=_Missing())
        _cursor.position_cursor(renderer, 80, fiber)  # 不抛异常
        renderer.place_cursor.assert_called_once()

    def test_no_layout_box_returns(self) -> None:
        """无 layout_box → 直接返回（不放置光标）。"""
        from src.tui.ink import _cursor
        renderer = MagicMock()
        fiber = SimpleNamespace(layout_box=None, props={})
        _cursor.position_cursor(renderer, 80, fiber)
        renderer.place_cursor.assert_not_called()

    def test_history_search_row_offset(self) -> None:
        """反向历史搜索激活 → 光标行 +1（搜索覆盖行）。"""
        from src.tui.ink import _cursor
        renderer = MagicMock()
        search = SimpleNamespace(active=True, query="x", matches=[], index=-1)
        fiber = self._fiber(history_search=search)
        _cursor.position_cursor(renderer, 80, fiber)
        row_with_search, _ = renderer.place_cursor.call_args[0]
        # 无搜索场景对比
        renderer2 = MagicMock()
        fiber2 = self._fiber(history_search=None)
        _cursor.position_cursor(renderer2, 80, fiber2)
        row_without, _ = renderer2.place_cursor.call_args[0]
        assert row_with_search == row_without + 1
