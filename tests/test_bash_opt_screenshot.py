"""bash_opt op=screenshot（进程窗口截图）工具层测试。

覆盖：schema 暴露（op 枚举 + path 参数）、参数校验（缺 path / 无 pid / 未知 op）、
输出路径规范化（补 .png / 建父目录 / 目录拒绝）、成功路径 JSON 结果、
「暂无窗口」轮询重试、截图失败与超时的错误透出。
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot.result import CaptureResult, NoWindowError, ScreenshotError
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 桩：只提供 bash 后台任务表。"""

    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321, status="running"):
    return {
        "read_buffer": "",
        "status": status,
        "done": False,
        "pid": pid,
        "task": None,
    }


def _fast_retry(monkeypatch, wait_seconds: float = 0.2, interval: float = 0.01):
    """压缩重试参数，避免测试真的等待 5 秒。"""
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_WAIT_SECONDS", wait_seconds)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_RETRY_INTERVAL", interval)


# ── schema / 显示 ────────────────────────────────────────

def test_schema_exposes_screenshot_op_and_path():
    schema = BashOptFunc.to_tool_schema()
    params = schema["function"]["parameters"]
    assert "screenshot" in params["properties"]["op"]["enum"]
    assert set(params["properties"]["op"]["enum"]) == {
        "read", "wait", "kill", "stdin", "keys", "screenshot",
    }
    assert "path" in params["properties"]
    assert "PNG" in params["properties"]["path"]["description"]
    assert params["required"] == ["task_id", "op"]


def test_display_params_shows_screenshot_path():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "screenshot", "path": "out/shot.png"}
    ) == "'screenshot bg-1 out/shot.png'"


# ── 参数校验 ─────────────────────────────────────────────

async def test_screenshot_requires_path():
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot")
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "path" in result


async def test_screenshot_requires_ready_process(monkeypatch):
    _fast_retry(monkeypatch)
    agent = _FakeAgent({"bg-1": _record(pid=None)})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path="shot.png")
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "进程" in result


async def test_screenshot_rejects_directory_path(tmp_path):
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path))
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "目录" in result


async def test_unknown_op_mentions_screenshot():
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="nope")
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "screenshot" in result


# ── 输出路径规范化 ───────────────────────────────────────

def test_prepare_screenshot_path_appends_png_and_creates_parent(tmp_path):
    target = tmp_path / "nested" / "deep" / "shot"
    resolved = BashOptFunc._prepare_screenshot_path(str(target))
    assert resolved.endswith("shot.png")
    assert os.path.isdir(os.path.dirname(resolved))


def test_prepare_screenshot_path_keeps_existing_extension(tmp_path):
    target = tmp_path / "shot.png"
    assert BashOptFunc._prepare_screenshot_path(str(target)).endswith("shot.png")


def test_prepare_screenshot_path_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    resolved = BashOptFunc._prepare_screenshot_path("~/shot")
    assert resolved.startswith(os.path.realpath(str(tmp_path)))
    assert resolved.endswith("shot.png")


def test_prepare_screenshot_path_rejects_empty():
    with pytest.raises(ValueError):
        BashOptFunc._prepare_screenshot_path("   ")


# ── 成功路径 ─────────────────────────────────────────────

async def test_screenshot_success_returns_json(monkeypatch, tmp_path):
    captured = {}

    def _fake_capture(pid, path):
        captured["pid"] = pid
        captured["path"] = path
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(
            path=path, width=640, height=480,
            window_pid=pid, window_title="Game", backend="windows",
        )

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    rec = _record(pid=1234)
    agent = _FakeAgent({"bg-1": rec})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "shot"))
    func.set_agent(agent)

    payload = json.loads(await func.execute())
    assert payload["task_id"] == "bg-1"
    assert payload["op"] == "screenshot"
    assert payload["width"] == 640 and payload["height"] == 480
    assert payload["window_title"] == "Game"
    assert payload["window_pid"] == 1234
    assert payload["path"].endswith("shot.png")
    assert "read_image" in payload["hint"]
    assert captured["pid"] == 1234
    assert rec["managed_by_tool"] is True


