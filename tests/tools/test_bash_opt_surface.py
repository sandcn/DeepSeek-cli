"""bash_opt 的 GUI 能力边界（只保留光标操作、截图、键盘输入）。

覆盖：
- schema 的 op 枚举只含保留项，已清理的 GUI op 不得出现；
- 运行时对已清理 op 返回可读的「未知操作」而不是静默执行；
- 缺少任务时 read 给出明确提示（不触碰任务表之外的行为）。
"""

from __future__ import annotations

import json

import pytest

from src.tools._bash_opt_schema import build_bash_opt_schema
from src.tools._screenshot import CaptureResult, ScreenshotError
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 替身：仅提供 bash_opt 依赖的后台任务表。"""

    def __init__(self) -> None:
        self._background_tasks: dict = {}


def _schema_props() -> dict:
    return build_bash_opt_schema()["function"]["parameters"]["properties"]


def test_schema_only_exposes_retained_ops() -> None:
    enum = set(_schema_props()["op"]["enum"])
    assert enum == {
        "read", "wait", "kill", "stdin", "close_stdin", "keys", "screenshot",
        "click", "move", "hover", "drag", "scroll", "key", "type", "release",
    }


def test_schema_parameter_surface_is_trimmed() -> None:
    """参数只保留终端操作与光标 / 截图 / 键盘所必需的最小集。"""
    assert set(_schema_props()) == {
        # 终端 / 后台任务
        "task_id", "op", "timeout", "text", "newline", "encoding",
        "max_lines", "key",
        # 截图与目标窗口
        "path", "crop", "screen", "window",
        # 光标操作
        "x", "y", "button", "count", "direction", "amount",
        "from_x", "from_y", "to_x", "to_y",
    }


def test_removed_gui_params_not_exposed() -> None:
    props = _schema_props()
    for name in (
        "element", "window_action", "clipboard_action", "template", "query",
        "ocr_lang", "macro", "actions", "wait_for", "diff", "color", "boxes",
        # 调参项（长按 / 连按 / 平滑 / 相对位移 / 投递方式 / 按键阶段等）
        "hold", "interval", "dwell", "duration", "steps", "method",
        "phase", "repeat", "relative_event", "hold_keys", "modifiers",
        "grid", "shot", "settle", "dx", "dy", "keys", "buttons",
    ):
        assert name not in props


def test_removed_params_are_ignored_by_from_args() -> None:
    """模型误传已删除的参数时只记录日志，不阻断执行（参数面已收敛）。"""
    func = BashOptFunc.from_args({
        "op": "click", "task_id": "bg-1", "x": 1, "y": 2,
        "hold": 3, "method": "message", "shot": True, "settle": 1,
    })
    assert isinstance(func, BashOptFunc)
    for name in ("hold", "method", "shot", "settle"):
        assert not hasattr(func, name)
    action = func._build_input_action()
    assert action.name == "click"
    assert action.x == 1 and action.y == 2


@pytest.mark.asyncio
async def test_removed_op_returns_unknown_with_supported_list() -> None:
    func = BashOptFunc(op="windows")
    func.set_agent(_FakeAgent())
    text = await func.execute()
    assert "未知操作: windows" in text
    assert "screenshot" in text
    assert "click" in text


@pytest.mark.asyncio
async def test_read_without_task_explains_requirement() -> None:
    func = BashOptFunc(op="read", task_id="bg-missing")
    func.set_agent(_FakeAgent())
    text = await func.execute()
    assert "后台任务不存在" in text
    assert "background=True" in text


# ── GUI 输入 op 的 path：操作完成后自动截图 ─────────────────


class _RunningTaskAgent:
    """带一个运行中任务的 Agent 替身（记录里有可截图 / 注入的 pid）。"""

    def __init__(self, pid: int = 4242) -> None:
        self._background_tasks = {"bg-1": {"pid": pid}}


def _input_result(action: str = "click") -> InputResult:
    return InputResult(action=action, backend="fake", window_pid=4242,
                       window_title="demo", detail={"x": 1, "y": 2})


def _capture_result(path: str) -> CaptureResult:
    return CaptureResult(path=path, width=800, height=600,
                         window_pid=4242, window_title="demo",
                         backend="fake", window_x=10, window_y=20)


def _patch_input(monkeypatch, action: str = "click") -> None:
    async def fake_send(self, pid, act):
        return _input_result(action)

    monkeypatch.setattr(BashOptFunc, "_send_input_with_retry", fake_send)


@pytest.mark.asyncio
async def test_input_op_with_path_screenshots_after_input(
        monkeypatch, tmp_path) -> None:
    """click 传 path：注入成功后截同一目标窗口，结果并入 screenshot 字段。"""
    _patch_input(monkeypatch)
    seen: dict = {}

    async def fake_capture(self, rec, *, target_path, crop, window, screen):
        seen.update(target_path=target_path, crop=crop,
                    window=window, screen=screen)
        return {"result": _capture_result(target_path), "region": None}

    monkeypatch.setattr(BashOptFunc, "_screenshot_target", fake_capture)

    func = BashOptFunc(op="click", task_id="bg-1", x="center", y="50%",
                       path=str(tmp_path / "after"))
    func.set_agent(_RunningTaskAgent())
    payload = json.loads(await func.execute())

    shot = payload["screenshot"]
    assert shot["path"].endswith("after.png")
    assert seen["target_path"] == shot["path"]
    assert seen["window"] is None          # 输入后截图固定走目标窗口
    assert seen["screen"] is None          # 不接受整屏形态
    # 输入结果本身不受影响
    assert payload["action"] == "click"
    assert payload["window_pid"] == 4242
    assert "自动截图" in payload["hint"]


@pytest.mark.asyncio
async def test_input_op_without_path_does_not_screenshot(monkeypatch) -> None:
    """不传 path 时只注入输入，不产生任何截图副作用。"""
    _patch_input(monkeypatch, action="type")

    async def fail_capture(*args, **kwargs):
        raise AssertionError("未传 path 不应截图")

    monkeypatch.setattr(BashOptFunc, "_screenshot_target", fail_capture)
    func = BashOptFunc(op="type", task_id="bg-1", text="hello")
    func.set_agent(_RunningTaskAgent())
    payload = json.loads(await func.execute())

    assert "screenshot" not in payload
    assert "screenshot_error" not in payload
    assert payload["action"] == "type"


@pytest.mark.asyncio
async def test_input_op_screenshot_failure_keeps_input_result(
        monkeypatch, tmp_path) -> None:
    """截图失败不回滚已完成的输入：结果保留动作细节并给出 screenshot_error。"""
    _patch_input(monkeypatch)

    async def fail_capture(*args, **kwargs):
        raise ScreenshotError("窗口当前不可见")

    monkeypatch.setattr(BashOptFunc, "_screenshot_target", fail_capture)
    func = BashOptFunc(op="key", task_id="bg-1", key="ctrl+s",
                       path=str(tmp_path / "after.png"))
    func.set_agent(_RunningTaskAgent())
    payload = json.loads(await func.execute())

    assert "screenshot" not in payload
    assert "窗口当前不可见" in payload["screenshot_error"]
    assert payload["window_pid"] == 4242


@pytest.mark.asyncio
async def test_input_op_screenshot_without_window_reports_error(
        monkeypatch, tmp_path) -> None:
    """release 无任务（rec=None）时带 path：给出可读提示而不是异常。"""
    _patch_input(monkeypatch, action="release")

    async def fail_capture(*args, **kwargs):
        raise AssertionError("没有窗口时不应尝试截图")

    monkeypatch.setattr(BashOptFunc, "_screenshot_target", fail_capture)
    func = BashOptFunc(op="release", path=str(tmp_path / "after.png"))
    func.set_agent(_RunningTaskAgent())
    payload = json.loads(await func.execute())

    assert "screenshot" not in payload
    assert "没有可截图的窗口" in payload["screenshot_error"]
