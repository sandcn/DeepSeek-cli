"""web_search / web_fetch 结果在工具卡内以流式 markdown 渲染（2026-10 用户需求）。

需求：``web_search`` / ``web_fetch`` 返回的结果用 TUI 流式 markdown 渲染
（工具卡标题+运行时间保留，卡片正文按 markdown 格式化：链接/列表/标题/分隔线）。

链路：
  工具 ``display()`` → ``Func._publish_tool_markdown(result)``
    → ``ToolOutputChunkEvent(markdown=True)``
    → ``EventDispatcher._on_tool_output`` → ``ToolMarkdownCmd``
    → ``apply._do_tool_markdown`` → ``AppModel.append_tool_markdown``
    → ``block.extra["_tool_md_lines"]``（``AnsiStreamRenderer`` 渲染行缓冲）
    → ``toolcard.tool_card_lines`` 渲染卡片正文（│ 引导线 + 单行截断 + 满宽背景）

覆盖：事件/命令字段、dispatcher 路由、模型 markdown 缓冲、卡片渲染、
关闭收尾（残差刷出）、终端 resize 整块重渲染、工具声明与格式化、
历史回放（``apply._append_tool_rich``）同样渲染 markdown。
"""

from __future__ import annotations

import pytest

from src.tools.base import Func
from src.tools.registry import tool_output_is_markdown
from src.tui._const import (
    RenderCommand, ToolMarkdownCmd, ToolOpenCmd, ToolCloseCmd,
)
from src.tui._dispatcher import EventDispatcher
from src.tui.app.apply import apply_cmd, _do_tool_markdown, _append_tool_rich
from src.tui.app.model import AppModel
from src.tui.app.toolcard import tool_card_lines, _tool_md_content_width
from src.tui.events import ToolOutputChunkEvent


_MD = (
    "来源 (2 条):\n\n"
    "- [标题一](https://example.com/a) — 摘要一 (2024-01-01)\n"
    "- [标题二](https://example.com/b) — 摘要二 (2024-02-02)\n\n"
    "回答时请引用来源。"
)


# ── 测试辅助 ─────────────────────────────────────────────

class _Capture:
    """捕获 emit(event)（monkeypatch src.core.events.publish.emit）。"""

    def __init__(self):
        self.events: list = []

    def __call__(self, event, *, bus=None):
        self.events.append(event)


@pytest.fixture()
def capture_emit(monkeypatch):
    cap = _Capture()
    import src.core.events.publish as publish_mod
    monkeypatch.setattr(publish_mod, "emit", cap)
    return cap


def _make_dispatcher() -> tuple[EventDispatcher, list]:
    cmds: list = []
    disp = EventDispatcher(push_cmd=cmds.append, main_label="main")
    return disp, cmds


def _plain(lines) -> str:
    return "\n".join("".join(r.text for r in row) for row in lines)


def _model(width: int = 80) -> AppModel:
    m = AppModel()
    m.width = width
    return m


# ── 1. 事件 / 命令字段 ───────────────────────────────────

def test_tool_output_event_default_not_markdown():
    """ToolOutputChunkEvent 默认 markdown=False（向后兼容）。"""
    ev = ToolOutputChunkEvent(label="t", text="x", tool_id="t")
    assert ev.markdown is False


def test_tool_markdown_cmd_cid():
    """ToolMarkdownCmd cid == RenderCommand.TOOL_MARKDOWN。"""
    cmd = ToolMarkdownCmd(text="x", tool_id="t")
    assert cmd.cid == RenderCommand.TOOL_MARKDOWN
    assert int(RenderCommand.TOOL_MARKDOWN) == 27


def test_publish_tool_markdown_emits_flag(capture_emit):
    """Func._publish_tool_markdown → 事件 markdown=True（归属解析同文本通道）。"""
    Func._publish_tool_markdown("# 标题\n正文", "w1")
    ev = capture_emit.events[0]
    assert isinstance(ev, ToolOutputChunkEvent)
    assert ev.markdown is True
    assert ev.label == "w1" and ev.tool_id == "w1"


def test_publish_tool_markdown_empty_skipped(capture_emit):
    """空文本不发事件。"""
    Func._publish_tool_markdown("", "w2")
    assert capture_emit.events == []


