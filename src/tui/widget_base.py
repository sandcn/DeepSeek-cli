"""统一 Widget 基类 — 所有 TUI 控件的根基类。

提供：
  - Widget:      带 props/state/compose/render/mount 模式的统一控件基类
  - WidgetTree:  Widget 树的递归渲染管理器

设计模式：
  - 模板方法 (Template Method):
    mount()/unmount() 定义骨架流程，did_mount()/will_unmount() 为钩子
    render() 成功后调用 did_update() 渲染后回调钩子
  - 组合 (Composite):
    compose() 声明子控件列表，render() 递归渲染整棵树
  - 状态 (State):
    props 为外部传入的不可变属性，state 为内部可变状态
  - 错误隔离 (Error Boundary):
    error_boundary() 静态方法捕获子控件渲染异常，输出占位符代替崩溃内容
    _render_node() 中子控件递归用独立 try/except 包裹

渲染优化：
  - 渲染短路：_render_node() 入口处校验 should_update() + _dirty，
    props 未变时跳过子树全量渲染（由子类重写 should_update() 控制）

使用示例:
    class Greeting(Widget):
        def render(self, buffer: RenderBuffer) -> None:
            buffer.write(0, 0, f"Hello, {self.props.get('name', 'World')}!")
    
    w = Greeting(props={"name": "TUI"})
    w.mount()
    from src.tui.render_buffer import RenderBuffer
    buf = RenderBuffer(20, 1)
    w.render(buf)
    print(buf.render())  # "Hello, TUI!"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from .render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "Widget",
    "WidgetTree",
]


# ═══════════════════════════════════════════════════════════
# Widget — 统一控件基类
# ═══════════════════════════════════════════════════════════


class Widget:
    """统一控件基类 — props/state/compose/render/mount 模式。

    所有 TUI 控件（内容组件、交互控件、布局控件）的根基类。
    核心概念：
      - props: 外部传入的不可变属性字典（只读）
      - state: 内部可变状态字典（通过 set_state() 修改）
      - children: 子控件列表（由 compose() 声明）
      - compose(): 声明式子控件组合，返回子控件列表
      - render(): 将内容渲染到 RenderBuffer
      - mount()/unmount(): 生命周期挂载/卸载

    Args:
        props: 外部传入的不可变属性字典，默认 {}。
    """

    def __init__(self, props: dict | None = None, key: str | None = None) -> None:
        """初始化控件。

        Args:
            props: 外部传入的不可变属性字典，默认 {}。
            key: 可选的身份标识键，用于 WidgetTree 中的查找和标识。
                 类似 React 的 ``key`` prop，在控件树中保持身份一致性。
        """
        self._props: dict[str, Any] = dict(props) if props else {}
        self._state: dict[str, Any] = {}
        self._children: list[Widget] = []
        self._mounted: bool = False
        self._dirty: bool = True
        self._parent: Widget | None = None
        self._key: str | None = key
        # 布局控件标记：如果为 True，WidgetTree 不递归渲染子控件
        #（由父控件 render() 自行处理子控件布局和渲染）
        self._renders_children: bool = False

    # ── 属性 ──────────────────────────────────────────────

    @property
    def key(self) -> str | None:
        """控件的唯一标识键。

        用于 WidgetTree 中按 key 查找控件。
        类似 React 的 ``key`` prop。
        """
        return self._key

    @property
    def parent(self) -> Optional[Widget]:
        """父控件引用。"""
        return self._parent

    @property
    def props(self) -> dict[str, Any]:
        """外部传入的不可变属性（只读）。

        返回副本以防止意外修改。若需更新 props，应通过父控件
        compose() 返回新子控件实例传入新 props。
        """
        return dict(self._props)

    @property
    def state(self) -> dict[str, Any]:
        """内部可变状态。

        通过 set_state() 修改，不应直接赋值。
        """
        return dict(self._state)

    @property
    def children(self) -> list[Widget]:
        """子控件列表（只读）。"""
        return list(self._children)

    @property
    def mounted(self) -> bool:
        """控件是否已挂载。"""
        return self._mounted

    @property
    def dirty(self) -> bool:
        """是否需要重渲染。"""
        return self._dirty

    def set_prop(self, key: str, value: Any) -> None:
        """设置单个属性（仅挂载前调用）。

        Args:
            key: 属性名。
            value: 属性值。
        """
        self._props[key] = value

    # ── 生命周期 ──────────────────────────────────────────

    def mount(self) -> None:
        """挂载控件。

        模板方法：
          1. 设置 _mounted = True
          2. 调用 did_mount() 钩子
          3. 递归挂载子控件（设置 _parent 引用）

        幂等：已挂载时重复调用无效果。
        """
        if self._mounted:
            return
        self._mounted = True
        try:
            self.did_mount()
        except Exception as exc:
            _logger.warning("%s.did_mount() 异常: %s", type(self).__name__, exc)
        # 递归挂载子控件
        for child in self._children:
            child._parent = self
            child.mount()

    def unmount(self) -> None:
        """卸载控件。

        模板方法：
          1. 递归卸载子控件
          2. 调用 will_unmount() 钩子
          3. 设置 _mounted = False

        幂等：未挂载时重复调用无效果。
        """
        if not self._mounted:
            return
        # 递归卸载子控件
        for child in self._children:
            child.unmount()
            child._parent = None
        try:
            self.will_unmount()
        except Exception as exc:
            _logger.warning("%s.will_unmount() 异常: %s", type(self).__name__, exc)
        self._mounted = False

    def did_mount(self) -> None:
        """挂载后的初始化钩子。

        子类可重写此方法执行初始化操作（如预计算数据、注册事件等）。
        默认实现为空操作（与现有 TuiComponent.did_mount 兼容）。
        """
        pass

    def will_unmount(self) -> None:
        """卸载前的清理钩子。

        子类可重写此方法执行清理操作（如取消事件订阅、释放资源等）。
        默认实现为空操作（与现有 TuiComponent.will_unmount 兼容）。
        """
        pass

    # ── 状态管理 ──────────────────────────────────────────

    def set_state(self, new_state: dict) -> None:
        """更新内部状态并标记为需要重渲染。

        将 new_state 合并到当前 state 字典中，
        然后设置 _dirty = True 触发重渲染。

        Args:
            new_state: 要合并的状态字典。
        """
        self._state.update(new_state)
        self._dirty = True

    def should_update(self, new_props: dict | None = None) -> bool:
        """判定是否需要重渲染。

        默认实现：当 new_props 与当前 props 有差异时返回 True。
        子类可重写此方法实现细粒度的更新判定。

        Args:
            new_props: 新的 props 字典（可选）。

        Returns:
            True 触发重渲染，False 跳过。
        """
        if self._dirty:
            return True
        if new_props is None:
            return True
        # 比较 props 的 key-value 差异
        for key, value in new_props.items():
            if key not in self._props or self._props[key] != value:
                return True
        for key in self._props:
            if key not in new_props:
                return True
        return False

    def mark_clean(self) -> None:
        """标记为已渲染（清除 dirty 标志）。"""
        self._dirty = False

    @staticmethod
    def error_boundary(cls: type, exc: Exception, buffer: RenderBuffer) -> None:
        """组件级 Error Boundary — 渲染崩溃时写入错误占位符。

        不吞异常，记录错误日志并输出友好的占位符代替崩溃内容。

        Args:
            cls: 崩溃组件的类型。
            exc: 捕获到的异常对象。
            buffer: 当前渲染缓冲区。
        """
        _logger.error("组件 %s 渲染崩溃: %s", cls.__name__, exc)
        buffer.write(0, 0, f"\033[31m[组件 {cls.__name__} 渲染异常]\033[0m")

    def did_update(self, new_props: dict | None = None) -> None:
        """渲染后的回调钩子 —— render() 成功后调用。

        子类可重写此方法执行渲染后的操作（如更新缓存、触发事件等）。
        默认实现为空操作。

        Args:
            new_props: 可选的新的 props 字典。当通过批量更新触发渲染时，
                      此处传入可能导致更新的新 props 值。
        """
        pass

    # ── 组合与渲染 ────────────────────────────────────────

    def compose(self) -> Widget | list[Widget]:
        """声明子控件组合。

        子类重写此方法返回子控件列表（或单控件），
        默认返回 self._children（叶子控件返回空列表）。

        Returns:
            Widget | list[Widget]: 子控件列表。
        """
        return self._children

    def render(self, buffer: RenderBuffer) -> None:
        """渲染控件内容到 RenderBuffer。

        抽象方法，子类必须实现。将控件的视觉内容写入 buffer。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 必须实现 render() 方法"
        )

    def update(self, new_props: dict | None = None) -> None:
        """更新控件状态并触发重渲染。

        外部调用的统一更新入口。
        1. 调用 should_update() 判定是否需要重渲染
        2. 若需重渲染，调用 compose() 获取子控件树
        3. 递归更新子控件

        Args:
            new_props: 新的 props（可选）。
        """
        if not self.should_update(new_props):
            return
        self._dirty = True
        # 更新子控件树
        composed = self.compose()
        if isinstance(composed, Widget):
            composed = [composed]
        self._children = list(composed)
        # 递归更新子控件
        for child in self._children:
            child._parent = self
            if not child._mounted:
                child.mount()

    # ── 辅助 ──────────────────────────────────────────────

    def find_child(self, cls: type) -> Optional[Widget]:
        """按类型查找第一个匹配的子控件（递归）。

        Args:
            cls: 目标控件类型。

        Returns:
            第一个匹配的 Widget 实例，未找到时返回 None。
        """
        for child in self._children:
            if isinstance(child, cls):
                return child
            found = child.find_child(cls)
            if found is not None:
                return found
        return None

    def find_children(self, cls: type) -> list[Widget]:
        """按类型查找所有匹配的子控件（递归）。

        Args:
            cls: 目标控件类型。

        Returns:
            匹配的 Widget 实例列表。
        """
        result: list[Widget] = []
        for child in self._children:
            if isinstance(child, cls):
                result.append(child)
            result.extend(child.find_children(cls))
        return result

    def walk(self) -> list[Widget]:
        """深度优先遍历整棵控件树。

        Returns:
            所有控件的列表（含自身）。
        """
        result: list[Widget] = [self]
        for child in self._children:
            result.extend(child.walk())
        return result

    def __repr__(self) -> str:
        """返回控件描述。"""
        props_str = ", ".join(
            f"{k}={v!r}" for k, v in self._props.items()
        )
        state_str = ", ".join(
            f"{k}={v!r}" for k, v in self._state.items()
        )
        parts = []
        if props_str:
            parts.append(props_str)
        if state_str:
            parts.append(f"[{state_str}]")
        return f"{type(self).__name__}({'; '.join(parts)})"


