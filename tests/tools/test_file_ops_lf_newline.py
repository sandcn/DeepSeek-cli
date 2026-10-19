# -*- coding: utf-8 -*-
"""文本编码 / 行尾写死（UTF-8 + LF）——回归测试。

「当前执行环境」章节声明「文本编码格式: utf-8」「行尾格式: LF」并写死；
写入侧必须与声明一致：Windows 原生下 text 模式默认把 ``\\n`` 翻译成
``\\r\\n``，因此 ``_atomic_write_local`` 必须显式 ``newline='\\n'``。
"""

from __future__ import annotations

import os

import pytest

from src.tools import file_ops
from src.tools.write import WriteFunc


def test_atomic_write_local_uses_utf8_and_lf_newline(tmp_path, monkeypatch):
    captured: dict = {}
    real_fdopen = os.fdopen

    def fake_fdopen(fd, mode="r", *args, **kwargs):
        captured["mode"] = mode
        captured.update(kwargs)
        return real_fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(file_ops.os, "fdopen", fake_fdopen)
    target = tmp_path / "lf.txt"
    file_ops._atomic_write_local(str(target), "第一行\n第二行\n")

    assert captured["mode"] == "w"
    assert captured["encoding"] == "utf-8"
    assert captured["newline"] == "\n"
    raw = target.read_bytes()
    assert raw == "第一行\n第二行\n".encode("utf-8")
    assert b"\r\n" not in raw


def test_atomic_write_file_defaults_to_utf8_lf(tmp_path):
    target = tmp_path / "defaults.txt"
    file_ops.atomic_write_file(str(target), "a\nb\n")
    raw = target.read_bytes()
    assert raw == b"a\nb\n"
    assert raw.decode("utf-8") == "a\nb\n"


def test_atomic_write_local_keeps_existing_crlf_bytes(tmp_path):
    """内容里已有的 CRLF 原样保留（edit 修改既有文件不破坏未触及的行）。"""
    target = tmp_path / "crlf.txt"
    file_ops._atomic_write_local(str(target), "old\r\nnew\n")
    assert target.read_bytes() == b"old\r\nnew\n"


async def test_write_tool_outputs_utf8_lf(tmp_path):
    target = tmp_path / "工具.txt"
    result = await WriteFunc(path=str(target), content="中文内容\n第二行\n").execute()
    assert "写入成功" in result
    assert target.read_bytes() == "中文内容\n第二行\n".encode("utf-8")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
