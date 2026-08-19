"""subagent 后台执行 + subagent_opt 工具测试（2026-08-18，2026-08-19 更新）。

需求：
  1. subagent 工具**无 background 参数、直接后台执行**（2026-08-19）：
     每次调用立即返回 {"task_id": "sa-xxx", "status": "running"} JSON，
     后台 subagent 在独立 asyncio 后台任务中执行（无前台阻塞模式）；
  2. 新增 subagent_opt 工具（类似 bash_opt）：按 task_id 操作后台 subagent
     任务（op=read / wait / kill）；
  3. 后台 subagent 仅主 Agent 独有：
     - SubAgent 工具白名单全类型排除 subagent（已有）与 subagent_opt（新增）；
     - subagent / subagent_opt 运行时 isinstance(agent, SubAgent) 强制校验；
     - 共享 ParallelExecutor barrier 批量并行模式已随 background 参数一并
       移除（每次调用独立后台执行，互不阻塞）。
  4. 并发/沙盒正确性（review 2026-08-18 修复）：
     - 后台 subagent 使用唯一 label（task_id），并发不互相覆盖；
     - SubAgentPanelController 活跃引用计数：最后一个活跃方 stop 才清理面板；
     - 沙盒记录可重挂（reindex_records），上下文压缩后索引失效不悬空；
     - 取消后台 subagent 时任务记录 status="cancelled"。
"""

from __future__ import annotations

import asyncio
import json

from src.core.base_agent import BaseAgent
from src.core.subagent import SubAgent
from src.tools import Subagent, SubagentOpt
from src.tools.subagent import SubagentFunc
from src.tools.subagent_opt import SubagentOptFunc


# ── 测试辅助 ──────────────────────────────────────────────

class _FakeMainAgent(BaseAgent):
    """最小主 Agent 桩：模拟主 Agent——显式初始化 subagent 后台任务表。

    _subagent_tasks 仅主 Agent 独有（后台 subagent 仅主 Agent 可派发），
    SubAgent 继承 BaseAgent 不持有该表；bash 表 _background_tasks 由
    BaseAgent 统一初始化（主 Agent / SubAgent 都有）。
    """

    def __init__(self):
        super().__init__()
        self._subagent_tasks: dict[str, dict] = {}


def _bare_subagent() -> SubAgent:
    """构造不调用 __init__ 的 SubAgent 实例（仅用于 isinstance 校验）。"""
    return SubAgent.__new__(SubAgent)


def _make_rec(task, task_id="sa-1", done=False, result="", status="running"):
    return {
        "task": task,
        "command": "subagent(任务A)",
        "description": "任务A",
        "agent_type": "execute",
        "created_at": 0.0,
        "done": done,
        "result": result,
        "status": status,
        "read_buffer": "",
    }


async def _async_noop(*args, **kwargs):
    return None


# ── 1. subagent schema 与参数解析 ─────────────────────────

def test_schema_has_no_background_param():
    """subagent schema 不含 background 参数（直接后台执行，无前台模式）。"""
    schema = SubagentFunc.to_tool_schema()
    props = schema["function"]["parameters"]["properties"]
    assert "background" not in props
    assert set(props) == {"description", "prompt", "type"}
    assert "background" not in schema["function"]["parameters"]["required"]


def test_from_args_no_background_always_background():
    """from_args 不解析 background：无论参数是否携带 background 均直接后台执行。

    subagent 工具无 background 参数、无前台模式——模型即使传入 background
    （历史/幻觉参数）也被忽略，执行恒为后台。
    """
    f1 = SubagentFunc.from_args({"description": "a", "prompt": "p"})
    assert not hasattr(f1, "background")
    f2 = SubagentFunc.from_args(
        {"description": "a", "prompt": "p", "background": False}
    )
    assert not hasattr(f2, "background")  # 传入 background 也被忽略


def test_display_params_no_bg_prefix():
    """display_params 直接后台：无 bg 前缀（所有调用恒后台）。"""
    assert SubagentFunc.display_params(
        {"description": "解析 user.py"}
    ) == "agent: 解析 user.py"
    assert SubagentFunc.display_params(
        {"description": "解析 user.py", "background": False}
    ) == "agent: 解析 user.py"  # 遗留 background 参数不影响展示


# ── 2. 后台执行 ────────────────────────────────────────

async def test_background_execute_returns_task_id_and_registers(monkeypatch):
    """后台执行立即返回 task_id JSON，并把任务注册到主 Agent 后台任务表。"""
    agent = _FakeMainAgent()
    func = SubagentFunc(
        description="任务A", prompt="指令", target_agent_type="map",
    )
    func.set_agent(agent)
    monkeypatch.setattr(
        "src.tools.subagent.SubagentFunc._run_background_subagent",
        _async_noop,
    )
    monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)

    result = await func.execute()
    payload = json.loads(result)
    assert payload["task_id"].startswith("sa-")
    assert payload["status"] == "running"
    assert payload["description"] == "任务A"
    assert payload["type"] == "map"

    rec = agent._subagent_tasks[payload["task_id"]]
    assert rec["done"] is False
    assert rec["status"] == "running"
    assert rec["description"] == "任务A"
    assert rec["agent_type"] == "map"
    assert rec["command"].startswith("subagent(")


