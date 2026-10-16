"""Cygwin / MSYS2 进程树收集策略（``_screenshot/proctree.py``）测试。

★ 背景（用户问题：``bash_opt`` 操作 GUI 时卡 10 秒左右才动）：
Cygwin 的 ``/proc`` 由运行时按需模拟，``os.listdir`` 很快，但逐个读取
``/proc/<pid>/status`` 需数百毫秒（实测单进程约 0.2s、整表 3~4s）。窗口定位
（``resolve_window_pids``）**每次**截图 / 输入注入都要调用，且输入注入在
「前台确认」路径上还会重复调用多次——于是「点一次按钮」平白多花约 10 秒。

修复：Cygwin / MSYS2 改走 ``ps`` 取 POSIX 父子关系（单次约 0.12s），不再逐
进程读 ``status``；``ps`` 的命令形态复用 ``_bash_support._children_map_from_ps``
（Cygwin 的 procps 只支持 ``ps -ef`` / ``ps ax``，该平台差异已在那里验证过）。

本测试固化该策略（避免回归到慢通道）：
  1. Cygwin 上 ``_children_map`` 走 ``ps`` 通道，绝不读 ``/proc/<pid>/status``；
  2. ``ps`` 输出为空（不可用）时返回 ``None``，由上层回退（保正确性）；
  3. 非 Cygwin 平台不受影响（Linux 仍走 ``/proc``，不碰 Cygwin 通道）；
  4. 真实 Cygwin 环境下仍能正确收集进程树与真实子进程。
"""

from __future__ import annotations

import os

import pytest

from src.tools._screenshot import proctree, winapi


def _raise_marker(message: str):
    """构造「一旦被调用即失败」的打桩函数（用于证明某路径未被走到）。"""
    def _boom(*_a, **_k):  # pragma: no cover - 只应在回归时触发
        raise AssertionError(message)
    return _boom


# ── 1. Cygwin 通道：复用 ps（多命令形态） ────────────────

def test_cygwin_channel_reuses_ps_children_map(monkeypatch):
    """Cygwin 通道直接复用 ``_bash_support`` 的 ps 解析结果。"""
    sentinel = {1: [10], 10: [11]}
    monkeypatch.setattr(proctree, "_children_map_from_ps", lambda: sentinel)

    assert proctree._cygwin_children_map() == sentinel


def test_cygwin_channel_returns_none_when_ps_empty(monkeypatch):
    """``ps`` 不可用（解析结果为空）→ ``None``（上层回退其它通道）。"""
    monkeypatch.setattr(proctree, "_children_map_from_ps", lambda: {})

    assert proctree._cygwin_children_map() is None


# ── 2. _children_map 平台策略（核心修复） ───────────────

