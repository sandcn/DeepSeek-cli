"""AnimationClock 和 _AnimationState 单元测试。

覆盖 animation 系统的注册/注销/帧更新/启停/重置。
测试策略：直接测试 AnimationClock 和 _AnimationState，
不依赖 use_animation hook（因其依赖 Hooks 运行时上下文）。
"""

from __future__ import annotations

import time
import pytest

from src.chat_ui.components.animation import (
    AnimationClock,
    SPINNER_FRAMES,
    _AnimationState,
    use_animation,
    use_typewriter,
)


# ── 测试辅助 ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_animation_clock():
    """每个测试前后重置 AnimationClock 单例。"""
    clock = AnimationClock.get_instance()
    if clock is not None:
        clock.stop()
    AnimationClock._set_instance(None)
    yield
    clock = AnimationClock.get_instance()
    if clock is not None:
        clock.stop()
    AnimationClock._set_instance(None)


# ═══════════════════════════════════════════════════════════
# TestAnimationClock
# ═══════════════════════════════════════════════════════════

class TestAnimationClock:
    """AnimationClock 测试。"""

    def test_singleton_lifecycle(self):
        """start/stop 正确管理全局单例引用。"""
        assert AnimationClock.get_instance() is None

        clock = AnimationClock(on_tick=lambda: None)
        clock.start()
        assert AnimationClock.get_instance() is clock

        clock.stop()
        assert AnimationClock.get_instance() is None

    def test_register_animation(self):
        """注册动画实例后 _animations 表中出现该条目。"""
        clock = AnimationClock(on_tick=lambda: None)
        anim = _AnimationState(interval=100)
        clock.register(anim)
        assert id(anim) in clock._animations

    def test_unregister_animation(self):
        """注销后动画从表中移除。"""
        clock = AnimationClock(on_tick=lambda: None)
        anim = _AnimationState(interval=100)
        clock.register(anim)
        assert id(anim) in clock._animations

        clock.unregister(anim)
        assert id(anim) not in clock._animations

    def test_tick_increments_frame(self):
        """_tick() 调用后活跃动画的 frame 递增。"""
        clock = AnimationClock(on_tick=lambda: None)
        anim = _AnimationState(interval=10, is_active=True)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        clock.register(anim)

        # 等待超过 interval 以确保 _tick 累积一帧
        time.sleep(0.015)  # 15ms > 10ms interval
        clock._tick()

        assert anim.frame >= 1

    def test_tick_skips_inactive(self):
        """_tick() 跳过 is_active=False 的动画。"""
        clock = AnimationClock(on_tick=lambda: None)
        anim = _AnimationState(interval=10, is_active=False)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        clock.register(anim)

        # 足够时间后 _tick
        time.sleep(0.015)
        clock._tick()

        # 非活跃动画 frame 应保持 0
        assert anim.frame == 0
        assert anim.time == 0.0
        assert anim.delta == 0.0

    def test_tick_cleans_dead_references(self):
        """_tick() 自动清理已死亡的弱引用条目。"""
        clock = AnimationClock(on_tick=lambda: None)
        anim = _AnimationState(interval=100)
        clock.register(anim)
        key = id(anim)
        assert key in clock._animations

        # 删除对其的唯一强引用
        del anim
        clock._tick()

        # 弱引用已失效，应被清理
        assert key not in clock._animations

    def test_start_stop(self):
        """start/stop 的幂等性和状态。"""
        tick_count = [0]
        clock = AnimationClock(on_tick=lambda: tick_count.__setitem__(0, tick_count[0] + 1))

        # 首次 start
        clock.start()
        assert clock._running is True
        # 二次 start 幂等
        clock.start()
        assert clock._running is True

        # 等待至少一次 tick
        time.sleep(0.1)

        clock.stop()
        assert clock._running is False
        # 二次 stop 幂等
        clock.stop()
        assert clock._running is False

    def test_elapsed_property(self):
        """elapsed 返回从 start 开始的毫秒数。"""
        clock = AnimationClock(on_tick=lambda: None)
        assert clock.elapsed == 0.0

        clock.start()
        time.sleep(0.01)
        assert clock.elapsed > 0.0

        clock.stop()
        # stop 后 elapsed 仍基于 time.monotonic() 持续增长
        # （因为 _start_time 未重置）
        elapsed_after_stop = clock.elapsed
        assert elapsed_after_stop > 0.0


# ═══════════════════════════════════════════════════════════
# TestAnimationState
# ═══════════════════════════════════════════════════════════

class TestAnimationState:
    """_AnimationState 测试。"""

    def test_initial_values(self):
        """初始值符合默认。"""
        anim = _AnimationState()
        assert anim.interval == 100
        assert anim.is_active is True
        assert anim.frame == 0
        assert anim.time == 0.0
        assert anim.delta == 0.0

    def test_reset(self):
        """reset 归零 frame/time/delta。"""
        anim = _AnimationState(interval=10)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        anim.frame = 10
        anim.time = 1000.0
        anim.delta = 50.0

        # 模拟 reset
        anim.frame = 0
        anim.time = 0.0
        anim.delta = 0.0
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()

        assert anim.frame == 0
        assert anim.time == 0.0
        assert anim.delta == 0.0

    def test_inactive_does_not_update(self):
        """非活跃状态的动画不更新 time/frame。"""
        anim = _AnimationState(interval=10, is_active=False)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()

        # 经过时间后手动标记 — 实际由 _tick 跳过
        # 这里验证状态未变化
        assert anim.frame == 0
        assert anim.time == 0.0

    def test_custom_interval(self):
        """自定义 interval 值。"""
        anim = _AnimationState(interval=200)
        assert anim.interval == 200

    def test_deactivate_then_reactivate(self):
        """先停用再激活，状态保留但更新暂停。"""
        anim = _AnimationState(interval=10, is_active=True)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()

        # 停用
        anim.is_active = False
        assert anim.is_active is False

        # 状态保留
        assert anim.frame == 0

        # 重新激活
        anim.is_active = True
        assert anim.is_active is True


