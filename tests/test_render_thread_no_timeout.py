"""渲染线程「单帧耗时无上界」约束回归测试（2026-08-19）。

★ 约束：渲染线程为 10Hz 循环，**单帧执行没有耗时上限**——大量上文重放/
超长 markdown 渲染一帧可达数秒以上。所有「等待渲染线程」的逻辑不得把
慢帧误判为超时/挂起：

  - 帧号推进（``_frame_seq``）或帧执行中（``_frame_active``）= 有进展，
    续期软超时继续等（每帧执行多久都可以）；
  - 硬上限仅防**真挂起**（帧内永久卡死），触达时降级 + warning。

本文件覆盖：
  1. ``_render_frame`` 帧执行标记（``_frame_active``）置位/复位（正常/
     早退/异常路径）；
  2. ``flush_input_router``：超长单帧期间（帧号未推进、``_frame_active``
     置位）不被软超时误降级，帧完成两帧后返回 True；帧内真挂起时硬上限
     降级 False；
  3. ``_wait_render_flush``：渲染线程停止立即返回；超长帧期间（软超时被
     帧内续期）持续等待至队列排空才返回；帧内真挂起时硬上限返回（不死等）；
  4. ``_join_render_thread``：渲染线程执行超长帧（>原固定 2s 语义）时
     持续等待至线程退出返回 True；真挂起时硬上限放弃返回 False；
  5. ``stop()`` / ``suspend()`` 集成：超长帧期间自适应等待线程退出后才
     执行渲染器清理（不再固定 2s 强制清理与写 stream 并发 → 撕裂）。
"""

from __future__ import annotations

import queue
import threading
import time

import pytest

import src.tui.ink.session as session_mod
from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.session import InkSession


# ── 测试桩 ──────────────────────────────────────────────

class _FakeThread:
    """可控生命周期的假渲染线程。

    alive_for=None 表示恒存活（模拟真挂起）；否则 alive_for 秒后退出
    （模拟超长单帧执行完成后线程自然退出）。
    """

    def __init__(self, alive_for: float | None):
        self._deadline = None if alive_for is None else time.monotonic() + alive_for

    def is_alive(self) -> bool:
        return self._deadline is None or time.monotonic() < self._deadline

    def join(self, timeout=None):
        time.sleep(min(timeout if timeout is not None else 0.05, 0.05))


class _RouterFlushStub:
    """flush_input_router 桩（借用真实方法，含 ``_frame_active`` 信号）。"""

    flush_input_router = InkSession.flush_input_router
    _advance_frame_seq = InkSession._advance_frame_seq

    def __init__(self):
        self._render_running = True
        self._frame_active = False
        self._frame_seq = 0
        self._frame_seq_lock = threading.Lock()
        self._frame_flush_waiters = []
        self._bottom_redraw_requested = threading.Event()
        self._dirty = False
        self._cmd_event = threading.Event()

    def request_bottom_redraw(self):
        pass


class _WaitFlushStub:
    """_wait_render_flush 桩（借用真实方法）。"""

    _wait_render_flush = InkSession._wait_render_flush

    def __init__(self):
        self._cmd_queue = queue.Queue()
        self._dirty = False
        self._render_running = True
        self._render_thread = None
        self._frame_active = False
        self._frame_seq = 0
        self._frame_seq_lock = threading.Lock()


class _JoinStub:
    """_join_render_thread 桩（借用真实方法）。"""

    _join_render_thread = InkSession._join_render_thread

    def __init__(self):
        self._render_thread = None
        self._render_version = 0


class _LifecycleStub(_JoinStub):
    """stop()/suspend() 桩（借用真实方法，记录渲染器清理调用）。"""

    stop = InkSession.stop
    suspend = InkSession.suspend
    flush = InkSession.flush
    _join_render_thread = InkSession._join_render_thread

    def __init__(self):
        super().__init__()
        self._render_running = False
        self._cmd_queue = queue.Queue()
        self._cmd_event = threading.Event()
        self._consecutive_full = 0
        self.renderer_suspended = 0
        self.drain_calls = []

        outer = self

        class _Renderer:
            def suspend(self):
                outer.renderer_suspended += 1

            def goto_bottom(self, row):
                pass

        self._ink_renderer = _Renderer()

    def _drain_queue_safe(self, keep_content=False):
        self.drain_calls.append(keep_content)
        return 0


