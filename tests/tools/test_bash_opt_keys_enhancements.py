"""``bash_opt`` ``op=keys`` 增强参数测试（shot / settle / wait_for / diff）。

回归背景：这些参数此前被 ``_op_keys`` **静默忽略**——传了 ``shot`` 不生成
截图、``settle`` 不等待、``diff`` 没有比较结果，调用方只看到「按键已发送」，
与 ``click`` / ``key`` 等输入 op 的行为不一致（实测带 ``shot`` 的 keys 既无
截图也无任何提示）。

现在的约定：

  - GUI 通路（目标进程有可接收键盘输入的窗口）：走 ``_keys_via_window``，
    ``settle`` / ``wait_for`` / ``diff`` / ``shot`` 与输入 op 完全一致；
  - 终端通路（PTY / stdin）：没有可截图画面，带这些参数时**明确报错**
    （不再静默丢弃，也不会「先报错又把按键发出去」）；
  - 带增强参数但 GUI 注入失败：返回失败原因，不再静默回退终端。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png
from src.tools._window_input import InputError, InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


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


async def _run_keys(rec: dict, key, **kwargs):
    func = BashOptFunc(task_id="bg-1", op="keys", key=key, **kwargs)
    func.set_agent(_FakeAgent({"bg-1": rec}))
    return await func.execute()


def _set_probe(monkeypatch, found: bool):
    monkeypatch.setattr(bash_opt_module, "probe_window",
                        lambda pid: object() if found else None)


def _stub_window_input(monkeypatch, *, error: Exception | None = None):
    """把窗口输入通道替换为记录型替身（成功返回 InputResult）。"""
    calls: list = []

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


@pytest.fixture()
def pty_writes(monkeypatch):
    """拦截 PTY 写入并记录字节（用于断言「没有偷偷回退终端」）。"""
    written: list = []

    async def _fake_write(fd, data):
        written.append((fd, bytes(data)))

    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    return written


def _patch_capture(monkeypatch):
    """替换 ``_capture_with_retry`` 为记录型替身（不真的截图）。"""
    calls: list = []

    async def _fake_capture(self, pid, path, crop=None, *, window=None,
                            grid=None):
        calls.append({"pid": pid, "path": path, "window": window, "grid": grid})
        return SimpleNamespace(
            to_dict=lambda: {"path": path, "width": 8, "height": 8,
                             "window_pid": pid, "window_title": "demo"})

    monkeypatch.setattr(BashOptFunc, "_capture_with_retry", _fake_capture)
    return calls


def _fake_temps(tmp_path, colors):
    """把 ``_temp_screenshot`` 替换为按序列产出的真实 PNG（BGRA 颜色）。"""
    state = {"n": 0}

    async def _impl(self, pid, window):
        index = min(state["n"], len(colors) - 1)
        state["n"] += 1
        path = tmp_path / f"keys-shot-{state['n']}.png"
        path.write_bytes(png.encode_png_bgra(2, 2, bytes(colors[index]) * 4))
        return str(path)

    return _impl


RED = (0, 0, 255, 255)
BLUE = (255, 0, 0, 255)


# ── GUI 通路：增强参数生效 ───────────────────────────────

async def test_keys_gui_shot_attaches_screenshot(monkeypatch, tmp_path):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch)
    captures = _patch_capture(monkeypatch)
    target = tmp_path / "keys.png"
    payload = json.loads(await _run_keys(_record(), "enter",
                                         shot=str(target)))
    assert payload["channel"] == "gui"
    assert payload["screenshot"]["path"] == str(target)
    assert captures and captures[0]["pid"] == 4321


async def test_keys_gui_shot_true_uses_auto_dir(monkeypatch, tmp_path):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch)
    captures = _patch_capture(monkeypatch)
    auto_dir = tmp_path / "auto"
    monkeypatch.setattr(BashOptFunc, "_SHOT_AUTO_DIR", str(auto_dir))
    payload = json.loads(await _run_keys(_record(), "enter", shot=True))
    assert payload["screenshot"]["path"].startswith(str(auto_dir))
    assert captures


async def test_keys_gui_diff_reports_change(monkeypatch, tmp_path):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED, BLUE]))
    payload = json.loads(await _run_keys(_record(), "enter", diff=True))
    assert payload["channel"] == "gui"
    assert payload["diff"]["changed"] is True
    assert "已变化" in payload["diff"]["summary"]


async def test_keys_gui_wait_for_change(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_WAIT_FOR_INTERVAL", 0.01)
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED, BLUE]))
    payload = json.loads(await _run_keys(_record(), "enter",
                                         wait_for="change"))
    assert payload["wait_for"]["satisfied"] is True
    assert payload["wait_for"]["mode"] == "change"


async def test_keys_gui_settle_waits(monkeypatch):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch)
    started = time.monotonic()
    payload = json.loads(await _run_keys(_record(), "enter", settle=0.05))
    elapsed = time.monotonic() - started
    assert payload["channel"] == "gui"
    assert elapsed >= 0.04


async def test_keys_gui_rejects_bad_enhancement_value(monkeypatch):
    _set_probe(monkeypatch, True)
    calls = _stub_window_input(monkeypatch)
    result = await _run_keys(_record(), "enter", diff="maybe")
    assert "按键参数非法" in result
    assert "diff 需要布尔值" in result
    # 参数非法时不注入按键
    assert calls == []


# ── 终端通路：增强参数明确报错（不静默丢弃） ─────────────

async def test_keys_terminal_route_rejects_enhancements(monkeypatch, pty_writes):
    _set_probe(monkeypatch, False)
    result = await _run_keys(_record(), "enter", shot="never.png")
    assert "keys 参数不受支持" in result
    assert "op=screenshot" in result
    # 既报错就不该再把按键写进终端
    assert pty_writes == []


async def test_keys_terminal_route_rejects_diff(monkeypatch, pty_writes):
    _set_probe(monkeypatch, False)
    result = await _run_keys(_record(), "enter", diff=True)
    assert "keys 参数不受支持" in result
    assert pty_writes == []


async def test_keys_terminal_plain_key_still_works(monkeypatch, pty_writes):
    """不带增强参数时，终端通路行为保持不变。"""
    _set_probe(monkeypatch, False)
    result = await _run_keys(_record(), "ctrl+c")
    assert "已向后台任务" in result
    assert pty_writes == [(9, b"\x03")]


# ── GUI 注入失败 + 增强参数：不再静默回退终端 ────────────

async def test_keys_gui_failure_with_enhancements_does_not_fall_back(
        monkeypatch, pty_writes):
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch, error=InputError("无法把窗口置于前台"))
    result = await _run_keys(_record(), "enter", diff=True)
    assert "目标 GUI 窗口注入失败" in result
    assert "没有回退到终端通道" in result
    assert "无法把窗口置于前台" in result
    assert pty_writes == []


async def test_keys_gui_failure_without_enhancements_falls_back(
        monkeypatch, pty_writes):
    """不带增强参数时仍保留原有的终端回退行为。"""
    _set_probe(monkeypatch, True)
    _stub_window_input(monkeypatch, error=InputError("无法把窗口置于前台"))
    result = await _run_keys(_record(), "enter")
    assert "已向后台任务" in result
    assert pty_writes == [(9, b"\r")]
