"""tests/conftest — pytest 公共测试 helper。

提供：
- wait_until：轮询等待条件成立（复用 test_event_bus.py 内联轮询逻辑）
- wait_pipe_readable：轮询等待 pipe 可读（复用 test_input.py _wait_pipe_readable 实现）

供 tests/ 下各测试模块复用（test_input.py / test_event_bus.py 等），
消除各文件重复的内联轮询代码。
"""

from __future__ import annotations

import select
import time
from typing import Callable

import pytest


def wait_until(condition: Callable[[], bool], timeout: float = 3.0, interval: float = 0.05) -> bool:
    """轮询等待 condition() 返回 True，超时返回 False。

    复用 test_event_bus.py 的 deadline 轮询模式：
    ``deadline = time.monotonic() + timeout``，循环检查 + time.sleep(interval)。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def wait_pipe_readable(pipe_fd: int, timeout: float = 2.0) -> bool:
    """轮询等待 pipe 数据就绪（消除固定 sleep 时序依赖）。

    写入端关闭（EOF）时 select 同样视为可读，立即返回 True。
    复用 test_input.py ``_wait_pipe_readable`` 原实现：select + 0.02s 轮询。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([pipe_fd], [], [], 0.02)
        if ready:
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def wait_until_fixture():
    """wait_until 的 fixture 包装（供注入式使用，函数 import 不可用时回退）。"""
    return wait_until


@pytest.fixture
def wait_pipe_readable_fixture():
    """wait_pipe_readable 的 fixture 包装（供注入式使用，函数 import 不可用时回退）。"""
    return wait_pipe_readable
