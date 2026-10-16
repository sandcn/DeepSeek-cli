"""src/core/_terminal — get_terminal_width 单元测试。

策略路径：
  1. /dev/tty ioctl 成功 → 返回真实列数（>0），0 列兜底 80
  2. /dev/tty 打开失败/ioctl 抛异常 → 回退 shutil
  3. shutil 也失败 → 80 兜底
通过 monkeypatch 构造各分支，不依赖真实终端。
"""

from __future__ import annotations

import pytest

from src.core import _terminal


@pytest.fixture
def fake_io(monkeypatch):
    """构造可注入的 os/fcntl/termios/struct/shutil 假实现。"""
    import fcntl
    import os
    import struct
    import termios

    state = {
        "open_ok": True,
        "ioctl_ok": True,
        "cols": 120,
        "shutil_cols": 100,
        "open_calls": 0,
    }

    def _open(path, flags):
        state["open_calls"] += 1
        if not state["open_ok"]:
            raise OSError("cannot open /dev/tty")
        return 3

    def _ioctl(fd, req, buf):
        if not state["ioctl_ok"]:
            raise OSError("ioctl failed")
        return struct.pack("HHHH", 24, state["cols"], 0, 0)

    monkeypatch.setattr(os, "open", _open)
    monkeypatch.setattr(fcntl, "ioctl", _ioctl)
    monkeypatch.setattr(os, "close", lambda fd: None)

    # get_terminal_width 在回退分支内 ``import shutil``（延迟导入），
    # 通过替换 sys.modules['shutil'] 注入假实现
    import sys

    class _Shutil:
        @staticmethod
        def get_terminal_size():
            if state.get("shutil_fail"):
                raise OSError("no tty")
            return type("Size", (), {"columns": state["shutil_cols"]})()

    monkeypatch.setitem(sys.modules, "shutil", _Shutil)
    return state


def test_ioctl_success_returns_cols(fake_io):
    assert _terminal.get_terminal_width() == 120
    assert fake_io["open_calls"] == 1


def test_ioctl_zero_cols_falls_back_to_80(fake_io):
    fake_io["cols"] = 0
    assert _terminal.get_terminal_width() == 80


def test_open_failure_falls_back_to_shutil(fake_io):
    fake_io["open_ok"] = False
    assert _terminal.get_terminal_width() == 100


def test_ioctl_failure_falls_back_to_shutil(fake_io):
    fake_io["ioctl_ok"] = False
    assert _terminal.get_terminal_width() == 100


def test_shutil_failure_returns_80(fake_io):
    fake_io["open_ok"] = False
    fake_io["shutil_fail"] = True
    assert _terminal.get_terminal_width() == 80