def test_children_map_prefers_cygwin_channel(monkeypatch):
    """Cygwin 上走 ps 通道，且绝不读 ``/proc/<pid>/status``。"""
    sentinel = {1: [2]}
    monkeypatch.setattr(proctree.winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(proctree, "_cygwin_children_map", lambda: sentinel)
    monkeypatch.setattr(proctree, "_proc_children_map",
                        _raise_marker("Cygwin 上不得扫描 /proc/<pid>/status"))
    monkeypatch.setattr(proctree, "_read_proc_ppid",
                        _raise_marker("Cygwin 上不得读 /proc/<pid>/status"))

    assert proctree._children_map() is sentinel


def test_children_map_falls_back_to_windows_table_when_ps_unavailable(monkeypatch):
    """ps 通道不可用且 ``/proc`` 不可读时，回退 Windows 进程表。"""
    sentinel = {5: [6]}
    monkeypatch.setattr(proctree.winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(proctree, "_cygwin_children_map", lambda: None)
    monkeypatch.setattr(proctree.os.path, "isdir", lambda _p: False)
    monkeypatch.setattr(proctree.winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(proctree, "_windows_children_map", lambda: sentinel)

    assert proctree._children_map() is sentinel


def test_children_map_cygwin_falls_back_to_proc_when_all_else_fails(monkeypatch):
    """ps 通道不可用但 ``/proc`` 可读时，仍保留 ``/proc`` 兜底（保正确性）。"""
    sentinel = {7: [8]}
    monkeypatch.setattr(proctree.winapi, "is_cygwin_like", lambda: True)
    monkeypatch.setattr(proctree, "_cygwin_children_map", lambda: None)
    monkeypatch.setattr(proctree.os.path, "isdir", lambda p: p == "/proc")
    monkeypatch.setattr(proctree.os.path, "exists", lambda p: p == "/proc/self/status")
    monkeypatch.setattr(proctree, "_proc_children_map", lambda: sentinel)

    assert proctree._children_map() is sentinel


def test_children_map_linux_keeps_proc_scan(monkeypatch):
    """非 Cygwin 平台（Linux）不受影响：仍走 ``/proc``，不碰 Cygwin 通道。"""
    monkeypatch.setattr(proctree.winapi, "is_cygwin_like", lambda: False)
    monkeypatch.setattr(proctree, "_cygwin_children_map",
                        _raise_marker("Linux 上不应走 Cygwin 通道"))
    monkeypatch.setattr(proctree.os.path, "isdir", lambda p: p == "/proc")
    monkeypatch.setattr(proctree.os.path, "exists", lambda p: p == "/proc/self/status")
    monkeypatch.setattr(proctree.os, "listdir",
                        lambda _p: ["1", "2", "3", "self", "uptime"])
    ppids = {1: 0, 2: 1, 3: 2}
    monkeypatch.setattr(proctree, "_read_proc_ppid", lambda pid: ppids.get(int(pid)))

    mapping = proctree._children_map()
    assert mapping is not None
    assert mapping.get(1) == [2]
    assert mapping.get(2) == [3]


# ── 3. 上层行为：collect_process_tree 展开 ──────────────

def test_collect_process_tree_expands_children_map(monkeypatch):
    """``collect_process_tree`` 用上面的映射展开全部后代。"""
    monkeypatch.setattr(proctree, "_children_map",
                        lambda: {1: [2, 3], 3: [4]})

    assert sorted(proctree.collect_process_tree(1)) == [1, 2, 3, 4]


def test_collect_process_tree_keeps_root_when_map_unavailable(monkeypatch):
    """映射不可用（``None``）时仍返回根，避免上层误判「无进程」。"""
    monkeypatch.setattr(proctree, "_children_map", lambda: None)

    assert proctree.collect_process_tree(42) == [42]


# ── 4. 真实环境回归（本机为 Cygwin 时生效） ─────────────

def test_real_cygwin_env_never_reads_proc_status(monkeypatch):
    """真实 Cygwin 环境下：进程树收集不读任何 ``/proc/<pid>/status``。

    这是用户问题的性能红线——修复前每次窗口定位都要逐进程读该文件
    （整表 3~4s），一次 GUI 操作累计约 10 秒。
    """
    if not winapi.is_cygwin_like():
        pytest.skip("仅在 Cygwin / MSYS2 环境验证")
    if proctree._cygwin_children_map() is None:
        pytest.skip("该环境 ps 不可用，走回退通道")
    monkeypatch.setattr(proctree, "_read_proc_ppid",
                        _raise_marker("Cygwin 上不得读 /proc/<pid>/status"))
    monkeypatch.setattr(proctree, "_proc_children_map",
                        _raise_marker("Cygwin 上不得扫描 /proc/<pid>/status"))

    tree = proctree.collect_process_tree(os.getpid())

    assert tree[0] == os.getpid()


def test_real_cygwin_env_finds_child_process(monkeypatch):
    """真实 Cygwin 环境下仍能找到真实子进程（通道正确性验证）。"""
    if not winapi.is_cygwin_like():
        pytest.skip("仅在 Cygwin / MSYS2 环境验证")
    if proctree._cygwin_children_map() is None:
        pytest.skip("该环境 ps 不可用，走回退通道")
    import subprocess
    import sys
    import time

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    try:
        deadline = time.monotonic() + 5
        descendants = proctree.collect_process_tree(os.getpid())
        while child.pid not in descendants and time.monotonic() < deadline:
            time.sleep(0.1)
            descendants = proctree.collect_process_tree(os.getpid())
        assert child.pid in descendants, f"子进程 {child.pid} 未出现在 {descendants}"
    finally:
        child.kill()
        child.wait()
