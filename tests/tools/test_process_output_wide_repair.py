"""``_process_output`` 对「UTF-16 被按单字节解码」文本的修复测试。

背景（实测：Windows 上 ``wsl.exe -l -v``）：``wsl.exe`` 按 UTF-16LE 输出，
PowerShell 5.1 的 native 管道按 UTF-8 解码，于是每个 ASCII 字符后夹一个
``\\x00``、BOM 变成两个 ``U+FFFD``。工具再解码这段文本时原始字节已不可逆，
但 NUL 的位置规律完整保留原文——去掉 NUL 即可还原。
"""

from __future__ import annotations

import pytest

from src.tools._process_output import (
    OutputLineDecoder,
    _repair_wide_text,
    decode_output,
)


def _wide_text(text: str, *, bom: bool = True) -> str:
    """模拟「UTF-16LE 字节被按 UTF-8 解码」后得到的文本（含误读的 BOM）。"""
    body = "".join(f"{char}\x00" for char in text)
    return ("\ufffd\ufffd" if bom else "") + body


def test_repair_strips_nul_from_wide_text():
    raw = _wide_text("  NAME  STATE  VERSION\n")
    repaired = _repair_wide_text(raw)
    assert "\x00" not in repaired
    assert repaired == "  NAME  STATE  VERSION\n"


def test_repair_is_noop_for_plain_text():
    text = "普通 UTF-8 文本 with ascii\n"
    assert _repair_wide_text(text) == text


def test_repair_keeps_isolated_nul():
    """正常文本里偶发的单个 NUL 不触发修复（避免误伤二进制-ish 输出）。"""
    text = "abc\x00defghijklmnopqrstuvwxyz"
    assert _repair_wide_text(text) == text


def test_repair_keeps_very_short_nul_text():
    """极短文本（样本不足）不触发修复，避免把二进制片段当宽字符流。"""
    assert _repair_wide_text("a\x00") == "a\x00"
    assert _repair_wide_text("ab\x00") == "ab\x00"


def test_repair_returns_all_nul_input_unchanged():
    assert _repair_wide_text("\x00\x00\x00") == "\x00\x00\x00"


def test_decode_output_repairs_wide_text():
    """端到端：字节流里出现宽字符 NUL 模式时同样被修复。"""
    wide_bytes = _wide_text("Ubuntu-24.04 Running").encode("utf-8")
    assert "\x00" not in decode_output(wide_bytes)
    assert "Ubuntu-24.04 Running" in decode_output(wide_bytes)


def test_line_decoder_repairs_wide_text():
    decoder = OutputLineDecoder()
    lines = decoder.feed(_wide_text("NAME STATE\n").encode("utf-8"))
    lines += decoder.flush()
    joined = "".join(lines)
    assert "\x00" not in joined
    assert "NAME STATE" in joined


def test_repair_does_not_touch_valid_utf16_bytes():
    """真正的 UTF-16 字节流走既有 BOM 分支，不受本修复影响。"""
    raw = "中文 UTF-16".encode("utf-16")  # 带 BOM
    assert decode_output(raw) == "中文 UTF-16"
