"""工具改名测试：dispatch_agent → subagent（2026-08-17 用户需求）。

验证改名后的完整链路：
- 工具注册名 / schema 名 = "subagent"（旧名 "dispatch_agent" 不存在）
- 工具类名 SubagentFunc / 导出名 Subagent / 源码文件 subagent.py
- 显示名映射（TOOL_DISPLAY_NAME）、TUI 图标与类别映射
- param_formatter 参数提取
- 各 SubAgent 类型的工具排除集合
- 调度器 DAG 识别 subagent 节点（bash 独占过滤 / 提前返回检测）
- ToolCallbackChain 对 subagent 走直接 execute 路径
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.param_formatter import extract_key_params
from src.core.subagent import _get_excluded_tools
from src.core.tool_dag import ToolCallNode, ToolDAG
from src.core.tool_executor_async import ToolScheduler
from src.tools import get_tools, Subagent
from src.tools._constants import TOOL_DISPLAY_NAME
from src.tools.registry import get_tool_schemas
from src.tools.subagent import SubagentFunc
from src.tui._tool_icons import TOOL_CATEGORY_MAP, TOOL_ICONS

TOOLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "tools"


def _make_dag(specs: list[tuple[str, str]]) -> ToolDAG:
    """构造 ToolDAG：specs = [(tc_id, tool_name), ...]，全部 general 类别。"""
    dag = object.__new__(ToolDAG)
    dag._nodes = {}
    for tc_id, name in specs:
        dag._nodes[tc_id] = ToolCallNode(
            tc_id=tc_id, name=name, arguments={},
            parallel_safe=True, requires_terminal=False,
            tool_category="general",
        )
    return dag


def test_tool_registered_as_subagent():
    """注册表：subagent 已注册，旧名 dispatch_agent 不存在。"""
    tools = get_tools()
    assert "subagent" in tools
    assert "dispatch_agent" not in tools


def test_schema_name_is_subagent():
    """schema 工具名为 subagent（模型调用名）。"""
    schemas = get_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "subagent" in names
    assert "dispatch_agent" not in names


def test_tool_class_name_and_export():
    """工具类名 SubagentFunc，导出名 Subagent。"""
    assert Subagent is SubagentFunc
    assert SubagentFunc.name == "subagent"


def test_source_file_renamed():
    """源码文件改名：subagent.py 存在，dispatch_agent.py 不存在。"""
    assert (TOOLS_ROOT / "subagent.py").is_file()
    assert not (TOOLS_ROOT / "dispatch_agent.py").exists()


def test_display_name_mapping():
    """TOOL_DISPLAY_NAME 映射 subagent → Task（旧名不再映射）。"""
    assert TOOL_DISPLAY_NAME["subagent"] == "Task"
    assert "dispatch_agent" not in TOOL_DISPLAY_NAME


def test_tui_icon_and_category_mapping():
    """TUI 图标/类别映射使用新工具名。"""
    assert "subagent" in TOOL_ICONS
    assert "subagent" in TOOL_CATEGORY_MAP
    assert TOOL_CATEGORY_MAP["subagent"] == "agent"
    assert "dispatch_agent" not in TOOL_ICONS
    assert "dispatch_agent" not in TOOL_CATEGORY_MAP


def test_param_formatter_extracts_subagent_params():
    """param_formatter 对 subagent 提取 description/type 关键参数。"""
    out = extract_key_params("subagent", {
        "description": "审查 API 层", "prompt": "审吧", "type": "review",
    })
    assert "审查 API 层" in out
    assert "review" in out


def test_exclusion_map_uses_subagent():
    """各 SubAgent 类型排除 subagent（而非旧名 dispatch_agent）。"""
    for agent_type in ("map", "review", "plan", "execute"):
        excluded = _get_excluded_tools(agent_type)
        assert "subagent" in excluded, f"{agent_type} 应排除 subagent"
        assert "dispatch_agent" not in excluded, f"{agent_type} 不应再排除旧名"


def test_scheduler_only_subagent_remaining():
    """提前返回检测：仅剩 subagent 节点时返回列表。"""
    scheduler = ToolScheduler()
    dag = _make_dag([("c1", "subagent"), ("c2", "subagent")])
    remaining = scheduler._only_subagent_remaining(dag)
    assert remaining is not None
    assert {r["name"] for r in remaining} == {"subagent"}
    assert {r["id"] for r in remaining} == {"c1", "c2"}


def test_scheduler_only_subagent_remaining_other_tool_blocks():
    """提前返回检测：混入其他工具时不触发。"""
    scheduler = ToolScheduler()
    dag = _make_dag([("c1", "subagent"), ("c2", "read_file")])
    assert scheduler._only_subagent_remaining(dag) is None


def test_find_next_layer_bash_filter_allows_subagent():
    """bash 独占过滤：bash 运行中仅 subagent 可并行（新工具名）。"""
    scheduler = ToolScheduler()
    scheduler._running_bash_ids = {"bash_1"}
    dag = _make_dag([("c1", "subagent")])
    target = scheduler._find_next_layer(dag, [["c1"]], is_outermost=True)
    assert target == ["c1"]


def test_find_next_layer_bash_filter_blocks_other_tools():
    """bash 独占过滤：bash 运行中普通工具被拦截（空层返回）。"""
    scheduler = ToolScheduler()
    scheduler._running_bash_ids = {"bash_1"}
    dag = _make_dag([("c1", "read_file")])
    target = scheduler._find_next_layer(dag, [["c1"]], is_outermost=True)
    assert target == []


def test_run_tool_method_subagent_direct_execute():
    """ToolCallbackChain 对 subagent 走直接 execute（不捕获 stdout）。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain

    agent = MagicMock()
    agent._display_port = MagicMock()
    chain = ToolCallbackChain(agent)

    func = MagicMock()
    func.execute = AsyncMock(return_value="ok")
    func.display = AsyncMock(return_value="displayed")

    tc = {"id": "call_x", "name": "subagent", "arguments": {}}
    with patch.object(chain, "_run_with_capture") as mock_capture:
        result = asyncio.run(chain._run_tool_method(func, tc))
    assert result == "ok"
    func.execute.assert_awaited_once()
    func.display.assert_not_called()
    mock_capture.assert_not_called()
