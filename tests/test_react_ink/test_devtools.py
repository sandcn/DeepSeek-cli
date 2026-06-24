"""开发者工具单元测试。

覆盖 ErrorBoundary 错误边界、debug_component_tree 组件树调试、
inspect_hooks 状态检查。
"""

from __future__ import annotations

import pytest

from src.chat_ui.devtools.stats import (
    ErrorBoundary,
    debug_component_tree,
    inspect_hooks,
    RenderStats,
)
from src.chat_ui.components.base import TuiComponent


# ── 测试辅助 ────────────────────────────────────────────

class _GoodComp(TuiComponent):
    """正常渲染的组件。"""

    def __init__(self, text: str = "ok"):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


class _BadComp(TuiComponent):
    """渲染时抛出异常的组件。"""

    def render(self) -> str:
        raise ValueError("模拟渲染异常")


class _ParentComp(TuiComponent):
    """含子组件的父组件。"""

    def __init__(self, name: str, children=None):
        super().__init__(children=children)
        self.name = name

    def render(self) -> str:
        return self.name


# ═══════════════════════════════════════════════════════════
# TestErrorBoundary
# ═══════════════════════════════════════════════════════════

class TestErrorBoundary:
    """ErrorBoundary 测试。"""

    def test_catches_exception(self):
        """捕获子组件异常，不向外传播。"""
        boundary = ErrorBoundary(children=[_BadComp()])
        output = boundary.render()
        # 不崩溃，返回回退 UI
        assert "Error" in output
        assert boundary.error is not None
        assert isinstance(boundary.error, ValueError)

    def test_default_fallback_shows_error(self):
        """默认回退 UI 显示错误类型和消息。"""
        boundary = ErrorBoundary(children=[_BadComp()])
        output = boundary.render()
        assert "ValueError" in output
        assert "模拟渲染异常" in output

    def test_custom_fallback(self):
        """自定义 fallback 函数生效。"""
        boundary = ErrorBoundary(
            children=[_BadComp()],
            fallback=lambda e: f"自定义错误: {e}",
        )
        output = boundary.render()
        assert "自定义错误" in output

    def test_passes_through_without_error(self):
        """无异常时正常渲染。"""
        boundary = ErrorBoundary(children=[_GoodComp("hello")])
        output = boundary.render()
        assert "hello" in output
        assert boundary.error is None

    def test_on_error_callback(self):
        """异常时触发 on_error 回调。"""
        errors = []

        def _on_error(e):
            errors.append(e)

        boundary = ErrorBoundary(
            children=[_BadComp()],
            on_error=_on_error,
        )
        boundary.render()
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    def test_clear_error(self):
        """clear_error() 清除错误状态。"""
        boundary = ErrorBoundary(children=[_BadComp()])
        boundary.render()
        assert boundary.error is not None

        boundary.clear_error()
        assert boundary.error is None

    def test_add_child_chain(self):
        """链式 add_child 返回 self。"""
        boundary = ErrorBoundary()
        result = boundary.add_child(_GoodComp("a"))
        assert result is boundary
        assert len(boundary.children) == 1

    def test_multiple_children(self):
        """多个子组件全部渲染。"""
        boundary = ErrorBoundary(
            children=[_GoodComp("a"), _GoodComp("b"), _GoodComp("c")],
        )
        output = boundary.render()
        assert "a" in output
        assert "b" in output
        assert "c" in output


# ═══════════════════════════════════════════════════════════
# TestDebugComponentTree
# ═══════════════════════════════════════════════════════════

class TestDebugComponentTree:
    """debug_component_tree 测试。"""

    def test_single_component(self):
        """单个组件输出包含组件类型名。"""
        comp = _GoodComp()
        tree = debug_component_tree(comp)
        assert "_GoodComp" in tree
        assert "dirty" in tree

    def test_nested_components(self):
        """嵌套组件显示树形结构。"""
        child = _GoodComp("leaf")
        parent = _ParentComp("root", children=[child])
        tree = debug_component_tree(parent)
        assert "_ParentComp" in tree
        assert "_GoodComp" in tree
        # 树形字符
        assert "└─" in tree or "├─" in tree

    def test_component_with_hooks(self):
        """含 hooks 的组件显示 hooks 信息。"""
        from src.chat_ui.vdom.types import HookState

        comp = _GoodComp()
        comp._ensure_hooks()
        comp._hooks.append(HookState(type="state", value=42))
        comp._hooks.append(HookState(type="effect", value=None))

        tree = debug_component_tree(comp)
        assert "hooks=2" in tree


