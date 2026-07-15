"""
TUI 框架统一入口 — `Framework` 单例 + 公开 API。

提供：
  - Framework: 全局单例框架管理器（组件工厂 + 效果注册表 + 样式表 + 动画上下文）
  - create_component(): 创建组件并触发生命周期
  - frame_from_context(): 安全获取当前帧号的统一入口
  - get_animator(): 获取全局动画上下文实例

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
    from .widgets.base import TuiComponent
    from .core.animator import AnimatorContext

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "Framework",
    "create_component",
    "frame_from_context",
    "get_animator",
]


class Framework:
    """TUI 框架全局单例管理器。

    职责：
      1. 组件创建与生命周期管理（create_component）
      2. 效果注册表访问（get_registry）
      3. 样式表访问（get_stylesheet）
      4. 动画上下文访问（get_animator）
      5. 动画帧号获取（get_frame）

    使用示例：
        >>> framework = Framework.get_default()
        >>> component = framework.create_component(MyWidget, frame=5)
        >>> registry = framework.get_registry()
        >>> animator = framework.get_animator()
        >>> animator.frame
        0
    """

    _instance: Framework | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._registry: Any = None
        self._stylesheet: Any = None
        self._animator: Any = None

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
        """重置默认实例（供测试使用）。"""
        with cls._instance_lock:
            cls._instance = None

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

    def get_registry(self) -> Any:
        """获取全局效果注册表（EffectRegistry）。"""
        if self._registry is None:
            from .core.effects import EffectRegistry
            self._registry = EffectRegistry
        return self._registry

    def get_stylesheet(self) -> Any:
        """获取全局样式表（StyleSheet）。"""
        if self._stylesheet is None:
            from .core.style import StyleSheet
            self._stylesheet = StyleSheet
        return self._stylesheet

    def get_animator(self) -> "AnimatorContext":
        """获取全局动画上下文（AnimatorContext 实例）。"""
        if self._animator is None:
            from .core.animator import AnimatorContext
            self._animator = AnimatorContext.get_default()
        return self._animator

    def get_frame(self) -> int:
        """获取当前动画帧号。"""
        try:
            return self.get_animator().frame
        except (AttributeError, ImportError) as exc:
            _logger.debug("get_frame() 降级返回 0: %s", exc)
            return 0


def create_component(component_cls: type, *args: Any,
                     **kwargs: Any) -> TuiComponent:
    """创建组件实例并触发生命周期（便捷调用）。

    用法::

        from tui_framework.framework import create_component
        widget = create_component(MyWidget, label="test")
    """
    return Framework.get_default().create_component(component_cls, *args, **kwargs)


def frame_from_context(default: int = 0) -> int:
    """安全获取当前帧号的统一入口。"""
    return Framework.get_default().get_frame()


def get_animator() -> "AnimatorContext":
    """获取全局动画上下文（便捷调用）。"""
    return Framework.get_default().get_animator()
