"""bash_opt 快捷键探针（Ctrl+A / Ctrl+C 是否真的生效）测试。

背景（2026-10-20 实测）：WinForms 单行 TextBox 收到 Ctrl+A 的 keydown/keyup
却不执行全选（SendInput 与 PostMessage 两条通道都一样，而 Ctrl+V 正常）。
工具如果只回「已注入」，调用方会误以为执行成功。

探针对「效果可可靠观测」的快捷键做注入后复核：

  - ``ctrl+a``：比较注入前后的窗口截图（全选会改变选中高亮）；
  - ``ctrl+c``：比较注入前后的剪贴板文本（复制会改变剪贴板）；

未生效时在结果里附 ``shortcut_probe.warning`` 与替代方案；其它快捷键
（如 ``ctrl+s``）不做探针，避免误报。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._window_input import InputError, InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict | None = None):
        self._background_tasks = records or {}
        self._subagent_tasks = {}


def _func(**kwargs):
    func = BashOptFunc(**kwargs)
    func.set_agent(_FakeAgent())
    return func


# ── 按键文本归一化 ──────────────────────────────────────

def test_shortcut_token_normalizes_variants():
    token = BashOptFunc._shortcut_token
    assert token("ctrl+a") == "ctrl+a"
    assert token("Ctrl_A") == "ctrl+a"
    assert token("control-c") == "ctrl+c"
    assert token("  ") == ""
    assert token(None) == ""


# ── 不参与探针的快捷键 ──────────────────────────────────

async def test_probe_before_ignores_unprobed_shortcut(monkeypatch):
    func = _func(task_id="bg-1", op="key", key="enter")
    called = {"shot": 0}

    async def _shot(self, pid, window):
        called["shot"] += 1
        return None

    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _shot)
    assert await func._probe_shortcut_before(4321, "enter") is None
    assert called["shot"] == 0


async def test_probe_before_captures_screen_for_ctrl_a(monkeypatch):
    func = _func(task_id="bg-1", op="key", key="ctrl+a")

    async def _shot(self, pid, window):
        return "before.png"

    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _shot)
    probe = await func._probe_shortcut_before(4321, "ctrl+a")

    assert probe == {"kind": "screen", "key": "ctrl+a", "path": "before.png"}


# ── 界面变化探针（Ctrl+A） ──────────────────────────────

async def test_probe_after_screen_unchanged_warns(monkeypatch):
    func = _func(task_id="bg-1", op="key", key="ctrl+a")
    removed: list[str] = []

    async def _diff(self, pid, before, *, window, tolerance):
        return {"changed": False, "changed_ratio": 0.0}

    monkeypatch.setattr(BashOptFunc, "_diff_result", _diff)
    monkeypatch.setattr(BashOptFunc, "_remove_temp",
                        staticmethod(lambda path: removed.append(path)))

    result = await func._probe_shortcut_after(
        4321, {"kind": "screen", "key": "ctrl+a", "path": "before.png"})

    assert result["changed"] is False
    assert "warning" in result
    assert "Ctrl+A" in result["warning"]
    assert "element" in result["warning"]
    # 基准图必须被清理（否则每次 Ctrl+A 都在临时目录留一张 PNG）
    assert removed == ["before.png"]


async def test_probe_after_screen_changed_has_no_warning(monkeypatch):
    func = _func(task_id="bg-1", op="key", key="ctrl+a")

    async def _diff(self, pid, before, *, window, tolerance):
        return {"changed": True, "changed_ratio": 0.12}

    monkeypatch.setattr(BashOptFunc, "_diff_result", _diff)
    monkeypatch.setattr(BashOptFunc, "_remove_temp", staticmethod(lambda path: None))

    result = await func._probe_shortcut_after(
        4321, {"kind": "screen", "key": "ctrl+a", "path": "before.png"})

    assert result["changed"] is True
    assert result["changed_ratio"] == pytest.approx(0.12)
    assert "warning" not in result


async def test_probe_after_screen_missing_baseline_warns(monkeypatch):
    """基准图截取失败时不下结论，但给出可执行的复核建议。"""
    func = _func(task_id="bg-1", op="key", key="ctrl+a")

    result = await func._probe_shortcut_after(
        4321, {"kind": "screen", "key": "ctrl+a", "path": None})

    assert result["changed"] is None
    assert "warning" in result
    assert "elements" in result["warning"]


async def test_probe_after_screen_compare_failed_warns(monkeypatch):
    func = _func(task_id="bg-1", op="key", key="ctrl+a")

    async def _diff(self, pid, before, *, window, tolerance):
        return {"changed": None, "reason": "无法截取注入后的截图"}

    monkeypatch.setattr(BashOptFunc, "_diff_result", _diff)
    monkeypatch.setattr(BashOptFunc, "_remove_temp", staticmethod(lambda path: None))

    result = await func._probe_shortcut_after(
        4321, {"kind": "screen", "key": "ctrl+a", "path": "before.png"})

    assert result["changed"] is None
    assert "无法截取注入后的截图" in result["warning"]


# ── 剪贴板探针（Ctrl+C） ────────────────────────────────

async def test_probe_after_clipboard_unchanged_warns(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "read_clipboard_text", lambda: "same")
    func = _func(task_id="bg-1", op="key", key="ctrl+c")

    result = await func._probe_shortcut_after(
        4321, {"kind": "clipboard", "key": "ctrl+c", "text": "same"})

    assert result["changed"] is False
    assert "warning" in result
    assert "Ctrl+C" in result["warning"]


async def test_probe_after_clipboard_changed_has_no_warning(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "read_clipboard_text", lambda: "new")
    func = _func(task_id="bg-1", op="key", key="ctrl+c")

    result = await func._probe_shortcut_after(
        4321, {"kind": "clipboard", "key": "ctrl+c", "text": "old"})

    assert result["changed"] is True
    assert "warning" not in result


# ── 集成：keys（GUI）结果附带探针结论 ────────────────────

async def test_keys_gui_attaches_shortcut_probe(monkeypatch):
    seen = {}

    async def _before(self, pid, key_text):
        seen["before"] = (pid, key_text)
        return {"kind": "screen", "key": "ctrl+a", "path": None}

    async def _after(self, pid, probe):
        seen["after"] = probe
        return {"key": "ctrl+a", "method": "screen", "changed": True}

    monkeypatch.setattr(BashOptFunc, "_probe_shortcut_before", _before)
    monkeypatch.setattr(BashOptFunc, "_probe_shortcut_after", _after)

    def _send(pid, action):
        return InputResult(
            action=action.name, backend="test", window_pid=pid,
            window_title="demo", detail={"key": action.shortcut.display()})

    monkeypatch.setattr(bash_opt_module, "send_window_input", _send)
    func = _func(task_id="bg-1", op="keys", key="ctrl+a")

    payload = json.loads(await func._send_keys_to_window(4321, "ctrl+a"))

    assert payload["shortcut_probe"]["changed"] is True
    assert seen["before"] == (4321, "ctrl+a")
    assert seen["after"]["key"] == "ctrl+a"


async def test_keys_gui_failure_cleans_probe_screenshot(monkeypatch):
    """注入失败时同样要清理探针基准图（不能只在成功路径清理）。"""
    removed: list[str] = []

    async def _before(self, pid, key_text):
        return {"kind": "screen", "key": "ctrl+a", "path": "probe.png"}

    monkeypatch.setattr(BashOptFunc, "_probe_shortcut_before", _before)
    monkeypatch.setattr(BashOptFunc, "_remove_temp",
                        staticmethod(lambda path: removed.append(path)))

    def _send(pid, action):
        raise InputError("boom")

    monkeypatch.setattr(bash_opt_module, "send_window_input", _send)
    func = _func(task_id="bg-1", op="keys", key="ctrl+a")

    with pytest.raises(InputError):
        await func._send_keys_to_window(4321, "ctrl+a")

    assert removed == ["probe.png"]
