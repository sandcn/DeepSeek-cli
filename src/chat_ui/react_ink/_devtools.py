"""开发者工具 — 组件树调试 / Hooks 检查 / 性能统计。

提供：
  - ErrorBoundary — 错误边界，捕获子组件异常并显示回退 UI
  - debug_component_tree() — 组件树结构化调试输出
  - inspect_hooks() — Hooks 状态检查
  - RenderStats — 渲染帧计数 / 性能统计

仅当环境变量 CHAT_UI_DEVTOOLS 为非空时激活 RenderStats 统计收集。
ErrorBoundary / debug_component_tree / inspect_hooks 始终可用（显式调用）。
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .._components import TuiComponent
    from ._types import HookState


# ── 环境检测 ─────────────────────────────────────────────

def _devtools_enabled() -> bool:
    """检查是否启用开发者工具。

    Returns:
        True 当环境变量 CHAT_UI_DEVTOOLS 为非空字符串。
    """
    return bool(os.environ.get("CHAT_UI_DEVTOOLS", ""))


# ═══════════════════════════════════════════════════════════
# ErrorBoundary — 错误边界组件
# ═══════════════════════════════════════════════════════════

class ErrorBoundary:
    """错误边界组件。

    捕获子组件渲染过程中的异常，显示回退 UI 而不是崩溃。
    类似 React ErrorBoundary。

    使用示例:
        boundary = ErrorBoundary(
            children=[SomeComponent()],
            fallback=lambda e: f"出错了: {e}",
            on_error=lambda e: logger.error("组件异常", exc_info=e),
        )
    """

    def __init__(
        self,
        children: list[Any] | None = None,
        fallback: Callable[[Exception], str] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ):
        """初始化错误边界。

        Args:
            children: 子组件列表。
            fallback: 回退渲染函数 (error) -> str，为 None 时使用默认回退 UI。
            on_error: 错误回调 (error) -> None，用于日志/上报。
        """
        self._children: list[Any] = list(children) if children is not None else []
        self._fallback: Callable[[Exception], str] | None = fallback
        self._on_error: Callable[[Exception], None] | None = on_error
        self._error: Exception | None = None

    @property
    def children(self) -> list[Any]:
        """子组件列表（只读视图）。"""
        return self._children

    @property
    def fallback(self) -> Callable[[Exception], str] | None:
        """回退渲染函数。"""
        return self._fallback

    @property
    def on_error(self) -> Callable[[Exception], None] | None:
        """错误回调。"""
        return self._on_error

    @property
    def error(self) -> Exception | None:
        """最近一次捕获的异常（无异常时为 None）。"""
        return self._error

    def render(self) -> str:
        """渲染子组件，捕获异常时显示回退 UI。

        Returns:
            子组件渲染结果或回退 UI 字符串。
        """
        try:
            outputs: list[str] = []
            for child in self._children:
                if hasattr(child, 'render'):
                    result = child.render()
                    outputs.append(str(result))
            return "\n".join(outputs)
        except Exception as e:
            self._error = e
            if self._on_error is not None:
                try:
                    self._on_error(e)
                except Exception:
                    pass  # 错误回调异常不传播
            if self._fallback is not None:
                try:
                    return self._fallback(e)
                except Exception:
                    pass  # 回退渲染异常，降级到默认回退
            return self._default_fallback(e)

    def _default_fallback(self, error: Exception) -> str:
        """默认回退 UI：显示错误信息框。

        Args:
            error: 捕获到的异常。

        Returns:
            格式化的错误信息框字符串。
        """
        error_type = type(error).__name__
        error_msg = str(error)
        # 多行错误信息时只取第一行用于简洁显示
        first_line = error_msg.split("\n")[0] if error_msg else "(无消息)"
        return (
            "\n┌─ Error ─────────────────────────────\n"
            f"│ {error_type}: {first_line}\n"
            "└────────────────────────────────────\n"
        )

    def add_child(self, child: Any) -> "ErrorBoundary":
        """链式添加子组件并返回 self。

        Args:
            child: 要添加的子组件。

        Returns:
            self，支持链式调用。
        """
        self._children.append(child)
        return self

    def clear_error(self) -> None:
        """清除已记录的错误状态。"""
        self._error = None


# ═══════════════════════════════════════════════════════════
# 组件树调试
# ═══════════════════════════════════════════════════════════

def debug_component_tree(root: Any, indent: int = 0) -> str:
    """生成组件树调试输出。

    递归遍历组件树，输出每个组件的类型、状态、hooks 信息。

    示例输出:
        ThinkingBlock [dirty=False, hooks=2]
          └─ TextBox [dirty=True, hooks=0]

    Args:
        root: 组件树的根组件（需有 _children / _hooks / _dirty 属性）。
        indent: 当前缩进级别（内部递归使用）。

    Returns:
        结构化的组件树字符串表示。
    """
    return _debug_tree_inner(root, indent, is_last=True, prefix_stack=[])


def _debug_tree_inner(
    node: Any,
    indent: int,
    is_last: bool,
    prefix_stack: list[str],
) -> str:
    """递归辅助函数 — 带树形字符的组件树遍历。

    Args:
        node: 当前节点。
        indent: 当前缩进级别。
        is_last: 当前节点是否为父节点的最后一个子节点。
        prefix_stack: 祖先层级的竖线连接前缀栈。

    Returns:
        当前子树的结构化字符串。
    """
    lines: list[str] = []

    # 构建当前行的前缀（含树形字符）
    if indent == 0:
        line_prefix = ""
    else:
        ancestor_prefix = "".join(prefix_stack)
        branch = "└─ " if is_last else "├─ "
        line_prefix = ancestor_prefix + branch

    # 组件基本信息
    comp_type = type(node).__name__
    info = f"{comp_type}"

    # dirty 状态
    dirty = getattr(node, '_dirty', None)
    if dirty is not None:
        info += f" [dirty={dirty}]"
    else:
        info += " [dirty=N/A]"

    # Hooks 信息
    hooks = getattr(node, '_hooks', None)
    if hooks is not None:
        hook_types = [h.type for h in hooks if h is not None]
        info += f", hooks={len(hook_types)}"
        if hook_types:
            info += f" [{', '.join(hook_types)}]"

    lines.append(f"{line_prefix}{info}")

    # 递归子组件
    children = getattr(node, '_children', None)
    if children is None:
        children = getattr(node, 'children', None)
        if callable(children) and not isinstance(children, list):
            children = children()

    if children:
        child_list = list(children) if hasattr(children, '__iter__') else []
        for i, child in enumerate(child_list):
            is_child_last = (i == len(child_list) - 1)

            # 计算子节点的新 prefix_stack
            if indent == 0:
                child_prefix = []
            else:
                child_prefix = list(prefix_stack)
                # 当前层级的竖线：如果当前节点不是最后一个，则延续竖线
                child_prefix.append("│  " if not is_last else "   ")

            lines.append(
                _debug_tree_inner(child, indent + 1, is_child_last, child_prefix)
            )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# Hooks 状态检查
# ═══════════════════════════════════════════════════════════

def inspect_hooks(component: Any) -> str:
    """检查组件的 Hooks 状态。

    返回格式化的 hooks 状态报告:

        Hook #0 [state]: value=42
        Hook #1 [effect]: deps=[a, b], has_cleanup=True
        Hook #2 [ref]: current=<object>

    Args:
        component: 要检查的组件实例（需有 _hooks 属性）。

    Returns:
        格式化的 hooks 状态报告字符串。
    """
    hooks: list[Any] | None = getattr(component, '_hooks', None)
    if not hooks:
        return "(no hooks)"

    lines: list[str] = []
    for i, hook in enumerate(hooks):
        if hook is None:
            lines.append(f"Hook #{i}: (empty)")
            continue

        htype: str = getattr(hook, 'type', 'unknown')
        value: Any = getattr(hook, 'value', None)
        deps: Any = getattr(hook, 'deps', None)
        cleanup: Any = getattr(hook, 'cleanup', None)

        if htype in ('state', 'reducer'):
            lines.append(f"Hook #{i} [{htype}]: value={value!r}")

        elif htype == 'effect':
            # effect 的 deps 可能存储在 EffectState.value 中
            effect_deps = deps
            has_cleanup = cleanup is not None

            # 如果 value 是 EffectState，从中提取 deps 和 cleanup_fn
            if value is not None and hasattr(value, 'deps'):
                effect_deps = value.deps
            if value is not None and hasattr(value, 'cleanup_fn'):
                has_cleanup = has_cleanup or value.cleanup_fn is not None

            if effect_deps is not None:
                deps_str = f"deps={effect_deps}"
            else:
                deps_str = "deps=every-render"

            lines.append(f"Hook #{i} [effect]: {deps_str}, has_cleanup={has_cleanup}")

        elif htype == 'ref':
            if isinstance(value, dict) and 'current' in value:
                lines.append(f"Hook #{i} [ref]: current={value['current']!r}")
            else:
                lines.append(f"Hook #{i} [ref]: current={value!r}")

        elif htype in ('memo', 'callback'):
            lines.append(f"Hook #{i} [{htype}]: deps={deps}")

        elif htype == 'context':
            lines.append(f"Hook #{i} [context]: value={value!r}")

        else:
            lines.append(f"Hook #{i} [{htype}]: value={value!r}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 渲染统计
# ═══════════════════════════════════════════════════════════

class RenderStats:
    """渲染统计收集器。

    收集渲染帧数、耗时、组件数量和 hook 数量等指标。
    仅在 CHAT_UI_DEVTOOLS 环境变量非空时激活实际统计。

    Attributes:
        frame_count: 渲染帧计数。
        total_render_time_ms: 累计渲染耗时（毫秒）。
        component_count: 当前帧的组件数量。
        hook_count: 当前帧的 hook 数量。
    """

    __slots__ = (
        "frame_count",
        "total_render_time_ms",
        "component_count",
        "hook_count",
        "_enabled",
    )

    def __init__(self) -> None:
        self.frame_count: int = 0
        self.total_render_time_ms: float = 0.0
        self.component_count: int = 0
        self.hook_count: int = 0
        self._enabled: bool = _devtools_enabled()

    @property
    def enabled(self) -> bool:
        """是否启用统计收集。"""
        return self._enabled

    def record_frame(
        self,
        render_time_ms: float = 0.0,
        component_count: int = 0,
        hook_count: int = 0,
    ) -> None:
        """记录一帧的渲染统计。

        仅在 devtools 启用时记录，否则为 no-op。

        Args:
            render_time_ms: 本帧渲染耗时（毫秒）。
            component_count: 本帧渲染的组件数量。
            hook_count: 本帧渲染的 hook 数量。
        """
        if not self._enabled:
            return
        self.frame_count += 1
        self.total_render_time_ms += render_time_ms
        self.component_count = component_count
        self.hook_count = hook_count

    def reset(self) -> None:
        """重置所有统计计数器。"""
        self.frame_count = 0
        self.total_render_time_ms = 0.0
        self.component_count = 0
        self.hook_count = 0

    def summary(self) -> str:
        """生成统计摘要。

        Returns:
            格式化的统计摘要字符串，或提示 devtools 未启用的消息。
        """
        if not self._enabled:
            return "RenderStats: devtools 未启用（设置 CHAT_UI_DEVTOOLS=1 以激活）"

        if self.frame_count == 0:
            return "RenderStats: 尚无渲染帧数据"

        avg_time = self.total_render_time_ms / self.frame_count
        return (
            f"RenderStats:\n"
            f"  帧数: {self.frame_count}\n"
            f"  总耗时: {self.total_render_time_ms:.2f} ms\n"
            f"  平均耗时: {avg_time:.2f} ms/frame\n"
            f"  最近帧组件数: {self.component_count}\n"
            f"  最近帧 Hook 数: {self.hook_count}"
        )


# ── 全局单例 ────────────────────────────────────────────

_render_stats = RenderStats()
"""全局渲染统计单例。

由渲染引擎在每帧渲染完成后调用 record_frame() 记录统计。
通过 _render_stats.summary() 获取可读摘要。
"""
