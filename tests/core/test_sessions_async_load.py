"""会话加载异步化 — _SessionLoadJob / /load 后台读取 / /sessions 视图异步初始化。"""

from __future__ import annotations

import types

from src.core.commands import _data_cmd as dc


class _Recorder:
    def __init__(self):
        self.calls = []

    def write(self, text, level="info", source="core"):
        self.calls.append(text)


class _FakeP:
    """持久化端口桩（可注入读取延迟/异常）。"""

    def __init__(self, data=None, delay=0.0, error=None):
        self._data = data
        self._delay = delay
        self._error = error
        self.thread_names = []
        self.saved = []

    def list_sessions(self):
        return [{"id": "a1", "title": "t", "model": "m", "saved_at": "x",
                 "message_count": 1}]

    def load_session(self, sid):
        import threading
        import time
        self.thread_names.append(threading.current_thread().name)
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._data

    def save_session(self, msgs, model=None, subagents=None, session_id=None,
                     session_log=None):
        self.saved.append(list(msgs))
        return "saved1234567890"


def _make_ctx(fake_p):
    return types.SimpleNamespace(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old"},
        ],
        state={},
        session=None,
        ui_adapter=None,
        persistence_port=fake_p,
    )


# ── _SessionLoadJob ──────────────────────────────────────


def test_session_load_job_runs_in_background_thread():
    fake = _FakeP(data={"messages": [{"role": "user", "content": "hi"}]})
    job = dc._SessionLoadJob(fake, "abc")
    job.start()
    finished, data = job.wait(5.0)
    assert finished is True
    assert data["messages"][0]["content"] == "hi"
    assert fake.thread_names == ["session-load"]
    assert job.error is None


def test_session_load_job_error_captured():
    fake = _FakeP(error=RuntimeError("boom"))
    job = dc._SessionLoadJob(fake, "abc")
    job.start()
    finished, data = job.wait(5.0)
    assert finished is True
    assert data is None
    assert isinstance(job.error, RuntimeError)


def test_session_load_job_timeout():
    fake = _FakeP(data={"messages": []}, delay=0.5)
    job = dc._SessionLoadJob(fake, "abc")
    job.start()
    finished, data = job.wait(0.01)
    assert finished is False and data is None
    # 后台继续完成后可再次拿到结果
    finished, data = job.wait(5.0)
    assert finished is True and data == {"messages": []}


# ── _load_session_by_id（异步读取 + 并行准备） ───────────


def test_load_session_by_id_async_replaces_messages(monkeypatch):
    fake = _FakeP(data={"messages": [{"role": "user", "content": "loaded"}],
                        "model": "m2"})
    ctx = _make_ctx(fake)
    rec = _Recorder()
    monkeypatch.setattr(dc, "_out", rec)
    monkeypatch.setattr(dc, "get_sandbox_manager", lambda: None)

    assert dc._load_session_by_id(ctx, "abc") is True
    assert [m["content"] for m in ctx.messages] == ["sys", "loaded"]
    assert ctx.state["model"] == "m2"
    joined = "\n".join(rec.calls)
    assert "正在加载会话 abc" in joined
    assert "已加载会话" in joined
    # 读取发生在后台线程（异步），且当前会话已自动保存
    assert fake.thread_names == ["session-load"]
    assert fake.saved and fake.saved[0][0]["content"] == "old"


def test_load_session_by_id_missing_session(monkeypatch):
    fake = _FakeP(data=None)
    ctx = _make_ctx(fake)
    rec = _Recorder()
    monkeypatch.setattr(dc, "_out", rec)
    monkeypatch.setattr(dc, "get_sandbox_manager", lambda: None)

    assert dc._load_session_by_id(ctx, "missing") is False
    assert "未找到会话" in "\n".join(rec.calls)


def test_load_session_by_id_read_error(monkeypatch):
    fake = _FakeP(error=OSError("disk"))
    ctx = _make_ctx(fake)
    rec = _Recorder()
    monkeypatch.setattr(dc, "_out", rec)
    monkeypatch.setattr(dc, "get_sandbox_manager", lambda: None)

    assert dc._load_session_by_id(ctx, "abc") is False
    assert "会话加载失败" in "\n".join(rec.calls)


def test_load_session_by_id_empty_id():
    assert dc._load_session_by_id(_make_ctx(_FakeP()), "  ") is False


