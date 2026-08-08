"""Phase B/C — 分组工具卡（grouped tool use）+ 折叠摘要测试（2026-08-08）。

对齐 Claude Code：
  - 同一 assistant 消息内 ≥2 个**连续同类**分组工具（read_file/grep/find/
    glob/search）合并为一张摘要卡（open_tool_group）——成员输出丢弃、成员
    close 聚合群组状态（running/fail/done）；
  - 单次调用（长度 1）不分组，仍走单卡；bash/write/edit 不分组；
  - 可折叠工具（collapsed_read_search）标题 `Read N files` + 末成员 detail
    提示行，无成员全文输出；
  - Dispatcher 抑制分组成员单卡 ToolOpenCmd（群组卡已打开）。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui.app.toolcard import tool_card_lines
from src.tui._const import (
    ToolOpenCmd,
    ToolGroupOpenCmd,
    ToolOutputCmd,
    ToolCloseCmd,
    DisplayMsgsCmd,
)
from src.tui._dispatcher import EventDispatcher
from src.tui.events.event_types import (
    ToolBatchStartedEvent,
    ToolGroupPlannedEvent,
    ToolStartedEvent,
    ToolDoneEvent,
)


def _model() -> AppModel:
    m = AppModel()
    m.width = 60
    return m


def _replay(m, tool_calls, content=None):
    """经 DisplayMsgsCmd 回放单条 assistant 消息（含 tool_calls）。"""
    apply_cmd(m, DisplayMsgsCmd(messages=[{
        "role": "assistant", "content": content, "tool_calls": tool_calls,
    }], speed=0))


def _tc(tool_id, name, args):
    """构造消息历史格式的 tool_call dict。"""
    return {
        "id": tool_id, "type": "function",
        "function": {"name": name, "arguments": args},
    }


# ═══════════════════════════════════════════════════════════
# 模型层群组生命周期（open/close/flush）
# ═══════════════════════════════════════════════════════════

class TestGroupLifecycle:
    def test_group_two_reads_one_card(self):
        """2 个连续 read_file → 1 块群组卡（标题含 Read，2 成员）。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "src/a.py"), ("r2", "src/b.py")])
        assert len(m.blocks) == 1
        assert m.blocks[-1] is blk
        assert blk.extra["_group"] is True
        assert blk.extra["_group_tool"] == "read_file"
        assert len(blk.extra["_members"]) == 2
        # 成员注册进 tool_boxes → 群组块
        assert m.tool_boxes["r1"] is blk
        assert m.tool_boxes["r2"] is blk
        # 标题渲染（运行中）：● Read ...
        title = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[0])
        assert title.startswith("\u25cf"), title
        assert "Read" in title, title

    def test_group_output_dropped(self):
        """群组成员输出丢弃（摘要卡无正文），block.lines 仍 1 行。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "a.py"), ("r2", "b.py")])
        apply_cmd(m, ToolOutputCmd(tool_id="r1", text="file content...\nmore"))
        assert len(blk.lines) == 1, "群组成员输出应丢弃，块仍仅标题占位"

    def test_group_member_close_propagates(self):
        """成员 close 聚合群组状态：r1 fail + r2 done → 群组 ✖，标题原位翻转。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "a.py"), ("r2", "b.py")])
        # r1 失败 → r2 仍在运行 → 群组 running
        apply_cmd(m, ToolCloseCmd(tool_id="r1", success=False))
        assert blk.extra["_tool_status"] == "running"
        # r2 完成 → 全部关闭 → 最终化，聚合 fail（含失败成员）
        apply_cmd(m, ToolCloseCmd(tool_id="r2", success=True))
        assert blk.closed is True
        assert blk.extra["_tool_status"] == "fail"
        assert m.tool_boxes == {}
        assert m._tool_groups == {}
        # 标题渲染 ✖
        plains = [ln.plain for ln in m.committed_lines]
        assert any(p.startswith("\u2716") for p in plains), plains

    def test_group_all_done_renders_check(self):
        """全部成员 done → 群组 ✔。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "a.py"), ("r2", "b.py")])
        apply_cmd(m, ToolCloseCmd(tool_id="r1", success=True))
        apply_cmd(m, ToolCloseCmd(tool_id="r2", success=True))
        assert blk.closed and blk.extra["_tool_status"] == "done"
        plains = [ln.plain for ln in m.committed_lines]
        assert any(p.startswith("\u2714") for p in plains), plains

    def test_group_flush_on_round_end(self):
        """回合末 flush_tool_groups：未完成成员置 done 后最终化群组。"""
        m = _model()
        blk = m.open_tool_group("grep", [("g1", "foo"), ("g2", "bar")])
        apply_cmd(m, ToolCloseCmd(tool_id="g1", success=True))
        assert blk.closed is False  # g2 未关闭
        m.flush_tool_groups()
        assert blk.closed is True
        assert blk.extra["_tool_status"] == "done"
        assert m.tool_boxes == {}
        assert m._tool_groups == {}

    def test_group_open_reuse_updates_member_detail(self):
        """群组成员 open_tool_box（防御路径）仅更新成员 detail，不覆盖群组标题。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "old.py"), ("r2", "b.py")])
        reused = m.open_tool_box("r1", "read_file", "new.py")
        assert reused is blk
        assert blk.extra["_group_tool"] == "read_file"  # 群组标题未被覆盖
        members = blk.extra["_members"]
        assert members[0]["detail"] == "new.py"


