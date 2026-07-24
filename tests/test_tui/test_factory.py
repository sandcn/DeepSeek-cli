"""测试 ChatUI 组件工厂 — _create_framework_components / _create_chat_ui_components。"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from src.tui.consumer.factory import (
    _create_framework_components,
    _create_chat_ui_components,
    _FrameworkComponents,
    _ChatUIComponents,
)
from src.tui.state.render_state import ChatRenderState
from src.tui.testing import tui_test_env


# ═══════════════════════════════════════════════════════════
# _FrameworkComponents
# ═══════════════════════════════════════════════════════════

class TestFrameworkComponents:
    """_FrameworkComponents 容器测试。"""

    def test_has_attributes(self):
        """_FrameworkComponents 包含三个属性。"""
        fw = _FrameworkComponents(
            output_adapter=MagicMock(),
            renderer=MagicMock(),
            engine=MagicMock(),
        )
        assert hasattr(fw, "output_adapter")
        assert hasattr(fw, "renderer")
        assert hasattr(fw, "engine")


# ═══════════════════════════════════════════════════════════
# _ChatUIComponents
# ═══════════════════════════════════════════════════════════

class TestChatUIComponents:
    """_ChatUIComponents 容器测试。"""

    def test_has_all_attributes(self):
        """_ChatUIComponents 包含 8 个子系统属性。"""
        c = _ChatUIComponents(
            rs=MagicMock(),
            cursor_tracker=MagicMock(),
            bottom_bar=MagicMock(),
            output_adapter=MagicMock(),
            tui_renderer=MagicMock(),
            engine=MagicMock(),
            dispatcher=MagicMock(),
            cmpl_handler=MagicMock(),
        )
        assert hasattr(c, "rs")
        assert hasattr(c, "cursor_tracker")
        assert hasattr(c, "bottom_bar")
        assert hasattr(c, "output_adapter")
        assert hasattr(c, "tui_renderer")
        assert hasattr(c, "engine")
        assert hasattr(c, "dispatcher")
        assert hasattr(c, "cmpl_handler")


# ═══════════════════════════════════════════════════════════
# _create_chat_ui_components
# ═══════════════════════════════════════════════════════════

class TestCreateChatUIComponents:
    """_create_chat_ui_components 集成测试。"""

    def test_returns_chat_ui_components(self):
        """_create_chat_ui_components 返回 _ChatUIComponents 实例。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert isinstance(components, _ChatUIComponents)

    def test_rs_is_chat_render_state(self):
        """components.rs 是 ChatRenderState 实例。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert isinstance(components.rs, ChatRenderState)

    def test_engine_is_tui_engine(self):
        """components.engine 是 TuiEngine 实例。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            from src.tui.engine.engine import TuiEngine
            assert isinstance(components.engine, TuiEngine)

    def test_dispatcher_is_event_dispatcher(self):
        """components.dispatcher 是 EventDispatcher 实例。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            from src.tui.engine.dispatcher import EventDispatcher
            assert isinstance(components.dispatcher, EventDispatcher)

    def test_cursor_tracker_not_none(self):
        """components.cursor_tracker 不为 None。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert components.cursor_tracker is not None

    def test_bottom_bar_not_none(self):
        """components.bottom_bar 不为 None。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert components.bottom_bar is not None

    def test_output_adapter_not_none(self):
        """components.output_adapter 不为 None。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert components.output_adapter is not None

    def test_tui_renderer_not_none(self):
        """components.tui_renderer 不为 None。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert components.tui_renderer is not None

    def test_cmpl_handler_not_none(self):
        """components.cmpl_handler 不为 None。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert components.cmpl_handler is not None

    def test_same_output_adapter_between_renderer_and_fw(self):
        """tui_renderer 和 components 的 output_adapter 是同一个。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            assert components.tui_renderer.output_adapter is components.output_adapter

    def test_engine_push_cmd_works(self):
        """引擎的 push_cmd 可正常入队。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            from src.tui.engine.const import RenderCommand
            components.engine.push_cmd((RenderCommand.NOTIFICATION, "test"))
            assert components.engine._cmd_queue.qsize() > 0

    def test_dispatcher_push_cmd_linked(self):
        """dispatcher 的 push_cmd 连接到 engine。"""
        with tui_test_env():
            components = _create_chat_ui_components()
            from src.tui.engine.const import RenderCommand
            # dispatcher 的 push_cmd 是 engine.push_cmd
            components.dispatcher._push_cmd((RenderCommand.NOTIFICATION, "test"))
            assert components.engine._cmd_queue.qsize() > 0

    def test_create_with_event_bus(self):
        """传入 event_bus 时使用指定实例。"""
        with tui_test_env():
            from src.tui.events.event_bus import DisplayEventBus
            bus = DisplayEventBus.get_default()
            components = _create_chat_ui_components(event_bus=bus)
            assert components is not None


# ═══════════════════════════════════════════════════════════
# _create_framework_components
# ═══════════════════════════════════════════════════════════

class TestCreateFrameworkComponents:
    """_create_framework_components 测试。"""

    def test_returns_framework_components(self):
        """_create_framework_components 返回 _FrameworkComponents。"""
        with tui_test_env():
            from src.tui.widgets.cursor_tracker import CursorTracker
            from src.tui.widgets.bottom_bar import _BottomBar

            rs = ChatRenderState()
            output_adapter = MagicMock()
            bb = MagicMock()
            cursor_tracker = MagicMock(spec=CursorTracker)

            fw = _create_framework_components(
                rs=rs,
                output_adapter=output_adapter,
                bottom_bar=bb,
                cursor_tracker=cursor_tracker,
            )
            assert isinstance(fw, _FrameworkComponents)

    def test_renderer_has_output_adapter(self):
        """renderer 引用了传入的 output_adapter。"""
        with tui_test_env():
            rs = ChatRenderState()
            output_adapter = MagicMock()
            bb = MagicMock()
            cursor_tracker = MagicMock()

            fw = _create_framework_components(
                rs=rs,
                output_adapter=output_adapter,
                bottom_bar=bb,
                cursor_tracker=cursor_tracker,
            )
            assert fw.renderer.output_adapter is output_adapter

    def test_engine_has_renderer(self):
        """engine 引用了创建的 renderer。"""
        with tui_test_env():
            rs = ChatRenderState()
            output_adapter = MagicMock()
            bb = MagicMock()
            cursor_tracker = MagicMock()

            fw = _create_framework_components(
                rs=rs,
                output_adapter=output_adapter,
                bottom_bar=bb,
                cursor_tracker=cursor_tracker,
            )
            # engine 的 _renderer 应该是 fw.renderer
            assert fw.engine._renderer is fw.renderer

    def test_engine_has_bottom_bar(self):
        """engine 引用了传入的 bottom_bar。"""
        with tui_test_env():
            rs = ChatRenderState()
            output_adapter = MagicMock()
            bb = MagicMock()
            cursor_tracker = MagicMock()

            fw = _create_framework_components(
                rs=rs,
                output_adapter=output_adapter,
                bottom_bar=bb,
                cursor_tracker=cursor_tracker,
            )
            assert fw.engine._bb is bb

    def test_engine_has_cursor_tracker(self):
        """engine 引用了传入的 cursor_tracker。"""
        with tui_test_env():
            rs = ChatRenderState()
            output_adapter = MagicMock()
            bb = MagicMock()
            cursor_tracker = MagicMock()

            fw = _create_framework_components(
                rs=rs,
                output_adapter=output_adapter,
                bottom_bar=bb,
                cursor_tracker=cursor_tracker,
            )
            assert fw.engine._cursor_tracker is cursor_tracker
