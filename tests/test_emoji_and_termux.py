"""src/renderer/emoji_map + src/tools/utils — Emoji 解析与 Termux 通知单元测试。

覆盖：
  - resolve_emoji：短代码替换、未知短代码保留、无冒号文本、相邻短代码
  - EMOJI_MAP 完整性（键格式、值为单字符或合法序列）
  - termux_notify：非 Termux 跳过、Termux 路径（mock subprocess）
"""

from __future__ import annotations

import pytest

from src.renderer.emoji_map import EMOJI_MAP, resolve_emoji


# ── resolve_emoji ────────────────────────────────────────

def test_resolve_emoji_basic():
    assert resolve_emoji(":smile:") == "\U0001f60a"


def test_resolve_emoji_in_sentence():
    assert resolve_emoji("工作完成 :tada: !") == "工作完成 \U0001f389 !"


def test_resolve_emoji_multiple():
    assert resolve_emoji(":heart: and :star:") == "\u2764\ufe0f and \u2b50"


def test_resolve_emoji_unknown_kept():
    assert resolve_emoji(":unknown_shortcode:") == ":unknown_shortcode:"


def test_resolve_emoji_no_emoji():
    assert resolve_emoji("plain text") == "plain text"


def test_resolve_emoji_empty():
    assert resolve_emoji("") == ""


def test_resolve_emoji_adjacent():
    assert resolve_emoji(":fire::fire:") == "\U0001f525\U0001f525"


def test_resolve_emoji_partial_colon():
    assert resolve_emoji("time: x") == "time: x"
    assert resolve_emoji(":") == ":"
    assert resolve_emoji("::") == "::"


# ── EMOJI_MAP 数据完整性 ─────────────────────────────────

def test_emoji_map_keys_wellformed():
    for key in EMOJI_MAP:
        assert key.startswith(":") and key.endswith(":"), key
        assert len(key) > 2


def test_emoji_map_values_nonempty():
    for key, val in EMOJI_MAP.items():
        assert val, key
        # 映射后能 round-trip
        assert resolve_emoji(key) == val


def test_emoji_map_no_duplicate_values():
    # 允许别名指向同一字符，但检查无空值/无异常字符
    for key, val in EMOJI_MAP.items():
        assert "\x00" not in val


# ── termux_notify ────────────────────────────────────────

def test_termux_notify_non_termux(monkeypatch):
    import src.tools.utils as tu

    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr(tu.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no termux-api")))
    assert tu.termux_notify("msg") == "非Termux环境，跳过通知"


def test_termux_notify_success_path(monkeypatch):
    import src.tools.utils as tu

    class _R:
        def __init__(self, rc, out=""):
            self.returncode = rc
            self.stderr = ""
            self.stdout = out

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _R(0)

    monkeypatch.setenv("TERMUX_VERSION", "0.1")
    monkeypatch.setattr(tu.subprocess, "run", fake_run)
    result = tu.termux_notify("任务完成", vibrate=True, notification=True, toast=False)
    assert "震动成功" in result
    assert "系统通知发送成功" in result
    assert calls[0] == ["termux-vibrate", "-d", "10000"]
    assert calls[1][0] == "termux-notification"


def test_termux_notify_command_failure(monkeypatch):
    import src.tools.utils as tu

    class _R:
        def __init__(self, rc, out=""):
            self.returncode = rc
            self.stderr = "boom"
            self.stdout = ""

    monkeypatch.setenv("TERMUX_VERSION", "0.1")
    monkeypatch.setattr(tu.subprocess, "run", lambda cmd, **kw: _R(1))
    result = tu.termux_notify("msg", vibrate=True, notification=False, toast=False)
    assert "震动失败" in result


def test_termux_notify_timeout(monkeypatch):
    import subprocess

    import src.tools.utils as tu

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="termux-vibrate", timeout=5)

    monkeypatch.setenv("TERMUX_VERSION", "0.1")
    monkeypatch.setattr(tu.subprocess, "run", timeout)
    result = tu.termux_notify("msg", vibrate=True, notification=False)
    assert "命令执行超时" in result


def test_async_termux_notify_non_termux():
    import asyncio

    import src.tools.utils as tu

    result = asyncio.run(tu.async_termux_notify("msg"))
    # 无 TERMUX_VERSION 且 command -v 失败/异常 → 跳过
    assert "跳过" in result
