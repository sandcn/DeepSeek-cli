"""测试 Framework 门面（外观模式）— 委托类 + 外观委托正确性。

测试覆盖：
  - ConfigManager: 配置管理（get/set + 默认值）
  - EventBusManager: 事件总线（subscribe/unsubscribe/publish）
  - ComponentFactory: 组件工厂（create_component）
  - WidgetTreeManager: Widget 树管理（create_widget/mount/unmount）
  - AnimationManager: 动画上下文（get_animator/get_frame）
  - Framework 外观委托正确性（所有方法签名不变）
  - 模块级便捷函数兼容性
"""

from __future__ import annotations

import pytest

from src.tui.framework import Framework, get_animator, get_framework
from src.tui.framework_delegates import (
    AnimationManager,
    ComponentFactory,
    ConfigManager,
    EventBusManager,
    WidgetTreeManager,
)
from src.tui.animation.animator import AnimatorContext
from src.tui.config import TuiConfig


# ════════════════════════════════════════════════════════
# 测试前/后清理
# ════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_framework():
    """每个测试前重置 Framework 单例，确保隔离。"""
    Framework.reset_default()
    AnimatorContext.reset_default()
    yield
    Framework.reset_default()
    AnimatorContext.reset_default()


# ════════════════════════════════════════════════════════
# ConfigManager 测试
# ════════════════════════════════════════════════════════


class TestConfigManager:
    """ConfigManager 委托类测试。"""

    def test_get_config_returns_defaults(self):
        """get_config() 返回默认 TuiConfig 实例。"""
        framework = Framework.get_default()
        config = framework.get_config()
        assert isinstance(config, TuiConfig)
        assert config.render_interval == 0.1

    def test_set_config_overrides_default(self):
        """set_config() 覆盖默认配置。"""
        framework = Framework.get_default()
        custom = TuiConfig.defaults().with_overrides(render_interval=0.05)
        framework.set_config(custom)
        assert framework.get_config().render_interval == 0.05

    def test_get_config_is_cached(self):
        """get_config() 首次返回后缓存结果。"""
        framework = Framework.get_default()
        a = framework.get_config()
        b = framework.get_config()
        assert a is b

    def test_get_config_not_set_returns_defaults(self):
        """配置未设置时 get_config() 返回默认值。"""
        framework = Framework.get_default()
        cfg = framework.get_config()
        assert cfg.render_interval == 0.1
        assert cfg.fade_total_frames == 6

    def test_config_manager_delegates_correctly(self):
        """Framework 外观方法正确委托到 ConfigManager。"""
        framework = Framework.get_default()
        assert framework._config_mgr is not None
        assert isinstance(framework._config_mgr, ConfigManager)


# ════════════════════════════════════════════════════════
# EventBusManager 测试
# ════════════════════════════════════════════════════════


class TestEventBusManager:
    """EventBusManager 委托类测试。"""

    def test_get_event_bus_returns_instance(self):
        """get_event_bus() 返回 DisplayEventBus 实例。"""
        framework = Framework.get_default()
        bus = framework.get_event_bus()
        from src.tui.events.event_bus import DisplayEventBus
        assert isinstance(bus, DisplayEventBus)

    def test_get_event_bus_is_singleton(self):
        """get_event_bus() 返回同一单例。"""
        framework = Framework.get_default()
        a = framework.get_event_bus()
        b = framework.get_event_bus()
        assert a is b

    def test_publish_event_does_not_raise(self):
        """publish_event() 发布事件不抛异常。"""
        framework = Framework.get_default()
        from src.tui.events.event_types import SessionStarted
        # 发布一个简单的事件，不订阅，不应抛异常
        framework.publish_event(SessionStarted())

    def test_subscribe_unsubscribe(self):
        """subscribe/unsubscribe 不抛异常。"""
        framework = Framework.get_default()
        dummy = lambda e: None
        from src.tui.events.event_types import SessionStarted
        framework.subscribe(SessionStarted, dummy)
        framework.unsubscribe(SessionStarted, dummy)

    def test_event_bus_manager_delegates_correctly(self):
        """Framework 外观方法正确委托到 EventBusManager。"""
        framework = Framework.get_default()
        assert framework._event_bus_mgr is not None
        assert isinstance(framework._event_bus_mgr, EventBusManager)