# ═══════════════════════════════════════════════════════════
# TestInspectHooks
# ═══════════════════════════════════════════════════════════

class TestInspectHooks:
    """inspect_hooks 测试。"""

    def test_no_hooks(self):
        """无 hooks 的组件返回提示。"""
        comp = _GoodComp()
        result = inspect_hooks(comp)
        assert "no hooks" in result

    def test_state_hook(self):
        """state hook 显示值。"""
        from src.chat_ui.vdom.types import HookState

        comp = _GoodComp()
        comp._ensure_hooks()
        comp._hooks.append(HookState(type="state", value=42))

        result = inspect_hooks(comp)
        assert "[state]" in result
        assert "42" in result

    def test_effect_hook(self):
        """effect hook 显示依赖和 cleanup 信息。"""
        from src.chat_ui.vdom.types import HookState

        comp = _GoodComp()
        comp._ensure_hooks()
        comp._hooks.append(
            HookState(type="effect", value=None, deps=["a", "b"], cleanup=None)
        )

        result = inspect_hooks(comp)
        assert "[effect]" in result

    def test_ref_hook(self):
        """ref hook 显示 current 值。"""
        from src.chat_ui.vdom.types import HookState

        comp = _GoodComp()
        comp._ensure_hooks()
        comp._hooks.append(
            HookState(type="ref", value={"current": "hello"})
        )

        result = inspect_hooks(comp)
        assert "[ref]" in result
        assert "hello" in result


# ═══════════════════════════════════════════════════════════
# TestRenderStats
# ═══════════════════════════════════════════════════════════

class TestRenderStats:
    """RenderStats 测试。"""

    def test_initial_values(self):
        """初始统计值全为 0。"""
        stats = RenderStats()
        assert stats.frame_count == 0
        assert stats.total_render_time_ms == 0.0

    def test_record_frame(self):
        """record_frame 正确累加。"""
        stats = RenderStats()
        # 直接设置 _enabled 为 True 进行测试
        stats._enabled = True
        stats.record_frame(render_time_ms=16.0, component_count=5, hook_count=3)
        assert stats.frame_count == 1
        assert stats.total_render_time_ms == 16.0
        assert stats.component_count == 5
        assert stats.hook_count == 3

    def test_record_frame_disabled_noop(self):
        """禁用时 record_frame 不记录。"""
        stats = RenderStats()
        stats._enabled = False
        stats.record_frame(render_time_ms=100.0)
        assert stats.frame_count == 0

    def test_reset(self):
        """reset 归零所有计数。"""
        stats = RenderStats()
        stats._enabled = True
        stats.record_frame(10.0, 3, 2)
        stats.record_frame(20.0, 4, 3)

        stats.reset()
        assert stats.frame_count == 0
        assert stats.total_render_time_ms == 0.0

    def test_summary(self):
        """summary 输出格式化统计。"""
        stats = RenderStats()
        stats._enabled = True
        stats.record_frame(render_time_ms=10.0, component_count=3, hook_count=2)
        stats.record_frame(render_time_ms=20.0, component_count=4, hook_count=3)

        summary = stats.summary()
        assert "帧数" in summary
        assert "2" in summary
        assert "30.00" in summary or "30.0" in summary  # total 10+20

    def test_summary_disabled(self):
        """禁用时 summary 提示未启用。"""
        stats = RenderStats()
        stats._enabled = False
        summary = stats.summary()
        assert "未启用" in summary or "devtools" in summary

    def test_summary_empty(self):
        """无数据时 summary 提示。"""
        stats = RenderStats()
        stats._enabled = True
        summary = stats.summary()
        assert "尚无" in summary or "0" in summary
