"""Cygwin / MSYS2 下的窗口归属隔离与 Windows 盘符路径回归（2026-10-20）。

两个真实缺陷（当前 Cygwin 环境实测）：

1. **串窗 / 误杀**：``_command_exe_hint`` 把命令行首 token 当 ``win_hint``
   （Cygwin 下通常是 ``bash``），``find_processes_by_name`` 于是命中**所有**
   bash.exe（含其它任务的命令包装器），再由 ``_expand_windows_descendants``
   展开它们的 Windows 后代——把别的任务的 GUI 窗口算进当前任务。实测：
   无窗口的 PTY bash 任务 ``op=windows`` / ``screenshot`` 返回另一个任务的
   WinForms 窗口，``op=type`` 的文本被打进那个窗口的输入框；``op=kill``
   的按名兜底同样会误杀用户开着的同名程序。

   修复：shell / 解释器名不再作为 hint；按名兜底命中的进程只取自身、
   不展开 Windows 后代；创建时间读不到的候选不再参与杀树兜底。

2. **Windows 盘符路径**：Cygwin 的 Python 是 POSIX 语义，
   ``os.path.abspath("E:/tmp/a.png")`` 得到 ``<cwd>/E:/tmp/a.png``——实测
   截图被写进项目目录，并新建出 ``E:`` 目录。修复：``normalize_user_path``
   在 Cygwin / MSYS2 上把盘符路径换算成 ``/cygdrive/e/...`` / ``/e/...``。
"""

from __future__ import annotations

import types

import pytest

from src.tools import _bash_support as support
from src.tools import _task_winpid, _win_proc, bash as bash_mod, file_ops
from src.tools._screenshot import win as win_backend


# ── 1. shell / 解释器名不做「同名进程」兜底 ─────────────────

def test_is_shell_like_exe():
    assert support.is_shell_like_exe("bash") is True
    assert support.is_shell_like_exe("BASH.EXE") is True
    assert support.is_shell_like_exe("python3.9") is True
    assert support.is_shell_like_exe(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe") is True
    assert support.is_shell_like_exe("mintty.exe") is False
    assert support.is_shell_like_exe("notepad.exe") is False
    assert support.is_shell_like_exe("") is False
    assert support.is_shell_like_exe(None) is False


def test_command_exe_hint_skips_shell_like():
    """``bash --login -i`` 这类命令不再产出可做同名兜底的 hint。"""
    assert bash_mod._command_exe_hint("bash --login -i") is None
    assert bash_mod._command_exe_hint("sh -c 'echo hi'") is None
    assert bash_mod._command_exe_hint("python3.9 -u script.py") is None
    assert bash_mod._command_exe_hint(
        "powershell.exe -NoProfile -File x.ps1") is None
    # 真正的 GUI 程序仍然保留 hint
    assert bash_mod._command_exe_hint("cmd /c notepad.exe") == "notepad.exe"
    assert bash_mod._command_exe_hint(
        "mintty.exe --title=x -e sleep 5") == "mintty.exe"
    assert bash_mod._command_exe_hint("") is None


# ── 2. hint 兜底命中的进程不参与 Windows 后代展开（防串窗） ──

def test_resolve_window_pids_does_not_expand_hint_candidates(monkeypatch):
    """hint 候选只取自身：后代展开只针对可信归属（根 WINPID / Job）。"""
    expanded: list[set[int]] = []

    monkeypatch.setattr(win_backend.winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(win_backend.proctree, "collect_process_tree",
                        lambda pid: [pid])
    monkeypatch.setattr(win_backend.proctree, "read_winpid", lambda pid: 1000)
    monkeypatch.setattr(_task_winpid, "winpid_for", lambda pid: 1000)
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: "notepad")
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: 1)
    monkeypatch.setattr(_win_proc, "is_process_alive", lambda winpid: True)
    monkeypatch.setattr(_win_proc, "find_processes_by_name", lambda hint: [2000])
    monkeypatch.setattr(_win_proc, "process_create_time", lambda pid: 5)

    def fake_expand(root_pids):
        expanded.append(set(root_pids))
        return set(root_pids) | {3000}

    monkeypatch.setattr(win_backend, "_expand_windows_descendants", fake_expand)

    result = win_backend.resolve_window_pids(42)

    assert expanded == [{1000}], "hint 候选不应参与 Windows 后代展开"
    assert result == {1000, 3000, 2000}


def test_resolve_window_pids_without_hint_only_expands_trusted(monkeypatch):
    expanded: list[set[int]] = []

    monkeypatch.setattr(win_backend.winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(win_backend.proctree, "collect_process_tree",
                        lambda pid: [pid])
    monkeypatch.setattr(win_backend.proctree, "read_winpid", lambda pid: 1000)
    monkeypatch.setattr(_task_winpid, "winpid_for", lambda pid: 1000)
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: 1)
    monkeypatch.setattr(_win_proc, "is_process_alive", lambda winpid: True)

    def fake_expand(root_pids):
        expanded.append(set(root_pids))
        return set(root_pids) | {3000}

    monkeypatch.setattr(win_backend, "_expand_windows_descendants", fake_expand)

    assert win_backend.resolve_window_pids(42) == {1000, 3000}
    assert expanded == [{1000}]


