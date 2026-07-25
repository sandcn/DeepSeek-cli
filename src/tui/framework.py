"""
TUI 框架统一入口 — `Framework` 单例 + 公开 API（外观模式）。

Framework 是 TUI 框架的**统一协调者**，使用外观（Facade）模式将 8 个职责
委托到 5 个单一职责委托类：

  - ConfigManager      → 配置管理（get_config/set_config）
  - EventBusManager    → 事件总线（get_event_bus/publish_event/subscribe/unsubscribe）
  - ComponentFactory   → 组件工厂（create_component）
  - WidgetTreeManager  → Widget 树管理（create_widget/mount_widget/...）
  - AnimationManager   → 动画上下文（get_animator/get_frame）

Framework 自身保留：
  - 生命周期管理（start/stop/is_running）
  - 单例模式（get_default/reset_default）
  - RenderBuffer 工厂（create_render_buffer）
  - 组件注册表访问（get_component_registry）

便捷函数：
  - create_component(): 创建组件并触发生命周期
  - create_widget(): 创建 Widget 并挂载到 WidgetTree
  - frame_from_context(): 安全获取当前帧号的统一入口
  - get_animator(): 获取全局动画上下文实例
  - get_framework(): Framework.get_default() 的语义别名

Widget 树管理：
  - mount_widget() / unmount_widget(): 挂载/卸载控件树根节点
  - create_widget(): 创建 Widget 并自动挂载
  - get_widget_tree(): 获取当前 WidgetTree 实例
  - render_widget_tree(): 渲染整棵 Widget 树到 RenderBuffer

事件集成：
  - subscribe() / unsubscribe(): 订阅/取消 DisplayEventBus 事件
  - get_event_bus(): 获取 DisplayEventBus 实例
  - publish_event(): 便捷发布事件

扩展点：
  - Widget 生命周期：mount → compose → render → unmount
  - 自定义组件：继承 Widget 或 TuiComponent，通过 create_widget/create_component 创建
  - 自定义布局：继承 Widget，在 render() 中组合子控件
  - 事件驱动：通过 subscribe() 注册事件监听
  - FrameworkRenderer 子类化：继承 FrameworkRenderer 并通过 @register_render_command
    注册自定义渲染命令，实现应用特定的渲染逻辑
  - RenderState 子类化：继承 RenderState 实现领域特定的渲染器生命周期管理，
    通过 set_output_adapter() 注入共享输出适配器

设计原则：
  - 单例管理：框架全局唯一，通过 Framework.get_default() 获取
  - 外观模式：8 职责拆分到 5 委托类，Framework 作为统一入口
  - 延迟导入：所有组件/效果模块在首次使用时才导入，避免循环依赖
  - 线程安全：单例创建和 API 调用均使用 threading.Lock 保护
  - 零 I/O：不涉及终端或文件 I/O，纯管理职责
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:
    from .components._base import TuiComponent
    from .animation.animator import AnimatorContext
    from .core.component_registry import ComponentRegistry
    from .config import TuiConfig
    from .widget_base import Widget, WidgetTree
    from .render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "Framework",
    "create_component",
    "create_widget",
    "frame_from_context",
    "get_animator",
    "get_framework",
]


# ═══════════════════════════════════════════════════════════
# 类型协议 — 为延迟导入属性提供类型标注
# ═══════════════════════════════════════════════════════════

# ComponentRegistryProtocol — 组件注册表协议


@runtime_checkable
class ComponentRegistryProtocol(Protocol):
    """组件注册表协议 — 管理 RenderCommand → 组件映射。

    匹配 ``ComponentRegistry`` 的 ``get_default()`` 和 ``resolve()`` 接口。
    Framework 中存储的是类引用（而非实例），
    通过 ``.get_default()`` 获取单例实例后再调用实例方法。

    用法（在 Framework 中）::

        self._component_registry: ComponentRegistryProtocol | None = None
        ...
        self._component_registry = ComponentRegistry
        return self._component_registry.get_default()
    """

    @classmethod
    def get_default(cls) -> ComponentRegistryProtocol:
        """获取全局默认注册表实例（线程安全单例）。"""
        ...

    def resolve(
        self, command_id: int
    ) -> Optional[Tuple[str, Tuple[int, ...]]]:
        """解析命令 ID 对应的方法名和参数索引。

        Args:
            command_id: RenderCommand 枚举值。

        Returns:
            (method_name, arg_indices) 元组，未注册时返回 None。
        """
        ...


# AnimatorContextProtocol — 动画上下文协议


@runtime_checkable
class AnimatorContextProtocol(Protocol):
    """动画上下文协议 — 统一动画时钟管理。

    匹配 ``AnimatorContext`` 的 ``get_default()`` 和 ``frame`` 接口。
    Framework 中存储的是类引用（而非实例），
    通过 ``.get_default()`` 获取单例实例后再访问 ``.frame`` 属性。

    用法（在 Framework 中）::

        self._animator: AnimatorContextProtocol | None = None
        ...
        self._animator = AnimatorContext
        return self._animator.get_default()
    """

    @classmethod
    def get_default(cls) -> AnimatorContextProtocol:
        """获取全局默认动画上下文实例（线程安全单例）。"""
        ...

    @property
    def frame(self) -> int:
        """当前动画帧号（单调递增整数）。"""
        ...


# WidgetTreeProtocol — 控件树协议


@runtime_checkable
class WidgetTreeProtocol(Protocol):
    """控件树协议 — 控件树的递归渲染管理器。

    匹配 ``WidgetTree`` 的 ``root``、``set_root()``、``render()`` 接口。
    Framework 中存储的是 WidgetTree 实例（而非类引用），
    直接访问属性和方法。

    用法（在 Framework 中）::

        self._widget_tree: WidgetTreeProtocol | None = None
        ...
        self._widget_tree = WidgetTree()
        self._widget_tree.root      # → Widget | None
        self._widget_tree.set_root(widget)
        self._widget_tree.render(buffer)
    """

    @property
    def root(self) -> Optional["Widget"]:
        """获取控件树根节点，无树时返回 None。"""
        ...

    def set_root(self, root: "Widget") -> None:
        """设置新的根节点（卸载旧根，挂载新根）。

        Args:
            root: 新的根节点 Widget 实例。
        """
        ...

    def render(self, buffer: "RenderBuffer") -> None:
        """递归渲染整棵控件树到 buffer。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        ...


