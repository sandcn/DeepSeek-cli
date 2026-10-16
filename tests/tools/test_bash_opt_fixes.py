"""bash_opt 修复项回归测试（行为缺陷 / 资源泄漏 / 健壮性 / 文档一致性）。

覆盖以下修复（每条对应一个测试组）：

1. ``op=read`` 等只读观察 op **不再接管**任务（不再让任务结果被静默丢弃）；
2. ``read_buffer`` 内存上限 + ``op=read`` 返回截断；
3. ``_await_screen``（wait_for=stable）不再泄漏临时截图；
4. ``op=keys`` 的窗口探测在线程中执行（不阻塞事件循环）；
5. ``asyncio.wait_for`` 超时统一转为可读的领域错误（不再输出空的「内部错误」）；
6. PTY 写入有总超时（写入不再无限挂起）；
7. ``op=record`` / ``op=replay`` 的非 dict 步骤给出可读参数错误；
8. ``op=kill`` / ``op=wait`` 之后对同 task_id 的操作给出「已结束」精确提示；
9. schema：function description 覆盖全部 op、timeout/on_error/seconds 描述对齐实现；
10. ``op=windows`` 全量计数 + 截断提示；
11. ``op=replay`` 返回的宏路径与 ``op=record`` 一致（绝对路径）；
12. ``grid`` 支持步骤级显式关闭（``None``）与继承实例值（未指定）；
13. ``element`` 语义：拒绝 release、回报聚焦点击、整屏截图忽略无关参数；
14. ``display_params`` 展示补全。
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import tempfile
import threading
import time

import pytest
from PIL import Image

from src.tools import bash as bash_module
from src.tools.bash_opt import BashOptFunc
from src.tools._screenshot.elements import ElementInfo
from src.tools._screenshot.monitors import Monitor, MonitorError
from src.tools._screenshot.result import CaptureResult
from src.tools._screenshot.windows import WindowInfo
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.macro import Macro
from src.tools._window_input.result import InputResult


class _FakeAgent:
    def __init__(self, records: dict | None = None):
        self._background_tasks = dict(records or {})
        self._subagent_tasks = {}

    def _remove_background_task(self, task_id):
        return self._background_tasks.pop(task_id, None)


class _FakeStdin:
    """PIPE stdin 写端替身（记录写入内容）。"""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        return None


def _record(pid=4321, **extra):
    rec = {"read_buffer": "", "read_buffer_dropped": 0, "status": "running",
           "done": False, "pid": pid, "task": None, "io_lock": asyncio.Lock()}
    rec.update(extra)
    return rec


def _func(agent, task_id="bg-1", **kwargs):
    func = BashOptFunc(task_id=task_id, **kwargs)
    func.set_agent(agent)
    return func


def _call(agent, task_id="bg-1", **kwargs):
    return _func(agent, task_id, **kwargs).execute()


# ═══════════════════════════════════════════════════════════
# 1. 接管语义：只读观察 op 不再让任务结果丢失
# ═══════════════════════════════════════════════════════════

async def test_read_does_not_take_over_task():
    agent = _FakeAgent({"bg-1": _record()})
    await _call(agent, op="read")
    assert not agent._background_tasks["bg-1"].get("managed_by_tool")


async def test_windows_op_does_not_take_over_task():
    agent = _FakeAgent({"bg-1": _record()})
    await _call(agent, op="windows")
    assert not agent._background_tasks["bg-1"].get("managed_by_tool")


async def test_stdin_takes_over_task():
    stdin = _FakeStdin()
    agent = _FakeAgent({"bg-1": _record(mode="pipe", stdin_writer=stdin)})
    result = await _call(agent, op="stdin", text="hi")
    assert stdin.written and agent._background_tasks["bg-1"]["managed_by_tool"]
    assert "发送 stdin" in result


async def test_wait_timeout_does_not_take_over_task():
    async def _never():
        await asyncio.sleep(5)

    task = asyncio.ensure_future(_never())
    agent = _FakeAgent({"bg-1": _record(task=task)})
    try:
        result = await _call(agent, op="wait", timeout=0.01)
        assert "超时" in result
        assert not agent._background_tasks["bg-1"].get("managed_by_tool")
    finally:
        task.cancel()
        await asyncio.sleep(0)


# ═══════════════════════════════════════════════════════════
# 2. read_buffer 上限 + op=read 返回截断
# ═══════════════════════════════════════════════════════════

async def test_append_read_buffer_caps_memory(monkeypatch):
    monkeypatch.setattr(bash_module, "_READ_BUFFER_MAX_CHARS", 100)
    rec = _record()
    await bash_module._append_read_buffer(rec, "a" * 80)
    await bash_module._append_read_buffer(rec, "b" * 80)
    assert len(rec["read_buffer"]) == 100
    assert rec["read_buffer"].endswith("b" * 80)
    assert rec["read_buffer_dropped"] == 60


async def test_read_truncates_and_reports_dropped(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_READ_OUTPUT_MAX_CHARS", 50)
    rec = _record(read_buffer="x" * 200, read_buffer_dropped=7)
    agent = _FakeAgent({"bg-1": rec})
    payload = json.loads(await _call(agent, op="read"))
    assert payload["truncated"] is True
    assert payload["dropped_chars"] == 150
    assert payload["output"] == "x" * 50
    assert payload["buffer_overflow_chars"] == 7
    assert rec["read_buffer"] == ""
    assert rec.get("read_buffer_dropped", 0) == 0


# ═══════════════════════════════════════════════════════════
# 3. _await_screen 不再泄漏临时截图
# ═══════════════════════════════════════════════════════════

async def test_await_screen_cleans_temp_files(monkeypatch):
    tmp = tempfile.gettempdir()
    pattern = os.path.join(tmp, "bash_opt-bg-leak-*.png")
    for path in glob.glob(pattern):
        os.remove(path)

    counter = {"n": 0}

    async def fake_capture(self, pid, path, crop=None, *, window=None, grid=None):
        counter["n"] += 1
        color = (counter["n"] * 9 % 256, 0, 0)
        Image.new("RGB", (8, 8), color).save(path)  # 每轮都不同 → 持续变化
        return CaptureResult(path=path, width=8, height=8, window_pid=pid,
                             window_title="", backend="fake")

    monkeypatch.setattr(BashOptFunc, "_capture_with_retry", fake_capture)
    func = BashOptFunc(task_id="bg-leak", op="click", x=1, y=1)
    result = await func._await_screen(1, mode="stable", before_path=None,
                                      window=None, timeout=0.5)
    assert result["samples"] >= 2, "至少两轮采样才会触发稳定判定"
    assert glob.glob(pattern) == [], "stable 等待不得在临时目录残留截图"


# ═══════════════════════════════════════════════════════════
# 4. op=keys 的窗口探测在线程中执行
# ═══════════════════════════════════════════════════════════

async def test_keys_probe_window_runs_off_event_loop(monkeypatch):
    seen = {}

    def fake_probe(pid):
        seen["thread"] = threading.current_thread()
        seen["pid"] = pid
        return None

    monkeypatch.setattr("src.tools.bash_opt.probe_window", fake_probe)
    agent = _FakeAgent({"bg-1": _record(mode="pipe", stdin_writer=_FakeStdin())})
    await _call(agent, op="keys", key="enter")
    assert seen["pid"] == 4321
    assert seen["thread"] is not threading.main_thread(), (
        "窗口探测必须在线程中执行，不能阻塞事件循环")


# ═══════════════════════════════════════════════════════════
# 5. 超时统一转为可读的领域错误
# ═══════════════════════════════════════════════════════════

def _slow_elements(pid, window=None):
    time.sleep(0.5)
    return []


async def test_element_lookup_timeout_is_readable(monkeypatch):
    monkeypatch.setattr("src.tools.bash_opt.list_process_elements", _slow_elements)
    monkeypatch.setattr(BashOptFunc, "_INPUT_TIMEOUT", 0.05)
    agent = _FakeAgent({"bg-1": _record()})
    result = await _call(agent, op="click", element="确定")
    assert result.startswith("(控件定位失败")
    assert "超时" in result
    assert "内部错误" not in result


async def test_screenshot_element_timeout_is_readable(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tools.bash_opt.list_process_elements", _slow_elements)
    monkeypatch.setattr(BashOptFunc, "_INPUT_TIMEOUT", 0.05)
    agent = _FakeAgent({"bg-1": _record()})
    result = await _call(agent, op="screenshot", path=str(tmp_path / "s.png"),
                         element="确定")
    assert result.startswith("(截图失败")
    assert "超时" in result
    assert "内部错误" not in result


async def test_window_frame_timeout_returns_none(monkeypatch):
    class _SlowBackend:
        name = "slow"

        def locate(self, pid, window=None):
            time.sleep(0.5)
            return None

    monkeypatch.setattr("src.tools.bash_opt.resolve_input_backend", lambda: _SlowBackend())
    monkeypatch.setattr(BashOptFunc, "_INPUT_TIMEOUT", 0.05)
    func = BashOptFunc(task_id="bg-1", op="click", x=1, y=1)
    assert await func._window_frame_for(1, None) is None


async def test_resolve_monitor_timeout_raises_monitor_error(monkeypatch):
    def _slow_monitors():
        time.sleep(0.5)
        return [Monitor(0, 0, 100, 100, primary=True)]

    monkeypatch.setattr("src.tools.bash_opt.list_monitors", _slow_monitors)
    monkeypatch.setattr(BashOptFunc, "_INPUT_TIMEOUT", 0.05)
    func = BashOptFunc(task_id="bg-1", op="screenshot", path="x.png", screen=True)
    with pytest.raises(MonitorError):
        await func._resolve_monitor(None)


# ═══════════════════════════════════════════════════════════
# 6. PTY 写入总超时
# ═══════════════════════════════════════════════════════════

async def test_write_pty_all_times_out(monkeypatch):
    import src.tools.bash_opt as module

    def _always_blocking(fd, data):
        raise BlockingIOError(11, "resource temporarily unavailable")

    monkeypatch.setattr(module.os, "write", _always_blocking)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await module._write_pty_all(1, b"payload", timeout=0.05, retry_interval=0.005)
    assert time.monotonic() - started < 1.0


async def test_pty_write_timeout_reports_readable_error(monkeypatch):
    import src.tools.bash_opt as module

    def _always_blocking(fd, data):
        raise BlockingIOError(11, "resource temporarily unavailable")

    monkeypatch.setattr(module.os, "write", _always_blocking)
    monkeypatch.setattr(module, "_PTY_WRITE_TIMEOUT", 0.05)
    monkeypatch.setattr(module, "_PTY_WRITE_RETRY_INTERVAL", 0.005)
    agent = _FakeAgent({"bg-1": _record(mode="pty", master_fd=1)})
    result = await _call(agent, op="stdin", text="hi")
    assert result.startswith("(写入后台任务")
    assert "超时" in result


# ═══════════════════════════════════════════════════════════
# 7. record / replay 步骤校验
# ═══════════════════════════════════════════════════════════

async def test_record_non_dict_step_is_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_MACRO_DIR", str(tmp_path / "macros"))
    agent = _FakeAgent({"bg-1": _record()})
    result = await _call(agent, op="record", macro="m1", actions=["not-a-dict"])
    assert result.startswith("(record 失败")
    assert "内部错误" not in result


async def test_replay_non_mapping_steps_is_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_MACRO_DIR", str(tmp_path / "macros"))
    monkeypatch.setattr("src.tools.bash_opt.load_macro",
                        lambda **kwargs: Macro(name="bad", steps=("not-a-dict",)))
    agent = _FakeAgent({"bg-1": _record()})
    result = await _call(agent, op="replay", macro="bad")
    assert result.startswith("(replay 失败")
    assert "内部错误" not in result


async def test_replay_path_matches_record_absolute(monkeypatch, tmp_path):
    macro_dir = tmp_path / "macros"
    monkeypatch.setattr(BashOptFunc, "_MACRO_DIR", str(macro_dir))
    agent = _FakeAgent({"bg-1": _record()})
    recorded = json.loads(await _call(agent, op="record", macro="abs1",
                                      actions=[{"op": "wait", "seconds": 0}]))
    replayed = json.loads(await _call(agent, op="replay", macro="abs1"))
    assert os.path.isabs(recorded["path"])
    assert replayed["path"] == recorded["path"]


# ═══════════════════════════════════════════════════════════
# 8. kill / wait 之后的「已结束」精确提示
# ═══════════════════════════════════════════════════════════

async def test_kill_then_read_reports_finished(monkeypatch):
    from src.tools._bash_support import KillResult

    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree",
                        lambda pid: KillResult(pid=pid, attempts=1, verified=True))
    agent = _FakeAgent({"bg-1": _record(process=None)})
    killed = await _call(agent, op="kill")
    assert "已杀死" in killed
    again = await _call(agent, op="read")
    assert "已结束" in again and "kill" in again


async def test_wait_then_read_reports_finished():
    rec = _record(done=True, status="completed", stdout="ok", stderr="",
                  returncode=0)
    agent = _FakeAgent({"bg-1": rec})
    waited = json.loads(await _call(agent, op="wait"))
    assert waited["stdout"] == "ok"
    again = await _call(agent, op="read")
    assert "已结束" in again and "wait" in again


# ═══════════════════════════════════════════════════════════
# 9. schema / 描述与实现对齐
# ═══════════════════════════════════════════════════════════

def test_function_description_covers_all_key_ops():
    schema = BashOptFunc.to_tool_schema()["function"]
    description = schema["description"]
    for name in ("locate", "pixel", "annotate", "record", "replay", "release"):
        assert name in description, name


def test_schema_docs_match_implementation():
    props = BashOptFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "wait_window" in props["timeout"]["description"]
    assert "replay" in props["on_error"]["description"]
    assert "步骤内字段" in props["seconds"]["description"]


# ═══════════════════════════════════════════════════════════
# 10. op=windows 全量计数 + 截断提示
# ═══════════════════════════════════════════════════════════

async def test_windows_op_counts_all_windows(monkeypatch):
    infos = [WindowInfo(handle=0x1000 + index, pid=1, title=f"w{index}",
                        class_name="C", width=100, height=100) for index in range(60)]
    monkeypatch.setattr("src.tools.bash_opt.list_process_windows", lambda pid: infos)
    agent = _FakeAgent({"bg-1": _record()})
    payload = json.loads(await _call(agent, op="windows"))
    assert payload["windows_total"] == 60
    assert payload["selectable_total"] == 60
    assert len(payload["windows"]) == 60


async def test_windows_op_marks_truncation(monkeypatch):
    infos = [WindowInfo(handle=0x1000 + index, pid=1, title=f"w{index}",
                        class_name="C", width=100, height=100) for index in range(5)]
    monkeypatch.setattr("src.tools.bash_opt.list_process_windows", lambda pid: infos)
    monkeypatch.setattr(BashOptFunc, "_MAX_WINDOW_LIST", 3)
    agent = _FakeAgent({"bg-1": _record()})
    payload = json.loads(await _call(agent, op="windows"))
    assert payload["truncated"] is True
    assert payload["returned"] == 3
    assert payload["selectable_total"] == 5  # 计数基于全量
    assert "仅返回前 3 条" in payload["hint"]


# ═══════════════════════════════════════════════════════════
# 11. grid：步骤级显式关闭 / 继承实例值
# ═══════════════════════════════════════════════════════════

def test_resolve_grid_unset_inherits_and_none_disables():
    from src.tools.bash_opt import _UNSET

    func = BashOptFunc(task_id="bg-1", op="screenshot", path="x.png", grid=25)
    assert func._resolve_grid() == 25
    assert func._resolve_grid(_UNSET) == 25
    assert func._resolve_grid(None) is None
    assert func._resolve_grid(False) is None
    assert func._resolve_grid(0) == 0


# ═══════════════════════════════════════════════════════════
# 12. element 语义
# ═══════════════════════════════════════════════════════════

async def test_element_with_release_is_rejected():
    agent = _FakeAgent({"bg-1": _record()})
    result = await _call(agent, op="release", element="确定")
    assert result.startswith("(输入参数非法")
    assert "release" in result


async def test_element_focus_click_is_reported(monkeypatch):
    element = ElementInfo(handle=1, pid=4321, class_name="Edit", text="输入",
                          left=100, top=50, width=80, height=20)

    async def fake_frame(self, pid, window):
        return WindowFrame(10, 10, 400, 300)

    def fake_send(pid, action):
        return InputResult(action=action.name, backend="fake", window_pid=pid,
                           window_title="T", detail={})

    monkeypatch.setattr("src.tools.bash_opt.list_process_elements",
                        lambda pid, window=None: [element])
    monkeypatch.setattr(BashOptFunc, "_window_frame_for", fake_frame)
    monkeypatch.setattr("src.tools.bash_opt.send_window_input", fake_send)
    agent = _FakeAgent({"bg-1": _record()})
    payload = json.loads(await _call(agent, op="type", text="hi", element="输入"))
    assert payload["element"]["text"] == "输入"
    assert payload["focus_click"]["x"] == element.center_x - 10
    assert payload["focus_click"]["y"] == element.center_y - 10
    assert "点击" in payload["focus_click"]["note"]


async def test_screen_screenshot_notes_ignored_window_params(monkeypatch, tmp_path):
    monitor = Monitor(0, 0, 100, 100, primary=True)

    def fake_capture_screen(target, path, crop=None, grid=None):
        with open(path, "wb") as handle:
            handle.write(b"png")
        return CaptureResult(path=path, width=100, height=100, window_pid=0,
                             window_title="", backend="fake")

    monkeypatch.setattr("src.tools.bash_opt.list_monitors", lambda: [monitor])
    monkeypatch.setattr("src.tools.bash_opt.capture_screen", fake_capture_screen)
    agent = _FakeAgent({"bg-1": _record()})
    payload = json.loads(await _call(agent, op="screenshot",
                                     path=str(tmp_path / "s.png"),
                                     screen=True, window="main"))
    assert "忽略" in payload["hint"]


# ═══════════════════════════════════════════════════════════
# 13. display_params 展示补全
# ═══════════════════════════════════════════════════════════

def test_display_params_annotate_and_record():
    annotate = BashOptFunc.display_params({
        "task_id": "bg-1", "op": "annotate", "boxes": ["1,2,3,4"],
        "grid": 20, "output": "out.png"})
    assert "grid=20" in annotate and "out.png" in annotate
    record = BashOptFunc.display_params({
        "task_id": "bg-1", "op": "record", "macro": "m",
        "actions": [{"op": "wait", "seconds": 0}], "append": True})
    assert "append" in record


async def test_record_append_flag_reaches_disk(monkeypatch, tmp_path):
    macro_dir = tmp_path / "macros"
    monkeypatch.setattr(BashOptFunc, "_MACRO_DIR", str(macro_dir))
    agent = _FakeAgent({"bg-1": _record()})
    await _call(agent, op="record", macro="app", actions=[{"op": "wait", "seconds": 0}])
    payload = json.loads(await _call(agent, op="record", macro="app", append=True,
                                     actions=[{"op": "wait", "seconds": 0}]))
    assert payload["appended"] is True and payload["steps"] == 1
    with open(payload["path"], encoding="utf-8") as handle:
        saved = json.loads(handle.read())
    assert len(saved["steps"]) == 2