async def test_background_tables_are_independent():
    """subagent 与 bash 后台任务分表独立（_subagent_tasks / _background_tasks）。

    同一 Agent 上同时存在 subagent 后台任务与 bash 后台任务时：
      - subagent 记录只落在 _subagent_tasks（task_id 前缀 sa-）；
      - bash 记录只落在 _background_tasks（task_id 前缀 bg-）；
      - 两表互不可见：bash_opt 查 _background_tasks、subagent_opt 查
        _subagent_tasks，误传对方 task_id 时天然查不到对方记录。
    """
    import asyncio

    from src.tools.subagent_opt import SubagentOptFunc
    from src.tools.bash_opt import BashOptFunc

    agent = _FakeMainAgent()

    # subagent 后台任务 → _subagent_tasks
    sa_id = "sa-aaaa"
    agent._subagent_tasks[sa_id] = _make_rec(task=None)
    # bash 后台任务 → _background_tasks
    bg_id = "bg-bbbb"
    bg_task = asyncio.ensure_future(_async_noop())
    agent._background_tasks[bg_id] = {"task": bg_task, "done": False}

    # 表内容互斥：subagent 记录不在 bash 表，bash 记录不在 subagent 表
    assert sa_id not in agent._background_tasks
    assert bg_id not in agent._subagent_tasks

    # subagent_opt 只看 subagent 表：能查到 sa- 任务，查不到 bg- 任务
    f1 = SubagentOptFunc(task_id=sa_id, op="read")
    f1.set_agent(agent)
    payload1 = json.loads(await f1.execute())
    assert payload1["task_id"] == sa_id
    f2 = SubagentOptFunc(task_id=bg_id, op="read")
    f2.set_agent(agent)
    assert "sa-xxx" in await f2.execute()  # 前缀校验拒绝

    # bash_opt 只看 bash 表：能查到 bg- 任务，查不到 sa- 任务
    b1 = BashOptFunc(task_id=bg_id, op="read")
    b1.set_agent(agent)
    payload2 = json.loads(await b1.execute())
    assert payload2["task_id"] == bg_id
    b2 = BashOptFunc(task_id=sa_id, op="read")
    b2.set_agent(agent)
    assert "bg-xxx" in await b2.execute()  # 前缀校验拒绝

    await bg_task
    # subagent 记录仍保留（bash_opt 的 read 未触达 subagent 表）
    assert sa_id in agent._subagent_tasks


async def test_process_subagent_tasks_independent():
    """_process_subagent_tasks 独立处理 subagent 表（不触碰 bash 表）。

    两表各自完成的任务分别由 _process_subagent_tasks / _process_background_tasks
    消费：subagent 表有已完成任务时 _process_subagent_tasks 返回 True 并插入
    用户消息；bash 表中的未完成任务不受影响、不被误消费。
    """
    agent = _FakeMainAgent()

    # subagent 表：一个已完成任务 + 一个运行中任务
    agent._subagent_tasks["sa-done"] = _make_rec(
        task=None, done=True, result="## 任务A\n结果", status="completed",
    )
    agent._subagent_tasks["sa-running"] = _make_rec(task=None, done=False)

    # bash 表：一个运行中任务（不应被 subagent 处理流程消费）
    agent._background_tasks["bg-keep"] = {"task": None, "done": False}

    assert await agent._process_subagent_tasks() is True
    # 已完成 subagent 记录被消费移除，运行中 subagent 记录保留
    assert "sa-done" not in agent._subagent_tasks
    assert "sa-running" in agent._subagent_tasks
    # bash 表记录完全未被触碰
    assert "bg-keep" in agent._background_tasks
    # 插入的用户消息为 subagent 结果 JSON
    assert any("sa-done" in m["content"] for m in agent.messages)


async def test_background_execute_without_agent():
    """后台执行未关联 Agent 上下文时返回错误提示。"""
    func = SubagentFunc(description="a", prompt="p")
    result = await func.execute()
    assert "未关联父 Agent" in result


async def test_background_execute_rejected_in_subagent():
    """后台 subagent 仅主 Agent 独有：SubAgent 内运行时强制拒绝。"""
    func = SubagentFunc(description="a", prompt="p")
    func.set_agent(_bare_subagent())
    result = await func.execute()
    assert result.startswith("错误")
    assert "仅主 Agent" in result


async def test_background_run_completes_record(monkeypatch):
    """后台任务完成后把格式化结果写入任务记录（_complete_subagent_task）。"""
    agent = _FakeMainAgent()
    func = SubagentFunc(
        description="任务A", prompt="指令", target_agent_type="execute",
    )
    func.set_agent(agent)
    monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)
    monkeypatch.setattr("src.core.display_target.get_output_publisher", lambda: None)

    captured_spec = {}

    async def fake_run(self, specs, max_workers=None):
        captured_spec.update(specs[0])
        return [{
            "label": "agent-1",
            "description": "任务A",
            "result": "结果内容",
            "error": "",
            "agent_type": "execute",
        }]

    monkeypatch.setattr(
        "src.core.parallel_executor.ParallelExecutor.run", fake_run,
    )

    result = await func.execute()
    task_id = json.loads(result)["task_id"]
    # 等待后台任务完成
    await asyncio.wait_for(agent._subagent_tasks[task_id]["task"], timeout=5)

    rec = agent._subagent_tasks[task_id]
    assert rec["done"] is True
    assert rec["status"] == "completed"
    assert rec["result"] == "## 任务A\n结果内容"

    # spec 传参正确（tool_label 为空串 → dispatch_label 兼容）
    assert captured_spec["description"] == "任务A"
    assert captured_spec["prompt"] == "指令"
    assert captured_spec["agent_type"] == "execute"
    assert captured_spec["tool_label"] == ""
    # ★ P1（review 2026-08-18）：后台 subagent 使用唯一 label（task_id），
    #   并发执行不互相覆盖（TUI 槽位 / 导出去重键 / 轨迹存档）
    assert captured_spec["label"] == task_id


