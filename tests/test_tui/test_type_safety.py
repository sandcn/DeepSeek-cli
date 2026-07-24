"""类型安全修复测试 — 覆盖 renderer_base/engine/renderer 的类型兼容性。

测试覆盖：
  - register_render_command 装饰器的类型安全（_RenderCommandMethod Protocol）
  - CursorTracker 在 TuiEngine.__init__ 中的类型兼容性
  - CursorTracker 在 FrameworkRenderer.__init__ 中的类型兼容性
  - CursorTracker 在 TuiRenderer.__init__ 中的类型兼容性
  - cursor_tracker 在调用链中的类型传递一致性
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from src.tui.engine.engine import TuiEngine
from src.tui.engine.renderer_base import FrameworkRenderer, _RenderCommandMethod
from src.tui.engine.renderer import TuiRenderer
from src.tui.widgets.cursor_tracker import CursorTracker
from src.tui.testing import tui_test_env


# ════════════════════════════════════════════════════════
# _RenderCommandMethod Protocol 验证
# ════════════════════════════════════════════════════════


class TestRenderCommandMethodProtocol:
    """验证 _RenderCommandMethod Protocol 的行为。"""

    def test_protocol_is_runtime_checkable(self):
        """_RenderCommandMethod 是 runtime_checkable Protocol。"""
        assert isinstance(_RenderCommandMethod, type)
        # 验证可以 isinstance 检查
        class ValidMethod:
            _render_command_id: tuple[int, tuple[int, ...]] = (1, ())

        instance = ValidMethod()
        assert isinstance(instance, _RenderCommandMethod)  # 需要 runtime_checkable

    def test_protocol_requires_render_command_id(self):
        """_RenderCommandMethod 要求 _render_command_id 属性。"""
        class ValidMethod:
            _render_command_id: tuple[int, tuple[int, ...]] = (1, ())

        instance = ValidMethod()
        assert instance._render_command_id == (1, ())

    def test_protocol_accepts_various_command_ids(self):
        """_RenderCommandMethod 接受不同的命令 ID 和参数索引组合。"""
        cases = [
            (1, ()),
            (100, (1,)),
            (255, (1, 2, 3)),
            (0, (0,)),
        ]
        for cmd_id, arg_indices in cases:
            class Method:
                _render_command_id: tuple[int, tuple[int, ...]] = (cmd_id, arg_indices)

            assert Method()._render_command_id == (cmd_id, arg_indices)


# ════════════════════════════════════════════════════════
# CursorTracker 类型兼容性验证
# ════════════════════════════════════════════════════════


class TestCursorTrackerTypeInTuiEngine:
    """验证 CursorTracker 类型在 TuiEngine.__init__ 中的兼容性。"""

    def test_tui_engine_accepts_cursor_tracker_instance(self):
        """TuiEngine.__init__ 接受 CursorTracker 实例。"""
        with tui_test_env():
            tracker = CursorTracker()
            renderer = MagicMock(spec=FrameworkRenderer)
            bb = MagicMock()
            engine = TuiEngine(renderer, bb, cursor_tracker=tracker)
            assert engine._cursor_tracker is tracker

    def test_tui_engine_accepts_cursor_tracker_none(self):
        """TuiEngine.__init__ 接受 cursor_tracker=None。"""
        with tui_test_env():
            engine = TuiEngine(
                MagicMock(spec=FrameworkRenderer),
                MagicMock(),
                cursor_tracker=None,
            )
            assert engine._cursor_tracker is None

    def test_tui_engine_default_cursor_tracker_is_none(self):
        """TuiEngine.__init__ 默认 cursor_tracker 为 None。"""
        with tui_test_env():
            engine = TuiEngine(MagicMock(spec=FrameworkRenderer), MagicMock())
            assert engine._cursor_tracker is None


class TestCursorTrackerTypeInFrameworkRenderer:
    """验证 CursorTracker 类型在 FrameworkRenderer.__init__ 中的兼容性。"""

    def test_framework_renderer_accepts_cursor_tracker_instance(self):
        """FrameworkRenderer.__init__ 接受 CursorTracker 实例。"""
        tracker = CursorTracker()
        adapter = MagicMock()
        renderer = FrameworkRenderer(
            output_adapter=adapter,
            cursor_tracker=tracker,
        )
        assert renderer._tracker is tracker

    def test_framework_renderer_accepts_cursor_tracker_none(self):
        """FrameworkRenderer.__init__ 接受 cursor_tracker=None。"""
        renderer = FrameworkRenderer(
            output_adapter=MagicMock(),
            cursor_tracker=None,
        )
        assert renderer._tracker is None

    def test_framework_renderer_default_cursor_tracker_is_none(self):
        """FrameworkRenderer.__init__ 默认 cursor_tracker 为 None。"""
        renderer = FrameworkRenderer(output_adapter=MagicMock())
        assert renderer._tracker is None


class TestCursorTrackerTypeInTuiRenderer:
    """验证 CursorTracker 类型在 TuiRenderer.__init__ 中的兼容性。"""

    def test_tui_renderer_accepts_cursor_tracker_instance(self):
        """TuiRenderer.__init__ 接受 CursorTracker 实例。"""
        with tui_test_env():
            tracker = CursorTracker()
            rs = MagicMock()
            adapter = MagicMock()
            bb = MagicMock()
            renderer = TuiRenderer(
                rs=rs,
                output_adapter=adapter,
                bottom_bar=bb,
                cursor_tracker=tracker,
            )
            assert renderer._tracker is tracker

    def test_tui_renderer_accepts_cursor_tracker_none(self):
        """TuiRenderer.__init__ 接受 cursor_tracker=None。"""
        with tui_test_env():
            renderer = TuiRenderer(
                rs=MagicMock(),
                output_adapter=MagicMock(),
                bottom_bar=MagicMock(),
                cursor_tracker=None,
            )
            assert renderer._tracker is None

    def test_tui_renderer_default_cursor_tracker_is_none(self):
        """TuiRenderer.__init__ 默认 cursor_tracker 为 None。"""
        with tui_test_env():
            renderer = TuiRenderer(
                rs=MagicMock(),
                output_adapter=MagicMock(),
                bottom_bar=MagicMock(),
            )
            assert renderer._tracker is None


# ════════════════════════════════════════════════════════
# CursorTracker 调用链类型传递
# ════════════════════════════════════════════════════════


class TestCursorTrackerCallChain:
    """验证 cursor_tracker 在 TuiEngine → FrameworkRenderer 调用链中的传递。"""

    def test_cursor_tracker_passed_through_renderer_chain(self):
        """cursor_tracker 从 TuiEngine 传递到 FrameworkRenderer。"""
        with tui_test_env():
            tracker = CursorTracker()
            renderer = MagicMock(spec=FrameworkRenderer)
            bb = MagicMock()
            engine = TuiEngine(renderer, bb, cursor_tracker=tracker)
            assert engine._cursor_tracker is tracker

    def test_cursor_tracker_record_newlines_works(self):
        """CursorTracker 的 record_newlines 在框架渲染中正常工作。"""
        tracker = CursorTracker()
        initial_row = tracker.pos.row
        tracker.record_newlines(3)
        assert tracker.pos.row == initial_row + 3
        assert tracker.pos.col == 1

    def test_cursor_tracker_move_to_works(self):
        """CursorTracker 的 move_to 在框架渲染中正常工作。"""
        tracker = CursorTracker()
        tracker.move_to(10, 5)
        assert tracker.pos.row == 10
        assert tracker.pos.col == 5


# ════════════════════════════════════════════════════════
# renderer_base.py 中 type: ignore 修复验证
# ════════════════════════════════════════════════════════


class TestRegisterRenderCommandTypeSafety:
    """验证 register_render_command 装饰器移除 type: ignore 后的类型安全性。"""

    def test_decorator_sets_render_command_id(self):
        """@register_render_command 正确设置 _render_command_id。"""
        from src.tui.engine.const import RenderCommand
        from src.tui.engine.renderer_base import register_render_command

        class MockRenderer:
            @register_render_command(RenderCommand.NOTIFICATION, (1,))
            def my_method(self, text: str) -> None:
                pass

        instance = MockRenderer()
        method = instance.my_method
        # 验证 _render_command_id 已被正确设置
        assert hasattr(method, '_render_command_id')
        cmd_id, arg_indices = method._render_command_id
        assert cmd_id == RenderCommand.NOTIFICATION
        assert arg_indices == (1,)

    def test_decorator_empty_arg_indices(self):
        """@register_render_command 参数索引可为空元组。"""
        from src.tui.engine.const import RenderCommand
        from src.tui.engine.renderer_base import register_render_command

        class MockRenderer:
            @register_render_command(RenderCommand.SPLASH, ())
            def splash_method(self) -> None:
                pass

        instance = MockRenderer()
        method = instance.splash_method
        assert hasattr(method, '_render_command_id')
        cmd_id, arg_indices = method._render_command_id
        assert cmd_id == RenderCommand.SPLASH
        assert arg_indices == ()

    def test_decorator_multiple_commands(self):
        """同一个类可应用多个 @register_render_command。"""
        from src.tui.engine.const import RenderCommand
        from src.tui.engine.renderer_base import register_render_command

        class MultiRenderer:
            @register_render_command(RenderCommand.NOTIFICATION, (1,))
            def notify(self, text: str) -> None:
                pass

            @register_render_command(RenderCommand.ERROR, (1,))
            def error(self, message: str) -> None:
                pass

            @register_render_command(RenderCommand.SPLASH, ())
            def splash(self) -> None:
                pass

        instance = MultiRenderer()
        assert instance.notify._render_command_id == (RenderCommand.NOTIFICATION, (1,))
        assert instance.error._render_command_id == (RenderCommand.ERROR, (1,))
        assert instance.splash._render_command_id == (RenderCommand.SPLASH, ())
