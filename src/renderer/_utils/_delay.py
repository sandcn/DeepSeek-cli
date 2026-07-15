"""跨平台高精度延时。"""

from __future__ import annotations

import sys
import time

_PLATFORM = sys.platform
_IS_LINUX = _PLATFORM in ('linux', 'android')


def _precise_delay(seconds: float) -> None:
    """跨平台延时，避免 CPU 忙等。

    - Linux/Android：``time.sleep`` 精度 ~1ms，直接使用。
    - 其他平台（Cygwin/Windows/macOS）：``time.sleep(0)`` 仅让出
      CPU 时间片，由 render 线程自然帧率驱动打字机节奏。

    移除了原先 Windows/macOS 上的忙等循环（``while perf_counter < deadline: pass``），
    消除 100% CPU 占用。打字机效果由渲染帧率自然控制。
    """
    if seconds <= 0:
        return
    if _IS_LINUX:
        time.sleep(seconds)
    else:
        # 仅让出 CPU，不忙等 — 下一帧自然推进
        time.sleep(0)