# ── _build_entries_async（/sessions 视图异步初始化） ─────


def test_build_entries_async_writes_state_and_notifies():
    fake = _FakeP(data={"messages": [{"role": "user", "content": "hi"}]})
    state = types.SimpleNamespace(done=False, loading=True, entries=[],
                                  loading_error="")
    notified = []

    dc._build_entries_async(fake, state, notify=lambda: notified.append(1))
    import time
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not notified:
        time.sleep(0.01)
    assert notified  # 逐条增量 + 完成各请求一次重绘
    assert state.loading is False
    assert state.entries and state.entries[0]["preview_lines"][0][1] == "hi"
    assert state.loading_error == ""


def test_build_entries_async_adds_one_entry_per_load():
    """每加载成功一条即写入 state.entries（界面逐条增加一条信息）。"""
    import threading
    import time

    class _StreamP:
        def __init__(self):
            self.gate = threading.Event()

        def iter_sessions(self):
            for i in range(3):
                if not self.gate.wait(5.0):
                    return
                self.gate.clear()
                yield {
                    "id": f"s{i}", "title": f"t{i}", "model": "m",
                    "saved_at": f"2026-0{i + 1}-01T00:00:00", "message_count": 1,
                }

        def load_session(self, sid):
            return {"messages": [{"role": "user", "content": sid}]}

    p = _StreamP()
    state = types.SimpleNamespace(done=False, loading=True, entries=[],
                                  loading_error="")
    dc._build_entries_async(p, state, notify=lambda: None)

    for expected in (1, 2, 3):
        p.gate.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(state.entries) < expected:
            time.sleep(0.005)
        assert len(state.entries) == expected, (expected, state.entries)
    # 全部完成后 loading 复位且顺序为 saved_at 降序
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and state.loading:
        time.sleep(0.01)
    assert state.loading is False
    assert [e["id"] for e in state.entries] == ["s2", "s1", "s0"]


def test_build_entries_async_skips_closed_view():
    fake = _FakeP()
    state = types.SimpleNamespace(done=True, loading=True, entries=[],
                                  loading_error="")
    notified = []
    dc._build_entries_async(fake, state, notify=lambda: notified.append(1))
    import time
    time.sleep(0.2)
    assert notified == []
    assert state.loading is True


def test_build_entries_async_tolerates_list_error():
    """list_sessions 抛异常时条目为空、loading 复位且仍请求重绘（视图不卡死）。"""
    class _BadP(_FakeP):
        def list_sessions(self):
            raise RuntimeError("nope")

    state = types.SimpleNamespace(done=False, loading=True, entries=[],
                                  loading_error="")
    notified = []
    dc._build_entries_async(_BadP(), state, notify=lambda: notified.append(1))
    import time
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not notified:
        time.sleep(0.01)
    assert notified == [1]
    assert state.loading is False
    assert state.entries == []


def test_build_entries_async_records_error(monkeypatch):
    """_build_session_entries 自身抛异常时记录 loading_error（视图显示失败）。"""
    def _boom(_persistence, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dc, "_build_session_entries", _boom)
    state = types.SimpleNamespace(done=False, loading=True, entries=[],
                                  loading_error="")
    notified = []
    dc._build_entries_async(_FakeP(), state, notify=lambda: notified.append(1))
    import time
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not notified:
        time.sleep(0.01)
    assert notified == [1]
    assert state.loading is False
    assert "boom" in state.loading_error


# ── open_fullscreen_view(bg_setup=...) ──────────────────


class _FakeChatUI:
    def __init__(self, model):
        self._model = model
        self.redraws = 0
        self.flushes = 0

    def get_model(self):
        return self._model

    def request_bottom_redraw(self):
        self.redraws += 1

    def flush_input_router(self, timeout=2.0):
        self.flushes += 1
        return True


