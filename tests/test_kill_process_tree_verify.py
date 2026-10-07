"""进程树杀死「杀后校验、未死补杀」测试（bash_opt op=kill 需求）。

需求：bash_opt 的 kill 要杀死**整个进程树**，杀完后要**检查是不是真正
杀了**，没有杀死**再杀一次**。

实现要点（src/tools/_bash_support.py）：
  - 后代收集：``/proc/<pid>/status`` 的 ``PPid`` 字段（Linux / Cygwin），
    无 ``/proc`` 平台（macOS / BSD）用 ``ps -eo pid=,ppid=`` 兜底；
  - 存活校验：``os.kill(pid, 0)`` + 状态字符判僵尸（僵尸视为已终止），
    Windows 原生用 ``OpenProcess`` / ``GetExitCodeProcess``；
  - 公开 API ``kill_process_tree(pid)`` 每轮「收集 → 杀死 → 校验」，
    仍有存活则补杀（最多 ``KILL_MAX_ATTEMPTS`` 轮），返回 ``KillResult``；
  - ``bash_opt._op_kill`` 在线程中调用该 API，并把校验结论 / 残留 PID
    反馈给模型。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest

import src.tools._bash_support as support
from src.tools._bash_support import KillResult, kill_process_tree
from src.tools.bash_opt import BashOptFunc

_POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="需要 POSIX shell（sh）")


class _FakeAgent:
    """最小 Agent 桩：仅提供 bash 后台任务表与移除方法。"""

    def __init__(self, tasks: dict | None = None):
        self._background_tasks = tasks if tasks is not None else {}
        self._subagent_tasks = {}
        self.removed: list[str] = []

    def _remove_background_task(self, task_id: str) -> None:
        self._background_tasks.pop(task_id, None)
        self.removed.append(task_id)


# ── 1. KillResult 语义 ────────────────────────────────

def test_kill_result_success_semantics():
    """success 仅在「已校验且无残留」时为真。"""
    assert KillResult(pid=1, attempts=1, verified=True).success is True
    assert KillResult(pid=1, attempts=1, verified=True,
                      remaining_pids=(2,)).success is False
    assert KillResult(pid=1, attempts=1, verified=False).success is False
    assert KillResult(pid=1).success is False


@pytest.mark.parametrize("bad", [0, -1, "abc", None])
def test_kill_process_tree_invalid_pid_returns_empty_result(bad):
    """非法 PID（<=0 / 非数字）：不抛异常，返回空结果。"""
    result = kill_process_tree(bad)
    assert result.pid == 0
    assert result.attempts == 0
    assert result.killed_pids == ()
    assert result.success is False


# ── 2. 杀后校验 + 未死补杀 ────────────────────────────

def test_kill_process_tree_retries_until_all_dead(monkeypatch):
    """第一轮杀完仍存活 → 自动再杀一次；第二轮全死后停止。"""
    rounds: list[int] = []

    def fake_kill_once(root_pid, collected=None):
        rounds.append(root_pid)
        if collected is not None:
            collected.update({root_pid, root_pid + 1})
        return [root_pid, root_pid + 1]

    def fake_alive(pid):
        # 第一轮结束后 root 仍存活；第二轮（已尝试 2 次）全部退出
        return pid == 4321 and len(rounds) < 2

    monkeypatch.setattr(support, "_kill_tree_once", fake_kill_once)
    monkeypatch.setattr(support, "_pid_alive", fake_alive)

    result = kill_process_tree(4321)
    assert rounds == [4321, 4321]
    assert result.attempts == 2
    assert result.killed_pids == (4321, 4322)
    assert result.remaining_pids == ()
    assert result.verified is True
    assert result.success is True


def test_kill_process_tree_reports_remaining_after_max_attempts(monkeypatch):
    """始终杀不死（如权限不足）：尝试到上限后报告残留 PID。"""
    calls: list[int] = []

    def fake_kill_once(root_pid, collected=None):
        calls.append(root_pid)
        if collected is not None:
            collected.add(root_pid)
        return [root_pid]

    monkeypatch.setattr(support, "_kill_tree_once", fake_kill_once)
    monkeypatch.setattr(support, "_wait_until_exit",
                        lambda pids, timeout=0: list(pids))

    result = kill_process_tree(5555)
    assert result.attempts == support.KILL_MAX_ATTEMPTS
    assert len(calls) == support.KILL_MAX_ATTEMPTS
    assert result.remaining_pids == (5555,)
    assert result.success is False


def test_kill_process_tree_attempts_param_limits_rounds(monkeypatch):
    """attempts=1 时只杀一轮（不补杀），仍报告残留。"""
    calls: list[int] = []

    def fake_kill_once(root_pid, collected=None):
        calls.append(root_pid)
        if collected is not None:
            collected.add(root_pid)
        return [root_pid]

    monkeypatch.setattr(support, "_kill_tree_once", fake_kill_once)
    monkeypatch.setattr(support, "_wait_until_exit",
                        lambda pids, timeout=0: list(pids))

    result = kill_process_tree(777, attempts=1)
    assert calls == [777]
    assert result.attempts == 1
    assert result.remaining_pids == (777,)


def test_kill_process_tree_verify_false_single_round(monkeypatch):
    """verify=False：仅杀一轮、不校验不等待（兼容快速杀语义）。"""
    calls: list[int] = []

    def fake_kill_once(root_pid, collected=None):
        calls.append(root_pid)
        if collected is not None:
            collected.add(root_pid)
        return [root_pid]

    monkeypatch.setattr(support, "_kill_tree_once", fake_kill_once)
    monkeypatch.setattr(support, "_wait_until_exit",
                        lambda pids, timeout=0: pytest.fail("verify=False 不应校验"))

    result = kill_process_tree(888, verify=False)
    assert calls == [888]
    assert result.verified is False
    assert result.remaining_pids == ()
    assert result.success is False


def test_kill_process_tree_recollects_new_descendants(monkeypatch):
    """每轮重新收集后代：第二轮发现的「新 fork 后代」也被杀死并记录。"""
    rounds: list[int] = []

    def fake_kill_once(root_pid, collected=None):
        rounds.append(root_pid)
        tree = [root_pid]
        if len(rounds) >= 2:
            tree.append(root_pid + 100)
        if collected is not None:
            collected.update(tree)
        return tree

    monkeypatch.setattr(support, "_kill_tree_once", fake_kill_once)
    monkeypatch.setattr(
        support, "_wait_until_exit",
        lambda pids, timeout=0: [] if len(rounds) >= 2 else list(pids),
    )

    result = kill_process_tree(1000)
    assert result.attempts == 2
    assert result.killed_pids == (1000, 1100)
    assert result.remaining_pids == ()
    assert result.success is True


# ── 3. 存活判定（含僵尸） ──────────────────────────────

def test_pid_alive_self_true_and_invalid_false():
    """当前进程存活；0 / 负数 PID 一律判死。"""
    assert support._pid_alive(os.getpid()) is True
    assert support._pid_alive(0) is False
    assert support._pid_alive(-5) is False
    assert support._pid_alive("not-a-pid") is False


def test_pid_alive_process_lookup_error_is_dead(monkeypatch):
    """进程不存在（ProcessLookupError）→ 判死。"""
    def fake_kill(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(support.os, "kill", fake_kill)
    assert support._pid_alive(4242) is False


def test_pid_alive_permission_error_is_alive(monkeypatch):
    """无权限操作（PermissionError）说明进程仍存在 → 判存活。"""
    def fake_kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(support.os, "kill", fake_kill)
    monkeypatch.setattr(support, "_pid_state", lambda pid: "S")
    assert support._pid_alive(4242) is True


def test_pid_alive_zombie_is_dead(monkeypatch):
    """僵尸进程已终止（无法再杀）→ 判死，避免无限「补杀」。"""
    monkeypatch.setattr(support.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(support, "_pid_state", lambda pid: "Z")
    assert support._pid_alive(4242) is False


@pytest.mark.skipif(os.name == "nt", reason="Windows 原生无 /proc 状态字符")
def test_pid_state_reads_current_process():
    """当前进程状态可读（非僵尸）。"""
    state = support._pid_state(os.getpid())
    assert state is not None
    assert state != "Z"


@_POSIX_ONLY
def test_pid_alive_real_zombie_is_dead():
    """真实僵尸进程（未收尸的子进程）判死：杀完不会误判「没杀死」。"""
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        time.sleep(0.2)
        os.kill(proc.pid, support._signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while support._pid_state(proc.pid) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert support._pid_state(proc.pid) == "Z"
        assert support._pid_alive(proc.pid) is False
    finally:
        proc.wait(timeout=5)


# ── 4. 跨平台收集 / 辅助函数 ───────────────────────────

def test_children_map_from_ps_parses(monkeypatch):
    """ps 输出解析：合法行入表，非法行跳过。"""
    monkeypatch.setattr(
        support, "_run_quiet",
        lambda argv: "   10     1\n   11    10\n garbage line\n",
    )
    mapping = support._children_map_from_ps()
    assert mapping[1] == [10]
    assert mapping[10] == [11]


def test_parse_ps_output_with_header():
    """带表头（ps -ef / ps ax 风格）按 PID/PPID 列名定位列。"""
    text = (
        "     UID     PID    PPID  TTY        STIME COMMAND\n"
        "     lmy    5280    5271 pty2     07:03:12 /usr/bin/ps\n"
        "     lmy   31779   31778 pty2     05:06:32 /usr/bin/bash\n"
    )
    mapping = support._parse_ps_output(text)
    assert mapping[5271] == [5280]
    assert mapping[31778] == [31779]


def test_children_map_from_ps_falls_back_across_command_forms(monkeypatch):
    """首个解析出结果的 ps 形态即采用（GNU 无表头失败 → Cygwin ps -ef 成功）。"""
    seen: list[list[str]] = []

    def fake_run(argv):
        seen.append(argv)
        if argv[:2] == ["ps", "-eo"]:
            return "ps: unknown option -- o\n"
        return "  PID  PPID\n  10     1\n  11    10\n"

    monkeypatch.setattr(support, "_run_quiet", fake_run)
    mapping = support._children_map_from_ps()
    assert seen[0] == ["ps", "-eo", "pid=,ppid="]
    assert mapping[1] == [10]
    assert mapping[10] == [11]


def test_read_ppid_missing_returns_none():
    """不存在的 PID 读不到 PPid（返回 None，不抛异常）。"""
    assert support._read_ppid(999999) is None


def test_children_map_contains_current_process():
    """后代映射能定位当前进程（/proc 或 ps 至少一条通道可用）。"""
    mapping = support._children_map()
    if not mapping:
        pytest.skip("当前环境无 /proc 且 ps 不可用")
    assert any(os.getpid() in children for children in mapping.values())


def test_is_windows_flag_matches_os_name():
    """Windows 判定与 os.name 一致（Cygwin 为 posix，不算 Windows 原生）。"""
    assert support._is_windows() is (os.name == "nt")


def test_kill_pid_windows_invokes_taskkill(monkeypatch):
    """Windows 原生：用 taskkill /F /T 杀整棵进程树。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_run_quiet",
                        lambda argv: calls.append(argv) or "")
    support._kill_pid(1234)
    assert calls == [["taskkill", "/F", "/T", "/PID", "1234"]]


