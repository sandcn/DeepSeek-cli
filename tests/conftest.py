"""pytest 全局夹具。

★ 2026-08-20（全量测试稳定性）：确保每个测试开始前主线程存在可用事件循环。
Python 3.9 的 asyncio.run() 结束后会 set_event_loop(None)（_set_called 残留
True），同进程内再构造 asyncio 原语（Queue/Lock/Event 等）会经
get_event_loop() 抛 ``RuntimeError: There is no current event loop``——
xdist 多 worker 并行下偶发失败（取决于测试在 worker 内的分配顺序）。
本夹具在每个测试前修复该状态，消除整类偶发失败。

★ 覆盖率模式（``--cov``）下的性能预算用例：``coverage`` 的逐行插桩会让被
测代码慢数倍，所有**时间预算型**断言（用例如名含 ``budget`` / ``bounded``）
必然超时失败——这是测量方式的伪失败，与代码性能无关。CI 因此在两个步骤中
分别承担职责：完整套件（**不带**覆盖率）验证正确性与性能预算；覆盖率步骤
只统计覆盖面。本夹具在检测到 ``--cov`` 生效时跳过这些用例，避免把工具开销
误报为性能回归。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_main_event_loop():
    """主线程事件循环兜底：无循环时（asyncio.run 残留态）重建一个。"""
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield


def _coverage_instrumented(config) -> bool:
    """当前运行是否启用了覆盖率插桩（只看显式传入的 ``--cov``）。"""
    return bool(getattr(config.option, "cov_source", None))


#: 时间预算型用例命名特征（插桩后执行时间失真，见模块 docstring）
_TIMING_SENSITIVE_TOKENS = ("budget", "bounded", "_perf", "perf_")


@pytest.fixture(autouse=True)
def _skip_timing_budget_when_measuring_coverage(request):
    """覆盖率插桩模式下跳过时间预算型用例（性能预算由无插桩的完整套件验证）。"""
    if not _coverage_instrumented(request.config):
        yield
        return
    node_id = request.node.nodeid.lower()
    if any(token in node_id for token in _TIMING_SENSITIVE_TOKENS):
        pytest.skip("覆盖率插桩改变执行时间，时间预算用例在 --cov 模式下不适用")
    yield


def capturable_window_pids(limit: int = 5) -> list[int]:
    """当前桌面可截图的窗口 PID（过滤外壳/隐藏/幽灵窗口；非 Windows 返回空表）。

    供截图相关测试挑选真实目标；无桌面会话时返回空表，调用方据此 skip。
    """
    try:
        from src.tools._screenshot import winapi
    except Exception:  # pragma: no cover - 环境缺 ctypes 支持
        return []
    if not winapi.is_windows_platform():
        return []
    from src.tools._screenshot.win import SHELL_WINDOW_CLASSES

    pids: list[int] = []
    try:
        for hwnd in winapi.enum_children_windows():
            if not winapi.user32().IsWindowVisible(hwnd):
                continue
            left, top, right, bottom = winapi.window_rect(hwnd)
            if right - left <= 50 or bottom - top <= 50:
                continue
            if winapi.window_class(hwnd) in SHELL_WINDOW_CLASSES:
                continue
            if winapi.is_window_cloaked(hwnd):
                continue
            pid = winapi.window_pid(hwnd)
            if pid and pid not in pids:
                pids.append(pid)
            if len(pids) >= limit:
                break
    except OSError:  # pragma: no cover - 无桌面会话
        return pids
    return pids


@pytest.fixture
def real_window_pids() -> list[int]:
    """真实可截图窗口的 PID 列表（空表 = 无桌面窗口，用例应 skip）。"""
    return capturable_window_pids()
