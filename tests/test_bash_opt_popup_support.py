"""``bash_opt`` 对弹层窗口（tool window）的支持：输出契约与错误指引测试。

覆盖：

  - ``op=windows`` 的窗口条目 / hint 暴露 ``tool_window`` 与 ``client_area``
    （模型据此预判弹层能否用消息投递）；
  - 输入失败时的可操作指引：无可换算客户区 → 建议去掉 ``method='message'``；
    选择器无匹配 → 建议先用 ``op=windows`` 复核；
  - 工具 schema 的 op 说明里保留这两项能力，避免文档回退。
"""

from __future__ import annotations

import json

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.windows import WindowInfo
from src.tools._window_input import InputError
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 桩：只提供 bash 后台任务表。"""

    def __init__(self, records: dict):
        self._background_tasks = records


def _record() -> dict:
    return {
        "read_buffer": "",
        "status": "running",
        "done": False,
        "pid": 4321,
        "mode": "pty",
        "task": None,
        "io_lock": None,
        "master_fd": 9,
        "stdin_writer": None,
    }


async def _run(func: BashOptFunc) -> str:
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    return await func.execute()


def _stub_send_error(monkeypatch, message: str) -> None:
    def _boom(pid, action):
        raise InputError(message)

    monkeypatch.setattr(bash_opt_module, "send_window_input", _boom)


# ── op=windows 输出 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_windows_output_exposes_popup_fields(monkeypatch):
    infos = [
        WindowInfo(handle=0x20, pid=99, title="", class_name="Chrome_WidgetWin_1",
                   width=151, height=279, tool_window=True, client_area=False),
    ]
    monkeypatch.setattr(bash_opt_module, "list_process_windows", lambda pid: infos)

    result = await _run(BashOptFunc(task_id="bg-1", op="windows"))
    payload = json.loads(result)
    entry = payload["windows"][0]
    assert entry["tool_window"] is True
    assert entry["client_area"] is False
    assert "no-client" in entry["summary"]
    hint = payload["hint"]
    assert "tool_window" in hint and "client_area" in hint
    assert "前台" in hint


@pytest.mark.asyncio
async def test_windows_output_without_windows_gives_empty_hint(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "list_process_windows", lambda pid: [])
    result = await _run(BashOptFunc(task_id="bg-1", op="windows"))
    payload = json.loads(result)
    assert payload["total"] == 0
    assert "未找到可见窗口" in payload["hint"]


# ── 输入失败的可操作指引 ─────────────────────────────────

@pytest.mark.asyncio
async def test_client_area_error_suggests_removing_message_method(monkeypatch):
    _stub_send_error(
        monkeypatch,
        "窗口 0x2720AAC 没有可换算的客户区（ClientToScreen / GetClientRect "
        "都不可用），无法用 PostMessage 换算鼠标坐标。",
    )
    func = BashOptFunc(task_id="bg-1", op="click", window="#1", x=55, y=115)
    result = await _run(func)
    assert result.startswith("(输入失败")
    assert "客户区" in result
    assert "method='message'" in result
    assert "合成输入" in result


@pytest.mark.asyncio
async def test_selector_error_suggests_rechecking_windows(monkeypatch):
    _stub_send_error(
        monkeypatch,
        "窗口选择器 '#1' 没有匹配的窗口。当前可用窗口: #1 0x10 …",
    )
    func = BashOptFunc(task_id="bg-1", op="click", window="#1", x=5, y=5)
    result = await _run(func)
    assert "op=windows" in result
    assert "重试" in result


@pytest.mark.asyncio
async def test_other_input_errors_pass_through(monkeypatch):
    _stub_send_error(monkeypatch, "目标进程没有可接收输入的可见窗口")
    func = BashOptFunc(task_id="bg-1", op="click", x=5, y=5)
    result = await _run(func)
    assert result == "(输入失败: 目标进程没有可接收输入的可见窗口)"


# ── schema 契约 ─────────────────────────────────────────

def test_schema_documents_popup_capabilities():
    description = json.dumps(BashOptFunc.to_tool_schema(), ensure_ascii=False)
    assert "tool_window" in description
    assert "client_area" in description
    assert "method='message'" in description
