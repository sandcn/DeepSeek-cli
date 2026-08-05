"""bash_task 工具（按 task_id 操作后台 bash 任务）测试。

覆盖需求：
1. bash_task schema 含 task_id/op 参数；按键名映射为跨平台 ANSI/VT100 序列
2. op=wait：等待后台任务完成并返回输出（JSON：task_id/command/status/output）；
   超时返回提示且不取消后台任务（asyncio.wait 观察而非 wait_for 干预）
3. op=kill：杀死后台任务所有进程树（killpg + /proc 递归补杀后代）并从 tasklist 移除
4. op=stdin：向后台任务 stdin 发送文本输入（newline 控制是否追加换行）
5. op=keys：向后台任务发送光标/键盘消息（跨平台 ANSI 序列，端到端验证子进程收到字节）
6. 参数校验：无 agent / 任务不存在 / 未知操作 / stdin 缺 text / keys 缺 key
7. subagent 类型排除 bash_task（map/review/plan），execute 保留
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from src.core.agent import Agent
from src.core.ports.model import ModelResult
from src.tools.bash import BashFunc, kill_process_tree
from src.tools.bash_task import BashTaskFunc, _resolve_key

# ═══════════════════════════════════════════════════════════
# 辅助函数 / fixture
# ═══════════════════════════════════════════════════════════


async def _start_bg(agent, command: str) -> str:
    """启动一个后台 bash 任务，返回 task_id。"""
    ret = await agent.get_tool_registry().dispatch(
        "bash", {"command": command, "background": True}, agent=agent,
    ).execute()
    data = json.loads(ret)
    assert data["status"] == "running"
    return data["task_id"]


async def _wait_task_ready(agent, task_id: str, timeout: float = 3.0) -> dict:
    """轮询等待后台任务记录填充 mode/pid（进程句柄已建立）。"""
    rec = agent._background_tasks[task_id]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rec.get("mode") and rec.get("pid"):
            return rec
        await asyncio.sleep(0.02)
    return rec


async def _cleanup_bg_tasks(agent) -> None:
    """清理 agent 中残留的后台任务（防止测试失败时进程泄漏）。"""
    for task_id in list(getattr(agent, "_background_tasks", {}) or {}):
        rec = agent._background_tasks.pop(task_id, None)
        if rec is None:
            continue
        pid = rec.get("pid")
        if pid is not None:
            try:
                kill_process_tree(pid)
            except Exception:
                pass
        task = rec.get("task")
        if task is not None and not task.done():
            task.cancel()
    if hasattr(agent, "_publish_background_task_event"):
        try:
            agent._publish_background_task_event()
        except Exception:
            pass


@pytest.fixture
async def agent():
    """Agent fixture：测试结束后清理后台任务，防止进程泄漏。"""
    a = Agent(model="fake-model")
    yield a
    await _cleanup_bg_tasks(a)


# ═══════════════════════════════════════════════════════════
# 基础行为：schema / 按键映射 / 参数校验
# ═══════════════════════════════════════════════════════════

class TestBashTaskBasics:
    """bash_task 工具基础行为。"""

    def test_schema_contains_params(self) -> None:
        """schema 包含 task_id/op 及可选参数。"""
        schema = BashTaskFunc.to_tool_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "task_id" in props
        assert "op" in props
        assert props["op"]["type"] == "string"
        assert set(props["op"]["enum"]) == {"wait", "kill", "stdin", "keys"}
        assert "timeout" in props
        assert "text" in props
        assert "newline" in props
        assert "key" in props
        assert schema["function"]["parameters"]["required"] == ["task_id", "op"]

    @pytest.mark.parametrize("key,expected", [
        # 光标键（VT100/ANSI 标准，跨平台）
        ("up", "\x1b[A"),
        ("down", "\x1b[B"),
        ("right", "\x1b[C"),
        ("left", "\x1b[D"),
        # 编辑键
        ("home", "\x1b[H"),
        ("end", "\x1b[F"),
        ("page_up", "\x1b[5~"),
        ("page_down", "\x1b[6~"),
        ("insert", "\x1b[2~"),
        ("delete", "\x1b[3~"),
        ("backspace", "\x7f"),
        ("tab", "\t"),
        ("enter", "\r"),
        ("escape", "\x1b"),
        ("space", " "),
        # 功能键
        ("f1", "\x1bOP"),
        ("f4", "\x1bOS"),
        ("f5", "\x1b[15~"),
        ("f12", "\x1b[24~"),
        # 控制组合
        ("ctrl_c", "\x03"),
        ("ctrl_d", "\x04"),
        ("ctrl_z", "\x1a"),
        ("ctrl_l", "\x0c"),
        # 大小写 / 连字符归一化
        ("UP", "\x1b[A"),
        ("Ctrl-C", "\x03"),
        ("ctrl-x", "\x18"),
    ])
    def test_key_mapping(self, key: str, expected: str) -> None:
        """按键名解析为对应 ANSI/VT100 序列。"""
        assert _resolve_key(key) == expected

    @pytest.mark.parametrize("key", ["ctrl_a", "ctrl_z", "ctrl_m"])
    def test_ctrl_letters_generated(self, key: str) -> None:
        """ctrl_a..ctrl_z 程序化生成（0x01..0x1A）。"""
        letter = key[len("ctrl_"):]
        assert _resolve_key(key) == chr(ord(letter) - ord("a") + 1)

    def test_key_mapping_unknown(self) -> None:
        """未知按键返回 None。"""
        assert _resolve_key("nope") is None
        assert _resolve_key("") is None

    @pytest.mark.asyncio
    async def test_requires_agent(self) -> None:
        """未关联 Agent 时返回错误提示。"""
        result = await BashTaskFunc(task_id="bg-x", op="wait").execute()
        assert "需要关联 Agent" in result

    @pytest.mark.asyncio
    async def test_task_not_found(self, agent) -> None:
        """任务不存在时返回明确错误。"""
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": "bg-none", "op": "wait"}, agent=agent,
        ).execute()
        assert "后台任务不存在" in result
        assert "bg-none" in result

    @pytest.mark.asyncio
    async def test_unknown_op(self, agent) -> None:
        """未知操作返回错误。"""
        task_id = await _start_bg(agent, "echo hi")
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "unknown"}, agent=agent,
        ).execute()
        assert "未知操作" in result
        assert task_id in agent._background_tasks

    @pytest.mark.asyncio
    async def test_stdin_requires_text(self, agent) -> None:
        """stdin 操作缺 text 返回错误。"""
        task_id = await _start_bg(agent, "cat")
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "stdin"}, agent=agent,
        ).execute()
        assert "text" in result

    @pytest.mark.asyncio
    async def test_keys_requires_key(self, agent) -> None:
        """keys 操作缺 key 返回错误。"""
        task_id = await _start_bg(agent, "cat")
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys"}, agent=agent,
        ).execute()
        assert "key" in result

    @pytest.mark.asyncio
    async def test_keys_unknown_key(self, agent) -> None:
        """keys 操作未知按键返回错误。"""
        task_id = await _start_bg(agent, "cat")
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "not-a-key"}, agent=agent,
        ).execute()
        assert "未知按键" in result


# ═══════════════════════════════════════════════════════════
# op=wait
# ═══════════════════════════════════════════════════════════

class TestBashTaskWait:
    """wait 操作：等待后台任务完成并获取输出。"""

    @pytest.mark.asyncio
    async def test_wait_returns_output(self, agent) -> None:
        """wait 返回 JSON（task_id/command/status/output），并从 tasklist 移除。"""
        task_id = await _start_bg(agent, "sleep 0.1 && echo bg-done")
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 15}, agent=agent,
        ).execute()

        data = json.loads(result)
        assert data["task_id"] == task_id
        assert data["command"] == "sleep 0.1 && echo bg-done"
        assert data["status"] == "completed"
        assert data["output"] == "bg-done"
        # 输出已由本工具拿到，任务记录被移除（避免重复插入对话）
        assert task_id not in agent._background_tasks

    @pytest.mark.asyncio
    async def test_wait_on_finished_task(self, agent) -> None:
        """任务已完成但记录仍在时，wait 直接返回结果并移除记录。"""
        task_id = await _start_bg(agent, "echo fast")
        rec = agent._background_tasks[task_id]
        # 等待任务自然完成（不通过 bash_task）
        await asyncio.wait_for(rec["task"], timeout=15)
        assert rec.get("done") is True

        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 5}, agent=agent,
        ).execute()
        data = json.loads(result)
        assert data["output"] == "fast"
        assert task_id not in agent._background_tasks

    @pytest.mark.asyncio
    async def test_wait_timeout_does_not_cancel_task(self, agent) -> None:
        """wait 超时返回提示，后台任务继续运行（asyncio.wait 不干预任务）。"""
        task_id = await _start_bg(agent, "sleep 2 && echo still-alive")
        rec = agent._background_tasks[task_id]

        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 0.2}, agent=agent,
        ).execute()
        assert "超时" in result
        # 任务未被取消、记录未被移除
        assert task_id in agent._background_tasks
        assert not rec["task"].done()

        # 再次 wait 能等到完成
        result2 = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 10}, agent=agent,
        ).execute()
        data = json.loads(result2)
        assert data["output"] == "still-alive"
        assert task_id not in agent._background_tasks


# ═══════════════════════════════════════════════════════════
# op=kill
# ═══════════════════════════════════════════════════════════

class TestBashTaskKill:
    """kill 操作：杀死后台任务所有进程树。"""

    @pytest.mark.asyncio
    async def test_kill_process_tree(self, agent) -> None:
        """kill 杀死后台进程树并从 tasklist 移除。"""
        # 命令含后台子进程（sleep 30 & wait），验证整棵进程树被杀死
        task_id = await _start_bg(agent, "sleep 30 & wait")
        rec = await _wait_task_ready(agent, task_id)
        pid = rec.get("pid")
        assert pid is not None

        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "kill"}, agent=agent,
        ).execute()
        assert "已杀死" in result
        assert task_id not in agent._background_tasks

        # 验证进程（及后代）已消失：等待回收后 os.kill(pid, 0) 应抛 ProcessLookupError
        await asyncio.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    @pytest.mark.asyncio
    async def test_kill_removes_io_handles_from_records(self, agent) -> None:
        """kill 后进程记录不再可操作（后续 stdin 操作返回任务不存在）。"""
        task_id = await _start_bg(agent, "sleep 30")
        rec = await _wait_task_ready(agent, task_id)
        assert rec.get("pid") is not None

        await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "kill"}, agent=agent,
        ).execute()

        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "stdin", "text": "x"}, agent=agent,
        ).execute()
        assert "后台任务不存在" in result


# ═══════════════════════════════════════════════════════════
# op=stdin
# ═══════════════════════════════════════════════════════════

class TestBashTaskStdin:
    """stdin 操作：向后台任务发送文本输入。"""

    @pytest.mark.skipif(not BashFunc._is_pty_available(), reason="PTY 不可用")
    @pytest.mark.asyncio
    async def test_stdin_sends_text(self, agent) -> None:
        """向 cat 发送文本，cat 输出包含收到的内容。"""
        task_id = await _start_bg(agent, "cat")
        rec = await _wait_task_ready(agent, task_id)
        assert rec.get("mode") == "pty"

        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "stdin", "text": "hello-input"}, agent=agent,
        ).execute()
        assert "发送" in result

        # PTY 模式下 ctrl_d（VEOF）结束 cat
        await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "ctrl_d"}, agent=agent,
        ).execute()
        wait = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 10}, agent=agent,
        ).execute()
        data = json.loads(wait)
        assert "hello-input" in data["output"]

    @pytest.mark.asyncio
    async def test_stdin_newline_false_sends_raw(self, agent) -> None:
        """newline=false 时不追加换行（用假 stdin 写端验证原始字节）。"""
        task_id = await _start_bg(agent, "echo hi")
        rec = agent._background_tasks[task_id]

        written = bytearray()

        class _FakeStdin:
            def write(self, data: bytes) -> None:
                written.extend(data)

            async def drain(self) -> None:
                return None

        # 手工构造 pipe 模式写端，验证 raw 发送
        rec["mode"] = "pipe"
        rec["stdin_writer"] = _FakeStdin()
        result = await agent.get_tool_registry().dispatch(
            "bash_task",
            {"task_id": task_id, "op": "stdin", "text": "raw-bytes", "newline": False},
            agent=agent,
        ).execute()
        assert "发送" in result
        assert bytes(written) == b"raw-bytes"

        # newline=true 时追加换行
        written.clear()
        result = await agent.get_tool_registry().dispatch(
            "bash_task",
            {"task_id": task_id, "op": "stdin", "text": "with-nl", "newline": True},
            agent=agent,
        ).execute()
        assert "发送" in result
        assert bytes(written) == b"with-nl\n"


# ═══════════════════════════════════════════════════════════
# op=keys
# ═══════════════════════════════════════════════════════════

class TestBashTaskKeys:
    """keys 操作：向后台任务发送光标/键盘消息（跨平台 ANSI/VT100）。"""

    @pytest.mark.skipif(not BashFunc._is_pty_available(), reason="PTY 不可用")
    @pytest.mark.asyncio
    async def test_keys_sends_ansi_sequence_end_to_end(self, agent) -> None:
        """端到端：发送 up 键，后台 dd 收到 \x1b[A 字节（od 十六进制 1b 5b 41）。"""
        task_id = await _start_bg(agent, "dd bs=1 count=3 2>/dev/null | od -An -tx1")
        rec = await _wait_task_ready(agent, task_id)
        assert rec.get("mode") == "pty"

        r1 = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "up"}, agent=agent,
        ).execute()
        assert "发送" in r1
        # enter 键使 canonical 模式下的输入行完成
        r2 = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "enter"}, agent=agent,
        ).execute()
        assert "发送" in r2

        wait = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 10}, agent=agent,
        ).execute()
        data = json.loads(wait)
        # od -tx1 输出 "\x1b[A" 的十六进制：1b 5b 41
        assert "1b 5b 41" in data["output"]

    @pytest.mark.asyncio
    async def test_keys_sends_raw_sequence_via_fake_stdin(self, agent) -> None:
        """keys 写入的是 ANSI 字节序列（用假 stdin 写端验证）。"""
        task_id = await _start_bg(agent, "echo hi")
        rec = agent._background_tasks[task_id]

        written = bytearray()

        class _FakeStdin:
            def write(self, data: bytes) -> None:
                written.extend(data)

            async def drain(self) -> None:
                return None

        rec["mode"] = "pipe"
        rec["stdin_writer"] = _FakeStdin()
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "up"}, agent=agent,
        ).execute()
        assert "发送" in result
        assert bytes(written) == b"\x1b[A"

        written.clear()
        result = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "ctrl_c"}, agent=agent,
        ).execute()
        assert "发送" in result
        assert bytes(written) == b"\x03"


# ═══════════════════════════════════════════════════════════
# SubAgent 工具排除
# ═══════════════════════════════════════════════════════════

class TestBashTaskSubagentExclusion:
    """bash_task 在受限 subagent 类型中排除（与 bash 同策略）。"""

    def test_excluded_for_restricted_agent_types(self) -> None:
        """map/review/plan 类型排除 bash_task，execute 保留。"""
        from src.core.subagent import _get_excluded_tools

        for agent_type in ("map", "review", "plan"):
            excluded = _get_excluded_tools(agent_type)
            assert "bash_task" in excluded, f"{agent_type} 应排除 bash_task"
        assert "bash_task" not in _get_excluded_tools("execute")


# ═══════════════════════════════════════════════════════════
# Agent.run 完整循环集成
# ═══════════════════════════════════════════════════════════

class _BashTaskModelPort:
    """可编程模型端口：第1轮启动后台 bash，第2轮用 bash_task wait，第3轮结束。"""

    def __init__(self):
        self.agent = None
        self.call_count = 0

    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return ModelResult(
                content="", usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_1", "name": "bash",
                    "arguments": {"command": "sleep 0.2 && echo integrated", "background": True},
                }],
            )
        if self.call_count == 2:
            task_id = next(iter(self.agent._background_tasks.keys()))
            return ModelResult(
                content="", usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_2", "name": "bash_task",
                    "arguments": {"task_id": task_id, "op": "wait", "timeout": 10},
                }],
            )
        return ModelResult(
            content="最终回复", usage={"input": 1, "output": 1}, tool_calls=[],
        )


class _BashTaskTimeoutModelPort:
    """第1轮启动长任务，第2轮 bash_task wait（短超时返回提示），第3轮结束。

    验证：wait 超时后任务被 bash_task 管理，对话轮次不等待其完成、
    结果也不自动插入用户消息（由大模型继续用 bash_task 管理）。
    """

    def __init__(self):
        self.agent = None
        self.call_count = 0

    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return ModelResult(
                content="", usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_1", "name": "bash",
                    "arguments": {"command": "sleep 2 && echo late-result", "background": True},
                }],
            )
        if self.call_count == 2:
            task_id = next(iter(self.agent._background_tasks.keys()))
            return ModelResult(
                content="", usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_2", "name": "bash_task",
                    "arguments": {"task_id": task_id, "op": "wait", "timeout": 0.2},
                }],
            )
        return ModelResult(
            content="对话结束", usage={"input": 1, "output": 1}, tool_calls=[],
        )


class TestBashTaskAgentIntegration:
    """完整 Agent.run 循环中 bash_task 与后台任务管理的协作。"""

    @pytest.mark.asyncio
    async def test_bash_task_wait_consumes_result_no_duplicate(self) -> None:
        """大模型用 bash_task wait 主动拿到输出后，结果不重复插入用户消息。"""
        port = _BashTaskModelPort()
        agent = Agent(model="fake-model", async_model_port=port)
        port.agent = agent

        try:
            interrupted = await agent.run()
        finally:
            await _cleanup_bg_tasks(agent)

        assert interrupted is False
        assert port.call_count == 3

        # bash_task.wait 已消费结果 → 不再有后台结果 JSON 用户消息（无重复）
        bg_msgs = [
            m for m in agent.messages
            if m["role"] == "user" and m["content"].startswith("{")
        ]
        assert bg_msgs == []
        # 最终回复为 assistant 消息
        assert agent.messages[-1]["role"] == "assistant"
        assert agent.messages[-1]["content"] == "最终回复"
        # tasklist 清空
        assert agent._background_tasks == {}

    @pytest.mark.asyncio
    async def test_bash_task_marks_task_as_managed(self, agent) -> None:
        """bash_task 操作后任务被标记 managed_by_tool（结果不再自动插入）。"""
        task_id = await _start_bg(agent, "sleep 5")
        rec = agent._background_tasks[task_id]
        assert rec.get("managed_by_tool") is not True

        # 任意 op（此处用 keys）触发管理标记
        await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "ctrl_c"}, agent=agent,
        ).execute()
        assert rec.get("managed_by_tool") is True

    @pytest.mark.asyncio
    async def test_wait_timeout_task_not_waited_no_auto_insert(self) -> None:
        """wait 超时后任务由 bash_task 管理：对话轮次不等待、结果不自动插入。"""
        port = _BashTaskTimeoutModelPort()
        agent = Agent(model="fake-model", async_model_port=port)
        port.agent = agent

        try:
            started = time.monotonic()
            interrupted = await agent.run()
            elapsed = time.monotonic() - started
        finally:
            await _cleanup_bg_tasks(agent)

        assert interrupted is False
        assert port.call_count == 3
        # 对话应在 sleep 2 完成前结束（_process_background_tasks 不等待 managed 任务）
        assert elapsed < 1.5, f"不应等待 managed 任务完成，实际耗时 {elapsed:.2f}s"
        # wait 超时后结果不自动插入用户消息（无后台结果 JSON 消息）
        bg_msgs = [
            m for m in agent.messages
            if m["role"] == "user" and m["content"].startswith("{")
        ]
        assert bg_msgs == []
        assert agent.messages[-1]["role"] == "assistant"
        assert agent.messages[-1]["content"] == "对话结束"
