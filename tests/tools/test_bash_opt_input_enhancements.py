"""bash_opt 输入动作增强能力测试（wait_for / diff / 语义坐标贯通）。

覆盖：``wait_for='change'``（满足 / 超时）、``wait_for='stable'``、
``diff=true``（变化 / 未变化）、基准图缺失时的降级说明，以及 ``settle``
与 ``wait_for`` 数值形式的关系；坐标语义值贯通到注入动作。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._screenshot import png
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


def _patch_send(monkeypatch, recorder: list):
    def _impl(pid, action):
        recorder.append(action)
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="App", detail={"delivery": "sendinput"})
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


def _fake_temps(tmp_path, colors):
    """把 ``_temp_screenshot`` 替换为按序列产出的真实 PNG（BGRA 颜色）。"""
    state = {"n": 0}

    async def _impl(self, pid, window):
        index = min(state["n"], len(colors) - 1)
        state["n"] += 1
        path = tmp_path / f"shot-{state['n']}.png"
        path.write_bytes(png.encode_png_bgra(2, 2, bytes(colors[index]) * 4))
        return str(path)

    return _impl


def _func(**kwargs):
    func = BashOptFunc(task_id="bg-1", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": _record()}))
    return func


def _fast_wait(monkeypatch, interval=0.01):
    monkeypatch.setattr(BashOptFunc, "_WAIT_FOR_INTERVAL", interval)


RED = (0, 0, 255, 255)
BLUE = (255, 0, 0, 255)


async def test_wait_for_change_satisfied(monkeypatch, tmp_path):
    _fast_wait(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED, BLUE]))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="click", x=1, y=1, wait_for="change").execute())
    wait = payload["wait_for"]
    assert wait["mode"] == "change" and wait["satisfied"] is True
    assert wait["change"]["changed"] is True


async def test_wait_for_change_times_out(monkeypatch, tmp_path):
    _fast_wait(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED]))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="click", x=1, y=1, wait_for="change", wait_timeout=0.05).execute())
    wait = payload["wait_for"]
    assert wait["satisfied"] is False
    assert "没有变化" in wait["reason"]


async def test_wait_for_stable_satisfied(monkeypatch, tmp_path):
    _fast_wait(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED]))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="key", key="enter", wait_for="stable").execute())
    assert payload["wait_for"]["satisfied"] is True
    assert payload["wait_for"]["mode"] == "stable"


async def test_wait_for_numeric_value_acts_as_settle(monkeypatch):
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="click", x=1, y=1, wait_for="0.01").execute())
    assert "wait_for" not in payload


async def test_diff_reports_change_and_region(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED, BLUE]))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(op="click", x=1, y=1, diff=True).execute())
    assert payload["diff"]["changed"] is True
    assert payload["diff"]["region"] == {"x": 0, "y": 0, "width": 2, "height": 2}
    assert "已变化" in payload["diff"]["summary"]


async def test_diff_reports_no_change(monkeypatch, tmp_path):
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot",
                        _fake_temps(tmp_path, [RED]))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(op="click", x=1, y=1, diff=True).execute())
    assert payload["diff"]["changed"] is False
    assert "未发生变化" in payload["diff"]["summary"]


async def test_diff_reports_missing_baseline(monkeypatch):
    async def _no_shot(self, pid, window):
        return None
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _no_shot)
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(op="click", x=1, y=1, diff=True).execute())
    assert payload["diff"]["changed"] is None
    assert "基准图" in payload["diff"]["reason"]


async def test_input_rejects_bad_diff_and_tolerance(monkeypatch):
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    result = await _func(op="click", x=1, y=1, diff="maybe").execute()
    assert "diff 需要布尔值" in result
    result = await _func(op="click", x=1, y=1, tolerance=999).execute()
    assert "tolerance" in result


async def test_semantic_coordinates_flow_into_action(monkeypatch):
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    await _func(op="click", x="center", y="50%").execute()
    assert recorder[0].x == "center" and recorder[0].y == "50%"
    recorder.clear()
    await _func(op="drag", to_x="right", to_y="bottom").execute()
    assert recorder[0].to_x == "right" and recorder[0].to_y == "bottom"


# ── wait_for='stable' 容忍光标闪烁级别的微小噪声 ─────────

def _noisy_temps(tmp_path, size=40):
    """第 1 帧纯色、之后每帧只改 1 个像素（模拟输入光标闪烁）。"""
    base = bytearray(size * size * 4)
    noisy = bytearray(base)
    noisy[0:4] = bytes((0, 0, 255, 255))
    frames = [bytes(base), bytes(noisy)]
    state = {"n": 0}

    async def _impl(self, pid, window):
        index = min(state["n"], len(frames) - 1)
        state["n"] += 1
        path = tmp_path / f"noise-{state['n']}.png"
        path.write_bytes(png.encode_png_bgra(size, size, frames[index]))
        return str(path)

    return _impl


def _busy_temps(tmp_path, size=40):
    """每帧大面积变化（模拟动画），用于验证不会被误判为稳定。"""
    state = {"n": 0}

    async def _impl(self, pid, window):
        state["n"] += 1
        data = bytearray(size * size * 4)
        if state["n"] % 2:
            for offset in range(0, len(data), 8):
                data[offset:offset + 4] = bytes((0, 0, 255, 255))
        path = tmp_path / f"busy-{state['n']}.png"
        path.write_bytes(png.encode_png_bgra(size, size, bytes(data)))
        return str(path)

    return _impl


async def test_wait_for_stable_ignores_tiny_noise(monkeypatch, tmp_path):
    """光标闪烁只造成个别像素变化，不应让「等界面稳定」永远失败。"""
    _fast_wait(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _noisy_temps(tmp_path))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="key", key="enter", wait_for="stable").execute())
    wait = payload["wait_for"]
    assert wait["satisfied"] is True
    assert "微小变化" in wait["ignored_change"]


async def test_wait_for_stable_still_waits_on_real_changes(monkeypatch, tmp_path):
    """大面积变化（动画）不能被微小噪声容忍规则误判为稳定。"""
    _fast_wait(monkeypatch)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", _busy_temps(tmp_path))
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="key", key="enter", wait_for="stable",
        wait_timeout=0.05).execute())
    wait = payload["wait_for"]
    assert wait["satisfied"] is False
    assert "没有稳定" in wait["reason"]
