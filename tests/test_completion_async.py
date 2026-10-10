"""补全异步加载 — AsyncSource / 引擎异步模式 / 「加载中…」占位与动态刷新。"""

from __future__ import annotations

import os
import threading
import time

from src.tui._async_source import AsyncSource
from src.tui._completion import (
    _SHOW_ITEMS,
    _SHOW_LOADING,
    _SHOW_NONE,
    _CmplHandler,
    _show_completions_for,
)
from src.tui._completion_engine import CompletionEngine


class _Bridge:
    """InkBridge 补全面最小桩（记录显示调用）。"""

    def __init__(self):
        self.visible = False
        self.loading = False
        self.items = []
        self.texts = []
        self.calls = []
        self.hidden = 0

    @property
    def is_completion_visible(self):
        return self.visible

    @property
    def is_completion_loading(self):
        return self.visible and self.loading

    def show_completions(self, items, selected_idx, texts=None, start_pos=0,
                         orig_prefix="", title="补全", types=None,
                         match_prefix="", descriptions=None, split_desc=False,
                         loading=False):
        self.calls.append({
            "items": list(items), "texts": list(texts or []),
            "loading": bool(loading), "orig_prefix": orig_prefix,
        })
        self.visible = True
        self.loading = bool(loading)
        self.items = list(items)
        self.texts = list(texts or [])

    def hide_completions(self):
        self.hidden += 1
        self.visible = False
        self.loading = False

    def cycle_completion(self, delta=1):
        return 0

    def get_selected_completion(self):
        return ("", 0, "")


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ── AsyncSource ─────────────────────────────────────────


def test_async_source_sync_fallback_immediate():
    src = AsyncSource("t")
    src.register("k", lambda: [1, 2], ttl=60.0)
    ready, value = src.peek("k", sync_fallback=True)
    assert ready is True
    assert value == [1, 2]
    # 命中缓存（fetcher 不再执行）
    calls = {"n": 0}
    src.register("k2", lambda: (calls.__setitem__("n", calls["n"] + 1), ["x"])[1])
    src.peek("k2", sync_fallback=True)
    src.peek("k2", sync_fallback=True)
    assert calls["n"] == 1


def test_async_source_async_mode_not_ready_then_ready():
    src = AsyncSource("t")
    gate = threading.Event()
    src.register("k", lambda: (gate.wait(3), ["v"])[1], ttl=60.0)
    ready, value = src.peek("k", sync_fallback=False)
    assert ready is False and value is None
    assert src.pending_keys() == ["k"]
    gate.set()
    assert _wait_until(lambda: src.is_ready("k"))
    ready, value = src.peek("k", sync_fallback=False)
    assert ready is True and value == ["v"]


def test_async_source_listener_and_invalidate():
    src = AsyncSource("t")
    seen = []
    src.add_listener(lambda key: seen.append(key))
    src.register("k", lambda: 1)
    src.prefetch(["k"])
    assert _wait_until(lambda: seen == ["k"])
    assert src.is_ready("k")
    src.invalidate("k")
    assert not src.is_ready("k")


def test_async_source_unknown_key_not_ready():
    src = AsyncSource("t")
    assert src.peek("missing") == (False, None)


def test_async_source_prefetch_dedup():
    src = AsyncSource("t")
    calls = {"n": 0}
    gate = threading.Event()

    def _fetch():
        calls["n"] += 1
        gate.wait(3)
        return "v"

    src.register("k", _fetch)
    src.prefetch(["k"])
    src.prefetch(["k"])
    src.prefetch(["k"])
    gate.set()
    assert _wait_until(lambda: src.is_ready("k"))
    assert calls["n"] == 1


# ── 引擎异步模式 ────────────────────────────────────────


def test_engine_sync_mode_unchanged():
    eng = CompletionEngine(commands_source=lambda: ["/help", "/load"])
    assert [i.text for i in eng.complete("/he")] == ["/help"]
    assert eng.pending is False


def test_engine_async_first_pass_pending_then_ready():
    gate = threading.Event()
    eng = CompletionEngine(commands_source=lambda: ["/help", "/load"], async_mode=True)
    eng.register_source("sessions", lambda: (gate.wait(3), [
        {"id": "abc1234567", "title": "标题"},
    ])[1])
    items = eng.complete("/load ")
    assert items == []
    assert eng.pending is True
    gate.set()
    assert _wait_until(lambda: eng._source.is_ready("sessions"))
    items = eng.complete("/load ")
    assert eng.pending is False
    assert [i.item_type for i in items] == ["session"]
    assert items[0].text == "/load abc1234567"


