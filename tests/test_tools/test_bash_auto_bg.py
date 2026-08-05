"""bash 工具前台执行超过 1 分钟自动转后台测试。

覆盖需求（2026-08-06 改造：删除 timeout 参数，改为自动转后台）：
1. bash schema 不再包含 timeout 参数；构造函数也不接受 timeout
2. 前台命令在 _AUTO_BG_TIMEOUT 秒内完成 → 正常返回输出（行为不变）
3. 前台命令超过 _AUTO_BG_TIMEOUT 秒未完成 → 自动转后台：
   - 命令**不终止**（asyncio.wait 观察而非 wait_for 强杀）
   - 返回 {"task_id": ..., "status": "running", "command": ...} JSON
   - 任务注册到 agent._background_tasks，bash_task 工具可继续管理
4. 转后台后 bash_task wait 可拿到完整输出、kill 可终止、stdin/keys 可交互
5. 无 Agent 上下文时超时返回错误提示（无法自动转后台管理）
6. 转后台任务完成后，对话轮次结束插入 JSON 用户消息（与 background=True 一致）
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from src.core.agent import Agent
from src.core.ports.model import ModelResult
from src.tools.bash import BashFunc
from src.tools.bash_task import BashTaskFunc


async def _cleanup_bg_tasks(agent) -> None:
    """清理 agent 中残留的后台任务（防止测试失败时进程泄漏）。"""
    for task_id in list(getattr(agent, "_background_tasks", {}) or {}):
        rec = agent._background_tasks.pop(task_id, None)
        if rec is None:
            continue
        pid = rec.get("pid")
        if pid is not None:
            try:
                from src.tools.bash import kill_process_tree
                kill_process_tree(pid)
            except Exception:
                pass
        task = rec.get("task")
        if task is not None and not task.done():
            task.cancel()


@pytest.fixture
async def agent():
    """Agent fixture：测试结束后清理后台任务，防止进程泄漏。"""
    a = Agent(model="fake-model")
    yield a
    await _cleanup_bg_tasks(a)


async def _dispatch_bash(agent, command: str, background: bool = False):
    """通过工具注册表调度 bash（自动 set_agent）。"""
    args = {"command": command}
    if background:
        args["background"] = True
    return agent.get_tool_registry().dispatch("bash", args, agent=agent)


# ═══════════════════════════════════════════════════════════
# schema / 构造函数：timeout 已移除
# ═══════════════════════════════════════════════════════════

class TestTimeoutRemoved:
    """timeout 参数已从 schema 与构造函数移除。"""

    def test_schema_has_no_timeout_param(self) -> None:
        """schema 不再包含 timeout 参数。"""
        schema = BashFunc.to_tool_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "timeout" not in props
        assert "command" in props
        assert "cwd" in props
        assert "background" in props

    def test_schema_mentions_auto_background(self) -> None:
        """schema 描述包含自动转后台说明。"""
        desc = BashFunc.to_tool_schema()["function"]["description"]
        assert "自动转后台" in desc
        assert "bash_task" in desc
        # timeout 参数相关说明已移除（仍可提及系统 `timeout` 命令警示）
        assert "timeout 参数" not in desc
        assert "超时限制" not in desc
        assert "超时秒数" not in desc

    def test_constructor_rejects_timeout(self) -> None:
        """构造函数不再接受 timeout 参数（传了抛 TypeError）。"""
        with pytest.raises(TypeError):
            BashFunc(command="echo hi", timeout=30)

    def test_auto_bg_timeout_constant(self) -> None:
        """自动转后台阈值默认 60 秒。"""
        assert BashFunc._AUTO_BG_TIMEOUT == 60


# ═══════════════════════════════════════════════════════════
# 前台快速命令：行为不变
# ═══════════════════════════════════════════════════════════

class TestFastForegroundCommand:
    """前台命令在阈值内完成 → 正常返回输出。"""

    @pytest.mark.asyncio
    async def test_fast_command_returns_output(self, agent) -> None:
        func = await _dispatch_bash(agent, "echo fast-done")
        ret = await func.execute()
        assert ret == "fast-done"
        # 未转后台：无后台任务
        assert agent._background_tasks == {}

    @pytest.mark.asyncio
    async def test_fast_command_stderr_merged(self, agent) -> None:
        func = await _dispatch_bash(agent, "echo out; echo err >&2")
        ret = await func.execute()
        assert "out" in ret and "err" in ret


# ═══════════════════════════════════════════════════════════
# 前台慢命令：自动转后台
# ═══════════════════════════════════════════════════════════

class TestAutoBackground:
    """前台命令超过 _AUTO_BG_TIMEOUT 秒 → 自动转后台并返回 task_id JSON。"""

    @pytest.mark.asyncio
    async def test_long_command_auto_bg_returns_json(self, agent, monkeypatch) -> None:
        """超过阈值 → 返回 task_id JSON、任务注册、命令不终止继续运行。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        func = await _dispatch_bash(agent, "sleep 3 && echo auto-bg-done")
        started = time.monotonic()
        ret = await func.execute()
        elapsed = time.monotonic() - started

        # 返回 task_id JSON（命令 sleep 3 未完成，1 秒后自动转后台返回）
        data = json.loads(ret)
        assert data["status"] == "running"
        assert data["task_id"].startswith("bg-")
        assert data["command"] == "sleep 3 && echo auto-bg-done"
        # 命令本身 sleep 3s，返回应远快于命令完成（约 1s 阈值 + 调度开销）
        assert elapsed < 2.5

        # 任务已注册到 agent._background_tasks 且仍在运行
        task_id = data["task_id"]
        assert task_id in agent._background_tasks
        rec = agent._background_tasks[task_id]
        assert rec["done"] is False
        assert rec["task"] is not None
        assert not rec["task"].done()

        # 等待后台任务真正完成：结果写入记录（命令未被终止）
        await asyncio.wait_for(rec["task"], timeout=15)
        assert rec["done"] is True
        assert rec["result"] == "auto-bg-done"

    @pytest.mark.asyncio
    async def test_auto_bg_process_handle_recorded(self, agent, monkeypatch) -> None:
        """转后台后任务记录包含子进程句柄（pid/mode），bash_task 可操作。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        func = await _dispatch_bash(agent, "sleep 3")
        ret = await func.execute()
        task_id = json.loads(ret)["task_id"]

        rec = agent._background_tasks[task_id]
        # 子进程句柄应已记录（PTY 或 PIPE 至少 pid 非空）
        assert rec["pid"] is not None
        assert rec["process"] is not None
        assert rec["mode"] in ("pty", "pipe")

        # bash_task kill 可终止自动转后台的任务
        kill_ret = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "kill"}, agent=agent,
        ).execute()
        assert "已杀死" in kill_ret
        assert task_id not in agent._background_tasks

    @pytest.mark.asyncio
    async def test_auto_bg_wait_gets_output(self, agent, monkeypatch) -> None:
        """转后台后 bash_task wait 拿到命令完整输出，并从 tasklist 移除。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        func = await _dispatch_bash(agent, "sleep 2 && echo bg-wait-done")
        ret = await func.execute()
        task_id = json.loads(ret)["task_id"]

        wait = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 15}, agent=agent,
        ).execute()
        data = json.loads(wait)
        assert data["task_id"] == task_id
        assert data["status"] == "completed"
        assert data["output"] == "bg-wait-done"
        assert task_id not in agent._background_tasks

    @pytest.mark.skipif(not BashFunc._is_pty_available(), reason="PTY 不可用")
    @pytest.mark.asyncio
    async def test_auto_bg_stdin_interactive(self, agent, monkeypatch) -> None:
        """转后台后 stdin/keys 仍可交互（cat 挂起 → 转后台 → 写入输入 → 结果含内容）。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        func = await _dispatch_bash(agent, "cat")
        ret = await func.execute()
        task_id = json.loads(ret)["task_id"]

        # 进程句柄就绪后写入 stdin
        deadline = time.monotonic() + 3
        rec = agent._background_tasks[task_id]
        while time.monotonic() < deadline:
            if rec.get("mode") and rec.get("pid"):
                break
            await asyncio.sleep(0.02)
        assert rec.get("mode") == "pty"

        r1 = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "stdin", "text": "hello-auto-bg"},
            agent=agent,
        ).execute()
        assert "发送" in r1
        # ctrl_d 结束 cat
        await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "keys", "key": "ctrl_d"}, agent=agent,
        ).execute()

        wait = await agent.get_tool_registry().dispatch(
            "bash_task", {"task_id": task_id, "op": "wait", "timeout": 10}, agent=agent,
        ).execute()
        data = json.loads(wait)
        assert "hello-auto-bg" in data["output"]

    @pytest.mark.asyncio
    async def test_no_agent_returns_error(self, monkeypatch) -> None:
        """无 Agent 上下文时超时返回错误提示（无法自动转后台管理）。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        # 直接构造（未关联 agent），命令 sleep 2（会自己结束，不泄漏）
        func = BashFunc(command="sleep 2")
        ret = await func.execute()
        assert "无法自动转后台" in ret

    @pytest.mark.asyncio
    async def test_auto_bg_completes_and_inserts_user_message(self, agent, monkeypatch) -> None:
        """自动转后台任务完成后，对话轮次结束插入 JSON 用户消息（与 background=True 一致）。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        func = await _dispatch_bash(agent, "sleep 2 && echo auto-msg")
        ret = await func.execute()
        task_id = json.loads(ret)["task_id"]

        # 等待任务完成
        rec = agent._background_tasks[task_id]
        await asyncio.wait_for(rec["task"], timeout=15)
        assert rec["done"] is True

        # 一轮对话完成后处理
        proceed = await agent._process_background_tasks()
        assert proceed is True

        last = agent.messages[-1]
        assert last["role"] == "user"
        msg = json.loads(last["content"])
        assert msg["task_id"] == task_id
        assert msg["output"] == "auto-msg"
        assert msg["status"] == "completed"
        assert task_id not in agent._background_tasks

    @pytest.mark.asyncio
    async def test_auto_bg_stops_publishing_after_promotion(self, agent, monkeypatch) -> None:
        """转后台后不再向工具卡片发布实时输出（行回调在提升为后台时断开）。

        回归：自动转后台前，工具卡片显示实时输出（如 ping 逐行）；
        转后台后工具已返回 task_id JSON（卡片闭合），后续输出不应再发布
        （由 bash_task wait 获取完整结果）。
        """
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        published: list[str] = []

        async def _on_line(text: str, is_stderr: bool) -> None:
            published.append(text)

        func = await _dispatch_bash(agent, "echo start; sleep 2; echo after-promote")
        # 走 display/web_display 共用的行回调路径
        ret = await func._run_with_line_callback(_on_line)
        data = json.loads(ret)
        assert data["status"] == "running"

        # 转后台前（1 秒内）的输出已发布
        assert any("start" in p for p in published), f"start 应已发布: {published}"

        # 等待命令完成：转后台后的输出不应再发布到行回调
        rec = agent._background_tasks[data["task_id"]]
        await asyncio.wait_for(rec["task"], timeout=15)
        await asyncio.sleep(0.1)
        assert not any("after-promote" in p for p in published), \
            f"转后台后不应继续发布输出: {published}"

        # 完整结果仍可通过后台任务记录获取（bash_task wait 可拿到）
        assert rec["result"] == "start\nafter-promote"


# ═══════════════════════════════════════════════════════════
# Agent.run 完整循环集成
# ═══════════════════════════════════════════════════════════

class _AutoBgModelPort:
    """可编程模型端口：第1轮调用前台 bash（长命令自动转后台），第2轮结束对话。"""

    def __init__(self):
        self.call_count = 0

    async def call(self, messages, model=None, tools=None, display=None,
                   label=None, silent=False, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return ModelResult(
                content="", usage={"input": 1, "output": 1},
                tool_calls=[{
                    "id": "call_1", "name": "bash",
                    "arguments": {"command": "sleep 2 && echo auto-integrated"},
                }],
            )
        return ModelResult(
            content="最终回复", usage={"input": 1, "output": 1}, tool_calls=[],
        )


class TestAgentRunAutoBackground:
    """Agent.run 循环中前台长命令自动转后台的完整编排。"""

    @pytest.mark.asyncio
    async def test_auto_bg_continues_round(self, monkeypatch) -> None:
        """自动转后台任务完成后插入用户消息，模型继续一轮对话处理结果。"""
        monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 1)
        port = _AutoBgModelPort()
        agent = Agent(model="fake-model", async_model_port=port)

        try:
            interrupted = await agent.run()
        finally:
            await _cleanup_bg_tasks(agent)

        assert interrupted is False
        # 模型调用 3 次：①前台 bash（1s 后自动转后台返回 JSON）
        #  ②结束对话 ③后台结果插入后处理
        assert port.call_count == 3

        # 后台结果 JSON 用户消息已插入
        bg_msgs = [
            json.loads(m["content"]) for m in agent.messages
            if m["role"] == "user" and m["content"].startswith("{")
        ]
        assert len(bg_msgs) == 1
        assert bg_msgs[0]["output"] == "auto-integrated"
        assert agent._background_tasks == {}
