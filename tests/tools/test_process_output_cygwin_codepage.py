"""Windows 代码页解码（Cygwin / MSYS2）回归测试。

背景：修复前 ``_windows_code_page`` 只认 ``os.name == 'nt'``，Cygwin 下
``default_output_encodings()`` 只有 UTF-8，原生程序输出的 GBK 变成
``����``，「系统」这类与 UTF-8 双重合法的短行被误读成 ``ϵͳ``。
"""

from __future__ import annotations

import sys

import pytest

from src.tools._process_output import (
    decode_output,
    default_output_encodings,
    windows_ansi_codepage,
)

_CYGWIN = (sys.platform or "").startswith(("cygwin", "msys"))


def test_gbk_decode_with_explicit_candidate():
    """显式给 cp936 候选时，GBK 中文按本地代码页正确解码。"""
    assert decode_output("中文".encode("gbk"), encodings=("utf-8", "cp936")) == "中文"


def test_gbk_double_valid_short_line_disambiguated():
    """「系统」的 GBK 字节也是合法 UTF-8（ϵͳ）——有 CJK 候选时应改判。"""
    assert decode_output("系统".encode("gbk"), encodings=("utf-8", "cp936")) == "系统"


def test_valid_utf8_not_misdecoded_as_gbk():
    """真 UTF-8（含 3 字节序列）不能被本地 CJK 候选改判。"""
    text = "中文 café"
    assert decode_output(text.encode("utf-8"), encodings=("utf-8", "cp936")) == text


@pytest.mark.skipif(not _CYGWIN, reason="需要 Cygwin / MSYS2")
def test_cygwin_default_candidates_include_ansi_codepage():
    """Cygwin 下候选编码必须包含系统 ANSI 代码页（中文系统 cp936）。"""
    encodings = default_output_encodings()
    assert windows_ansi_codepage() > 0
    assert any(name in ("cp936", "gbk", "gb18030") for name in encodings), encodings


@pytest.mark.skipif(not _CYGWIN, reason="需要 Cygwin / MSYS2")
def test_cygwin_default_decode_gbk_real_bytes():
    """Cygwin 默认解码路径（不显式传 candidates）应能解出 GBK 中文。"""
    assert decode_output("中文".encode("gbk")) == "中文"
    assert decode_output("系统".encode("gbk")) == "系统"


def test_non_windows_codepage_zero(monkeypatch):
    """非 Windows 平台读取代码页返回 0（不抛异常）。"""
    import src.tools._process_output as module

    monkeypatch.setattr(module, "_is_windows_like", lambda: False)
    assert windows_ansi_codepage() == 0
