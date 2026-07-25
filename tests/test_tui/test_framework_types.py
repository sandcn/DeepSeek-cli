"""测试 Framework 类型协议 — ComponentRegistryProtocol / AnimatorContextProtocol / WidgetTreeProtocol。

测试覆盖：
  - Protocol 定义完整性：3 个 Protocol 均可正常导入
  - ComponentRegistryProtocol：可被 ComponentRegistry 满足
  - AnimatorContextProtocol：可被 AnimatorContext 满足
  - WidgetTreeProtocol：可被 WidgetTree 满足
  - runtime_checkable：isinstance 检查正确
  - Framework 使用 Protocol 类型标注后初始化正常
"""

from __future__ import annotations

from typing import Protocol

import pytest

from src.tui.framework import (
    AnimatorContextProtocol,
    ComponentRegistryProtocol,
    WidgetTreeProtocol,
)


# ════════════════════════════════════════════════════════
# Protocol 导入验证
# ════════════════════════════════════════════════════════


class TestProtocolDefinitions:
    """验证 3 个 Protocol 均可正常导入且为 Protocol 类型。"""

    def test_component_registry_protocol_is_protocol(self):
        """ComponentRegistryProtocol 是 Protocol 子类。"""
        assert isinstance(ComponentRegistryProtocol, type)
        assert issubclass(ComponentRegistryProtocol, Protocol)

    def test_animator_context_protocol_is_protocol(self):
        """AnimatorContextProtocol 是 Protocol 子类。"""
        assert isinstance(AnimatorContextProtocol, type)
        assert issubclass(AnimatorContextProtocol, Protocol)

    def test_widget_tree_protocol_is_protocol(self):
        """WidgetTreeProtocol 是 Protocol 子类。"""
        assert isinstance(WidgetTreeProtocol, type)
        assert issubclass(WidgetTreeProtocol, Protocol)

    def test_all_protocols_importable(self):
        """所有 Protocol 可从 framework 正确导入。"""
        # Protocol 类现在定义在 framework.py 中（非 __all__）
        # 验证它们可直接从 src.tui.framework 导入
        from src.tui.framework import (
            ComponentRegistryProtocol as CRP,
            AnimatorContextProtocol as ACP,
            WidgetTreeProtocol as WTP,
        )
        assert CRP is ComponentRegistryProtocol
        assert ACP is AnimatorContextProtocol
        assert WTP is WidgetTreeProtocol


# ════════════════════════════════════════════════════════
# ComponentRegistryProtocol 兼容性
# ════════════════════════════════════════════════════════


class TestComponentRegistryProtocol:
    """验证 ComponentRegistry 满足 ComponentRegistryProtocol。"""

    def test_component_registry_matches_protocol(self):
        """ComponentRegistry 类匹配 ComponentRegistryProtocol。"""
        from src.tui.core.component_registry import ComponentRegistry
        # 验证 isinstance 运行时检查
        # ComponentRegistry 作为类定义了 get_default() classmethod
        # 但由于 Protocol 中 get_default 是 @classmethod，
        # 对于类级别的 isinstance 检查，需验证实例兼容性
        instance = ComponentRegistry.get_default()
        assert isinstance(instance, ComponentRegistryProtocol)

    def test_protocol_get_default_returns_self_type(self):
        """get_default() 返回的实例同样满足 Protocol。"""
        from src.tui.core.component_registry import ComponentRegistry
        instance = ComponentRegistry.get_default()
        assert isinstance(instance, ComponentRegistryProtocol)

    def test_protocol_resolve_returns_tuple(self):
        """resolve() 返回正确类型的元组。"""
        from src.tui.core.component_registry import ComponentRegistry
        instance = ComponentRegistry.get_default()
        result = instance.resolve(1)  # RenderCommand.NOTIFICATION
        assert result is not None
        method_name, arg_indices = result
        assert isinstance(method_name, str)
        assert isinstance(arg_indices, tuple)

    def test_protocol_resolve_unregistered_returns_none(self):
        """未注册的命令 ID 返回 None。"""
        from src.tui.core.component_registry import ComponentRegistry
        instance = ComponentRegistry.get_default()
        result = instance.resolve(9999)
        assert result is None

    def test_mock_implementation_works(self):
        """Mock 实现可满足 Protocol。"""
        class MockRegistry:
            @classmethod
            def get_default(cls) -> MockRegistry:
                return cls()

            def resolve(self, command_id: int):
                if command_id == 1:
                    return ("test_method", (0,))
                return None

        assert isinstance(MockRegistry, type)
        instance = MockRegistry.get_default()
        assert isinstance(instance, ComponentRegistryProtocol)
        result = instance.resolve(1)
        assert result == ("test_method", (0,))


