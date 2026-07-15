"""组件基类 — TuiComponent + 可写入协议。

提供框架级别的组件基类，去除业务层依赖（OutputAdapter 等）。
所有组件子类必须实现 render() 方法。
"""

from __future__ import annotations

import logging
import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tui_framework.events.event_types import KeyPressEvent, MouseEvent

_logger = logging.getLogger(__name__)


@runtime_checkable
class Writeable(Protocol):
    """可写入协议 — 定义最小输出接口。

    任何实现 write(text: str) 方法的对象均可作为 render_to_adapter 的目标。
    框架内不绑定具体 OutputAdapter 实现，由调用方注入。
    """

    def write(self, text: str) -> None: ...


class TuiComponent:
    """组件基类。

    所有子类必须实现 render() 方法，可选重写 render_to_adapter()。

    ## 生命周期

    组件生命周期调用顺序：
      1. ``did_mount()`` — 组件创建后调用
      2. ``should_update(new_props)`` → 渲染前调用，返回 True 触发重渲染
      3. ``render()`` — 执行渲染输出
      4. ``will_unmount()`` — 组件销毁前调用

    所有生命周期方法默认空实现，子类可按需重写。

    ## 两种渲染路径

    路径 A（默认）：
        子类仅实现 render() → str。
        基类 render_to_adapter() 自动调用 render() 获取输出，
        再将结果通过 adapter.write() 写入目标。

    路径 B（高级——需要直接操作 adapter）：
        子类重写 render_to_adapter()，完全绕过 render()，
        直接对 adapter 进行操作。重写时仍应实现 render() 作为降级/调试用途。
    """

    def __init__(self) -> None:
        self._mounted: bool = False

    def did_mount(self) -> None:
        """组件挂载后调用 — 执行初始化逻辑。"""
        self._mounted = True
        _logger.debug("TuiComponent.did_mount() [id=%s]", getattr(self, '_id', 'N/A'))

    def will_unmount(self) -> None:
        """组件卸载前调用 — 清理资源。"""
        self._mounted = False
        _logger.debug("TuiComponent.will_unmount() [id=%s]", getattr(self, '_id', 'N/A'))

    def should_update(self, new_props: dict | None = None) -> bool:
        """渲染前调用 — 决定是否需要重渲染。

        Args:
            new_props: 新的属性字典（可选）。

        Returns:
            True 触发重渲染，False 跳过渲染。
        """
        return True

    @abstractmethod
    def render(self) -> str:
        """渲染组件内容。

        子类必须实现此方法，返回渲染后的文本内容。

        Returns:
            str: 渲染后的文本内容。
        """

    def render_to_adapter(self, adapter: Writeable) -> int:
        """通过 adapter 渲染组件，返回估计行数。

        默认实现（路径 A）：
            调用 self.render() 获取输出，通过 adapter.write() 写入。

        Args:
            adapter: 可写入对象（实现 Writeable 协议）。

        Returns:
            int: 渲染内容的估计行数。
        """
        if not self.should_update():
            return 0
        output = self.render()
        if output:
            adapter.write(output)
            return _estimate_content_lines(output)
        return 0


def _estimate_content_lines(text: str) -> int:
    """估算文本内容的终端行数。"""
    if not text:
        return 1
    return text.count('\n') + 1


