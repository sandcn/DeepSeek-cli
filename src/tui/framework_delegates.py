"""Framework 委托类 — 将 8 职责拆分为 5 个单一职责委托类。

``Framework`` 原承担 8 个职责，现拆分为 5 个委托类，Framework 作为外观门面
保持所有公开 API 签名不变。

委托类清单：
  - ``ConfigManager`` — 配置 get/set + 默认值
  - ``EventBusManager`` — 事件总线 subscribe/unsubscribe/publish 委托
  - ``ComponentFactory`` — create_component + 组件生命周期
  - ``WidgetTreeManager`` — create_widget/mount/unmount/get_widget_tree/render
  - ``AnimationManager`` — get_animator/get_frame + AnimatorContext 单例

设计原则：
  - 单一职责：每个委托类仅负责一个职责域
  - 向后兼容：所有公开 API 签名与原有 Framework 方法完全一致
  - 延迟导入：各委托类内部保持延迟导入，避免启动时循环依赖
  - 最小耦合：委托类通过 Framework 实例引用访问共享状态
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ._animator import AnimatorContext
    from .config import TuiConfig
    from .render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "ConfigManager",
    "EventBusManager",
    "ComponentFactory",
    "WidgetTreeManager",
    "AnimationManager",
]


# ═══════════════════════════════════════════════════════════
# ConfigManager — 配置管理
# ═══════════════════════════════════════════════════════════


class ConfigManager:
    """配置管理委托类 — get/set + 默认值。

    职责：
      - get_config(): 获取当前 TUI 配置（延迟加载默认值）
      - set_config(): 设置 TUI 配置
    """

    def __init__(self, framework) -> None:
        """初始化配置管理器。

        Args:
            framework: Framework 实例引用（访问 _config 共享状态）。
        """
        self._framework = framework

    def get_config(self) -> TuiConfig:
        """获取当前 TUI 配置。

        返回 TuiConfig 默认配置。可通过 set_config() 覆盖。

        Returns:
            TuiConfig 实例（frozen=True，不可变）。
        """
        if self._framework._config is None:
            from .config import TuiConfig
            self._framework._config = TuiConfig.defaults()
        return self._framework._config

    def set_config(self, config: TuiConfig) -> None:
        """设置 TUI 配置。

        Args:
            config: TuiConfig 实例。
        """
        self._framework._config = config


# ═══════════════════════════════════════════════════════════
# EventBusManager — 事件总线委托
# ═══════════════════════════════════════════════════════════


class EventBusManager:
    """事件总线委托类 — subscribe/unsubscribe/publish。

    职责：
      - get_event_bus(): 获取全局 DisplayEventBus 实例
      - publish_event(): 便捷发布事件
      - subscribe(): 订阅事件总线事件
      - unsubscribe(): 取消订阅事件总线事件
    """

    def __init__(self, framework) -> None:
        """初始化事件总线管理器。

        Args:
            framework: Framework 实例引用。
        """
        self._framework = framework

    def get_event_bus(self):
        """获取全局 DisplayEventBus 实例。

        Returns:
            DisplayEventBus 单例实例。
        """
        from .events.event_bus import DisplayEventBus
        return DisplayEventBus.get_default()

    def publish_event(self, event) -> None:
        """发布事件到 DisplayEventBus。

        Args:
            event: DisplayEvent 子类实例。
        """
        self.get_event_bus().publish(event)

    def subscribe(
        self,
        event_type: type,
        callback: Callable[..., Any] | None = None,
    ) -> None:
        """订阅事件总线事件。

        委托 DisplayEventBus.get_default().subscribe() 注册事件监听。

        Args:
            event_type: 事件类型类。
            callback: 回调函数。
        """
        try:
            from .events.event_bus import DisplayEventBus
            DisplayEventBus.get_default().subscribe(callback, event_type=event_type)
        except ModuleNotFoundError as exc:
            _logger.warning("subscribe 失败（DisplayEventBus 模块未就绪）: %s", exc)

    def unsubscribe(
        self,
        event_type: type,
        callback: Callable[..., Any] | None = None,
    ) -> None:
        """取消订阅事件总线事件。

        委托 DisplayEventBus.get_default().unsubscribe() 取消监听。

        Args:
            event_type: 事件类型类。
            callback: 之前注册的回调函数。
        """
        try:
            from .events.event_bus import DisplayEventBus
            DisplayEventBus.get_default().unsubscribe(callback, event_type=event_type)
        except ModuleNotFoundError as exc:
            _logger.warning("unsubscribe 失败（DisplayEventBus 模块未就绪）: %s", exc)


# ═══════════════════════════════════════════════════════════
# ComponentFactory — 组件工厂
# ═══════════════════════════════════════════════════════════


class ComponentFactory:
    """组件工厂委托类 — create_component + 组件生命周期。

    职责：
      - create_component(): 创建组件实例并触发生命周期
    """

    def __init__(self, framework) -> None:
        """初始化组件工厂。

        Args:
            framework: Framework 实例引用。
        """
        self._framework = framework

    def create_component(
        self,
        component_cls: type,
        *args: Any,
        **kwargs: Any,
    ) -> TuiComponent:
        """创建组件实例并触发生命周期。

        Args:
            component_cls: 组件类（必须为 TuiComponent 子类）。
            *args: 传递给组件构造器的位置参数。
            **kwargs: 传递给组件构造器的关键字参数。

        Returns:
            已调用 did_mount() 的组件实例，_mounted=True。
        """
        instance = component_cls(*args, **kwargs)
        instance.did_mount()
        return instance


# ═══════════════════════════════════════════════════════════
# WidgetTreeManager — Widget 树管理
# ═══════════════════════════════════════════════════════════


class WidgetTreeManager:
    """Widget 树管理委托类 — Widget 创建/挂载/卸载/渲染。

    职责：
      - _ensure_widget_tree(): 确保内部 WidgetTree 实例存在（延迟创建）
      - mount_widget(): 挂载控件到 Widget 树
      - unmount_widget(): 从 Widget 树卸载控件
      - get_widget_root(): 获取当前 Widget 树根节点
      - has_widget_tree(): 是否已有挂载的 Widget 树
      - get_widget_tree(): 获取当前 WidgetTree 实例
      - create_widget(): 创建 Widget 实例，挂载到 WidgetTree
      - render_widget_tree(): 渲染整棵 Widget 树到 RenderBuffer
    """

    def __init__(self, framework) -> None:
        """初始化 Widget 树管理器。

        Args:
            framework: Framework 实例引用（访问 _widget_tree 共享状态）。
        """
        self._framework = framework

    def _ensure_widget_tree(self) -> None:
        """确保内部 WidgetTree 实例存在（延迟创建）。"""
        if self._framework._widget_tree is None:
            from .widget_base import WidgetTree
            self._framework._widget_tree = WidgetTree()

    def mount_widget(self, widget) -> None:
        """挂载控件到 Widget 树（委托 set_root 处理卸载旧根+挂载新根）。

        Args:
            widget: 要挂载的 Widget 实例。
        """
        self._ensure_widget_tree()
        self._framework._widget_tree.set_root(widget)

    def unmount_widget(self, widget) -> None:
        """从 Widget 树卸载控件。

        Args:
            widget: 要卸载的 Widget 实例。
        """
        if self._framework._widget_tree is not None and self._framework._widget_tree.root is widget:
            self._framework._widget_tree.set_root(None)
        else:
            widget.unmount()

    def get_widget_root(self):
        """获取当前 Widget 树根节点。

        Returns:
            Widget 实例，无树时返回 None。
        """
        if self._framework._widget_tree is None:
            return None
        return self._framework._widget_tree.root

    def has_widget_tree(self) -> bool:
        """是否已有挂载的 Widget 树。"""
        return (
            self._framework._widget_tree is not None
            and self._framework._widget_tree.root is not None
        )

    def get_widget_tree(self) -> WidgetTree | None:
        """获取当前 WidgetTree 实例。

        Returns:
            WidgetTree 实例，尚未创建时返回 None。
        """
        return self._framework._widget_tree

    def create_widget(
        self,
        widget_cls: type,
        *args,
        key: str | None = None,
        **kwargs,
    ) -> Widget:
        """创建 Widget 实例，挂载到 WidgetTree 并触发生命周期。

        Args:
            widget_cls: Widget 子类。
            *args: 传递给构造器的位置参数。
            key: 控件的身份标识键（可选）。
            **kwargs: 传递给构造器的关键字参数。

        Returns:
            已挂载的 Widget 实例（_mounted=True）。
        """
        if key is not None:
            kwargs['key'] = key
        instance = widget_cls(*args, **kwargs)
        self._ensure_widget_tree()
        instance.mount()
        return instance

    def render_widget_tree(self, buffer) -> None:
        """渲染整棵 Widget 树到 RenderBuffer。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        if self._framework._widget_tree is not None:
            self._framework._widget_tree.render(buffer)