# ════════════════════════════════════════════════════════
# AnimatorContextProtocol 兼容性
# ════════════════════════════════════════════════════════


class TestAnimatorContextProtocol:
    """验证 AnimatorContext 满足 AnimatorContextProtocol。"""

    def test_animator_context_matches_protocol(self):
        """AnimatorContext 实例匹配 AnimatorContextProtocol。"""
        from src.tui.animation.animator import AnimatorContext
        instance = AnimatorContext.get_default()
        assert isinstance(instance, AnimatorContextProtocol)

    def test_protocol_get_default_returns_self_type(self):
        """get_default() 返回的实例同样满足 Protocol。"""
        from src.tui.animation.animator import AnimatorContext
        instance = AnimatorContext.get_default()
        assert isinstance(instance, AnimatorContextProtocol)

    def test_protocol_frame_is_int(self):
        """frame 属性返回 int 类型。"""
        from src.tui.animation.animator import AnimatorContext
        instance = AnimatorContext.get_default()
        assert isinstance(instance.frame, int)

    def test_protocol_frame_starts_at_zero(self):
        """frame 初始值为 0。"""
        from src.tui.animation.animator import AnimatorContext
        AnimatorContext.reset_default()
        instance = AnimatorContext.get_default()
        assert instance.frame == 0

    def test_mock_implementation_works(self):
        """Mock 实现可满足 Protocol。"""
        class MockAnimator:
            def __init__(self):
                self._frame = 0

            @classmethod
            def get_default(cls) -> MockAnimator:
                return cls()

            @property
            def frame(self) -> int:
                return self._frame

        assert isinstance(MockAnimator, type)
        instance = MockAnimator.get_default()
        assert isinstance(instance, AnimatorContextProtocol)
        assert instance.frame == 0

    def teardown_method(self):
        """每个测试后重置 AnimatorContext 单例。"""
        from src.tui.animation.animator import AnimatorContext
        AnimatorContext.reset_default()


# ════════════════════════════════════════════════════════
# WidgetTreeProtocol 兼容性
# ════════════════════════════════════════════════════════


class TestWidgetTreeProtocol:
    """验证 WidgetTree 满足 WidgetTreeProtocol。"""

    def test_widget_tree_matches_protocol(self):
        """WidgetTree 实例匹配 WidgetTreeProtocol。"""
        from src.tui.widget_base import WidgetTree
        tree = WidgetTree()
        assert isinstance(tree, WidgetTreeProtocol)

    def test_protocol_root_is_none_by_default(self):
        """未设置根节点时 root 为 None。"""
        from src.tui.widget_base import WidgetTree
        tree = WidgetTree()
        assert tree.root is None

    def test_protocol_set_root_and_render(self):
        """set_root() 和 render() 方法可调用。"""
        from src.tui.widget_base import WidgetTree
        from src.tui.render_buffer import RenderBuffer

        tree = WidgetTree()
        # 空树渲染不应报错
        buf = RenderBuffer(10, 5)
        tree.render(buf)  # 空树无副作用

        # 验证方法存在
        assert hasattr(tree, "set_root")
        assert hasattr(tree, "render")

    def test_mock_implementation_works(self):
        """Mock 实现可满足 Protocol。"""
        from src.tui.widget_base import Widget

        class MockWidget:
            def __init__(self):
                pass

        class MockWidgetTree:
            def __init__(self):
                self._root = None

            @property
            def root(self):
                return self._root

            def set_root(self, root):
                self._root = root

            def render(self, buffer):
                pass

        instance = MockWidgetTree()
        assert isinstance(instance, WidgetTreeProtocol)
        assert instance.root is None
        instance.set_root(MockWidget())
        assert instance.root is not None

    def test_protocol_root_accepts_widget(self):
        """root 属性接受 Widget 类型。"""
        from src.tui.widget_base import Widget, WidgetTree

        class TestWidget(Widget):
            def render(self, buffer):
                pass

        tree = WidgetTree()
        widget = TestWidget()
        tree.set_root(widget)
        assert tree.root is widget


