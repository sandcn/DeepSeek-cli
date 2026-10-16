"""src/checkpoint — 任务断点保存/加载/清除/摘要 全量单元测试。

覆盖：
  - save_checkpoint（显式/自动提取任务描述、目录创建、OSError 容错）
  - load_checkpoint（正常/文件缺失/JSON 损坏）
  - clear_checkpoint / has_checkpoint
  - get_checkpoint_info（摘要字段、saved_at 缺失兜底）
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import src.checkpoint as cp_mod
from src.checkpoint import (
    clear_checkpoint,
    get_checkpoint_info,
    has_checkpoint,
    load_checkpoint,
    save_checkpoint,
)


@pytest.fixture
def ckpt_file(tmp_path: Path, monkeypatch):
    """将 CHECKPOINT_FILE 指向临时目录下的文件。"""
    target = tmp_path / ".chat" / "_checkpoint.json"
    monkeypatch.setattr(cp_mod, "CHECKPOINT_FILE", target)
    return target


# ── save_checkpoint ────────────────────────────────────────

def test_save_checkpoint_writes_file(ckpt_file: Path):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    save_checkpoint(messages, model="deepseek-chat")
    assert ckpt_file.exists()
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["model"] == "deepseek-chat"
    assert data["message_count"] == 2
    assert data["messages"] == messages
    assert data["task_description"] == "hello"
    assert "saved_at" in data


def test_save_checkpoint_explicit_task_description(ckpt_file: Path):
    save_checkpoint([{"role": "user", "content": "ignored"}], "m", "我的任务")
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["task_description"] == "我的任务"


def test_save_checkpoint_auto_extract_truncates_200(ckpt_file: Path):
    long_content = "字" * 300
    save_checkpoint([{"role": "user", "content": long_content}], "m")
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["task_description"] == "字" * 200 + "…"


def test_save_checkpoint_no_user_message(ckpt_file: Path):
    save_checkpoint([{"role": "assistant", "content": "hi"}], "m")
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["task_description"] == ""


def test_save_checkpoint_last_user_message_wins(ckpt_file: Path):
    save_checkpoint(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "mid"},
            {"role": "user", "content": "last"},
        ],
        "m",
    )
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["task_description"] == "last"


def test_save_checkpoint_oserror_is_silent(ckpt_file: Path, monkeypatch):
    """写文件抛 OSError 时静默记日志，不向上抛。"""

    class _BoomPath(type(ckpt_file)):
        def write_text(self, *a, **k):
            raise OSError("disk full")

    monkeypatch.setattr(cp_mod, "CHECKPOINT_FILE", _BoomPath(ckpt_file))
    save_checkpoint([{"role": "user", "content": "x"}], "m")  # 不应抛异常


# ── load_checkpoint ────────────────────────────────────────

def test_load_checkpoint_roundtrip(ckpt_file: Path):
    save_checkpoint([{"role": "user", "content": "hi"}], "m", "任务")
    data = load_checkpoint()
    assert data is not None
    assert data["model"] == "m"
    assert data["task_description"] == "任务"


def test_load_checkpoint_missing_returns_none(ckpt_file: Path):
    assert load_checkpoint() is None


def test_load_checkpoint_corrupt_returns_none(ckpt_file: Path):
    ckpt_file.parent.mkdir(parents=True, exist_ok=True)
    ckpt_file.write_text("{not json", encoding="utf-8")
    assert load_checkpoint() is None


# ── clear_checkpoint / has_checkpoint ──────────────────────

def test_clear_checkpoint_removes_file(ckpt_file: Path):
    save_checkpoint([{"role": "user", "content": "x"}], "m")
    assert has_checkpoint()
    clear_checkpoint()
    assert not has_checkpoint()


def test_clear_checkpoint_missing_is_noop(ckpt_file: Path):
    clear_checkpoint()  # 不应抛异常


def test_clear_checkpoint_oserror_silent(ckpt_file: Path, monkeypatch):
    save_checkpoint([{"role": "user", "content": "x"}], "m")

    class _BoomPath(type(ckpt_file)):
        def unlink(self, **k):
            raise OSError("permission denied")

    monkeypatch.setattr(cp_mod, "CHECKPOINT_FILE", _BoomPath(ckpt_file))
    clear_checkpoint()  # 不应抛异常


def test_has_checkpoint_false_when_missing(ckpt_file: Path):
    assert not has_checkpoint()


# ── get_checkpoint_info ────────────────────────────────────

def test_get_checkpoint_info_summary(ckpt_file: Path, monkeypatch):
    save_checkpoint([{"role": "user", "content": "任务内容"}], "deepseek", "任务内容")
    monkeypatch.setattr(cp_mod.time, "time", lambda: 1000.0)
    # 覆写 saved_at 便于断言 elapsed
    raw = json.loads(ckpt_file.read_text(encoding="utf-8"))
    raw["saved_at"] = 940.0
    ckpt_file.write_text(json.dumps(raw), encoding="utf-8")

    info = get_checkpoint_info()
    assert info is not None
    assert info["message_count"] == 1
    assert info["task_description"] == "任务内容"
    assert info["model"] == "deepseek"
    assert info["elapsed_minutes"] == pytest.approx(1.0)


def test_get_checkpoint_info_missing_returns_none(ckpt_file: Path):
    assert get_checkpoint_info() is None


def test_get_checkpoint_info_defaults(ckpt_file: Path):
    """saved_at/message_count/task_description/model 缺失时的兜底。"""
    ckpt_file.parent.mkdir(parents=True, exist_ok=True)
    ckpt_file.write_text(json.dumps({"saved_at": 0}), encoding="utf-8")
    info = get_checkpoint_info()
    assert info is not None
    assert info["message_count"] == 0
    assert info["task_description"] == ""
    assert info["model"] == "?"
    assert info["elapsed_minutes"] >= 0