# ═══════════════════════════════════════════════════════════
# WidgetTree — 控件树渲染管理器
# ═══════════════════════════════════════════════════════════


class WidgetTree:
    """控件树渲染管理器。

    管理 Widget 树的根节点，提供递归渲染能力。
    支持批量更新和增量渲染。

    Args:
        root: 控件树的根节点 Widget。
    """

    def __init__(self, root: Widget | None = None) -> None:
        self._root: Widget | None = root
        self._frame: int = 0

    @property
    def root(self) -> Widget | None:
        """获取根节点。"""
        return self._root

    def set_root(self, root: Widget) -> None:
        """设置新的根节点（卸载旧根，挂载新根）。

        Args:
            root: 新的根节点 Widget。
        """
        if self._root is not None:
            self._root.unmount()
        self._root = root
        if root is not None:
            root.mount()

    def render(self, buffer: RenderBuffer) -> None:
        """递归渲染整棵控件树到 buffer。

        从根节点开始，按 compose() → render() 递归遍历，
        每个控件渲染到 buffer 中。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        if self._root is None:
            return
        self._frame += 1
        self._render_node(self._root, buffer)

    def _render_node(self, widget: Widget, buffer: RenderBuffer) -> None:
        """递归渲染单个控件及其子控件。

        1. 渲染短路检测 — props 未变且未标记 dirty 时跳过子树
        2. 调用 widget.compose() 获取/更新子控件列表
        3. 调用 widget.render(buffer) 渲染自身
        4. render() 成功后调用 did_update() 回调
        5. 递归渲染每个子控件（独立 try/except 隔离错误）

        Args:
            widget: 要渲染的控件。
            buffer: 目标 RenderBuffer 实例。
        """
        # 渲染短路：props 未变且未标记 dirty 时跳过子树渲染
        if not widget.should_update() and not widget.dirty:
            _logger.debug(
                "WidgetTree 跳过 %s 渲染 (props 未变)", type(widget).__name__
            )
            return

        # 更新子控件树
        composed = widget.compose()
        if isinstance(composed, Widget):
            composed = [composed]
        widget._children = list(composed)

        # 渲染自身
        # 优先使用 _render_to_buffer（TuiComponent 兼容方法）
        if hasattr(widget, '_render_to_buffer'):
            try:
                widget._render_to_buffer(buffer)
                # 标记已渲染 — 放 try 内确保 render 成功后清除 dirty
                widget.mark_clean()
                # 渲染成功回调
                widget.did_update()
            except Exception as exc:
                _logger.warning(
                    "%s._render_to_buffer() 异常: %s", type(widget).__name__, exc
                )
        else:
            try:
                widget.render(buffer)
                # 标记已渲染 — 放 try 内确保 render 成功后清除 dirty
                widget.mark_clean()
                # 渲染成功回调
                widget.did_update()
            except Exception as exc:
                _logger.warning(
                    "%s.render() 异常: %s", type(widget).__name__, exc
                )

        # 递归渲染子控件（安全守卫）
        # 如果控件标记了 _renders_children=True（如布局控件），
        # 其 render() 已自行处理子控件渲染，跳过递归避免双重渲染。
        if widget._renders_children:
            return
        for child in widget._children:
            if child is widget:
                _logger.warning("跳过自我引用的子控件: %s", type(widget).__name__)
                continue
            try:
                self._render_node(child, buffer)
            except Exception as exc:
                Widget.error_boundary(type(child), exc, buffer)

    def update_tree(self) -> None:
        """更新整棵控件树（重新 compose 所有节点）。

        遍历所有节点，调用 update() 方法触发重渲染判定。
        """
        if self._root is None:
            return
        self._update_node(self._root)

    def _update_node(self, widget: Widget) -> None:
        """递归更新单个控件。

        Args:
            widget: 要更新的控件。
        """
        widget.update()
        for child in widget._children:
            self._update_node(child)

    def walk(self) -> list[Widget]:
        """遍历整棵控件树。

        Returns:
            所有控件的列表。
        """
        if self._root is None:
            return []
        return self._root.walk()

    def find(self, key: str) -> Optional[Widget]:
        """按 key 查找控件（递归，返回第一个匹配项）。

        Args:
            key: 要查找的控件 key。

        Returns:
            匹配的 Widget 实例，未找到时返回 None。
        """
        if self._root is None:
            return None
        for widget in self._root.walk():
            if widget.key == key:
                return widget
        return None

    def find_all(self, key: str) -> list[Widget]:
        """按 key 查找所有匹配控件（递归）。

        Args:
            key: 要查找的控件 key。

        Returns:
            匹配的 Widget 实例列表。
        """
        if self._root is None:
            return []
        return [w for w in self._root.walk() if w.key == key]

    def find_by_type(self, cls: type) -> list[Widget]:
        """按类型查找所有匹配控件（递归）。

        Args:
            cls: 目标控件类型。

        Returns:
            匹配的 Widget 实例列表。
        """
        if self._root is None:
            return []
        return [w for w in self._root.walk() if isinstance(w, cls)]

    def clear(self) -> None:
        """清空控件树（卸载根节点）。"""
        if self._root is not None:
            self._root.unmount()
        self._root = None