def test_kill_pid_posix_ignores_missing_process(monkeypatch):
    """POSIX：进程已退出（ProcessLookupError）不抛异常。"""
    monkeypatch.setattr(support, "_is_windows", lambda: False)
    monkeypatch.setattr(support.os, "kill", lambda pid, sig: None)
    support._kill_pid(4321)  # 不抛异常

    def raise_lookup(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(support.os, "kill", raise_lookup)
    support._kill_pid(4322)  # 不抛异常


# ── 5. 真实进程树（端到端） ────────────────────────────

@_POSIX_ONLY
def test_collect_tree_pids_finds_descendants():
    """真实进程树：能收集到根进程与全部后代。"""
    proc = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30 & wait"],
                            start_new_session=True)
    try:
        time.sleep(0.3)
        tree = support._collect_tree_pids(proc.pid)
        assert proc.pid in tree
        assert len(tree) >= 2  # 至少一个后台 sleep 后代
    finally:
        support.kill_process_tree(proc.pid, verify=False)
        proc.wait(timeout=5)


@_POSIX_ONLY
def test_kill_process_tree_real_tree_all_dead():
    """真实进程树：杀后校验全部退出，KillResult.success 为真。"""
    proc = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30 & wait"],
                            start_new_session=True)
    try:
        time.sleep(0.3)
        tree = support._collect_tree_pids(proc.pid)
        assert len(tree) >= 2

        result = kill_process_tree(proc.pid)
        proc.wait(timeout=5)

        assert result.verified is True
        assert result.attempts >= 1
        assert result.success is True
        assert set(result.killed_pids) >= set(tree)
        for pid in tree:
            assert support._pid_alive(pid) is False
    finally:
        if proc.poll() is None:
            support.kill_process_tree(proc.pid, verify=False)
            proc.wait(timeout=5)


