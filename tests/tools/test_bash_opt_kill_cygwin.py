"""``bash_opt op=kill`` 在 Cygwin 下的参数透传 / 残留处理测试。"""

from __future__ import annotations

from src.tools import bash_opt as bash_opt_module
from src.tools._bash_support import KillResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, rec: dict):
        self._background_tasks = {"bg-1": rec}
        self.removed: list[str] = []

    def _remove_background_task(self, task_id: str):
        self.removed.append(task_id)
        return self._background_tasks.pop(task_id, None)

    def _publish_background_task_event(self):
        pass


def _rec() -> dict:
    return {
        "pid": 123,
        "process": None,
        "task": None,
        "winpid": 456,
        "pid_create_time": 1000,
        "job_handle": None,
        "descendant_pids": {124},
        "descendant_create_times": {124: 1001},
        "descendant_win_pids": {457},
        "descendant_win_create_times": {457: 1002},
        "win_hint": "notepad.exe",
        "win_hints": ["notepad.exe", "mintty"],
        "win_start_time": 999,
    }


async def test_kill_passes_windows_fields_to_kill_process_tree(monkeypatch):
    captured: dict = {}

    def fake_kill(pid, **kwargs):
        captured.update(kwargs)
        return KillResult(pid=pid, attempts=1, verified=True)

    monkeypatch.setattr(bash_opt_module, "kill_process_tree", fake_kill)
    rec = _rec()
    agent = _FakeAgent(rec)
    func = BashOptFunc(task_id="bg-1", op="kill")
    text = await func._op_kill(agent, rec)
    assert "无残留进程" in text
    assert captured["winpid"] == 456
    assert captured["extra_win_pids"] == {457}
    assert captured["win_hint"] == "notepad.exe"
    assert captured["win_hints"] == ["notepad.exe", "mintty"]
    assert captured["win_start_time"] == 999
    assert agent.removed == ["bg-1"]


async def test_kill_keeps_task_when_windows_residual(monkeypatch):
    def fake_kill(pid, **kwargs):
        return KillResult(
            pid=pid, attempts=1, verified=True, remaining_win_pids=(7777,))

    monkeypatch.setattr(bash_opt_module, "kill_process_tree", fake_kill)
    rec = _rec()
    agent = _FakeAgent(rec)
    func = BashOptFunc(task_id="bg-1", op="kill")
    text = await func._op_kill(agent, rec)
    assert "Windows 进程仍存活" in text
    assert "7777" in text
    # 任务记录保留，便于再次 op=kill 补杀
    assert agent.removed == []
    assert "bg-1" in agent._background_tasks
    assert rec["status"] == "kill_failed"
    assert rec["managed_by_tool"] is True