async def test_background_run_error_writes_failure(monkeypatch):
    """后台执行异常时任务记录写入错误结果而非崩溃。"""
    agent = _FakeMainAgent()
    func = SubagentFunc(description="任务A", prompt="指令")
    func.set_agent(agent)
    monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)
    monkeypatch.setattr("src.core.display_target.get_output_publisher", lambda: None)

    async def fake_run(self, specs, max_workers=None):
        raise RuntimeError("模拟执行失败")

    monkeypatch.setattr(
        "src.core.parallel_executor.ParallelExecutor.run", fake_run,
    )

    result = await func.execute()
    task_id = json.loads(result)["task_id"]
    await asyncio.wait_for(agent._subagent_tasks[task_id]["task"], timeout=5)

    rec = agent._subagent_tasks[task_id]
    assert rec["done"] is True
    assert "执行出错" in rec["result"]


async def test_background_subagent_captures_sandbox_index(monkeypatch):
    """后台 subagent 的文件沙盒：变更关联派发轮次的消息索引（与执行语义一致）。

    后台任务由 asyncio.ensure_future 创建，复制派发时 contextvars（含
    message_index）。MainAgent 后续对话更新沙盒索引不影响后台 subagent
    的文件变更关联（SubAgent 文件操作经 record_file_change_from_context
    读取后台任务上下文中的索引）——保证沙盒可精确回滚到派发轮次。
    """
    from src.core.sandbox_manager import (
        SandboxManager,
        get_current_message_index,
        get_sandbox_manager,
        set_current_message_index,
        set_sandbox_manager,
    )

    old_sm = get_sandbox_manager()
    sm = SandboxManager()
    set_sandbox_manager(sm)
    try:
        # 派发时（subagent 工具调用所在轮次）：assistant tool_calls 消息索引 = 3
        set_current_message_index(3)

        captured = {}

        async def fake_run(self, specs, max_workers=None):
            # 模拟后台 subagent 执行期间的文件操作索引读取
            await asyncio.sleep(0.01)
            captured["bg_index"] = get_current_message_index()
            return [{
                "label": "agent-1", "description": "任务A",
                "result": "ok", "error": "", "agent_type": "execute",
            }]

        monkeypatch.setattr(
            "src.core.parallel_executor.ParallelExecutor.run", fake_run,
        )
        monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)
        monkeypatch.setattr(
            "src.core.display_target.get_output_publisher", lambda: None,
        )

        agent = _FakeMainAgent()
        func = SubagentFunc(description="任务A", prompt="指令")
        func.set_agent(agent)
        result = await func.execute()
        task_id = json.loads(result)["task_id"]

        # MainAgent 继续对话：沙盒索引前进到 10
        set_current_message_index(10)

        await asyncio.wait_for(agent._subagent_tasks[task_id]["task"], timeout=5)
        # 后台 subagent 执行期间读取的索引 = 派发时索引（3），
        # 不受 MainAgent 后续轮次更新影响（contextvars 快照语义）
        assert captured["bg_index"] == 3
        assert get_current_message_index() == 10
    finally:
        set_sandbox_manager(old_sm)


async def test_execute_always_background_without_shared_executor(monkeypatch):
    """无共享 executor（同轮仅 subagent 或异常场景）仍直接后台执行。

    旧前台模式依赖调度层创建的共享 ParallelExecutor barrier（无共享实例时报错）；
    改为直接后台执行后，无论是否存在共享 executor，execute() 恒走后台路径。
    """
    agent = _FakeMainAgent()
    func = SubagentFunc(description="a", prompt="p")
    func.set_agent(agent)
    monkeypatch.setattr(
        "src.tools.subagent.SubagentFunc._run_background_subagent",
        _async_noop,
    )
    monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)

    result = await func.execute()
    payload = json.loads(result)
    assert payload["task_id"].startswith("sa-")
    assert payload["status"] == "running"
    assert payload["description"] == "a"
    assert any(k.startswith("sa-") for k in agent._subagent_tasks)


async def test_execute_always_background(monkeypatch):
    """execute() 恒走 _execute_background（无 background 参数、无前台分支）并返回 task_id。"""
    agent = _FakeMainAgent()
    func = SubagentFunc(description="任务A", prompt="指令")
    func.set_agent(agent)
    monkeypatch.setattr(
        "src.tools.subagent.SubagentFunc._run_background_subagent",
        _async_noop,
    )
    monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)

    result = await func.execute()
    payload = json.loads(result)
    assert payload["task_id"].startswith("sa-")
    assert payload["status"] == "running"
    assert payload["description"] == "任务A"
    assert payload["type"] == "execute"

    rec = agent._subagent_tasks[payload["task_id"]]
    assert rec["done"] is False
    assert rec["status"] == "running"