# ═══════════════════════════════════════════════════════════
# 折叠摘要（collapsed_read_search，Phase C）
# ═══════════════════════════════════════════════════════════

class TestCollapsedSummary:
    def test_collapsed_read_title_hint(self):
        """折叠 read 卡：`● Read 2 files` + 末成员 detail 提示行，无成员行。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "src/a.py"), ("r2", "src/b.py")])
        assert blk.extra["_collapsed"] is True
        lines = tool_card_lines(blk, 60, 0, None)
        title = "".join(r.text for r in lines[0])
        assert title == "\u25cf Read 2 files", title
        # 折叠内容仅 1 行：末成员 detail（无成员全文输出）
        assert len(lines) == 2, lines
        hint = "".join(r.text for r in lines[1])
        assert hint == "│ src/b.py", hint

    def test_collapsed_grep_title(self):
        """折叠 grep 卡：`● Grep 2`（无 files 后缀）。"""
        m = _model()
        blk = m.open_tool_group("grep", [("g1", "keyword1"), ("g2", "keyword2")])
        assert blk.extra["_collapsed"] is True
        title = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[0])
        assert title == "\u25cf Grep 2", title

    def test_collapsed_closed_static_icon(self):
        """折叠卡关闭后状态图标 ✔（聚合 done）。"""
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "a.py"), ("r2", "b.py")])
        apply_cmd(m, ToolCloseCmd(tool_id="r1", success=True))
        apply_cmd(m, ToolCloseCmd(tool_id="r2", success=True))
        title = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[0])
        assert title.startswith("\u2714 Read 2 files"), title

    def test_toggle_group_collapsed_expand_collapse(self):
        """toggle_group_collapsed：折叠摘要卡 ↔ 展开全部成员（committed_lines 重建）。

        对齐 CC collapsed_read_search 展开交互——Ctrl+X 切换。折叠态仅末成员
        提示；展开态显示全部成员 detail；再切换恢复折叠。
        """
        m = _model()
        blk = m.open_tool_group("read_file", [("r1", "src/a.py"), ("r2", "src/b.py")])
        apply_cmd(m, ToolCloseCmd(tool_id="r1", success=True))
        apply_cmd(m, ToolCloseCmd(tool_id="r2", success=True))
        # 默认折叠
        plains = [ln.plain for ln in m.committed_lines]
        assert any("Read 2 files" in p for p in plains), plains
        assert not any("src/a.py" in p for p in plains), "折叠态不显示全部成员"
        # 展开
        assert m.toggle_group_collapsed() == 1
        plains = [ln.plain for ln in m.committed_lines]
        assert any("✔ Read" in p for p in plains), plains
        assert any("src/a.py" in p for p in plains), plains
        assert any("src/b.py" in p for p in plains), plains
        assert not any("Read 2 files" in p for p in plains), plains
        # 再切换恢复折叠
        m.toggle_group_collapsed()
        plains = [ln.plain for ln in m.committed_lines]
        assert any("Read 2 files" in p for p in plains), plains

    def test_toggle_group_collapsed_no_groups_noop(self):
        """无群组卡时 toggle 返回 0（no-op）。"""
        m = _model()
        assert m.toggle_group_collapsed() == 0


# ═══════════════════════════════════════════════════════════
# 回放路径分组（Phase B4）
# ═══════════════════════════════════════════════════════════

class TestReplayGrouping:
    def test_replay_groups_same_name(self):
        """历史回放：2 个连续 read_file → 1 张群组卡（与 live 一致）。"""
        m = _model()
        _replay(m, [
            _tc("r1", "read_file", '{"path": "a.py"}'),
            _tc("r2", "read_file", '{"path": "b.py"}'),
        ])
        tool_blocks = [b for b in m.blocks if b.kind == "tool"]
        assert len(tool_blocks) == 1, tool_blocks
        blk = tool_blocks[0]
        assert blk.extra["_group"] is True
        assert len(blk.extra["_members"]) == 2
        # 回放结束强制关闭 → 群组最终化
        assert blk.closed is True
        plains = [ln.plain for ln in m.committed_lines]
        assert any("Read 2 files" in p for p in plains), plains

    def test_replay_non_consecutive_no_group(self):
        """回放：read_file + bash + read_file（不相邻）→ 3 张独立单卡。"""
        m = _model()
        _replay(m, [
            _tc("r1", "read_file", '{"path": "a.py"}'),
            _tc("b1", "bash", '{"command": "ls"}'),
            _tc("r2", "read_file", '{"path": "b.py"}'),
        ])
        tool_blocks = [b for b in m.blocks if b.kind == "tool"]
        assert len(tool_blocks) == 3, tool_blocks
        assert all(not b.extra.get("_group") for b in tool_blocks), tool_blocks

    def test_bash_not_grouped(self):
        """回放：2 个 bash 不分组（对齐 CC）→ 2 张独立单卡。"""
        m = _model()
        _replay(m, [
            _tc("b1", "bash", '{"command": "ls"}'),
            _tc("b2", "bash", '{"command": "pwd"}'),
        ])
        tool_blocks = [b for b in m.blocks if b.kind == "tool"]
        assert len(tool_blocks) == 2, tool_blocks
        assert all(not b.extra.get("_group") for b in tool_blocks)


# ═══════════════════════════════════════════════════════════
# Task（dispatch_agent）分组卡（对齐 CC renderGroupedToolUse）
# ═══════════════════════════════════════════════════════════

class TestTaskGrouping:
    def test_task_group_title_and_rows(self):
        """Task 分组卡：`● N agents finished` + `@description` 行（展开态）。"""
        m = _model()
        apply_cmd(m, ToolGroupOpenCmd(
            tool_name="dispatch_agent",
            members=(("t1", "解析 user.py"), ("t2", "测试 auth")),
        ))
        blk = m.blocks[-1]
        assert blk.extra["_group"] is True
        assert blk.extra["_group_tool"] == "dispatch_agent"
        assert blk.extra["_collapsed"] is False  # Task 卡展开态（非折叠摘要）
        lines = tool_card_lines(blk, 60, 0, None)
        title = "".join(r.text for r in lines[0])
        assert title == "\u25cf 2 agents", title  # 运行中
        plains = ["".join(r.text for r in l) for l in lines]
        assert any("│ @解析 user.py" in p for p in plains), plains
        assert any("│ @测试 auth" in p for p in plains), plains

    def test_task_group_done_title(self):
        """Task 分组卡全部成员 done → `✔ N agents finished`。"""
        m = _model()
        apply_cmd(m, ToolGroupOpenCmd(
            tool_name="dispatch_agent",
            members=(("t1", "解析 user.py"), ("t2", "测试 auth")),
        ))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
        apply_cmd(m, ToolCloseCmd(tool_id="t2", success=True))
        blk = m.blocks[-1]
        assert blk.closed and blk.extra["_tool_status"] == "done"
        title = "".join(r.text for r in tool_card_lines(blk, 60, 0, None)[0])
        assert title == "\u2714 2 agents finished", title

    def test_task_replay_groups_same_name(self):
        """回放：2 个连续 dispatch_agent → 1 张 Task 分组卡。"""
        m = _model()
        _replay(m, [
            _tc("t1", "dispatch_agent", '{"description": "解析 user.py", "prompt": "..."}'),
            _tc("t2", "dispatch_agent", '{"description": "测试 auth", "prompt": "..."}'),
        ])
        tool_blocks = [b for b in m.blocks if b.kind == "tool"]
        assert len(tool_blocks) == 1, tool_blocks
        blk = tool_blocks[0]
        assert blk.extra["_group"] and blk.extra["_group_tool"] == "dispatch_agent"
        plains = [ln.plain for ln in m.committed_lines]
        assert any("agents finished" in p for p in plains), plains
        assert any("@解析 user.py" in p for p in plains), plains
        assert any("@测试 auth" in p for p in plains), plains

    def test_task_single_not_grouped(self):
        """回放：单个 dispatch_agent 不分组（长度 1 仍单卡，非群组）。

        live 路径 Dispatcher 排除 dispatch_agent 单卡（SubAgent 面板自渲染）；
        回放路径（无 live 面板）保留单 Task 卡（历史调用可见），与分组判定
        （长度 1 不分组）一致。
        """
        m = _model()
        _replay(m, [
            _tc("t1", "dispatch_agent", '{"description": "解析 user.py", "prompt": "..."}'),
        ])
        tool_blocks = [b for b in m.blocks if b.kind == "tool"]
        assert len(tool_blocks) == 1, tool_blocks
        assert not tool_blocks[0].extra.get("_group"), "单 Task 不分组，走单卡"


# ═══════════════════════════════════════════════════════════
# Dispatcher 抑制成员单卡 open（Phase B2）
# ═══════════════════════════════════════════════════════════

class TestDispatcherGroupSuppression:
    def test_group_member_no_individual_open(self):
        """分组成员 ToolStartedEvent 抑制单卡 ToolOpenCmd（仍推进计数）。"""
        pushed = []
        d = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        d._on_tool_batch_start(ToolBatchStartedEvent(label="main", tool_names=("read_file",), source="agent"))
        d._on_tool_group_planned(ToolGroupPlannedEvent(
            label="main", tool_name="read_file",
            members=(("r1", "a.py"), ("r2", "b.py")), source="agent",
        ))
        assert isinstance(pushed[-1], ToolGroupOpenCmd)
        assert d._group_member_ids == {"r1", "r2"}
        before = len(pushed)
        d._on_tool_started(ToolStartedEvent(
            label="r1", tool_name="read_file", detail="a.py", source="agent", tool_id="r1",
        ))
        d._on_tool_started(ToolStartedEvent(
            label="r2", tool_name="read_file", detail="b.py", source="agent", tool_id="r2",
        ))
        new = pushed[before:]
        assert not any(isinstance(c, ToolOpenCmd) for c in new), new
        # 计数仍推进（2 个 ToolCountIncCmd）
        assert sum(1 for c in new if c.cid == 14) == 2, new

    def test_batch_start_resets_member_ids(self):
        """新批 tool_batch_start 清空上一批分组成员 id（防残留误抑制）。

        无分组的新批也会发布 tool_batch_start（names 为空）重置集合——
        上一批成员 id 不残留，避免后续同名 id 单工具被误抑制开卡。
        """
        pushed = []
        d = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        d._on_tool_batch_start(ToolBatchStartedEvent(label="main", tool_names=("read_file",), source="agent"))
        d._on_tool_group_planned(ToolGroupPlannedEvent(
            label="main", tool_name="read_file",
            members=(("r1", "a.py"), ("r2", "b.py")), source="agent",
        ))
        assert d._group_member_ids == {"r1", "r2"}
        # 新批无分组 → tool_batch_start(names=[]) 重置
        d._on_tool_batch_start(ToolBatchStartedEvent(label="main", tool_names=(), source="agent"))
        assert d._group_member_ids == set()

    def test_non_member_started_still_opens(self):
        """非成员工具 ToolStartedEvent 仍推单卡 ToolOpenCmd（不受抑制影响）。"""
        pushed = []
        d = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        d._on_tool_batch_start(ToolBatchStartedEvent(label="main", tool_names=("read_file",), source="agent"))
        d._on_tool_group_planned(ToolGroupPlannedEvent(
            label="main", tool_name="read_file",
            members=(("r1", "a.py"), ("r2", "b.py")), source="agent",
        ))
        before = len(pushed)
        d._on_tool_started(ToolStartedEvent(
            label="x1", tool_name="bash", detail="ls", source="agent", tool_id="x1",
        ))
        assert any(isinstance(c, ToolOpenCmd) for c in pushed[before:]), pushed[before:]

    def test_member_done_still_closes(self):
        """分组成员 ToolDoneEvent 仍推 ToolCloseCmd（路由到群组成员 close）。"""
        pushed = []
        d = EventDispatcher(push_cmd=lambda cmd: pushed.append(cmd))
        d._on_tool_group_planned(ToolGroupPlannedEvent(
            label="main", tool_name="read_file",
            members=(("r1", "a.py"), ("r2", "b.py")), source="agent",
        ))
        before = len(pushed)
        d._on_tool_done(ToolDoneEvent(
            label="r1", tool_name="read_file", success=True, source="agent", tool_id="r1",
        ))
        assert any(isinstance(c, ToolCloseCmd) for c in pushed[before:]), pushed[before:]