@_POSIX_ONLY
def test_kill_process_tree_real_process_no_children():
    """无子进程的普通进程同样被杀并校验通过。"""
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    time.sleep(0.2)
    result = kill_process_tree(proc.pid)
    proc.wait(timeout=5)
    assert result.success is True
    assert proc.returncode is not None


# ── 6. 模块导出 / bash_opt op=kill 集成 ────────────────

def test_bash_module_reexports_kill_process_tree():
    """bash 模块 re-export 公开 API（bash_opt 与 ESC 路径经此导入）。"""
    from src.tools import bash
    assert bash.kill_process_tree is support.kill_process_tree
    assert bash._kill_process_tree is support._kill_process_tree


async def test_bash_opt_kill_reports_verified_no_residual(monkeypatch):
    """op=kill 报告「校验无残留」，并移除任务记录。"""
    agent = _FakeAgent({"bg-x": {"pid": 1234, "process": None, "task": None}})
    captured: dict = {}

    def fake_kill(pid, **kwargs):
        captured["pid"] = pid
        captured["kwargs"] = kwargs
        return KillResult(pid=pid, attempts=2, killed_pids=(pid,), verified=True)

    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree", fake_kill)

    opt = BashOptFunc(task_id="bg-x", op="kill")
    opt.set_agent(agent)
    out = await opt.execute()

    assert captured["pid"] == 1234
    assert "校验" in out and "无残留进程" in out
    assert "bg-x" not in agent._background_tasks
    assert agent.removed == ["bg-x"]