def test_publish_tool_text_not_markdown(capture_emit):
    """纯文本通道不受影响（markdown=False）。"""
    Func._publish_tool_text("plain", "w3")
    assert capture_emit.events[0].markdown is False


# ── 2. dispatcher 路由 ───────────────────────────────────

def test_dispatcher_markdown_routes_to_markdown_cmd():
    """ToolOutputChunkEvent(markdown=True) → ToolMarkdownCmd。"""
    disp, cmds = _make_dispatcher()
    disp._on_tool_output(ToolOutputChunkEvent(
        label="w1", tool_id="w1", text=_MD, source="agent", markdown=True,
    ))
    assert len(cmds) == 1
    assert isinstance(cmds[0], ToolMarkdownCmd)
    assert cmds[0].tool_id == "w1"
    assert "#" not in cmds[0].text  # 原样透传 markdown 源（未渲染）


def test_dispatcher_plain_routes_to_output_cmd():
    """普通工具输出仍走 ToolOutputCmd。"""
    from src.tui._const import ToolOutputCmd

    disp, cmds = _make_dispatcher()
    disp._on_tool_output(ToolOutputChunkEvent(
        label="b1", tool_id="b1", text="ls", source="agent",
    ))
    assert isinstance(cmds[0], ToolOutputCmd)


# ── 3. 模型 markdown 缓冲（apply） ───────────────────────

def test_apply_renders_markdown_into_card_body():
    """ToolMarkdownCmd → 卡片正文来自 markdown 渲染缓冲（非 block.lines）。"""
    m = _model()
    m.open_tool_box("t1", "web_search", "python")
    apply_cmd(m, ToolMarkdownCmd(text=_MD, tool_id="t1"))
    block = m.tool_boxes["t1"]
    md_lines = block.extra.get("_tool_md_lines")
    assert md_lines, "应渲染出 markdown 行"
    # 渲染行写入独立缓冲，不改写 block.lines 纯文本数据源
    assert len(block.lines) == 1  # 仅标题行
    body = _plain(tool_card_lines(block, 80, 0, None))
    assert "标题一" in body and "标题二" in body
    assert "https://example.com/a" not in body  # 链接文本渲染（URL 不裸露）
    assert "- [" not in body  # markdown 语法未原样输出


def test_markdown_links_are_clickable():
    """markdown 链接渲染为带 OSC 8 link 的 run（终端可点击）。"""
    from src.renderer.ansi import AnsiStreamRenderer

    r = AnsiStreamRenderer(width=60)
    r.write("- [标题](https://example.com/a)")
    r.close()
    runs = [run for line in r.take_lines() for run in line.runs]
    links = [getattr(run, "link", None) for run in runs]
    assert "https://example.com/a" in links


def test_close_flushes_markdown_tail():
    """关闭工具时渲染器 close() 刷出未闭合残差（尾部段落不丢）。"""
    m = _model()
    m.open_tool_box("t2", "web_fetch", "https://e.com")
    apply_cmd(m, ToolMarkdownCmd(text="# 标题\n\n尾部段落无换行", tool_id="t2"))
    block = m.tool_boxes["t2"]
    before = len(block.extra.get("_tool_md_lines") or [])
    apply_cmd(m, ToolCloseCmd(tool_id="t2", success=True))
    after = block.extra.get("_tool_md_lines") or []
    assert len(after) >= before
    assert block.extra.get("_tool_md_renderer") is None
    body = _plain(tool_card_lines(block, 80, 0, None))
    assert "尾部段落无换行" in body


def test_markdown_body_only_emitted_at_start_zero():
    """markdown 正文仅卡片首行提交（start==0）发射，start>0 不重复。"""
    m = _model()
    m.open_tool_box("t3", "web_search", "q")
    apply_cmd(m, ToolMarkdownCmd(text=_MD, tool_id="t3"))
    block = m.tool_boxes["t3"]
    assert "标题一" in _plain(tool_card_lines(block, 80, 0, None))
    # start>0（增量提交/冻结尾）：无标题行、无正文（不重复渲染）
    assert _plain(tool_card_lines(block, 80, 1, None)).strip() == ""
    assert _plain(tool_card_lines(block, 80, 2, None)).strip() == ""


