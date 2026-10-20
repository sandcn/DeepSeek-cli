"""Cygwin 下窗口 PID 归属解析（``resolve_window_pids``）回归测试。

背景：Cygwin 的 POSIX PID 与 Windows PID 是两套编号；后台命令可能
``exec`` 成原生 GUI（regedit / notepad），POSIX 进程退出后
``/proc/<pid>/winpid`` 消失。修复前会把 POSIX PID 直接当 Windows PID
用（可能命中无关窗口），或直接失败。修复后优先查任务登记表
（``_task_winpid``）的 Windows PID / Job 成员 / exe 名兜底。
"""

from __future__ import annotations

import pytest

from src.tools import _task_winpid, _win_proc
from src.tools._screenshot import proctree, win, winapi


@pytest.fixture
def cygwin_env(monkeypatch):
    """把平台判定固定为 Cygwin，并清空任务登记。"""
    _task_winpid.clear()
    monkeypatch.setattr(winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(proctree, "collect_process_tree", lambda pid: [pid])
    monkeypatch.setattr(proctree, "read_winpid", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "winpid_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "job_processes", lambda job: [])
    monkeypatch.setattr(_win_proc, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(win, "_expand_windows_descendants", lambda pids: set(pids))
    yield
    _task_winpid.clear()


def test_registered_winpid_used_when_proc_mapping_gone(cygwin_env, monkeypatch):
    """POSIX 进程 exec 后 /proc 映射消失：用登记的 Windows PID 定位窗口。"""
    monkeypatch.setattr(_task_winpid, "winpid_for", lambda pid: 5555)
    assert win.resolve_window_pids(4242) == {5555}


def test_job_members_added_to_window_candidates(cygwin_env, monkeypatch):
    """Job 成员（原生子进程）不在 POSIX 树里，也要纳入窗口候选。"""
    monkeypatch.setattr(_task_winpid, "winpid_for", lambda pid: 5555)
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: 900)
    monkeypatch.setattr(_task_winpid, "job_processes", lambda job: [7777])
    assert win.resolve_window_pids(4242) == {5555, 7777}


def test_store_app_hint_filters_by_start_time(cygwin_env, monkeypatch):
    """App Execution Alias：按 exe 名 + 启动时间纳入真实进程。"""
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: "notepad.exe")
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: 100_000_000)
    monkeypatch.setattr(
        _win_proc, "find_processes_by_name", lambda name: [7777, 8888])
    monkeypatch.setattr(
        _win_proc, "process_create_time",
        lambda pid: {7777: 101_000_000, 8888: 1}.get(pid),
    )
    assert win.resolve_window_pids(4242) == {7777}


def test_posix_pid_not_used_as_windows_pid(cygwin_env, monkeypatch):
    """映射不到且 pid 不是真实 Windows 进程时，不能把 POSIX PID 当 Windows PID。"""
    monkeypatch.setattr(winapi, "list_processes", lambda: [(9999, 1, "other.exe")])
    assert win.resolve_window_pids(4242) == set()


def test_pid_is_real_windows_process_falls_back(cygwin_env, monkeypatch):
    """调用方直接传 Windows PID（无 /proc 映射）时仍可按原值处理。"""
    monkeypatch.setattr(winapi, "list_processes", lambda: [(4242, 1, "app.exe")])
    assert win.resolve_window_pids(4242) == {4242}
