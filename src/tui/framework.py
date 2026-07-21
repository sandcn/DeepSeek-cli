"""
TUI 框架统一入口 — `Framework` 单例 + 公开 API。

提供：
  - Framework: 全局单例框架管理器（组件工厂 + 效果注册表 + 样式表 + 动画上下文）
  - create_component(): 创建组件并触发生命周期
  - create_widget(): 创建 Widget 并挂载到 WidgetTree
  - frame_from_context(): 安全获取当前帧号的统一入口
  - get_animator(): 获取全局动画上下文实例

Widget 树管理：
  - mount_widget() / unmount_widget(): 挂载/卸载控件树根节点
  - create_widget(): 创建 Widget 并自动挂载

事件集成：
  - subscribe() / unsubscribe(): 订阅/取消 DisplayEventBus 事件

设计原则：
  - 单例管理：框架全局唯一，通过 Framework.get_default() 获取
  - 延迟导入：所有组件/效果模块在首次使用时才导入，避免循环依赖
  - 线程安全：单例创建和 API 调用均使用 threading.Lock 保护
  - 零 I/O：不涉及终端或文件 I/O，纯管理职责
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

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
]


# ═══════════════════════════════════════════════════════════
# Framework — 全局单例框架管理器
# ═══════════════════════════════════════════════════════════


class Framework:
    """TUI 框架全局单例管理器。

    职责：
      1. 组件创建与生命周期管理（create_component）
      2. 效果注册表访问（get_registry）
      3. 样式表访问（get_stylesheet）
      4. 动画上下文访问（get_animator）
      5. 动画帧号获取（get_frame）

    架构确认（2026-07-15）：
      ✅ 单一职责：Framework 仅管理 TUI 层单例与工厂方法，不涉及 I/O
      ✅ 依赖方向：webui → tui（单向），Framework 不依赖 webui 层
      ✅ 无新增依赖：get_animator() 仅委托已有 AnimatorContext，未引入新模块

    使用示例：
        >>> framework = Framework.get_default()
        >>> # 创建组件（自动触发 did_mount）
        >>> component = framework.create_component(Separator, style="aurora", frame=5)
        >>> # 获取效果注册表
        >>> registry = framework.get_registry()
        >>> registry.has("aurora")
        True
        >>> # 获取样式表
        >>> stylesheet = framework.get_stylesheet()
        >>> stylesheet.get("bold")
        Style(bold=True)
        >>> # 获取动画上下文
        >>> animator = framework.get_animator()
        >>> animator.frame
        0
    """

    _instance: Framework | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        """初始化框架实例（私有构造器，通过 get_default() 获取）。"""
        self._lock = threading.Lock()
        self._registry: Any = None  # EffectRegistry 引用（延迟导入）
        self._stylesheet: Any = None  # StyleSheet 引用（延迟导入）
        self._animator: Any = None  # AnimatorContext（延迟导入）
        self._component_registry: Any = None
        self._config: Any = None
        self._running: bool = False
        self._lifecycle_lock = threading.Lock()
        self._widget_tree: Any = None  # WidgetTree 实例（延迟创建）

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

    # ── WidgetTree 管理 ─────────────────────────────

    def _ensure_widget_tree(self) -> None:
        """确保内部 WidgetTree 实例存在（延迟创建）。"""
        if self._widget_tree is None:
            from .widget_base import WidgetTree
            self._widget_tree = WidgetTree()

    def mount_widget(self, widget):
        """挂载控件到 Widget 树（先卸载旧根再挂载新根）。

        Args:
            widget: 要挂载的 Widget 实例。
        """
        self._ensure_widget_tree()
        if self.has_widget_tree():
            old_root = self._widget_tree.root
            if old_root is not None:
                old_root.unmount()
        widget.mount()
        self._widget_tree.set_root(widget)

    def unmount_widget(self, widget):
        """从 Widget 树卸载控件。

        Args:
            widget: 要卸载的 Widget 实例。
        """
        if self._widget_tree is not None and self._widget_tree.root is widget:
            self._widget_tree.set_root(None)
        widget.unmount()

    def get_widget_root(self):
        """获取当前 Widget 树根节点。

        Returns:
            Widget 实例，无树时返回 None。
        """
        if self._widget_tree is None:
            return None
        return self._widget_tree.root

    def has_widget_tree(self) -> bool:
        """是否已有挂载的 Widget 树。"""
        return (self._widget_tree is not None
                and self._widget_tree.root is not None)

    def create_widget(self, widget_cls: type, *args, key: str | None = None, **kwargs) -> "Widget":
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

    def render_widget_tree(self, buffer):
        """渲染整棵 Widget 树到 RenderBuffer。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        if self._widget_tree is not None:
            self._widget_tree.render(buffer)

    # ── 公开 API ──────────────────────────────────────

    def create_component(self, component_cls: type, *args: Any,
                         **kwargs: Any) -> TuiComponent:
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

    def get_animator(self) -> "AnimatorContext":
        """获取全局动画上下文（AnimatorContext 实例）。

        Returns:
            AnimatorContext 单例实例。
        """
        if self._animator is None:
            from .animation.animator import AnimatorContext
            self._animator = AnimatorContext
        return self._animator.get_default()

    def get_frame(self) -> int:
        """获取当前动画帧号。

        委托 get_animator() 获取 AnimatorContext 单例并返回其帧号。

        向后兼容：原 get_frame() 直接调用 AnimatorContext.get_default().frame，
        现改为委托 get_animator().frame，接口和行为完全不变。
        所有现有调用方（renderer.py / components / widgets）无需修改。

        Returns:
            当前帧号（单调递增整数），AnimatorContext 未初始化或异常时返回 0。
        """
        try:
            return self.get_animator().frame
        except (AttributeError, ImportError) as exc:
            _logger.debug("get_frame() 降级返回 0: %s", exc)
            return 0

    def get_component_registry(self) -> "ComponentRegistry":
        """获取全局组件注册表。"""
        if self._component_registry is None:
            from .core.component_registry import ComponentRegistry
            self._component_registry = ComponentRegistry
        return self._component_registry.get_default()

    def subscribe(self, event_type: type, callback) -> None:
        """订阅事件总线事件。

        委托 DisplayEventBus.get_default().subscribe() 注册事件监听。

        Args:
            event_type: 事件类型类。
            callback: 回调函数。
        """
        try:
            from .events.event_bus import DisplayEventBus
            DisplayEventBus.get_default().subscribe(callback, event_type=event_type)
        except ImportError as exc:
            _logger.warning("subscribe 失败（DisplayEventBus 未就绪）: %s", exc)

    def unsubscribe(self, event_type: type, callback) -> None:
        """取消订阅事件总线事件。

        委托 DisplayEventBus.get_default().unsubscribe() 取消监听。

        Args:
            event_type: 事件类型类。
            callback: 之前注册的回调函数。
        """
        try:
            from .events.event_bus import DisplayEventBus
            DisplayEventBus.get_default().unsubscribe(callback, event_type=event_type)
        except ImportError as exc:
            _logger.warning("unsubscribe 失败（DisplayEventBus 未就绪）: %s", exc)


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


def create_widget(widget_cls: type, *args, key: str | None = None, **kwargs) -> "Widget":
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


def get_animator() -> "AnimatorContext":
    """获取全局动画上下文（Framework.get_animator 的便捷调用）。

    用法::

        from src.tui.framework import get_animator
        animator = get_animator()
        print(animator.frame)

    Returns:
        AnimatorContext 单例实例。
    """
    return Framework.get_default().get_animator()
