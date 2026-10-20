"""Cygwin / MSYS2 下「daemon 化的 GUI 杀不掉」回归测试（2026-10-20）。

缺陷（本机 Cygwin 实测）：``mintty`` 由任务启动后会 daemon 化——启动器
0.07 秒内退出，真正的 mintty 被 reparent 到 init（POSIX ``PPID=1``），
Windows ``th32ParentProcessID`` 指向已退出的 fork 中间体。于是：

  - ``_collect_tree_pids``（Cygwin ``ps``）看不到它；
  - ``_collect_win_tree``（Toolhelp32 父子链）看不到它；
  - 运行期采样器 ``ProcessTreeTracker`` 沿同一套链，同样看不到；
  - 命令不是以 mintty 开头时（``cd /tmp && mintty ...`` / ``bash -c ...``）
    原有 ``win_hint``（只取首个 token，= ``cd`` / ``None``）也命中不了。

``op=kill`` 因此返回「校验：无残留进程」，mintty 窗口却还在（Job 建不起来
时 100% 复现）。

修复：
  - ``_command_exe_hints`` 按「简单命令」分段取**全部**程序名候选，并解析
    ``bash -c`` / ``python -c`` 参数里的程序；
  - ``kill_process_tree(win_hints=...)`` 用全部候选做按名兜底；
  - 按名匹配限定在任务启动后 :data:`_HINT_MATCH_WINDOW` 内（避免把用户
    中途打开的同名终端/程序当成任务进程误杀），没有启动时间则不做兜底。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.tools import _bash_support as support
from src.tools import _win_proc
from src.tools import bash as bash_mod
from src.tools._bash_support import kill_process_tree


# ── 1. 命令行程序名候选：分段 + 解包解释器参数 ──────────────

def test_command_exe_hints_collects_later_segments():
    """``cd /tmp && mintty ...`` 的 mintty 也要成为候选。"""
    assert bash_mod._command_exe_hints(
        "cd /tmp && mintty -t x -e sleep 300") == ["mintty"]
    assert bash_mod._command_exe_hint(
        "cd /tmp && mintty -t x -e sleep 300") == "mintty"
    # 多个程序位按出现顺序保留（kill 会全部参与按名兜底）
    assert bash_mod._command_exe_hints("sleep 5 && mintty -t x") == [
        "sleep", "mintty"]


def test_command_exe_hints_unwraps_shell_and_interpreter():
    """shell / 解释器包装要往下解析到真正的程序。"""
    assert bash_mod._command_exe_hints("bash -c 'mintty -t x -e sleep 5'") == [
        "mintty"]
    assert bash_mod._command_exe_hints("sh -c 'mintty'") == ["mintty"]
    assert bash_mod._command_exe_hints(
        "python -c \"import subprocess; subprocess.Popen(['mintty'])\""
    ) == ["mintty"]


def test_command_exe_hints_skips_launchers_and_builtins():
    """透传启动器后才是程序；shell 内建命令的参数只是数据。"""
    assert bash_mod._command_exe_hints("cygstart mintty -t x") == ["mintty"]
    assert bash_mod._command_exe_hints("env FOO=1 mintty -t x") == ["mintty"]
    assert bash_mod._command_exe_hints("cd /tmp") == []
    assert bash_mod._command_exe_hints("echo hello") == []
    assert bash_mod._command_exe_hints("bash --login -i") == []
    assert bash_mod._command_exe_hints("") == []
    assert bash_mod._command_exe_hint("") is None


def test_command_exe_hints_keeps_exe_token_priority():
    """显式 ``.exe`` token 仍优先（Store 应用的窗口归属兜底形态不变）。"""
    assert bash_mod._command_exe_hint("cmd /c notepad.exe") == "notepad.exe"
    assert bash_mod._command_exe_hint(
        "mintty.exe --title=x -e sleep 5") == "mintty.exe"
    assert bash_mod._command_exe_hint(
        "powershell -Command 'Start-Process mspaint.exe'") == "mspaint.exe"


# ── 2. 按名兜底：多候选 + 启动时间窗 ───────────────────────

def _patch_processes(monkeypatch, mapping: dict[str, list[int]],
                     times: dict[int, int | None]):
    monkeypatch.setattr(
        _win_proc, "find_processes_by_name",
        lambda name: list(mapping.get(name, ())))
    monkeypatch.setattr(
        _win_proc, "process_create_time", lambda pid: times.get(pid))


def test_win_pids_for_hint_accepts_multiple_names(monkeypatch):
    start = 1_500_000_000
    _patch_processes(
        monkeypatch,
        {"mintty": [11, 12], "notepad": [12, 13]},
        {11: start + 1, 12: start + 2, 13: start + 3},
    )
    # 多个候选取并集、去重；单个字符串仍然兼容
    assert support._win_pids_for_hint(["mintty", "notepad"], start) == [11, 12, 13]
    assert support._win_pids_for_hint("mintty", start) == [11, 12]


def test_win_pids_for_hint_skips_shell_names(monkeypatch):
    start = 1_500_000_000

    def _find(name):
        if support.is_shell_like_exe(name):
            pytest.fail(f"shell / 解释器名不应触发同名进程查询: {name}")
        return [11] if name == "mintty" else []

    monkeypatch.setattr(_win_proc, "find_processes_by_name", _find)
    monkeypatch.setattr(_win_proc, "process_create_time", lambda pid: start + 1)

    assert support._win_pids_for_hint(["bash", "mintty", "mintty"], start) == [11]
    assert support._win_pids_for_hint(None, start) == []
    assert support._win_pids_for_hint([], start) == []


def test_win_pids_for_hint_requires_start_time(monkeypatch):
    """没有任务启动时间就不按名兜底（否则会命中用户开着的同名程序）。"""
    monkeypatch.setattr(
        _win_proc, "find_processes_by_name",
        lambda name: pytest.fail("没有启动时间时不应按名匹配"))
    assert support._win_pids_for_hint(["mintty"], None) == []
    assert support._win_pids_for_hint(["mintty"], 0) == []


def test_win_pids_for_hint_excludes_processes_after_window(monkeypatch):
    """任务启动很久之后才出现的同名进程不是任务进程（用户自己开的）。"""
    start = 1_500_000_000
    later = start + support._HINT_MATCH_WINDOW + 10_000_000
    _patch_processes(monkeypatch, {"mintty": [11, 12]},
                     {11: start + 5, 12: later})
    assert support._win_pids_for_hint("mintty", start) == [11]


def test_win_pids_for_hint_excludes_programs_started_before_task(monkeypatch):
    """任务启动前就存在的同名进程（例如用户的终端）不算任务进程。"""
    start = 1_500_000_000
    _patch_processes(monkeypatch, {"mintty": [11]}, {11: start - 30_000_000})
    assert support._win_pids_for_hint("mintty", start) == []


# ── 3. kill_process_tree 用全部候选做按名兜底 ───────────────

def _patch_kill_env(monkeypatch, captured: dict):
    monkeypatch.setattr(support, "_is_cygwin_like", lambda: True)
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_pid_reused", lambda pid, ct: False)
    monkeypatch.setattr(support, "_winpid_of", lambda pid, retries=0: None)
    monkeypatch.setattr(support, "_self_winpid", lambda: 999)
    monkeypatch.setattr(support, "_collect_tree_pids", lambda pid: [pid])
    monkeypatch.setattr(support, "_collect_win_tree", lambda winpid: [])
    monkeypatch.setattr(
        support, "_kill_tree_once",
        lambda root, collected=None, extra_pids=None, **kwargs:
        captured.update(kwargs) or [root])
    monkeypatch.setattr(support, "_wait_until_exit", lambda pids, timeout=0: [])
    monkeypatch.setattr(
        support, "_wait_win_until_exit", lambda pids, timeout=0: [])
    monkeypatch.setattr(support, "_wait_job_empty",
                        lambda handle, timeout=0: 0)


def test_kill_process_tree_kills_hint_matched_gui(monkeypatch):
    """Job / 进程树都够不着时，按名候选仍然进入杀与校验集合。"""
    start = 1_500_000_000
    captured: dict = {}
    _patch_kill_env(monkeypatch, captured)
    _patch_processes(monkeypatch, {"mintty": [4004]},
                     {4004: start + 2})

    result = kill_process_tree(
        4321, win_hints=["mintty"], win_start_time=start)

    assert captured["win_root"] is None
    assert captured["extra_win_pids"] == [4004]
    assert result.remaining_win_pids == ()
    assert result.attempts == 1


def test_kill_process_tree_merges_single_and_multi_hints(monkeypatch):
    """``win_hint``（单值）与 ``win_hints``（多值）合并去重后一起兜底。"""
    start = 1_500_000_000
    captured: dict = {}
    _patch_kill_env(monkeypatch, captured)
    _patch_processes(
        monkeypatch,
        {"mintty": [4004], "notepad.exe": [5005]},
        {4004: start + 2, 5005: start + 3},
    )

    kill_process_tree(4321, win_hint="mintty", win_hints=["mintty", "notepad.exe"],
                      win_start_time=start)

    assert captured["extra_win_pids"] == [4004, 5005]


def test_kill_process_tree_without_hints_still_kills_tree(monkeypatch):
    """没有 hint 时行为不变（只是不含按名兜底）。"""
    captured: dict = {}
    _patch_kill_env(monkeypatch, captured)
    monkeypatch.setattr(
        _win_proc, "find_processes_by_name",
        lambda name: pytest.fail("没有 hint 不应按名匹配"))

    result = kill_process_tree(4321, win_start_time=1_500_000_000)

    assert captured["extra_win_pids"] == []
    assert result.success is True


# ── 4. 任务记录：后台任务保存全部候选 ───────────────────────

@pytest.mark.asyncio
async def test_background_record_stores_all_hints(monkeypatch):
    """``bash background=true`` 的任务记录里要带 ``win_hints``（供 kill 用）。"""

    class _Agent:
        def __init__(self):
            self._background_tasks = {}

        def _register_background_task(self, task_id, rec):
            self._background_tasks[task_id] = rec

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(bash_mod.BashFunc, "_run_background_task", _noop)
    monkeypatch.setattr(bash_mod, "print_to_terminal", _noop)

    agent = _Agent()
    func = bash_mod.BashFunc(
        "cd /tmp && mintty -t x -e sleep 5", background=True)
    func.agent = agent
    out = await func.execute()
    await asyncio.sleep(0)

    task_id = json.loads(out)["task_id"]
    rec = agent._background_tasks[task_id]
    assert rec["win_hints"] == ["mintty"]
    assert rec["win_hint"] == "mintty"
