"""剪贴板能力层（``src/tools/_clipboard.py``）测试。

覆盖：换行归一化、后端解析、Windows 后端真实读写往返（保存并恢复原内容）、
命令型后端（x11 / macOS）的读写命令与错误处理。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tools import _clipboard as clip
from src.tools._clipboard import (
    ClipboardError,
    MacOSClipboardBackend,
    X11ClipboardBackend,
    WindowsClipboardBackend,
    normalize_newlines,
    read_clipboard_text,
    resolve_backend,
    to_platform_newlines,
    write_clipboard_text,
)


# ── 换行归一化 ──────────────────────────────────────────

def test_newline_normalization():
    assert normalize_newlines("a\r\nb\rc\nd") == "a\nb\nc\nd"
    assert to_platform_newlines("a\nb") == "a\r\nb"
    assert to_platform_newlines("a\r\nb") == "a\r\nb"


# ── 后端解析 ────────────────────────────────────────────

def test_resolve_backend_returns_platform_backend():
    backend = resolve_backend()
    assert backend is not None
    assert isinstance(backend, (WindowsClipboardBackend, MacOSClipboardBackend,
                                X11ClipboardBackend))


def test_backends_are_registered_once():
    names = [getattr(backend, "name", "") for backend in clip.available_backends()]
    assert names.count("windows") == 1
    assert "macos" in names and "x11" in names


# ── Windows 后端真实往返 ────────────────────────────────

def test_windows_backend_roundtrip_restores_original():
    backend = WindowsClipboardBackend()
    if not backend.supports():
        pytest.skip("非 Windows 平台")
    original = backend.read_text()
    try:
        backend.write_text("hello 中文 世界\n第二行")
        assert backend.read_text() == "hello 中文 世界\n第二行"
        clip.clear_clipboard()
        assert backend.read_text() == ""
    finally:
        backend.write_text(original)
    assert backend.read_text() == original


# ── 命令型后端 ──────────────────────────────────────────

def _patch_command_backend(monkeypatch, backend, calls: list, *,
                           returncode: int = 0):
    monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/" + name)

    def _run(command, input=None, capture_output=None, text=None, timeout=None):
        calls.append((command, input))
        return SimpleNamespace(returncode=returncode, stdout="粘贴内容\r\n",
                               stderr="失败原因" if returncode else "")
    monkeypatch.setattr(clip.subprocess, "run", _run)
    return backend


def test_x11_backend_reads_and_writes(monkeypatch):
    calls: list = []
    _patch_command_backend(monkeypatch, X11ClipboardBackend(), calls)
    backend = X11ClipboardBackend()
    assert backend.read_text() == "粘贴内容\n"
    backend.write_text("要写入")
    assert calls[0][0][:2] == ["xclip", "-selection"]
    assert calls[1][1] == "要写入"


def test_command_backend_reports_failure(monkeypatch):
    calls: list = []
    _patch_command_backend(monkeypatch, MacOSClipboardBackend(), calls,
                           returncode=1)
    backend = MacOSClipboardBackend()
    with pytest.raises(ClipboardError) as error:
        backend.read_text()
    assert "失败原因" in str(error.value)


def test_public_helpers_use_resolved_backend(monkeypatch):
    recoder = {"written": []}

    class _Backend:
        name = "stub"

        def supports(self):
            return True

        def read_text(self):
            return "内容"

        def write_text(self, text):
            recoder["written"].append(text)

    undo = clip.register_backend(_Backend(), prepend=True)
    try:
        assert read_clipboard_text() == "内容"
        write_clipboard_text("新内容")
        assert recoder["written"] == ["新内容"]
    finally:
        undo()