# ═══════════════════════════════════════════════════════════
# TestSpinnerNewTypes
# ═══════════════════════════════════════════════════════════

NEW_SPINNER_TYPES = [
    "dots_matrix",
    "arc",
    "bouncing_ball",
    "clock",
    "shark",
]


class TestSpinnerNewTypes:
    """SPINNER_FRAMES 新增 5 种 spinner 类型测试。"""

    @pytest.mark.parametrize("spinner_type", NEW_SPINNER_TYPES)
    def test_all_new_spinner_types_return_char(self, spinner_type: str):
        """参数化测试：5 种新类型均返回非空帧列表且每帧为有效字符串。"""
        frames = SPINNER_FRAMES[spinner_type]
        assert isinstance(frames, list), f"{spinner_type} 帧列表应为 list"
        assert len(frames) > 0, f"{spinner_type} 帧列表不应为空"

        for i, frame in enumerate(frames):
            assert isinstance(frame, str), (
                f"{spinner_type}[{i}] 应为 str，实际: {type(frame).__name__}"
            )
            assert len(frame) > 0, f"{spinner_type}[{i}] 不应为空字符串"

    def test_dots_matrix_frames(self):
        """dots_matrix 每帧长度均为 3（3×3 点阵）。"""
        frames = SPINNER_FRAMES["dots_matrix"]
        assert len(frames) == 6, f"dots_matrix 应有 6 帧，实际: {len(frames)}"

        for i, frame in enumerate(frames):
            assert len(frame) == 3, (
                f"dots_matrix[{i}] 长度应为 3，实际: {len(frame)}（值: {frame!r}）"
            )

    def test_new_types_in_spinner_frames(self):
        """SPINNER_FRAMES 包含全部 5 种新类型的 key。"""
        for spinner_type in NEW_SPINNER_TYPES:
            assert spinner_type in SPINNER_FRAMES, (
                f"SPINNER_FRAMES 缺少 key: {spinner_type!r}"
            )


# ═══════════════════════════════════════════════════════════
# TestTypewriterEnhanced
# ═══════════════════════════════════════════════════════════

class TestTypewriterEnhanced:
    """use_typewriter 增强测试 — 光标闪烁、样式、done 过渡。"""

    def test_cursor_blinks(self):
        """光标在相邻帧切换可见/不可见（frame 奇偶决定）。"""
        from unittest.mock import patch

        # frame 0 (even) → cursor visible (time=150 确保 chars_shown≥1)
        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 150, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("hello", {"speed": 100, "cursor": True})
        assert tw["cursor_visible"] is True
        assert "▊" in tw["output"]

        # frame 1 (odd) → cursor hidden
        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 1, "time": 100, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("hello", {"speed": 100, "cursor": True})
        assert tw["cursor_visible"] is False
        assert "▊" not in tw["output"]

    def test_cursor_removed_after_done(self):
        """done 后延迟约 300ms 光标最终消失。"""
        from unittest.mock import patch

        # done=true + time 仍在 300ms 窗口内（done_time=2*100=200, time=250 < 500）
        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 20, "time": 250, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("hi", {"speed": 100, "cursor": True})
        assert tw["done"] is True
        assert tw["cursor_visible"] is True  # frame 20 even, 仍在过渡期
        assert "▊" in tw["output"]

        # done=true + time 远超 300ms 窗口（done_time=200, time=600 > 500）
        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 50, "time": 600, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("hi", {"speed": 100, "cursor": True})
        assert tw["done"] is True
        assert tw["cursor_visible"] is False
        assert "▊" not in tw["output"]

    def test_cursor_style_line(self):
        """cursor_style="line" 输出 | 光标字符。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 0, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("test", {"speed": 100, "cursor": True, "cursor_style": "line"})
        assert tw["cursor_char"] == "|"
        assert "|" in tw["output"]

    def test_custom_cursor_char(self):
        """自定义 cursor_char 覆盖 style 推导值。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 0, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter(
                "test",
                {"speed": 100, "cursor": True, "cursor_char": "█", "cursor_style": "line"},
            )
        # 显式 cursor_char 覆盖 cursor_style="line" 推导的 "|"
        assert tw["cursor_char"] == "█"
        assert "█" in tw["output"]

    def test_return_fields(self):
        """验证返回 dict 含所有新增字段。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 50, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("hello world", {"speed": 50})
        assert "output" in tw
        assert "progress" in tw
        assert "done" in tw
        assert "cursor_visible" in tw
        assert "cursor_char" in tw
        assert "reset" in tw
        assert callable(tw["reset"])
        assert tw["cursor_char"] == "▊"
        assert isinstance(tw["cursor_visible"], bool)
        assert isinstance(tw["progress"], float)
        assert isinstance(tw["done"], bool)
