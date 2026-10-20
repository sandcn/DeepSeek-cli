"""P0~P2 架构修复回归测试（渲染调度 / 宿主适配 / 命令缓冲 / 效果队列 /
视图状态所有权 / 历史窗口 / 缓存注册表 / 错误策略 / 输入路由 / 输入泵）。

每个用例锁定一个架构契约，防止后续演进回退到旧实现。
"""

from __future__ import annotations

import time

from src.tui.app.history_store import HistoryBuffer
from src.tui.app.model import AppModel
from src.tui.app.views import AppViews
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink.cache_registry import CacheRegistry
from src.tui.ink.command_buffer import CommandBuffer
from src.tui.ink.effect_scheduler import EffectScheduler
from src.tui.ink.error_policy import RenderErrorPolicy
from src.tui.ink.host import DefaultHostAdapter, HostAdapter, NullHostAdapter
from src.tui.ink.input_pump import InputPump
from src.tui.ink.input_router import InputRouter
from src.tui.ink.scheduler import RenderScheduler


# ── P0-1 渲染调度 ─────────────────────────────────


class TestRenderScheduler:
    def test_stop_unblocks_wait(self):
        sched = RenderScheduler(max_fps=60)
        sched.stop()
        assert sched.wait() is False

    def test_request_wakes_and_is_consumed(self):
        sched = RenderScheduler(max_fps=60)
        sched.on_frame()
        sched.request("test")
        assert sched.wait() is True
        assert not sched.has_pending()

    def test_max_fps_throttles(self):
        sched = RenderScheduler(max_fps=1000)
        sched.on_frame()
        start = time.monotonic()
        sched.request()
        sched.wait()
        assert time.monotonic() - start >= 0.0005


# ── P0-2 视图状态所有权 ───────────────────────────


class TestAppViewsOwnership:
    def test_trace_fields_live_in_view_state(self):
        model = AppModel()
        model.trace_selected = 7
        assert model.views.trace.trace_selected == 7
        assert model.trace_selected == 7
        assert "trace_selected" not in model.__dict__

    def test_navigation_fields_live_in_view_state(self):
        model = AppModel()
        model.fullscreen = "trace"
        model.bottom_view = "user_select"
        assert model.views.navigation.fullscreen == "trace"
        assert model.views.navigation.bottom_view == "user_select"
        assert model.trace_open is True
        assert "fullscreen" not in model.__dict__

    def test_view_lifecycle_owner(self):
        views = AppViews()
        views.open_fullscreen("config")
        assert views.navigation.fullscreen == "config"
        views.close_fullscreen()
        assert views.navigation.fullscreen == ""
        views.trace.trace_selected = 3
        assert views.reset_view("trace") is True
        assert views.trace.trace_selected == -1
        assert views.reset_view("unknown") is False

    def test_view_objects_are_owned(self):
        model = AppModel()
        assert model.config_view is model.views.config_view
        assert "config_view" not in model.__dict__


# ── P0-3 宿主适配 ─────────────────────────────────


class TestHostAdapter:
    def test_null_adapter_is_noop(self):
        adapter: HostAdapter = NullHostAdapter()
        model = _SimpleModel()
        adapter.prepare_frame(model, 100, 20)
        assert adapter.content_line_count(model) == 0
        assert adapter.coalesce_commands([1]) == [1]

    def test_default_adapter_reflows_before_width_assignment(self):
        from src.tui.app.model import AppModel

        model = AppModel()
        model.set_history_window(0)
        model.append_committed("user", [])
        adapter = DefaultHostAdapter()
        # 宽度从 80 → 120：reflow 必须看到旧宽度，不能在赋值后跳过。
        adapter.prepare_frame(model, 120, 24)
        assert model.width == 120

    def test_default_adapter_calls_optional_hooks(self):
        calls = []

        class _M(_SimpleModel):
            def reflow_committed(self, width):
                calls.append(("reflow", width, self.width))

            def refresh_running_tool_titles(self):
                calls.append(("refresh",))

        m = _M()
        m.width = 80
        DefaultHostAdapter().prepare_frame(m, 100, 24)
        assert ("refresh",) in calls
        assert calls[0] == ("reflow", 100, 80)


# ── P1-1 输入泵 ───────────────────────────────────


