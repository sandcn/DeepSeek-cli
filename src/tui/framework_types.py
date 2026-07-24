"""Framework 类型协议 — 为延迟导入属性提供类型标注。

定义 ``ComponentRegistryProtocol``、``AnimatorContextProtocol``、
``WidgetTreeProtocol`` 等 Protocol 类，用于替换 ``Framework`` 中
延迟导入属性的 ``: Any`` 类型标注。

这些 Protocol 使用 duck typing，运行时无需实际导入具体类。
通过 ``@runtime_checkable`` 装饰器支持 ``isinstance()`` 运行时检查。

设计原则：
  - 最小接口：每个 Protocol 仅定义 Framework 实际使用的方法/属性
  - duck typing：不依赖具体类的继承关系，仅要求接口一致
  - 向后兼容：替换 ``: Any`` 后运行时行为完全不变
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    ClassVar,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

if TYPE_CHECKING:
    from .render_buffer import RenderBuffer
    from .widget_base import Widget


__all__: list[str] = [
    "ComponentRegistryProtocol",
    "AnimatorContextProtocol",
    "WidgetTreeProtocol",
]


# ═══════════════════════════════════════════════════════════
# ComponentRegistryProtocol — 组件注册表协议
# ═══════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════
# AnimatorContextProtocol — 动画上下文协议
# ═══════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════
# WidgetTreeProtocol — 控件树协议
# ═══════════════════════════════════════════════════════════


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
