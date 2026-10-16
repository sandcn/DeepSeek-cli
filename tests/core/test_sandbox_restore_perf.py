"""沙盒恢复 I/O 优化（editmsg 回车卡顿修复）回归测试。

覆盖 ``_FileHistory.restore`` 的两项降 I/O 改动：
- 磁盘内容已等于目标快照的文件跳过写入（不调用 ``atomic_write_text``）；
- 去掉冗余备份（不再 mkstemp + copy2 整份备份），恢复后目录无残留临时文件。

同时校验辅助函数 ``_read_text_if_file`` 的边界（目录 / 符号链接 / 不存在
一律返回 None，使调用方按「需恢复」处理，不误跳过）。
"""

from __future__ import annotations

import os

import pytest

from src.core.internal.shared import _sandbox_history as hist_mod
from src.core.internal.shared._sandbox_history import _FileHistory, _read_text_if_file


def _record(fh, path, before, after, idx=1):
    fh.record(path, before, after, idx, "write_file", "file")


# ── _read_text_if_file 边界 ────────────────────────────────

def test_read_text_if_file_reads_plain_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    assert _read_text_if_file(str(p)) == "hello"


def test_read_text_if_file_none_for_missing_and_directory(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    assert _read_text_if_file(str(d)) is None
    assert _read_text_if_file(str(tmp_path / "missing.txt")) is None


def test_read_text_if_file_none_for_symlink(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("same", encoding="utf-8")
    link = tmp_path / "l.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("平台不支持符号链接")
    assert _read_text_if_file(str(link)) is None


# ── restore：跳过无变化文件 ────────────────────────────────

def test_restore_skips_write_when_content_equals_target(tmp_path, monkeypatch):
    p = tmp_path / "a.txt"
    p.write_text("same", encoding="utf-8")
    fh = _FileHistory()
    _record(fh, str(p), "same", "changed", idx=1)

    calls = []
    monkeypatch.setattr(
        hist_mod, "atomic_write_text", lambda *a, **k: calls.append(a),
    )

    results = fh.restore(0)
    assert results[str(p)] is True
    assert calls == []
    assert p.read_text(encoding="utf-8") == "same"


def test_restore_writes_when_content_differs(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("current", encoding="utf-8")
    fh = _FileHistory()
    _record(fh, str(p), "target", "other", idx=1)

    results = fh.restore(0)
    assert results[str(p)] is True
    assert p.read_text(encoding="utf-8") == "target"


def test_restore_does_not_skip_symlink_even_if_content_matches(tmp_path, monkeypatch):
    target = tmp_path / "t.txt"
    target.write_text("same", encoding="utf-8")
    link = tmp_path / "l.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("平台不支持符号链接")

    fh = _FileHistory()
    _record(fh, str(link), "same", "changed", idx=1)

    calls = []
    monkeypatch.setattr(
        hist_mod, "atomic_write_text", lambda *a, **k: calls.append(a),
    )
    results = fh.restore(0)
    assert results[str(link)] is True
    assert len(calls) == 1


# ── restore：无冗余备份残留 ────────────────────────────────

def test_restore_leaves_no_backup_or_temp_files(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("current", encoding="utf-8")
    fh = _FileHistory()
    _record(fh, str(p), "target", "other", idx=1)

    fh.restore(0)
    assert p.read_text(encoding="utf-8") == "target"
    assert [n for n in os.listdir(tmp_path) if n != "a.txt"] == []


def test_restore_multi_file_mixed_skip_and_write(tmp_path):
    same = tmp_path / "same.txt"
    same.write_text("keep", encoding="utf-8")
    diff = tmp_path / "diff.txt"
    diff.write_text("old", encoding="utf-8")

    fh = _FileHistory()
    _record(fh, str(same), "keep", "mutated", idx=1)
    _record(fh, str(diff), "new", "mutated", idx=2)

    results = fh.restore(0)
    assert results[str(same)] is True
    assert results[str(diff)] is True
    assert same.read_text(encoding="utf-8") == "keep"
    assert diff.read_text(encoding="utf-8") == "new"
