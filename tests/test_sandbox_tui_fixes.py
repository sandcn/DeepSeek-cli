"""文件沙盒 TUI 缺陷修复回归测试（2026-10）。

覆盖本轮修复：
  - 消息索引 0 的二次确认（``int(x or -1)`` 把合法值 0 吞成 -1）；
  - 搜索匹配在排序 / 视图模式 / 类型过滤切换后的重算（过滤显示错位条目）；
  - 概览视图 ``h``/``l`` 打开子视图：状态复位（二次进入不再空白）/
    ``visible`` 兜底 / 释放父视图命令线程轮询；
  - ``use_memo`` 缓存依赖值驱动（消息子记录内容签名、统计区块签名）；
  - 输入校验（拒绝负数消息索引——此前等价于隐式「全部回滚」）；
  - 消息视图下按键语义（``i`` 统计面板可用，``d``/``r``/``x``/``y`` 明确提示）；
  - 显示修复（``line_delta`` 无内容、统计分隔线按显示宽度、头部单位）。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest


# ── headless 交互 harness（渲染 + 按键注入） ────────────────


class _Harness:
    """headless reconciler：渲染组件树并取到 input router 以注入按键。

    与真实渲染循环一致：事件由**上一帧**注册的 handler 处理，处理后再渲染
    一帧（使闭包内的状态快照更新）——视图内 handler 捕获的是渲染期标量快照，
    不重渲染会让后续按键基于旧模式计算。
    """

    def __init__(self, element, columns: int = 100, rows: int = 30):
        from src.tui.ink.reconciler import Reconciler

        self.columns = columns
        self.recon = Reconciler(schedule_callback=lambda: None)
        self.recon.hook_context.install_headless(columns, rows)
        self.router = None
        self.recon.hook_context.input_router_callback = self._capture
        self.root = Reconciler.create_root()
        self.element = element
        self.render()

    def _capture(self, router):
        self.router = router

    def render(self) -> None:
        self.recon.render(self.root, self.element, self.columns, 0)

    def send(self, event) -> bool:
        assert self.router is not None, "router 未发布（渲染失败）"
        ok = bool(self.router(event))
        self.render()
        return ok


def _char(ch: str):
    from src.tui._input_parser import KeyEvent

    return KeyEvent(kind="char", char=ch)


def _enter():
    from src.tui._input_parser import KeyEvent

    return KeyEvent(kind="enter")


def _render_text(model, component, width: int = 100) -> str:
    from src.tui.ink import h, renderToString
    from src.tui.ink.helpers import strip_ansi

    out = renderToString(h(component, {"model": model, "width": width}), {"columns": width})
    return strip_ansi(out)


# ── 数据夹具 ───────────────────────────────────────────────


@pytest.fixture
def sm(tmp_path):
    from src.core.sandbox_manager import SandboxManager

    mgr = SandboxManager()
    # 消息 0：合法索引（修复前无法二次确认回滚）
    mgr.record_file_change(str(tmp_path / "a.py"), None, "1\n", 0, tool_name="write_file")
    mgr.record_file_change(str(tmp_path / "a.py"), "1\n", "2\n", 0, tool_name="write_file")
    mgr.record_file_change(str(tmp_path / "a.py"), "2\n", "3\n", 0, tool_name="write_file")
    mgr.record_file_change(str(tmp_path / "b.py"), None, "b\n", 1, tool_name="write_file")
    mgr.record_file_change(str(tmp_path / "c.py"), None, "c\n", 1, tool_name="write_file")
    mgr.record_file_change(str(tmp_path / "c.py"), "c\n", "cc\n", 1, tool_name="write_file")
    return mgr


# ── 1. as_int：0 是合法值 ──────────────────────────────────


def test_as_int_preserves_zero():
    from src.tui.app.sandbox_common import as_int

    assert as_int(0, -1) == 0
    assert as_int("0", -1) == 0
    assert as_int(None, -1) == -1
    assert as_int("x", -1) == -1
    assert as_int(3) == 3


# ── 2. 消息索引 0 的二次确认（历史视图） ────────────────────


def test_history_view_confirms_message_zero(sm):
    from src.core.commands._sandbox_cmd import build_message_entries
    from src.tui.app._state_types import SandboxHistoryViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_history_view import SandboxHistoryView, _message_deps

    model = AppModel()
    state = SandboxHistoryViewState(
        visible=True, seq=1, entries=build_message_entries(sm),
    )
    model.sandbox_history_view = state
    hv = _Harness(_element(SandboxHistoryView, model))

    assert hv.send(_char("R")) is True          # 第一次：进入待确认
    assert state.restore_confirm == 0
    assert "确认" in state.status_message
    assert _message_deps(state.entries[0], 40)      # 依赖可计算（不抛异常）
    hv.send(_char("R"))                          # 第二次：确认（修复前被 0→-1 吞掉）
    assert state.applied == {"action": "restore-message", "index": 0}
    assert state.applied_seq == 1


def _element(component, model, width: int = 100):
    from src.tui.ink import h

    return h(component, {"model": model, "width": width})


# ── 3. 搜索匹配在基准列表变化后重算 ────────────────────────


def _changes_model(sm):
    from src.core.commands._sandbox_cmd import (
        build_change_entries,
        build_message_entries,
        build_sandbox_sections,
    )
    from src.tui.app._state_types import ChangesViewState
    from src.tui.app.model import AppModel

    model = AppModel()
    model.changes_view = ChangesViewState(
        visible=True, seq=1,
        entries=build_change_entries(sm),
        messages=build_message_entries(sm),
        sections=build_sandbox_sections(sm),
    )
    return model


def _search(hv, text):
    hv.send(_char("/"))
    for ch in text:
        hv.send(_char(ch))
    hv.send(_enter())


def test_changes_view_search_resync_after_sort(sm):
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    hv = _Harness(_element(ChangesView, model))
    _search(hv, "b")                     # path 排序下 b.py 在下标 1
    assert cv.search_matches == [1]
    hv.send(_char("s"))                  # 切到修改次数排序（基准列表重建）
    assert cv.search_matches == [2]      # 重算后仍指向 b.py
    hv.send(_char("f"))                  # 开过滤
    assert cv.search_filter is True
    text = _render_text(model, ChangesView)
    assert "b.py" in text
    assert "c.py" not in text


def test_changes_view_search_resync_after_type_filter(sm):
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    hv = _Harness(_element(ChangesView, model))
    _search(hv, "a.py")
    assert cv.search_matches == [0]
    hv.send(_char("T"))                  # 类型过滤：全部 → 新建
    hv.send(_char("T"))                  # → 修改
    assert cv.type_filter == "修改"
    # a.py 经「修改」过滤后仍在下标 0；匹配必须仍指向 a.py
    hv.send(_char("f"))
    text = _render_text(model, ChangesView)
    assert "a.py" in text


def test_records_view_search_resync_after_sort_desc(sm):
    from src.core.commands._sandbox_cmd import build_record_history_entries
    from src.tui.app._state_types import SandboxRecordsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_records_view import SandboxRecordsView

    model = AppModel()
    rv = SandboxRecordsViewState(
        visible=True, seq=1, entries=build_record_history_entries(sm),
    )
    model.sandbox_records_view = rv
    hv = _Harness(_element(SandboxRecordsView, model))
    _search(hv, "b.py")
    before = list(rv.search_matches)
    assert before
    hv.send(_char("s"))                  # 倒序
    assert rv.sort_desc is True
    assert rv.search_matches != before   # 重算（旧下标已失效）
    hv.send(_char("f"))
    text = _render_text(model, SandboxRecordsView)
    assert "b.py" in text


def test_resync_search_disables_filter_when_no_match():
    from src.tui.app._state_types import ChangesViewState
    from src.tui.app.sandbox_common import resync_search

    state = ChangesViewState(search_pattern="zzz", search_matches=[3], search_filter=True)
    found = resync_search(state, [{"path": "a.py"}], lambda e: e["path"])
    assert found == 0
    assert state.search_matches == []
    assert state.search_idx == -1
    assert state.search_filter is False   # 无匹配时自动关过滤（不留空列表）


def _file_entry(path: str) -> dict:
    return {
        "path": path, "change_label": "修改", "records": 1,
        "message_index": "1-1", "tools": ["write_file"],
        "before": "a\n", "after": "b\n", "history": [],
    }


def test_changes_view_search_self_heals_after_data_refresh(sm):
    """沙盒实时刷新重建基准列表（流式期间工具继续写文件）后匹配自愈。"""
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    hv = _Harness(_element(ChangesView, model))
    _search(hv, "b.py")
    hv.send(_char("f"))                   # 开过滤
    assert cv.search_filter is True
    old_matches = list(cv.search_matches)

    # 模拟刷新器写入新数据：插入 aa.py 使 b.py 的下标右移
    cv.entries = [_file_entry("a.py"), _file_entry("aa.py"), _file_entry("b.py")]
    hv.render()
    assert cv.search_matches != old_matches
    assert cv.entries[cv.search_matches[0]]["path"].endswith("b.py")
    text = _render_text(model, ChangesView)
    assert "b.py" in text
    assert "aa.py" not in text


def test_sync_search_matches_unit():
    from src.tui.app._state_types import SandboxRecordsViewState
    from src.tui.app.sandbox_common import sync_search_matches

    state = SandboxRecordsViewState(search_pattern="b", search_matches=[2], search_idx=0)
    items = [{"path": "a.py"}, {"path": "b.py"}]
    found = sync_search_matches(state, items, lambda e: e["path"])
    assert found == [1]
    assert state.search_matches == [1]
    assert state.search_idx == 0

    state.search_filter = True
    state.search_pattern = "zzz"
    assert sync_search_matches(state, items, lambda e: e["path"]) == []
    assert state.search_filter is False


# ── 4. 输入校验：拒绝负索引 ────────────────────────────────


def test_changes_view_rejects_negative_message_index(sm):
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    hv = _Harness(_element(ChangesView, model))
    hv.send(_char("R"))                  # 进入回滚到消息索引输入模式
    assert cv.restore_mode is True
    hv.send(_char("-"))
    assert cv.restore_value == ""        # 第一道防线：非数字字符不入缓冲
    # 第二道防线：缓冲被外部写脏（负号）时 Enter 仍拒绝提交
    cv.restore_value = "-1"
    hv.send(_enter())
    assert cv.applied is None            # 未提交任何动作
    assert cv.applied_seq == 0
    assert "非负" in cv.status_message


def test_changes_view_accepts_non_negative_message_index(sm):
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    hv = _Harness(_element(ChangesView, model))
    hv.send(_char("R"))
    hv.send(_char("1"))
    hv.send(_enter())
    assert cv.applied == {"action": "restore-message", "index": 1}
    assert cv.applied_seq == 1


def test_apply_sandbox_action_rejects_negative_index(sm):
    from src.core.commands._sandbox_cmd import apply_sandbox_action

    msg = apply_sandbox_action(sm, {"action": "restore-message", "index": -1})
    assert "无效消息索引" in msg
    assert len(sm.get_all_file_changes()) == 6   # 记录未被清空（未发生全量回滚）


# ── 5. 消息视图下的按键语义 ────────────────────────────────


def test_changes_view_message_mode_stats_panel(sm):
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    model.changes_view.view_mode = "message"
    hv = _Harness(_element(ChangesView, model))
    hv.send(_char("i"))
    assert cv.detail_mode == "stats"
    text = _render_text(model, ChangesView)
    assert "概览" in text                 # 修复前该分支被消息详情抢先


def test_changes_view_message_mode_hints(sm):
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    cv.view_mode = "message"
    hv = _Harness(_element(ChangesView, model))
    hv.send(_char("d"))
    assert "不适用" in cv.status_message
    hv.send(_char("r"))
    assert "不适用" in cv.status_message
    hv.send(_char("x"))
    assert cv.applied is None and "R 回滚" in cv.status_message
    hv.send(_char("y"))
    assert "复制" in cv.status_message
    text = _render_text(model, ChangesView)
    assert "条消息" in text               # 头部单位修正（此前恒为「个文件」）


# ── 6. 概览视图打开子视图 ─────────────────────────────────


def test_open_sub_view_resets_state_and_releases_parent(sm, monkeypatch):
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager
    from src.tui.app._state_types import (
        SandboxHistoryViewState,
        SandboxStatsViewState,
    )
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import _open_sub_view

    old = get_sandbox_manager()
    set_sandbox_manager(sm)
    try:
        model = AppModel()
        parent = SandboxStatsViewState(visible=True, seq=1)
        model.sandbox_view = parent
        # 上次关闭的子视图残留：done / 选中 / 搜索态
        model.sandbox_history_view = SandboxHistoryViewState(
            visible=False, seq=2, done=True, selected=5, search_pattern="x",
            search_matches=[1], status_message="旧提示",
        )
        model.fullscreen = "sandbox"
        _open_sub_view(model, "sandbox_history")
        sub = model.sandbox_history_view
        assert model.fullscreen == "sandbox_history"
        assert sub.done is False           # 二次进入不再空白
        assert sub.visible is True         # 无刷新器也兜底可见
        assert sub.selected == 0
        assert sub.search_pattern == ""
        assert sub.search_matches == []
        assert sub.status_message == ""
        assert parent.done is True         # 释放父视图命令轮询
    finally:
        set_sandbox_manager(old)


def test_open_sub_view_releases_command_thread(sm, monkeypatch):
    """``/sandbox`` 命令线程在切到子视图后必须退出（此前悬挂至超时）。"""
    import src.tui.consumer as consumer
    from src.core.commands._sandbox_cmd import _cmd_sandbox
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import _open_sub_view

    old = get_sandbox_manager()
    set_sandbox_manager(sm)
    try:
        model = AppModel()
        chat_ui = SimpleNamespace(
            get_model=lambda: model,
            request_bottom_redraw=lambda: None,
            flush_input_router=lambda *_a, **_k: None,
        )
        monkeypatch.setattr(consumer, "get_active_chat_ui", lambda: chat_ui)
        ctx = SimpleNamespace(arg="")
        worker = threading.Thread(target=_cmd_sandbox, args=(ctx,), daemon=True)
        worker.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and model.fullscreen != "sandbox":
            time.sleep(0.02)
        assert model.fullscreen == "sandbox"
        _open_sub_view(model, "sandbox_history")
        worker.join(3.0)
        assert not worker.is_alive(), "命令线程仍被父视图轮询占用"
        assert model.fullscreen == "sandbox_history"
    finally:
        set_sandbox_manager(old)


# ── 7. 状态复位单一真源 ────────────────────────────────────


def test_reset_sandbox_view_state_fields():
    from src.tui.app._state_types import ChangesViewState
    from src.tui.app.sandbox_common import reset_sandbox_view_state

    state = ChangesViewState(
        visible=True, done=True, selected=4, cursor=9, scroll=9,
        pane="detail", help_open=True, search_mode=True, search_query="q",
        search_pattern="p", search_matches=[1, 2], search_idx=1,
        search_filter=True, status_message="msg", revert_confirm="a.py",
        revert_all_confirm=True, restore_mode=True, restore_value="3",
        export_message="导出",
    )
    old_matches = state.search_matches
    reset_sandbox_view_state(state, "changes_view", visible=True)
    assert state.done is False and state.action == ""
    assert state.selected == 0 and state.cursor == 0 and state.scroll == 0
    assert state.pane == "list" and state.help_open is False
    assert state.search_mode is False and state.search_query == ""
    assert state.search_pattern == "" and state.search_matches == []
    assert state.search_matches is not old_matches
    assert state.search_idx == -1 and state.search_filter is False
    assert state.status_message == ""
    assert state.revert_confirm == "" and state.revert_all_confirm is False
    assert state.restore_mode is False and state.restore_value == ""
    assert state.export_message == ""
    assert state.visible is True


def test_f11_toggle_keeps_sort_preference(sm):
    """F11 重开复位确认/搜索态，但不改用户排序偏好。"""
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    model.changes_view.sort_mode = "records"
    session = SimpleNamespace(request_bottom_redraw=lambda: None)
    cb = _make_changes_toggle_cb(model, session)
    cb()
    assert model.fullscreen == "changes"
    assert model.changes_view.visible is True
    assert model.changes_view.sort_mode == "records"
    cb()
    assert model.fullscreen == ""


# ── 8. 缓存依赖（值驱动） ──────────────────────────────────


def test_message_signature_detects_content_change():
    from src.tui.app.sandbox_common import message_signature

    base = {
        "index": 1, "count": 1, "files": 1, "time": 1.0, "tools": ["write_file"],
        "changes": [{
            "path": "a.py", "tool": "write_file", "change_label": "修改",
            "before": "old\n", "after": "new\n", "message_index": 1,
        }],
    }
    sig1 = message_signature(base)
    changed = {
        **base,
        "changes": [{**base["changes"][0], "after": "other\n"}],
    }
    assert message_signature(changed) != sig1   # 条数不变、内容变化 → 必须失效
    assert message_signature(base) == sig1


def test_record_signature_detects_metadata_change():
    from src.tui.app.sandbox_common import record_signature

    row = {"path": "a.py", "tool": "write_file", "change_label": "修改",
           "before": "x\n", "after": "y\n", "message_index": 1, "seq": 1}
    sig = record_signature(row)
    assert record_signature({**row, "tool": "bash"}) != sig
    assert record_signature({**row, "seq": 2}) != sig
    assert record_signature({**row, "time": 5.0}) != sig
    assert record_signature(row) == sig


def test_sections_signature_value_driven():
    from src.tui.app.sandbox_common import sections_signature

    a = [{"title": "概览", "rows": [("文件数", "1", "info", None)]}]
    b = [{"title": "概览", "rows": [("文件数", "2", "info", None)]}]
    assert sections_signature(a) != sections_signature(b)
    assert sections_signature(a) == sections_signature(
        [{"title": "概览", "rows": [("文件数", "1", "info", None)]}],
    )


def test_file_entry_signature_detects_tools_and_history():
    from src.tui.app.changes_view import _detail_deps, _history_deps
    from src.tui.app.sandbox_common import file_entry_signature

    entry = {
        "path": "a.py", "change_label": "修改", "records": 2,
        "before": "x\n", "after": "z\n", "tools": ["write_file"],
        "history": [{"path": "a.py", "seq": 1, "before": "x\n", "after": "y\n"}],
    }
    sig = file_entry_signature(entry)
    assert file_entry_signature({**entry, "tools": ["write_file", "bash"]}) != sig
    assert _detail_deps(entry, 40) != _detail_deps({**entry, "tools": ["bash"]}, 40)
    changed_hist = {**entry, "history": [{"path": "a.py", "seq": 1, "before": "x\n", "after": "q\n"}]}
    assert _history_deps(entry, 40) != _history_deps(changed_hist, 40)
    assert _history_deps(entry, 40) == _history_deps(dict(entry), 40)


def test_stats_label_column_handles_dict_rows():
    from src.tui.app.sandbox_common import stats_label_column

    wide = stats_label_column([{"title": "t", "rows": [
        {"label": "很长的中文字标签", "value": "1"},
    ]}])
    narrow = stats_label_column([{"title": "t", "rows": [("a", "1")]}])
    assert wide > narrow
    assert narrow == 14


# ── 9. 数据层性能 / 显示修复 ───────────────────────────────


def test_sandbox_signature_uses_light_stats(sm, monkeypatch):
    from src.core.commands._sandbox_cmd import sandbox_signature

    calls = {"n": 0}
    orig = sm.get_extended_stats

    def _spy():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(sm, "get_extended_stats", _spy)
    assert sandbox_signature(sm) == (6, 3, 1)
    assert calls["n"] == 0          # 签名走轻量 get_stats（每帧调用）


def test_build_view_data_stats_single_pass(sm, monkeypatch):
    from src.core.commands._sandbox_cmd import build_view_data

    calls = {"n": 0}
    orig = sm.get_extended_stats

    def _spy():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(sm, "get_extended_stats", _spy)
    data = build_view_data(sm)
    assert calls["n"] == 1          # sections 复用同一份统计
    assert set(data) >= {"files", "messages", "records", "sections", "stats"}


def test_line_delta_without_content():
    from src.tui.app.sandbox_common import line_delta

    assert line_delta(None, None) == "—"
    assert line_delta(None, "a\n") == "+1 行"
    assert line_delta("a\n", None) == "-1 行"
    assert line_delta("a\n", "a\nb\n") == "-1/+2"


def test_stats_rows_separator_fits_width():
    from src.tui._width import wcswidth_simple
    from src.tui.app.sandbox_common import stats_rows

    sections = [{
        "title": "中文标题测试",
        "rows": [("文件数", "1", "info", None), ("记录数", "2", "info", None)],
    }]
    for width in (30, 40, 60, 80):
        for runs in stats_rows(sections, width):
            text = "".join(r.text for r in runs)
            assert wcswidth_simple(text) <= width, (width, text)


def test_history_view_header_counts(sm):
    from src.core.commands._sandbox_cmd import build_message_entries
    from src.tui.app._state_types import SandboxHistoryViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_history_view import SandboxHistoryView

    model = AppModel()
    model.sandbox_history_view = SandboxHistoryViewState(
        visible=True, seq=1, entries=build_message_entries(sm),
    )
    text = _render_text(model, SandboxHistoryView)
    assert "2/2 组" in text


# ── 10. 帮助键（半角 ? / 全角 ？） ─────────────────────────


def _all_sandbox_models(sm):
    from src.core.commands._sandbox_cmd import (
        build_change_entries,
        build_message_entries,
        build_record_history_entries,
        build_sandbox_sections,
    )
    from src.tui.app._state_types import (
        SandboxHistoryViewState,
        SandboxRecordsViewState,
        SandboxStatsViewState,
    )
    from src.tui.app.model import AppModel

    model = AppModel()
    model.changes_view = _changes_model(sm).changes_view
    model.sandbox_view = SandboxStatsViewState(
        visible=True, seq=1, sections=build_sandbox_sections(sm),
    )
    model.sandbox_history_view = SandboxHistoryViewState(
        visible=True, seq=1, entries=build_message_entries(sm),
    )
    model.sandbox_records_view = SandboxRecordsViewState(
        visible=True, seq=1, entries=build_record_history_entries(sm),
    )
    assert build_change_entries(sm)     # 数据可用（夹具健壮性）
    return model


@pytest.mark.parametrize("key", ["?", "\uff1f"])
def test_help_key_opens_panel(sm, key):
    """半角 ``?`` 与全角 ``？``（中文输入法）都应打开帮助面板。"""
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.sandbox_history_view import SandboxHistoryView
    from src.tui.app.sandbox_records_view import SandboxRecordsView
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    model = _all_sandbox_models(sm)
    cases = [
        (ChangesView, "changes_view", "文件变更审查"),
        (SandboxStatsView, "sandbox_view", "文件沙盒"),
        (SandboxHistoryView, "sandbox_history_view", "沙盒历史"),
        (SandboxRecordsView, "sandbox_records_view", "变更记录流水"),
    ]
    for comp, attr, title in cases:
        state = getattr(model, attr)
        state.help_open = False
        hv = _Harness(_element(comp, model))
        assert hv.send(_char(key)) is True
        assert state.help_open is True, (key, attr)
        text = _render_text(model, comp)
        assert "帮助面板" in text, (key, attr, title)


def test_help_key_not_triggered_inside_search_input(sm):
    """搜索输入模式下 ``?`` 是搜索文本，不打开帮助。"""
    from src.tui.app.changes_view import ChangesView

    model = _changes_model(sm)
    cv = model.changes_view
    hv = _Harness(_element(ChangesView, model))
    hv.send(_char("/"))
    hv.send(_char("?"))
    assert cv.help_open is False
    assert cv.search_query.endswith("?")