# ── 3. subagent_opt 工具 ─────────────────────────────────

def test_subagent_opt_tool_name_and_schema():
    """subagent_opt 注册名 / schema name / op 枚举 / 必填参数。

    wait_all 为批量操作无需 task_id，因此必填参数仅 op（task_id 可选）。
    """
    assert SubagentOptFunc.name == "subagent_opt"
    schema = SubagentOptFunc.to_tool_schema()
    assert schema["function"]["name"] == "subagent_opt"
    assert schema["function"]["parameters"]["required"] == ["op"]
    assert set(schema["function"]["parameters"]["properties"]["op"]["enum"]) == {
        "read", "wait", "kill", "wait_all",
    }


def test_registry_discovers_subagent_opt():
    """注册表自动发现 subagent_opt；包导出别名可用。"""
    from src.tools.registry import ToolRegistry

    reg = ToolRegistry()
    tools = reg.get_tools()
    assert "subagent_opt" in tools
    assert tools["subagent_opt"] is SubagentOptFunc

    inst = reg.dispatch("subagent_opt", {"task_id": "sa-x", "op": "read"})
    assert isinstance(inst, SubagentOptFunc)
    assert getattr(SubagentOpt, "name", None) == "subagent_opt" or SubagentOpt is SubagentOptFunc


def test_display_name_mapping():
    """UI 显示名映射：subagent_opt → SubagentOpt。"""
    from src.tools._constants import TOOL_DISPLAY_NAME
    from src.tools.registry import get_tool_display_name

    assert TOOL_DISPLAY_NAME.get("subagent_opt") == "SubagentOpt"
    assert get_tool_display_name("subagent_opt") == "SubagentOpt"


async def test_subagent_opt_read():
    """op=read 返回状态与已产生结果，并标记 managed_by_tool。"""
    agent = _FakeMainAgent()
    rec = _make_rec(task=None, done=False, result="")
    agent._subagent_tasks["sa-1"] = rec

    func = SubagentOptFunc(task_id="sa-1", op="read")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["task_id"] == "sa-1"
    assert payload["status"] == "running"
    assert payload["output"] == ""
    assert payload["description"] == "任务A"
    assert rec["managed_by_tool"] is True


async def test_subagent_opt_read_completed():
    """op=read 已完成任务返回 completed 状态与结果。"""
    agent = _FakeMainAgent()
    rec = _make_rec(task=None, done=True, result="## 任务A\n结果", status="completed")
    agent._subagent_tasks["sa-1"] = rec

    func = SubagentOptFunc(task_id="sa-1", op="read")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["status"] == "completed"
    assert payload["output"] == "## 任务A\n结果"


async def test_subagent_opt_wait_completed_removes_record():
    """op=wait 已完成任务：返回结果并从任务表移除（避免重复插入用户消息）。"""
    agent = _FakeMainAgent()
    rec = _make_rec(task=None, done=True, result="## 任务A\n结果", status="completed")
    agent._subagent_tasks["sa-1"] = rec

    func = SubagentOptFunc(task_id="sa-1", op="wait")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["output"] == "## 任务A\n结果"
    assert "sa-1" not in agent._subagent_tasks


async def test_subagent_opt_wait_running_completes():
    """op=wait 运行中任务：等待完成并拿到结果（完成回调写入记录）。"""
    agent = _FakeMainAgent()
    task_id = "sa-wait"
    agent._subagent_tasks[task_id] = _make_rec(task=None)

    async def _work():
        await asyncio.sleep(0.01)
        agent._complete_subagent_task(task_id, "## 任务A\n最终结果")

    task = asyncio.ensure_future(_work())
    agent._subagent_tasks[task_id]["task"] = task

    func = SubagentOptFunc(task_id=task_id, op="wait", timeout=5)
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["status"] == "completed"
    assert payload["output"] == "## 任务A\n最终结果"
    assert task_id not in agent._subagent_tasks


async def test_subagent_opt_wait_timeout():
    """op=wait 超时（短 timeout）：返回错误提示且任务继续运行、记录保留。"""
    agent = _FakeMainAgent()
    task_id = "sa-slow"
    agent._subagent_tasks[task_id] = _make_rec(task=None)

    async def _slow():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_slow())
    agent._subagent_tasks[task_id]["task"] = task

    func = SubagentOptFunc(task_id=task_id, op="wait", timeout=0.05)
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "超时" in result
    assert not task.done()          # 任务继续运行（wait 只观察不干预）
    assert task_id in agent._subagent_tasks

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_subagent_opt_kill():
    """op=kill 取消后台任务并从任务表移除。"""
    agent = _FakeMainAgent()
    task_id = "sa-kill"
    agent._subagent_tasks[task_id] = _make_rec(task=None)

    async def _long_running():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_long_running())
    agent._subagent_tasks[task_id]["task"] = task

    func = SubagentOptFunc(task_id=task_id, op="kill")
    func.set_agent(agent)
    result = await func.execute()
    assert "已取消" in result
    assert task_id not in agent._subagent_tasks
    assert task.cancelled()


