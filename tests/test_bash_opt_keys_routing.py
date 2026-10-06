"""``bash_opt`` ``op=keys`` 自动路由测试（有 GUI 窗口走窗口 / 否则走终端）。

路由规则：

  - 目标进程（含子进程）有可接收键盘输入的 GUI 窗口 → 按键作为窗口级键盘
    消息注入该窗口（与 op=key 同一通道）；
  - 没有 GUI 窗口 → 回退写入终端（PTY master / stdin 管道）；
  - 两者都不可用 → 返回错误说明。

覆盖：GUI 通路成功（不再写终端）、无窗口回退终端、GUI 注入失败后终端回退、
两条通道都不可用时报错、非法键名提示。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._window_input import InputError, InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


class _Writer:
    def __init__(self):
        self.sent: list[bytes] = []

    def write(self, data):
        self.sent.append(bytes(data))

    async def drain(self):
        return None


def _record(mode: str = "pty") -> dict:
    return {
        "read_buffer": "",
        "status": "running",
        "done": False,
        "pid": 4321,
        "mode": mode,
        "task": None,
        "io_lock": None,
        "master_fd": 9,
        "stdin_writer": None,
    }


async def _run_keys(rec: dict, key):
    func = BashOptFunc(task_id="bg-1", op="keys", key=key)
    func.set_agent(_FakeAgent({"bg-1": rec}))
    return await func.execute()


@pytest.fixture()
def pty_writes(monkeypatch):
    """拦截 PTY 写入并记录字节。"""
    written: list[tuple] = []

    async def _fake_write(fd, data):
        written.append((fd, bytes(data)))

    monkeypatch.setattr(bash_opt_module, "_write_pty_all", _fake_write)
    return written


def _stub_window_input(monkeypatch, *, error: Exception | None = None):
    """把窗口输入通道替换为记录型替身（成功返回 InputResult）。"""
    calls: list[tuple] = []

    def _fake_send(pid, action):
        calls.append((pid, action))
        if error is not None:
            raise error
        return InputResult(
            action=action.name, backend="test", window_pid=pid,
            window_title="demo", detail={"key": action.shortcut.display()},
        )

    monkeypatch.setattr(bash_opt_module, "send_window_input", _fake_send)
    return calls


def _set_probe(monkeypatch, found: bool):
    monkeypatch.setattr(bash_opt_module, "probe_window",
                        lambda pid: object() if found else None)


# ── GUI 窗口存在：走窗口注入 ─────────────────────────────

async def test_keys_with_gui_window_injects_to_window(monkeypatch, pty_writes):
    _set_probe(monkeypatch, True)
    calls = _stub_window_input(monkeypatch)
    rec = _record(mode="pty")
    result = await _run_keys(rec, "up")
    payload = json.loads(result)
    assert payload["channel"] == "gui"
    assert payload["op"] == "keys"
    assert payload["key"] == "up"
    assert calls and calls[0][0] == 4321
    # 走 GUI 通道后不得再写终端
    assert pty_writes == []


async def test_keys_gui_route_accepts_gui_only_key(monkeypatch, pty_writes):
    """GUI 通道支持 f21-f24（终端无对应序列）。"""
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch)
    rec = _record(mode="pty")
    result = await _run_keys(rec, "f21")
    assert json.loads(result)["channel"] == "gui"
    assert pty_writes == []


# ── 无 GUI 窗口：回退终端 ────────────────────────────────

async def test_keys_without_window_writes_terminal(monkeypatch, pty_writes):
    _set_probe(monkeypatch, False)
    rec = _record(mode="pty")
    result = await _run_keys(rec, "ctrl+c")
    assert "已向后台任务" in result
    assert pty_writes == [(9, b"\x03")]


async def test_keys_without_window_writes_pipe(monkeypatch):
    _set_probe(monkeypatch, False)
    rec = _record(mode="pipe")
    writer = _Writer()
    rec["stdin_writer"] = writer
    await _run_keys(rec, "esc")
    assert writer.sent == [b"\x1b"]


# ── GUI 注入失败：回退终端 ───────────────────────────────

async def test_keys_gui_failure_falls_back_to_terminal(monkeypatch, pty_writes):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch, error=InputError("无法把窗口置于前台"))
    rec = _record(mode="pty")
    result = await _run_keys(rec, "enter")
    assert "已向后台任务" in result
    assert pty_writes == [(9, b"\r")]


async def test_keys_gui_and_terminal_both_unavailable(monkeypatch):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch, error=InputError("无法把窗口置于前台"))
    rec = _record(mode=None)
    result = await _run_keys(rec, "enter")
    assert "GUI 窗口注入失败" in result
    assert "终端备选通道也不可用" in result
    assert "无法把窗口置于前台" in result


# ── 两者都不可用（无窗口 + 无终端句柄） ──────────────────

async def test_keys_no_window_no_terminal_reports_error(monkeypatch):
    _set_probe(monkeypatch, False)
    rec = _record(mode=None)
    result = await _run_keys(rec, "enter")
    assert "没有可接收键盘输入的 GUI 窗口" in result
    assert "终端句柄" in result


# ── 非法键名 ─────────────────────────────────────────────

async def test_keys_invalid_key_names_terminal_and_window(monkeypatch):
    _set_probe(monkeypatch, False)
    result = await _run_keys(_record(), "unknown_key")
    assert result.startswith("(按键解析失败")
    assert "终端按键支持" in result
    assert "GUI 窗口" in result


async def test_keys_missing_key_parameter(monkeypatch):
    _set_probe(monkeypatch, False)
    result = await _run_keys(_record(), None)
    assert "需要 key 参数" in result