def test_unknown_tool_id_fallback_box(capture_emit):
    """未知 tool_id：兜底建 box（markdown 输出不丢失）。"""
    m = _model()
    _do_tool_markdown(m, ToolMarkdownCmd(text="# 标题", tool_id="unknown-1"))
    block = m.tool_boxes["unknown-1"]
    assert block.extra.get("_tool_md_source") == "# 标题"


def test_assistant_tool_id_dropped():
    """assistant 归属的 markdown 输出丢弃（无归属，不建空卡）。"""
    m = _model()
    _do_tool_markdown(m, ToolMarkdownCmd(text="# 标题", tool_id="assistant"))
    assert m.tool_boxes == {}


def test_markdown_source_accumulates():
    """多次发布：源文本累积、行缓冲增量增长。"""
    m = _model()
    m.open_tool_box("t4", "web_search", "q")
    apply_cmd(m, ToolMarkdownCmd(text="第一段\n\n", tool_id="t4"))
    apply_cmd(m, ToolMarkdownCmd(text="第二段\n", tool_id="t4"))
    block = m.tool_boxes["t4"]
    assert block.extra.get("_tool_md_source") == "第一段\n\n第二段\n"
    body = _plain(tool_card_lines(block, 80, 0, None))
    assert "第一段" in body and "第二段" in body


def test_width_change_during_stream_no_duplication():
    """流式期间终端变宽/变窄：源重放重建缓冲，历史行不重复。"""
    m = _model(width=80)
    apply_cmd(m, ToolOpenCmd(tool_name="web_search", tool_id="s1", detail="q"))
    apply_cmd(m, ToolMarkdownCmd(text="第一段内容\n\n", tool_id="s1"))
    m.width = 40
    apply_cmd(m, ToolMarkdownCmd(text="第二段内容\n", tool_id="s1"))
    block = m.blocks[0]
    src = block.extra["_tool_md_source"]
    rendered = _plain(tool_card_lines(block, 40, 0, None))
    # 源文本每段只出现一次（重放未与旧缓冲叠加）
    assert rendered.count("第一段内容") == 1
    assert rendered.count("第二段内容") == 1
    assert block.extra.get("_tool_md_render_width") == _tool_md_content_width(40)
    assert len(src.split("第一段内容")) == 2


# ── 4. 终端 resize 整块重渲染 ────────────────────────────

def test_resize_rerenders_markdown_at_new_width():
    """宽度变化：按源文本整块重渲染（渲染宽度随卡片宽度更新）。"""
    m = _model(width=60)
    m.open_tool_box("r1", "web_search", "q")
    apply_cmd(m, ToolMarkdownCmd(text=_MD, tool_id="r1"))
    apply_cmd(m, ToolCloseCmd(tool_id="r1", success=True))
    block = m.blocks[0]
    assert block.extra.get("_tool_md_render_width") == _tool_md_content_width(60)
    tool_card_lines(block, 60, 0, None)
    tool_card_lines(block, 40, 0, None)
    assert block.extra.get("_tool_md_render_width") == _tool_md_content_width(40)
    # 每行不超过新卡片宽度
    for row in tool_card_lines(block, 40, 0, None):
        assert sum(r.width for r in row) <= 40


# ── 5. 工具声明 / 结果格式化 ─────────────────────────────

def test_tools_declare_markdown_output():
    """web_search / web_fetch 声明 markdown_output；其他工具默认 False。"""
    from src.tools.web_fetch import WebFetchFunc
    from src.tools.web_search import WebSearchFunc

    assert WebSearchFunc.markdown_output is True
    assert WebFetchFunc.markdown_output is True
    assert Func.markdown_output is False
    assert tool_output_is_markdown("web_search") is True
    assert tool_output_is_markdown("web_fetch") is True
    assert tool_output_is_markdown("bash") is False


def test_format_fetch_result_is_markdown():
    """web_fetch 结果格式化为规范 markdown（标题 + 链接 + 分隔线）。"""
    from src.tools.page_fetcher import format_fetch_result

    md = format_fetch_result({
        "title": "页面标题", "url": "https://e.com/x", "domain": "e.com",
        "date": "2024-01-01", "body": "正文内容",
    })
    assert md.startswith("# 页面标题")
    assert "- 来源: [https://e.com/x](https://e.com/x)" in md
    assert "- 域名: e.com" in md
    assert "- 发布时间: 2024-01-01" in md
    assert "\n---\n" in md
    assert md.rstrip().endswith("正文内容")