# ── 3.1 op=wait_all（批量等待所有后台 subagent） ─────────

async def test_subagent_opt_wait_all_empty():
    """op=wait_all 空表：返回 count=0 的空结果 JSON（不报错）。"""
    agent = _FakeMainAgent()
    func = SubagentOptFunc(op="wait_all")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["count"] == 0
    assert payload["completed"] == 0
    assert payload["running"] == 0
    assert payload["timed_out"] is False
    assert payload["tasks"] == []


async def test_subagent_opt_wait_all_all_done():
    """op=wait_all 全部已完成：返回每个任务结果 JSON 并从任务表移除。

    tasks 中每个元素是一个 subagent 的结果对象（与 op=wait 返回结构一致：
    task_id/description/agent_type/status/output）；已完成记录全部消费移除，
    避免 _process_subagent_tasks 再以用户消息重复插入。
    """
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-1"] = _make_rec(
        task=None, task_id="sa-1", done=True, result="## 任务A\n结果1",
        status="completed",
    )
    agent._subagent_tasks["sa-2"] = _make_rec(
        task=None, task_id="sa-2", done=True, result="## 任务B\n结果2",
        status="completed",
    )
    func = SubagentOptFunc(op="wait_all")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["count"] == 2
    assert payload["completed"] == 2
    assert payload["running"] == 0
    assert payload["timed_out"] is False
    assert [t["task_id"] for t in payload["tasks"]] == ["sa-1", "sa-2"]
    assert payload["tasks"][0]["status"] == "completed"
    assert payload["tasks"][0]["output"] == "## 任务A\n结果1"
    assert payload["tasks"][1]["output"] == "## 任务B\n结果2"
    # 已消费：全部记录移除
    assert agent._subagent_tasks == {}


async def test_subagent_opt_wait_all_mixed_done_running():
    """op=wait_all 混合完成/运行中：等待运行中任务完成后返回全部结果并移除。"""
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-1"] = _make_rec(
        task=None, task_id="sa-1", done=True, result="## 任务A\n结果1",
        status="completed",
    )
    task_id = "sa-2"
    agent._subagent_tasks[task_id] = _make_rec(task=None, task_id=task_id)

    async def _work():
        await asyncio.sleep(0.01)
        agent._complete_subagent_task(task_id, "## 任务B\n结果2")

    task = asyncio.ensure_future(_work())
    agent._subagent_tasks[task_id]["task"] = task

    func = SubagentOptFunc(op="wait_all", timeout=5)
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["count"] == 2
    assert payload["completed"] == 2
    assert payload["running"] == 0
    assert payload["timed_out"] is False
    outputs = {t["task_id"]: t["output"] for t in payload["tasks"]}
    assert outputs["sa-1"] == "## 任务A\n结果1"
    assert outputs["sa-2"] == "## 任务B\n结果2"
    assert agent._subagent_tasks == {}


async def test_subagent_opt_wait_all_timeout_keeps_running():
    """op=wait_all 超时：已完成任务消费移除，未完成任务保留并标记 managed_by_tool。

    超时后返回 timed_out=true 与当前状态（完成的任务给结果、未完成的 status=
    running）——模型可据此再次 wait_all / wait / kill 管理未完成任务。
    """
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-1"] = _make_rec(
        task=None, task_id="sa-1", done=True, result="## 任务A\n结果1",
        status="completed",
    )
    task_id = "sa-slow"
    agent._subagent_tasks[task_id] = _make_rec(task=None, task_id=task_id)

    async def _slow():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_slow())
    agent._subagent_tasks[task_id]["task"] = task

    func = SubagentOptFunc(op="wait_all", timeout=0.05)
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["count"] == 2
    assert payload["completed"] == 1
    assert payload["running"] == 1
    assert payload["timed_out"] is True
    # 已完成任务被消费移除
    assert "sa-1" not in agent._subagent_tasks
    # 未完成任务保留（标记 managed_by_tool，可再次管理）且未被取消
    assert task_id in agent._subagent_tasks
    assert agent._subagent_tasks[task_id]["managed_by_tool"] is True
    assert not task.done()
    # 返回结果中 running 任务 output 为空
    slow = next(t for t in payload["tasks"] if t["task_id"] == task_id)
    assert slow["status"] == "running"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_subagent_opt_wait_all_no_task_id_required():
    """op=wait_all 无需 task_id：from_args 缺省 task_id 可正常构造并执行。"""
    from src.tools.registry import ToolRegistry

    reg = ToolRegistry()
    inst = reg.dispatch("subagent_opt", {"op": "wait_all"})
    assert isinstance(inst, SubagentOptFunc)
    assert inst.task_id == ""
    assert inst.op == "wait_all"
    assert inst.timeout == SubagentOptFunc._DEFAULT_WAIT_TIMEOUT


async def test_subagent_opt_wait_all_marks_managed():
    """op=wait_all 标记所有任务 managed_by_tool（含已完成——先标记后消费）。

    与单个 wait 语义一致：结果由本工具主动获取后，
    _process_subagent_tasks 不再自动等待/插入其结果。
    """
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-1"] = _make_rec(
        task=None, task_id="sa-1", done=True, result="结果1", status="completed",
    )
    func = SubagentOptFunc(op="wait_all")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["completed"] == 1
    # 已完成记录已被消费移除（managed_by_tool 标记随记录一并移除，
    # 不影响 _process_subagent_tasks——表内已无该任务）
    assert agent._subagent_tasks == {}


