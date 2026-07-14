"""测试 _animator 动画基础设施。

测试覆盖：
  - AnimatorContext.tick() 帧号递增
  - AnimatorContext.breath_frame 自动取模（12帧周期）
  - AnimatorContext.pulse_frame 自动取模（4帧周期）
  - AnimatorContext.progress_breath_offset（8帧周期）
  - AnimatorContext.agent_breath_offset（12帧周期）
  - AnimatorContext.get_default() 单例
  - AnimatorContext.reset_default() 重置
  - BreathPalette.register() / get() / get_color() / has() 全接口
  - BreathPalette.get_color() 取模循环
  - get("nonexistent") 降级返回空列表
  - get_color("nonexistent") 降级返回 45（CYAN_256）
  - 预注册调色板完整性（非空 + 长度正确）
  - 预注册调色板一致性（think/sep_msg/prompt 三者完全一致）
"""

from __future__ import annotations

import pytest

from src.tui.core.animator import AnimatorContext, BreathPalette


# ════════════════════════════════════════════════════════
# AnimatorContext — 帧号推进
# ════════════════════════════════════════════════════════

class TestAnimatorContextTick:
    """AnimatorContext.tick() 帧号递增行为。"""

    def test_tick_increments_frame(self):
        ctx = AnimatorContext()
        assert ctx.frame == 0
        ctx.tick()
        assert ctx.frame == 1
        ctx.tick()
        assert ctx.frame == 2

    def test_tick_with_delta(self):
        ctx = AnimatorContext()
        ctx.tick(delta=5)
        assert ctx.frame == 5

    def test_tick_default_delta_is_one(self):
        ctx = AnimatorContext()
        ctx.tick()
        ctx.tick()
        ctx.tick()
        assert ctx.frame == 3


class TestAnimatorContextBreathFrame:
    """AnimatorContext.breath_frame 自动取模（周期=12）。"""

    def test_breath_frame_starts_at_zero(self):
        ctx = AnimatorContext()
        assert ctx.breath_frame == 0

    def test_breath_frame_cycles(self):
        ctx = AnimatorContext()
        # 推进完整周期
        for _ in range(12):
            ctx.tick()
        assert ctx.frame == 12
        assert ctx.breath_frame == 0  # 回到起点

    def test_breath_frame_mod_12(self):
        ctx = AnimatorContext()
        ctx.tick(delta=5)
        assert ctx.breath_frame == 5
        ctx.tick(delta=7)
        assert ctx.breath_frame == 0  # (5+7) % 12 = 0

    def test_breath_frame_values_in_range(self):
        ctx = AnimatorContext()
        seen = set()
        for _ in range(24):
            seen.add(ctx.breath_frame)
            ctx.tick()
        assert seen == set(range(12))


class TestAnimatorContextPulseFrame:
    """AnimatorContext.pulse_frame 自动取模（周期=4）。"""

    def test_pulse_frame_starts_at_zero(self):
        ctx = AnimatorContext()
        assert ctx.pulse_frame == 0

    def test_pulse_frame_cycles(self):
        ctx = AnimatorContext()
        for _ in range(4):
            ctx.tick()
        assert ctx.pulse_frame == 0

    def test_pulse_frame_values_in_range(self):
        ctx = AnimatorContext()
        seen = set()
        for _ in range(12):
            seen.add(ctx.pulse_frame)
            ctx.tick()
        assert seen == set(range(4))


class TestAnimatorContextProgressBreathOffset:
    """AnimatorContext.progress_breath_offset 周期=8。"""

    def test_offset_starts_at_zero(self):
        ctx = AnimatorContext()
        assert ctx.progress_breath_offset == 0

    def test_offset_cycles_8(self):
        ctx = AnimatorContext()
        for _ in range(8):
            ctx.tick()
        assert ctx.progress_breath_offset == 0

    def test_offset_values_in_range(self):
        ctx = AnimatorContext()
        seen = set()
        for _ in range(16):
            seen.add(ctx.progress_breath_offset)
            ctx.tick()
        assert seen == set(range(8))


class TestAnimatorContextAgentBreathOffset:
    """AnimatorContext.agent_breath_offset 周期=12。"""

    def test_offset_starts_at_zero(self):
        ctx = AnimatorContext()
        assert ctx.agent_breath_offset == 0

    def test_offset_cycles_12(self):
        ctx = AnimatorContext()
        for _ in range(12):
            ctx.tick()
        assert ctx.agent_breath_offset == 0

    def test_offset_values_in_range(self):
        ctx = AnimatorContext()
        seen = set()
        for _ in range(24):
            seen.add(ctx.agent_breath_offset)
            ctx.tick()
        assert seen == set(range(12))


# ════════════════════════════════════════════════════════
# AnimatorContext — 单例
# ════════════════════════════════════════════════════════

