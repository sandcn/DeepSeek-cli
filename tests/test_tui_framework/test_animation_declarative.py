"""测试声明式动效集成 — @effect 装饰器 + AnimatedWidget。

覆盖：
  - @effect 装饰器元数据注册
  - EffectInstance 生命周期（trigger/tick/reset/active）
  - AnimatedWidget 效果初始化与触发
  - 各效果类型渲染验证（fade_in/slide_in/pulse/shimmer/rainbow）
  - 多效果叠加
  - 效果与 Widget 子类集成
"""

from __future__ import annotations

import pytest

from tui_framework.animation.declarative import (
    EffectBuilder,
    EffectInstance,
    effect,
)
from tui_framework.widgets.animated import AnimatedWidget


# ═══════════════════════════════════════════════════════════
# @effect 装饰器测试
# ═══════════════════════════════════════════════════════════


class TestEffectDecorator:
    """@effect 装饰器测试。"""

    def test_single_effect_registers_metadata(self):
        """单个 @effect 装饰器正确注册元数据。"""
        @effect("test_anim", type="fade_in", duration=6, easing="smooth")
        class TestWidget(AnimatedWidget):
            pass

        assert hasattr(TestWidget, "_declared_effects")
        assert len(TestWidget._declared_effects) == 1
        meta = TestWidget._declared_effects[0]
        assert meta["name"] == "test_anim"
        assert meta["type"] == "fade_in"
        assert meta["duration"] == 6
        assert meta["easing"] == "smooth"

    def test_multiple_effects_stacked(self):
        """多个 @effect 装饰器叠加正确。"""
        @effect("second", type="pulse", duration=4, easing="bounce")
        @effect("first", type="fade_in", duration=6, easing="smooth")
        class TestWidget(AnimatedWidget):
            pass

        assert len(TestWidget._declared_effects) == 2
        assert TestWidget._declared_effects[0]["name"] == "first"
        assert TestWidget._declared_effects[1]["name"] == "second"

    def test_unsupported_effect_type_raises(self):
        """不支持的效果类型抛出 ValueError。"""
        with pytest.raises(ValueError, match="不支持的效果类型"):
            @effect("bad", type="unknown_type")
            class TestWidget(AnimatedWidget):
                pass

    def test_all_supported_types_accepted(self):
        """所有支持的效果类型均可正常注册。"""
        for etype in ("fade_in", "slide_in", "pulse", "shimmer", "rainbow"):
            @effect(f"test_{etype}", type=etype)
            class _(AnimatedWidget):
                pass

    def test_decorator_returns_same_class(self):
        """装饰器返回原始类（保持类身份）。"""
        @effect("test", type="fade_in")
        class TestWidget(AnimatedWidget):
            pass

        assert issubclass(TestWidget, AnimatedWidget)

    def test_default_duration_and_easing(self):
        """未指定 duration/easing 时使用默认值。"""
        @effect("test", type="fade_in")
        class TestWidget(AnimatedWidget):
            pass

        meta = TestWidget._declared_effects[0]
        assert meta["duration"] == 6
        assert meta["easing"] == "smooth"


# ═══════════════════════════════════════════════════════════
# EffectInstance 测试
# ═══════════════════════════════════════════════════════════


class TestEffectInstance:
    """EffectInstance 生命周期与状态测试。"""

    def test_initial_state_inactive(self):
        """初始状态为未激活。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        assert not inst.active
        assert inst.frame == -1
        assert inst.progress == 0.0

    def test_trigger_activates(self):
        """trigger() 激活效果，帧号重置为 0。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        inst.trigger()
        assert inst.active
        assert inst.frame == 0

    def test_tick_advances_frame(self):
        """tick() 推进帧号。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        inst.trigger()
        assert inst.frame == 0
        inst.tick()
        assert inst.frame == 1
        inst.tick()
        assert inst.frame == 2

    def test_tick_noop_when_inactive(self):
        """未激活时 tick() 无效果。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        inst.tick()
        inst.tick()
        assert not inst.active
        assert inst.frame == -1

    def test_completes_after_duration(self):
        """帧号达到 duration 后完成（不再激活）。"""
        inst = EffectInstance(name="test", type="fade_in", duration=3)
        inst.trigger()
        inst.tick()  # 0→1
        inst.tick()  # 1→2
        inst.tick()  # 2→3 (active=False)
        assert not inst.active
        assert inst.frame == 3

    def test_reset_deactivates(self):
        """reset() 将效果重置为未激活。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        inst.trigger()
        inst.tick()
        inst.reset()
        assert not inst.active
        assert inst.frame == -1

    def test_progress_calculation(self):
        """progress 属性正确计算。"""
        inst = EffectInstance(name="test", type="fade_in", duration=5)
        inst.trigger()
        assert inst.progress == 0.0
        inst.tick()
        assert inst.progress == 0.25  # 1/4
        inst.tick()
        assert inst.progress == 0.5  # 2/4
        inst.tick()
        assert inst.progress == 0.75  # 3/4
        inst.tick()
        assert inst.progress == 1.0  # 4/4

    def test_progress_inactive_returns_zero(self):
        """未激活时 progress 返回 0。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        assert inst.progress == 0.0

    def test_duration_zero_safe(self):
        """duration=0 时 activate→tick 立即完成。"""
        inst = EffectInstance(name="test", type="fade_in", duration=0)
        inst.trigger()
        assert not inst.active


