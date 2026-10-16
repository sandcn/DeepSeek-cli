"""``src.api._stream_lifecycle`` 兼容 re-export 层测试。

背景：该模块原为 ``core.stats._stream_lifecycle`` 的**整份副本**（两份实现
逐字节相同）——核心层已下沉实现，api 侧遗留副本会随修改漂移（真源不唯一）。
收敛为 re-export 兼容层后，本文件固化：

  1. api 路径与 core 路径指向同一函数对象（真源唯一，副本漂移不可能）；
  2. 经 api 路径注册的回调能被 core 路径的 ``_notify_*`` 触发（嵌套深度语义：
     仅最外层 0→1 触发 start、1→0 触发 end）；
  3. 回调异常被隔离（不影响调用方）。
"""

from __future__ import annotations

import pytest

from src.core.stats import _stream_lifecycle as core_mod


@pytest.fixture(autouse=True)
def _reset_lifecycle():
    core_mod.set_stream_lifecycle_callbacks()
    core_mod._stream_depth = 0
    yield
    core_mod.set_stream_lifecycle_callbacks()
    core_mod._stream_depth = 0


def test_api_module_is_alias_of_core_module():
    """api 兼容层的符号与 core 真源是同一对象（禁止双份实现）。"""
    from src.api import _stream_lifecycle as api_mod

    assert api_mod.set_stream_lifecycle_callbacks is core_mod.set_stream_lifecycle_callbacks
    assert api_mod._notify_stream_started is core_mod._notify_stream_started
    assert api_mod._notify_stream_ended is core_mod._notify_stream_ended
    assert api_mod._notify_stream_progress is core_mod._notify_stream_progress


def test_nested_depth_triggers_outermost_only():
    """经 api 路径注册回调：嵌套流式只在最外层触发 start/end。"""
    from src.api._stream_lifecycle import (
        _notify_stream_ended,
        _notify_stream_progress,
        _notify_stream_started,
        set_stream_lifecycle_callbacks,
    )

    events: list[str] = []
    set_stream_lifecycle_callbacks(
        start_cb=lambda: events.append("start"),
        end_cb=lambda: events.append("end"),
        progress_cb=lambda: events.append("progress"),
    )

    _notify_stream_started()   # 深度 0 → 1：触发 start
    _notify_stream_started()   # 嵌套，不触发
    _notify_stream_progress()  # 周期性进度
    _notify_stream_ended()     # 深度 2 → 1：不触发
    _notify_stream_ended()     # 深度 1 → 0：触发 end

    assert events == ["start", "progress", "end"]
    assert core_mod._stream_depth == 0


def test_callback_exceptions_are_isolated():
    """回调抛异常被吞掉（不向调用方传播）。"""
    from src.api._stream_lifecycle import (
        _notify_stream_ended,
        _notify_stream_progress,
        _notify_stream_started,
        set_stream_lifecycle_callbacks,
    )

    def _boom():
        raise RuntimeError("boom")

    set_stream_lifecycle_callbacks(start_cb=_boom, end_cb=_boom, progress_cb=_boom)

    _notify_stream_started()
    _notify_stream_progress()
    _notify_stream_ended()

    assert core_mod._stream_depth == 0


def test_set_callbacks_without_args_clears():
    """无参调用清空回调（stop/teardown 路径）。"""
    from src.api._stream_lifecycle import (
        _notify_stream_started,
        set_stream_lifecycle_callbacks,
    )

    events: list[str] = []
    set_stream_lifecycle_callbacks(start_cb=lambda: events.append("start"))
    set_stream_lifecycle_callbacks()
    _notify_stream_started()

    assert events == []
