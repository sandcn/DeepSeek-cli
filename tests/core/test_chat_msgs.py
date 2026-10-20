"""src/chat_msgs — 会话消息工具/持久化模块单元测试。

覆盖：
  - _validate_session_id（路径遍历防护、.json 后缀剥离）
  - generate_id（唯一、十六进制）
  - get_recover_cmd
  - save_session（system 过滤、标题策略、subagents、文件写入）
  - load_session（正常/缺失/损坏/非法 ID、subagents 归一化）
  - list_sessions（摘要提取、排序、缓存、不可读/损坏文件跳过）
  - delete_session / rename_session / export_session
所有文件 IO 重定向到临时目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import src.chat_msgs as cm
from src.chat_msgs import (
    ExportError,
    _validate_session_id,
    delete_session,
    export_session,
    generate_id,
    get_recover_cmd,
    list_sessions,
    load_session,
    rename_session,
    save_session,
)


# ── 基础工具（无文件 IO）─────────────────────────────────

def test_validate_session_id_valid():
    assert _validate_session_id("abc123") == "abc123"
    assert _validate_session_id("my-session_1") == "my-session_1"


def test_validate_session_id_strips_json():
    assert _validate_session_id("abc.json") == "abc"


def test_validate_session_id_path_traversal():
    assert _validate_session_id("../etc/passwd") is None
    assert _validate_session_id("a/b") is None
    assert _validate_session_id("a b") is None


def test_validate_session_id_empty():
    assert _validate_session_id("") is None
    assert _validate_session_id(".json") is None


def test_generate_id_hex():
    sid = generate_id()
    assert len(sid) == 32
    int(sid, 16)  # 十六进制


def test_generate_id_unique():
    assert generate_id() != generate_id()


def test_get_recover_cmd():
    assert get_recover_cmd("sid123") == "python chat.py --load sid123"


def test_get_recover_cmd_custom_script():
    assert get_recover_cmd("sid", script="run.py") == "python run.py --load sid"


# ── 文件 IO 隔离夹具 ─────────────────────────────────────

@pytest.fixture(autouse=True)
def msgs_dir(tmp_path: Path, monkeypatch):
    """将 CHAT_MSGS_DIR 指向临时目录并禁用外部副作用。"""
    msgs_dir = tmp_path / "msg_list"
    msgs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cm, "CHAT_MSGS_DIR", msgs_dir)
    monkeypatch.setattr(cm, "ensure_chat_msgs_dir", lambda: None)
    monkeypatch.setattr(cm, "get_token_stats", lambda: {"input": 10, "output": 20, "total": 30})
    monkeypatch.setattr(cm, "_sync_terminal_title", lambda title: None)
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    return msgs_dir


def _write_session(msgs_dir: Path, sid: str, **overrides):
    data = {
        "id": sid,
        "title": "默认标题",
        "model": "deepseek-chat",
        "saved_at": "2026-08-01T00:00:00",
        "token_stats": {"input": 1, "output": 2},
        "messages": [{"role": "user", "content": "hello"}],
        "subagents": [],
    }
    data.update(overrides)
    (msgs_dir / f"{sid}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


# ── save_session ─────────────────────────────────────────

def test_save_session_filters_system_messages(msgs_dir):
    sid = save_session([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "content": "result"},
    ], "m")
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant", "tool"]


def test_save_session_auto_title_from_first_user(msgs_dir):
    sid = save_session([{"role": "user", "content": "帮我写个测试"}], "m")
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["title"] == "帮我写个测试"


def test_save_session_title_truncated_40(msgs_dir):
    content = "x" * 60
    sid = save_session([{"role": "user", "content": content}], "m")
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["title"] == "x" * 40 + "…"


def test_save_session_preserves_existing_title(msgs_dir):
    _write_session(msgs_dir, "sid1", title="用户重命名")
    sid = save_session([{"role": "user", "content": "新消息"}], "m", session_id="sid1")
    assert sid == "sid1"
    data = json.loads((msgs_dir / "sid1.json").read_text(encoding="utf-8"))
    assert data["title"] == "用户重命名"  # 保留，不覆盖


def test_save_session_subagents(msgs_dir):
    sid = save_session([{"role": "user", "content": "hi"}], "m",
                       subagents=[{"id": "s1", "name": "子代理"}])
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["subagents"] == [{"id": "s1", "name": "子代理"}]


def test_save_session_specified_id(msgs_dir):
    sid = save_session([{"role": "user", "content": "hi"}], "m", session_id="custom-1")
    assert sid == "custom-1"
    assert (msgs_dir / "custom-1.json").exists()


def test_save_session_write_error_raises(msgs_dir, monkeypatch):
    monkeypatch.setattr(cm.Path, "write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("readonly")))
    with pytest.raises(OSError):
        save_session([{"role": "user", "content": "hi"}], "m")


def test_save_session_atomic_no_temp_file_left(msgs_dir):
    """成功保存后不残留临时文件（原子写使用隐藏临时名）。"""
    sid = save_session([{"role": "user", "content": "hi"}], "m")
    assert (msgs_dir / f"{sid}.json").exists()
    assert not list(msgs_dir.glob("*.tmp"))
    assert not [p for p in msgs_dir.iterdir() if ".tmp" in p.name]


def test_save_session_atomic_keeps_old_file_on_replace_failure(msgs_dir, monkeypatch):
    """替换失败时旧会话文件保持不变（不会被截断）。"""
    _write_session(msgs_dir, "atomic1", title="旧内容")
    old = (msgs_dir / "atomic1.json").read_text(encoding="utf-8")

    def _fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(cm.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        save_session(
            [{"role": "user", "content": "新内容"}], "m", session_id="atomic1",
        )
    assert (msgs_dir / "atomic1.json").read_text(encoding="utf-8") == old
    assert not [p for p in msgs_dir.iterdir() if ".tmp" in p.name]


def test_rename_session_atomic_keeps_old_file_on_replace_failure(msgs_dir, monkeypatch):
    """重命名替换失败时返回 False 且旧文件保持不变。"""
    _write_session(msgs_dir, "atomic2", title="旧标题")
    old = (msgs_dir / "atomic2.json").read_text(encoding="utf-8")

    def _fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(cm.os, "replace", _fail_replace)
    assert rename_session("atomic2", "新标题") is False
    assert (msgs_dir / "atomic2.json").read_text(encoding="utf-8") == old
    assert not [p for p in msgs_dir.iterdir() if ".tmp" in p.name]


# ── load_session ─────────────────────────────────────────

def test_load_session_roundtrip(msgs_dir):
    sid = save_session([{"role": "user", "content": "hi"}], "m")
    data = load_session(sid)
    assert data["id"] == sid
    assert data["model"] == "m"


def test_load_session_without_json_suffix(msgs_dir):
    _write_session(msgs_dir, "sid2")
    assert load_session("sid2.json")["id"] == "sid2"


def test_load_session_missing_returns_none(msgs_dir):
    assert load_session("nonexistent") is None


def test_load_session_corrupt_returns_none(msgs_dir):
    (msgs_dir / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_session("bad") is None


def test_load_session_invalid_id_returns_none(msgs_dir):
    assert load_session("../evil") is None


def test_load_session_normalizes_missing_subagents(msgs_dir):
    _write_session(msgs_dir, "old", subagents=None)
    data = json.loads((msgs_dir / "old.json").read_text(encoding="utf-8"))
    del data["subagents"]  # 模拟旧会话文件
    (msgs_dir / "old.json").write_text(json.dumps(data), encoding="utf-8")
    loaded = load_session("old")
    assert loaded["subagents"] == []


# ── list_sessions ────────────────────────────────────────

def test_list_sessions_returns_summaries(msgs_dir):
    _write_session(msgs_dir, "a", saved_at="2026-08-02T00:00:00", model="m1")
    _write_session(msgs_dir, "b", saved_at="2026-08-03T00:00:00", model="m2",
                   messages=[{"role": "user", "content": "任务B"}])
    sessions = list_sessions()
    assert len(sessions) == 2
    # 按 saved_at 降序：b 在前
    assert sessions[0]["id"] == "b"
    assert sessions[0]["model"] == "m2"
    assert sessions[0]["message_count"] == 1
    assert sessions[1]["id"] == "a"


def test_list_sessions_empty_dir(msgs_dir):
    assert list_sessions() == []


def test_list_sessions_skips_unreadable(msgs_dir, monkeypatch):
    _write_session(msgs_dir, "ok")
    _write_session(msgs_dir, "hidden")
    monkeypatch.setattr(cm.os, "access", lambda path, mode: Path(path).name != "hidden.json")
    sessions = list_sessions()
    assert [s["id"] for s in sessions] == ["ok"]


def test_list_sessions_skips_corrupt(msgs_dir):
    _write_session(msgs_dir, "good")
    (msgs_dir / "bad.json").write_text("{corrupt", encoding="utf-8")
    sessions = list_sessions()
    assert [s["id"] for s in sessions] == ["good"]


def test_list_sessions_title_fallback(msgs_dir):
    _write_session(msgs_dir, "legacy", title=None)
    data = json.loads((msgs_dir / "legacy.json").read_text(encoding="utf-8"))
    del data["title"]
    (msgs_dir / "legacy.json").write_text(json.dumps(data), encoding="utf-8")
    sessions = list_sessions()
    assert sessions[0]["title"] == "hello"


def test_list_sessions_cache(msgs_dir):
    _write_session(msgs_dir, "a")
    sessions1 = list_sessions()
    # 新增文件后，缓存未过期 → 仍返回旧列表
    _write_session(msgs_dir, "b", saved_at="2026-08-04T00:00:00")
    sessions2 = list_sessions()
    assert [s["id"] for s in sessions1] == [s["id"] for s in sessions2]
    # 缓存过期后重新读取
    cm._session_cache_mtime = 0.0
    sessions3 = list_sessions()
    assert len(sessions3) == 2


def test_list_sessions_reports_file_mtime_ns(msgs_dir):
    """摘要携带会话文件 ``st_mtime_ns``（``/load`` 补全按它排序）。"""
    _write_session(msgs_dir, "a")
    _write_session(msgs_dir, "b")
    old_ns = 1_700_000_000_000_000_000
    new_ns = 1_700_000_100_000_000_000
    os.utime(msgs_dir / "a.json", ns=(old_ns, old_ns))
    os.utime(msgs_dir / "b.json", ns=(new_ns, new_ns))
    by_id = {s["id"]: s for s in list_sessions()}
    assert by_id["a"]["mtime_ns"] == old_ns
    assert by_id["b"]["mtime_ns"] == new_ns


def test_list_sessions_backfills_mtime_for_legacy_index(msgs_dir):
    """升级前的索引条目无 ``mtime_ns`` → 用 stat 补齐（不必重新解析文件）。"""
    _write_session(msgs_dir, "a")
    expected = (msgs_dir / "a.json").stat().st_mtime_ns
    list_sessions()
    index = json.loads(cm._index_path().read_text(encoding="utf-8"))
    for item in index["files"].values():
        item["entry"].pop("mtime_ns", None)
    cm._index_path().write_text(json.dumps(index), encoding="utf-8")
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    sessions = list_sessions()
    assert sessions[0]["mtime_ns"] == expected


# ── delete_session / rename_session ──────────────────────

def test_delete_session(msgs_dir):
    _write_session(msgs_dir, "delme")
    assert delete_session("delme") is True
    assert not (msgs_dir / "delme.json").exists()


def test_delete_session_missing_returns_false(msgs_dir):
    assert delete_session("nope") is False


def test_delete_session_invalid_id_returns_false(msgs_dir):
    assert delete_session("../evil") is False


def test_rename_session(msgs_dir):
    _write_session(msgs_dir, "r1", title="旧标题")
    assert rename_session("r1", "  新标题  ") is True
    data = json.loads((msgs_dir / "r1.json").read_text(encoding="utf-8"))
    assert data["title"] == "新标题"  # strip 后


def test_rename_session_missing_returns_false(msgs_dir):
    assert rename_session("ghost", "标题") is False


def test_rename_session_corrupt_returns_false(msgs_dir):
    (msgs_dir / "corrupt.json").write_text("bad", encoding="utf-8")
    assert rename_session("corrupt", "标题") is False


# ── export_session ───────────────────────────────────────

def test_export_session_to_stdout(msgs_dir):
    _write_session(msgs_dir, "e1")
    out = export_session("e1")
    assert out is not None
    data = json.loads(out)
    assert data["id"] == "e1"


def test_export_session_missing_returns_none(msgs_dir):
    assert export_session("nope") is None


def test_export_session_to_file(tmp_path, msgs_dir, monkeypatch):
    _write_session(msgs_dir, "e2")
    monkeypatch.chdir(tmp_path)
    out = export_session("e2", output="export.json")
    assert out is not None
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["id"] == "e2"


def test_export_session_outside_cwd_rejected(tmp_path, msgs_dir, monkeypatch):
    """导出目标越出当前目录 → ExportError（修复前误报「未找到会话」）。"""
    _write_session(msgs_dir, "e3")
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "escape.json"
    with pytest.raises(ExportError):
        export_session("e3", output=str(outside))
    assert not outside.exists()


def test_export_session_existing_file_rejected(tmp_path, msgs_dir, monkeypatch):
    """目标文件已存在 → ExportError（不覆盖既有文件）。"""
    _write_session(msgs_dir, "e4")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "exists.json").write_text("occupied", encoding="utf-8")
    with pytest.raises(ExportError):
        export_session("e4", output="exists.json")
    assert (tmp_path / "exists.json").read_text(encoding="utf-8") == "occupied"