class TestInputPump:
    def test_pump_reads_bytes_and_wakes(self):
        class _FakeInput:
            def __init__(self):
                self.fed = []
                self.reads = 0

            def poll_raw(self, timeout=0.05):
                self.reads += 1
                if self.reads == 1:
                    return b"abc"
                time.sleep(0.005)
                return b""

            def feed_raw(self, data):
                self.fed.append(data)

        inp = _FakeInput()
        wakes = []
        pump = InputPump(inp, lambda: wakes.append(1), poll_interval=0.005)
        pump.start()
        try:
            deadline = time.monotonic() + 0.5
            while not wakes and time.monotonic() < deadline:
                time.sleep(0.005)
        finally:
            pump.stop()
        assert inp.fed == [b"abc"]
        assert wakes


# ── P1-2 命令缓冲 ─────────────────────────────────


class TestCommandBuffer:
    def test_priority_then_insert_order(self):
        buf = CommandBuffer(100)
        buf.put("low", 10)
        buf.put("high1", 0)
        buf.put("high2", 0)
        assert [buf.get_nowait()[2] for _ in range(3)] == ["high1", "high2", "low"]

    def test_replace_same_kind_keeps_position(self):
        buf = CommandBuffer(100)
        buf.put("a", 5)
        buf.put("old", 5)
        buf.put("c", 5)
        assert buf.replace_same_kind(7, "new", lambda c: 7 if c in ("old", "new") else 0)
        items = sorted(buf.queue, key=lambda it: it[1])
        assert [it[2] for it in items] == ["a", "new", "c"]
        assert buf.unfinished_tasks == 3

    def test_evict_low_and_drain(self):
        buf = CommandBuffer(2)
        buf.put("low", 10)
        assert buf.put("x", 0) is True
        assert buf.put("y", 0) is False
        assert buf.evict_low(10, lambda c: 0) == "low"
        assert buf.put("y", 0) is True
        dropped, kept = buf.drain({99}, lambda c: 0)
        assert dropped == 2 and kept == 0
        assert buf.unfinished_tasks == 0

    def test_join_returns_after_task_done(self):
        buf = CommandBuffer(10)
        buf.put("a", 0)
        buf.get_nowait()
        buf.task_done()
        assert buf.join(timeout=0.1) is True


# ── P1-4 效果队列 ─────────────────────────────────


class TestEffectScheduler:
    def test_defer_and_flush(self):
        sched = EffectScheduler()
        ran = []
        sched.enqueue("f1")
        sched.enqueue("f1")  # 去重
        assert len(sched) == 1
        assert sched.flush(lambda f: ran.append(f)) == 1
        assert ran == ["f1"]
        assert len(sched) == 0


# ── P1-5 输入路由 ─────────────────────────────────


class TestInputRouter:
    def test_route_then_modal_then_fallback(self):
        calls = []
        router = InputRouter(lambda ev: ev == "handled")
        router.set_fallback(lambda ev: calls.append(ev))
        assert router.dispatch("handled") is True
        router.push_modal("m")
        assert router.dispatch("blocked") is True
        assert calls == []          # 模态吞掉，不进兜底
        router.pop_modal("m")
        assert router.dispatch("x") is True
        assert calls == ["x"]

    def test_set_modals_reconciles_active_tokens(self):
        router = InputRouter(lambda ev: False)
        router.set_modals(["a", None, "b"])
        assert router.modal_depth == 2
        assert router.route("anything") is True
        router.set_modals([])
        assert router.route("anything") is False

    def test_focus_tab_handled_by_router(self):
        from types import SimpleNamespace

        calls = []
        router = InputRouter(
            lambda ev: False,
            focus_next=lambda: calls.append("next"),
            focus_previous=lambda: calls.append("prev"),
        )
        router.set_focus_enabled(True)
        assert router.route(SimpleNamespace(kind="tab", modifier=0)) is True
        assert router.route(SimpleNamespace(kind="tab", modifier=2)) is True
        assert calls == ["next", "prev"]


# ── P1-1 单写者 ───────────────────────────────────


