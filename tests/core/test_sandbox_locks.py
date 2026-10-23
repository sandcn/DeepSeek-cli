"""文件沙盒 Agent 锁与工具调用审计回归测试。

覆盖用户需求（2026-10）：
  - 同一路径（含目录子树）同一时刻只允许一个 Agent 持有写锁；
  - 其他 Agent 写入返回「写入被锁定」错误（含持锁 Agent 标识），不落盘；
  - 读工具不抢写锁，但记录审计（带当前锁持有者）；
  - 锁随 Agent 生命周期释放（SubAgent 结束 / 任务记录移除 / 主 Agent 轮次结束），
    或由持锁方经 subagent_opt(op="release", file=...) 主动释放；
  - 锁保存在 SandboxManager 内存 map 中（路径 → 锁信息），沙盒视图可见。
"""

from __future__ import annotations

import pytest

from src.core.commands._sandbox_cmd import (
    build_record_history_entries,
    build_sandbox_sections,
    sandbox_signature,
)
from src.core.sandbox_manager import (
    SandboxManager,
    clear_current_agent_id,
    get_sandbox_manager,
    resolve_agent_id,
    set_sandbox_manager,
)
from src.tools.cp import CpFunc
from src.tools.mkdir import MkdirFunc
from src.tools.mv import MvFunc
from src.tools.read import ReadFunc
from src.tools.rm import RmFunc
from src.tools.subagent_opt import SubagentOptFunc
from src.tools.write import WriteFunc


class _Agent:
    """最小 Agent 桩：只提供沙盒锁需要的 agent_id / label。"""

    def __init__(self, agent_id: str = "", label: str = ""):
        self.agent_id = agent_id
        self.label = label


@pytest.fixture(autouse=True)
def sandbox():
    """每个用例使用独立 SandboxManager，结束后恢复全局状态。"""
    previous = get_sandbox_manager()
    manager = SandboxManager()
    set_sandbox_manager(manager)
    clear_current_agent_id()
    try:
        yield manager
    finally:
        manager.clear()
        set_sandbox_manager(previous)
        clear_current_agent_id()


# ── Agent 标识解析 ───────────────────────────────────────


def test_resolve_agent_id_prefers_agent_id_then_label():
    assert resolve_agent_id(None) == "main"
    assert resolve_agent_id(_Agent(label="sa-x")) == "sa-x"
    assert resolve_agent_id(_Agent(agent_id="execute-1", label="sa-x")) == "execute-1"
    assert resolve_agent_id(object()) == "main"


# ── 锁粒度 / 目录覆盖 ───────────────────────────────────


def test_lock_conflict_message_matches_required_template(sandbox, tmp_path):
    """锁冲突提示必须与用户指定措辞完全一致（含 subagent_opt release 指引）。"""
    path = str(tmp_path / "a.txt")
    assert sandbox.acquire_locks("sa-xx", [path], "write") == ""
    message = sandbox.acquire_locks("main", [path], "write")
    expected = (
        f"(写入被锁定: {path} 已被 agent sa-xx 锁定"
        f"（write 于 {path}），"
        f"无法执行 write；请等待该 agent 结束 / 被移除释放锁，"
        f"或由持锁方调用 "
        f"subagent_opt(op=\"release\", file=\"{path}\") 主动释放)"
    )
    assert message == expected


def test_locks_are_per_path(sandbox, tmp_path):
    path_a = str(tmp_path / "a.txt")
    path_b = str(tmp_path / "b.txt")
    assert sandbox.acquire_locks("agent-a", [path_a], "write") == ""
    assert sandbox.acquire_locks("agent-b", [path_b], "write") == ""
    assert sandbox.get_lock_owner(path_a) == "agent-a"
    assert sandbox.get_lock_owner(path_b) == "agent-b"