class TestAnimatorContextSingleton:
    """AnimatorContext.get_default() 单例行为。"""

    def test_get_default_returns_same_instance(self):
        a = AnimatorContext.get_default()
        b = AnimatorContext.get_default()
        assert a is b

    def test_get_default_instance_is_animator_context(self):
        ctx = AnimatorContext.get_default()
        assert isinstance(ctx, AnimatorContext)

    def test_default_instance_state_shared(self):
        # 注意：测试间会共享默认实例，此测试依赖 reset_default 后执行
        AnimatorContext.reset_default()
        ctx = AnimatorContext.get_default()
        ctx.tick()
        assert ctx.frame == 1
        # 同一实例，再 get 发现 frame=1
        assert AnimatorContext.get_default().frame == 1
        AnimatorContext.reset_default()  # 清理


class TestAnimatorContextResetDefault:
    """AnimatorContext.reset_default() 重置行为。"""

    def test_reset_clears_default(self):
        AnimatorContext.reset_default()
        a = AnimatorContext.get_default()
        a.tick(delta=42)
        AnimatorContext.reset_default()
        b = AnimatorContext.get_default()
        assert b.frame == 0  # 新实例

    def test_reset_returns_new_instance(self):
        AnimatorContext.reset_default()
        a = AnimatorContext.get_default()
        AnimatorContext.reset_default()
        b = AnimatorContext.get_default()
        assert a is not b

    def test_reset_then_get_default_is_fresh(self):
        AnimatorContext.reset_default()
        ctx = AnimatorContext.get_default()
        assert ctx.frame == 0
        assert ctx.breath_frame == 0
        assert ctx.pulse_frame == 0


# ════════════════════════════════════════════════════════
# BreathPalette — 注册与查询
# ════════════════════════════════════════════════════════

class TestBreathPaletteRegister:
    """BreathPalette.register() 注册行为。"""

    def test_register_and_get(self):
        BreathPalette.register("_test_palette", [1, 2, 3])
        assert BreathPalette.get("_test_palette") == [1, 2, 3]

    def test_register_many(self):
        BreathPalette.register_many({
            "_test_a": [10, 20],
            "_test_b": [30, 40, 50],
        })
        assert BreathPalette.get("_test_a") == [10, 20]
        assert BreathPalette.get("_test_b") == [30, 40, 50]

    def test_register_overwrites_existing(self):
        BreathPalette.register("_test_overwrite", [1, 1, 1])
        BreathPalette.register("_test_overwrite", [9, 9, 9])
        assert BreathPalette.get("_test_overwrite") == [9, 9, 9]

    def test_register_defensive_copy(self):
        colors = [1, 2, 3]
        BreathPalette.register("_test_defensive", colors)
        colors.append(4)  # 外部修改不影响已注册的调色板
        assert BreathPalette.get("_test_defensive") == [1, 2, 3]


class TestBreathPaletteGet:
    """BreathPalette.get() 查询行为。"""

    def test_get_existing(self):
        BreathPalette.register("_test_get_existing", [5, 6, 7])
        assert BreathPalette.get("_test_get_existing") == [5, 6, 7]

    def test_get_nonexistent_returns_empty_list(self):
        assert BreathPalette.get("nonexistent") == []

    def test_get_returns_same_content(self):
        BreathPalette.register("_test_get_same", [1, 2])
        assert BreathPalette.get("_test_get_same") == [1, 2]


class TestBreathPaletteGetColor:
    """BreathPalette.get_color() 取色行为。"""

    def test_get_color_frame_zero(self):
        BreathPalette.register("_test_gc", [10, 20, 30])
        assert BreathPalette.get_color("_test_gc", 0) == 10

    def test_get_color_mod_cycle(self):
        BreathPalette.register("_test_mod", [10, 20, 30])
        assert BreathPalette.get_color("_test_mod", 3) == 10  # 3 % 3 = 0
        assert BreathPalette.get_color("_test_mod", 4) == 20  # 4 % 3 = 1
        assert BreathPalette.get_color("_test_mod", 5) == 30  # 5 % 3 = 2

    def test_get_color_nonexistent_returns_45(self):
        """不存在的调色板返回 CYAN_256=45。"""
        assert BreathPalette.get_color("nonexistent", 0) == 45
        assert BreathPalette.get_color("nonexistent", 99) == 45

    def test_get_color_default_frame_is_zero(self):
        BreathPalette.register("_test_default_frame", [7, 8, 9])
        assert BreathPalette.get_color("_test_default_frame") == 7


class TestBreathPaletteHas:
    """BreathPalette.has() 存在性检查。"""

    def test_has_existing(self):
        BreathPalette.register("_test_has_yes", [1])
        assert BreathPalette.has("_test_has_yes") is True

    def test_has_nonexistent(self):
        assert BreathPalette.has("_test_has_no") is False

    def test_has_after_register(self):
        assert BreathPalette.has("_test_has_later") is False
        BreathPalette.register("_test_has_later", [1])
        assert BreathPalette.has("_test_has_later") is True