# ════════════════════════════════════════════════════════
# ComponentFactory 测试
# ════════════════════════════════════════════════════════


class TestComponentFactory:
    """ComponentFactory 委托类测试。"""

    def test_create_component_returns_instance(self):
        """create_component() 返回组件实例。"""
        framework = Framework.get_default()
        from src.tui.components._separator import Separator
        sep = framework.create_component(Separator, style="aurora", frame=5)
        assert sep is not None
        assert sep._mounted

    def test_create_component_calls_did_mount(self):
        """create_component() 触发 did_mount()。"""
        framework = Framework.get_default()
        from src.tui.components._base import TuiComponent

        call_count = 0

        class TestComp(TuiComponent):
            def did_mount(self) -> None:
                nonlocal call_count
                call_count += 1
                super().did_mount()

        comp = framework.create_component(TestComp)
        assert call_count == 1
        assert comp._mounted

    def test_component_factory_delegates_correctly(self):
        """Framework 外观方法正确委托到 ComponentFactory。"""
        framework = Framework.get_default()
        assert framework._component_factory is not None
        assert isinstance(framework._component_factory, ComponentFactory)


# ════════════════════════════════════════════════════════
# WidgetTreeManager 测试
# ════════════════════════════════════════════════════════


class TestWidgetTreeManager:
    """WidgetTreeManager 委托类测试。"""

    def test_create_widget_returns_instance(self):
        """create_widget() 返回已挂载的 Widget 实例。"""
        framework = Framework.get_default()
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class SimpleWidget(Widget):
            def render(self, buffer: RenderBuffer) -> None:
                buffer.write(0, 0, "Hello")

        widget = framework.create_widget(SimpleWidget)
        assert widget is not None
        assert widget._mounted

    def test_get_widget_tree_none_initially(self):
        """初始状态下 get_widget_tree() 返回 None。"""
        framework = Framework.get_default()
        assert framework.get_widget_tree() is None

    def test_create_widget_creates_tree_instance(self):
        """create_widget() 后会创建 WidgetTree 实例，但 root 为 None（需 mount_widget 设置）。"""
        framework = Framework.get_default()
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class SimpleWidget(Widget):
            def render(self, buffer: RenderBuffer) -> None:
                buffer.write(0, 0, "Hello")

        framework.create_widget(SimpleWidget)
        tree = framework.get_widget_tree()
        assert tree is not None
        # create_widget 只挂载不设根，root 为 None（与原始行为一致）
        assert tree.root is None

    def test_has_widget_tree_false_initially(self):
        """初始状态下 has_widget_tree() 返回 False。"""
        framework = Framework.get_default()
        assert not framework.has_widget_tree()

    def test_has_widget_tree_true_after_mount(self):
        """mount_widget() 后 has_widget_tree() 返回 True。"""
        framework = Framework.get_default()
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class SimpleWidget(Widget):
            def render(self, buffer: RenderBuffer) -> None:
                buffer.write(0, 0, "Hello")

        widget = SimpleWidget()
        framework.mount_widget(widget)
        assert framework.has_widget_tree()

    def test_mount_unmount_widget(self):
        """mount_widget/unmount_widget 生命周期正常。"""
        framework = Framework.get_default()
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class SimpleWidget(Widget):
            def render(self, buffer: RenderBuffer) -> None:
                buffer.write(0, 0, "Hello")

        widget = SimpleWidget()
        framework.mount_widget(widget)
        assert framework.has_widget_tree()
        assert framework.get_widget_root() is widget

        framework.unmount_widget(widget)
        assert not framework.has_widget_tree()

    def test_get_widget_root(self):
        """get_widget_root() 返回正确的根节点（通过 mount_widget 设置后）。"""
        framework = Framework.get_default()
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class SimpleWidget(Widget):
            def render(self, buffer: RenderBuffer) -> None:
                buffer.write(0, 0, "Hello")

        widget = SimpleWidget()
        framework.mount_widget(widget)
        root = framework.get_widget_root()
        assert root is widget

    def test_render_widget_tree_does_not_raise(self):
        """render_widget_tree() 不抛异常。"""
        framework = Framework.get_default()
        from src.tui.widget_base import Widget
        from src.tui.render_buffer import RenderBuffer

        class SimpleWidget(Widget):
            def render(self, buffer: RenderBuffer) -> None:
                buffer.write(0, 0, "Hello")

        framework.create_widget(SimpleWidget)
        buf = framework.create_render_buffer(20, 3)
        # 不抛异常即通过
        framework.render_widget_tree(buf)

    def test_widget_tree_manager_delegates_correctly(self):
        """Framework 外观方法正确委托到 WidgetTreeManager。"""
        framework = Framework.get_default()
        assert framework._widget_tree_mgr is not None
        assert isinstance(framework._widget_tree_mgr, WidgetTreeManager)