class Widget(TuiComponent):
    """交互式控件基类 — 扩展 TuiComponent 增加焦点/交互能力。

    ## 设计模式：模板方法

    Widget 定义事件处理骨架（handle_key/handle_mouse），子类覆写钩子方法
    （on_key/on_mouse/on_focus/on_blur）实现具体交互行为。

    ## 状态管理

    每个 Widget 维护以下运行时状态：
    - ``_focused``: 是否拥有键盘焦点
    - ``_disabled``: 是否禁用交互（禁用时不响应事件）
    - ``_visible``: 是否可见（不可见时渲染为空字符串）
    - ``_id``: 全局唯一标识符（自动生成 uuid4）
    - ``_effects``: 动效实例列表（默认空，由 ``AnimatedWidget`` 子类填充）

    ## 生命周期扩展

    在 TuiComponent 生命周期基础上新增：
      1. ``focus()`` → ``on_focus()`` — 获得焦点
      2. ``blur()`` → ``on_blur()`` — 失去焦点
      3. ``handle_key(event)`` → ``on_key(event)`` — 处理按键
      4. ``handle_mouse(event)`` → ``on_mouse(event)`` — 处理鼠标
    """

    def __init__(self) -> None:
        super().__init__()
        self._focused: bool = False
        self._disabled: bool = False
        self._visible: bool = True
        self._id: str = uuid.uuid4().hex[:12]
        self._theme: "Theme | None" = None
        self._effects: list = []  # 动效实例列表，由 AnimatedWidget 子类填充

    # ── 焦点管理 ────────────────────────────────────────

    @property
    def focused(self) -> bool:
        """是否拥有焦点。"""
        return self._focused

    @property
    def widget_id(self) -> str:
        """Widget 唯一标识符。"""
        return self._id

    def focus(self) -> None:
        """获得焦点。

        设置 ``_focused=True`` 并调用 ``on_focus()`` 钩子。
        若已处于焦点状态则忽略（幂等）。
        """
        if self._focused:
            return
        self._focused = True
        _logger.debug("Widget.focus() [id=%s]", self._id)
        try:
            self.on_focus()
        except Exception:
            _logger.exception("Widget.on_focus() 异常 [id=%s]", self._id)

    def blur(self) -> None:
        """失去焦点。

        设置 ``_focused=False`` 并调用 ``on_blur()`` 钩子。
        若已处于非焦点状态则忽略（幂等）。
        """
        if not self._focused:
            return
        self._focused = False
        _logger.debug("Widget.blur() [id=%s]", self._id)
        try:
            self.on_blur()
        except Exception:
            _logger.exception("Widget.on_blur() 异常 [id=%s]", self._id)

    def on_focus(self) -> None:
        """焦点获得钩子 — 子类覆写实现焦点进入时的行为。"""

    def on_blur(self) -> None:
        """焦点失去钩子 — 子类覆写实现焦点离开时的行为。"""

    # ── 启用/禁用 ────────────────────────────────────────

    @property
    def disabled(self) -> bool:
        """是否禁用。"""
        return self._disabled

    def enable(self) -> None:
        """启用控件。设置 ``_disabled=False``。"""
        self._disabled = False

    def disable(self) -> None:
        """禁用控件。设置 ``_disabled=True``。"""
        self._disabled = True

    # ── 可见性 ──────────────────────────────────────────

    @property
    def visible(self) -> bool:
        """是否可见。"""
        return self._visible

    def show(self) -> None:
        """显示控件。设置 ``_visible=True``。"""
        self._visible = True

    def hide(self) -> None:
        """隐藏控件。设置 ``_visible=False``。"""
        self._visible = False

    # ── 事件处理 ────────────────────────────────────────

    def handle_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件（模板方法）。

        默认检查禁用/可见状态后委托给 ``on_key()``。
        子类一般不应覆写此方法，而是覆写 ``on_key()`` 钩子。

        Args:
            event: 键盘事件对象。

        Returns:
            True 表示事件已被消费（终止冒泡），False 表示未处理（继续传播）。
        """
        if self._disabled or not self._visible:
            return False
        try:
            return self.on_key(event)
        except Exception:
            _logger.exception("Widget.on_key() 异常 [id=%s, key=%s]",
                              self._id, getattr(event, 'key', '?'))
            return False

    def handle_mouse(self, event: MouseEvent) -> bool:
        """处理鼠标事件（模板方法）。

        默认检查禁用/可见状态后委托给 ``on_mouse()``。
        子类一般不应覆写此方法，而是覆写 ``on_mouse()`` 钩子。

        Args:
            event: 鼠标事件对象。

        Returns:
            True 表示事件已被消费，False 表示未处理。
        """
        if self._disabled or not self._visible:
            return False
        try:
            return self.on_mouse(event)
        except Exception:
            _logger.exception("Widget.on_mouse() 异常 [id=%s]", self._id)
            return False

    def on_key(self, event: KeyPressEvent) -> bool:
        """按键事件钩子 — 子类覆写实现按键处理逻辑。

        Args:
            event: 键盘事件对象。

        Returns:
            True 表示事件已被消费，False 表示未处理。
        """
        return False

    def on_mouse(self, event: MouseEvent) -> bool:
        """鼠标事件钩子 — 子类覆写实现鼠标处理逻辑。

        Args:
            event: 鼠标事件对象。

        Returns:
            True 表示事件已被消费，False 表示未处理。
        """
        return False

    # ── 主题支持 ────────────────────────────────────────

    @property
    def theme(self) -> "Theme | None":
        """控件级主题（可能为 None，表示使用全局主题）。"""
        return self._theme

    @theme.setter
    def theme(self, value: "Theme | None") -> None:
        self._theme = value

    def resolve_theme_color(self, key: str, default: str = "") -> str:
        """按优先级链查找主题颜色。

        查找顺序：
        1. 控件自身的 ``theme``（含其继承链）
        2. 全局活动主题 ``THEME``

        Args:
            key: 语义键（如 ``"border"``、``"title"`` 等）。
            default: 所有链均未命中时的回退值。

        Returns:
            ANSI 颜色码字符串。
        """
        if self._theme is not None:
            result = self._theme.get(key)
            if result:
                return result
        # 回退到全局主题
        from ..core.theme import THEME as _global_theme
        return _global_theme.get(key, default)
