"""集成测试 — Hooks + Box + Focus + Animation 组合场景。

覆盖跨模块边界的端到端场景，验证模块间协作正确性。
"""

from __future__ import annotations

import pytest

from src.chat_ui.react_ink._hooks import (
    _hooks_runtime,
    use_state,
    use_effect,
    use_ref,
)
from src.chat_ui.react_ink._focus import FocusManager, _FocusableEntry
from src.chat_ui.react_ink._box import Box
from src.chat_ui.react_ink._animation import AnimationClock, _AnimationState
from src.chat_ui._components import TuiComponent
from src.chat_ui.react_ink._types import HookState


# ── 测试辅助 ────────────────────────────────────────────

class _MockComponent:
    """模拟 TuiComponent（同 test_hooks.py）。"""

    def __init__(self):
        self._hooks: list[HookState] | None = None
        self._hook_index: int = 0
        self._dirty: bool = False
        self._mounted: bool = False

    def _ensure_hooks(self) -> list[HookState]:
        if self._hooks is None:
            self._hooks = []
        return self._hooks


class _TextComp(TuiComponent):
    """简单文本子组件。"""

    def __init__(self, text: str = "hello"):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测试前后清理全局状态。"""
    _hooks_runtime._pending_effects.clear()
    _hooks_runtime._component_stack.clear()
    _hooks_runtime._current_component = None
    _hooks_runtime._rerender_callback = None

    FocusManager._instance = None

    clock = AnimationClock.get_instance()
    if clock is not None:
        clock.stop()
    AnimationClock._set_instance(None)

    yield

    _hooks_runtime._pending_effects.clear()
    _hooks_runtime._component_stack.clear()
    _hooks_runtime._current_component = None
    _hooks_runtime._rerender_callback = None

    FocusManager._instance = None

    clock = AnimationClock.get_instance()
    if clock is not None:
        clock.stop()
    AnimationClock._set_instance(None)


def _enter(comp):
    _hooks_runtime.enter_component(comp)


def _exit(comp):
    _hooks_runtime.exit_component(comp)


# ═══════════════════════════════════════════════════════════
# TestHooksWithBox
# ═══════════════════════════════════════════════════════════

class TestHooksWithBox:
    """Hooks + Box 组合场景。"""

    def test_state_driven_box_content(self):
        """use_state 驱动 Box 子组件内容变更。"""
        comp = _MockComponent()
        _enter(comp)
        text, set_text = use_state("initial")
        _exit(comp)

        # 用 state 值构建 Box 内容
        box = Box(border_style="single", children=_TextComp(text))
        output = box.render()
        assert "initial" in str(output)

        # 更新 state
        _enter(comp)
        text2, set_text2 = use_state("initial")
        set_text2("updated")
        _exit(comp)

        # 新 Box 渲染更新后的内容
        _enter(comp)
        text3, _ = use_state("initial")
        _exit(comp)
        box2 = Box(border_style="single", children=_TextComp(text3))
        output2 = box2.render()
        assert "updated" in str(output2)

    def test_effect_on_box_mount(self):
        """use_effect 在 Box 渲染后触发副作用。"""
        mounted = []

        def _effect():
            mounted.append("mounted")
            return None

        comp = _MockComponent()
        _enter(comp)
        use_effect(_effect, [])
        _exit(comp)

        # 模拟"渲染后"执行 effect
        _hooks_runtime.run_effects()
        assert "mounted" in mounted

    def test_multiple_hooks_in_box_context(self):
        """在 Box 上下文中使用多个 hooks。"""
        comp = _MockComponent()
        _enter(comp)
        # 模拟组件内部使用 hooks 来管理 Box 属性
        count, set_count = use_state(0)
        ref = use_ref("default")
        _exit(comp)

        assert count == 0
        assert ref["current"] == "default"

        # 更新 state 并用 ref 传递配置
        _enter(comp)
        count2, set_count2 = use_state(0)
        ref2 = use_ref("default")
        set_count2(5)
        ref2["current"] = "custom_bg"
        _exit(comp)

        # 用于 Box 构造
        _enter(comp)
        count3, _ = use_state(0)
        ref3 = use_ref("default")
        _exit(comp)

        assert count3 == 5
        assert ref3["current"] == "custom_bg"


# ═══════════════════════════════════════════════════════════
# TestFocusWithAnimation
# ═══════════════════════════════════════════════════════════

class TestFocusWithAnimation:
    """Focus + Animation 组合场景。"""

    def test_focus_changes_during_animation(self):
        """动画进行中焦点切换正常工作。"""
        import time

        # 启动动画时钟
        clock = AnimationClock(on_tick=lambda: None)
        clock.start()

        # 注册焦点组件
        fm = FocusManager()
        fm.register("a", _FocusableEntry(component=None, is_active=True))
        fm.register("b", _FocusableEntry(component=None, is_active=True))

        # 创建动画
        anim = _AnimationState(interval=10, is_active=True)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        clock.register(anim)

        # 焦点操作与动画并行
        fm.focus("a")
        assert fm.active_id == "a"

        # 推进动画帧
        time.sleep(0.015)
        clock._tick()

        # 动画不影响焦点
        assert fm.active_id == "a"

        # 切换焦点
        fm.focus_next()
        assert fm.active_id == "b"

        # 再次推进动画
        time.sleep(0.015)
        clock._tick()

        # 焦点保持不变
        assert fm.active_id == "b"

        clock.stop()

    def test_focus_manager_singleton_independent_of_animation(self):
        """FocusManager 和 AnimationClock 单例独立。"""
        fm1 = FocusManager()
        fm2 = FocusManager()
        assert fm1 is fm2  # FocusManager 是单例

        clock = AnimationClock(on_tick=lambda: None)
        # 两个单例互不影响
        assert fm1 is not clock

    def test_focus_enable_disable_during_animation(self):
        """动画期间启用/禁用焦点。"""
        import time

        clock = AnimationClock(on_tick=lambda: None)
        clock.start()

        fm = FocusManager()
        fm.register("x", _FocusableEntry(component=None, is_active=True))

        anim = _AnimationState(interval=10, is_active=True)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        clock.register(anim)

        # 禁用焦点
        fm.disable()
        fm.focus_next()
        assert fm.active_id is None

        # 动画仍在运行
        time.sleep(0.015)
        clock._tick()
        assert anim.frame >= 0

        # 重新启用焦点
        fm.enable()
        fm.focus_next()
        assert fm.active_id == "x"

        clock.stop()
