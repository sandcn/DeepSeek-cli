"""bash_task → bash_opt 工具改名验证测试（2026-08-18）。

工具 ``bash_task`` 整体改名为 ``bash_opt``（彻底改名）：
  - 工具注册名 / schema name：``bash_task`` → ``bash_opt``
  - 模块文件：``src/tools/bash_task.py`` → ``src/tools/bash_opt.py``
  - 类名：``BashTaskFunc`` → ``BashOptFunc``（导出别名 ``BashTask`` → ``BashOpt``）
  - UI 显示名映射：``"bash_task": "BashTask"`` → ``"bash_opt": "BashOpt"``
  - subagent 各类型工具排除表（map/review/plan）同步更新
  - README 与源码注释中的旧名全部清理

本文件验证：旧名零残留、新名全链路生效、改名不影响工具行为（冒烟）。
"""

from __future__ import annotations

import json
from pathlib import Path

import src.tools as tools_pkg
from src.tools import BashOpt
from src.tools.bash_opt import BashOptFunc


# ── 1. 工具名与 schema ───────────────────────────────────

def test_tool_name_is_bash_opt():
    """工具类注册名与 schema name 一致为 bash_opt。"""
    assert BashOptFunc.name == "bash_opt"
    schema = BashOptFunc.to_tool_schema()
    assert schema["function"]["name"] == "bash_opt"
    required = schema["function"]["parameters"]["required"]
    assert required == ["task_id", "op"]
    assert set(schema["function"]["parameters"]["properties"]["op"]["enum"]) == {
        "read", "wait", "kill", "stdin", "keys",
    }


def test_old_module_and_export_removed():
    """旧名零残留：模块文件、包导出、类名均不再存在。"""
    assert not Path("src/tools/bash_task.py").exists()
    assert Path("src/tools/bash_opt.py").exists()
    assert not hasattr(tools_pkg, "BashTask")
    assert not hasattr(tools_pkg, "BashTaskFunc")
    assert not hasattr(tools_pkg, "bash_task")
    assert getattr(tools_pkg, "BashOpt") is BashOptFunc
    assert "BashOpt" in tools_pkg.__all__
    assert "BashTask" not in tools_pkg.__all__


def test_registry_discovers_bash_opt():
    """注册表自动发现：注册 bash_opt，不再注册 bash_task。"""
    from src.tools.registry import ToolRegistry

    reg = ToolRegistry()
    tools = reg.get_tools()
    assert "bash_opt" in tools
    assert tools["bash_opt"] is BashOptFunc
    assert "bash_task" not in tools

    schemas = reg.get_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "bash_opt" in names
    assert "bash_task" not in names

    # dispatch 按新名可取到工具实例
    inst = reg.dispatch("bash_opt", {"task_id": "bg-x", "op": "read"})
    assert isinstance(inst, BashOptFunc)


def test_display_name_mapping():
    """UI 显示名映射：bash_opt → BashOpt，bash_task 键已移除。"""
    from src.tools._constants import TOOL_DISPLAY_NAME
    from src.tools.registry import get_tool_display_name

    assert TOOL_DISPLAY_NAME.get("bash_opt") == "BashOpt"
    assert "bash_task" not in TOOL_DISPLAY_NAME
    assert get_tool_display_name("bash_opt") == "BashOpt"


# ── 2. subagent 工具排除表 ───────────────────────────────

def test_subagent_exclusion_map_uses_bash_opt():
    """map/plan 排除表使用 bash_opt；review 已放开（只读查询用）；旧名不在任何排除表中。"""
    from src.core.subagent import _TOOL_EXCLUSION_MAP, _get_excluded_tools

    for agent_type in ("map", "plan"):
        excluded = _get_excluded_tools(agent_type)
        assert "bash_opt" in excluded, f"{agent_type} 应排除 bash_opt"
        assert "bash_task" not in excluded
    # ★ 2026-08-20（用户需求）：review 放开 bash/bash_opt（可做只读查询，
    #   但提示词强制禁止用 bash 修改文件）
    assert "bash_opt" not in _get_excluded_tools("review")
    assert "bash" not in _get_excluded_tools("review")
    # execute 保留 bash_opt（不在排除表中）
    assert "bash_opt" not in _get_excluded_tools("execute")
    for excluded in _TOOL_EXCLUSION_MAP.values():
        assert "bash_task" not in excluded


def test_can_use_respects_new_name():
    """Func.can_use 按新名生效：map 拒绝、execute 放行。"""
    assert Func_can_use("bash_opt", "map") is False
    assert Func_can_use("bash_opt", "execute") is True


def Func_can_use(tool_name: str, agent_type: str) -> bool:
    from src.tools.base import Func

    ok, _err = Func.can_use(tool_name, agent_type=agent_type)
    return ok


# ── 3. 行为冒烟：改名不影响工具逻辑 ──────────────────────