# ═══════════════════════════════════════════════════════════
# Framework — 全局单例框架管理器（外观模式）
# ═══════════════════════════════════════════════════════════


class Framework:
    """TUI 框架全局单例管理器（外观模式）。

    职责（Framework 自身保留）：
      1. 生命周期管理（start / stop / is_running）
      2. RenderBuffer 工厂（create_render_buffer）
      3. 组件注册表访问（get_component_registry）
      4. 单例管理（get_default / reset_default）

    委托职责（拆分到 5 个委托类）：
      - ConfigManager:    配置管理（get_config / set_config）
      - EventBusManager:  事件总线（get_event_bus / publish_event / subscribe / unsubscribe）
      - ComponentFactory: 组件工厂（create_component）
      - WidgetTreeManager: Widget 树管理（create_widget / mount_widget / ...）
      - AnimationManager: 动画上下文（get_animator / get_frame）

    架构变更（2026-07-24）：
      - Framework 从"神类"重构为外观（Facade）模式
      - 所有公开 API 签名保持不变，调用方无需修改
      - 委托类可通过 framework._config_mgr / framework._event_bus_mgr 等属性访问

    **Widget 生命周期**（mount → compose → render → unmount）::

        from src.tui import Framework, Widget, RenderBuffer

        class MyWidget(Widget):
            def compose(self):
                # 返回子控件列表（可选）
                return []

            def render(self, buffer: RenderBuffer):
                # 将自身渲染到 buffer
                buffer.write(0, 0, "Hello")

        # 方式一：通过 Framework 创建并自动挂载
        fw = Framework.get_default()
        widget = fw.create_widget(MyWidget)
        fw.mount_widget(widget)

        # 方式二：手动挂载到 WidgetTree
        tree = fw.get_widget_tree()
        widget.mount()
        tree.set_root(widget)

        # 渲染整棵 Widget 树
        buf = fw.create_render_buffer(80, 24)
        fw.render_widget_tree(buf)
        print(buf.render())

        # 卸载
        widget.unmount()

    **事件订阅**::

        from src.tui.events.event_types import UserMessageEvent

        def on_user_msg(event):
            print(f"User: {event.text}")

        fw.subscribe(UserMessageEvent, on_user_msg)
        # 发布事件
        fw.publish_event(UserMessageEvent(text="hello"))

    **自定义组件创建**::

        from src.tui import create_component
        from src.tui.components import Separator

        # create_component 自动调用 did_mount()
        sep = create_component(Separator, style="aurora", frame=5)

    **配置管理**::

        from src.tui import TuiConfig

        cfg = TuiConfig.defaults().with_overrides(render_interval=0.05)
        fw.set_config(cfg)
        print(fw.get_config().render_interval)  # 0.05

    **自定义渲染器（子类化 FrameworkRenderer）**::

        from src.tui.engine.renderer_base import FrameworkRenderer, register_render_command

        class MyRenderer(FrameworkRenderer):
            @register_render_command(100, (1,))
            def _do_custom_cmd(self, text: str) -> None:
                self._adapter.write(text)

    **自定义渲染状态（子类化 RenderState）**::

        from src.tui.state.render_state import RenderState

        class MyRenderState(RenderState):
            def __init__(self):
                super().__init__()
                self.my_renderer = None

            def close_all(self):
                if self.my_renderer:
                    self.my_renderer.close()
                    self.my_renderer = None

    使用示例：
        >>> framework = Framework.get_default()
        >>> # 创建组件（自动触发 did_mount）
        >>> component = framework.create_component(Separator, style="aurora", frame=5)
        >>> # 获取动画上下文
        >>> animator = framework.get_animator()
        >>> animator.frame
        0
        >>> # 检查运行状态
        >>> framework.is_running()
        False
    """

    _instance: Framework | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        """初始化框架实例（私有构造器，通过 get_default() 获取）。"""
        self._lock = threading.Lock()
        # ── 共享状态 ──
        self._animator: AnimatorContextProtocol | None = None  # AnimatorContext（延迟导入）
        self._component_registry: ComponentRegistryProtocol | None = None
        self._config: TuiConfig | None = None
        self._running: bool = False
        self._lifecycle_lock = threading.Lock()
        self._widget_tree: WidgetTreeProtocol | None = None  # WidgetTree 实例（延迟创建）
        # ── 初始化 5 个委托类 ──
        self._config_mgr = ConfigManager(self)
        self._event_bus_mgr = EventBusManager(self)
        self._component_factory = ComponentFactory(self)
        self._widget_tree_mgr = WidgetTreeManager(self)
        self._animation_mgr = AnimationManager(self)

    # ── 单例访问 ──────────────────────────────────────

    @classmethod
    def get_default(cls) -> Framework:
        """获取全局默认框架实例（线程安全单例）。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default(cls) -> None:
        """重置默认实例（供测试使用）。

        可测试性确认（2026-07-15）：
          ✅ 所有测试文件（test_framework.py / test_components_gradient.py 等）
             在 setUp/tearDown 中正确调用 reset_default() 确保测试隔离
          ✅ AnimatorContext.reset_default() 与 Framework.reset_default()
             配合使用，双重重置确保单例状态干净
        """
        with cls._instance_lock:
            cls._instance = None

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> None:
        """启动框架——预热子系统。幂等操作。"""
        with self._lifecycle_lock:
            if self._running:
                return
            _ = self.get_component_registry()
            _ = self.get_animator()
            self._running = True

    def stop(self) -> None:
        """停止框架。幂等操作。"""
        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False

    def is_running(self) -> bool:
        """查询框架是否处于运行状态。

        Returns:
            True 如果 Framework 已调用 start() 且尚未 stop()。
        """
        return self._running

    # ── 配置管理（委托 ConfigManager）──────────────────

    def get_config(self) -> TuiConfig:
        """获取当前 TUI 配置。

        返回 TuiConfig 默认配置。可通过 set_config() 覆盖。

        Returns:
            TuiConfig 实例（frozen=True，不可变）。
        """
        return self._config_mgr.get_config()

    def set_config(self, config: TuiConfig) -> None:
        """设置 TUI 配置。

        Args:
            config: TuiConfig 实例。
        """
        self._config_mgr.set_config(config)

    # ── 事件总线访问（委托 EventBusManager）────────────

    def get_event_bus(self):
        """获取全局 DisplayEventBus 实例。

        Returns:
            DisplayEventBus 单例实例。
        """
        return self._event_bus_mgr.get_event_bus()

    def publish_event(self, event) -> None:
        """发布事件到 DisplayEventBus。

        Args:
            event: DisplayEvent 子类实例。
        """
        self._event_bus_mgr.publish_event(event)

    # ── RenderBuffer 工厂（Framework 自身保留）────────

    def create_render_buffer(self, width: int, height: int) -> RenderBuffer:
        """创建 RenderBuffer 实例。

        Args:
            width: 缓冲区宽度（列数）。
            height: 缓冲区高度（行数）。

        Returns:
            新的 RenderBuffer 实例。
        """
        from .render_buffer import RenderBuffer
        return RenderBuffer(width, height)

    # ── WidgetTree 管理（委托 WidgetTreeManager）───────

    def _ensure_widget_tree(self) -> None:
        """确保内部 WidgetTree 实例存在（延迟创建）。"""
        self._widget_tree_mgr._ensure_widget_tree()

    def mount_widget(self, widget) -> None:
        """挂载控件到 Widget 树（先卸载旧根再挂载新根）。

        Args:
            widget: 要挂载的 Widget 实例。
        """
        self._widget_tree_mgr.mount_widget(widget)

    def unmount_widget(self, widget) -> None:
        """从 Widget 树卸载控件。

        Args:
            widget: 要卸载的 Widget 实例。
        """
        self._widget_tree_mgr.unmount_widget(widget)

    def get_widget_root(self):
        """获取当前 Widget 树根节点。

        Returns:
            Widget 实例，无树时返回 None。
        """
        return self._widget_tree_mgr.get_widget_root()

    def has_widget_tree(self) -> bool:
        """是否已有挂载的 Widget 树。"""
        return self._widget_tree_mgr.has_widget_tree()

    def get_widget_tree(self) -> WidgetTree | None:
        """获取当前 WidgetTree 实例。

        Returns:
            WidgetTree 实例，尚未创建时返回 None。
        """
        return self._widget_tree_mgr.get_widget_tree()

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
        return self._widget_tree_mgr.create_widget(
            widget_cls, *args, key=key, **kwargs
        )

    def render_widget_tree(self, buffer) -> None:
        """渲染整棵 Widget 树到 RenderBuffer。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        self._widget_tree_mgr.render_widget_tree(buffer)

    # ── 公开 API ──────────────────────────────────────

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
        return self._component_factory.create_component(
            component_cls, *args, **kwargs
        )

    def get_animator(self) -> AnimatorContext:
        """获取全局动画上下文（AnimatorContext 实例）。

        Returns:
            AnimatorContext 单例实例。
        """
        return self._animation_mgr.get_animator()

    def get_frame(self) -> int:
        """获取当前动画帧号。

        委托 get_animator() 获取 AnimatorContext 单例并返回其帧号。

        向后兼容：原 get_frame() 直接调用 AnimatorContext.get_default().frame，
        现改为委托 get_animator().frame，接口和行为完全不变。
        所有现有调用方（renderer.py / components / widgets）无需修改。

        Returns:
            当前帧号（单调递增整数），AnimatorContext 未初始化或异常时返回 0。
        """
        return self._animation_mgr.get_frame()

    def get_component_registry(self) -> ComponentRegistry:
        """获取全局组件注册表。"""
        if self._component_registry is None:
            from .core.component_registry import ComponentRegistry
            self._component_registry = ComponentRegistry
        return self._component_registry.get_default()

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
        self._event_bus_mgr.subscribe(event_type, callback)

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
        self._event_bus_mgr.unsubscribe(event_type, callback)


