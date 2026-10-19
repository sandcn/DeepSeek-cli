"""Windows 原生进程树收集 / 杀死测试（bash_opt op=kill 需求）。

修复背景（用户报障「bash_opt kill 不能在当前环境下杀死整个进程树」）：
原生 Windows 没有 ``/proc``，也没有 ``ps``——修复前
``_bash_support._children_map`` 只能退回 ``ps``（通常不存在，返回空表），
``_collect_tree_pids`` 因此**永远只返回根 PID**，op=kill 完全依赖
``taskkill /F /T`` 的隐式树遍历。一旦根进程先退出（后台 shell 结束、其
启动的子进程成为常驻孤儿），``taskkill /PID 根`` 找不到进程，后代全部
残留。

本模块覆盖修复后的完整链路：
  - Toolhelp32 进程表收集（含根已退出后的孤儿后代）；
  - 逐进程 ``TerminateProcess`` + 失败回退 ``taskkill /F /T``；
  - 杀后校验（含 Windows 权限不足不谎报「无残留」）；
  - pid 复用防护（创建时间不匹配时拒绝杀，避免误杀无关进程树）；
  - bash_opt / ESC 路径把记录的创建时间透传给 kill_process_tree。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

import src.tools._bash_support as support
from src.tools import _win_proc
from src.tools._bash_support import KillResult, kill_process_tree
from src.tools.bash import BashFunc
from src.tools.bash_opt import BashOptFunc

_IS_WINDOWS = os.name == "nt"
_WINDOWS_ONLY = pytest.mark.skipif(not _IS_WINDOWS, reason="需要原生 Windows")


class _FakeAgent:
    """最小 Agent 桩：仅提供 bash 后台任务表与注册 / 完成 / 移除方法。"""

    def __init__(self, tasks: dict | None = None):
        self._background_tasks = tasks if tasks is not None else {}
        self._subagent_tasks = {}
        self.removed: list[str] = []

    def _publish_background_task_event(self) -> None:
        pass

    def _register_background_task(self, task_id: str, rec: dict) -> None:
        self._background_tasks[task_id] = rec

    def _complete_background_task(self, task_id: str, result: str) -> None:
        rec = self._background_tasks.get(task_id)
        if rec is None:
            return
        from src.core.base_agent import _parse_bash_result_fields
        rec["result"] = result
        rec["stdout"], rec["stderr"], rec["returncode"] = (
            _parse_bash_result_fields(result))
        rec["status"] = "completed"
        rec["done"] = True

    def _remove_background_task(self, task_id: str) -> None:
        self._background_tasks.pop(task_id, None)
        self.removed.append(task_id)


def _spawn_sleeper() -> subprocess.Popen:
    """启动一个长睡 Python 子进程（测试用真实进程树节点）。"""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


# ── 1. 进程表收集（Windows Toolhelp32） ────────────────

def test_children_map_windows_uses_toolhelp32(monkeypatch):
    """Windows 原生：用 Toolhelp32 进程表构建父子映射（不再退回不存在的 ps）。"""
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_prefer_ps_process_tree", lambda: False)
    monkeypatch.setattr(
        _win_proc, "list_processes",
        lambda: [(10, 1, "a.exe"), (11, 10, "b.exe"), (12, 12, "self.exe")],
    )

    mapping = support._children_map()

    assert mapping[1] == [10]
    assert mapping[10] == [11]
    # 防御：PPID 指向自身的异常记录不能入表（DFS 会成环）
    assert 12 not in mapping


def test_children_map_windows_does_not_fall_back_to_cygwin_ps(monkeypatch):
    """Windows 原生不回退 ps：PATH 里的 ps 多为 Cygwin 的，PID 空间不同。"""
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_prefer_ps_process_tree", lambda: False)
    monkeypatch.setattr(_win_proc, "list_processes", lambda: [])
    monkeypatch.setattr(
        support, "_children_map_from_ps",
        lambda: pytest.fail("Windows 原生不应回退 ps（POSIX/Windows PID 空间不同）"),
    )

    assert support._children_map() == {}


@_WINDOWS_ONLY
def test_windows_children_map_contains_current_process():
    """真实 Windows 进程表：当前进程出现在某个父进程的子列表里。"""
    mapping = support._children_map()
    assert mapping, "Toolhelp32 进程表不应为空"
    assert any(os.getpid() in children for children in mapping.values())


@_WINDOWS_ONLY
def test_collect_tree_pids_finds_grandchild():
    """真实进程树：Toolhelp32 能收集到根、子、孙三层。"""
    script = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(p.pid, flush=True)\n"
        "p.wait()\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, text=True)
    grandchild = 0
    try:
        grandchild = int(proc.stdout.readline().strip())
        tree: list[int] = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            tree = support._collect_tree_pids(proc.pid)
            if grandchild in tree:
                break
            time.sleep(0.05)
        assert proc.pid in tree
        assert grandchild in tree, f"未收集到孙进程 {grandchild}: {tree}"
    finally:
        support.kill_process_tree(proc.pid)
        proc.wait(timeout=10)
        if grandchild:
            _win_proc.terminate_process(grandchild)


# ── 2. 根进程已退出：孤儿后代仍能被收集并杀掉（用户报障核心） ──

@_WINDOWS_ONLY
def test_kill_process_tree_kills_orphans_after_root_exit():
    """根进程先退出、子进程成为孤儿：kill 仍能定位并杀死后代。

    这正是修复前失效的场景：``taskkill /PID 根`` 找不到已退出的根，
    而旧的 ``_collect_tree_pids`` 在 Windows 上只有根 PID。
    """
    # 根进程启动子进程后立即退出（不 wait）——子进程成为孤儿，
    # 其 Toolhelp32 PPID 仍指向已退出的根 PID。
    script = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(p.pid, flush=True)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, text=True)
    root_pid = proc.pid
    orphan = int(proc.stdout.readline().strip())
    try:
        proc.wait(timeout=10)
        assert proc.poll() is not None
        assert _win_proc.is_process_alive(orphan) is True

        # 修复后的收集通道：根已退出仍能按 PPID 找到孤儿
        tree = support._collect_tree_pids(root_pid)
        assert orphan in tree, f"未收集到孤儿后代 {orphan}: {tree}"

        result = kill_process_tree(root_pid)

        assert result.verified is True
        assert result.pid_reused is False
        assert result.success is True, result
        assert orphan in result.killed_pids
        assert _win_proc.is_process_alive(orphan) is False
    finally:
        if _win_proc.is_process_alive(orphan):
            _win_proc.terminate_process(orphan)


# ── 3. 单进程终止：TerminateProcess 优先，失败回退 taskkill ──

def test_kill_pid_skips_already_dead_process(monkeypatch):
    """已退出的 PID 直接跳过（不为每个已死 PID 拉起一次 taskkill 进程）。"""
    calls: list = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(_win_proc, "terminate_process",
                        lambda pid: calls.append(("terminate", pid)) or True)
    monkeypatch.setattr(support, "_run_quiet",
                        lambda argv: calls.append(("taskkill", argv)) or "")

    support._kill_pid(1234)

    assert calls == []


def test_kill_pid_windows_prefers_terminate_process(monkeypatch):
    """Windows：TerminateProcess 成功即返回，不再拉起 taskkill。"""
    calls: list = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(_win_proc, "terminate_process",
                        lambda pid: calls.append(("terminate", pid)) or True)
    monkeypatch.setattr(support, "_run_quiet",
                        lambda argv: calls.append(("taskkill", argv)) or "")

    support._kill_pid(1234)

    assert calls == [("terminate", 1234)]


def test_kill_pid_windows_falls_back_to_taskkill(monkeypatch):
    """Windows：TerminateProcess 失败（权限 / 受保护进程）回退 taskkill /T。"""
    calls: list = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(_win_proc, "terminate_process",
                        lambda pid: calls.append(("terminate", pid)) or False)
    monkeypatch.setattr(support, "_run_quiet",
                        lambda argv: calls.append(("taskkill", argv)) or "")

    support._kill_pid(1234)

    assert calls == [
        ("terminate", 1234),
        ("taskkill", ["taskkill", "/F", "/T", "/PID", "1234"]),
    ]


def test_kill_tree_once_windows_falls_back_to_taskkill_without_descendants(monkeypatch):
    """进程表不可用（快照为空）时，Windows 退回 taskkill /T 隐式遍历。"""
    calls: list = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_collect_tree_pids", lambda root: [root])
    monkeypatch.setattr(support, "_children_map", lambda: {})
    monkeypatch.setattr(support, "_kill_process_group", lambda pid: calls.append("killpg"))
    monkeypatch.setattr(support, "_run_quiet", lambda argv: calls.append(argv) or "")

    support._kill_tree_once(4321)

    assert ["taskkill", "/F", "/T", "/PID", "4321"] in calls


def test_kill_tree_once_windows_single_process_uses_terminate(monkeypatch):
    """快照可用且根确实无子进程：走快速 TerminateProcess，不拉 taskkill。"""
    killed: list[int] = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_collect_tree_pids", lambda root: [root])
    monkeypatch.setattr(support, "_children_map", lambda: {1: [2]})
    monkeypatch.setattr(support, "_kill_process_group", lambda pid: None)
    monkeypatch.setattr(support, "_kill_pid", lambda pid: killed.append(pid))
    monkeypatch.setattr(support, "_run_quiet",
                        lambda argv: pytest.fail("进程表可用时不应 taskkill"))

    support._kill_tree_once(7)

    assert killed == [7]


def test_kill_tree_once_kills_descendants_before_root(monkeypatch):
    """收集到后代时逐个杀（叶方向优先），根最后杀，不再额外 taskkill /T。"""
    killed: list[int] = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_collect_tree_pids", lambda root: [root, 2, 3])
    monkeypatch.setattr(support, "_kill_process_group", lambda pid: None)
    monkeypatch.setattr(support, "_kill_pid", lambda pid: killed.append(pid))
    monkeypatch.setattr(support, "_run_quiet",
                        lambda argv: pytest.fail("收集到后代时不应 taskkill"))

    tree = support._kill_tree_once(1)

    assert tree == [1, 2, 3]
    assert killed == [3, 2, 1]


# ── 4. 存活判定 / pid 复用防护 ─────────────────────────

def test_win_pid_alive_delegates_to_win_proc(monkeypatch):
    """Windows 存活判定走 _win_proc（权限不足判存活，见其单测）。"""
    monkeypatch.setattr(_win_proc, "is_process_alive", lambda pid: pid == 42)
    assert support._win_pid_alive(42) is True
    assert support._win_pid_alive(43) is False


def test_win_pid_alive_failure_is_conservative(monkeypatch):
    """判定过程异常时保守视为存活（不谎报「已全部退出」）。"""
    def _boom(pid):
        raise OSError("kernel32 不可用")

    monkeypatch.setattr(_win_proc, "is_process_alive", _boom)
    assert support._win_pid_alive(42) is True


def test_process_create_time_none_outside_windows(monkeypatch):
    """非 Windows 没有统一的创建时间读取路径 → None（不启用复用防护）。"""
    monkeypatch.setattr(support, "_is_windows", lambda: False)
    assert support.process_create_time(1234) is None


def test_kill_process_tree_rejects_reused_pid(monkeypatch):
    """pid 已被系统复用（创建时间不匹配）→ 一轮都不杀，结果标记 pid_reused。"""
    killed: list[int] = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_pid_reused", lambda pid, ct: True)
    monkeypatch.setattr(support, "_kill_tree_once",
                        lambda root, collected=None, extra_pids=None:
                        killed.append(root) or [root])

    result = kill_process_tree(1234, create_time=999)

    assert killed == []
    assert result.pid_reused is True
    assert result.attempts == 0
    assert result.success is False


def test_kill_process_tree_passes_create_time_to_reuse_guard(monkeypatch):
    """创建时间透传到复用判定；未记录创建时间时不启用防护。"""
    seen: list = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(
        support, "_pid_reused",
        lambda pid, ct: seen.append((pid, ct)) or False,
    )
    monkeypatch.setattr(support, "_kill_tree_once",
                        lambda root, collected=None, extra_pids=None: [root])

    kill_process_tree(77, create_time=555, verify=False)
    kill_process_tree(78, verify=False)

    assert seen == [(77, 555), (78, None)]


def test_kill_result_pid_reused_is_not_success():
    """KillResult：pid_reused 时 success 恒为 False（即使已校验无残留）。"""
    result = KillResult(pid=1, attempts=0, verified=True, pid_reused=True)
    assert result.success is False


# ── 5. _win_proc 原生能力（真实系统调用） ──────────────

@_WINDOWS_ONLY
def test_win_proc_list_and_alive():
    """真实进程表包含当前进程；存活判定区分自身 / 不存在。"""
    processes = _win_proc.list_processes()
    assert any(pid == os.getpid() for pid, _ppid, _name in processes)
    assert _win_proc.is_process_alive(os.getpid()) is True
    assert _win_proc.is_process_alive(0) is False
    assert _win_proc.is_process_alive(99999999) is False


@_WINDOWS_ONLY
def test_win_proc_create_time_and_reuse_guard():
    """创建时间稳定可读，且作为 pid 复用判定依据。"""
    created = _win_proc.process_create_time(os.getpid())
    assert created is not None and created > 0
    assert _win_proc.process_create_time(os.getpid()) == created
    assert _win_proc.is_pid_reused(os.getpid(), created) is False
    assert _win_proc.is_pid_reused(os.getpid(), created + 1) is True
    assert _win_proc.is_pid_reused(os.getpid(), None) is None
    assert _win_proc.process_create_time(99999999) is None


@_WINDOWS_ONLY
def test_win_proc_terminate_process():
    """TerminateProcess 能终止真实子进程。"""
    proc = _spawn_sleeper()
    try:
        time.sleep(0.2)
        assert _win_proc.terminate_process(proc.pid) is True
        proc.wait(timeout=10)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


@_WINDOWS_ONLY
def test_win_proc_is_supported_on_windows():
    """原生 Windows 上 kernel32 进程 API 应可用。"""
    assert _win_proc.is_supported() is True


# ── 6. bash_opt / ESC 路径透传创建时间 ────────────────

async def test_bash_opt_kill_passes_create_time(monkeypatch):
    """op=kill 把任务记录里的根进程创建时间传给 kill_process_tree。"""
    captured: dict = {}

    def fake_kill(pid, **kwargs):
        captured["pid"] = pid
        captured["create_time"] = kwargs.get("create_time")
        return KillResult(pid=pid, attempts=1, killed_pids=(pid,), verified=True)

    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree", fake_kill)
    agent = _FakeAgent({"bg-w": {"pid": 1234, "process": None, "task": None,
                                 "pid_create_time": 987654321}})

    opt = BashOptFunc(task_id="bg-w", op="kill")
    opt.set_agent(agent)
    out = await opt.execute()

    assert captured == {"pid": 1234, "create_time": 987654321}
    assert "无残留进程" in out


async def test_bash_opt_kill_reports_pid_reused(monkeypatch):
    """op=kill 遇到 pid 复用：明确报告「未杀」，不谎称已杀死。"""
    monkeypatch.setattr(
        "src.tools.bash_opt.kill_process_tree",
        lambda pid, **kwargs: KillResult(pid=pid, attempts=0, verified=True,
                                         pid_reused=True),
    )
    agent = _FakeAgent({"bg-r": {"pid": 4321, "process": None, "task": None,
                                 "pid_create_time": 111}})

    opt = BashOptFunc(task_id="bg-r", op="kill")
    opt.set_agent(agent)
    out = await opt.execute()

    assert "未杀死" in out
    assert "复用" in out
    assert "4321" in out
    assert "bg-r" not in agent._background_tasks


async def test_cancel_tasks_passes_create_time_to_kill(monkeypatch):
    """ESC 中断路径：记录的创建时间 / Job / 采样后代随 pid 一起透传。"""
    from src.core.base_agent import _cancel_tasks_and_kill_pids

    captured: list = []

    def fake_kill(pid, **kwargs):
        captured.append((pid, kwargs.get("create_time"),
                         kwargs.get("job_handle"), kwargs.get("extra_pids")))
        return KillResult(pid=pid, attempts=1, verified=True)

    monkeypatch.setattr("src.tools.bash.kill_process_tree", fake_kill)

    await _cancel_tasks_and_kill_pids(
        [], [(111, 999, 7, {111, 112}, {111: 999, 112: 1000}), 222],
    )

    assert captured == [
        (111, 999, 7, {111, 112}),
        (222, None, None, None),
    ]


# ── 7. 进程树跟踪器：跨越中间层退出的断链 ────────────────

def test_process_tree_tracker_bridges_chain_after_middle_exit(monkeypatch):
    """中间层退出后，孙进程仍按历史 PPID 关系识别为后代。

    这正是修复前 Toolhelp32 单次快照失效的场景：第二次采样里 101（中
    间层）已退出，但 102 的 PPID 仍指向 101——跟踪器保留历史关系即可关联。
    """
    from src.tools._bash_support import ProcessTreeTracker

    tables = [
        [(100, 1, "root.exe"), (101, 100, "mid.exe")],
        [(102, 101, "grand.exe"), (1, 0, "init.exe")],
    ]
    monkeypatch.setattr(_win_proc, "list_processes", lambda: tables.pop(0))
    monkeypatch.setattr(_win_proc, "process_create_time", lambda pid: pid * 10)

    tracker = ProcessTreeTracker(100)
    first = tracker.observe()
    second = tracker.observe()

    assert first == {100, 101}
    assert second == {100, 101, 102}
    assert tracker.observed.get(102) == 1020


def test_process_tree_tracker_ignores_unrelated_processes(monkeypatch):
    """与根无父子关系的进程不会进入后代 / 杀目标集合。"""
    from src.tools._bash_support import ProcessTreeTracker

    monkeypatch.setattr(
        _win_proc, "list_processes",
        lambda: [(200, 1, "other.exe"), (201, 200, "child.exe")],
    )
    monkeypatch.setattr(_win_proc, "process_create_time", lambda pid: 1)

    tracker = ProcessTreeTracker(100)
    tracker.observe()

    assert tracker.descendants == {100}
    assert 200 not in tracker.observed and 201 not in tracker.observed


def test_process_tree_tracker_requires_root(monkeypatch):
    """根 PID 未知时不做任何采样（返回空集）。"""
    from src.tools._bash_support import ProcessTreeTracker

    monkeypatch.setattr(_win_proc, "list_processes",
                        lambda: pytest.fail("未知根不应采样"))
    assert ProcessTreeTracker(0).observe() == set()


# ── 8. Job 对象：真实整树终止 ──────────────────────────

@_WINDOWS_ONLY
def test_win_proc_job_terminates_descendants():
    """Job 能一次终止根 + 其子进程（cmd → ping），active 计数归零。"""
    job = _win_proc.create_job()
    assert job
    proc = subprocess.Popen(
        ["cmd", "/c", "ping -n 60 127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert _win_proc.assign_process_to_job(job, proc.pid) is True
        active = 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            active = _win_proc.job_active_processes(job) or 0
            if active >= 2:
                break
            time.sleep(0.05)
        assert active >= 2, f"Job 未纳入子进程（active={active}）"
        assert _win_proc.terminate_job(job) is True
        proc.wait(timeout=10)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _win_proc.job_active_processes(job) == 0:
                break
            time.sleep(0.05)
        assert _win_proc.job_active_processes(job) == 0
    finally:
        _win_proc.close_handle(job)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


@_WINDOWS_ONLY
def test_kill_process_tree_with_job_handle_kills_job_members():
    """kill_process_tree 传入 Job 句柄：TerminateJobObject 覆盖整树。"""
    job = _win_proc.create_job()
    assert job
    proc = subprocess.Popen(
        ["cmd", "/c", "ping -n 60 127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert _win_proc.assign_process_to_job(job, proc.pid) is True
        time.sleep(0.5)
        result = kill_process_tree(proc.pid, job_handle=job)
        proc.wait(timeout=10)
        assert result.verified is True
        assert result.job_remaining == 0
        assert result.success is True, result
        assert _win_proc.job_active_processes(job) == 0
    finally:
        _win_proc.close_handle(job)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# ── 9. extra_pids：覆盖已脱离 PPID 链的孤儿 ─────────────

@_WINDOWS_ONLY
def test_kill_process_tree_kills_extra_pids_beyond_chain():
    """采样累积的孤儿 PID（不在当前树里）同样被杀并校验。"""
    root = _spawn_sleeper()
    orphan = _spawn_sleeper()
    try:
        time.sleep(0.2)
        created = _win_proc.process_create_time(orphan.pid)
        assert created
        result = kill_process_tree(
            root.pid,
            extra_pids=[orphan.pid],
            extra_create_times={orphan.pid: created},
        )
        root.wait(timeout=10)
        assert result.verified is True
        assert result.success is True, result
        assert orphan.pid in result.killed_pids
        assert _win_proc.is_process_alive(orphan.pid) is False
    finally:
        for proc in (root, orphan):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


def test_filter_reused_pids_skips_reused(monkeypatch):
    """extra_pids 中创建时间不匹配（PID 已复用）的候选被跳过。"""
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(
        support, "_pid_reused",
        lambda pid, ct: pid == 5 and ct == 55,
    )
    safe = support._filter_reused_pids([5, 6], {5: 55, 6: 66})
    assert safe == [6]


@_WINDOWS_ONLY
async def test_background_task_samples_descendants_and_job():
    """真实后台任务：采样记录后代 PID，并建立 Windows Job 句柄。"""
    import asyncio

    agent = _FakeAgent()
    f = BashFunc(command="cmd /c ping -n 20 127.0.0.1", background=True)
    f.set_agent(agent)
    payload = json.loads(await f.execute())
    assert "task_id" in payload, payload
    tid = payload["task_id"]
    try:
        deadline = time.monotonic() + 10.0
        rec = agent._background_tasks.get(tid) or {}
        while time.monotonic() < deadline:
            rec = agent._background_tasks.get(tid) or {}
            if rec.get("job_handle") and rec.get("descendant_pids"):
                break
            await asyncio.sleep(0.1)
        assert rec.get("job_handle"), "后台任务应建立 Windows Job"
        assert rec.get("descendant_pids"), "采样未记录任何后代"
        assert rec.get("pid") in rec.get("descendant_pids"), "根进程应被采样记录"
    finally:
        opt = BashOptFunc(task_id=tid, op="kill")
        opt.set_agent(agent)
        await opt.execute()


async def test_promote_to_background_records_job_and_sampling_fields(monkeypatch):
    """自动转后台：记录带创建时间 / Job / 采样容器（供 op=kill 杀孤儿）。"""
    import asyncio

    class _Proc:
        pid = 4321

    agent = _FakeAgent()
    f = BashFunc(command="sleep 300")
    f.set_agent(agent)
    monkeypatch.setattr("src.tools.bash.process_create_time", lambda pid: 777)
    exec_task = asyncio.ensure_future(asyncio.sleep(0.2))
    holder = {"process": _Proc(), "job_handle": 123, "mode": "pipe"}
    try:
        payload = json.loads(await f._promote_to_background(exec_task, holder, {}))
        tid = payload["task_id"]
        rec = agent._background_tasks[tid]
        assert rec["pid"] == 4321
        assert rec["pid_create_time"] == 777
        assert rec["job_handle"] == 123
        assert rec["descendant_pids"] == set()
        assert rec["descendant_create_times"] == {}
        assert holder["promoted"] is True
        # Job 归属记录：_run_exec 的 finally 不得关闭（此处只验证标记）
        assert "job_handle" in rec
    finally:
        exec_task.cancel()
        try:
            await exec_task
        except asyncio.CancelledError:
            pass
        # 让采样协程观察到 done 并自行退出（30ms 一轮）
        await asyncio.sleep(0.05)


