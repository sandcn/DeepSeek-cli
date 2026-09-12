"""read_file 聊天区工具卡隐藏文件内容（2026-09 用户需求）。

需求：read_file 聊天区工具卡只显示标题行（``✔ Read '<path>'``），不显示
读到的文件内容；读取失败/空文件等错误提示仍显示。内容行仍保留在工具块
数据 ``block.lines`` 中（轨迹 Trace / 详情视图照常可见）。

链路：
  read_file.display() 成功 → ``_publish_tool_text(..., chat_hidden=True)``
    → ``ToolOutputChunkEvent(chat_hidden=True)``
    → ``EventDispatcher._on_tool_output`` → ``ToolOutputCmd(chat_hidden=True)``
    → ``apply._do_tool_output`` → ``AppModel.append_tool_output(chat_hidden=True)``
    → ``block.extra["_chat_hidden_lines"]``（行对象引用）
    → ``toolcard.tool_card_lines`` 跳过隐藏行 + 跳过省略提示行

历史回放：``apply._append_tool_rich`` 命中 read_file 成功返回值形态
（``文件: <path>``）→ 同样登记隐藏。
"""

from __future__ import annotations

import pytest

from src.tools.base import Func
from src.tools.read_file import ReadFileFunc
from src.tui._const import RenderCommand, ToolOutputCmd
from src.tui._dispatcher import EventDispatcher
from src.tui.app.apply import _append_tool_rich
from src.tui.app.model import AppModel
from src.tui.app.toolcard import tool_card_lines
from src.tui.events import ToolOutputChunkEvent


# ── 测试辅助 ─────────────────────────────────────────────

class _Capture:
    """捕获 emit(event)（monkeypatch src.tui.events.publish.emit）。"""

    def __init__(self):
        self.events: list = []

    def __call__(self, event, *, bus=None):
        self.events.append(event)


@pytest.fixture()
def capture_emit(monkeypatch):
    cap = _Capture()
    import src.tui.events.publish as publish_mod
    monkeypatch.setattr(publish_mod, "emit", cap)
    return cap


def _make_dispatcher() -> tuple[EventDispatcher, list]:
    cmds: list = []
    disp = EventDispatcher(push_cmd=cmds.append, main_label="main")
    return disp, cmds


def _tool_block(model: AppModel, tool_id: str):
    box = model.tool_boxes.get(tool_id)
    if box is not None:
        return box
    for b in reversed(model.blocks):
        if b.kind == "tool" and b.extra.get("tool_id") == tool_id:
            return b
    raise AssertionError(f"未找到 tool_id={tool_id} 的工具块")


def _plain(lines) -> str:
    return "\n".join("".join(r.text for r in row) for row in lines)


# ── 1. 事件 / 命令字段 ───────────────────────────────────

def test_tool_output_event_default_not_hidden():
    """ToolOutputChunkEvent 默认 chat_hidden=False（向后兼容）。"""
    ev = ToolOutputChunkEvent(label="t", text="x", tool_id="t")
    assert ev.chat_hidden is False


def test_tool_output_cmd_default_not_hidden():
    """ToolOutputCmd 默认 chat_hidden=False；cid 不变。"""
    cmd = ToolOutputCmd(text="x", tool_id="t")
    assert cmd.chat_hidden is False
    assert cmd.cid == RenderCommand.TOOL_OUTPUT


def test_publish_tool_text_passes_chat_hidden(capture_emit):
    """Func._publish_tool_text(chat_hidden=True) → 事件带 chat_hidden=True。"""
    Func._publish_tool_text("body", "c1", chat_hidden=True)
    ev = capture_emit.events[0]
    assert isinstance(ev, ToolOutputChunkEvent)
    assert ev.chat_hidden is True
    assert ev.label == "c1" and ev.tool_id == "c1"