def test_open_fullscreen_view_bg_setup_background(monkeypatch):
    from src.core.commands import _view_opener as vo
    from src.tui.app._state_types import SessionsViewState
    from src.tui.app.model import AppModel

    model = AppModel()
    fake = _FakeChatUI(model)
    monkeypatch.setattr(
        "src.core.adapters.ui_runtime.get_active_chat_ui", lambda: fake,
    )
    rec = _Recorder()
    monkeypatch.setattr(vo, "_out", rec)

    import threading
    bg_threads = []

    def setup(_model, state):
        state.entries = []
        state.loading = True

    def bg_setup(state, refresh):
        bg_threads.append(threading.current_thread().name)
        state.entries = [{"id": "a1", "title": "t"}]
        state.loading = False
        refresh()

    def on_tick(state):
        return not state.loading

    opened = vo.open_fullscreen_view(
        types.SimpleNamespace(), view_id="sessions", state_attr="sessions_view",
        state_cls=SessionsViewState, setup=setup, bg_setup=bg_setup,
        on_tick=on_tick, close_hint="会话浏览器已关闭",
    )
    assert opened is True
    assert bg_threads and bg_threads[0] == "view-bg-sessions"
    assert fake.redraws >= 2          # 打开时 + bg 完成后
    assert model.fullscreen == ""     # 关闭后清理
    assert any("会话浏览器已关闭" in c for c in rec.calls)


# ── _open_sessions_ui（/sessions 视图异步初始化端到端） ──


def test_open_sessions_ui_loads_entries_asynchronously(monkeypatch):
    """打开 /sessions：初始 loading 占位（entries 空）→ 后台逐条填充 → 关闭。"""
    import threading
    import time

    from src.core.commands import _view_opener as vo
    from src.tui.app.model import AppModel

    class _GatedP(_FakeP):
        def __init__(self):
            super().__init__()
            self.gate = threading.Event()

        def iter_sessions(self):
            if not self.gate.wait(5):
                return
            yield {"id": "a1", "title": "标题甲", "model": "m",
                   "saved_at": "2026-10-10T00:00:00", "message_count": 1}

        def load_session(self, sid):
            return {"messages": [{"role": "user", "content": "hi"}]}

    fake = _GatedP()
    model = AppModel()
    chat_ui = _FakeChatUI(model)
    monkeypatch.setattr(
        "src.core.adapters.ui_runtime.get_active_chat_ui", lambda: chat_ui,
    )
    seen = []
    real_sleep = time.sleep

    def fake_sleep(_sec):
        real_sleep(0.005)
        sv = model.sessions_view
        seen.append((bool(sv.loading), len(sv.entries)))
        if len(seen) == 1:
            fake.gate.set()          # 放行后台加载（首批之前：加载占位）
        if not sv.loading and sv.entries:
            sv.try_set_final("cancel")

    monkeypatch.setattr(vo._time, "sleep", fake_sleep)
    ctx = types.SimpleNamespace(persistence_port=fake)

    assert dc._open_sessions_ui(ctx) is True
    assert seen[0] == (True, 0)                      # 初始：加载占位
    assert seen[-1][0] is False and seen[-1][1] >= 1  # 完成：条目就绪
    assert model.fullscreen == ""


def test_open_sessions_ui_no_chat_ui_returns_false(monkeypatch):
    from src.core.adapters import ui_runtime

    monkeypatch.setattr(ui_runtime, "get_active_chat_ui", lambda: None)
    ctx = types.SimpleNamespace(persistence_port=_FakeP())
    assert dc._open_sessions_ui(ctx) is False



def test_sessions_view_renders_loading_placeholder():
    from src.tui.app.model import AppModel
    from src.tui.app.sessions_view import SessionsView
    from src.tui.ink import h, renderToString

    model = AppModel()
    model.sessions_view.visible = True
    model.sessions_view.seq = 1
    model.sessions_view.entries = []
    model.sessions_view.loading = True
    out = renderToString(h(SessionsView, {"model": model, "width": 100}),
                         {"columns": 100})
    assert "正在加载会话列表" in out


def test_sessions_view_renders_entries_after_load():
    from src.tui.app.model import AppModel
    from src.tui.app.sessions_view import SessionsView
    from src.tui.ink import h, renderToString

    model = AppModel()
    model.sessions_view.visible = True
    model.sessions_view.seq = 1
    model.sessions_view.entries = [
        {"id": "a1", "title": "标题甲", "model": "m", "saved_at": "now",
         "message_count": 2, "preview_lines": [("用户", "hi")]},
    ]
    model.sessions_view.loading = False
    out = renderToString(h(SessionsView, {"model": model, "width": 100}),
                         {"columns": 100})
    assert "标题甲" in out
    assert "正在加载会话列表" not in out