def test_format_fetch_result_without_body():
    """无正文：输出占位提示（不抛异常）。"""
    from src.tools.page_fetcher import format_fetch_result

    md = format_fetch_result({
        "title": "T", "url": "https://e.com", "domain": "", "date": "", "body": "",
    })
    assert md.startswith("# T")
    assert "(未提取到正文内容)" in md


# ── 6. 工具 display() 发 markdown（不经网络） ────────────

async def test_web_search_display_publishes_markdown(monkeypatch, capture_emit):
    """WebSearchFunc.display 结果经 markdown 通道发布（无纯文本行）。"""
    from src.tools.web_search import WebSearchFunc

    f = WebSearchFunc("q")

    async def _fake_execute():
        return _MD

    monkeypatch.setattr(f, "execute", _fake_execute)
    result = await f.display()
    assert result == _MD
    evs = [e for e in capture_emit.events if isinstance(e, ToolOutputChunkEvent)]
    assert evs and evs[-1].markdown is True
    assert evs[-1].text == _MD


async def test_web_fetch_display_publishes_markdown(monkeypatch, capture_emit):
    """WebFetchFunc.display 结果经 markdown 通道发布。"""
    from src.tools.web_fetch import WebFetchFunc

    f = WebFetchFunc("https://e.com")
    md = "# 标题\n\n- 来源: [https://e.com](https://e.com)\n\n---\n\n正文"

    async def _fake_execute():
        return md

    monkeypatch.setattr(f, "execute", _fake_execute)
    result = await f.display()
    assert result == md
    evs = [e for e in capture_emit.events if isinstance(e, ToolOutputChunkEvent)]
    assert evs and evs[-1].markdown is True


# ── 7. 历史回放同样渲染 markdown ─────────────────────────

def test_history_replay_markdown_tool():
    """历史回放 web_search 结果 → markdown 缓冲渲染（非纯文本行）。"""
    m = _model()
    m.open_tool_box("c1", "web_search", "q")
    _append_tool_rich(m, {
        "role": "tool", "tool_call_id": "c1", "content": _MD,
    })
    block = m.blocks[0]
    assert block.extra.get("_tool_md_lines")
    body = _plain(tool_card_lines(block, 80, 0, None))
    assert "标题一" in body


def test_history_replay_plain_tool_stays_plain():
    """历史回放普通工具（bash）→ 纯文本行（不变）。"""
    m = _model()
    m.open_tool_box("c2", "bash", "ls")
    _append_tool_rich(m, {
        "role": "tool", "tool_call_id": "c2", "content": "a\nb",
    })
    block = m.blocks[0]
    assert block.extra.get("_tool_md_lines") is None
    assert "a" in _plain(tool_card_lines(block, 80, 0, None))


# ── 8. 队列 / 优先级归类 ─────────────────────────────────

def test_markdown_cmd_stream_priority_and_keep():
    """markdown 命令与 TOOL_OUTPUT 同序且暂停保留（不丢内容）。"""
    from src.tui.ink._cmd_priority import (
        _CMD_PRIORITY_CRITICAL, _get_cmd_priority, _STREAM_CMDS,
    )
    from src.tui.ink._session_queue_mixin import (
        _APPEND_STREAM_CMDS, _KEEP_CONTENT_CMDS,
    )
    from src.tui._const import CONTENT_COMMANDS

    cmd = ToolMarkdownCmd(text="x", tool_id="t")
    assert RenderCommand.TOOL_MARKDOWN in _STREAM_CMDS
    assert _get_cmd_priority(cmd) == _CMD_PRIORITY_CRITICAL
    assert RenderCommand.TOOL_MARKDOWN in _KEEP_CONTENT_CMDS
    assert RenderCommand.TOOL_MARKDOWN in _APPEND_STREAM_CMDS
    assert RenderCommand.TOOL_MARKDOWN in CONTENT_COMMANDS
