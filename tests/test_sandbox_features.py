"""文件沙盒界面数据层（core / 命令 / TUI 辅助）单元测试。

覆盖 2026-10 新增：
  - ``SandboxManager`` 视图/统计/回滚查询方法（文件路径 / 文件历史 / 消息分组 /
    消息记录 / 条件过滤 / 扩展统计 / 回滚预览 / 回滚 / 撤销回滚）；
  - ``core.commands._sandbox_cmd`` 数据构建（文件 / 消息 / 文件历史 / 记录流水 /
    统计区块 / 视图快照 / 刷新器 / 动作执行）；
  - ``tui.app.sandbox_common`` 纯辅助（排序 / 差异行 / 历史行 / 统计行）；
  - ``tui.app.sandbox_export`` 导出（Markdown / JSON / 写盘）；
  - ``tui.app.changes_view`` 辅助函数（搜索文本 / 类型过滤 / 目录树 / 预览）；
  - 视图注册、清单条目、状态类型、ui_runtime 桥接。
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def sm(tmp_path):
    """真实 SandboxManager（记录若干文件变更）。"""
    from src.core.sandbox_manager import SandboxManager

    manager = SandboxManager()
    f1 = str(tmp_path / "a.py")
    f2 = str(tmp_path / "sub" / "b.py")
    manager.record_file_change(f1, "old\n", "new\n", 3, tool_name="write_file")
    manager.record_file_change(f1, "new\n", "newer\n", 5, tool_name="update_file")
    manager.record_file_change(f2, None, "x\n", 5, tool_name="write_file")
    return manager


# ── SandboxManager 查询 / 统计 / 回滚 ─────────────────────


def test_get_file_paths_and_history(sm, tmp_path):
    paths = sm.get_file_paths()
    assert any(p.endswith("a.py") for p in paths)
    a = [p for p in paths if p.endswith("a.py")][0]
    history = sm.get_file_history(a)
    assert len(history) == 2
    assert [r.message_index for r in history] == [3, 5]


def test_get_message_groups(sm):
    groups = sm.get_message_groups()
    idxs = [i for i, _ in groups]
    assert idxs == [3, 5]
    assert len(dict(groups)[5]) == 2


def test_get_message_records(sm):
    assert len(sm.get_message_records(3)) == 1
    assert len(sm.get_message_records(5)) == 2
    assert sm.get_message_records(999) == []
    assert sm.get_message_records("bad") == []


def test_find_records(sm):
    assert len(sm.find_records(tool_name="write_file")) == 2
    assert len(sm.find_records(message_index=5)) == 2
    assert len(sm.find_records(change_type="新建文件")) == 1
    a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
    assert len(sm.find_records(file_path=a)) == 2


def test_get_extended_stats(sm):
    stats = sm.get_extended_stats()
    assert stats["total_files"] == 2
    assert stats["total_records"] == 3
    assert stats["message_groups"] == 2
    assert stats["tool_counts"]["write_file"] == 2
    assert stats["tool_counts"]["update_file"] == 1
    assert stats["revert_count"] == 0
    assert stats["content_chars"] > 0


def test_revert_file_and_undo(sm, tmp_path):
    a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
    with open(a, "w", encoding="utf-8") as fh:
        fh.write("newer\n")
    current, target = sm.preview_revert(a)
    assert current == "newer\n" and target == "old\n"
    ok, before, after = sm.revert_file(a)
    assert ok and before == "newer\n" and after == "old\n"
    assert open(a, encoding="utf-8").read() == "old\n"
    assert sm.get_last_revert(a) is not None
    # 撤销回滚 → 回到回滚前
    ok2, path = sm.undo_last_revert(a)
    assert ok2 and path == a
    assert open(a, encoding="utf-8").read() == "newer\n"


def test_revert_created_file_removes_it(sm, tmp_path):
    b = [p for p in sm.get_file_paths() if p.endswith("b.py")][0]
    os.makedirs(os.path.dirname(b), exist_ok=True)
    with open(b, "w", encoding="utf-8") as fh:
        fh.write("x\n")
    ok, _before, after = sm.revert_file(b)
    assert ok and after is None
    assert not os.path.exists(b)


def test_revert_file_unknown_path(sm):
    ok, before, after = sm.revert_file("/nonexistent/xyz")
    assert ok is False and before is None and after is None
    assert sm.undo_last_revert("/nonexistent/xyz") == (False, "")


# ── _sandbox_cmd 数据构建 ─────────────────────────────


def test_change_label():
    from src.core.commands._sandbox_cmd import change_label

    assert change_label(None, "x") == "新建"
    assert change_label("x", None) == "删除"
    assert change_label("x", "x") == "无变化"
    assert change_label("x", "y") == "修改"
    assert change_label(None, "x", True) == "新建目录"


def test_build_change_entries(sm):
    from src.core.commands._sandbox_cmd import build_change_entries

    entries = build_change_entries(sm)
    labels = {os.path.basename(e["path"]): e["change_label"] for e in entries}
    assert labels["a.py"] == "修改"
    assert labels["b.py"] == "新建"
    a = [e for e in entries if e["path"].endswith("a.py")][0]
    assert a["records"] == 2
    assert a["first_index"] == 3 and a["last_index"] == 5
    assert "write_file" in a["tools"]
    assert len(a["history"]) == 2


def test_build_message_entries(sm):
    from src.core.commands._sandbox_cmd import build_message_entries

    entries = build_message_entries(sm)
    assert [e["index"] for e in entries] == [3, 5]
    assert entries[0]["count"] == 1
    assert entries[1]["files"] == 2
    assert entries[1]["changes"]


def test_build_record_history_and_file_history(sm):
    from src.core.commands._sandbox_cmd import (
        build_file_history_entries,
        build_record_history_entries,
    )

    all_records = build_record_history_entries(sm)
    assert len(all_records) == 3
    assert [r["seq"] for r in all_records] == [1, 2, 3]
    a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
    only_a = build_record_history_entries(sm, a)
    assert len(only_a) == 2
    assert len(build_file_history_entries(sm, a)) == 2


def test_build_sandbox_sections(sm):
    from src.core.commands._sandbox_cmd import build_sandbox_sections

    sections = build_sandbox_sections(sm)
    titles = [s["title"] for s in sections]
    assert "概览" in titles
    assert "工具分布" in titles
    assert "变更类型" in titles
    assert "缓存与回滚" in titles


def test_build_sandbox_sections_without_sandbox():
    from src.core.commands._sandbox_cmd import build_sandbox_sections

    sections = build_sandbox_sections(None)
    assert sections and sections[0]["title"] == "沙盒"


def test_sandbox_signature_and_view_data(sm):
    from src.core.commands._sandbox_cmd import (
        build_view_data,
        sandbox_signature,
    )

    sig = sandbox_signature(sm)
    assert sig == (3, 2, 5)
    data = build_view_data(sm)
    assert set(data) >= {"files", "messages", "records", "sections", "stats"}
    assert len(data["records"]) == 3
    assert sandbox_signature(None) == (0, 0, 0)


def test_apply_sandbox_action_revert_all_and_clear(sm, tmp_path):
    from src.core.commands._sandbox_cmd import apply_sandbox_action

    a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
    with open(a, "w", encoding="utf-8") as fh:
        fh.write("newer\n")
    msg = apply_sandbox_action(sm, {"action": "revert", "path": a})
    assert "已回滚" in msg
    msg = apply_sandbox_action(sm, {"action": "revert-all"})
    assert "已回滚" in msg
    msg = apply_sandbox_action(sm, {"action": "clear"})
    assert "已清空" in msg
    assert sm.get_all_file_changes() == []


def test_apply_sandbox_action_restore_message(sm, tmp_path):
    from src.core.commands._sandbox_cmd import apply_sandbox_action

    a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
    with open(a, "w", encoding="utf-8") as fh:
        fh.write("newer\n")
    msg = apply_sandbox_action(sm, {"action": "restore-message", "index": 3})
    assert "消息 3" in msg


def test_apply_sandbox_action_edge_cases(sm):
    from src.core.commands._sandbox_cmd import apply_sandbox_action

    assert apply_sandbox_action(None, {"action": "revert"}) == "文件沙盒未初始化"
    assert apply_sandbox_action(sm, "bad") == "无效操作"
    assert apply_sandbox_action(sm, {"action": "nope"}) == "未知操作"
    assert apply_sandbox_action(sm, {"action": "restore-message", "index": "x"}) == "无效消息索引"


def test_make_sandbox_refresher_rebuilds(sm):
    from src.core.commands._sandbox_cmd import make_sandbox_refresher
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager

    old = get_sandbox_manager()
    set_sandbox_manager(sm)
    try:
        calls = []
        refresher = make_sandbox_refresher(None, lambda data: calls.append(data))
        assert refresher() is True  # 首次强制重建
        assert refresher() is False  # 签名未变
        sm.record_file_change("/tmp/z", None, "z", 7, tool_name="write_file")
        assert refresher() is True
        assert refresher(force=True) is True
        assert calls
    finally:
        set_sandbox_manager(old)


def test_make_sandbox_refresher_without_manager():
    from src.core.commands._sandbox_cmd import make_sandbox_refresher
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager

    old = get_sandbox_manager()
    set_sandbox_manager(None)
    try:
        refresher = make_sandbox_refresher(None, lambda data: None)
        assert refresher() is False
    finally:
        set_sandbox_manager(old)


def test_cmd_sandbox_arg_dispatch(monkeypatch):
    import types

    import src.core.commands._sandbox_cmd as mod

    calls: list = []
    monkeypatch.setattr(mod, "open_sandbox_ui", lambda ctx: (calls.append("stats"), True)[1])
    monkeypatch.setattr(mod, "open_sandbox_history_ui", lambda ctx: (calls.append("history"), True)[1])
    monkeypatch.setattr(mod, "open_sandbox_records_ui", lambda ctx: (calls.append("records"), True)[1])

    assert mod._cmd_sandbox(types.SimpleNamespace(arg="history")) is True
    assert mod._cmd_sandbox(types.SimpleNamespace(arg="h")) is True
    assert mod._cmd_sandbox(types.SimpleNamespace(arg="records")) is True
    assert mod._cmd_sandbox(types.SimpleNamespace(arg="r")) is True
    assert mod._cmd_sandbox(types.SimpleNamespace(arg="")) is True
    assert calls == ["history", "history", "records", "records", "stats"]


def test_consume_sandbox_actions_idempotent(sm, tmp_path):
    from src.core.commands._sandbox_cmd import consume_sandbox_actions
    from src.tui.app.model import AppModel

    model = AppModel()
    a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
    with open(a, "w", encoding="utf-8") as fh:
        fh.write("newer\n")
    model.changes_view.applied = {"action": "revert", "path": a}
    model.changes_view.applied_seq = 1
    assert consume_sandbox_actions(model, sm) is True
    assert "已回滚" in model.changes_view.status_message
    assert model.changes_view.revert_confirm == ""
    # 幂等：同一 applied_seq 不重复消费（命令路径与 F11 路径共用同一函数）
    assert consume_sandbox_actions(model, sm) is False
    # 无沙盒 / 无 model → False
    assert consume_sandbox_actions(model, None) is False
    assert consume_sandbox_actions(None, sm) is False


def test_consume_sandbox_actions_multi_view(sm):
    from src.core.commands._sandbox_cmd import consume_sandbox_actions
    from src.tui.app.model import AppModel

    model = AppModel()
    model.sandbox_view.applied = {"action": "clear"}
    model.sandbox_view.applied_seq = 2
    model.sandbox_history_view.applied = {"action": "restore-message", "index": 3}
    model.sandbox_history_view.applied_seq = 1
    assert consume_sandbox_actions(model, sm) is True
    assert "已清空" in model.sandbox_view.status_message
    assert model.sandbox_view.clear_confirm is False


def test_refresher_calls_action_handler(sm):
    from src.core.commands._sandbox_cmd import make_sandbox_refresher
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager

    old = get_sandbox_manager()
    set_sandbox_manager(sm)
    try:
        seen: list = []

        def _handler(sandbox):
            seen.append("action")
            return False

        refresher = make_sandbox_refresher(
            None, lambda data: seen.append("apply"), action_handler=_handler,
        )
        assert refresher() is True
        assert seen[0] == "action"
        assert "apply" in seen
        # 签名未变：仍先消费动作，但不再重建数据
        seen.clear()
        assert refresher() is False
        assert seen == ["action"]
    finally:
        set_sandbox_manager(old)


def test_register_session_handlers_injects_sandbox_refresher():
    """会话就绪注入：``model.sandbox_refresher`` / ``model.sandbox_source``。"""
    import types

    from src.app_loop._session_setup import _register_session_handlers
    from src.tui.app.model import AppModel

    model = AppModel()

    class _UI:
        def get_model(self):
            return model

        def set_message_source(self, src):
            model.message_source = src

        def set_sandbox_source(self, src):
            model.sandbox_source = src

    session = types.SimpleNamespace(
        messages=[{"role": "system", "content": "x"}],
        on=lambda *a, **k: None,
        off=lambda *a, **k: None,
    )
    _register_session_handlers(session, None, {}, _UI())
    assert callable(model.sandbox_refresher)
    assert model.sandbox_source is not None
    assert model.message_source is not None
    # 无全局沙盒管理器时刷新器安全返回 False（不抛异常）
    assert model.sandbox_refresher() is False


def test_open_changes_ui_without_chat_ui(monkeypatch):
    from src.core.commands import _sandbox_cmd

    monkeypatch.setattr(_sandbox_cmd, "get_sandbox_manager", lambda: None)
    import types

    assert _sandbox_cmd.open_changes_ui(types.SimpleNamespace()) is False
    assert _sandbox_cmd.open_sandbox_ui(types.SimpleNamespace()) is False


# ── sandbox_common 纯辅助 ─────────────────────────────


def test_entry_sort_key_and_label():
    from src.tui.app.sandbox_common import SORT_MODES, entry_sort_key, sort_mode_label

    entries = [
        {"path": "b", "records": 1, "mtime": 5.0, "last_index": 9},
        {"path": "a", "records": 3, "mtime": 1.0, "last_index": 2},
    ]
    assert [e["path"] for e in entry_sort_key(entries, "path")] == ["a", "b"]
    assert [e["path"] for e in entry_sort_key(entries, "records")] == ["a", "b"]
    assert [e["path"] for e in entry_sort_key(entries, "recent")] == ["b", "a"]
    assert [e["path"] for e in entry_sort_key(entries, "index")] == ["a", "b"]
    assert len(SORT_MODES) == 4
    for mode in SORT_MODES:
        assert sort_mode_label(mode)


def test_diff_rows_and_line_delta():
    from src.tui.app.sandbox_common import diff_rows, line_delta

    rows = diff_rows("a.py", "old\n", "new\n", 80)
    assert rows
    same = diff_rows("a.py", "x", "x", 80)
    assert "无变化" in "".join(r.text for r in same[0])
    assert line_delta(None, "a\nb\n") == "+2 行"
    assert line_delta("a\nb\n", None) == "-2 行"
    assert line_delta("a\n", "a\nb\n") == "-1/+2"


def test_history_rows_and_stats_rows():
    from src.tui.app.sandbox_common import history_rows, stats_rows

    entry = {
        "path": "a.py", "change_label": "修改", "records": 1,
        "history": [{
            "seq": 1, "path": "a.py", "change_label": "修改",
            "tool": "write_file", "message_index": 3, "time": 0.0,
            "before": "old\n", "after": "new\n",
        }],
    }
    rows = history_rows(entry, 80)
    assert rows
    assert history_rows(None, 80)
    srows = stats_rows([{"title": "T", "rows": [("l", "v", "info", None)]}], 80)
    assert srows
    assert stats_rows([], 80)


def test_message_detail_rows_and_change_tag_style():
    from src.tui.app.sandbox_common import change_tag_style, fmt_time, message_detail_rows

    entry = {
        "index": 5, "count": 1, "files": 1, "time": 0.0, "tools": ["write_file"],
        "changes": [{
            "path": "a.py", "change_label": "新建", "tool": "write_file",
            "before": None, "after": "x\n",
        }],
    }
    rows = message_detail_rows(entry, 80)
    assert rows
    assert message_detail_rows(None, 80)
    assert change_tag_style("新建") is not None
    assert change_tag_style("删除") is not None
    assert fmt_time(0.0) != ""
    assert fmt_time("bad") == ""


# ── sandbox_export ────────────────────────────────────


def test_sandbox_export_markdown_json_and_write(tmp_path):
    from src.tui.app.sandbox_export import (
        entries_to_json,
        entries_to_markdown,
        export_filename,
        unified_diff_text,
        write_export,
    )

    entries = [{
        "path": "a.py", "change_label": "修改", "before": "old\n", "after": "new\n",
        "records": 1, "message_index": "3-3", "tools": ["write_file"],
    }]
    md = entries_to_markdown(entries, when=0)
    assert "a.py" in md and "```diff" in md
    js = entries_to_json(entries, when=0)
    assert "a.py" in js
    assert export_filename("md", when=0).startswith("sandbox-changes-")
    path = write_export(entries, "json", directory=str(tmp_path), when=0)
    assert path.endswith(".json")
    assert unified_diff_text("a.py", "old", "new")
    assert unified_diff_text("a.py", "", "") == ""


# ── changes_view 辅助 ────────────────────────────────


def test_change_and_message_search_text():
    from src.tui.app.changes_view import _change_search_text, _message_search_text

    assert "a.py" in _change_search_text({"path": "a.py", "change_label": "修改", "tools": ["bash"]})
    assert "bash" in _change_search_text({"path": "a.py", "tools": ["bash"]})
    assert _change_search_text(None) == ""
    assert "消息 3" in _message_search_text({"index": 3, "file_paths": ["a"], "tools": []})
    assert _message_search_text(None) == ""


def test_type_match():
    from src.tui.app.changes_view import _type_match

    assert _type_match({"change_label": "新建"}, "") is True
    assert _type_match({"change_label": "新建"}, "新建") is True
    assert _type_match({"change_label": "修改"}, "新建") is False
    assert _type_match({"is_dir": True}, "目录") is True
    assert _type_match({"is_dir": False}, "目录") is False


def test_tree_items_groups_and_index():
    from src.tui.app.changes_view import _tree_items

    ordered = [
        {"path": "pkg/a.py", "change_label": "修改"},
        {"path": "pkg/sub/b.py", "change_label": "新建"},
    ]
    index_map = [0, 1]
    items = _tree_items(ordered, index_map)
    groups = [it for it in items if it.get("_group")]
    files = [it for it in items if not it.get("_group")]
    assert len(groups) == 2  # pkg 与 pkg/sub
    assert len(files) == 2
    assert {f["_index"] for f in files} == {0, 1}


def test_detail_and_preview_rows():
    from src.tui.app.changes_view import _detail_rows, _preview_rows

    entry = {
        "path": "a.py", "change_label": "修改", "before": "old\n", "after": "new\n",
        "records": 1, "message_index": "3-3", "tools": ["write_file"],
    }
    assert _detail_rows(entry, 60)
    assert _detail_rows(None, 60)
    preview = _preview_rows(entry, 60)
    assert preview
    removed = _preview_rows({"path": "a.py", "before": None, "after": "x"}, 60)
    assert any("删除" in r.text for row in removed for r in row)


# ── 注册 / 状态类型 / 桥接 ────────────────────────────


def test_sandbox_views_registered():
    from src.tui.app.view_registry import active_view_ids, fullscreen_views

    for vid in ("changes", "sandbox", "sandbox_history", "sandbox_records"):
        assert vid in active_view_ids()
        assert vid in fullscreen_views()


def test_manifest_declares_sandbox_views_and_command():
    from src.plugins.manifest import COMMAND_PLUGIN_ENTRIES, UI_VIEW_ENTRIES

    view_ids = [e["config"]["id"] for e in UI_VIEW_ENTRIES]
    for vid in ("sandbox", "sandbox_history", "sandbox_records"):
        assert vid in view_ids
    names = [e["config"]["name"] for e in COMMAND_PLUGIN_ENTRIES]
    assert "sandbox" in names


def test_sandbox_state_types_defaults():
    from src.tui.app._state_types import (
        ChangesViewState,
        SandboxHistoryViewState,
        SandboxRecordsViewState,
        SandboxStatsViewState,
    )

    assert ChangesViewState().view_mode == "file"
    assert ChangesViewState().detail_mode == "diff"
    assert SandboxStatsViewState().clear_confirm is False
    assert SandboxHistoryViewState().restore_confirm == -1
    assert SandboxRecordsViewState().sort_desc is False


def test_ui_runtime_sandbox_state_cls():
    from src.core.adapters.ui_runtime import (
        get_sandbox_history_view_state_cls,
        get_sandbox_records_view_state_cls,
        get_sandbox_stats_view_state_cls,
    )
    from src.tui.app._state_types import (
        SandboxHistoryViewState,
        SandboxRecordsViewState,
        SandboxStatsViewState,
    )

    assert get_sandbox_stats_view_state_cls() is SandboxStatsViewState
    assert get_sandbox_history_view_state_cls() is SandboxHistoryViewState
    assert get_sandbox_records_view_state_cls() is SandboxRecordsViewState


def test_model_has_sandbox_view_states_and_refresher():
    from src.tui.app.model import AppModel
    from src.tui.app._state_types import SandboxStatsViewState

    model = AppModel()
    assert isinstance(model.sandbox_view, SandboxStatsViewState)
    assert model.sandbox_refresher is None
    assert model.sandbox_source is None
