"""会话标题归一化 —— 折行/制表符不得写进标题（列表渲染与终端输出格式回归）。

★ 修复回归（2026-10）：会话标题取自首条用户消息前 40 字符（仅 ``strip()``），
多行输入把 ``\\n`` 原样写入会话文件 —— TUI 会话浏览器左栏与
``chat.py session list`` 按行渲染时被拆行（实测线上 304 个会话中 86 个标题
含换行）。现保存 / 重命名 / 摘要读取统一经 ``chat_msgs.normalize_title``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.chat_msgs as cm
from src.chat_msgs import (
    _session_summary_entry,
    list_sessions,
    normalize_title,
    rename_session,
    save_session,
)


@pytest.fixture(autouse=True)
def msgs_dir(tmp_path: Path, monkeypatch):
    target = tmp_path / "msg_list"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cm, "CHAT_MSGS_DIR", target)
    monkeypatch.setattr(cm, "ensure_chat_msgs_dir", lambda: None)
    monkeypatch.setattr(cm, "get_token_stats", lambda: {"input": 0, "output": 0, "total": 0})
    monkeypatch.setattr(cm, "_sync_terminal_title", lambda title: None)
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    return target


# ── normalize_title ─────────────────────────────────────

def test_normalize_title_collapses_newlines():
    assert normalize_title("第一行\n第二行") == "第一行 第二行"
    assert normalize_title("a\r\nb\tc") == "a b c"
    assert normalize_title("  前后空白  ") == "前后空白"
    assert normalize_title("连续\n\n\n空行") == "连续 空行"


def test_normalize_title_handles_empty():
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


# ── save_session ────────────────────────────────────────

def test_save_session_normalizes_multiline_title(msgs_dir):
    sid = save_session(
        [{"role": "user", "content": "实现我的世界原版所有东西\n不能操作git"}], "m",
    )
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert "\n" not in data["title"]
    assert data["title"] == "实现我的世界原版所有东西 不能操作git"


def test_save_session_multiline_title_truncated_without_newline(msgs_dir):
    content = "行一\n" + "x" * 60
    sid = save_session([{"role": "user", "content": content}], "m")
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert "\n" not in data["title"]
    assert data["title"].endswith("…")


def test_save_session_normalizes_preserved_title(msgs_dir):
    """已有会话的旧标题（含换行）在再次保存时被归一化。"""
    legacy = msgs_dir / "sid1.json"
    legacy.write_text(json.dumps({
        "id": "sid1", "title": "旧\n标题", "model": "m",
        "saved_at": "2026-08-01T00:00:00", "messages": [{"role": "user", "content": "hi"}],
    }, ensure_ascii=False), encoding="utf-8")
    save_session([{"role": "user", "content": "新消息"}], "m", session_id="sid1")
    data = json.loads(legacy.read_text(encoding="utf-8"))
    assert data["title"] == "旧 标题"


# ── rename_session ──────────────────────────────────────

def test_rename_session_normalizes_newlines(msgs_dir):
    save_session([{"role": "user", "content": "hi"}], "m", session_id="r1")
    assert rename_session("r1", "新\n标题\t带制表") is True
    data = json.loads((msgs_dir / "r1.json").read_text(encoding="utf-8"))
    assert data["title"] == "新 标题 带制表"


# ── 摘要读取（旧数据防御） ──────────────────────────────

def test_session_summary_entry_normalizes_legacy_title():
    entry = _session_summary_entry(
        {"id": "x", "title": "多行\n标题", "model": "m", "saved_at": "s",
         "message_count": 1},
        Path("x.json"),
    )
    assert entry["title"] == "多行 标题"


def test_session_summary_entry_fallback_title_normalized():
    """无 title 字段 → 从首条 user 消息提取，同样归一化。"""
    entry = _session_summary_entry(
        {"id": "x", "model": "m", "saved_at": "s", "message_count": 1,
         "messages": [{"role": "user", "content": "第一行\n第二行"}]},
        Path("x.json"),
    )
    assert entry["title"] == "第一行 第二行"


def test_list_sessions_titles_are_single_line(msgs_dir):
    """列表输出的每个标题都是单行（终端列表格式不再被破坏）。"""
    sid = save_session([{"role": "user", "content": "多行\n输入\n标题"}], "m")
    sessions = list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert "\n" not in sessions[0]["title"]