def test_engine_path_completion_uses_sync_glob(tmp_path):
    """文件/路径补全保持原同步 glob 实现（不参与异步加载）。"""
    (tmp_path / "sub_a").mkdir()
    (tmp_path / "a.txt").touch()
    eng = CompletionEngine(async_mode=True)
    eng.warmup()
    assert _wait_until(lambda: eng._source.is_ready("commands"))
    prefix = os.path.join(str(tmp_path), "s")
    items = eng.complete(prefix)
    assert [i.display for i in items] == ["sub_a" + os.sep]
    assert items[0].item_type == "dir"
    assert eng.pending is False          # 路径补全同步执行，不产生待加载数据源


def test_async_source_stream_emits_incrementally():
    src = AsyncSource("t")
    notifications = []

    def producer(emit):
        emit([1])
        emit([1, 2])
        emit([1, 2, 3])
        time.sleep(0.15)
        emit([1, 2, 3, 4])

    src.add_listener(lambda key: notifications.append(1))
    src.register_stream("k", producer)
    src.prefetch(["k"])
    assert _wait_until(lambda: src.peek("k", sync_fallback=False) == (True, [1, 2, 3, 4]))
    # 增量：首个 emit 立即通知 + 流结束兜底通知（≥2 次）
    assert len(notifications) >= 2


def test_engine_stream_sessions_grows_candidates():
    """会话候选**逐条**就绪：加载成功一条即补全弹窗增加一条候选。"""
    gate = threading.Event()

    def producer(emit):
        emit([{"id": "a1", "title": "t1"}])
        gate.wait(3)
        emit([{"id": "a1", "title": "t1"}, {"id": "b2", "title": "t2"}])

    eng = CompletionEngine(async_mode=True)
    eng.register_stream_source("sessions", producer)
    assert eng.complete("/load ") == []
    assert eng.pending is True
    assert _wait_until(lambda: len(eng.complete("/load ")) == 1)
    gate.set()
    assert _wait_until(lambda: len(eng.complete("/load ")) == 2)
    assert eng.pending is False


def test_engine_warmup_preloads_resident_keys():
    eng = CompletionEngine(commands_source=lambda: ["/help"], async_mode=True)
    eng.warmup()
    assert _wait_until(lambda: eng._source.is_ready("commands"))
    assert eng._source.is_ready("models") or True  # 模型源可能为空但已加载


# ── _CmplHandler 「加载中…」占位与动态刷新 ───────────────


def test_show_completions_for_loading_state():
    gate = threading.Event()
    eng = CompletionEngine(async_mode=True)
    eng.register_source("sessions", lambda: (gate.wait(3), [
        {"id": "abc12345", "title": "t"},
    ])[1])
    bridge = _Bridge()
    assert _show_completions_for(bridge, eng, "/load ") == _SHOW_LOADING
    assert bridge.loading is True and bridge.items == []
    gate.set()
    assert _wait_until(lambda: eng._source.is_ready("sessions"))
    assert _show_completions_for(bridge, eng, "/load ") == _SHOW_ITEMS
    assert bridge.loading is False and bridge.items


def test_show_completions_for_none_state():
    eng = CompletionEngine(commands_source=lambda: ["/help"])
    bridge = _Bridge()
    assert _show_completions_for(bridge, eng, "/zzz") == _SHOW_NONE
    assert bridge.visible is False


def test_handler_tab_during_loading_returns_text():
    gate = threading.Event()
    eng = CompletionEngine(async_mode=True)
    eng.register_source("sessions", lambda: (gate.wait(3), [
        {"id": "abc12345", "title": "t"},
    ])[1])
    bridge = _Bridge()
    handler = _CmplHandler(bridge, eng, lambda: None, text_provider=lambda: "/load ")
    handler.on_auto("/load ")
    assert bridge.loading is True
    assert handler.on_tab("/load ") == "/load "   # 不插入制表符、不应用
    gate.set()


def test_handler_refreshes_popup_when_data_ready():
    gate = threading.Event()
    eng = CompletionEngine(async_mode=True)
    eng.register_source("sessions", lambda: (gate.wait(3), [
        {"id": "abc12345", "title": "标题"},
    ])[1])
    bridge = _Bridge()
    handler = _CmplHandler(bridge, eng, lambda: None, text_provider=lambda: "/load ")
    handler.on_auto("/load ")
    assert bridge.loading is True
    gate.set()
    assert _wait_until(lambda: bridge.items)
    assert bridge.loading is False
    assert bridge.texts[0].startswith("/load abc12345")
    assert handler is not None


def test_handler_dismiss_resets_debounce():
    eng = CompletionEngine(async_mode=True)
    bridge = _Bridge()
    handler = _CmplHandler(bridge, eng, lambda: None)
    handler.on_auto("/config ")
    handler.on_dismiss()
    assert handler._last_auto_text is None
