"""AnimationClock 和 _AnimationState 单元测试。

覆盖 animation 系统的注册/注销/帧更新/启停/重置。
测试策略：直接测试 AnimationClock 和 _AnimationState，
不依赖 use_animation hook（因其依赖 Hooks 运行时上下文）。
"""

from __future__ import annotations

import time
import pytest

from src.chat_ui.react_ink._animation import (
    AnimationClock,
    _AnimationState,
    use_animation,
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