def test_resolve_window_pids_empty_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(win_backend.winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(win_backend.proctree, "collect_process_tree",
                        lambda pid: [pid])
    monkeypatch.setattr(win_backend.proctree, "read_winpid", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "winpid_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "job_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "hint_for", lambda pid: None)
    monkeypatch.setattr(_task_winpid, "start_time_for", lambda pid: None)
    monkeypatch.setattr(win_backend.winapi, "list_processes", lambda: [])

    assert win_backend.resolve_window_pids(42) == set()


# ── 3. 杀树兜底：shell 不兜底、未知创建时间不杀 ─────────────

def test_win_pids_for_hint_skips_unknown_create_time(monkeypatch):
    times = {11: None, 12: 2_000_000_000, 13: 1_000_000_000}
    monkeypatch.setattr(_win_proc, "find_processes_by_name",
                        lambda hint: [11, 12, 13])
    monkeypatch.setattr(_win_proc, "process_create_time",
                        lambda pid: times[pid])

    assert support._win_pids_for_hint("notepad", 1_500_000_000) == [12]


def test_win_pids_for_hint_refuses_shell_names(monkeypatch):
    monkeypatch.setattr(
        _win_proc, "find_processes_by_name",
        lambda hint: pytest.fail("shell / 解释器不应触发同名进程查询"),
    )
    assert support._win_pids_for_hint("bash", 1_500_000_000) == []
    assert support._win_pids_for_hint("python3.9", 1_500_000_000) == []
    assert support._win_pids_for_hint(None, 1_500_000_000) == []


# ── 4. Windows 盘符路径换算 ────────────────────────────────

def _fake_sys(platform: str):
    return types.SimpleNamespace(platform=platform)


def test_normalize_user_path_cygwin(monkeypatch):
    monkeypatch.setattr(file_ops, "sys", _fake_sys("cygwin"))
    assert file_ops.normalize_user_path("E:/tmp/a.png") == "/cygdrive/e/tmp/a.png"
    assert file_ops.normalize_user_path(r"E:\tmp\a.png") == "/cygdrive/e/tmp/a.png"
    assert file_ops.normalize_user_path("/tmp/a.png") == "/tmp/a.png"
    assert file_ops.normalize_user_path("") == ""


def test_normalize_user_path_msys(monkeypatch):
    monkeypatch.setattr(file_ops, "sys", _fake_sys("msys"))
    assert file_ops.normalize_user_path("E:/tmp/a.png") == "/e/tmp/a.png"


def test_normalize_user_path_native_windows_unchanged(monkeypatch):
    monkeypatch.setattr(file_ops, "sys", _fake_sys("win32"))
    assert file_ops.normalize_user_path("E:/tmp/a.png") == "E:/tmp/a.png"
    assert file_ops.normalize_user_path(r"E:\tmp\a.png") == r"E:\tmp\a.png"


def test_normalize_user_path_unc(monkeypatch):
    monkeypatch.setattr(file_ops, "sys", _fake_sys("cygwin"))
    assert (file_ops.normalize_user_path(r"\\server\share\a.png")
            == "//server/share/a.png")


def test_prepare_screenshot_path_converts_windows_drive(monkeypatch):
    """端到端：``E:/tmp/out.png`` 落到 /cygdrive/e/tmp/out.png。"""
    from src.tools import bash_opt as bo

    monkeypatch.setattr(file_ops, "sys", _fake_sys("cygwin"))
    monkeypatch.setattr(bo.os, "makedirs", lambda *args, **kwargs: None)

    assert (bo.BashOptFunc._prepare_screenshot_path("E:/tmp/out.png")
            == "/cygdrive/e/tmp/out.png")


def test_prepare_image_path_converts_windows_drive(monkeypatch):
    from src.tools import bash_opt as bo

    monkeypatch.setattr(file_ops, "sys", _fake_sys("cygwin"))
    monkeypatch.setattr(bo.os.path, "isfile", lambda path: True)

    assert (bo.BashOptFunc._prepare_image_path("E:/tmp/in.png")
            == "/cygdrive/e/tmp/in.png")


def test_macro_path_converts_windows_drive(monkeypatch):
    from src.tools._window_input import macro as macro_mod

    monkeypatch.setattr(file_ops, "sys", _fake_sys("cygwin"))
    monkeypatch.setattr(macro_mod, "validate_path_security", lambda path: None)

    assert (macro_mod.resolve_macro_path("E:/tmp/m.json")
            == "/cygdrive/e/tmp/m.json")
