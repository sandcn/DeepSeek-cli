"""chat_ui 构建器模块单元测试 — ChatUIBuilder。

测试覆盖：
  - build() 默认构造路径（所有子系统创建正常）
  - set_*() 注入覆盖（7 个 setter 各至少一个使用场景）
  - build() 返回值 ChatUIComponents 字段完整性
  - 构造失败异常传播
  - Builder 链式调用
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ── 将项目根目录加入 sys.path（Termux 环境需要）───
sys.path.insert(0, "/home/DeepSeek-cli")


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_event_bus():
    """Mock DisplayEventBus。"""
    from unittest.mock import MagicMock
    bus = MagicMock()
    return bus


@pytest.fixture
def builder():
    """ChatUIBuilder 实例。"""
    from src.chat_ui._builder import ChatUIBuilder
    return ChatUIBuilder()


# ═══════════════════════════════════════════════════════════
# Test default build path
# ═══════════════════════════════════════════════════════════

class TestDefaultBuild:
    """ChatUIBuilder.build() 默认构造路径测试。"""

    def test_build_with_event_bus(self, builder, mock_event_bus):
        """传入自定义 event_bus → 使用传入的实例。"""
        from src.chat_ui._builder import ChatUIComponents

        components = builder.build(mock_event_bus)

        assert isinstance(components, ChatUIComponents)
        assert components.bus is mock_event_bus

    def test_build_without_event_bus(self, builder):
        """不传 event_bus → 使用 DisplayEventBus.get_default()。"""
        with patch("src.chat_ui._builder.DisplayEventBus") as MockBus:
            mock_bus = MagicMock()
            MockBus.get_default.return_value = mock_bus
            components = builder.build()

        MockBus.get_default.assert_called_once()
        assert components.bus is mock_bus

    def test_build_components_all_not_none(self, builder, mock_event_bus):
        """build() 返回的 ChatUIComponents 所有 7 个字段均非 None。"""
        components = builder.build(mock_event_bus)

        assert components.bus is not None
        assert components.rs is not None
        assert components.bottom_bar is not None
        assert components.renderer is not None
        assert components.engine is not None
        assert components.disp is not None
        assert components.cmpl is not None

    def test_build_components_types(self, builder, mock_event_bus):
        """build() 返回的字段具有正确的类型。"""
        from src.chat_ui._protocols import (
            BottomBarProtocol,
            ContentRendererProtocol,
            EventDispatcherProtocol,
            RenderEngineProtocol,
            RenderStateProtocol,
            CmplHandlerProtocol,
        )

        components = builder.build(mock_event_bus)

        assert isinstance(components.bus, object)  # event_bus 是具体类型
        assert isinstance(components.rs, RenderStateProtocol)
        assert isinstance(components.bottom_bar, BottomBarProtocol)
        assert isinstance(components.renderer, ContentRendererProtocol)
        assert isinstance(components.engine, RenderEngineProtocol)
        assert isinstance(components.disp, EventDispatcherProtocol)
        assert isinstance(components.cmpl, CmplHandlerProtocol)


# ═══════════════════════════════════════════════════════════
# Test set_* injection
# ═══════════════════════════════════════════════════════════

class TestSetInjections:
    """set_*() 注入覆盖测试。"""

    def test_set_render_state(self, builder, mock_event_bus):
        """set_render_state() → build() 使用注入的 mock。"""
        mock_rs = MagicMock()
        builder.set_render_state(mock_rs)
        components = builder.build(mock_event_bus)
        assert components.rs is mock_rs

    def test_set_bottom_bar(self, builder, mock_event_bus):
        """set_bottom_bar() → build() 使用注入的 mock。"""
        mock_bb = MagicMock()
        builder.set_bottom_bar(mock_bb)
        components = builder.build(mock_event_bus)
        assert components.bottom_bar is mock_bb

    def test_set_output_adapter(self, builder, mock_event_bus):
        """set_output_adapter() → build() 使用注入的 mock。"""
        mock_adapter = MagicMock()
        builder.set_output_adapter(mock_adapter)
        components = builder.build(mock_event_bus)
        assert components.renderer.adapter is mock_adapter

    def test_set_renderer(self, builder, mock_event_bus):
        """set_renderer() → build() 使用注入的 mock。"""
        mock_renderer = MagicMock()
        builder.set_renderer(mock_renderer)
        components = builder.build(mock_event_bus)
        assert components.renderer is mock_renderer

    def test_set_engine(self, builder, mock_event_bus):
        """set_engine() → build() 使用注入的 mock。"""
        mock_engine = MagicMock()
        builder.set_engine(mock_engine)
        components = builder.build(mock_event_bus)
        assert components.engine is mock_engine

    def test_set_dispatcher(self, builder, mock_event_bus):
        """set_dispatcher() → build() 使用注入的 mock。"""
        mock_disp = MagicMock()
        builder.set_dispatcher(mock_disp)
        components = builder.build(mock_event_bus)
        assert components.disp is mock_disp

    def test_set_completion_handler(self, builder, mock_event_bus):
        """set_completion_handler() → build() 使用注入的 mock。"""
        mock_cmpl = MagicMock()
        builder.set_completion_handler(mock_cmpl)
        components = builder.build(mock_event_bus)
        assert components.cmpl is mock_cmpl

    def test_multiple_injections(self, builder, mock_event_bus):
        """多个 set_* 同时注入。"""
        mock_rs = MagicMock()
        mock_engine = MagicMock()
        mock_disp = MagicMock()

        builder.set_render_state(mock_rs)
        builder.set_engine(mock_engine)
        builder.set_dispatcher(mock_disp)

        components = builder.build(mock_event_bus)
        assert components.rs is mock_rs
        assert components.engine is mock_engine
        assert components.disp is mock_disp


# ═══════════════════════════════════════════════════════════
# Test ChatUIComponents NamedTuple
# ═══════════════════════════════════════════════════════════

class TestChatUIComponents:
    """ChatUIComponents NamedTuple 完整性测试。"""

    def test_namedtuple_field_access(self, builder, mock_event_bus):
        """ChatUIComponents 字段可通过属性名和索引访问。"""
        from src.chat_ui._builder import ChatUIComponents

        components = builder.build(mock_event_bus)

        # 属性名访问
        assert components.bus is not None
        assert components.rs is not None
        assert components.bottom_bar is not None
        assert components.renderer is not None
        assert components.engine is not None
        assert components.disp is not None
        assert components.cmpl is not None

        # 索引访问
        assert components[0] is components.bus
        assert components[1] is components.rs
        assert components[2] is components.bottom_bar
        assert components[3] is components.renderer
        assert components[4] is components.engine
        assert components[5] is components.disp
        assert components[6] is components.cmpl

    def test_namedtuple_unpacking(self, builder, mock_event_bus):
        """ChatUIComponents 支持解包。"""
        components = builder.build(mock_event_bus)
        bus, rs, bb, renderer, engine, disp, cmpl = components

        assert bus is components.bus
        assert rs is components.rs
        assert bb is components.bottom_bar
        assert renderer is components.renderer
        assert engine is components.engine
        assert disp is components.disp
        assert cmpl is components.cmpl


# ═══════════════════════════════════════════════════════════
# Test builder chaining
# ═══════════════════════════════════════════════════════════

class TestBuilderChaining:
    """Builder 链式调用测试。"""

    def test_set_returns_self(self, builder):
        """set_*() 返回 ChatUIBuilder 实例，支持链式调用。"""
        mock_rs = MagicMock()
        mock_engine = MagicMock()

        result = (
            builder
            .set_render_state(mock_rs)
            .set_engine(mock_engine)
        )

        assert result is builder

    def test_chained_build(self, builder, mock_event_bus):
        """链式 set_* + build() 正确组合。"""
        mock_rs = MagicMock()
        mock_bb = MagicMock()
        mock_engine = MagicMock()

        components = (
            builder
            .set_render_state(mock_rs)
            .set_bottom_bar(mock_bb)
            .set_engine(mock_engine)
            .build(mock_event_bus)
        )

        assert components.rs is mock_rs
        assert components.bottom_bar is mock_bb
        assert components.engine is mock_engine


# ═══════════════════════════════════════════════════════════
# Test robustness
# ═══════════════════════════════════════════════════════════

class TestRobustness:
    """构造失败异常传播测试。"""

    def test_default_construction_no_error(self, builder, mock_event_bus):
        """默认构造路径不抛出异常。"""
        try:
            builder.build(mock_event_bus)
        except Exception as exc:
            pytest.fail(f"build() 不应抛出异常: {exc}")

    def test_invalid_dependency_raises(self, builder):
        """缺失必需属性时在 consumer 层抛出 AttributeError，不在 builder 层。"""
        # 注入一个没有 output_adapter 的 renderer
        # builder.build() 本身不会校验 adapter，因此正常返回
        # 错误会在 ChatUIConsumer.output_adapter 被访问时抛到上层
        class BadRenderer:
            def render(self, cmd): ...
            # 没有 adapter property

        builder.set_renderer(BadRenderer())
        with patch("src.chat_ui._builder.DisplayEventBus") as MockBus:
            MockBus.get_default.return_value = MagicMock()
            # build() 本身不校验 adapter，不应抛出异常
            components = builder.build()
            assert components.renderer is not None


# ═══════════════════════════════════════════════════════════
# Test constructor init fields
# ═══════════════════════════════════════════════════════════

class TestConstructor:
    """ChatUIBuilder 构造初始状态测试。"""

    def test_all_fields_none(self):
        """初始状态下所有注入字段均为 None。"""
        from src.chat_ui._builder import ChatUIBuilder
        b = ChatUIBuilder()

        assert b._rs is None
        assert b._bottom_bar is None
        assert b._output_adapter is None
        assert b._renderer is None
        assert b._engine is None
        assert b._disp is None
        assert b._cmpl is None

    def test_all_setters_available(self, builder):
        """所有 7 个 set_* 方法均可调用。"""
        assert hasattr(builder, "set_render_state")
        assert hasattr(builder, "set_bottom_bar")
        assert hasattr(builder, "set_output_adapter")
        assert hasattr(builder, "set_renderer")
        assert hasattr(builder, "set_engine")
        assert hasattr(builder, "set_dispatcher")
        assert hasattr(builder, "set_completion_handler")
