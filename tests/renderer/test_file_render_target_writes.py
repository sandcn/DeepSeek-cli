"""FileRenderTarget 写入路径回归（P0：assert → 显式返回值）。

★ 修复（P0）：``FileRenderTarget.write`` / ``write_line`` 原以
  ``assert self._console is not None`` 保证 Console 已就绪——``assert`` 在
  ``python -O`` 下被剥离，写入路径的保证静默消失。现由 ``_ensure_open()``
  直接返回可用 ``Console``（恒非 None），调用方不再依赖断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.renderer.targets.file import FileRenderTarget


def test_requires_non_empty_path(tmp_path: Path):
    with pytest.raises(ValueError):
        FileRenderTarget("")


def test_ensure_open_returns_console_and_is_idempotent(tmp_path: Path):
    target = FileRenderTarget(str(tmp_path / "out.txt"))
    console = target._ensure_open()
    assert console is not None
    assert target._ensure_open() is console
    target.close()


def test_write_creates_file_with_text(tmp_path: Path):
    out = tmp_path / "nested" / "out.txt"
    target = FileRenderTarget(str(out))
    target.write("你好 world")
    target.close()
    assert out.exists()
    assert "你好 world" in out.read_text(encoding="utf-8")


def test_write_none_is_noop_and_creates_no_file(tmp_path: Path):
    out = tmp_path / "none.txt"
    target = FileRenderTarget(str(out))
    target.write(None)
    assert not out.exists()
    target.close()


def test_write_converts_ansi_to_plain_text(tmp_path: Path):
    out = tmp_path / "ansi.txt"
    target = FileRenderTarget(str(out))
    target.write("\x1b[31m红色\x1b[0m")
    target.close()
    content = out.read_text(encoding="utf-8")
    assert "红色" in content
    assert "\x1b" not in content


def test_write_line_blank_and_text(tmp_path: Path):
    out = tmp_path / "lines.txt"
    target = FileRenderTarget(str(out))
    target.write_line("第一行")
    target.write_line()
    target.write_line("第三行")
    target.close()
    content = out.read_text(encoding="utf-8")
    assert "第一行" in content
    assert "第三行" in content
    assert content.count("\n") >= 2


def test_write_after_close_reopens(tmp_path: Path):
    out = tmp_path / "reopen.txt"
    target = FileRenderTarget(str(out))
    target.write("a")
    target.close()
    assert target._handle is None
    assert target._console is None
    target.write("b")
    target.close()
    assert "b" in out.read_text(encoding="utf-8")


def test_flush_and_width(tmp_path: Path):
    target = FileRenderTarget(str(tmp_path / "w.txt"), width=42)
    assert target.width == 42
    target.write("x")
    target.flush()
    target.close()


def test_clear_line_is_noop(tmp_path: Path):
    target = FileRenderTarget(str(tmp_path / "c.txt"))
    assert target.clear_line() is None
    target.close()