# ═══════════════════════════════════════════════════════════
# AnimationManager — 动画上下文管理
# ═══════════════════════════════════════════════════════════


class AnimationManager:
    """动画上下文管理委托类 — get_animator/get_frame。

    职责：
      - get_animator(): 获取全局动画上下文（AnimatorContext 实例）
      - get_frame(): 获取当前动画帧号
    """

    def __init__(self, framework) -> None:
        """初始化动画管理器。

        Args:
            framework: Framework 实例引用（访问 _animator 共享状态）。
        """
        self._framework = framework

    def get_animator(self) -> AnimatorContext:
        """获取全局动画上下文（AnimatorContext 实例）。

        Returns:
            AnimatorContext 单例实例。
        """
        if self._framework._animator is None:
            from ._animator import AnimatorContext
            self._framework._animator = AnimatorContext
        return self._framework._animator.get_default()

    def get_frame(self) -> int:
        """获取当前动画帧号。

        委托 get_animator() 获取 AnimatorContext 单例并返回其帧号。

        向后兼容：原 get_frame() 直接调用 AnimatorContext.get_default().frame，
        现改为委托 get_animator().frame，接口和行为完全不变。

        Returns:
            当前帧号（单调递增整数），AnimatorContext 未初始化或异常时返回 0。
        """
        try:
            return self.get_animator().frame
        except (AttributeError, ModuleNotFoundError) as exc:
            _logger.debug("get_frame() 降级返回 0（AnimatorContext 未就绪）: %s", exc)
            return 0
