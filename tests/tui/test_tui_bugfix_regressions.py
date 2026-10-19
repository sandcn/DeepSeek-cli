"""本轮 TUI 缺陷修复的回归测试（2026-10）。

覆盖：插件注册表扩展合并、状态栏思考级别缓存、欢迎卡呼吸色缓存、
工具卡省略行统计、TraceView Ctrl+D 实时选中、模型档案删除后的当前项
同步、事件聚合锁、系统监控停止、剪贴板 UTF-8 边界、启动期输入暂存、
输出历史尾部读取、补全处理器关闭、装配失败回滚。
"""

from __future__ import annotations

import io
import threading
from types import SimpleNamespace

import pytest

from src.tui._completion import _CmplHandler
from src.tui._completion_engine import CompletionEngine, CompletionItem
from src.tui._completion_providers import (
    CompletionProvider,
    active_provider_ids,
    register_provider,
)
from src.tui.app._status_segments import (
    StatusSegment,
    active_segment_ids,
    register_segment,
)
from src.tui.app.chat_view import _WELCOME_STATIC_CACHE, _welcome_rows
from src.tui.app.model_view import _delete_selected
from src.tui.app.toolcard import _tool_result_line_count
from src.tui.app.trace_view import _handle_trace_event
from src.tui.core.style import Style


def _ext_status_segment(ctx):
    from src.tui.ink import StyledRun
    return [StyledRun("EXT", None)]


def _ext_completion_provider(engine, ctx):
    if ctx.last_word == "zzz":
        return [CompletionItem("zzz-ext")]
    return []


def _ext_boom_provider(engine, ctx):
    raise RuntimeError("boom")


# ── 注册表：扩展条目必须参与 active 迭代 ──────────────────

def test_status_segment_extension_is_active_and_resolvable():
    undo = register_segment(StatusSegment(
        "test-extra", 15,
        "tests.tui.test_tui_bugfix_regressions:_ext_status_segment",
    ))
    try:
        assert "test-extra" in active_segment_ids()
        from src.tui.app._status_segments import resolve_segment
        assert resolve_segment("test-extra") is _ext_status_segment
    finally:
        undo()


def test_completion_provider_extension_is_dispatched():
    undo = register_provider(CompletionProvider(
        "test-extra", 1,
        "tests.tui.test_tui_bugfix_regressions:_ext_completion_provider",
    ))
    try:
        assert "test-extra" in active_provider_ids()
        engine = CompletionEngine(commands_source=lambda: [])
        assert [it.text for it in engine.complete("zzz")] == ["zzz-ext"]
    finally:
        undo()


# ── 状态栏：思考级别短 TTL 缓存 ───────────────────────────

def test_reasoning_effort_is_cached(monkeypatch):
    import src.tui.app.status_bar as sb
    import src.config.model_profiles as mp

    calls = []
    monkeypatch.setattr(mp, "current_reasoning_effort", lambda: calls.append(1) or "high")
    sb._reasoning_effort_cache.clear()
    try:
        assert sb._cached_reasoning_effort("m1") == "high"
        assert sb._cached_reasoning_effort("m1") == "high"
        assert len(calls) == 1
        # 模型名变化 → 缓存键不同，立即重新读取
        assert sb._cached_reasoning_effort("m2") == "high"
        assert len(calls) == 2
    finally:
        sb._reasoning_effort_cache.clear()


# ── 欢迎卡：呼吸色必须参与缓存键 ─────────────────────────

def test_welcome_cache_includes_breathing_color(monkeypatch):
    import src.tui.app.chat_view as cv
    import src.tui.app._welcome as welcome

    monkeypatch.setattr(welcome, "environment_info", lambda model: ("x",))
    monkeypatch.setattr(
        welcome, "welcome_card_rows",
        lambda model, width: [[("dot", Style(fg=1, bold=True))]],
    )
    colors = iter([10, 20])
    monkeypatch.setattr(cv, "time_glow", lambda *args: next(colors))
    _WELCOME_STATIC_CACHE[0] = None
    _WELCOME_STATIC_CACHE[1] = None
    model = SimpleNamespace(status=SimpleNamespace(status_active=True))
    try:
        first = _welcome_rows(model, True, 40)
        second = _welcome_rows(model, True, 40)
        assert first[0][0].style.fg == 10
        assert second[0][0].style.fg == 20
    finally:
        _WELCOME_STATIC_CACHE[0] = None
        _WELCOME_STATIC_CACHE[1] = None


# ── 工具卡：头省略解析失败不得清零尾省略 ─────────────────