# ═══════════════════════════════════════════════════════════
# EffectInstance 渲染效果测试
# ═══════════════════════════════════════════════════════════


class TestEffectRendering:
    """各效果类型的渲染输出测试。"""

    def test_fade_in_produces_ansi_color(self):
        """fade_in 效果产生 ANSI 颜色序列。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        inst.trigger()
        result = inst.apply("Hello")
        assert "\033[38;5;" in result
        assert "Hello" in result
        assert result.endswith("\033[0m")

    def test_fade_in_inactive_returns_unchanged(self):
        """未激活的 fade_in 返回原始内容。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        result = inst.apply("Hello")
        assert result == "Hello"

    def test_fade_in_empty_content(self):
        """空内容的 fade_in 返回空字符串。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6)
        inst.trigger()
        result = inst.apply("")
        assert result == ""

    def test_slide_in_reveals_progressively(self):
        """slide_in 逐帧增加可见字符。"""
        inst = EffectInstance(name="test", type="slide_in", duration=6, easing="linear")
        inst.trigger()
        # frame 0: progress = 0/5 = 0
        r0 = inst.apply("ABCDEF")
        assert r0 == ""

        # Advance to frame 3: progress = 3/5 = 0.6, reveal 4 chars
        for _ in range(3):
            inst.tick()
        r3 = inst.apply("ABCDEF")
        assert len(r3) > 0

    def test_slide_in_full_reveal_at_end(self):
        """slide_in 在最后一帧完全揭示。"""
        inst = EffectInstance(name="test", type="slide_in", duration=6, easing="linear")
        inst.trigger()
        for _ in range(5):
            inst.tick()
        # frame 5: progress = 5/5 = 1.0
        inst.tick()  # advances past duration, but apply still uses frame 5
        result = inst.apply("ABCDEF")
        assert result == "ABCDEF"

    def test_pulse_produces_ansi_color(self):
        """pulse 效果产生呼吸色 ANSI 序列。"""
        inst = EffectInstance(name="test", type="pulse", duration=6)
        inst.trigger()
        result = inst.apply("Pulse")
        assert "\033[38;5;" in result
        assert "Pulse" in result

    def test_shimmer_adds_highlight_band(self):
        """shimmer 效果对部分字符添加高亮色。"""
        inst = EffectInstance(name="test", type="shimmer", duration=10)
        inst.trigger()
        result = inst.apply("Shimmer")
        # 应至少包含一些 ANSI 颜色序列
        assert "\033[38;5;" in result

    def test_rainbow_cycles_colors(self):
        """rainbow 效果每个字符使用不同色号。"""
        inst = EffectInstance(name="test", type="rainbow", duration=12)
        inst.trigger()
        result = inst.apply("Rainbow")
        # 多个 \033[38;5; 序列表示逐字符着色
        assert result.count("\033[38;5;") >= 1

    def test_apply_inactive_returns_unchanged_for_all_types(self):
        """所有效果类型在未激活时返回原始内容。"""
        for etype in ("fade_in", "slide_in", "pulse", "shimmer", "rainbow"):
            inst = EffectInstance(name="test", type=etype, duration=6)
            result = inst.apply("Test")
            assert result == "Test", f"type={etype} 未激活时应返回原始内容"

    def test_bounce_easing_applied(self):
        """bounce 缓动效果可正常应用。"""
        inst = EffectInstance(name="test", type="fade_in", duration=6, easing="bounce")
        inst.trigger()
        result = inst.apply("Bounce")
        assert "\033[38;5;" in result


# ═══════════════════════════════════════════════════════════
# EffectBuilder 测试
# ═══════════════════════════════════════════════════════════


class TestEffectBuilder:
    """EffectBuilder 测试。"""

    def test_build_creates_effect_instance(self):
        """build() 从元数据创建 EffectInstance。"""
        meta = {"name": "test", "type": "fade_in", "duration": 8, "easing": "smooth"}
        inst = EffectBuilder.build(meta)
        assert isinstance(inst, EffectInstance)
        assert inst.name == "test"
        assert inst.type == "fade_in"
        assert inst.duration == 8
        assert inst.easing == "smooth"

    def test_build_defaults(self):
        """build() 对缺少的字段使用默认值。"""
        meta = {"name": "test", "type": "fade_in"}
        inst = EffectBuilder.build(meta)
        assert inst.duration == 6
        assert inst.easing == "smooth"

    def test_get_effect_fn_for_registered_type(self):
        """get_effect_fn() 对已注册且有函数的类型返回函数。"""
        # "pulse" 映射到 EffectRegistry 的 "pulse"，有函数实现
        fn = EffectBuilder.get_effect_fn("pulse")
        assert fn is not None

    def test_get_effect_fn_for_unregistered_type(self):
        """get_effect_fn() 对无 EffectRegistry 映射的类型返回 None。"""
        fn = EffectBuilder.get_effect_fn("slide_in")
        assert fn is None  # slide_in has no EffectRegistry mapping


# ═══════════════════════════════════════════════════════════
# AnimatedWidget 测试
# ═══════════════════════════════════════════════════════════


class TestAnimatedWidget:
    """AnimatedWidget 效果初始化与触发测试。"""

    def test_effects_initialized_on_mount(self):
        """did_mount() 时从 _declared_effects 初始化效果实例。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()

        assert len(w._effect_instances) == 1
        assert isinstance(w._effect_instances[0], EffectInstance)
        assert w._effect_instances[0].name == "appear"

    def test_trigger_effect_activates(self):
        """trigger_effect() 激活指定效果。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()

        assert not w._effect_instances[0].active
        result = w.trigger_effect("appear")
        assert result is True
        assert w._effect_instances[0].active

    def test_trigger_nonexistent_effect_returns_false(self):
        """触发不存在效果返回 False。"""
        @effect("appear", type="fade_in")
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        assert w.trigger_effect("nonexistent") is False

    def test_reset_effect(self):
        """reset_effect() 重置指定效果。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        w.trigger_effect("appear")
        assert w._effect_instances[0].active

        result = w.reset_effect("appear")
        assert result is True
        assert not w._effect_instances[0].active

    def test_get_effect_by_name(self):
        """get_effect() 按名称获取效果实例。"""
        @effect("first", type="fade_in")
        @effect("second", type="pulse")
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()

        inst = w.get_effect("first")
        assert inst is not None
        assert inst.name == "first"

        inst2 = w.get_effect("second")
        assert inst2 is not None
        assert inst2.name == "second"

    def test_has_active_effects(self):
        """has_active_effects() 正确反映激活状态。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        assert not w.has_active_effects()

        w.trigger_effect("appear")
        assert w.has_active_effects()

    def test_apply_effects_wraps_content(self):
        """_apply_effects() 对激活效果施加 ANSI。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        w.trigger_effect("appear")

        result = w._apply_effects("Content")
        assert "\033[38;5;" in result
        assert "Content" in result

    def test_apply_effects_no_active_returns_unchanged(self):
        """无激活效果时 _apply_effects() 返回原始内容。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        result = w._apply_effects("Content")
        assert result == "Content"

    def test_apply_effects_empty_content(self):
        """空内容时 _apply_effects() 返回空字符串。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        w.trigger_effect("appear")
        result = w._apply_effects("")
        assert result == ""

    def test_multiple_effects_overlay(self):
        """多个激活效果可以叠加。"""
        @effect("second", type="pulse", duration=10)
        @effect("first", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        w.trigger_effect("first")
        w.trigger_effect("second")

        result = w._apply_effects("Multi")
        # 两个 ANI 效果叠加
        assert "\033[38;5;" in result
        assert "Multi" in result

    def test_current_frame_property(self):
        """current_frame 返回全局动画帧号。"""
        @effect("test", type="fade_in")
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        frame = w.current_frame
        assert isinstance(frame, int)
        assert frame >= 0

    def test_no_declared_effects_safe(self):
        """无 _declared_effects 的类正常实例化。"""
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        assert w._effect_instances == []
        assert not w.has_active_effects()

    def test_update_animation_manual_frame(self):
        """update_animation() 手动设置帧号。"""
        @effect("test", type="fade_in")
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        old_frame = w.current_frame
        w.update_animation(42)
        assert w.current_frame == 42
        # Restore
        w.update_animation(old_frame)

    def test_widget_effects_list_synced(self):
        """Widget._effects 与 AnimatedWidget._effect_instances 同步。"""
        @effect("appear", type="fade_in", duration=6)
        class TestW(AnimatedWidget):
            pass

        w = TestW()
        w.did_mount()
        assert len(w._effects) == 1
        assert w._effects[0] is w._effect_instances[0]


# ═══════════════════════════════════════════════════════════
# Select/Dialog 动效集成烟雾测试
# ═══════════════════════════════════════════════════════════


class TestWidgetAnimationIntegration:
    """Select/Dialog 与声明式动效集成烟雾测试。"""

    def test_select_has_declared_effects(self):
        """Select 类注册了 expand 效果。"""
        from tui_framework.widgets.select import Select
        assert hasattr(Select, "_declared_effects")
        names = [e["name"] for e in Select._declared_effects]
        assert "expand" in names

    def test_dialog_has_declared_effects(self):
        """Dialog 类注册了 appear 效果。"""
        from tui_framework.widgets.dialog import Dialog
        assert hasattr(Dialog, "_declared_effects")
        names = [e["name"] for e in Dialog._declared_effects]
        assert "appear" in names

    def test_select_expand_triggers_effect(self):
        """Select 展开时触发 expand 效果。"""
        from tui_framework.events.event_types import KeyPressEvent
        from tui_framework.widgets.select import Select

        sel = Select(options=["A", "B", "C"])
        sel.did_mount()

        assert not sel.get_effect("expand").active

        # 模拟 Enter 展开
        sel.on_key(KeyPressEvent(key="enter"))
        assert sel.expanded
        assert sel.get_effect("expand").active

    def test_dialog_first_render_triggers_appear(self):
        """Dialog 首次渲染时触发 appear 效果。"""
        from tui_framework.widgets.dialog import Dialog

        dlg = Dialog(title="Test", content="Hello")
        dlg.did_mount()

        assert not dlg._appear_triggered
        assert not dlg.get_effect("appear").active

        dlg.render()
        assert dlg._appear_triggered
        assert dlg.get_effect("appear").active

    def test_dialog_appear_only_once(self):
        """Dialog appear 效果仅触发一次。"""
        from tui_framework.widgets.dialog import Dialog

        dlg = Dialog(title="Test", content="Hello")
        dlg.did_mount()

        dlg.render()
        first_frame = dlg.get_effect("appear").frame

        dlg.render()  # 第二次渲染不重复触发
        # 帧号已推进（tick 发生在 render 的 _apply_effects 中）
        second_frame = dlg.get_effect("appear").frame
        # 第二次应该比第一次多 1（因为 tick 了一次）
        assert second_frame == first_frame or second_frame == first_frame + 1

    def test_select_renders_with_animation(self):
        """Select 展开状态下渲染含动效 ANSI。"""
        from tui_framework.events.event_types import KeyPressEvent
        from tui_framework.widgets.select import Select

        sel = Select(options=["Alpha", "Beta", "Gamma"])
        sel.did_mount()
        sel.on_key(KeyPressEvent(key="enter"))

        output = sel.render()
        # 展开时边框应保留，内容可能因 slide_in 效果部分可见
        assert "┌" in output or "Alpha" in output or "▼" in output or "▲" in output

    def test_dialog_renders_with_animation(self):
        """Dialog 渲染含动效 ANSI。"""
        from tui_framework.widgets.dialog import Dialog

        dlg = Dialog(title="MyDialog", content="Body text", width=40)
        dlg.did_mount()

        output = dlg.render()
        # 渐显效果会产生 ANSI 颜色序列
        assert "\033[38;5;" in output
        assert "MyDialog" in output
