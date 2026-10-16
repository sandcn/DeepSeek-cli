"""会话列表读取链路修复 —— 锁外 yield（防死锁）+ 头部快速解析 + 磁盘索引。

★ 修复回归（2026-10）：
  1. ``chat_msgs.iter_sessions`` 缓存命中分支**在锁内 yield** —— 生成器挂起
     期间 ``_session_cache_lock`` 不释放，调用方在迭代中重入任何加锁路径
     （同线程调用 ``list_sessions``）即永久死锁。现改为锁内取快照、锁外
     yield；本文件用**子进程 + 超时**验证（真死锁时不会挂起测试进程）。
  2. ``_load_session_summary`` 只读前 16KB 却直接 ``json.loads`` —— 大于
     16KB 的会话文件 JSON 必然被截断、解析必败（轻量路径从不生效，每次列表
     全量读取）。现改为头部标量字段提取（``_parse_session_head``），并对旧
     格式文件加磁盘索引缓存（``.session_index.json``）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import src.chat_msgs as cm
from src.chat_msgs import list_sessions, save_session

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def msgs_dir(tmp_path: Path, monkeypatch):
    """CHAT_MSGS_DIR 指向临时目录（含索引文件写入隔离）。"""
    target = tmp_path / "msg_list"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cm, "CHAT_MSGS_DIR", target)
    monkeypatch.setattr(cm, "ensure_chat_msgs_dir", lambda: None)
    monkeypatch.setattr(cm, "get_token_stats", lambda: {"input": 0, "output": 0, "total": 0})
    monkeypatch.setattr(cm, "_sync_terminal_title", lambda title: None)
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    return target


def _write_new_format(msgs_dir: Path, sid: str, *, title: str = "标题",
                      messages=None, message_count=None, **extra) -> Path:
    """写一个「新格式」会话文件（标量字段前置，模拟 save_session 布局）。"""
    messages = messages if messages is not None else [{"role": "user", "content": "hi"}]
    data = {
        "id": sid,
        "title": title,
        "model": "deepseek-chat",
        "saved_at": "2026-10-01T00:00:00",
        "message_count": len(messages) if message_count is None else message_count,
        "token_stats": {"input": 1, "output": 2},
        "messages": messages,
        "subagents": [],
        "session_log": [],
    }
    data.update(extra)
    path = msgs_dir / f"{sid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _write_legacy(msgs_dir: Path, sid: str) -> Path:
    """写一个「旧格式」会话文件（无 message_count 前置字段）。"""
    data = {
        "id": sid,
        "title": "旧会话",
        "model": "m",
        "saved_at": "2026-09-01T00:00:00",
        "token_stats": {"input": 0, "output": 0},
        "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        "subagents": [],
    }
    path = msgs_dir / f"{sid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ── 1. 死锁回归（子进程隔离） ────────────────────────────

_DEADLOCK_SCRIPT = textwrap.dedent(
    """
    import json, pathlib, sys, tempfile, threading

    sys.path.insert(0, {repo!r})
    import src.chat_msgs as cm

    tmp = pathlib.Path(tempfile.mkdtemp()) / "msg_list"
    tmp.mkdir(parents=True, exist_ok=True)
    cm.CHAT_MSGS_DIR = tmp
    cm.ensure_chat_msgs_dir = lambda: None
    (tmp / "a.json").write_text(json.dumps({{
        "id": "a", "title": "t", "model": "m", "saved_at": "s",
        "message_count": 1, "token_stats": {{}}, "messages": [],
    }}), encoding="utf-8")

    cm.list_sessions()  # 预热缓存（TTL 内命中「持锁 yield」分支）

    result = {{}}

    def worker():
        try:
            for _item in cm.iter_sessions():
                # 迭代过程中重入会话函数（修复前：同一把不可重入锁 → 死锁）
                cm.list_sessions()
                result["ok"] = True
                break
        except Exception as exc:  # noqa: BLE001
            result["error"] = repr(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():
        print("DEADLOCK")
        sys.exit(2)
    if not result.get("ok"):
        print("FAILED", result)
        sys.exit(3)
    print("OK")
    """
)


def test_iter_sessions_reentrant_without_deadlock():
    """缓存命中路径在迭代中重入 ``list_sessions`` 不得死锁（子进程 + 超时）。"""
    script = _DEADLOCK_SCRIPT.format(repo=str(_REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


# ── 2. 头部快速解析 ─────────────────────────────────────

def test_parse_session_head_extracts_scalar_fields():
    header = (
        '{\n  "id": "abc",\n  "title": "多行\\n标题",\n  "model": "m",\n'
        '  "saved_at": "2026-10-01T00:00:00",\n  "message_count": 12,\n'
        '  "token_stats": {"input": 1},\n  "messages": ['
    )
    head = cm._parse_session_head(header)
    assert head is not None
    assert head["id"] == "abc"
    assert head["title"] == "多行\n标题"
    assert head["message_count"] == 12
    assert head["messages"] == []


def test_parse_session_head_requires_message_count():
    """缺 message_count（旧格式）→ 返回 None（调用方回退全量读取）。"""
    header = '{"id": "a", "title": "t", "token_stats": {}, "messages": ['
    assert cm._parse_session_head(header) is None


def test_parse_session_head_rejects_non_object():
    assert cm._parse_session_head("[1, 2, 3") is None
    assert cm._parse_session_head("") is None


def test_list_sessions_head_fast_path_avoids_full_read(msgs_dir, monkeypatch):
    """新格式大文件（>16KB）经头部解析构建摘要，全程不整读文件。"""
    big = [{"role": "user", "content": "x" * 500} for _ in range(500)]
    _write_new_format(msgs_dir, "big", messages=big)
    assert (msgs_dir / "big.json").stat().st_size > 16384

    reads = {"n": 0}
    real_read_text = Path.read_text

    def _counting(self, *args, **kwargs):
        if self.name == "big.json":
            reads["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _counting)

    sessions = list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "big"
    assert sessions[0]["message_count"] == 500
    # 未整读会话文件（头部扫描走 open/read；索引写入走 write_text）
    assert reads["n"] == 0


def test_list_sessions_legacy_file_full_read(msgs_dir):
    """旧格式文件（无 message_count）回退全量读取并正确统计。"""
    _write_legacy(msgs_dir, "legacy")
    sessions = list_sessions()
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["title"] == "旧会话"


# ── 3. 磁盘索引缓存 ─────────────────────────────────────

def test_index_cache_skips_reparse_on_second_scan(msgs_dir, monkeypatch):
    """第二次扫描命中的磁盘索引，不再解析文件（替换 _load_session_summary 为炸弹）。"""
    _write_legacy(msgs_dir, "legacy")
    first = list_sessions()
    assert first[0]["message_count"] == 2
    assert cm._index_path().exists()

    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0

    def _boom(_filepath):  # pragma: no cover - 命中索引时不应被调用
        raise AssertionError("索引命中后仍解析了会话文件")

    monkeypatch.setattr(cm, "_load_session_summary", _boom)
    second = list_sessions()
    assert second[0]["message_count"] == 2


def test_index_invalidated_when_file_changes(msgs_dir, monkeypatch):
    """文件大小/mtime 变化后索引失效 → 重新解析（内容更新可见）。"""
    _write_legacy(msgs_dir, "legacy")
    assert list_sessions()[0]["message_count"] == 2

    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    _write_new_format(msgs_dir, "legacy", messages=[{"role": "user", "content": "z"}] * 7)
    sessions = list_sessions()
    assert sessions[0]["message_count"] == 7


def test_index_corrupt_file_falls_back(msgs_dir):
    """索引文件损坏 → 退化为常规扫描（不抛异常、结果正确）。"""
    _write_legacy(msgs_dir, "legacy")
    cm._index_path().write_text("{ not json", encoding="utf-8")
    sessions = list_sessions()
    assert sessions[0]["message_count"] == 2


def test_index_not_globbed_as_session(msgs_dir):
    """索引文件（``.session_index.json``）不得被当成会话文件列出。"""
    _write_legacy(msgs_dir, "legacy")
    list_sessions()  # 触发索引写盘
    assert cm._index_path().exists()
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    assert [s["id"] for s in list_sessions()] == ["legacy"]


def test_save_session_writes_front_message_count(msgs_dir):
    """``save_session`` 把 message_count 写在复合字段之前（供头部快速读取）。"""
    sid = save_session(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
        "m",
    )
    raw = (msgs_dir / f"{sid}.json").read_text(encoding="utf-8")
    assert raw.index('"message_count"') < raw.index('"token_stats"')
    assert raw.index('"message_count"') < raw.index('"messages"')
    assert cm._parse_session_head(raw)["message_count"] == 1


# ── 5. 预览轻量读取（会话浏览器首屏） ─────────────────────

def test_load_session_head_messages_small_file_matches_full_read(msgs_dir):
    from src.chat_msgs import load_session, load_session_head_messages

    messages = [
        {"role": "user", "content": "一"},
        {"role": "assistant", "content": "二"},
        {"role": "user", "content": "三"},
    ]
    _write_new_format(msgs_dir, "pre", messages=messages)

    head = load_session_head_messages("pre", 2)
    assert head == messages[:2]
    assert load_session("pre")["messages"] == messages


def test_load_session_head_messages_large_file_reads_head_only(msgs_dir, monkeypatch):
    """大文件只读头部（不整读）即可取到前若干条消息。"""
    from src.chat_msgs import load_session_head_messages

    messages = [{"role": "user", "content": "y" * 2000} for _ in range(300)]
    path = _write_new_format(msgs_dir, "huge", messages=messages)
    assert path.stat().st_size > 262144

    reads = {"n": 0}
    real_read_text = Path.read_text

    def _counting(self, *args, **kwargs):
        if self.name == "huge.json":
            reads["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _counting)

    head = load_session_head_messages("huge", 3)
    assert head is not None
    assert len(head) == 3
    assert head[0]["content"] == "y" * 2000
    assert reads["n"] == 0  # 未整读文件


def test_load_session_head_messages_single_element_over_scan_returns_none(msgs_dir):
    """单条消息超出扫描上限 → None（调用方回退全量读取）。"""
    from src.chat_msgs import load_session_head_messages

    messages = [{"role": "user", "content": "z" * 400000}]
    _write_new_format(msgs_dir, "fat", messages=messages)
    assert load_session_head_messages("fat", 1) is None


def test_load_session_head_messages_missing_and_invalid(msgs_dir):
    from src.chat_msgs import load_session_head_messages

    assert load_session_head_messages("nope", 5) is None
    assert load_session_head_messages("../evil", 5) is None
    assert load_session_head_messages("nope", 0) is None


def test_head_messages_ignores_messages_like_text_in_title(msgs_dir):
    """标题里出现 ``"messages"`` 字样不得误导头部解析（只按顶层键匹配）。"""
    from src.chat_msgs import load_session_head_messages

    messages = [{"role": "user", "content": "hi"}]
    _write_new_format(
        msgs_dir, "tricky",
        title='"messages": [{"role": "user", "content": "假"}]',
        messages=messages,
    )
    head = load_session_head_messages("tricky", 5)
    assert head == messages


def test_build_session_entries_prefers_head_loader(msgs_dir, monkeypatch):
    """会话浏览器条目优先走轻量预览（不再整读会话文件）。"""
    from src.core.adapters.persistence import JsonFilePersistence
    from src.core.commands._data_cmd import _build_session_entries

    messages = [{"role": "user", "content": "甲"}, {"role": "assistant", "content": "乙"}]
    _write_new_format(msgs_dir, "p1", messages=messages)

    port = JsonFilePersistence()
    calls = {"head": 0, "load": 0}
    real_head = port.load_session_head_messages

    def _head(session_id, max_messages):
        calls["head"] += 1
        return real_head(session_id, max_messages)

    def _load(session_id):  # pragma: no cover - 命中轻量路径时不应调用
        calls["load"] += 1
        return {"messages": messages}

    monkeypatch.setattr(port, "load_session_head_messages", _head)
    monkeypatch.setattr(port, "load_session", _load)

    entries = _build_session_entries(port)
    assert calls["head"] == 1
    assert calls["load"] == 0
    assert entries[0]["preview_lines"][0][1] == "甲"


def test_build_session_entries_falls_back_without_head_loader(msgs_dir):
    """端口不支持轻量读取 → 回退全量加载（行为不变）。"""
    from src.core.commands._data_cmd import _build_session_entries

    class _Port:
        def __init__(self):
            self.loaded = 0

        def iter_sessions(self):
            yield {"id": "x", "title": "t", "model": "m", "saved_at": "s",
                   "message_count": 1}

        def load_session(self, session_id):
            self.loaded += 1
            return {"messages": [{"role": "user", "content": "兜底"}]}

    port = _Port()
    entries = _build_session_entries(port)
    assert port.loaded == 1
    assert entries[0]["preview_lines"][0][1] == "兜底"
