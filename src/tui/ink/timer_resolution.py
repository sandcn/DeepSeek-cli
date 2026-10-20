"""高分辨率定时器请求（P2-2 从 InkSession 抽取，避免 session↔render_loop 环）。

Windows/Cygwin 默认定时器粒度约 15.6ms，``Event.wait(16.67ms)`` 实测会睡
~31ms；``timeBeginPeriod(1)`` 把粒度降到 ~1ms，使 60Hz 节拍可达。

按**线程标识**引用计数：渲染线程进入循环时申请、退出时释放；同进程多个
渲染线程/会话只申请一次，最后一个线程退出时恢复系统默认分辨率。
"""

from __future__ import annotations

import logging
import sys
import threading

_logger = logging.getLogger(__name__)

_high_res_timer_lock = threading.Lock()
_high_res_timer_owners: set[int] = set()
_high_res_timer_lib = None


def supports_high_resolution_timer() -> bool:
    """当前平台是否支持 ``timeBeginPeriod``（Windows 原生 / Cygwin）。"""
    return sys.platform == "win32" or sys.platform.startswith("cygwin")


def acquire_high_resolution_timer() -> None:
    """为当前渲染线程申请 1ms 系统定时器分辨率（幂等，按线程标识）。"""
    if not supports_high_resolution_timer():
        return
    tid = threading.get_ident()
    global _high_res_timer_lib
    with _high_res_timer_lock:
        if tid in _high_res_timer_owners:
            return
        if not _high_res_timer_owners:
            try:
                import ctypes

                lib = ctypes.CDLL("winmm.dll")
                lib.timeBeginPeriod(1)
                _high_res_timer_lib = lib
            except Exception:
                _logger.debug(
                    "申请 1ms 定时器分辨率失败（回退系统默认节拍）", exc_info=True,
                )
                _high_res_timer_lib = None
                return
        _high_res_timer_owners.add(tid)


def release_high_resolution_timer() -> None:
    """释放当前渲染线程申请的定时器分辨率（幂等；最后一个线程恢复默认）。"""
    if not supports_high_resolution_timer():
        return
    tid = threading.get_ident()
    global _high_res_timer_lib
    with _high_res_timer_lock:
        if tid not in _high_res_timer_owners:
            return
        _high_res_timer_owners.discard(tid)
        if not _high_res_timer_owners and _high_res_timer_lib is not None:
            try:
                _high_res_timer_lib.timeEndPeriod(1)
            except Exception:
                _logger.debug("恢复系统定时器分辨率异常", exc_info=True)
            _high_res_timer_lib = None


__all__ = [
    "supports_high_resolution_timer",
    "acquire_high_resolution_timer",
    "release_high_resolution_timer",
]