def test_publish_tool_text_default_not_hidden(capture_emit):
    """不带 chat_hidden 时事件为 False（其他工具输出不受影响）。"""
    Func._publish_tool_text("body", "c2")
    assert capture_emit.events[0].chat_hidden is False


# ── 2. read_file.display 上屏标记 ────────────────────────

async def test_display_success_content_chat_hidden(tmp_path, capture_emit):
    """成功读取：文件内容上屏事件带 chat_hidden=True（聊天卡隐藏）。"""
    p = tmp_path / "a.py"
    p.write_text("x = 1\ny = 2\n", encoding="utf-8")
    await ReadFileFunc(path=str(p)).display()
    evs = [e for e in capture_emit.events if isinstance(e, ToolOutputChunkEvent)]
    assert evs, "成功读取仍应上屏内容（供 Trace 可见）"
    assert evs[-1].chat_hidden is True
    import re
    plain = re.sub(r"\x1b\[[0-9;]*m", "", evs[-1].text)
    assert "x = 1" in plain


async def test_display_error_not_chat_hidden(tmp_path, capture_emit):
    """读取失败：错误提示照常显示（chat_hidden=False）。"""
    missing = str(tmp_path / "missing.txt")
    await ReadFileFunc(path=missing).display()
    evs = [e for e in capture_emit.events if isinstance(e, ToolOutputChunkEvent)]
    assert evs
    assert evs[-1].chat_hidden is False
    assert "文件不存在" in evs[-1].text


async def test_display_empty_file_no_event(tmp_path, capture_emit):
    """空文件：无内容上屏事件（仅返回提示）。"""
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    await ReadFileFunc(path=str(p)).display()
    evs = [e for e in capture_emit.events if isinstance(e, ToolOutputChunkEvent)]
    assert evs == []


# ── 3. dispatcher 透传 ───────────────────────────────────

def test_dispatcher_passes_chat_hidden():
    """ToolOutputChunkEvent(chat_hidden=True) → ToolOutputCmd(chat_hidden=True)。"""
    disp, cmds = _make_dispatcher()
    disp._on_tool_output(ToolOutputChunkEvent(
        label="c1", tool_id="c1", text="x", source="agent", chat_hidden=True,
    ))
    assert len(cmds) == 1
    assert isinstance(cmds[0], ToolOutputCmd)
    assert cmds[0].chat_hidden is True


def test_dispatcher_default_not_hidden():
    """普通工具输出 → chat_hidden=False。"""
    disp, cmds = _make_dispatcher()
    disp._on_tool_output(ToolOutputChunkEvent(
        label="c2", tool_id="c2", text="y", source="agent",
    ))
    assert cmds[0].chat_hidden is False


# ── 4. AppModel 隐藏行登记 + 聊天卡渲染 ──────────────────

def _model_with_tool(tool_id: str, tool_name: str, detail: str = "") -> AppModel:
    m = AppModel()
    m.width = 80
    m.open_tool_box(tool_id, tool_name, detail)
    return m


def test_append_tool_output_registers_hidden_rows():
    """append_tool_output(chat_hidden=True) 登记行对象到 _chat_hidden_lines。"""
    m = _model_with_tool("t1", "read_file", "'x.py'")
    m.append_tool_output("t1", "alpha\nbeta", chat_hidden=True)
    block = _tool_block(m, "t1")
    hidden = block.extra.get("_chat_hidden_lines")
    assert hidden and len(hidden) == 2
    for row in hidden:
        assert any(row is l for l in block.lines)


def test_append_tool_output_not_hidden_by_default():
    """默认（chat_hidden=False）不登记隐藏行。"""
    m = _model_with_tool("t2", "read_file", "'x.py'")
    m.append_tool_output("t2", "alpha\nbeta")
    block = _tool_block(m, "t2")
    assert not block.extra.get("_chat_hidden_lines")


