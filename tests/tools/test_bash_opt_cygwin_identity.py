"""Cygwin / MSYS2 下的进程身份归属回归测试（2026-10-20）。

两个真实缺陷（当前环境 Cygwin 实测）：

1. **窗口归属**：Cygwin 命令名通常不带 ``.exe``（``mintty`` / ``python``），
   而 Toolhelp32 的 exe 名恒为 ``mintty.exe``。``find_processes_by_name``
   按字面精确比较 → hint 兜底恒为空，``op=windows`` / ``op=screenshot`` /
   输入 op 的窗口解析对 daemon 化后的 GUI 程序（mintty reparent 到 init）
   完全失效（实测 ``mintty -t ... -e sleep`` 任务返回 ``windows_total=0``，
   而窗口确实存在）。

2. **kill 拒绝杀树**：Cygwin 的 ``bash -c`` 在 exec 目标程序时会重建
   Windows 进程（``/proc/<pid>/winpid`` 与创建时间都变）。后台任务记录的是
   exec **前**的创建时间，``op=kill`` 的 pid 复用防护据此误判「PID 已被
   系统复用」并拒绝杀树——PTY 后台任务（Cygwin 下没有 Job，
   ``job_was_active`` 为 0）实测 100% 复现。修复：PTY 分支也为子进程建立
   Windows Job，Job 内仍有活动成员时不做复用拒绝。

本文件覆盖：名称后缀归一化、PTY 分支的 Job 透传、Job 活跃时跳过复用防护。
"""

from __future__ import annotations

import pytest

from src.tools import _bash_support as support
from src.tools import _win_proc
from src.tools import bash as bash_mod
from src.tools._bash_support import kill_process_tree
from src.tools.bash import BashFunc


# ── 1. exe 名归一化（Cygwin 命令名不带 .exe） ──────────────

def test_find_processes_by_name_matches_without_exe_suffix(monkeypatch):
    """``mintty`` 能命中 ``mintty.exe``，``mintty.exe`` 同样能命中。"""
    monkeypatch.setattr(
        _win_proc, "list_processes",
        lambda: [(101, 1, "mintty.exe"), (102, 1, "MINTTY.EXE"),
                 (103, 1, "notepad.exe"), (104, 1, "notepadplus.exe")],
    )
    assert _win_proc.find_processes_by_name("mintty") == [101, 102]
    assert _win_proc.find_processes_by_name("Mintty.Exe") == [101, 102]
    assert _win_proc.find_processes_by_name("notepad") == [103]
    assert _win_proc.find_processes_by_name("notepad.exe") == [103]


def test_find_processes_by_name_does_not_match_prefix(monkeypatch):
    """仅前缀相同的名字不算命中（``note`` 不命中 ``notepad.exe``）。"""
    monkeypatch.setattr(
        _win_proc, "list_processes",
        lambda: [(201, 1, "notepad.exe")],
    )
    assert _win_proc.find_processes_by_name("note") == []
    assert _win_proc.find_processes_by_name("") == []
    assert _win_proc.find_processes_by_name("   ") == []


def test_exe_stem_normalizes_suffix():
    """归一化只剥 ``.exe``，保留其余字符（含路径 basename 已由调用方处理）。"""
    assert _win_proc._exe_stem("Mintty.EXE") == "mintty"
    assert _win_proc._exe_stem("python3") == "python3"
    assert _win_proc._exe_stem("") == ""


# ── 2. PTY 分支建立并透传 Windows Job ─────────────────────

@pytest.mark.asyncio
@pytest.mark.skipif(
    not BashFunc._is_pty_available(), reason="PTY 不可用（如原生 Windows）",
)
async def test_run_pty_creates_job_and_passes_to_ready(monkeypatch):
    """PTY 子进程创建后建立 Job，并作为 ``job_handle`` 交给 on_ready。"""
    monkeypatch.setattr(bash_mod, "_WINDOWS_LIKE", True)
    created: list[int] = []

    def _create_job(pid):
        created.append(pid)
        return 4242

    monkeypatch.setattr(bash_mod, "create_process_job", _create_job)
    captured: dict = {}

    def _ready(process, mode, master_fd=None, stdin_writer=None,
               job_handle=None):
        captured.update(mode=mode, job=job_handle, pid=process.pid,
                        has_master=master_fd is not None)

    result = await BashFunc("echo pty-job-ok")._run_pty(
        interactive=True, interactive_ready_fn=_ready,
    )

    assert result["returncode"] == 0
    assert "pty-job-ok" in result["stdout"]
    assert captured["mode"] == "pty"
    assert captured["job"] == 4242
    assert captured["has_master"] is True
    assert created == [captured["pid"]]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not BashFunc._is_pty_available(), reason="PTY 不可用（如原生 Windows）",
)
async def test_run_pty_without_callback_skips_job(monkeypatch):
    """无 on_ready 接管时**不**创建 Job（避免无人关闭的句柄泄漏）。"""
    monkeypatch.setattr(bash_mod, "_WINDOWS_LIKE", True)
    monkeypatch.setattr(
        bash_mod, "create_process_job",
        lambda pid: pytest.fail("无回调时不应创建 Job"),
    )
    result = await BashFunc("echo no-job")._run_pty()
    assert result["returncode"] == 0


# ── 3. Job 活跃时不做 pid 复用拒绝（Cygwin exec 重建场景） ──

def test_kill_process_tree_skips_reuse_guard_when_job_active(monkeypatch):
    """Job 内仍有活动成员 → Job 是任务权威归属，即使创建时间「变了」也照杀。

    这正是 Cygwin PTY 任务的场景：exec 让创建时间发生变化，但进程仍在任务
    Job 内，不能因此拒绝杀树（否则后台任务永远杀不掉）。
    """
    killed: list[int] = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_is_cygwin_like", lambda: False)
    monkeypatch.setattr(support, "_pid_reused", lambda pid, ct: True)
    monkeypatch.setattr(support, "job_active_processes", lambda handle: 1)
    monkeypatch.setattr(support, "terminate_process_job", lambda handle: True)
    monkeypatch.setattr(support, "_wait_job_empty", lambda handle, timeout=0: 0)
    monkeypatch.setattr(support, "_wait_until_exit", lambda pids, timeout=0: [])
    monkeypatch.setattr(
        support, "_kill_tree_once",
        lambda root, collected=None, extra_pids=None, **kwargs:
        killed.append(root) or [root],
    )

    result = kill_process_tree(4321, create_time=999, job_handle=555)

    assert killed == [4321]
    assert result.pid_reused is False
    assert result.success is True


def test_kill_process_tree_rejects_reuse_without_active_job(monkeypatch):
    """无 Job（或 Job 内已无活动成员）时，复用防护仍然生效。"""
    killed: list[int] = []
    monkeypatch.setattr(support, "_is_windows", lambda: True)
    monkeypatch.setattr(support, "_is_cygwin_like", lambda: False)
    monkeypatch.setattr(support, "_pid_reused", lambda pid, ct: True)
    monkeypatch.setattr(support, "job_active_processes", lambda handle: 0)
    monkeypatch.setattr(support, "terminate_process_job", lambda handle: True)
    monkeypatch.setattr(support, "_wait_job_empty", lambda handle, timeout=0: 0)
    monkeypatch.setattr(
        support, "_kill_tree_once",
        lambda root, collected=None, extra_pids=None, **kwargs:
        killed.append(root) or [root],
    )

    result = kill_process_tree(4322, create_time=999, job_handle=556)

    assert killed == []
    assert result.pid_reused is True
    assert result.success is False
