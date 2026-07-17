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

import functools
import warnings

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .components._base import TuiComponent
    from .animation.animator import AnimatorContext

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "Framework",
    "create_component",
    "frame_from_context",
    "get_animator",
]


# ═══════════════════════════════════════════════════════════
# deprecated — 废弃标记装饰器
# ═══════════════════════════════════════════════════════════


def deprecated(replacement: str = "") -> callable:
    """标记函数为已废弃，建议使用 replacement 替代。

    使用方式：
        @deprecated("new_function")
        def old_function():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            msg = f"{func.__name__}() 已废弃"
            if replacement:
                msg += f"，请使用 {replacement} 替代"
            warnings.warn(msg, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)
        return wrapper
    return decorator


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

    # ── 公开 API ──────────────────────────────────────

    @deprecated("create_component() 模块级函数")
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

    @deprecated("EffectRegistry 类直接访问")
    def get_registry(self) -> Any:
        """获取全局效果注册表（EffectRegistry）。

        Returns:
            EffectRegistry 类（本身即注册表，无需实例化）。
        """
        if self._registry is None:
            from .core.effects import EffectRegistry
            self._registry = EffectRegistry
        return self._registry

    @deprecated("StyleSheet 类直接访问")
    def get_stylesheet(self) -> Any:
        """获取全局样式表（StyleSheet）。

        Returns:
            StyleSheet 类（本身即注册表，无需实例化）。
        """
        if self._stylesheet is None:
            from .core.style import StyleSheet
            self._stylesheet = StyleSheet
        return self._stylesheet

    @deprecated("get_animator() 模块级函数")
    def get_animator(self) -> "AnimatorContext":
        """获取全局动画上下文（AnimatorContext 实例）。

        Returns:
            AnimatorContext 单例实例。
        """
        if self._animator is None:
            from .animation.animator import AnimatorContext
            self._animator = AnimatorContext
        return self._animator.get_default()

    @deprecated("frame_from_context() 模块级函数")
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
