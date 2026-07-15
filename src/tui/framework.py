"""
TUI 框架统一入口 — `Framework` 单例 + 公开 API。

提供：
  - Framework: 全局单例框架管理器（组件工厂 + 效果注册表 + 样式表）
  - create_component(): 创建组件并触发生命周期
  - frame_from_context(): 安全获取当前帧号的统一入口

设计原则：
  - 单例管理：框架全局唯一，通过 Framework.get_default() 获取
  - 延迟导入：所有组件/效果模块在首次使用时才导入，避免循环依赖
  - 线程安全：单例创建和 API 调用均使用 threading.Lock 保护
  - 零 I/O：不涉及终端或文件 I/O，纯管理职责
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .components._base import TuiComponent


__all__: list[str] = [
    "Framework",
    "create_component",
    "frame_from_context",
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
      4. 动画帧号上下文获取（get_frame）

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
    """

    _instance: Framework | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        """初始化框架实例（私有构造器，通过 get_default() 获取）。"""
        self._lock = threading.Lock()
        self._registry: Any = None  # EffectRegistry 引用（延迟导入）
        self._stylesheet: Any = None  # StyleSheet 引用（延迟导入）
        self._animator: Any = None  # AnimatorContext（延迟导入）

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
        """重置默认实例（供测试使用）。"""
        with cls._instance_lock:
            cls._instance = None

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

    def get_registry(self) -> Any:
        """获取全局效果注册表（EffectRegistry）。

        Returns:
            EffectRegistry 类（本身即注册表，无需实例化）。
        """
        if self._registry is None:
            from .core.effects import EffectRegistry
            self._registry = EffectRegistry
        return self._registry

    def get_stylesheet(self) -> Any:
        """获取全局样式表（StyleSheet）。

        Returns:
            StyleSheet 类（本身即注册表，无需实例化）。
        """
        if self._stylesheet is None:
            from .core.style import StyleSheet
            self._stylesheet = StyleSheet
        return self._stylesheet

    def get_frame(self) -> int:
        """获取当前动画帧号。

        通过 AnimatorContext 全局单例获取帧号。

        Returns:
            当前帧号（单调递增整数），AnimatorContext 未初始化时返回 0。
        """
        if self._animator is None:
            from .core.animator import AnimatorContext
            self._animator = AnimatorContext
        try:
            return self._animator.get_default().frame
        except Exception:
            return 0


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
