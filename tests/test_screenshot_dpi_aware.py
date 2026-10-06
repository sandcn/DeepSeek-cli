"""Windows 截图 DPI 感知修复的单元测试。

缺陷背景：非 DPI 感知进程在高 DPI 显示器（如 150%）上调用 ``GetWindowRect``
得到的是被系统虚拟化缩小的坐标（2560 物理像素的屏幕读成 1707），而
``PrintWindow`` / ``BitBlt`` 输出的始终是物理像素；按缩小尺寸创建内存 DC
只能容纳整窗左上角一部分，整窗截图的右下角被裁掉。

修复：截图前把进程标记为 DPI 感知（``winapi.ensure_process_dpi_aware``，
幂等、线程安全、多级 API 降级），使窗口几何与物理像素 1:1。

覆盖：
  - DPI 感知级别读取（非 Windows 返回 None）
  - 感知设置：已感知跳过、多级 API 降级、全部失败返回 False
  - 幂等缓存与线程安全入口
  - 后端截图前先确保 DPI 感知
  - 真实 Windows/Cygwin 环境下进程确实变为 DPI 感知
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import winapi
from src.tools._screenshot import win as win_mod
from src.tools._screenshot.result import NoWindowError


# ── 感知级别读取 ─────────────────────────────────────────

def test_process_dpi_awareness_none_on_non_windows(monkeypatch):
    """非 Windows 平台读不到感知级别（返回 None 而非抛异常）。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: False)
    assert winapi.process_dpi_awareness() is None


# ── 感知设置：分支与降级 ─────────────────────────────────

def test_apply_dpi_awareness_false_on_non_windows(monkeypatch):
    """非 Windows 平台不尝试设置（返回 False）。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: False)
    called = []
    monkeypatch.setattr(winapi, "_DPI_AWARE_SETTERS",
                        (lambda: called.append(True) or True,))
    assert winapi._apply_dpi_awareness() is False
    assert called == []


def test_apply_dpi_awareness_skips_setters_when_already_aware(monkeypatch):
    """进程已是 DPI 感知（其它模块设过）时直接复用，不重复调用 API。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        winapi, "process_dpi_awareness",
        lambda: winapi.PROCESS_DPI_AWARENESS_PER_MONITOR_AWARE,
    )
    called = []
    monkeypatch.setattr(winapi, "_DPI_AWARE_SETTERS",
                        (lambda: called.append(True) or True,))
    assert winapi._apply_dpi_awareness() is True
    assert called == []


def test_apply_dpi_awareness_system_aware_also_reused(monkeypatch):
    """系统级感知（1）同样视为已感知，不再尝试新接口。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        winapi, "process_dpi_awareness",
        lambda: winapi.PROCESS_DPI_AWARENESS_SYSTEM_AWARE,
    )
    called = []
    monkeypatch.setattr(winapi, "_DPI_AWARE_SETTERS",
                        (lambda: called.append(True) or True,))
    assert winapi._apply_dpi_awareness() is True
    assert called == []


def test_apply_dpi_awareness_falls_back_across_setters(monkeypatch):
    """前一个 API 失败/不可用时按顺序回退到下一个，直到成功。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "process_dpi_awareness", lambda: 0)
    calls: list[str] = []

    def failing():
        calls.append("fail")
        return False

    def raising():
        calls.append("raise")
        raise OSError("api unavailable")

    def succeeding():
        calls.append("ok")
        return True

    monkeypatch.setattr(winapi, "_DPI_AWARE_SETTERS",
                        (failing, raising, succeeding))
    assert winapi._apply_dpi_awareness() is True
    assert calls == ["fail", "raise", "ok"]