class _FrameStub:
    """_render_frame 帧执行标记桩（借用 mixin 方法）。"""

    _render_frame = _SessionFrameMixin._render_frame
    _render_frame_impl = _SessionFrameMixin._render_frame_impl

    def __init__(self, build_tree=None):
        self._frame_active = False
        self._build_tree = build_tree


# ── 1. 帧执行标记（_frame_active） ─────────────────────

class TestFrameActive:
    def test_frame_active_set_during_render_and_cleared_after(self):
        """渲染帧执行期间置位、完成后复位（包装器语义，impl 以桩覆盖）。"""
        stub = _FrameStub()
        observed = []

        def impl():
            observed.append("active" if stub._frame_active else "inactive")

        stub._render_frame_impl = impl
        stub._render_frame()
        assert observed == ["active"]
        assert stub._frame_active is False

    def test_frame_active_cleared_on_early_return(self):
        """build_tree=None 早退路径同样复位。"""
        stub = _FrameStub(build_tree=None)
        stub._render_frame()
        assert stub._frame_active is False

    def test_frame_active_cleared_on_exception(self):
        """渲染帧异常传播，但标记必须复位（不泄漏给等待方）。"""
        stub = _FrameStub()

        def boom():
            raise RuntimeError("render crash")

        stub._render_frame_impl = boom
        with pytest.raises(RuntimeError):
            stub._render_frame()
        assert stub._frame_active is False


# ── 2. flush_input_router：慢帧不误降级 ─────────────────

class TestFlushInputRouter:
    def test_returns_true_via_sync_render_when_thread_not_running(self):
        """render 线程未运行分支：走 request_bottom_redraw 同步渲染 → True。"""
        stub = _RouterFlushStub()
        stub.redraw_calls = []
        stub.request_bottom_redraw = lambda: stub.redraw_calls.append(True)
        stub._render_running = False
        assert stub.flush_input_router(timeout=1.0) is True
        assert stub.redraw_calls == [True]

    def test_waits_through_long_frame_without_premature_timeout(self):
        """超长单帧（帧号未推进、帧执行中）不被软超时误降级。

        场景：timeout=0.3（软超时），渲染线程执行 0.5s 的超长帧后才推两帧
        ——旧逻辑（仅帧号推进续期）0.3s 即误判超时返回 False；新逻辑
        ``_frame_active`` 视作有进展续期 → 帧完成后返回 True。
        """
        stub = _RouterFlushStub()

        def long_frame():
            stub._frame_active = True
            time.sleep(0.5)          # 超长单帧（> 软超时 0.3s）
            stub._frame_active = False
            stub._advance_frame_seq()
            stub._advance_frame_seq()

        t = threading.Thread(target=long_frame)
        t.start()
        try:
            ok = stub.flush_input_router(timeout=0.3)
        finally:
            t.join(timeout=3.0)
        assert ok is True

    def test_hung_frame_hits_hard_ceiling(self, monkeypatch):
        """帧内真挂起（_frame_active 恒真、帧号零推进）→ 硬上限降级 False。"""
        monkeypatch.setattr(session_mod, "_ROUTER_FLUSH_HARD_CEILING", 0.4)
        stub = _RouterFlushStub()
        stub._frame_active = True    # 恒真：帧内卡死
        start = time.monotonic()
        ok = stub.flush_input_router(timeout=0.2)
        elapsed = time.monotonic() - start
        assert ok is False
        # 软超时 0.2 被帧内信号持续续期 → 由硬上限 0.4 终结（不死等）
        assert 0.15 <= elapsed < 2.0
        # waiter 已清理（列表不残留）
        assert stub._frame_flush_waiters == []


# ── 3. _wait_render_flush：自适应等待 ───────────────────