# ═══════════════════════════════════════════════════════════
# 便捷函数（降低使用成本）
# ═══════════════════════════════════════════════════════════


def create_component(component_cls: type, *args: Any,
                     **kwargs: Any) -> TuiComponent:
    """创建组件实例并触发生命周期（Framework.create_component 的便捷调用）。

    用法::

        from src.tui.framework import create_component
        sep = create_component(Separator, style="aurora", frame=5)

    Args:
        component_cls: 组件类。
        *args: 位置参数。
        **kwargs: 关键字参数。

    Returns:
        已调用 did_mount() 的组件实例。
    """
    return Framework.get_default().create_component(component_cls, *args, **kwargs)


def create_widget(widget_cls: type, *args, key: str | None = None, **kwargs) -> Widget:
    """创建 Widget 实例并挂载到框架的 WidgetTree。

    便捷函数，等效于 Framework.get_default().create_widget(...)。

    用法::

        from src.tui.framework import create_widget
        from src.tui.components._separator import Separator
        widget = create_widget(Separator, style="aurora", frame=5)

    Args:
        widget_cls: Widget 子类。
        *args: 位置参数。
        key: 控件的身份标识键（可选）。
        **kwargs: 关键字参数。

    Returns:
        已挂载的 Widget 实例（_mounted=True）。
    """
    return Framework.get_default().create_widget(widget_cls, *args, key=key, **kwargs)


