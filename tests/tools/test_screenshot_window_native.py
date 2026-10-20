"""原生 Windows 下窗口归属解析（``resolve_window_pids``）回归测试。

背景（Windows 11 原生实测）：Store / 打包应用（Notepad、Windows Terminal
等）由 App Execution Alias 在**别的进程**里激活，父链指向 svchost /
explorer，不是任务根进程的后代。修复前非 Cygwin 分支直接 ``base = {pid}``，
``_task_winpid`` 的 Job / exe 名兜底只在 Cygwin 分支执行，于是：

  - ``op=wait_window`` 8 秒超时、``op=windows`` 返回 0 个窗口、
    ``op=screenshot`` 报「没有可截图的可见窗口」；
  - 而 ``op=kill``（进程树采样器记录过该 PID）却能把进程杀掉——两者行为
    不一致，用户侧表现为「能杀不能看」。

修复后两个平台都执行 Job 成员 + exe 名（带启动时间过滤）兜底；按名命中的
进程只取自身、**不展开后代**（沿用防串窗约束）。
"""

from __future__ import annotations

import pytest

from src.tools import _task_winpid, _win_proc
from src.tools._screenshot import win, winapi


@pytest.fixture
def native_env(monkeypatch):
    """固定为原生 Windows，并清空任务登记。"""
    _task_winpid.clear()
    monkeypatch.setattr(winapi, "is_cygwin_like", lambda: False)
    monkeypatch.setattr(win, "_expand_windows_descendants", lambda pids: set(pids))
    yield
    _task_winpid.clear()


def test_store_app_hint_included_on_native_windows(native_env, monkeypatch):
    """打包应用（如 notepad）按 exe 名兜底纳入窗口候选。"""
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: "notepad")
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: 100_000_000)
    monkeypatch.setattr(_win_proc, "find_processes_by_name", lambda name: [7777])
    monkeypatch.setattr(_win_proc, "process_create_time", lambda pid: 101_000_000)

    assert win.resolve_window_pids(4242) == {4242, 7777}


def test_store_app_hint_filters_processes_started_before_task(native_env, monkeypatch):
    """任务启动前就存在的同名程序（用户自己开着的 Notepad）不能被算进来。"""
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: "notepad")
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: 100_000_000)
    monkeypatch.setattr(_win_proc, "find_processes_by_name", lambda name: [7777, 8888])
    monkeypatch.setattr(_win_proc, "process_create_time",
                        lambda pid: {7777: 101_000_000, 8888: 1}.get(pid))

    assert win.resolve_window_pids(4242) == {4242, 7777}


def test_job_members_included_on_native_windows(native_env, monkeypatch):
    """Toolhelp32 父子链断开的原生子进程（在 Job 里）也要纳入窗口候选。"""
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: 900)
    monkeypatch.setattr(_task_winpid, "job_processes", lambda job: [6666])
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: None)

    assert win.resolve_window_pids(4242) == {4242, 6666}


def test_hint_candidates_are_not_expanded(native_env, monkeypatch):
    """按名兜底只取进程自身，绝不展开它的 Windows 后代（防串窗）。"""
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: "notepad")
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: 1)
    monkeypatch.setattr(_win_proc, "find_processes_by_name", lambda name: [7777])
    monkeypatch.setattr(_win_proc, "process_create_time", lambda pid: 5)

    expanded: list[set[int]] = []

    def fake_expand(root_pids):
        expanded.append(set(root_pids))
        return set(root_pids) | {9999}

    monkeypatch.setattr(win, "_expand_windows_descendants", fake_expand)

    assert win.resolve_window_pids(4242) == {4242, 9999, 7777}
    assert expanded == [{4242}]


def test_native_without_registration_keeps_root_only(native_env, monkeypatch):
    """没有登记信息时保持旧行为：根 pid（+ 后代展开结果）。"""
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: None)

    assert win.resolve_window_pids(4242) == {4242}
