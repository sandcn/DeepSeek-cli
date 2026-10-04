"""LoggedMessageList 的 list 兼容性回归测试。

回归背景：LoggedMessageList 曾继承 ``collections.abc.MutableSequence``
（不是 ``list`` 子类），导致：

- ``json.dumps(agent.messages)`` 抛
  ``TypeError: Object of type LoggedMessageList is not JSON serializable``；
- ``save_checkpoint`` 每轮失败并打印
  ``_handle_regular_msg: save_checkpoint 异常，不阻断消息处理``；
- 依赖 ``isinstance(messages, list)`` 的代码（图片上传优化、轨迹指纹）失效。

本类契约是「与 list 可互换」，故须为 ``list`` 子类。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.checkpoint as cp_mod
from src.checkpoint import save_checkpoint
from src.core.session_log import LoggedMessageList, SessionLog


@pytest.fixture
def ckpt_file(tmp_path: Path, monkeypatch):
    target = tmp_path / ".chat" / "_checkpoint.json"
    monkeypatch.setattr(cp_mod, "CHECKPOINT_FILE", target)
    return target


def _view(messages):
    return LoggedMessageList(SessionLog(), initial=messages)


# ── list 兼容性 ──────────────────────────────────────────


def test_isinstance_list_true():
    assert isinstance(_view([]), list)


def test_json_dumps_serializes_directly():
    view = _view([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ])
    dumped = json.dumps({"messages": view}, ensure_ascii=False)
    assert json.loads(dumped)["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


def test_equality_with_plain_list():
    view = _view([{"role": "user", "content": "a"}])
    assert view == [{"role": "user", "content": "a"}]
    assert [{"role": "user", "content": "a"}] == view


def test_concat_returns_list():
    view = _view([{"role": "user", "content": "a"}])
    merged = view + [{"role": "user", "content": "b"}]
    assert isinstance(merged, list)
    assert [m["content"] for m in merged] == ["a", "b"]


def test_list_copy_is_plain_list():
    view = _view([{"role": "user", "content": "a"}])
    assert type(view.copy()) is list
    assert type(view.to_list()) is list
    assert type(list(view)) is list


# ── checkpoint 端到端（原报错路径） ───────────────────────


def test_save_checkpoint_accepts_logged_message_list(ckpt_file: Path):
    view = _view([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ])
    save_checkpoint(view, "deepseek-flash")  # 回归前抛 TypeError
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["model"] == "deepseek-flash"
    assert data["message_count"] == 2
    assert data["task_description"] == "hello"
    assert data["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]


# ── 结构变更后与日志保持一致 ─────────────────────────────


def test_iadd_syncs_log():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[{"role": "user", "content": "a"}])
    view += [{"role": "user", "content": "b"}]
    assert list(view) == [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    assert view.verify() is True


def test_imul_syncs_log():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[{"role": "user", "content": "a"}])
    view *= 2
    assert list(view) == [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "a"},
    ]
    assert view.verify() is True


def test_sort_syncs_log():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[
        {"role": "user", "content": "b"},
        {"role": "user", "content": "a"},
    ])
    view.sort(key=lambda m: m["content"])
    assert [m["content"] for m in view] == ["a", "b"]
    assert view.verify() is True


def test_reverse_syncs_log():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
    ])
    view.reverse()
    assert [m["content"] for m in view] == ["b", "a"]
    assert view.verify() is True


def test_remove_syncs_log():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
    ])
    view.remove({"role": "user", "content": "a"})
    assert list(view) == [{"role": "user", "content": "b"}]
    assert view.verify() is True


def test_clear_keeps_system_and_syncs_log():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
    ])
    view.clear()
    assert list(view) == [{"role": "system", "content": "sys"}]
    assert view.verify() is True