def test_apply_dpi_awareness_false_when_all_setters_fail(monkeypatch):
    """所有 API 均失败且进程仍未感知时返回 False（调用方可继续截图）。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "process_dpi_awareness", lambda: 0)
    monkeypatch.setattr(winapi, "_DPI_AWARE_SETTERS",
                        (lambda: False, lambda: False))
    assert winapi._apply_dpi_awareness() is False


def test_apply_dpi_awareness_trusts_final_probe(monkeypatch):
    """setter 报失败但最终探测到已感知时仍视为成功（跨 API 语义差异兜底）。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    probes = iter([0, winapi.PROCESS_DPI_AWARENESS_PER_MONITOR_AWARE])
    monkeypatch.setattr(winapi, "process_dpi_awareness", lambda: next(probes))
    monkeypatch.setattr(winapi, "_DPI_AWARE_SETTERS", (lambda: False,))
    assert winapi._apply_dpi_awareness() is True


def test_dpi_setter_order_prefers_per_monitor_over_system(monkeypatch):
    """降级顺序：每显示器上下文 → shcore 每显示器 → 系统级。"""
    assert winapi._DPI_AWARE_SETTERS[0] is winapi._set_dpi_awareness_context
    assert winapi._DPI_AWARE_SETTERS[1] is winapi._set_shcore_dpi_awareness
    assert winapi._DPI_AWARE_SETTERS[-1] is winapi._set_system_dpi_aware
    assert (winapi.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            == -4), "PER_MONITOR_AWARE_V2 上下文句柄值必须为 (HANDLE)-4"


# ── 幂等入口 ─────────────────────────────────────────────

def test_ensure_process_dpi_aware_idempotent(monkeypatch):
    """同一进程只真正设置一次（结果缓存），重复调用零开销。"""
    monkeypatch.setattr(winapi, "_DPI_AWARE", None)
    calls: list[int] = []
    monkeypatch.setattr(winapi, "_apply_dpi_awareness",
                        lambda: (calls.append(1), True)[1])
    assert winapi.ensure_process_dpi_aware() is True
    assert winapi.ensure_process_dpi_aware() is True
    assert winapi.ensure_process_dpi_aware() is True
    assert calls == [1]


def test_ensure_process_dpi_aware_caches_failure(monkeypatch):
    """设置失败同样缓存（避免每次截图重复尝试失败的 API）。"""
    monkeypatch.setattr(winapi, "_DPI_AWARE", None)
    calls: list[int] = []
    monkeypatch.setattr(winapi, "_apply_dpi_awareness",
                        lambda: (calls.append(1), False)[1])
    assert winapi.ensure_process_dpi_aware() is False
    assert winapi.ensure_process_dpi_aware() is False
    assert calls == [1]


# ── 后端接线：截图前先确保感知 ───────────────────────────

def test_capture_ensures_dpi_aware_before_window_lookup(monkeypatch, tmp_path):
    """后端在解析窗口几何之前先确保 DPI 感知（顺序不可颠倒）。"""
    calls: list[str] = []
    monkeypatch.setattr(
        winapi, "ensure_process_dpi_aware",
        lambda: (calls.append("dpi"), True)[1],
    )

    def _no_window(pid):
        calls.append("resolve")
        raise NoWindowError("目标无窗口")

    monkeypatch.setattr(win_mod, "resolve_window_pids", _no_window)
    backend = win_mod.WindowsBackend()
    with pytest.raises(NoWindowError):
        backend.capture(1234, str(tmp_path / "x.png"))
    assert calls == ["dpi", "resolve"]


# ── 真实环境 ─────────────────────────────────────────────

@pytest.mark.skipif(not winapi.is_windows_platform(), reason="仅 Windows/Cygwin")
def test_real_process_becomes_dpi_aware():
    """真实环境：设置成功后进程感知级别不再是 unaware。"""
    assert winapi.ensure_process_dpi_aware() is True
    level = winapi.process_dpi_awareness()
    assert level is None or level != winapi.PROCESS_DPI_AWARENESS_UNAWARE
    assert winapi.ensure_process_dpi_aware() is True
