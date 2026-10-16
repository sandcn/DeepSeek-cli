"""进程树收集的平台策略：慢速 ``/proc`` 平台优先用 ``ps``（ESC 中断响应修复）。

★ 背景（用户 bug：按多次 ESC 才能中断正在运行的函数）：
Cygwin / MSYS2 的 ``/proc`` 由运行时按需模拟，逐个读取
``/proc/<pid>/status`` 需数百 ms（实测 Cygwin 约 0.17s/进程，整表 6~8s），
而 ``ps`` 一次仅约 0.17s。进程树收集（``_children_map``）位于 **ESC 中断
同步快路径** 上——bash 工具 ``_read_loop`` 检测到中断后 **同步** 调用
``kill_fn`` → ``_kill_process_tree`` → ``_collect_descendants`` →
``_children_map``，6~8s 的扫描让「按一次 ESC」看起来毫无反应（事件循环
与中断收尾被卡住数秒），用户误以为要反复按 ESC。

修复（src/tools/_bash_support.py）：
  - ``_prefer_ps_process_tree()``：``sys.platform`` 以 cygwin / msys 开头
    时返回 True（此类平台 ``/proc`` 为慢速模拟层）；
  - ``_children_map()``：该类平台直接走 ``ps``（单次约 0.17s），不再逐进程
    读 ``/proc/<pid>/status``。

（``_pid_state`` 的单进程 ``/proc`` 读取刻意保留：那是**单 PID** 查询而非
整表枚举，且 ``kill_process_tree`` 的存活校验靠它识别僵尸进程——Cygwin 的
``ps`` 不支持 ``state`` 列；中断快路径不做校验，不受其影响。）
"""

from __future__ import annotations

import builtins
import io

import pytest

from src.tools import _bash_support as bs


# ── 1. 平台判定 ─────────────────────────────────────────

@pytest.mark.parametrize("platform", ["cygwin", "msys", "cygwin2", "msys3"])
def test_prefer_ps_process_tree_true_on_slow_proc_platforms(monkeypatch, platform):
    """cygwin / msys 前缀平台 → True（/proc 为慢速模拟层）。"""
    monkeypatch.setattr(bs.sys, "platform", platform)
    assert bs._prefer_ps_process_tree() is True


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd12", "win32", "aix"])
def test_prefer_ps_process_tree_false_on_fast_platforms(monkeypatch, platform):
    """其它平台 → False（保持 /proc 优先；Linux 上 /proc 单次遍历最快）。"""
    monkeypatch.setattr(bs.sys, "platform", platform)
    assert bs._prefer_ps_process_tree() is False


# ── 2. _children_map 平台策略（核心修复） ───────────────

def test_children_map_uses_ps_on_cygwin_and_never_reads_proc(monkeypatch):
    """Cygwin 上 ``_children_map`` 直接走 ps，绝不读每进程 ``/proc`` status。

    性能红线：逐进程读 ``/proc/<pid>/status`` 是本 bug 的根因（6~8s）。
    """
    monkeypatch.setattr(bs.sys, "platform", "cygwin")
    sentinel = {1: [2, 3]}
    monkeypatch.setattr(bs, "_children_map_from_ps", lambda: sentinel)
    monkeypatch.setattr(bs.os.path, "isdir", lambda _p: True)
    monkeypatch.setattr(bs.os, "listdir", _raise_marker("不得枚举 /proc"))
    monkeypatch.setattr(bs, "_read_ppid", _raise_marker("不得读取 /proc/<pid>/status"))

    assert bs._children_map() is sentinel


def test_children_map_uses_proc_on_linux(monkeypatch):
    """Linux 上保持 /proc 优先（单次遍历 O(N)），且不调用 ps。"""
    monkeypatch.setattr(bs.sys, "platform", "linux")
    monkeypatch.setattr(bs.os.path, "isdir", lambda p: p == "/proc")
    monkeypatch.setattr(bs.os, "listdir", lambda _p: ["1", "2", "3", "self", "uptime"])
    ppid_map = {1: 0, 2: 1, 3: 2}
    monkeypatch.setattr(bs, "_read_ppid", lambda pid: ppid_map.get(pid))
    monkeypatch.setattr(bs, "_children_map_from_ps", _raise_marker("Linux 上不应走 ps"))

    mapping = bs._children_map()
    assert mapping.get(1) == [2]
    assert mapping.get(2) == [3]


def test_children_map_falls_back_to_ps_when_proc_absent(monkeypatch):
    """``/proc`` 不可用（macOS / BSD）→ 回退 ps。"""
    monkeypatch.setattr(bs.sys, "platform", "darwin")
    monkeypatch.setattr(bs.os.path, "isdir", lambda _p: False)
    sentinel = {7: [8]}
    monkeypatch.setattr(bs, "_children_map_from_ps", lambda: sentinel)

    assert bs._children_map() is sentinel


# ── 3. _pid_state（单进程查询，保留 /proc，保住僵尸判定） ──

def test_pid_state_reads_proc_state_line(monkeypatch):
    """读 ``/proc/<pid>/status`` 的 ``State`` 行首字符（僵尸判定依赖它）。"""
    monkeypatch.setattr(bs.os.path, "isdir", lambda _p: True)
    status = "Name:\tfoo\nState:\tZ (zombie)\nPPid:\t1\n"
    monkeypatch.setattr(builtins, "open", lambda path, *a, **k: io.StringIO(status))

    assert bs._pid_state(42) == "Z"


# ── 4. 用户场景回归：中断快路径不读每进程 /proc ─────────

def test_kill_fast_path_avoids_per_process_proc_on_cygwin(monkeypatch):
    """ESC 中断同步快路径 ``_kill_process_tree`` 在 Cygwin 上不读每进程 /proc。

    回归防护：修复前该快路径在 Cygwin 上逐进程读 ``/proc/<pid>/status``
    （6~8s 阻塞事件循环），按一次 ESC 数秒无响应。
    """
    monkeypatch.setattr(bs.sys, "platform", "cygwin")
    monkeypatch.setattr(bs, "_children_map_from_ps", lambda: {})
    read_calls: list = []
    monkeypatch.setattr(bs, "_read_ppid", lambda pid: read_calls.append(pid))
    monkeypatch.setattr(bs, "_kill_pid", lambda _pid: None)
    monkeypatch.setattr(bs, "_kill_process_group", lambda _pid: None)

    bs._kill_process_tree(999999)

    assert read_calls == []


# ── 辅助 ────────────────────────────────────────────────

def _raise_marker(message: str):
    """构造「一旦被调用即失败」的打桩函数（用于证明某路径未被走到）。"""
    def _boom(*_a, **_k):
        raise AssertionError(message)
    return _boom
