"""bash_opt op=screenshot（进程窗口截图）工具层测试。

覆盖：schema 暴露（op 枚举 + path + crop 参数）、参数校验（缺 path / 无 pid /
未知 op / crop 格式非法 / crop 越界）、输出路径规范化（补 .png / 建父目录 /
目录拒绝）、成功路径 JSON 结果、「暂无窗口」轮询重试、截图失败与超时的错误透出、
crop 区域裁剪（参数传递、结果 JSON、真实窗口端到端）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png as png_mod
from src.tools._screenshot.result import CaptureResult, NoWindowError, ScreenshotError
from src.tools._screenshot.transform import CropError, CropRegion
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
    # 2026-10-07 新增窗口输入 op（click/move/drag/scroll/key/type）
    # 与窗口枚举 / 窗口控制 op（windows/window）
    # 2026-10-08 新增 elements / wait_window / clipboard / sequence
    assert set(params["properties"]["op"]["enum"]) == {
        "read", "wait", "kill", "stdin", "keys", "screenshot",
        "windows", "window",
        "elements", "wait_window", "clipboard", "locate", "sequence",
        "click", "move", "hover", "drag", "scroll", "key", "type",
    }
    assert "path" in params["properties"]
    assert "PNG" in params["properties"]["path"]["description"]
    assert params["required"] == ["task_id", "op"]


def test_schema_exposes_crop_parameter():
    """crop 参数暴露且说明格式；crop 非必填，op 描述中点明其用途。"""
    schema = BashOptFunc.to_tool_schema()
    params = schema["function"]["parameters"]
    assert "crop" in params["properties"]
    description = params["properties"]["crop"]["description"]
    assert "x,y,width,height" in description
    assert "screenshot" in description
    assert "crop" not in params["required"]
    assert "crop" in params["properties"]["op"]["description"]
    assert "crop" in schema["function"]["description"]


def test_display_params_shows_screenshot_path():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "screenshot", "path": "out/shot.png"}
    ) == "'screenshot bg-1 out/shot.png'"


def test_display_params_shows_crop():
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "screenshot", "path": "out/shot.png",
         "crop": "1,2,30,40"}
    ) == "'screenshot bg-1 out/shot.png crop=1,2,30,40'"


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

    def _fake_capture(pid, path, crop=None):
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

    def _fake_capture(pid, path, crop=None):
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

    def _always_no_window(pid, path, crop=None):
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

    def _boom(pid, path, crop=None):
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

    def _slow(pid, path, crop=None):
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

    def _fake_capture(pid, path, crop=None):
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


# ── 裁剪（crop 指定大小） ────────────────────────────────

async def test_screenshot_passes_crop_region_to_capture(monkeypatch, tmp_path):
    """crop 解析为 CropRegion 传给截图后端；结果 JSON 回带 crop。"""
    seen = {}

    def _fake_capture(pid, path, crop=None):
        seen["crop"] = crop
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(path=path, width=10, height=20, window_pid=pid,
                             window_title="game", backend="windows")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    agent = _FakeAgent({"bg-1": _record(pid=99)})
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "crop.png"), crop="5,6,10,20")
    func.set_agent(agent)

    payload = json.loads(await func.execute())
    assert seen["crop"] == CropRegion(5, 6, 10, 20)
    assert payload["crop"] == {"x": 5, "y": 6, "width": 10, "height": 20}
    assert payload["width"] == 10 and payload["height"] == 20


async def test_screenshot_without_crop_sends_none(monkeypatch, tmp_path):
    """未传 crop 时后端收到 None（保持整窗原始像素），结果 JSON 不含 crop。"""
    seen = {}

    def _fake_capture(pid, path, crop=None):
        seen["crop"] = crop
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(path=path, width=4, height=4, window_pid=pid,
                             window_title="", backend="windows")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot", path=str(tmp_path / "full.png"))
    func.set_agent(agent)

    payload = json.loads(await func.execute())
    assert seen["crop"] is None
    assert "crop" not in payload
    assert payload["width"] == 4 and payload["height"] == 4


async def test_screenshot_blank_crop_means_full_window(monkeypatch, tmp_path):
    """crop 为空串 / 空白视为未指定（整窗），不报错。"""
    seen = {}

    def _fake_capture(pid, path, crop=None):
        seen["crop"] = crop
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        return CaptureResult(path=path, width=3, height=3, window_pid=pid,
                             window_title="", backend="windows")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "blank.png"), crop="   ")
    func.set_agent(agent)

    payload = json.loads(await func.execute())
    assert seen["crop"] is None
    assert "crop" not in payload


async def test_screenshot_rejects_malformed_crop(monkeypatch, tmp_path):
    """crop 格式非法：给出可读错误且完全不执行截图。"""
    calls = {"count": 0}

    def _fake_capture(pid, path, crop=None):
        calls["count"] += 1
        raise AssertionError("参数非法时不应执行截图")

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    agent = _FakeAgent({"bg-1": _record()})
    for bad in ("abc", "1,2,3", "1,2,3,4,5", "-1,0,10,10", "0,0,0,10"):
        func = BashOptFunc(task_id="bg-1", op="screenshot",
                           path=str(tmp_path / "bad.png"), crop=bad)
        func.set_agent(agent)
        result = await func.execute()
        assert result.startswith("(")
        assert "裁剪参数非法" in result
    assert calls["count"] == 0


async def test_screenshot_reports_crop_out_of_range(monkeypatch, tmp_path):
    """crop 越界（后端抛 CropError）经截图失败路径透出，原因可直接阅读。"""
    _fast_retry(monkeypatch)

    def _fake_capture(pid, path, crop=None):
        raise CropError(
            "裁剪区域超出截图范围: x=100, y=50, width=800, height=600，"
            "需满足 x+width<=640 且 y+height<=480；当前窗口截图为 640x480"
        )

    monkeypatch.setattr(bash_opt_module, "capture_process_window", _fake_capture)
    agent = _FakeAgent({"bg-1": _record()})
    func = BashOptFunc(task_id="bg-1", op="screenshot",
                       path=str(tmp_path / "oor.png"), crop="100,50,800,600")
    func.set_agent(agent)

    started = time.monotonic()
    result = await func.execute()
    assert result.startswith("(")
    assert "截图失败" in result
    assert "超出截图范围" in result
    assert "640x480" in result
    assert time.monotonic() - started < 3


def test_resolve_crop_normalizes_separators():
    func = BashOptFunc(task_id="bg-1", op="screenshot", crop=" 7 8 9 10 ")
    assert func._resolve_crop() == CropRegion(7, 8, 9, 10)
    assert BashOptFunc(task_id="bg-1", op="screenshot")._resolve_crop() is None
    assert BashOptFunc(task_id="bg-1", op="screenshot", crop="")._resolve_crop() is None


async def test_screenshot_op_crop_real_window(monkeypatch, tmp_path, real_window_pids):
    """真实窗口 + crop：产物尺寸即裁剪尺寸（无桌面窗口时跳过）。"""
    if not real_window_pids:
        pytest.skip("当前环境无可见窗口（无桌面会话或非 Windows）")
    _fast_retry(monkeypatch)
    agent = _FakeAgent({})
    last_error = ""
    for pid in real_window_pids:
        agent._background_tasks["bg-crop"] = _record(pid=pid)
        probe = BashOptFunc(task_id="bg-crop", op="screenshot",
                            path=str(tmp_path / f"probe_{pid}.png"))
        probe.set_agent(agent)
        probe_result = await probe.execute()
        if probe_result.startswith("("):
            last_error = probe_result
            continue
        full_width, full_height = png_mod.read_png_size(
            json.loads(probe_result)["path"])
        crop_width = max(1, min(40, full_width))
        crop_height = max(1, min(30, full_height))
        func = BashOptFunc(
            task_id="bg-crop", op="screenshot",
            path=str(tmp_path / f"crop_{pid}.png"),
            crop=f"0,0,{crop_width},{crop_height}",
        )
        func.set_agent(agent)
        result = await func.execute()
        if result.startswith("("):
            last_error = result
            continue
        payload = json.loads(result)
        assert payload["crop"] == {"x": 0, "y": 0,
                                   "width": crop_width, "height": crop_height}
        assert payload["width"] == crop_width
        assert payload["height"] == crop_height
        assert png_mod.read_png_size(payload["path"]) == (crop_width, crop_height)
        return
    pytest.skip(f"候选窗口均未能截图: {last_error}")