# ── 重试与错误 ───────────────────────────────────────────

async def test_screenshot_retries_until_window_appears(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    calls = {"count": 0}

    def _fake_capture(pid, path):
        calls["count"] += 1
        if calls["count"] < 3:
            raise NoWindowError("暂无窗口")
        return CaptureResult(
            path=path, width=10, height=10, window_pid=pid,
            window_title="late", backend="windows",
        )

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "late.png"))
    func.set_agent(agent)

    payload = json.loads(await func.execute())
    assert calls["count"] == 3
    assert payload["window_title"] == "late"


async def test_screenshot_gives_up_after_wait_window(monkeypatch, tmp_path):
    _fast_retry(monkeypatch, wait_seconds=0.05, interval=0.01)

    def _always_no_window(pid, path):
        raise NoWindowError("进程没有可见窗口")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _always_no_window)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "none.png"))
    func.set_agent(agent)

    started = time.monotonic()
    result = await func.execute()
    assert result.startswith("(")
    assert "截图失败" in result
    assert "可见窗口" in result
    assert time.monotonic() - started < 3


async def test_screenshot_reports_backend_error(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)

    def _boom(pid, path):
        raise ScreenshotError("当前会话无可用截图工具")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _boom)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "x.png"))
    func.set_agent(agent)

    result = await func.execute()
    assert result.startswith("(")
    assert "截图失败" in result
    assert "截图工具" in result


async def test_screenshot_timeout_reported(monkeypatch, tmp_path):
    _fast_retry(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_SCREENSHOT_TIMEOUT", 0.05)

    def _slow(pid, path):
        time.sleep(1.0)
        raise AssertionError("不应在超时后完成")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _slow)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "slow.png"))
    func.set_agent(agent)

    result = await func.execute()
    assert result.startswith("(")
    assert "超时" in result
    await asyncio.sleep(1.1)  # 等待遗留线程结束，避免干扰后续测试


async def test_screenshot_marks_managed_and_keeps_task(monkeypatch, tmp_path):
    """截图为只读观察操作：不结束任务、不消费 read_buffer。"""

    def _fake_capture(pid, path):
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(path=path, width=2, height=2, window_pid=pid,
                             window_title="", backend="windows")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    rec = _record()
    rec["read_buffer"] = "progress"
    agent = _FakeAgent({"bg-1": rec})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "obs.png"))
    func.set_agent(agent)

    await func.execute()
    assert rec["read_buffer"] == "progress"
    assert rec["status"] == "running"
    assert "bg-1" in agent._background_tasks


# ── 端到端（真实截图链路） ───────────────────────────────

async def test_screenshot_op_captures_real_window(monkeypatch, tmp_path, real_window_pids):
    """op=screenshot 走真实后端产出 PNG 文件（无桌面窗口时跳过）。"""
    if not real_window_pids:
        pytest.skip("当前环境无可见窗口（无桌面会话或非 Windows）")
    _fast_retry(monkeypatch)
    agent = _FakeAgent({})
    last_error = ""
    for pid in real_window_pids:
        agent._background_tasks["bg-e2e"] = _record(pid=pid)
        func = BashOptFunc(
            task_id="bg-e2e", op="screenshot",
            path=str(tmp_path / f"e2e_{pid}.png"),
        )
        func.set_agent(agent)
        result = await func.execute()
        if result.startswith("("):
            last_error = result
            continue
        payload = json.loads(result)
        assert payload["op"] == "screenshot"
        assert payload["width"] > 0 and payload["height"] > 0
        assert payload["window_pid"] > 0
        assert os.path.exists(payload["path"])
        assert os.path.getsize(payload["path"]) > 0
        return
    pytest.skip(f"候选窗口均未能截图: {last_error}")