async def test_subagent_opt_task_id_none_defensive():
    """task_id 传 None（模型传 null）：归一化为空串，不崩溃（P1 防御）。

    from_args 会把 None 传入 __init__（默认值不生效）；__init__ 归一化
    self.task_id = task_id or ""，execute 前缀校验拒绝并提示，零副作用。
    """
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-1"] = _make_rec(
        task=None, task_id="sa-1", done=True, result="结果1", status="completed",
    )
    for op in ("read", "wait", "kill"):
        func = SubagentOptFunc(task_id=None, op=op)
        func.set_agent(agent)
        result = await func.execute()
        assert result.startswith("(")
        assert "sa-xxx" in result
    # 未误操作：任务记录未被标记/移除（前缀校验在查表之前返回）
    rec = agent._subagent_tasks["sa-1"]
    assert "managed_by_tool" not in rec


async def test_subagent_opt_wait_all_ignores_task_id():
    """op=wait_all 传任意 task_id（含 bg-xxx）：批量语义，task_id 被忽略。"""
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-1"] = _make_rec(
        task=None, task_id="sa-1", done=True, result="结果1", status="completed",
    )
    func = SubagentOptFunc(task_id="bg-xxx", op="wait_all")
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["count"] == 1
    assert payload["tasks"][0]["task_id"] == "sa-1"


async def test_subagent_opt_wait_exception_keeps_record(monkeypatch):
    """op=wait 等待异常：返回错误提示且任务记录保留（避免结果永久丢失）。

    异常分支（非超时）不移除记录——否则任务仍在后台运行但记录已移除，
    _process_subagent_tasks 也无法再收集其结果。
    """
    agent = _FakeMainAgent()
    task_id = "sa-waiterr"
    agent._subagent_tasks[task_id] = _make_rec(task=None, task_id=task_id)

    async def _never_finish():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_never_finish())
    agent._subagent_tasks[task_id]["task"] = task

    async def _boom(*args, **kwargs):
        raise RuntimeError("模拟 wait 异常")

    monkeypatch.setattr("src.tools.subagent_opt.asyncio.wait", _boom)

    func = SubagentOptFunc(task_id=task_id, op="wait", timeout=5)
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "出错" in result
    # 记录保留、任务未被取消
    assert task_id in agent._subagent_tasks
    assert not task.done()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_subagent_opt_kill_completed_returns_result():
    """op=kill 已完成任务：不取消，返回结果提示并移除记录。

    避免模型误以为结果丢失（此前无条件返回"已取消"）。
    """
    agent = _FakeMainAgent()
    task_id = "sa-done"
    agent._subagent_tasks[task_id] = _make_rec(
        task=None, task_id=task_id, done=True, result="## 任务A\n完整结果",
        status="completed",
    )
    func = SubagentOptFunc(task_id=task_id, op="kill")
    func.set_agent(agent)
    result = await func.execute()
    assert "已完成" in result
    assert "完整结果" in result
    assert task_id not in agent._subagent_tasks


def test_subagent_opt_timeout_nan_defensive():
    """timeout=NaN 防御：按缺省超时（300s）处理，不产生 NaN 行为未定义。"""
    func = SubagentOptFunc(task_id="sa-1", op="wait", timeout=float("nan"))
    assert func.timeout == SubagentOptFunc._DEFAULT_WAIT_TIMEOUT
    func2 = SubagentOptFunc(task_id="sa-1", op="wait", timeout=float("inf"))
    assert func2.timeout is None  # inf > 0 → 无限等待


async def test_subagent_opt_wait_all_status_consistent_with_done():
    """op=wait_all status 与 done 标志一致：rec 内 status/done 不一致时以 done 为准。

    异常记录（status="completed" 但 done=False）返回 status=running，
    与 running 计数一致，避免 payload 内自相矛盾。
    """
    agent = _FakeMainAgent()
    agent._subagent_tasks["sa-x"] = _make_rec(
        task=None, task_id="sa-x", done=False, result="", status="completed",
    )
    func = SubagentOptFunc(op="wait_all", timeout=0.01)
    func.set_agent(agent)
    payload = json.loads(await func.execute())
    assert payload["completed"] == 0
    assert payload["running"] == 1
    assert payload["tasks"][0]["status"] == "running"


async def test_subagent_opt_unknown_task():
    """task_id 不存在时返回错误提示并引导使用 subagent 启动后台任务。"""
    agent = _FakeMainAgent()
    func = SubagentOptFunc(task_id="sa-nope", op="read")
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "sa-nope" in result
    assert "启动后台任务" in result