class TestWaitRenderFlush:
    async def test_returns_immediately_when_thread_stopped(self):
        """渲染线程停止 → 立即返回（不死等队列）。"""
        stub = _WaitFlushStub()
        stub._render_running = False
        stub._cmd_queue.put("pending")   # 队列非空也不等待
        start = time.monotonic()
        await stub._wait_render_flush()
        assert time.monotonic() - start < 0.5

    async def test_waits_through_long_frame_until_queue_drained(self, monkeypatch):
        """超长帧期间持续等待（软超时被帧内续期），队列排空后才返回。

        场景：soft=0.2（monkeypatch），帧执行 0.4s——若未按帧内信号续期，
        0.2s 即提前返回（elapsed≈0.2）；新逻辑等到 0.4s 排空才完成。
        """
        monkeypatch.setattr(session_mod, "_RENDER_FLUSH_SOFT_TIMEOUT", 0.2)
        monkeypatch.setattr(session_mod, "_RENDER_FLUSH_HARD_TIMEOUT", 5.0)
        stub = _WaitFlushStub()
        stub._render_thread = _FakeThread(alive_for=5.0)
        stub._cmd_queue.put("cmd")
        stub._frame_active = True

        def long_frame_finish():
            time.sleep(0.4)             # 超长单帧（> soft 0.2s）
            stub._cmd_queue.get_nowait()
            stub._cmd_queue.task_done()
            stub._frame_active = False
            stub._dirty = False

        t = threading.Thread(target=long_frame_finish)
        t.start()
        try:
            start = time.monotonic()
            await stub._wait_render_flush()
            elapsed = time.monotonic() - start
        finally:
            t.join(timeout=3.0)
        # 等到了队列排空（≥ 帧耗时），而非 0.2s 软超时提前退出
        assert elapsed >= 0.3

    async def test_waits_for_frame_completion_when_queue_drained_mid_frame(self, monkeypatch):
        """关键窗口：队列空 + dirty 已清 + 帧执行中 → 等帧完成才返回。

        渲染线程时序为 DRAIN 取空队列 → ``_should_render`` 清 ``_dirty`` →
        ``_render_frame`` 执行——本窗口内仅判「队列空 + 无脏」会立即返回
        （假阳性：帧尚未写入终端）。``_frame_active`` 纳入完成条件后必须
        等帧完成（0.3s 后复位）才返回。
        """
        monkeypatch.setattr(session_mod, "_RENDER_FLUSH_SOFT_TIMEOUT", 5.0)
        monkeypatch.setattr(session_mod, "_RENDER_FLUSH_HARD_TIMEOUT", 5.0)
        stub = _WaitFlushStub()
        stub._render_thread = _FakeThread(alive_for=5.0)
        stub._frame_active = True   # 队列空 + dirty False，但帧执行中

        def finish_frame():
            time.sleep(0.3)
            stub._frame_active = False

        t = threading.Thread(target=finish_frame)
        t.start()
        try:
            start = time.monotonic()
            await stub._wait_render_flush()
            elapsed = time.monotonic() - start
        finally:
            t.join(timeout=3.0)
        # 等到了帧完成（≥ 帧耗时 0.3s），而非窗口内立即返回
        assert elapsed >= 0.2

    async def test_hard_timeout_on_hung_frame(self, monkeypatch):
        """帧内真挂起（队列恒非空 + 帧执行中恒真）→ 硬上限返回（不死等）。"""
        monkeypatch.setattr(session_mod, "_RENDER_FLUSH_SOFT_TIMEOUT", 5.0)
        monkeypatch.setattr(session_mod, "_RENDER_FLUSH_HARD_TIMEOUT", 0.3)
        stub = _WaitFlushStub()
        stub._render_thread = _FakeThread(alive_for=None)
        stub._cmd_queue.put("cmd")
        stub._frame_active = True
        start = time.monotonic()
        await stub._wait_render_flush()
        elapsed = time.monotonic() - start
        assert 0.2 <= elapsed < 2.0


# ── 4. _join_render_thread：自适应 join ─────────────────