def test_tool_result_line_count_keeps_bash_omitted():
    block = SimpleNamespace(
        extra={"_bash_omitted_lines": 5, "_head_omitted_lines": "bad"},
        lines=["head", "body", "tail"], closed=False,
    )
    assert _tool_result_line_count(block) == 7


# ── TraceView：Ctrl+D 使用实时选中而非渲染快照 ───────────

def test_trace_ctrl_d_uses_live_selection():
    model = SimpleNamespace(
        trace_open=True, trace_selected=3, trace_pane="ledger",
        trace_search_mode=False, trace_search_side="",
        trace_search_pattern="", trace_search_matches=[], trace_search_idx=-1,
        trace_inspector_scroll=0, trace_inspector_cursor=0,
        trace_tree_collapsed=set(),
    )
    records = [object() for _ in range(6)]
    event = SimpleNamespace(kind="ctrl_key", char="\x04")
    handled = _handle_trace_event(
        model, records, [], [], 0,
        total=6, total_content=0, approx_content_vh=4,
        pane_state=None, event=event,
        raw_records=records, display_rows=records, base_rows=records,
    )
    assert handled is True
    # 3 + 半屏 2 = 5（陈旧快照 0 + 2 = 2 会暴露缺陷）
    assert model.trace_selected == 5


# ── 模型档案删除后同步当前项 ─────────────────────────────

def test_delete_active_profile_updates_active_and_applies(monkeypatch):
    import src.tui.app.model_view as mvmod

    profiles = [
        {"name": "a", "model": "a"},
        {"name": "b", "model": "b"},
    ]
    entries = [{"kind": "profile", "index": 0, "name": "b", "current": True}]
    active_calls = []

    monkeypatch.setattr(mvmod, "load_profiles", lambda: profiles)
    monkeypatch.setattr(mvmod, "save_profiles", lambda value: None)
    monkeypatch.setattr(mvmod, "active_profile_index", lambda: 0)
    monkeypatch.setattr(
        mvmod, "set_active_profile",
        lambda index: active_calls.append(index) or True,
    )
    monkeypatch.setattr(mvmod, "build_model_entries", lambda: entries)
    mv = SimpleNamespace(
        entries=[], selected=0, message="", edit_error="",
        applied=None, applied_seq=0, search_pattern="", search_idx=-1,
        search_matches=[], reset_edit_state=lambda: None,
    )

    _delete_selected(mv, {"kind": "profile", "index": 0, "name": "a"})

    assert active_calls == [0]
    assert mv.applied_seq == 1
    assert mv.applied == entries[0]


# ── 事件聚合：并发更新不得抛 dict changed size ───────────

def test_dispatcher_background_counts_concurrent_updates():
    from src.tui._dispatcher import EventDispatcher

    sink = []
    dispatcher = EventDispatcher(lambda cmd: sink.append(cmd))
    errors = []

    def worker(index: int) -> None:
        try:
            for _ in range(200):
                dispatcher._on_bg_bash_changed(SimpleNamespace(
                    label=f"l{index}", count=1, subagent_count=1,
                ))
        except Exception as exc:  # pragma: no cover - 失败时给出明确异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sink
    assert sink[-1].count == len(dispatcher._bg_bash_counts)
    assert sink[-1].subagent_count == len(dispatcher._bg_subagent_counts)


# ── 系统监控：stop 可终止后台线程 ────────────────────────

def test_system_monitor_stop_sets_stopped_state():
    from src.tui._system_monitor import _SystemMonitor

    monitor = _SystemMonitor()
    monitor._bg_started = True
    monitor._bg_stop.clear()
    monitor._bg_thread = None
    monitor.stop()
    assert monitor._bg_started is False
    assert monitor._bg_stop.is_set()


# ── 剪贴板：UTF-8 截断不切字符 ───────────────────────────

def test_truncate_utf8_keeps_character_boundary():
    from src.tui._screen import _truncate_utf8

    text = "中文"
    raw = text.encode("utf-8")
    assert _truncate_utf8(raw, 4) == "中".encode("utf-8")
    assert _truncate_utf8(raw, 2) == b""
    assert _truncate_utf8(raw, 6) == raw


def test_truncate_utf8_does_not_drop_tail_on_illegal_middle_byte():
    """中间非法 UTF-8 不得把后续有效数据全部丢弃（只回退尾部不完整序列）。"""
    from src.tui._screen import _truncate_utf8

    raw = b"abc\xffdef"
    out = _truncate_utf8(raw, 6)
    assert out == b"abc\xffde"
    assert out.endswith(b"de")


# ── 启动期输入暂存与取回 ─────────────────────────────────

def test_startup_input_stash_is_returned():
    from src.tui._screen import _stash_startup_input, take_startup_input

    take_startup_input()
    _stash_startup_input(b"abc")
    assert take_startup_input() == b"abc"
    assert take_startup_input() == b""