# ════════════════════════════════════════════════════════
# 预注册调色板完整性
# ════════════════════════════════════════════════════════

class TestPreRegisteredPalettes:
    """预注册调色板完整性检查。"""

    # 预注册调色板名称列表
    PALETTE_NAMES = [
        "think",
        "sep_msg",
        "prompt",
        "role_user",
        "role_asst",
        "role_tool",
        "sep_bar",
        "breath_bg",
        "tool_pulse",
        "agent_breath",
        "progress_amber_green",
        "pulse",
        "model",
        "error_pulse",
        "warn_pulse",
        "status_pulse",
    ]

    def test_all_palettes_non_empty(self):
        for name in self.PALETTE_NAMES:
            colors = BreathPalette.get(name)
            assert colors, f"调色板 '{name}' 为空或不存在"
            assert len(colors) > 0, f"调色板 '{name}' 长度为零"

    def test_think_length(self):
        assert len(BreathPalette.get("think")) == 12

    def test_sep_msg_length(self):
        assert len(BreathPalette.get("sep_msg")) == 12

    def test_prompt_length(self):
        assert len(BreathPalette.get("prompt")) == 12

    def test_role_user_length(self):
        assert len(BreathPalette.get("role_user")) == 8

    def test_role_asst_length(self):
        assert len(BreathPalette.get("role_asst")) == 8

    def test_role_tool_length(self):
        assert len(BreathPalette.get("role_tool")) == 8

    def test_sep_bar_length(self):
        assert len(BreathPalette.get("sep_bar")) == 10

    def test_breath_bg_length(self):
        assert len(BreathPalette.get("breath_bg")) == 10

    def test_tool_pulse_length(self):
        assert len(BreathPalette.get("tool_pulse")) == 12

    def test_agent_breath_length(self):
        assert len(BreathPalette.get("agent_breath")) == 12

    def test_progress_amber_green_length(self):
        assert len(BreathPalette.get("progress_amber_green")) == 8

    def test_pulse_length(self):
        assert len(BreathPalette.get("pulse")) == 4

    def test_model_length(self):
        assert len(BreathPalette.get("model")) == 4

    def test_error_pulse_length(self):
        assert len(BreathPalette.get("error_pulse")) == 6

    def test_warn_pulse_length(self):
        assert len(BreathPalette.get("warn_pulse")) == 6

    def test_status_pulse_length(self):
        assert len(BreathPalette.get("status_pulse")) == 8


class TestPreRegisteredConsistency:
    """预注册调色板一致性检查。"""

    def test_think_sep_msg_prompt_identical(self):
        """think/sep_msg/prompt 三者 gradient_range 参数完全一致（长度都=12）。"""
        think = BreathPalette.get("think")
        sep_msg = BreathPalette.get("sep_msg")
        prompt = BreathPalette.get("prompt")
        assert think == sep_msg == prompt
        assert len(think) == 12
        assert len(sep_msg) == 12
        assert len(prompt) == 12

    def test_think_colors_are_ints_in_range(self):
        """所有颜色值应为有效 256 色号（0-255）。"""
        colors = BreathPalette.get("think")
        for c in colors:
            assert 0 <= c <= 255, f"色号 {c} 超出 0-255 范围"

    def test_all_colors_are_ints(self):
        """所有预注册调色板的色号均为整数且范围有效。"""
        for name in TestPreRegisteredPalettes.PALETTE_NAMES:
            colors = BreathPalette.get(name)
            for c in colors:
                assert isinstance(c, int), f"'{name}' 中含非整数: {c}"
                assert 0 <= c <= 255, f"'{name}' 中色号 {c} 超出范围"

    def test_sep_bar_monotonic_decrease_then_increase(self):
        """sep_bar 先降后升，形状为 V 形。"""
        colors = BreathPalette.get("sep_bar")
        assert len(colors) >= 3
        # 前半段严格递减
        mid = len(colors) // 2
        for i in range(mid - 1):
            assert colors[i] > colors[i + 1], (
                f"前半段 ({i}) {colors[i]} 应 > {colors[i + 1]}"
            )
        # 后半段严格递增
        for i in range(mid, len(colors) - 1):
            assert colors[i] < colors[i + 1], (
                f"后半段 ({i}) {colors[i]} 应 < {colors[i + 1]}"
            )

    def test_breath_bg_monotonic_increase_then_decrease(self):
        """breath_bg 先升后降，形状为 ^ 形（山峰形）。"""
        colors = BreathPalette.get("breath_bg")
        assert len(colors) >= 3
        mid = len(colors) // 2
        for i in range(mid - 1):
            assert colors[i] < colors[i + 1], (
                f"前半段 ({i}) {colors[i]} 应 < {colors[i + 1]}"
            )
        for i in range(mid, len(colors) - 1):
            assert colors[i] > colors[i + 1], (
                f"后半段 ({i}) {colors[i]} 应 > {colors[i + 1]}"
            )