async def test_bash_opt_kill_reports_residual_pids(monkeypatch):
    """op=kill 校验仍有残留时列出残留 PID（可再次 kill 补杀）。"""
    agent = _FakeAgent({"bg-y": {"pid": 1234, "process": None, "task": None}})

    def fake_kill(pid, **kwargs):
        return KillResult(pid=pid, attempts=3,
                          remaining_pids=(pid, 55), verified=True)

    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree", fake_kill)

    opt = BashOptFunc(task_id="bg-y", op="kill")
    opt.set_agent(agent)
    out = await opt.execute()

    assert "仍存活" in out
    assert "1234" in out and "55" in out


async def test_bash_opt_kill_uses_process_pid_when_record_pid_missing(monkeypatch):
    """记录 pid 缺失但有 process 对象：用 process.pid 杀进程树。"""
    class _Proc:
        pid = 4321
        returncode = None
        killed = False

        def kill(self):
            self.killed = True

    proc = _Proc()
    agent = _FakeAgent({"bg-z": {"pid": None, "process": proc, "task": None}})
    captured: dict = {}

    def fake_kill(pid, **kwargs):
        captured["pid"] = pid
        return KillResult(pid=pid, attempts=1, killed_pids=(pid,), verified=True)

    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree", fake_kill)

    opt = BashOptFunc(task_id="bg-z", op="kill")
    opt.set_agent(agent)
    out = await opt.execute()

    assert captured["pid"] == 4321
    assert proc.killed is False  # 进程树杀已覆盖，无需 process.kill 兜底
    assert "bg-z" not in agent._background_tasks


async def test_bash_opt_kill_without_process_handle(monkeypatch):
    """无 pid 也无 process：不调用进程树杀，仍移除任务记录。"""
    called: list = []
    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree",
                        lambda *a, **k: called.append(a))
    agent = _FakeAgent({"bg-n": {"pid": None, "process": None, "task": None}})

    opt = BashOptFunc(task_id="bg-n", op="kill")
    opt.set_agent(agent)
    out = await opt.execute()

    assert called == []
    assert "已杀死后台任务 bg-n" in out
    assert "bg-n" not in agent._background_tasks


async def test_bash_opt_kill_cancels_running_task(monkeypatch):
    """运行中的 asyncio 后台任务被取消（进程树杀之外的第二重保证）。"""
    async def _long():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_long())
    agent = _FakeAgent({"bg-t": {"pid": 987, "process": None, "task": task}})
    monkeypatch.setattr("src.tools.bash_opt.kill_process_tree",
                        lambda pid, **k: KillResult(pid=pid, attempts=1,
                                                    killed_pids=(pid,),
                                                    verified=True))

    opt = BashOptFunc(task_id="bg-t", op="kill")
    opt.set_agent(agent)
    await opt.execute()

    assert task.cancelled()
    try:
        await task
    except asyncio.CancelledError:
        pass