class TestJoinRenderThread:
    def test_waits_for_thread_executing_long_frame(self):
        """渲染线程执行超长帧后自然退出 → 等待至退出返回 True。

        场景：线程 0.6s 后退出（超过原固定 2s 语义的时间尺度按比例缩小，
        关键在等待时长 > 单段 join 0.2s × 多段且不提前放弃）。
        """
        stub = _JoinStub()
        stub._render_thread = _FakeThread(alive_for=0.6)
        start = time.monotonic()
        joined = stub._join_render_thread(hard_timeout=3.0)
        elapsed = time.monotonic() - start
        assert joined is True
        assert elapsed >= 0.5          # 等满线程生命周期（未提前放弃）

    def test_returns_true_immediately_when_no_thread(self):
        stub = _JoinStub()
        assert stub._join_render_thread(hard_timeout=1.0) is True

    def test_hard_timeout_on_hung_thread(self, monkeypatch):
        """线程真挂起（恒存活）→ 硬上限放弃返回 False。"""
        monkeypatch.setattr(session_mod, "_JOIN_RENDER_HARD_TIMEOUT", 0.3)
        stub = _JoinStub()
        stub._render_thread = _FakeThread(alive_for=None)
        start = time.monotonic()
        joined = stub._join_render_thread()
        elapsed = time.monotonic() - start
        assert joined is False
        assert 0.2 <= elapsed < 2.0


# ── 5. stop()/suspend() 集成：等慢帧后再清理 ─────────────

class TestLifecycleWaitsSlowFrame:
    def test_stop_waits_for_long_frame_then_cleans(self):
        """stop()：渲染线程超长帧（0.5s）执行中 → 自适应等待至退出 →
        才执行渲染器清理（suspend 被调用，而非固定 2s 误判强制跳过）。"""
        stub = _LifecycleStub()
        stub._render_running = True
        stub._render_thread = _FakeThread(alive_for=0.5)
        start = time.monotonic()
        stub.stop()
        elapsed = time.monotonic() - start
        # 线程退出后才清理（等满其生命周期）
        assert elapsed >= 0.4
        assert stub.renderer_suspended == 1
        assert stub._render_running is False
        # stop 排空队列：全清空（keep_content=False）
        assert stub.drain_calls == [False]

    def test_suspend_waits_for_long_frame_then_cleans(self):
        """suspend()：超长帧执行中 → 自适应等待至退出 → 渲染器清理。"""
        stub = _LifecycleStub()
        stub._render_running = True
        stub._render_thread = _FakeThread(alive_for=0.5)
        start = time.monotonic()
        stub.suspend()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4
        assert stub.renderer_suspended == 1
        # suspend 排空队列：保留内容命令（keep_content=True）
        assert stub.drain_calls == [True]


# ── 6. 系统监控防御（review 修复回归） ──────────────────

class TestSystemStatsDefence:
    def test_safe_int_handles_infinity(self):
        """``int(float('inf'))`` 抛 OverflowError——_safe_int 必须回退默认值。"""
        from src.tui.ink._session_frame_mixin import _safe_int
        assert _safe_int(float("inf")) == 0
        assert _safe_int(float("-inf"), 5) == 5
        assert _safe_int(float("nan")) == 0

    def test_update_system_stats_tolerates_status_without_cpu_mem(self):
        """status 存在但缺 cpu/mem 字段 → 跳过更新，不抛 AttributeError。"""
        from types import SimpleNamespace
        from src.tui.ink._session_frame_mixin import _SessionFrameMixin

        class _Stub(_SessionFrameMixin):
            _update_system_stats = _SessionFrameMixin._update_system_stats

            def __init__(self):
                self._last_sys_stats_time = 0.0
                self._sys_stats_interval = 2.0
                self._model = SimpleNamespace(status=SimpleNamespace())  # 无 cpu/mem
                self._system_monitor = SimpleNamespace(
                    get_cpu_and_mem=lambda: (10, 20)
                )
                self._dirty = False

        stub = _Stub()
        stub._update_system_stats()   # 不抛异常即通过
        assert stub._dirty is False