class _FakeAgent:
    """最小 Agent 桩：提供 bash 表 _background_tasks 与 subagent 表 _subagent_tasks。

    两表独立（与 BaseAgent 设计一致）：bash_opt 只查 _background_tasks，
    subagent_opt 只查 _subagent_tasks。
    """

    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


async def test_execute_read_smoke():
    """op=read 正常工作并标记 managed_by_tool（行为不变）。"""
    rec = {
        "command": "echo hi",
        "read_buffer": "hi",
        "status": "running",
        "done": False,
    }
    agent = _FakeAgent({"bg-1": rec})
    func = BashOptFunc(task_id="bg-1", op="read")
    func.set_agent(agent)

    payload = json.loads(await func.execute())
    assert payload["task_id"] == "bg-1"
    assert payload["command"] == "echo hi"
    assert payload["status"] == "running"
    assert payload["output"] == "hi"
    assert rec["read_buffer"] == ""      # read 消费后清空
    assert rec["managed_by_tool"] is True


async def test_execute_without_agent_context():
    """未关联 Agent 上下文时返回提示（行为不变）。"""
    func = BashOptFunc(task_id="bg-1", op="read")
    result = await func.execute()
    assert result.startswith("(")
    assert "Agent" in result


async def test_execute_unknown_task_id():
    """task_id 不存在时返回错误提示并引导使用 bash background=True。"""
    agent = _FakeAgent({})
    func = BashOptFunc(task_id="bg-nope", op="wait")
    func.set_agent(agent)
    result = await func.execute()
    assert result.startswith("(")
    assert "bg-nope" in result
    assert "background=True" in result


async def test_execute_rejects_subagent_task_id():
    """bash_opt 收到 subagent 后台 task_id（sa-xxx）立即拒绝，不误操作。

    bash 与 subagent 后台任务分表独立（_background_tasks / _subagent_tasks）：
    即使同名 subagent 记录确实存在于 subagent 表，bash_opt 查 bash 表也
    不可得；叠加 task_id 前缀校验（"bg-"），误传 sa-xxx 时直接在查表前
    返回错误——杜绝误 cancel subagent 任务 / 误标 managed_by_tool 失联。
    """
    import asyncio

    agent = _FakeAgent({})
    # 模拟真实的 subagent 后台记录（含运行中的 asyncio task），
    # 注册在**独立的 subagent 表** _subagent_tasks 中
    task_id = "sa-7e79af9c9586"

    async def _long_running():
        await asyncio.sleep(100)

    task = asyncio.ensure_future(_long_running())
    agent._subagent_tasks[task_id] = {
        "task": task,
        "command": "subagent(解析 user.py)",
        "description": "解析 user.py",
        "agent_type": "map",
        "done": False,
        "result": "",
        "status": "running",
        "read_buffer": "",
    }

    for op in ("read", "wait", "kill", "stdin", "keys"):
        func = BashOptFunc(task_id=task_id, op=op)
        func.set_agent(agent)
        result = await func.execute()
        assert result.startswith("(")
        assert "bg-xxx" in result
        assert "subagent_opt" in result

    # bash 表保持为空（bash_opt 未触达 subagent 表），
    # subagent 任务完全未被误操作：未标记 managed_by_tool、未取消、记录保留
    assert agent._background_tasks == {}
    rec = agent._subagent_tasks[task_id]
    assert "managed_by_tool" not in rec
    assert not task.cancelled()
    assert task_id in agent._subagent_tasks

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_display_params():
    """display_params 摘要展示（UI 短参数）。"""
    assert BashOptFunc.display_params(
        {"task_id": "bg-1", "op": "read"}
    ) == "'read bg-1'"


async def test_execute_task_id_none_defensive():
    """task_id 传 None（模型传 null）：归一化为空串，不崩溃（与 subagent_opt 对称防御）。

    from_args 会把 None 传入 __init__（默认值不生效）；__init__ 归一化
    self.task_id = task_id or ""，execute 前缀校验拒绝并提示，零副作用。
    """
    agent = _FakeAgent({})
    for op in ("read", "wait", "kill", "stdin", "keys"):
        func = BashOptFunc(task_id=None, op=op)
        func.set_agent(agent)
        result = await func.execute()
        assert result.startswith("(")
        assert "bg-xxx" in result
        assert "subagent_opt" in result
    # 未误操作：bash 表未被触碰、无 managed_by_tool 标记
    assert agent._background_tasks == {}


def test_timeout_nan_defensive():
    """timeout=NaN 防御：按缺省超时（300s）处理，不产生 NaN 行为未定义。"""
    func = BashOptFunc(task_id="bg-1", op="wait", timeout=float("nan"))
    assert func.timeout == BashOptFunc._DEFAULT_WAIT_TIMEOUT
    func2 = BashOptFunc(task_id="bg-1", op="wait", timeout=float("inf"))
    assert func2.timeout is None  # inf > 0 → 无限等待
