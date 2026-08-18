"""bash 工具三元 JSON 返回结构测试（2026-08-18）。

返回给大模型的结构改为：{"stdout": ..., "stderr": ..., "returncode": N}
  - stdout / stderr 分离收集（前台统一 PIPE 分离模式）
  - returncode 是命令的退出码（bash $? 语义：被信号杀死为 128+sig）
  - 工具级错误（拒绝/取消/异常）→ stdout 空、stderr 放说明、returncode -1

同步覆盖：
  - _bash_rc 信号退出码规范化
  - _format_result / _error_result 组装与截断
  - 后台任务完成结果展开（bash_opt wait / _collect_done_background_messages）
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from src.tools.bash import BashFunc
from src.tools.bash_opt import BashOptFunc
from src.core.base_agent import _parse_bash_result_fields


def _loads(result: str) -> dict:
    return json.loads(result)


# ── 1. 前台执行：三元 JSON 结构 ────────────────────────

async def test_stdout_stderr_separated():
    """stdout 与 stderr 分离收集，不再合并。"""
    r = _loads(await BashFunc(command="echo out; echo err 1>&2").execute())
    assert r["stdout"] == "out"
    assert r["stderr"] == "err"
    assert r["returncode"] == 0


async def test_returncode_is_command_exit_code():
    """returncode 即命令退出码（bash $?）。"""
    r = _loads(await BashFunc(command="exit 42").execute())
    assert r["returncode"] == 42
    assert r["stdout"] == ""
    assert r["stderr"] == ""


async def test_returncode_pipeline_last_command():
    """管道退出码取最后一个命令（bash 默认，无 pipefail）。"""
    r = _loads(await BashFunc(command="sh -c 'exit 7' | cat").execute())
    assert r["returncode"] == 0


async def test_returncode_signal_killed_as_128_plus_sig():
    """子进程被信号杀死时 returncode 为 128+sig（$? 语义）。"""
    r = _loads(await BashFunc(command="kill -TERM $$").execute())
    assert r["returncode"] == 143  # 128 + 15
    r = _loads(await BashFunc(command="kill -9 $$").execute())
    assert r["returncode"] == 137  # 128 + 9


async def test_no_output_empty_strings_not_placeholder():
    """无输出时字段为空字符串（移除旧的 "(无输出)" 占位）。"""
    r = _loads(await BashFunc(command="true").execute())
    assert r == {"stdout": "", "stderr": "", "returncode": 0}


async def test_multiline_output_split_by_stream():
    """多行输出按流分别保留完整内容。"""
    r = _loads(await BashFunc(
        command="printf 'a\\nb\\n'; printf 'e1\\ne2\\n' 1>&2").execute())
    assert r["stdout"] == "a\nb"
    assert r["stderr"] == "e1\ne2"


async def test_display_path_returns_same_json():
    """display 路径（实时输出到终端）返回同结构三元 JSON。"""
    r = _loads(await BashFunc(command="printf 'a'; printf 'b' 1>&2").display())
    assert r == {"stdout": "a", "stderr": "b", "returncode": 0}


# ── 2. 工具级错误路径：三元 JSON（stderr 说明 + -1） ────

async def test_dangerous_command_rejected_as_json():
    """危险命令拒绝 → stdout 空、stderr 说明、returncode -1。"""
    r = _loads(await BashFunc(command="sudo rm -rf /").execute())
    assert r["stdout"] == ""
    assert "拒绝执行危险命令" in r["stderr"]
    assert r["returncode"] == -1


async def test_cwd_not_exist_as_json():
    """工作目录不存在 → 工具级错误三元 JSON。"""
    r = _loads(await BashFunc(command="echo hi", cwd="/nonexistent-dir-xyz").execute())
    assert r["stdout"] == ""
    assert "工作目录不存在" in r["stderr"]
    assert r["returncode"] == -1


# ── 3. 截断：stdout / stderr 各自独立截断 ──────────────

async def test_truncate_applies_per_stream():
    """stdout 与 stderr 各自独立截断（超 MAX_LINES 保留尾部）。"""
    n = BashFunc.MAX_LINES + 50
    r = _loads(await BashFunc(
        command=f"seq 1 {n}; seq 1 {n} 1>&2; echo TAIL").execute())
    assert "输出已截断" in r["stdout"]
    assert "输出已截断" in r["stderr"]
    # 截断标记为末行，其后无内容
    assert r["stdout"].rstrip("\n").endswith(
        f"…(输出已截断：超过 {BashFunc.MAX_LINES} 行，"
        f"仅展示最后 {BashFunc.MAX_LINES} 行)") or "(输出已截断" in r["stdout"].splitlines()[-1]
    # 尾部保留最新内容：stdout 截断后末段包含 TAIL（echo 在 seq 之后）与最大序号
    assert "TAIL" in r["stdout"]
    assert str(n) in r["stdout"]
    assert str(n) in r["stderr"]
    assert r["returncode"] == 0


def test_bash_rc_normalization():
    """_bash_rc：正常退出原样、负值转 128+sig、None 转 -1。"""
    assert BashFunc._bash_rc(0) == 0
    assert BashFunc._bash_rc(42) == 42
    assert BashFunc._bash_rc(-9) == 137
    assert BashFunc._bash_rc(-15) == 143
    assert BashFunc._bash_rc(None) == -1


def test_format_result_truncates_each_stream():
    """_format_result 对 stdout/stderr 分别截断，returncode 原样。"""
    big = "\n".join(f"line{i}" for i in range(BashFunc.MAX_LINES + 10))
    r = _loads(BashFunc._format_result(
        {"stdout": big, "stderr": big, "returncode": 3}))
    assert r["returncode"] == 3
    assert "输出已截断" in r["stdout"]
    assert "输出已截断" in r["stderr"]
    assert len(r["stdout"].splitlines()) <= BashFunc.MAX_LINES + 1
    assert len(r["stderr"].splitlines()) <= BashFunc.MAX_LINES + 1


def test_error_result_shape():
    """_error_result：stdout 空、stderr 说明、returncode -1。"""
    r = _loads(BashFunc._error_result("(拒绝执行危险命令: 测试)"))
    assert r == {"stdout": "", "stderr": "(拒绝执行危险命令: 测试)",
                 "returncode": -1}


def test_parse_bash_result_fields_roundtrip():
    """_parse_bash_result_fields：三元 JSON 解析 + 非法文本回退。"""
    out, err, rc = _parse_bash_result_fields(
        json.dumps({"stdout": "o", "stderr": "e", "returncode": 5}))
    assert (out, err, rc) == ("o", "e", 5)
    # 旧格式纯文本 → 原文进 stdout、无退出码
    out, err, rc = _parse_bash_result_fields("纯文本结果")
    assert (out, err, rc) == ("纯文本结果", "", None)
    # 空串
    assert _parse_bash_result_fields("") == ("", "", None)
    # 含 returncode 键但值为 None（异常时序）
    out, err, rc = _parse_bash_result_fields(
        json.dumps({"stdout": "", "stderr": "", "returncode": None}))
    assert rc is None


# ── 4. schema 描述同步 ─────────────────────────────────

def test_tool_schema_mentions_json_fields():
    """schema 描述向大模型声明三元 JSON 返回结构与 $? 语义。"""
    desc = BashFunc.to_tool_schema()["function"]["description"]
    assert "stdout" in desc and "stderr" in desc and "returncode" in desc
    assert "$?" in desc
    assert "128+sig" in desc
    params_desc = BashFunc.to_tool_schema()["function"]["parameters"]["properties"]["command"]["description"]
    assert "stdout" in params_desc and "returncode" in params_desc


# ── 5. 后台任务：完成结果三元展开 ──────────────────────

class _FakeAgent:
    """最小 Agent 桩：提供 bash 表 _background_tasks 与完成/移除方法。"""

    def __init__(self):
        self._background_tasks = {}

    def _publish_background_task_event(self):
        pass  # TUI 事件发布桩（测试无 TUI）

    def _register_background_task(self, tid, rec):
        self._background_tasks[tid] = rec

    def _complete_background_task(self, tid, result):
        rec = self._background_tasks.get(tid)
        if rec is None:
            return
        rec["result"] = result
        rec["stdout"], rec["stderr"], rec["returncode"] = (
            _parse_bash_result_fields(result))
        rec["status"] = "completed"
        rec["done"] = True

    def _remove_background_task(self, tid):
        return self._background_tasks.pop(tid, None)


async def test_background_wait_returns_three_fields():
    """bash_opt wait 返回 stdout/stderr/returncode（不再合并 output）。

    PTY 模式 stdout/stderr 物理合并（输出归 stdout、stderr 空），
    returncode 为命令退出码。
    """
    agent = _FakeAgent()
    f = BashFunc(command="echo bg-out; echo bg-err 1>&2; exit 5", background=True)
    f.set_agent(agent)
    started = _loads(await f.execute())
    tid = started["task_id"]
    assert started["status"] == "running"

    opt = BashOptFunc(task_id=tid, op="wait", timeout=10)
    opt.set_agent(agent)
    payload = _loads(await opt.execute())
    assert payload["task_id"] == tid
    assert payload["status"] == "completed"
    assert payload["returncode"] == 5
    assert "bg-out" in payload["stdout"]
    assert "output" not in payload          # 旧字段移除
    assert tid not in agent._background_tasks  # wait 后记录移除


async def test_background_collect_done_message_three_fields():
    """_collect_done_background_messages 插入的用户消息为三元展开 JSON。"""
    from src.core.base_agent import BaseAgent
    agent = _FakeAgent()
    tid = "bg-test123"
    agent._background_tasks[tid] = {
        "task": None, "command": "echo x", "done": True,
        "status": "completed",
        "result": json.dumps({"stdout": "x", "stderr": "", "returncode": 0}),
    }
    msgs = BaseAgent._collect_done_background_messages(agent)
    assert len(msgs) == 1
    payload = _loads(msgs[0])
    assert payload["task_id"] == tid
    assert payload["stdout"] == "x"
    assert payload["returncode"] == 0
    assert "output" not in payload
    assert tid not in agent._background_tasks


async def test_background_result_legacy_text_fallback():
    """result 为旧格式纯文本时回退：原文进 stdout、returncode None。"""
    from src.core.base_agent import BaseAgent
    agent = _FakeAgent()
    agent._background_tasks["bg-legacy"] = {
        "task": None, "command": "c", "done": True, "status": "completed",
        "result": "旧格式纯文本",
    }
    msgs = BaseAgent._collect_done_background_messages(agent)
    payload = _loads(msgs[0])
    assert payload["stdout"] == "旧格式纯文本"
    assert payload["stderr"] == ""
    assert payload["returncode"] is None


async def test_background_start_returns_task_json():
    """background=True 立即返回 task_id JSON（三元结果由 wait 获取）。"""
    agent = _FakeAgent()
    f = BashFunc(command="sleep 2", background=True)
    f.set_agent(agent)
    r = _loads(await f.execute())
    assert set(r.keys()) == {"task_id", "status", "command"}
    assert r["status"] == "running"
    # 清理：kill 后台任务
    opt = BashOptFunc(task_id=r["task_id"], op="kill")
    opt.set_agent(agent)
    await opt.execute()
    assert r["task_id"] not in agent._background_tasks
