"""bash_opt 已报告问题（P1–P6）回归测试。

覆盖：

  - P1：PTY 子进程建立控制终端（ctrl+c 才能变成 SIGINT），并做一次真实
        的「向 PTY master 写 \\x03 → shell 执行 INT trap」集成验证；
  - P2：clipboard / record / release / 带 path 的 annotate / screen=… 的
        screenshot 在没有 task_id 时仍可用；
  - P3：schema 与实现契约（shot/grid 类型、seconds 不再作为顶层参数、
        path / region 描述）；
  - P4：wait_window 在目标窗口超出默认清单上限时仍返回命中的窗口，
        elements 截断时给出 truncated 标记；
  - P5：read 的截断提示不再声称 wait 能拿到「完整结果」；release 不接管
        任务生命周期；
  - P6：locate 的模板路径走统一的图片路径安全校验。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import CaptureResult
from src.tools._screenshot.elements import ElementInfo
from src.tools._screenshot.monitors import Monitor
from src.tools._screenshot.windows import WindowInfo
from src.tools._window_input.action import INPUT_OPS
from src.tools._window_input.geometry import WindowFrame
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict | None = None):
        self._background_tasks = records or {}
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _func(**kwargs):
    func = BashOptFunc(**kwargs)
    func.set_agent(_FakeAgent())
    return func


# ═══════════════════════════════════════════════════════════
# P1：PTY 控制终端 / ctrl+c
# ═══════════════════════════════════════════════════════════

def test_pty_child_becomes_session_leader_with_controlling_terminal():
    """子进程必须有控制终端（start_new_session + TIOCSCTTY），\\x03 才是 SIGINT。

    修复后的实现：会话由 subprocess 的 C 层 ``start_new_session=True`` 建立
    （不再在 preexec_fn 里执行 Python 级 setsid——那会扩大 fork 后的死锁
    风险窗口），preexec_fn 只做一次 ``TIOCSCTTY``（作用在 fd 1，subprocess
    已把 slave dup 到标准句柄）。
    """
    from src.tools import bash as bash_module

    source = Path(bash_module.__file__).read_text(encoding="utf-8")
    assert "_attach_controlling_terminal" in source
    assert "TIOCSCTTY" in source
    assert "start_new_session=True" in source
    assert "preexec_fn=_attach_controlling_terminal" in source
    # 回归保护：不得再用 pass_fds 保留 slave fd——它会让子进程及全部后代
    # 多持有一份 slave 副本（实测孙进程出现 /dev/ptyN fd），既泄漏 fd，
    # 也破坏「输出全部关闭 → master 收 EOF」的语义。
    assert "pass_fds=(slave_fd,)" not in source


@pytest.mark.skipif(not hasattr(os, "setsid") or not hasattr(os, "write"),
                    reason="当前平台没有 POSIX PTY")
async def test_ctrl_c_interrupts_pty_child():
    """真实 PTY：向 master 写 \\x03，shell 的 INT trap 应执行并退出 42。"""
    from src.tools.bash import BashFunc

    fd_holder: dict = {}

    def _ready(master_fd):
        fd_holder["fd"] = master_fd

    task = asyncio.ensure_future(
        BashFunc(command="echo ready; sleep 30")
        ._run_pty(interactive=True, pty_ready_fn=_ready))
    try:
        for _ in range(300):
            if "fd" in fd_holder:
                break
            await asyncio.sleep(0.01)
        assert "fd" in fd_holder, "PTY master fd 未就绪"
        os.write(fd_holder["fd"], b"\x03")
        # 16 worker 并行跑全量测试时，事件循环调度可能被明显拖慢；给足
        # 余量（信号本身应在毫秒级送达，超时只用于判定「没有生效」）。
        result = await asyncio.wait_for(task, timeout=20)
    finally:
        if not task.done():
            task.cancel()
    # 控制终端生效时，SIGINT 会终止 shell（bash 语义 128+2）；
    # 若仍是一一普通字节，sleep 30 会让 wait_for 超时，测试失败。
    assert result["returncode"] == 130, result


# ═══════════════════════════════════════════════════════════
# P2：无 task_id 的 op
# ═══════════════════════════════════════════════════════════

def test_from_args_allows_taskless_op_without_task_id():
    func = BashOptFunc.from_args({"op": "clipboard",
                                  "clipboard_action": "get"})
    assert func.task_id == ""


async def test_clipboard_works_without_task(monkeypatch):
    monkeypatch.setattr(bash_opt_module, "read_clipboard_text", lambda: "clip")
    payload = json.loads(await _func(op="clipboard").execute())
    assert payload["text"] == "clip"


async def test_clipboard_works_after_task_finished(monkeypatch):
    """报告场景：任务已被 wait 取走、记录已移除，clipboard 仍应可用。"""
    monkeypatch.setattr(bash_opt_module, "read_clipboard_text", lambda: "late")
    func = BashOptFunc(task_id="bg-ended", op="clipboard")
    func.set_agent(_FakeAgent({}))
    payload = json.loads(await func.execute())
    assert payload["text"] == "late"


async def test_record_works_without_task(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_MACRO_DIR", str(tmp_path))
    func = _func(op="record", macro="m",
                 actions=[{"op": "wait", "seconds": 0.01}])
    payload = json.loads(await func.execute())
    assert Path(payload["path"]).is_file()
    assert payload["steps"] == 1


async def test_annotate_with_path_works_without_task(tmp_path):
    source = tmp_path / "in.png"
    output = tmp_path / "out.png"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(source)
    func = _func(op="annotate", path=str(source), points=["5,5"],
                 output=str(output))
    payload = json.loads(await func.execute())
    assert output.is_file()
    assert payload["path"] == str(output)


async def test_screen_screenshot_works_without_task(monkeypatch, tmp_path):
    monitor = Monitor(0, 0, 100, 80, True)
    monkeypatch.setattr(bash_opt_module, "list_monitors", lambda: [monitor])

    def _capture(monitor, path, crop=None, grid=None):
        Image.new("RGB", (monitor.width, monitor.height), (0, 0, 0)).save(path)
        return CaptureResult(
            path=path, width=monitor.width, height=monitor.height,
            window_pid=0, window_title="", backend="fake",
            window_selector="screen",
            window_x=monitor.left, window_y=monitor.top,
            window_rect={"x": monitor.left, "y": monitor.top,
                         "width": monitor.width, "height": monitor.height})

    monkeypatch.setattr(bash_opt_module, "capture_screen", _capture)
    func = _func(op="screenshot", path=str(tmp_path / "screen.png"),
                 screen=True)
    payload = json.loads(await func.execute())
    assert payload["width"] == 100 and payload["height"] == 80


async def test_release_works_without_task(monkeypatch):
    calls: list[tuple] = []

    def _fake_send(pid, action):
        calls.append((pid, action))
        return InputResult(action=action.name, backend="test",
                           window_pid=pid or 0, window_title="",
                           detail={"released_keys": ["w"]})

    monkeypatch.setattr(bash_opt_module, "send_window_input", _fake_send)
    payload = json.loads(await _func(op="release", keys=["w"]).execute())
    assert payload["action"] == "release"
    assert calls and calls[0][0] is None


async def test_task_op_without_task_id_reports_requirement():
    result = await _func(op="read").execute()
    assert result.startswith("(")
    assert "需要 task_id" in result
    assert "bg-xxx" in result


# ═══════════════════════════════════════════════════════════
# P3：schema 契约
# ═══════════════════════════════════════════════════════════

def test_schema_types_match_implementation():
    props = BashOptFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert set(props["shot"]["type"]) == {"string", "boolean"}
    assert set(props["grid"]["type"]) == {"number", "boolean"}
    assert "seconds" not in props
    assert "seconds" in props["actions"]["description"]
    assert "record" in props["path"]["description"]
    assert "整窗" in props["mode"]["description"]
    assert BashOptFunc.to_tool_schema()["function"]["parameters"]["required"] == ["op"]


def test_grid_accepts_boolean_like_strings():
    assert _func(op="screenshot", grid="true")._resolve_grid() == 0
    assert _func(op="screenshot", grid="auto")._resolve_grid() == 0
    assert _func(op="screenshot", grid="false")._resolve_grid() is None


# ═══════════════════════════════════════════════════════════
# P4：wait_window 清单上限 / elements 截断标记
# ═══════════════════════════════════════════════════════════

async def test_wait_window_finds_target_beyond_list_limit(monkeypatch):
    infos = [
        WindowInfo(handle=0x1000 + index, pid=99, title=f"w{index}",
                   class_name="C", width=100, height=100, order=index,
                   main=(index == 44))
        for index in range(45)
    ]
    monkeypatch.setattr(bash_opt_module, "list_process_windows",
                        lambda pid: infos)
    monkeypatch.setattr(BashOptFunc, "_WAIT_WINDOW_INTERVAL", 0.01)
    monkeypatch.setattr(BashOptFunc, "_MAX_WINDOW_LIST", 3)
    func = BashOptFunc(task_id="bg-1", op="wait_window", window="title:w44")
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["found"] is True
    assert payload["window"] is not None
    assert payload["window"]["handle"] == 0x1000 + 44
    assert payload["truncated"] is True
    assert payload["returned"] == 3


async def test_elements_marks_truncation(monkeypatch):
    elements = [
        ElementInfo(handle=0x20 + index, pid=99, class_name="Button",
                    text=f"b{index}", left=0, top=0, width=10, height=10)
        for index in range(3)
    ]
    monkeypatch.setattr(bash_opt_module, "list_process_elements",
                        lambda pid, window=None: elements)

    async def _frame(self, pid, window):
        return WindowFrame(0, 0, 100, 100)

    monkeypatch.setattr(BashOptFunc, "_window_frame_for", _frame)
    func = BashOptFunc(task_id="bg-1", op="elements", max_elements=2)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    payload = json.loads(await func.execute())
    assert payload["matched"] == 3
    assert payload["returned"] == 2
    assert payload["truncated"] is True


# ═══════════════════════════════════════════════════════════
# P5：read 提示 / release 不接管任务
# ═══════════════════════════════════════════════════════════

async def test_read_hint_does_not_claim_full_result(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_READ_OUTPUT_MAX_CHARS", 5)
    rec = {"read_buffer": "abcdefghij", "status": "running", "done": False}
    payload = json.loads(await BashOptFunc(task_id="bg-1", op="read")
                         ._op_read(rec))
    assert "1000 行" in payload["hint"]
    assert "完整结果" not in payload["hint"]


def test_release_does_not_take_over_task():
    assert "release" not in BashOptFunc._MANAGED_OPS
    assert "release" in INPUT_OPS


# ═══════════════════════════════════════════════════════════
# P6：locate 模板路径安全校验
# ═══════════════════════════════════════════════════════════

async def test_locate_template_uses_shared_path_validation(monkeypatch):
    called: dict = {}

    def _prepare(self, path):
        called["path"] = path
        raise ValueError(f"输入图片不存在: {path}")

    monkeypatch.setattr(BashOptFunc, "_prepare_image_path", _prepare)
    func = BashOptFunc(task_id="bg-1", op="locate", template="x.png")
    with pytest.raises(ValueError, match="输入图片不存在"):
        await func._locate_template("x.png", "shot.png", None, 25, 10,
                                    1.0, 1.0, 1)
    assert called["path"] == "x.png"
