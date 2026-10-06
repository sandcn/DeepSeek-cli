"""``bash_opt`` ``op=keys``（终端键盘输入）工具层测试。

覆盖：schema 按键说明、按键缺失/非法/不支持键的错误透出、PTY 与 PIPE
两种写入方式下实际发出的字节序列，以及键名规则与 GUI ``op=key`` 的统一。
"""

from __future__ import annotations

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(mode: str = "pty", status: str = "running") -> dict:
    return {
        "read_buffer": "",
        "status": status,
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


# ── schema ───────────────────────────────────────────────

def test_schema_documents_keys_aliases_and_combinations():
    schema = BashOptFunc.to_tool_schema()
    key_desc = schema["function"]["parameters"]["properties"]["key"]["description"]
    for token in ("ctrl+c", "esc", "pageup", "单个字符", "f1-f20"):
        assert token in key_desc, token


# ── 成功写入 ─────────────────────────────────────────────

async def test_keys_writes_pty_sequence(monkeypatch):
    written: list[bytes] = []

    async def _fake_write(fd, data):
        written.append((fd, bytes(data)))

    monkeypatch.setattr(bash_opt_module, "_write_pty_all", _fake_write)
    rec = _record(mode="pty")
    result = await _run_keys(rec, "up")
    assert "已向后台任务" in result
    assert written == [(9, b"\x1b[A")]


async def test_keys_accepts_control_combination(monkeypatch):
    written: list[bytes] = []

    async def _fake_write(fd, data):
        written.append((fd, bytes(data)))

    monkeypatch.setattr(bash_opt_module, "_write_pty_all", _fake_write)
    rec = _record(mode="pty")
    await _run_keys(rec, "ctrl+c")
    assert written == [(9, b"\x03")]


async def test_keys_writes_pipe_sequence():
    rec = _record(mode="pipe")
    sent: list[bytes] = []

    class _Writer:
        def write(self, data):
            sent.append(bytes(data))

        async def drain(self):
            return None

    rec["stdin_writer"] = _Writer()
    await _run_keys(rec, "esc")
    assert sent == [b"\x1b"]


# ── 错误路径 ─────────────────────────────────────────────

async def test_keys_missing_key_parameter():
    rec = _record()
    result = await _run_keys(rec, None)
    assert "需要 key 参数" in result


@pytest.mark.parametrize("key", ["unknown_key", "ctrl+", "f21"])
async def test_keys_invalid_key_reports_error(key):
    rec = _record()
    result = await _run_keys(rec, key)
    assert result.startswith("(按键解析失败")
    assert "终端按键支持" in result


async def test_keys_not_ready_process():
    """进程句柄未建立（mode 缺失）时给出重试提示，而非静默失败。"""
    rec = _record()
    rec["mode"] = None
    result = await _run_keys(rec, "enter")
    assert "尚未就绪" in result