def frame_from_context(default: int = 0) -> int:
    """安全获取当前帧号的统一入口。

    所有组件应通过此函数获取帧号，而非直接调用
    ``AnimatorContext.get_default().frame``。

    用法::

        from src.tui.framework import frame_from_context
        frame = frame_from_context()

    Args:
        default: AnimatorContext 未初始化时的兜底值，默认 0。

    Returns:
        当前帧号，获取失败时返回 default。
    """
    return Framework.get_default().get_frame()


def get_animator() -> AnimatorContext:
    """获取全局动画上下文（Framework.get_animator 的便捷调用）。

    用法::

        from src.tui.framework import get_animator
        animator = get_animator()
        print(animator.frame)

    Returns:
        AnimatorContext 单例实例。
    """
    return Framework.get_default().get_animator()


def get_framework() -> Framework:
    """获取全局框架实例（Framework.get_default 的语义别名）。

    用法::

        from src.tui.framework import get_framework
        fw = get_framework()
        print(fw.is_running())

    Returns:
        Framework 单例实例。
    """
    return Framework.get_default()


# ═══════════════════════════════════════════════════════════
# 委托类 — 8 职责拆分为 5 个单一职责委托类（原 framework_delegates.py）
# ═══════════════════════════════════════════════════════════

# ── ConfigManager — 配置管理 ────────────────────────────


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


# ── EventBusManager — 事件总线委托 ─────────────────────


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


# ── ComponentFactory — 组件工厂 ────────────────────────


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


# ── WidgetTreeManager — Widget 树管理 ──────────────────


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


# ── AnimationManager — 动画上下文管理 ──────────────────


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
            from .animation.animator import AnimatorContext
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