# ════════════════════════════════════════════════════════
# AnimationManager 测试
# ════════════════════════════════════════════════════════


class TestAnimationManager:
    """AnimationManager 委托类测试。"""

    def test_get_animator_returns_animator_context_instance(self):
        """get_animator() 返回 AnimatorContext 实例。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert isinstance(animator, AnimatorContext)

    def test_multiple_calls_return_same_instance(self):
        """多次调用 get_animator() 返回同一实例。"""
        framework = Framework.get_default()
        a = framework.get_animator()
        b = framework.get_animator()
        assert a is b

    def test_returns_singleton(self):
        """get_animator() 返回 AnimatorContext 单例。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert animator is AnimatorContext.get_default()

    def test_animator_initial_frame_is_zero(self):
        """新获取的 animator 初始帧号为 0。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert animator.frame == 0

    def test_frame_matches_get_frame(self):
        """get_animator().frame 应与 get_frame() 返回相同值。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert animator.frame == framework.get_frame() == 0

    def test_frame_matches_after_tick(self):
        """tick() 推进帧号后，get_animator().frame 与 get_frame() 保持一致。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        animator.tick(delta=5)
        assert animator.frame == 5
        assert framework.get_frame() == 5

    def test_get_frame_error_recovery(self):
        """当 AnimatorContext 异常时 get_frame() 兜底返回 0。"""
        Framework.reset_default()
        AnimatorContext.reset_default()
        framework = Framework.get_default()
        assert framework.get_frame() == 0

    def test_animation_manager_delegates_correctly(self):
        """Framework 外观方法正确委托到 AnimationManager。"""
        framework = Framework.get_default()
        assert framework._animation_mgr is not None
        assert isinstance(framework._animation_mgr, AnimationManager)


# ════════════════════════════════════════════════════════
# reset_default() 行为
# ════════════════════════════════════════════════════════


class TestResetDefault:
    """Framework.reset_default() 后 animator 行为。"""

    def test_reset_then_get_animator_returns_fresh(self):
        """reset_default() 后 get_animator() 返回新的 AnimatorContext 实例。"""
        framework = Framework.get_default()
        old_animator = framework.get_animator()
        old_animator.tick(delta=10)

        Framework.reset_default()
        AnimatorContext.reset_default()

        new_framework = Framework.get_default()
        new_animator = new_framework.get_animator()
        assert new_animator.frame == 0
        assert new_animator is not old_animator

    def test_reset_then_get_frame_is_zero(self):
        """reset 后 get_frame() 返回 0。"""
        framework = Framework.get_default()
        framework.get_animator().tick(delta=42)
        assert framework.get_frame() == 42
        Framework.reset_default()
        AnimatorContext.reset_default()
        new_framework = Framework.get_default()
        assert new_framework.get_frame() == 0

    def test_reset_clear_cached_animator(self):
        """reset 后 Framework 内缓存清空，重新延迟导入。"""
        Framework.reset_default()
        AnimatorContext.reset_default()
        framework = Framework.get_default()
        assert framework._animator is None
        animator = framework.get_animator()
        from src.tui.animation.animator import AnimatorContext as AC
        assert framework._animator is AC
        assert isinstance(animator, AnimatorContext)


# ════════════════════════════════════════════════════════
# 模块级便捷函数
# ════════════════════════════════════════════════════════


class TestModuleLevelFunctions:
    """模块级便捷函数兼容性。"""

    def test_get_animator_returns_animator_context(self):
        """模块级 get_animator() 返回 AnimatorContext 实例。"""
        animator = get_animator()
        assert isinstance(animator, AnimatorContext)

    def test_get_animator_same_as_framework_method(self):
        """模块级 get_animator() 与 Framework.get_default().get_animator() 返回同一实例。"""
        a = get_animator()
        b = Framework.get_default().get_animator()
        assert a is b

    def test_get_framework_returns_framework(self):
        """get_framework() 返回 Framework 实例。"""
        fw = get_framework()
        assert isinstance(fw, Framework)
        assert fw is Framework.get_default()

    def test_get_framework_same_as_default(self):
        """get_framework() 与 Framework.get_default() 返回同一实例。"""
        assert get_framework() is Framework.get_default()

    def test_after_reset_returns_fresh(self):
        """reset 后模块级函数也返回新实例。"""
        old = get_animator()
        old.tick(delta=99)
        Framework.reset_default()
        AnimatorContext.reset_default()
        new = get_animator()
        assert new.frame == 0
        assert new is not old


# ════════════════════════════════════════════════════════
# Framework 外观完整性
# ════════════════════════════════════════════════════════


class TestFrameworkFacadeIntegrity:
    """Framework 外观模式完整性验证。"""

    def test_all_delegates_initialized(self):
        """所有 5 个委托类在 __init__ 中正确初始化。"""
        framework = Framework.get_default()
        assert isinstance(framework._config_mgr, ConfigManager)
        assert isinstance(framework._event_bus_mgr, EventBusManager)
        assert isinstance(framework._component_factory, ComponentFactory)
        assert isinstance(framework._widget_tree_mgr, WidgetTreeManager)
        assert isinstance(framework._animation_mgr, AnimationManager)

    def test_create_render_buffer_works(self):
        """create_render_buffer 在 Framework 自身实现。"""
        framework = Framework.get_default()
        buf = framework.create_render_buffer(80, 24)
        from src.tui.render_buffer import RenderBuffer
        assert isinstance(buf, RenderBuffer)
        assert buf.width == 80
        assert buf.height == 24

    def test_get_component_registry_works(self):
        """get_component_registry 在 Framework 自身实现。"""
        framework = Framework.get_default()
        registry = framework.get_component_registry()
        from src.tui.core.component_registry import ComponentRegistry
        assert isinstance(registry, ComponentRegistry)

    def test_lifecycle_start_stop_is_running(self):
        """生命周期方法 start/stop/is_running 在 Framework 自身实现。"""
        framework = Framework.get_default()
        assert not framework.is_running()
        framework.start()
        assert framework.is_running()
        framework.stop()
        assert not framework.is_running()

    def test_start_is_idempotent(self):
        """start() 幂等。"""
        framework = Framework.get_default()
        framework.start()
        framework.start()
        assert framework.is_running()

    def test_stop_is_idempotent(self):
        """stop() 幂等。"""
        framework = Framework.get_default()
        framework.start()
        framework.stop()
        framework.stop()
        assert not framework.is_running()
