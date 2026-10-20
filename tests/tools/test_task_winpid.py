"""后台任务 Windows PID / Job 登记表（``_task_winpid``）单元测试。"""

from __future__ import annotations

import pytest

from src.tools import _task_winpid


@pytest.fixture(autouse=True)
def _clean_registry():
    _task_winpid.clear()
    yield
    _task_winpid.clear()


def test_register_and_lookup():
    _task_winpid.register(123, 456, job=789, hint="Notepad.exe", start_time=1000)
    assert _task_winpid.winpid_for(123) == 456
    assert _task_winpid.job_for(123) == 789
    assert _task_winpid.hint_for(123) == "notepad.exe"
    assert _task_winpid.start_time_for(123) == 1000
    assert _task_winpid.winpid_for(999) is None


def test_register_ignores_invalid_pid():
    _task_winpid.register(0, 456)
    _task_winpid.register(-1, 456)
    _task_winpid.register("bad", 456)  # type: ignore[arg-type]
    assert _task_winpid.winpid_for(0) is None
    assert _task_winpid.winpid_for(-1) is None


def test_unregister_removes_everything():
    _task_winpid.register(123, 456, job=789, hint="app.exe", start_time=1000)
    _task_winpid.unregister(123)
    assert _task_winpid.winpid_for(123) is None
    assert _task_winpid.job_for(123) is None
    assert _task_winpid.hint_for(123) is None
    assert _task_winpid.start_time_for(123) is None


def test_job_processes_empty_without_job():
    assert _task_winpid.job_processes(None) == []
    assert _task_winpid.job_processes(0) == []


def test_now_filetime_is_positive_and_monotonic():
    first = _task_winpid.now_filetime()
    second = _task_winpid.now_filetime()
    assert first > 0
    assert second >= first
