"""bash_opt 输入后「等待界面变化 / 稳定」的自适应轮询测试（性能增强）。

轮询间隔从 _WAIT_FOR_INTERVAL 起按 _WAIT_FOR_BACKOFF 放大、封顶
_WAIT_FOR_MAX_INTERVAL，避免长时间等待时高频截图。
"""

from __future__ import annotations

import pytest

from src.tools import bash_opt as bash_opt_module
from src.tools.bash_opt import BashOptFunc


class _Stop(Exception):
    """用于在采样若干次后中断 _await_screen 循环。"""


async def test_wait_for_interval_backs_off(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_WAIT_FOR_INTERVAL", 0.1)
    monkeypatch.setattr(BashOptFunc, "_WAIT_FOR_BACKOFF", 2.0)
    monkeypatch.setattr(BashOptFunc, "_WAIT_FOR_MAX_INTERVAL", 0.4)

    intervals: list[float] = []

    async def fake_sleep(seconds):
        intervals.append(round(seconds, 6))
        if len(intervals) >= 5:
            raise _Stop()

    async def no_shot(self, pid, window):
        return None

    monkeypatch.setattr(bash_opt_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(BashOptFunc, "_temp_screenshot", no_shot)

    func = BashOptFunc(task_id="bg-1", op="move", x=1, y=1)
    with pytest.raises(_Stop):
        await func._await_screen(1, mode="change", before_path=None,
                                 window=None, timeout=100.0)
    assert intervals == [0.1, 0.2, 0.4, 0.4, 0.4]