async def test_subagent_opt_rejects_bash_task_id():
    """task_id 非 sa- 前缀（如 bash 后台 bg-xxx）时拒绝，防止误操作 bash 任务。"""
    agent = _FakeMainAgent()
    # 模拟误传 bash 后台任务 id（bash 任务注册在 bash 专用表 _background_tasks；
    # 需 bash_opt 管理：kill 要杀进程树，且 subagent_opt 的 kill 不杀进程树——
    # 若误操作会 cancel task 导致 bash 子进程泄漏）。使用含真实运行中 task 的
    # 记录验证「立即拒绝、零副作用」。
    task_id = "bg-abc"

    async def _long_running():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_long_running())
    agent._background_tasks[task_id] = {"task": task, "done": False}
    for op in ("read", "wait", "kill"):
        func = SubagentOptFunc(task_id=task_id, op=op)
        func.set_agent(agent)
        result = await func.execute()
        assert result.startswith("(")
        assert "sa-xxx" in result
        assert "bash_opt" in result
    # bash 任务记录完全未被误操作：managed_by_tool 未设置、task 未被取消、未被移除
    assert "managed_by_tool" not in agent._background_tasks[task_id]
    assert not task.cancelled()
    assert task_id in agent._background_tasks
    # subagent 表始终为空（subagent_opt 未触达 bash 表记录）
    assert agent._subagent_tasks == {}

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_subagent_opt_without_agent():
    """未关联 Agent 上下文时返回提示。"""
    func = SubagentOptFunc(task_id="sa-1", op="read")
    result = await func.execute()
    assert result.startswith("(")
    assert "Agent" in result


async def test_subagent_opt_rejected_in_subagent():
    """subagent_opt 仅主 Agent 独有：SubAgent 内运行时强制拒绝。"""
    func = SubagentOptFunc(task_id="sa-1", op="read")
    func.set_agent(_bare_subagent())
    result = await func.execute()
    assert result.startswith("错误")
    assert "仅主 Agent" in result


async def test_subagent_opt_unknown_op():
    """未知 op 返回错误提示，且不标记 managed_by_tool（任务不失联）。"""
    agent = _FakeMainAgent()
    rec = _make_rec(task=None)
    agent._subagent_tasks["sa-1"] = rec
    func = SubagentOptFunc(task_id="sa-1", op="pause")
    func.set_agent(agent)
    result = await func.execute()
    assert "未知操作" in result
    # ★ P2（review 2026-08-18）：无效 op 不修改任务管理状态——否则
    #   _process_subagent_tasks 不再自动等待/插入结果，任务失联。
    assert "managed_by_tool" not in rec


def test_display_params():
    """display_params 摘要展示（UI 短参数）。"""
    assert SubagentOptFunc.display_params(
        {"task_id": "sa-1", "op": "wait"}
    ) == "'wait sa-1'"
    # wait_all 为批量操作：无 task_id，只显示 op
    assert SubagentOptFunc.display_params({"op": "wait_all"}) == "'wait_all'"


# ── 4. 独有性机制（仅主 Agent） ───────────────────────────

def test_subagent_tasks_table_only_on_main_agent():
    """_subagent_tasks 表仅主 Agent 独有：SubAgent 不持有。

    - BaseAgent（SubAgent 的基类）默认**不**初始化 _subagent_tasks——
      SubAgent 无法派发后台 subagent（白名单 + 运行时 isinstance 双保险），
      不该持有 subagent 后台任务表；
    - 主 Agent 形态（Agent 类 / 测试桩 _FakeMainAgent）显式初始化后才持有；
    - subagent_opt 的 hasattr(agent, '_subagent_tasks') 检查在 SubAgent 内
      天然失败（叠加 isinstance 检查双保险）。
    """
    from src.core.base_agent import BaseAgent

    plain = BaseAgent()          # SubAgent 的基类形态：无 subagent 表
    assert not hasattr(plain, "_subagent_tasks")

    main = _FakeMainAgent()      # 主 Agent 形态：显式初始化 subagent 表
    assert hasattr(main, "_subagent_tasks")
    assert main._subagent_tasks == {}

    # SubAgent 实例形态（__new__ 不跑 __init__，也不持有该表）
    sub = _bare_subagent()
    assert not hasattr(sub, "_subagent_tasks")


def test_exclusion_map_excludes_subagent_opt():
    """SubAgent 工具白名单全类型排除 subagent_opt（含 execute）。"""
    from src.core.subagent import _TOOL_EXCLUSION_MAP, _get_excluded_tools

    for agent_type in ("map", "review", "plan", "execute"):
        assert "subagent_opt" in _get_excluded_tools(agent_type), (
            f"{agent_type} 应排除 subagent_opt"
        )
        assert "subagent" in _TOOL_EXCLUSION_MAP[agent_type]


def test_can_use_subagent_opt():
    """Func.can_use：所有 SubAgent 类型拒绝 subagent_opt。"""
    from src.tools.base import Func

    for agent_type in ("map", "review", "plan", "execute"):
        ok, _err = Func.can_use("subagent_opt", agent_type=agent_type)
        assert ok is False, f"{agent_type} 应拒绝 subagent_opt"


# ── 5. review 2026-08-18 修复项 ──────────────────────────

