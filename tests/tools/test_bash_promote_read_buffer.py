"""bash：自动转后台时把「转后台前输出」并入 read_buffer（回归测试）。

背景（实机复现）：前台命令超过 60 秒自动转后台，``op=read`` 只看到转后台
之后的输出，启动阶段的 print（"start"）看不到，``op=wait`` 才有完整
``start\\nend``——模型容易误判「命令没输出 / 卡住」。

修复：行回调代理在转后台前把已产生的行记入 ``line_cb["pre"]``（受
``_READ_BUFFER_MAX_CHARS`` 限制），转后台时一次性写入 ``read_buffer``。
"""

from __future__ import annotations

import asyncio
import json

from src.tools import bash as bash_module
from src.tools.bash import (
    BashFunc,
    _READ_BUFFER_MAX_CHARS,
    _remember_pre_background_line,
    _take_pre_background_output,
)


class _FakeAgent:
    def __init__(self):
        self._background_tasks = {}

    def _register_background_task(self, task_id, rec):
        self._background_tasks[task_id] = rec

    def _complete_background_task(self, task_id, result, status="completed"):
        rec = self._background_tasks.get(task_id)
        if rec is not None:
            rec.update(done=True, status=status, result=result)


def _line_cb(pre=None):
    return {"fn": None, "pre": list(pre or []),
            "pre_len": sum(len(item) for item in (pre or []))}


def test_remember_pre_background_line_keeps_tail_under_limit():
    line_cb = _line_cb()
    first = "a" * (3 * _READ_BUFFER_MAX_CHARS // 4)
    second = "b" * (3 * _READ_BUFFER_MAX_CHARS // 4)
    _remember_pre_background_line(line_cb, first)
    _remember_pre_background_line(line_cb, second)
    text = _take_pre_background_output(line_cb)
    # 超限丢弃最旧的行，只保留最新内容
    assert text == second
    assert len(text) <= _READ_BUFFER_MAX_CHARS
    # 取走后缓存清空（转后台后不再重复写入）
    assert _take_pre_background_output(line_cb) == ""


def test_remember_pre_background_line_no_cache_is_noop():
    _remember_pre_background_line({}, "ignored\n")  # 不抛异常


async def test_promote_to_background_includes_preexisting_output(monkeypatch):
    async def _noop_print(*_args, **_kwargs):
        return None

    async def _noop_sample(_self, _agent, _task_id):
        return None

    monkeypatch.setattr(bash_module, "print_to_terminal", _noop_print)
    monkeypatch.setattr(BashFunc, "_sample_process_tree", _noop_sample)

    agent = _FakeAgent()
    func = BashFunc(command="long-running")
    func.set_agent(agent)
    task = asyncio.ensure_future(asyncio.sleep(0.01))
    holder = {"process": None, "mode": "pipe", "job_handle": None}
    line_cb = _line_cb(["start\n", "tick 0\n"])

    result = await func._promote_to_background(task, holder, line_cb)
    payload = json.loads(result)
    assert payload["status"] == "running"

    rec = agent._background_tasks[payload["task_id"]]
    # 转后台前的输出已并入 read_buffer（op=read 可见），且缓存已清空
    assert rec["read_buffer"] == "start\ntick 0\n"
    assert line_cb["pre"] == []