# ── 输出历史：只加载尾部且保持顺序 ───────────────────────

def test_output_history_tail_load(tmp_path, monkeypatch):
    import src.tui._stdout_tracker as st

    hist = tmp_path / "output_history"
    hist.write_text("\n".join(f"l{i}" for i in range(50)) + "\n", encoding="utf-8")
    monkeypatch.setattr(st, "OUTPUT_HISTORY_FILE", hist)
    monkeypatch.setattr(st._StdoutLineTracker, "_MAX_LINES", 5)
    tracker = st._StdoutLineTracker(io.StringIO())
    tracker._stop_flush_timer()
    try:
        assert list(tracker._ring) == [f"l{i}" for i in range(45, 50)]
    finally:
        tracker.close()


def test_output_history_keeps_huge_single_line(tmp_path, monkeypatch):
    """超过 4MB 且无换行的历史文件：保留窗口内容，不能显示为空。"""
    import src.tui._stdout_tracker as st

    hist = tmp_path / "output_history"
    hist.write_bytes(b"x" * (4 * 1024 * 1024 + 1024))
    monkeypatch.setattr(st, "OUTPUT_HISTORY_FILE", hist)
    tracker = st._StdoutLineTracker(io.StringIO())
    tracker._stop_flush_timer()
    try:
        tracker._ring.clear()
        tracker._load_output_history()
        assert len(tracker._ring) == 1
        line = tracker._ring[0]
        assert line
        assert line.endswith("x")
        assert len(line) <= 4 * 1024 * 1024
    finally:
        tracker.close()


# ── 补全处理器：close 注销监听并关闭引擎（幂等） ─────────

def test_completion_handler_close_is_idempotent():
    class FakeEngine:
        def __init__(self):
            self.closed = 0
            self.undone = 0

        def add_listener(self, listener):
            return lambda: setattr(self, "undone", self.undone + 1)

        def close(self):
            self.closed += 1

    engine = FakeEngine()
    handler = _CmplHandler(SimpleNamespace(), engine, lambda: None)
    handler.close()
    handler.close()
    assert engine.closed == 1
    assert engine.undone == 1


# ── 装配失败：还原终端模式、注销 SIGWINCH、关闭资源 ─────

def test_config_view_clamps_selected_after_render():
    from tests.tui.test_config_view import _render_component, _sample_entries
    from src.tui.app.config_view import ConfigView
    from src.tui.app.model import AppModel, ConfigViewState

    entries = _sample_entries()
    model = AppModel()
    model.config_view = ConfigViewState(
        visible=True, seq=1, entries=entries, selected=999,
    )
    fiber, _ = _render_component(ConfigView, model, width=80)
    # 手动 fiber 渲染不跑提交期 effect——显式执行以验证「渲染只收集、
    # 提交期落盘」的修复路径。
    from src.tui.ink.fiber import EffectHook
    for hook in fiber.hooks:
        if isinstance(hook, EffectHook) and callable(hook.create):
            hook.create()
    assert model.config_view.selected == len(entries) - 1


def test_user_select_key_uses_sequence():
    from src.tui.app.view_registry import _user_select_key

    key = _user_select_key("user_select")
    assert key(SimpleNamespace(user_select=SimpleNamespace(seq=1))) == "us-1"
    assert key(SimpleNamespace(user_select=SimpleNamespace(seq=2))) == "us-2"


def test_appmodel_unknown_field_warns_once(caplog):
    from src.tui.app.model import AppModel

    model = AppModel()
    with caplog.at_level("WARNING"):
        for _ in range(3):
            with pytest.raises(AttributeError):
                getattr(model, "no_such_field")
    assert caplog.text.count("no_such_field") == 1


def test_sigwinch_registration_skips_when_signal_missing(monkeypatch):
    """原生 Windows 无 ``signal.SIGWINCH``：注册回调不得抛 AttributeError。

    修复前 ``register_sigwinch_callback`` 直接访问 ``signal.SIGWINCH``，
    cmd 下启动即 ``❌ 致命错误: module 'signal' has no attribute 'SIGWINCH'``；
    无该信号时须静默降级（尺寸变化由渲染帧主动轮询兜底）。
    """
    from src.tui import _screen

    monkeypatch.delattr(_screen.signal, "SIGWINCH", raising=False)
    monkeypatch.setattr(_screen, "_sigwinch_registered", False)
    old = list(_screen._sigwinch_callbacks)
    token = object()
    try:
        _screen.register_sigwinch_callback(lambda w, h: None, token=token)
        assert any(item[1] is token for item in _screen._sigwinch_callbacks)
        assert _screen._sigwinch_registered is True
    finally:
        _screen._sigwinch_callbacks[:] = old