def test_directory_lock_covers_descendants(sandbox, tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    child = directory / "child.txt"

    assert sandbox.acquire_locks("agent-a", [str(directory)], "mkdir") == ""
    error = sandbox.acquire_locks("agent-b", [str(child)], "write")
    assert "写入被锁定" in error
    assert "agent-a" in error
    # 子路径查询返回覆盖它的目录锁持有者
    assert sandbox.get_lock_owner(str(child)) == "agent-a"


def test_child_lock_blocks_parent_directory_lock(sandbox, tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    child = directory / "child.txt"

    assert sandbox.acquire_locks("agent-b", [str(child)], "write") == ""
    error = sandbox.acquire_locks("agent-a", [str(directory)], "mkdir")
    assert "写入被锁定" in error
    assert "agent-b" in error


# ── 工具接入 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_tool_denied_for_other_agent(sandbox, tmp_path):
    path = tmp_path / "a.txt"

    first = WriteFunc(str(path), "one")
    first.set_agent(_Agent("agent-a"))
    assert "写入成功" in await first.execute()

    second = WriteFunc(str(path), "two")
    second.set_agent(_Agent("agent-b"))
    result = await second.execute()
    assert "写入被锁定" in result
    assert "agent-a" in result
    assert path.read_text(encoding="utf-8") == "one"
    assert sandbox.get_lock_owner(str(path)) == "agent-a"
    assert any(
        record.agent_id == "agent-a"
        for record in sandbox.get_all_file_changes()
    )

    # 持锁 Agent 自身仍可继续写
    third = WriteFunc(str(path), "three")
    third.set_agent(_Agent("agent-a"))
    assert "写入成功" in await third.execute()


@pytest.mark.asyncio
async def test_main_write_blocks_subagent_until_explicit_release(sandbox, tmp_path):
    """用户复现场景：主 Agent 先写 a.txt，SubAgent 再写必须被拒绝。

    主 Agent 的锁不得在轮次 / 工具调用结束时自动释放；只能由持锁方经
    subagent_opt(op="release") 主动释放（或 Agent 删除 / 沙盒清空）。
    """
    path = tmp_path / "a.txt"

    main_write = WriteFunc(str(path), "main")
    main_write.set_agent(_Agent("main"))
    assert "写入成功" in await main_write.execute()

    sub_write = WriteFunc(str(path), "sub")
    sub_write.set_agent(_Agent("sa-abc123"))
    result = await sub_write.execute()
    assert "写入被锁定" in result
    assert "main" in result
    assert path.read_text(encoding="utf-8") == "main"

    release = SubagentOptFunc(op="release", file=str(path))
    release.set_agent(_Agent("main"))
    assert "已释放" in await release.execute()

    assert "写入成功" in await sub_write.execute()
    assert path.read_text(encoding="utf-8") == "sub"


@pytest.mark.asyncio
async def test_scheduler_executes_tool_with_owner_agent(sandbox, tmp_path):
    """全局 DAG 中被其他 Agent 调度循环拾取的节点，仍按节点归属 Agent 执行。

    回归：主 Agent 的调度循环若以自身身份执行 SubAgent 的 write 节点，
    文件沙盒锁会按同 owner 放行，SubAgent 写入不会报错。
    """
    from src.core.adapters.tools import get_default_tool_registry
    from src.core.tool_executor_async import ToolScheduler

    path = tmp_path / "owner.txt"
    assert sandbox.acquire_locks("main", [str(path)], "write") == ""

    scheduler = ToolScheduler(registry=get_default_tool_registry())
    tc = {
        "id": "tc-owner-1",
        "name": "write",
        "arguments": {"file_path": str(path), "content": "sub"},
    }
    # 模拟该节点由 SubAgent 加入 DAG，却被主 Agent 的调度循环拾取执行
    scheduler._tool_agent_refs["tc-owner-1"] = _Agent("sa-owner-1")
    try:
        _tid, output, _success = await scheduler._execute_one_async(
            tc, agent_ref=_Agent("main"),
            on_before=None, on_after=None, run_method=None,
        )
    finally:
        scheduler._reset_global_state()

    assert "写入被锁定" in output
    assert "main" in output
    assert not path.exists()


@pytest.mark.asyncio
async def test_main_agent_lock_survives_round_end(sandbox, tmp_path, monkeypatch):
    """主 Agent 的锁在 run() 轮次结束（含中断退出）后仍然保持。"""
    import src.core.agent as agent_mod
    from src.core.agent import Agent

    class _Port:
        async def reset(self):
            return None

    class _Pipeline:
        async def run_round_async(self, ctx):
            return True  # 中断分支：立即结束本轮

    class _Ctx:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(agent_mod, "PipelineContext", _Ctx)
    agent = object.__new__(Agent)
    agent.agent_id = "main"
    agent._interrupt_port = _Port()
    agent._pipeline = _Pipeline()

    path = str(tmp_path / "round.txt")
    assert sandbox.acquire_locks("main", [path], "write") == ""
    assert await agent.run() is True
    assert sandbox.get_lock_owner(path) == "main"


@pytest.mark.asyncio
async def test_cp_mv_rm_mkdir_respect_locks(sandbox, tmp_path):
    # cp：目标被 agent-a 锁定 → agent-b 复制失败，目标内容不变
    source = tmp_path / "src.txt"
    source.write_text("source", encoding="utf-8")
    destination = tmp_path / "dst.txt"
    destination.write_text("destination", encoding="utf-8")
    assert sandbox.acquire_locks("agent-a", [str(destination)], "write") == ""
    cp_tool = CpFunc(str(source), str(destination))
    cp_tool.set_agent(_Agent("agent-b"))
    assert "写入被锁定" in await cp_tool.execute()
    assert destination.read_text(encoding="utf-8") == "destination"

    # mv：目标被锁定 → 移动失败，源仍在、目标未创建
    move_source = tmp_path / "move-src.txt"
    move_source.write_text("move", encoding="utf-8")
    move_destination = tmp_path / "move-dst.txt"
    assert sandbox.acquire_locks("agent-a", [str(move_destination)], "write") == ""
    mv_tool = MvFunc(str(move_source), str(move_destination))
    mv_tool.set_agent(_Agent("agent-b"))
    assert "写入被锁定" in await mv_tool.execute()
    assert move_source.exists()
    assert not move_destination.exists()

    # rm：路径被锁定 → 删除失败，文件仍在
    remove_path = tmp_path / "remove.txt"
    remove_path.write_text("remove", encoding="utf-8")
    assert sandbox.acquire_locks("agent-a", [str(remove_path)], "write") == ""
    rm_tool = RmFunc(str(remove_path))
    rm_tool.set_agent(_Agent("agent-b"))
    assert "写入被锁定" in await rm_tool.execute()
    assert remove_path.exists()

    # mkdir：目录路径被锁定 → 创建失败，目录不存在
    new_dir = tmp_path / "new-dir"
    assert sandbox.acquire_locks("agent-a", [str(new_dir)], "mkdir") == ""
    mkdir_tool = MkdirFunc(str(new_dir))
    mkdir_tool.set_agent(_Agent("agent-b"))
    assert "写入被锁定" in await mkdir_tool.execute()
    assert not new_dir.exists()


@pytest.mark.asyncio
async def test_file_change_records_use_current_agent_context(sandbox, tmp_path):
    from src.core.sandbox_manager import set_current_agent_id

    source = tmp_path / "ctx-src.txt"
    source.write_text("ctx", encoding="utf-8")
    destination = tmp_path / "ctx-dst.txt"

    set_current_agent_id("agent-ctx")
    cp_tool = CpFunc(str(source), str(destination))
    cp_tool.set_agent(_Agent("fallback-agent"))
    assert "复制成功" in await cp_tool.execute()

    records = [
        record for record in sandbox.get_all_file_changes()
        if record.file_path == str(destination)
    ]
    assert records
    assert records[-1].agent_id == "agent-ctx"


@pytest.mark.asyncio
async def test_read_allowed_and_audited_under_foreign_lock(sandbox, tmp_path):
    path = tmp_path / "read.txt"
    path.write_text("hello", encoding="utf-8")
    assert sandbox.acquire_locks("agent-a", [str(path)], "write") == ""

    read_tool = ReadFunc(str(path))
    read_tool.set_agent(_Agent("agent-b"))
    output = await read_tool.execute()
    assert "hello" in output

    reads = [
        record for record in sandbox.get_tool_audit_records()
        if record["action"] == "read"
    ]
    assert reads
    assert reads[-1]["agent_id"] == "agent-b"
    assert reads[-1]["lock_owner"] == "agent-a"


# ── 释放语义 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_requires_holder(sandbox, tmp_path):
    path = tmp_path / "release.txt"
    assert sandbox.acquire_locks("main", [str(path)], "write") == ""

    release = SubagentOptFunc(op="release", file=str(path))
    release.set_agent(_Agent("main"))
    message = await release.execute()
    assert "已释放" in message
    assert sandbox.get_lock_owner(str(path)) == ""

    # 非持有者释放：不报错但不释放任何锁
    other = SubagentOptFunc(op="release", file=str(path))
    other.set_agent(_Agent("other"))
    message = await other.execute()
    assert "未持有" in message


@pytest.mark.asyncio
async def test_release_requires_file_parameter(sandbox, tmp_path):
    path = tmp_path / "need-file.txt"
    assert sandbox.acquire_locks("main", [str(path)], "write") == ""

    release = SubagentOptFunc(op="release")
    release.set_agent(_Agent("main"))
    message = await release.execute()
    assert "file" in message
    assert sandbox.get_lock_owner(str(path)) == "main"


@pytest.mark.asyncio
async def test_release_without_agent_context_rejected(sandbox, tmp_path):
    path = tmp_path / "no-agent.txt"
    assert sandbox.acquire_locks("main", [str(path)], "write") == ""
    release = SubagentOptFunc(op="release", file=str(path))
    message = await release.execute()
    assert "关联 Agent" in message
    assert sandbox.get_lock_owner(str(path)) == "main"


@pytest.mark.asyncio
async def test_release_requires_caller_to_hold_lock(sandbox, tmp_path):
    """主 Agent 也没有特权：不能释放 SubAgent（sa-xx）持有的锁。"""
    path = tmp_path / "sub-locked.txt"
    assert sandbox.acquire_locks("sa-abc123", [str(path)], "write") == ""

    # 传 task_id 也不能代持锁方释放
    release = SubagentOptFunc(op="release", task_id="sa-abc123", file=str(path))
    release.set_agent(_Agent("main"))
    message = await release.execute()
    assert "未持有" in message or "没有特权" in message
    assert sandbox.get_lock_owner(str(path)) == "sa-abc123"

    # 仅传 file 同样不能释放他人的锁
    release2 = SubagentOptFunc(op="release", file=str(path))
    release2.set_agent(_Agent("main"))
    message2 = await release2.execute()
    assert "未持有" in message2
    assert sandbox.get_lock_owner(str(path)) == "sa-abc123"


@pytest.mark.asyncio
async def test_release_directory_releases_descendant_locks(sandbox, tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    child = directory / "child.txt"
    assert sandbox.acquire_locks(
        "main", [str(directory), str(child)], "mkdir",
    ) == ""
    assert len(sandbox.get_locks()) == 2

    release = SubagentOptFunc(op="release", file=str(directory))
    release.set_agent(_Agent("main"))
    message = await release.execute()
    assert "已释放 2 个锁" in message
    assert sandbox.get_locks() == []


def test_release_agent_locks_is_idempotent(sandbox, tmp_path):
    path_a = str(tmp_path / "a")
    path_b = str(tmp_path / "b")
    assert sandbox.acquire_locks("sub-1", [path_a], "write") == ""
    assert sandbox.acquire_locks("sub-1", [path_b], "write") == ""
    assert sandbox.release_agent_locks("sub-1") == 2
    assert sandbox.release_agent_locks("sub-1") == 0
    assert sandbox.get_locks() == []


def test_agent_lifecycle_release_helpers(sandbox, tmp_path):
    from src.core.agent import Agent
    from src.core.subagent import SubAgent

    sub_path = str(tmp_path / "sub.txt")
    assert sandbox.acquire_locks("sub-1", [sub_path], "write") == ""
    subagent = object.__new__(SubAgent)
    subagent.agent_id = "sub-1"
    subagent.label = "sub-1"
    subagent._release_sandbox_locks()
    assert sandbox.get_lock_owner(sub_path) == ""

    main_path = str(tmp_path / "main.txt")
    assert sandbox.acquire_locks("main", [main_path], "write") == ""
    agent = object.__new__(Agent)
    agent._release_sandbox_locks()
    assert sandbox.get_lock_owner(main_path) == ""


# ── 审计 / 视图 ──────────────────────────────────────────


def test_denied_call_is_audited(sandbox, tmp_path):
    path = str(tmp_path / "denied.txt")
    assert sandbox.acquire_locks("agent-a", [path], "write") == ""
    assert sandbox.acquire_locks("agent-b", [path], "write")
    denied = [
        record for record in sandbox.get_tool_audit_records()
        if record["action"] == "denied"
    ]
    assert denied
    assert denied[-1]["agent_id"] == "agent-b"
    assert denied[-1]["lock_owner"] == "agent-a"
    assert denied[-1]["success"] is False


def test_sandbox_views_include_locks_and_audit(sandbox, tmp_path):
    path = str(tmp_path / "ui.txt")
    assert sandbox.acquire_locks("agent-a", [path], "write") == ""

    sections = build_sandbox_sections(sandbox)
    assert any(section["title"] == "文件锁" for section in sections)
    assert any(section["title"] == "最近工具调用" for section in sections)

    records = build_record_history_entries(sandbox)
    assert any(row.get("is_audit") for row in records)
    assert any(row.get("agent_id") == "agent-a" for row in records)

    before = sandbox_signature(sandbox)
    assert sandbox.acquire_locks("agent-b", [str(tmp_path / "ui2.txt")], "write") == ""
    assert sandbox_signature(sandbox) != before


def test_subagent_opt_release_schema_and_tool_policy():
    schema = SubagentOptFunc.to_tool_schema()
    properties = schema["function"]["parameters"]["properties"]
    assert "file" in properties
    assert "release" in properties["op"]["enum"]

    from src.core.agent_types import excluded_tools

    for agent_type in ("map", "review", "plan", "execute"):
        assert "subagent_opt" in excluded_tools(agent_type)