# ════════════════════════════════════════════════════════
# Framework 集成验证
# ════════════════════════════════════════════════════════


class TestFrameworkIntegration:
    """验证 Framework 使用 Protocol 类型标注后初始化正常。"""

    def test_framework_init_with_protocol_types(self):
        """Framework.__init__ 使用 Protocol 类型标注后正常初始化。"""
        from src.tui.framework import Framework
        Framework.reset_default()
        fw = Framework.get_default()
        # 验证初始化后各属性为 None（延迟导入）
        # 注意: _registry 和 _stylesheet 已在技术债务清理中移除（死代码）
        assert fw._animator is None
        assert fw._component_registry is None
        assert fw._config is None
        assert fw._widget_tree is None

    def test_framework_get_animator_still_works(self):
        """get_animator() 在 Protocol 替换后仍正常工作。"""
        from src.tui.framework import Framework
        from src.tui.animation.animator import AnimatorContext
        Framework.reset_default()
        AnimatorContext.reset_default()
        fw = Framework.get_default()
        animator = fw.get_animator()
        assert isinstance(animator, AnimatorContextProtocol)
        assert isinstance(animator, AnimatorContext)

    def test_framework_get_component_registry_still_works(self):
        """get_component_registry() 在 Protocol 替换后仍正常工作。"""
        from src.tui.framework import Framework
        from src.tui.core.component_registry import ComponentRegistry
        Framework.reset_default()
        ComponentRegistry.reset_default()
        fw = Framework.get_default()
        registry = fw.get_component_registry()
        assert isinstance(registry, ComponentRegistryProtocol)
        assert isinstance(registry, ComponentRegistry)

    def test_framework_get_config_still_works(self):
        """get_config() 在 Protocol 替换后仍正常工作。"""
        from src.tui.framework import Framework
        from src.tui.config import TuiConfig
        Framework.reset_default()
        fw = Framework.get_default()
        config = fw.get_config()
        assert isinstance(config, TuiConfig)

    def test_framework_widget_tree_lifecycle(self):
        """WidgetTree 创建和使用在 Protocol 替换后仍正常工作。"""
        from src.tui.framework import Framework
        from src.tui.widget_base import Widget, WidgetTree
        from src.tui.render_buffer import RenderBuffer

        Framework.reset_default()
        fw = Framework.get_default()

        # 初始无树
        assert fw.get_widget_tree() is None

        # 创建 widget 树
        class TestWidget(Widget):
            def render(self, buffer):
                buffer.write(0, 0, "test")

        widget = TestWidget()
        fw.mount_widget(widget)
        tree = fw.get_widget_tree()
        assert isinstance(tree, WidgetTreeProtocol)
        assert isinstance(tree, WidgetTree)

    def teardown_method(self):
        """每个测试后重置 Framework 单例。"""
        from src.tui.framework import Framework
        from src.tui.animation.animator import AnimatorContext
        from src.tui.core.component_registry import ComponentRegistry
        Framework.reset_default()
        AnimatorContext.reset_default()
        ComponentRegistry.reset_default()