async def test_background_cancel_writes_cancelled_status(monkeypatch):
    """后台 subagent 被取消时任务记录写入 status="cancelled"（read 可区分）。"""
    agent = _FakeMainAgent()
    func = SubagentFunc(description="任务A", prompt="指令")
    func.set_agent(agent)
    monkeypatch.setattr("src.tools.subagent.print_to_terminal", _async_noop)
    monkeypatch.setattr("src.core.display_target.get_output_publisher", lambda: None)

    entered = asyncio.Event()

    async def fake_run(self, specs, max_workers=None):
        entered.set()  # 已进入执行体，可安全 cancel（消除 sleep 竞态）
        await asyncio.sleep(100)

    monkeypatch.setattr(
        "src.core.parallel_executor.ParallelExecutor.run", fake_run,
    )

    result = await func.execute()
    task_id = json.loads(result)["task_id"]
    task = agent._subagent_tasks[task_id]["task"]
    # 等待后台任务进入 fake_run 后再 cancel（消除时间竞态：0.05s sleep
    # 在 CI 高负载下可能早于调度完成，导致 CancelledError 落在 try 块外）
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    await asyncio.wait_for(task, timeout=5)

    rec = agent._subagent_tasks[task_id]
    assert rec["status"] == "cancelled"
    assert "已被取消" in rec["result"]


def test_spawn_subagent_uses_spec_label():
    """spawner 支持 spec 唯一 label（后台 subagent 并发不互相覆盖）。"""
    from src.core.internal.agent._subagent_spawner import SubAgentSpawner
    from src.core.subagent import SubAgent

    created = {}

    def fake_factory(label, description, prompt, parent_agent, model=None,
                     agent_type="execute", dispatch_label=""):
        sa = SubAgent.__new__(SubAgent)
        sa.label = label
        sa.description = description
        sa.agent_type = agent_type
        created["label"] = label
        return sa

    class FakeDisplay:
        def add_agent(self, *args, **kwargs):
            pass

    class FakePort:
        def publish_event(self, event):
            pass

    spawner = SubAgentSpawner(
        parent_agent=object(), agent_factory=fake_factory, event_port=FakePort(),
    )
    sa = spawner.spawn(
        {"description": "d", "prompt": "p", "agent_type": "execute",
         "label": "sa-abc123"},
        0, FakeDisplay(),
    )
    assert created["label"] == "sa-abc123"
    assert sa.label == "sa-abc123"
    # 缺省 label（spec 未携带，如直接调用 spawner）→ agent-N
    created.clear()
    spawner.spawn(
        {"description": "d", "prompt": "p", "agent_type": "execute"},
        1, FakeDisplay(),
    )
    assert created["label"] == "agent-2"


def test_panel_refcount_last_stop_cleans_up():
    """SubAgentPanelController 引用计数：最后一个活跃方 stop 才真正清理面板。

    模拟两个后台 subagent 并发：各自 ensure_active/stop 配对，
    先完成者的 stop 不清面板（refs>0），最后一个 stop 才真正清理。
    """
    from src.tui._subagent_panel import SubAgentPanelController

    ctrl = SubAgentPanelController()
    try:
        # 后台 subagent A 启动
        ctrl.ensure_active()
        assert ctrl._active_refs == 1
        assert ctrl._active is True
        # 后台 subagent B 启动（已 active → 仅计数递增，不重复订阅）
        ctrl.ensure_active()
        assert ctrl._active_refs == 2

        # A 完成 → stop：refs=1，面板保持活跃（B 还在显示）
        ctrl.stop()
        assert ctrl._active_refs == 1
        assert ctrl._active is True

        # B 完成 → stop：refs=0，真正清理面板
        ctrl.stop()
        assert ctrl._active_refs == 0
        assert ctrl._active is False
    finally:
        # 清理残留订阅（防御）
        if ctrl._active:
            ctrl.stop()


def test_panel_refcount_does_not_go_negative():
    """引用计数防御：多余 stop 不产生负数（恢复为 0）。"""
    from src.tui._subagent_panel import SubAgentPanelController

    ctrl = SubAgentPanelController()
    try:
        ctrl.ensure_active()   # refs=1
        ctrl.stop()            # refs=0，真正清理
        ctrl.stop()            # 已 inactive → 直接返回，refs 保持 0
        assert ctrl._active_refs == 0
        assert ctrl._active is False
        # 再次激活恢复正常
        ctrl.ensure_active()   # refs=1
        assert ctrl._active is True
        ctrl.stop()
        assert ctrl._active is False
    finally:
        if ctrl._active:
            ctrl.stop()


def test_sandbox_reindex_records():
    """沙盒 reindex_records：按 predicate 重挂记录到新索引并重建 message_history。"""
    from src.core.sandbox_manager import SandboxManager

    sm = SandboxManager()
    sm.record_file_change("a.txt", None, "v1", 3, tool_name="write_file")
    sm.record_file_change("b.txt", None, "v2", 3, tool_name="write_file")
    # 不匹配的记录（索引 7）保持不动
    sm.record_file_change("c.txt", None, "v3", 7, tool_name="write_file")

    moved = sm.reindex_records(lambda r: r.message_index == 3, 5)
    assert moved == 2
    for rec in sm.get_all_file_changes():
        assert rec.message_index in (5, 7)
    assert 5 in sm.message_history
    assert 3 not in sm.message_history
    assert sm.get_current_message_index_safe() == 7  # max(7, 5) 不倒退

    # 无匹配 → 0，不重建
    moved2 = sm.reindex_records(lambda r: r.message_index == 99, 5)
    assert moved2 == 0