def test_sigwinch_token_registration_roundtrip():
    from src.tui import _screen

    old = list(_screen._sigwinch_callbacks)
    token = object()
    calls = []
    try:
        _screen.register_sigwinch_callback(
            lambda w, h: calls.append((w, h)), token=token,
        )
        assert any(item[1] is token for item in _screen._sigwinch_callbacks)
        _screen.unregister_sigwinch_callback(token)
        assert not any(item[1] is token for item in _screen._sigwinch_callbacks)
    finally:
        _screen._sigwinch_callbacks[:] = old


def test_consumer_stop_closes_completion_even_if_not_started():
    import threading
    from src.tui._consumer import ChatUIConsumer

    class FakeLifecycle:
        is_started = False
        _state_lock = threading.RLock()

    class FakeCmpl:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    consumer = ChatUIConsumer.__new__(ChatUIConsumer)
    consumer._lifecycle = FakeLifecycle()
    consumer._cmpl_handler = FakeCmpl()
    consumer.stop()
    assert consumer._cmpl_handler.closed == 1


def test_commit_form_syncs_current_profile(monkeypatch):
    import src.tui.app.model_view as mvmod

    entries = [{"kind": "profile", "index": 0, "name": "a", "current": True}]
    monkeypatch.setattr(mvmod, "normalize_profile", lambda values: dict(values))
    monkeypatch.setattr(mvmod, "validate_profile", lambda profile, profiles, index=None: "")
    monkeypatch.setattr(mvmod, "load_profiles", lambda: [{"name": "a", "model": "a"}])
    monkeypatch.setattr(mvmod, "save_profiles", lambda profiles: None)
    monkeypatch.setattr(mvmod, "active_profile_index", lambda: 0)
    monkeypatch.setattr(mvmod, "set_active_profile", lambda index: True)
    monkeypatch.setattr(mvmod, "build_model_entries", lambda: entries)
    mv = SimpleNamespace(
        form_values={"name": "a", "model": "a"}, form_is_new=False,
        form_index=0, entries=[], selected=0, message="", edit_error="",
        applied=None, applied_seq=0, search_pattern="", search_idx=-1,
        search_matches=[], reset_edit_state=lambda: None,
    )
    mvmod._commit_form(mv)
    assert mv.applied_seq == 1
    assert mv.applied == entries[0]


def test_completion_provider_exception_is_isolated(monkeypatch):
    from src.tui import _completion_providers as cp

    undo = cp.register_provider(CompletionProvider(
        "boom", 1, __name__ + ":_ext_boom_provider",
    ))
    try:
        engine = CompletionEngine(commands_source=lambda: [])
        assert engine.complete("zzz") == []
    finally:
        undo()


def test_process_events_respects_frame_budget(monkeypatch):
    from src.tui import _input_dispatcher as idisp

    monkeypatch.setattr(idisp.time, "monotonic", lambda: 0.0)
    dispatcher = idisp.InputDispatcher.__new__(idisp.InputDispatcher)
    calls = []

    def _read():
        calls.append(1)
        return True

    dispatcher.read_stdin_once = _read
    dispatcher.process_events()
    assert len(calls) == dispatcher._MAX_EVENTS_PER_FRAME


def test_assembly_failure_cleans_session_and_tracker(monkeypatch):
    from src.tui import _assembly

    closed = {"tracker": 0, "terminal": 0, "sigwinch": []}

    class Tracker:
        def close(self):
            closed["tracker"] += 1

    class Session:
        def exit_terminal_modes(self):
            closed["terminal"] += 1

    session = Session()
    monkeypatch.setattr(_assembly._assembly_steps, "create_infrastructure", lambda: Tracker())
    monkeypatch.setattr(
        _assembly._assembly_steps, "create_shared",
        lambda: (SimpleNamespace(), SimpleNamespace()),
    )
    monkeypatch.setattr(_assembly._assembly_steps, "create_chat_domain", lambda: object())
    monkeypatch.setattr(
        _assembly._assembly_steps, "create_framework",
        lambda model, config, tracker, input_instance: (
            session, SimpleNamespace(), SimpleNamespace(),
        ),
    )

    def _fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_assembly._assembly_steps, "create_chat_domain_assembly", _fail)
    import src.tui._screen as screen
    monkeypatch.setattr(
        screen, "unregister_sigwinch_callback",
        lambda token: closed["sigwinch"].append(token),
    )

    with pytest.raises(RuntimeError):
        _assembly.TuiAssembly.assemble()

    assert closed["tracker"] == 1
    assert closed["terminal"] == 1
    assert closed["sigwinch"] == [session]
