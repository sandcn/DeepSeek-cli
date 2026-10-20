"""``/load`` 补全候选排序 — 按会话文件修改时间降序（最新在前）。

用户需求（2026-10）：``/load`` 补全对应的会话文件按**修改时间**排序；
输入前缀只做过滤，不改变顺序（不再按 sid 精确/前缀/子串语义分组）。
"""

from __future__ import annotations

import json
import os

import pytest

import src.chat_msgs as cm
from src.tui._completion_engine import CompletionEngine


def _engine(sessions):
    eng = CompletionEngine(commands_source=lambda: ["/help", "/load"])
    eng.register_source("sessions", lambda: list(sessions))
    return eng


def _session(sid: str, title: str = "", mtime_ns: int | None = None) -> dict:
    data = {"id": sid, "title": title}
    if mtime_ns is not None:
        data["mtime_ns"] = mtime_ns
    return data


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """把 ``chat_msgs.CHAT_MSGS_DIR`` 指向临时目录并清空进程级缓存。"""
    msgs_dir = tmp_path / "msg_list"
    msgs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cm, "CHAT_MSGS_DIR", msgs_dir)
    monkeypatch.setattr(cm, "ensure_chat_msgs_dir", lambda: None)
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    try:
        yield msgs_dir
    finally:
        cm._invalidate_session_cache()
        cm._session_cache_mtime = 0.0


def test_load_completion_sorted_by_mtime_desc():
    """无前缀时按文件修改时间降序（最新在前），与输入顺序无关。"""
    eng = _engine([
        _session("old", "旧", 1_000),
        _session("newest", "最新", 3_000),
        _session("mid", "中间", 2_000),
    ])
    items = eng.complete("/load ")
    assert [i.text for i in items] == [
        "/load newest", "/load mid", "/load old",
    ]
    assert all(i.item_type == "session" for i in items)


def test_load_completion_prefix_only_filters_keeps_mtime_order():
    """前缀只过滤：精确 sid 命中不再抢占首位（旧语义分组排序已移除）。"""
    eng = _engine([
        _session("al", "精确", 1_000),
        _session("alpha", "a", 3_000),
        _session("alpine", "b", 2_000),
        _session("zzz", "alpha 标题", 4_000),
    ])
    items = eng.complete("/load al")
    assert [i.text for i in items] == ["zzz", "alpha", "alpine", "al"]


def test_load_completion_prefix_filters_out_non_matches():
    eng = _engine([
        _session("alpha", "", 10),
        _session("beta", "", 20),
    ])
    assert [i.text for i in eng.complete("/load be")] == ["beta"]


def test_load_completion_missing_mtime_is_deterministic():
    """外部数据源无 mtime → 同键兜底按 sid 升序，顺序稳定可复现。"""
    eng = _engine([_session("b"), _session("a")])
    assert [i.text for i in eng.complete("/load ")] == ["/load a", "/load b"]


def test_load_completion_orders_real_session_files_by_mtime(session_dir):
    """端到端：真实会话文件的 ``st_mtime_ns`` 决定候选顺序。"""
    base = 1_700_000_000_000_000_000
    for sid, mtime_ns, saved_at in (
        ("older", base, "2026-09-01T00:00:00"),
        ("newer", base + 10**9, "2026-08-01T00:00:00"),
    ):
        path = session_dir / f"{sid}.json"
        path.write_text(
            json.dumps({
                "id": sid,
                "title": sid,
                "model": "m",
                "saved_at": saved_at,
                "messages": [{"role": "user", "content": "hi"}],
                "subagents": [],
            }),
            encoding="utf-8",
        )
        os.utime(path, ns=(mtime_ns, mtime_ns))
    # older 的 saved_at 更「新」也不影响顺序——排序键只看文件修改时间。
    eng = CompletionEngine(commands_source=lambda: ["/load"])
    assert [i.text for i in eng.complete("/load ")] == [
        "/load newer", "/load older",
    ]
