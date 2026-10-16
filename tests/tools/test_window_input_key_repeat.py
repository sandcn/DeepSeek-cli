"""按键连按（repeat）与 bash_opt repeat 参数贯通测试。

覆盖：动作模型的 repeat 解析（默认值、别名、范围、仅 press 生效、
摘要序列化）、三个平台后端的连按事件序列（Windows SendInput / X11
xdotool / macOS Quartz）、``op=key`` 与 ``op=keys`` 两条通道的 repeat
参数贯通（GUI 窗口注入与终端序列重复写入）。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._window_input import InputResult
from src.tools._window_input.action import (
    DEFAULT_KEY_REPEAT,
    MAX_KEY_REPEAT,
    build_action,
    describe_action,
)
from src.tools._window_input.macos import MacOSInputBackend, QuartzKeyboardDriver
from src.tools._window_input.result import ActionError
from src.tools._window_input.x11 import X11InputBackend
from src.tools.bash_opt import BashOptFunc


# ── 动作构建：repeat 解析 ───────────────────────────────

def test_repeat_defaults_to_one():
    action = build_action("key", {"key": "f12"})
    assert action.repeat == DEFAULT_KEY_REPEAT == 1
    assert action.effective_repeat == 1


def test_repeat_accepts_explicit_value_and_alias():
    assert build_action("key", {"key": "f12", "repeat": 5}).repeat == 5
    assert build_action("key", {"key": "f12", "times": "8"}).repeat == 8


def test_repeat_rejected_out_of_range():
    with pytest.raises(ActionError) as excinfo:
        build_action("key", {"key": "f12", "repeat": 0})
    assert "repeat" in str(excinfo.value)
    with pytest.raises(ActionError):
        build_action("key", {"key": "f12", "repeat": MAX_KEY_REPEAT + 1})
    with pytest.raises(ActionError):
        build_action("key", {"key": "f12", "repeat": "many"})


def test_effective_repeat_only_applies_to_press_phase():
    assert build_action("key", {"key": "a", "repeat": 4}).effective_repeat == 4
    assert build_action(
        "key", {"key": "a", "repeat": 4, "phase": "down"}).effective_repeat == 1
    assert build_action(
        "key", {"key": "a", "repeat": 4, "phase": "up"}).effective_repeat == 1


def test_describe_action_reports_repeat():
    payload = describe_action(build_action("key", {"key": "f12", "repeat": 3}))
    assert payload["repeat"] == 3
    assert "repeat" not in describe_action(build_action("key", {"key": "f12"}))

    long_press = describe_action(build_action(
        "key", {"key": "a", "repeat": 3, "phase": "down"}))
    assert long_press["repeat"] == 3
    assert long_press["effective_repeat"] == 1


# ── X11：xdotool 连按 ───────────────────────────────────

class _X11Runner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command):
        self.commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")


def _x11_backend(runner) -> X11InputBackend:
    return X11InputBackend(locator=lambda pid, runner=None: None, runner=runner)


def test_x11_repeat_sends_multiple_down_up_pairs():
    runner = _X11Runner()
    _x11_backend(runner)._key(
        "xdotool", build_action("key", {"key": "f12", "repeat": 3}))
    assert runner.commands == [["xdotool", "keydown", "F12"],
                               ["xdotool", "keyup", "F12"]] * 3


def test_x11_repeat_ignored_for_up_phase():
    runner = _X11Runner()
    _x11_backend(runner)._key(
        "xdotool", build_action("key", {"key": "ctrl", "repeat": 3, "phase": "up"}))
    assert runner.commands == [["xdotool", "keyup", "ctrl"]]


# ── macOS：Quartz 连按 ──────────────────────────────────

class _FakeQuartz:
    kCGHIDEventTap = 0

    def __init__(self) -> None:
        self.events: list[dict] = []

    def CGEventCreateKeyboardEvent(self, source, code, key_down):
        event = {"code": code, "down": bool(key_down), "flags": 0}
        self.events.append(event)
        return event

    def CGEventSetFlags(self, event, flags):
        event["flags"] = flags

    def CGEventPost(self, tap, event):
        return None

    def CFRelease(self, event):
        return None


def _macos_backend():
    fake = _FakeQuartz()
    driver = QuartzKeyboardDriver()
    driver._quartz = fake
    driver._loaded = True
    backend = MacOSInputBackend(locator=lambda pid: None, keyboard=driver)
    return backend, fake


def test_macos_repeat_sends_multiple_presses():
    backend, fake = _macos_backend()
    backend._key(build_action("key", {"key": "f12", "repeat": 3}))
    assert [event["down"] for event in fake.events] == [True, False] * 3


def test_macos_repeat_ignored_for_down_phase():
    backend, fake = _macos_backend()
    backend._key(build_action("key", {"key": "a", "repeat": 3, "phase": "down"}))
    assert len(fake.events) == 1
    assert fake.events[0]["down"] is True


# ── bash_opt：repeat 参数贯通 ───────────────────────────

class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records


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


async def _run_key(rec: dict, **kwargs) -> str:
    func = BashOptFunc(task_id="bg-1", op="key", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": rec}))
    return await func.execute()


async def _run_keys(rec: dict, **kwargs) -> str:
    func = BashOptFunc(task_id="bg-1", op="keys", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": rec}))
    return await func.execute()


def _stub_window_input(monkeypatch):
    calls: list = []

    def _fake_send(pid, action):
        calls.append(action)
        return InputResult(action=action.name, backend="test", window_pid=pid,
                           window_title="demo", detail={})

    monkeypatch.setattr(bash_opt_module, "send_window_input", _fake_send)
    return calls


async def test_key_repeat_reaches_window_channel(monkeypatch):
    calls = _stub_window_input(monkeypatch)
    payload = json.loads(await _run_key(_record(), key="f12", repeat=8))
    assert payload["op"] == "key"
    assert calls and calls[0].repeat == 8
    assert calls[0].effective_repeat == 8


async def test_key_repeat_defaults_to_single_press(monkeypatch):
    calls = _stub_window_input(monkeypatch)
    await _run_key(_record(), key="f12")
    assert calls[0].repeat == DEFAULT_KEY_REPEAT


async def test_key_rejects_bad_repeat(monkeypatch):
    _stub_window_input(monkeypatch)
    result = await _run_key(_record(), key="f12", repeat=0)
    assert result.startswith("(输入参数非法")
    assert "repeat" in result


async def test_keys_repeat_reaches_gui_channel(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "probe_window", lambda pid: object())
    calls = _stub_window_input(monkeypatch)
    payload = json.loads(await _run_keys(_record(), key="f12", repeat=4))
    assert payload["channel"] == "gui"
    assert calls[0].repeat == 4


async def test_keys_repeat_writes_terminal_multiple_times(monkeypatch):
    written: list[tuple] = []

    async def _fake_write(fd, data):
        written.append((fd, bytes(data)))

    monkeypatch.setattr(bash_opt_module, "_write_pty_all", _fake_write)
    monkeypatch.setattr(bash_opt_module, "probe_window", lambda pid: None)
    result = await _run_keys(_record(), key="ctrl+c", repeat=3)
    assert written == [(9, b"\x03")] * 3
    assert "x3" in result


async def test_keys_repeat_default_writes_once(monkeypatch):
    written: list[tuple] = []

    async def _fake_write(fd, data):
        written.append((fd, bytes(data)))

    monkeypatch.setattr(bash_opt_module, "_write_pty_all", _fake_write)
    monkeypatch.setattr(bash_opt_module, "probe_window", lambda pid: None)
    result = await _run_keys(_record(), key="esc")
    assert written == [(9, b"\x1b")]
    assert "x" not in result.split("按键: ")[-1]


async def test_keys_rejects_bad_repeat(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "probe_window", lambda pid: None)
    result = await _run_keys(_record(), key="enter", repeat=999)
    assert result.startswith("(按键参数非法")
    assert "repeat" in result
