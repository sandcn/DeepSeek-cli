"""InkSession 队列细节回归：_pop_starved_state_cmd 弹出后须通知 not_full。

P3（review 修复）：``_pop_starved_state_cmd`` 在 mutex 内私有弹出队列元素
（不经 ``get()``）→ 必须自行 ``not_full.notify_all()``，否则阻塞在
``put(block=True)`` 的调用方会空等到超时。
"""

from __future__ import annotations

import io
import queue
import threading
import time
from types import SimpleNamespace

from src.tui._const import RenderCommand
from src.tui.ink.session import InkSession
from src.tui.ink import hooks as H

import pytest


@pytest.fixture(autouse=True)
def _isolate_hook_globals():
    """保存/还原 hooks 全局状态（InkSession.__init__ 会写入全局注入）。"""
    saved = {
        "_input_router_callback": H._input_router_callback,
        "_app_control": H._app_control,
        "_stdin_accessor": H._stdin_accessor,
        "_stdout_accessor": H._stdout_accessor,
        "_stderr_accessor": H._stderr_accessor,
    }
    yield
    for name, value in saved.items():
        setattr(H, name, value)


def _make_session():
    return InkSession(model=SimpleNamespace(), stream=io.StringIO())


def test_pop_starved_state_cmd_notifies_not_full():
    session = _make_session()
    session._cmd_queue = queue.PriorityQueue(maxsize=1)

    class _Coalesce:
        cid = RenderCommand.PARSE_INFO

    session._cmd_queue.put_nowait((99, 1, _Coalesce()))

    done = threading.Event()

    def _blocked_producer():
        session._cmd_queue.put((1, 2, "content"), block=True, timeout=2.0)
        done.set()

    t = threading.Thread(target=_blocked_producer, daemon=True)
    t.start()
    time.sleep(0.05)
    assert not done.is_set()  # 队列已满，生产者阻塞

    victim = session._pop_starved_state_cmd(0)
    assert victim is not None
    assert done.wait(timeout=2.0)  # notify_all 唤醒生产者


def test_pop_starved_state_cmd_returns_none_when_nothing_starved():
    session = _make_session()
    session._cmd_queue = queue.PriorityQueue(maxsize=4)
    assert session._pop_starved_state_cmd(0) is None