def test_tool_card_hides_success_content():
    """read_file 成功内容：聊天卡仅标题行（无内容行/无省略提示）。"""
    m = _model_with_tool("t3", "read_file", "'x.py'")
    m.append_tool_output(
        "t3", "\n".join(f"line-{i}" for i in range(10)), chat_hidden=True,
    )
    block = _tool_block(m, "t3")
    rows = tool_card_lines(block, 80)
    assert len(rows) == 1, f"read_file 聊天卡应仅标题行，实际：{_plain(rows)!r}"
    assert "Read" in _plain(rows)
    # 内容仍保留在块数据中（Trace 可见）——head trim 保留前 3 行
    assert len(block.lines) == 4
    assert block.extra.get("_head_omitted_lines", 0) == 7


def test_tool_card_shows_error_content():
    """read_file 错误提示（chat_hidden=False）仍渲染为内容行。"""
    m = _model_with_tool("t4", "read_file", "'missing.txt'")
    m.append_tool_output("t4", "  x (文件不存在: missing.txt)")
    block = _tool_block(m, "t4")
    rows = tool_card_lines(block, 80)
    assert len(rows) == 2
    assert "文件不存在" in _plain(rows)


# ── 5. trim 后隐藏标记同步 ───────────────────────────────

def test_hidden_rows_pruned_after_head_trim():
    """head trim 删除后置行后，隐藏登记只保留仍在 block.lines 中的行。"""
    m = _model_with_tool("t5", "read_file", "'x.py'")
    m.append_tool_output(
        "t5", "\n".join(f"line-{i}" for i in range(50)), chat_hidden=True,
    )
    block = _tool_block(m, "t5")
    hidden = block.extra["_chat_hidden_lines"]
    assert len(hidden) == 3
    live_ids = {id(l) for l in block.lines}
    assert all(id(h) in live_ids for h in hidden)


def test_prune_chat_hidden_drops_dead_rows():
    """_prune_chat_hidden 移除已不在 block.lines 的行对象。"""
    m = _model_with_tool("t6", "read_file", "'x.py'")
    m.append_tool_output("t6", "a\nb\nc", chat_hidden=True)
    block = _tool_block(m, "t6")
    assert len(block.extra["_chat_hidden_lines"]) == 3
    # 手动删除一行（模拟 trim），再 prune
    del block.lines[1]
    m._prune_chat_hidden(block)
    assert len(block.extra["_chat_hidden_lines"]) == 2


# ── 6. 历史回放（apply._append_tool_rich） ────────────────

def test_history_replay_hides_read_file_content():
    """历史回放 read_file 成功返回值（文件: <path>）→ 内容行登记隐藏。"""
    m = _model_with_tool("c1", "read_file", "'x.py'")
    _append_tool_rich(m, {
        "role": "tool", "tool_call_id": "c1",
        "content": "文件: x.py\nalpha\nbeta",
    })
    block = _tool_block(m, "c1")
    hidden = block.extra.get("_chat_hidden_lines")
    assert hidden and len(hidden) == 3
    rows = tool_card_lines(block, 80)
    assert len(rows) == 1


def test_history_replay_error_not_hidden():
    """历史回放 read_file 错误提示 → 不隐藏（照常显示）。"""
    m = _model_with_tool("c2", "read_file", "'missing'")
    _append_tool_rich(m, {
        "role": "tool", "tool_call_id": "c2",
        "content": "(文件不存在: missing)",
    })
    block = _tool_block(m, "c2")
    assert not block.extra.get("_chat_hidden_lines")
    rows = tool_card_lines(block, 80)
    assert "文件不存在" in _plain(rows)


def test_history_replay_other_tool_not_hidden():
    """历史回放其他工具（非 read_file）内容不隐藏。"""
    m = _model_with_tool("c3", "bash", "ls")
    _append_tool_rich(m, {
        "role": "tool", "tool_call_id": "c3",
        "content": "文件: 无关",
    })
    block = _tool_block(m, "c3")
    assert not block.extra.get("_chat_hidden_lines")