class TestSingleWriter:
    def test_post_mutation_runs_on_render_thread(self):
        import io
        import threading

        from src.tui._config import TuiConfig
        from src.tui._screen import TerminalWidthCache
        from src.tui.ink import TEXT, h
        from src.tui.ink.session import InkSession

        model = _SimpleModel()
        session = InkSession(
            model=model,
            build_tree=lambda m, w: h(TEXT, {"children": "x"}),
            stream=io.StringIO(),
            width_cache=TerminalWidthCache(),
            config=TuiConfig.defaults(),
        )
        session.start()
        try:
            seen = []

            def _mutate():
                seen.append(threading.current_thread() is session._render_thread)
                model.input_text = "from-render"

            session.post_mutation(_mutate)
            assert model.input_text == "from-render"
            assert seen == [True]
            # 会话对象已组合 RenderLoop（P2-2）
            assert session._render_loop is not None
        finally:
            session.stop()

    def test_ink_bridge_routes_mutations_through_session(self):
        from src.tui._ink_bridge import InkBridge

        class _Session:
            def __init__(self):
                self.calls = 0

            def post_mutation(self, fn, wait=True, timeout=2.0):
                self.calls += 1
                return fn()

            def request_bottom_redraw(self):
                pass

        model = AppModel()
        session = _Session()
        bridge = InkBridge(model, session)
        bridge.set_model_name("m1")
        bridge.set_input_state("abc", 3)
        assert session.calls == 2
        assert model.status.model_name == "m1"
        assert model.input_text == "abc"


# ── P2-3 / P2-4 ───────────────────────────────────


class TestCacheAndErrorPolicy:
    def test_cache_registry_invalidates_named(self):
        reg = CacheRegistry()
        hits = []
        reg.register("a", lambda: hits.append("a"))
        reg.register("b", lambda: hits.append("b"))
        assert reg.invalidate("resize", names=["a"]) == 1
        assert hits == ["a"]
        assert reg.epoch == 1

    def test_error_policy_strict_and_degrade(self):
        strict = RenderErrorPolicy(strict=True)
        assert strict.on_frame_error(RuntimeError("x"), 1) == "raise"
        policy = RenderErrorPolicy(strict=False, frame_failure_limit=2)
        assert policy.on_frame_error(RuntimeError("x"), 1) == "retry"
        assert policy.on_frame_error(RuntimeError("x"), 2) == "degrade"
        assert policy.take_announcement()
        assert policy.take_announcement() is None


# ── P2-5 历史窗口 ─────────────────────────────────


class TestHistoryWindow:
    def test_trim_keeps_tail_and_calls_back(self):
        dropped = []
        buf = HistoryBuffer([1, 2, 3, 4], max_lines=2, on_trim=dropped.append)
        assert list(buf) == [3, 4]
        assert dropped == [2]
        buf.append(5)
        assert list(buf) == [4, 5]
        assert buf.trimmed_total == 3

    def test_model_history_window(self):
        model = AppModel()
        model.set_history_window(3)
        for i in range(6):
            model.committed_lines.append(i)
        assert list(model.committed_lines) == [3, 4, 5]
        assert model._history_trimmed_total == 3


# ── P1-3 历史段 fast path ─────────────────────────


class TestFrameHistorySegment:
    def test_history_unchanged_and_diff_skip(self):
        from src.tui.ink.diff import first_diff_line
        from src.tui.ink.frame_targets import FrameTargets
        from src.tui.ink.output import Frame, Line

        history = [Line() for _ in range(3)]
        live1 = [Line()]
        live2 = [Line()]
        live2[0].append("changed")
        f1 = Frame(history + live1, history_ref=history, history_len=3)
        f2 = Frame(history + live2, history_ref=history, history_len=3)
        assert f1.history_unchanged(f2)
        assert f1.history_lines == history
        # 历史段跳过 → 首差异在 live 区（第 4 行）
        assert first_diff_line(f1, f2) == 3
        plan = FrameTargets().plan(f1, f2)
        assert plan.history_changed is False
        assert plan.live_changed is True
        assert plan.first_diff == 3
        assert plan.history_end == 3

    def test_history_change_forces_full_scan(self):
        from src.tui.ink.frame_targets import FrameTargets
        from src.tui.ink.output import Frame, Line

        h1 = [Line()]
        h2 = [Line()]
        h1[0].append("old")
        h2[0].append("new")
        f1 = Frame(h1 + [Line()], history_ref=h1, history_len=1)
        f2 = Frame(h2 + [Line()], history_ref=h2, history_len=1)
        plan = FrameTargets().plan(f1, f2)
        assert plan.history_changed is True
        assert plan.first_diff == 0
