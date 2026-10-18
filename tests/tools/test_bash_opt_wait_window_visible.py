"""op=wait_window 默认等到窗口「可用」（可见且未最小化）。

背景（真实用例）：启动泰拉瑞亚后 ``wait_window`` 立刻返回 ``found=true``，
但窗口 ``visible=false``（游戏还在初始化）——紧接着的 ``screenshot`` /
``click`` 用 ``main`` 选择器全部失败，还要再等一轮。现在默认等窗口真正可见、
可注入输入才返回；``allow_hidden=true`` 保留旧行为（直接操作隐藏 / 最小化
窗口），超时时也会明确指出「窗口已存在但不可见，应先 restore」。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.windows import WindowInfo
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid: int = 4321) -> dict:
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _window(title: str = "游戏", *, visible: bool = True,
            minimized: bool = False) -> WindowInfo:
    return WindowInfo(handle=0x10, pid=99, title=title, class_name="GameWindow",
                      width=800, height=600, order=0, main=True,
                      visible=visible, minimized=minimized)


def _fast(monkeypatch, interval: float = 0.01) -> None:
    monkeypatch.setattr(BashOptFunc, "_WAIT_WINDOW_INTERVAL", interval)


def _run(monkeypatch, windows, **kwargs) -> str:
    monkeypatch.setattr(bash_opt_module, "list_process_windows", windows)
    _fast(monkeypatch)
    func = BashOptFunc(task_id="bg-1", op="wait_window", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    return func.execute()


async def test_wait_window_keeps_waiting_while_hidden(monkeypatch):
    """窗口先以 visible=false 出现：继续等到可见后再返回。"""
    calls = {"count": 0}

    def _impl(pid):
        calls["count"] += 1
        if calls["count"] < 3:
            return [_window(visible=False)]
        return [_window(visible=True)]

    result = await _run(monkeypatch, _impl, timeout=2.0)
    payload = json.loads(result)
    assert payload["found"] is True
    assert payload["visible"] is True
    assert payload["selectable"] is True
    assert payload["attempts"] >= 3


async def test_wait_window_times_out_with_restore_hint(monkeypatch):
    """一直不可见：超时信息说明窗口已存在，并给出 restore 指引。"""
    result = await _run(monkeypatch, lambda pid: [_window(visible=False)],
                        timeout=0.05)
    assert result.startswith("(等待窗口超时")
    assert "已经存在" in result
    assert "restore" in result
    assert "allow_hidden" in result


async def test_wait_window_allow_hidden_returns_hidden_window(monkeypatch):
    """allow_hidden=true：命中的隐藏窗口直接返回，并提示可操作性问题。"""
    result = await _run(monkeypatch, lambda pid: [_window(visible=False)],
                        timeout=2.0, allow_hidden=True)
    payload = json.loads(result)
    assert payload["found"] is True
    assert payload["visible"] is False
    assert payload["selectable"] is False
    assert "allow_hidden=true" in payload["hint"]


async def test_wait_window_minimized_window_is_not_selectable(monkeypatch):
    result = await _run(monkeypatch, lambda pid: [_window(minimized=True)],
                        timeout=0.05)
    assert result.startswith("(等待窗口超时")
    assert "已经存在" in result


async def test_wait_window_reports_selectable_fields(monkeypatch):
    result = await _run(monkeypatch, lambda pid: [_window()], timeout=1.0)
    payload = json.loads(result)
    assert payload["found"] is True
    assert payload["visible"] is True
    assert payload["selectable"] is True


def test_schema_exposes_allow_hidden():
    schema = BashOptFunc.to_tool_schema()
    props = schema["function"]["parameters"]["properties"]
    assert props["allow_hidden"]["type"] == "boolean"
    assert "wait_window" in props["allow_hidden"]["description"]
