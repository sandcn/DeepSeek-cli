"""轨迹视图（DSH 风格）测试 — Ctrl+H 开关 + 左台账右检查器（2026-08-19）。

覆盖：
  1. 输入解析：0x08（Ctrl+H）→ ctrl_key '\x08'；0x7f（Backspace 键）→
     backspace；CSI u ``\x1b[104;5u`` / ``\x1b[8;5u`` → ctrl_key '\x08'；
     Alt+Backspace 词删除不回归。
  2. 分发：Ctrl+H 注入回调 → 调用轨迹开关；未注入 → 回退 backspace；
     set_trace_toggle_callback(None) 清除。
  3. 记录构建：块 → 记录种类映射 / 轮次分隔 / 工具摘要·状态·耗时 /
     详情惰性提取 / subagent 记录。
  4. TraceView 渲染：台账行（选中高亮/耗时右对齐/截断）+ 检查器
     （标题/元信息/内容换行截断）+ App 集成（trace_open 时消息区替换）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.renderer.ansi.helpers import AnsiLine
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser, KeyEvent
from src.tui.app.app import build_app_element
from src.tui.app.model import AppModel
from src.tui.app.trace import (
    TraceRecord,
    _messages_fingerprint,
    _records_from_messages,
    block_detail_lines,
    build_trace_records,
)
from src.tui.app.trace_view import (
    TraceView,
    _inspector_children,
    _ledger_row_runs,
)
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber

# ═══════════════════════════════════════════════════════════
# 1. 输入解析（0x08 = Ctrl+H）
# ═══════════════════════════════════════════════════════════

def test_ctrl_h_byte_decodes_to_ctrl_key():
    """0x08（Ctrl+H 字节）→ ctrl_key '\x08'（不再判为 backspace）。"""
    ev = InputParser._decode_control_char(0x08)
    assert ev.kind == "ctrl_key"
    assert ev.char == "\x08"


def test_del_byte_stays_backspace():
    """0x7f（Backspace 键/DEL 字节）→ backspace（现代终端退格语义保持）。"""
    ev = InputParser._decode_control_char(0x7f)
    assert ev.kind == "backspace"


def test_ctrl_h_csi_u_letter_path():
    """CSI u Ctrl+H（\\x1b[104;5u，增强键盘协议）→ ctrl_key '\x08'。"""
    ev = InputParser._dispatch_csi([104, 5], "u")
    assert ev.kind == "ctrl_key"
    assert ev.char == "\x08"


def test_ctrl_h_csi_u_keycode_path():
    """CSI u Ctrl+H 控制码路径（\\x1b[8;5u）→ ctrl_key '\x08'。"""
    ev = InputParser._dispatch_csi([8, 5], "u")
    assert ev.kind == "ctrl_key"
    assert ev.char == "\x08"


def test_alt_backspace_stays_word_delete():
    """回归：Alt+Backspace（ESC DEL 与 CSI u \\x1b[8;3u）保持词删除语义。"""
    ev = InputParser._dispatch_csi([8, 3], "u")
    assert ev.kind == "backspace"
    assert ev.modifier == 1
    ev2 = InputParser._decode_control_char(0x7f)
    assert ev2.kind == "backspace"


def test_ctrl_letter_mappings_not_regressed():
    """回归：既有 CSI u Ctrl 字母映射不受 Ctrl+H 改动影响。"""
    assert InputParser._dispatch_csi([97, 5], "u").kind == "home"    # Ctrl+A
    assert InputParser._dispatch_csi([119, 5], "u").kind == "delete"  # Ctrl+W


# ═══════════════════════════════════════════════════════════
# 2. 分发（Ctrl+H 轨迹开关 / backspace 回退）
# ═══════════════════════════════════════════════════════════

def _make_dispatcher() -> tuple[InputDispatcher, InputBufferEditor]:
    """构造测试用 dispatcher（不经真实 stdin，与 test_input_enter_residual 同约定）。"""
    io = InputIO(fd=0)
    be = InputBufferEditor(history_file=Path("unused"))
    parser = InputParser(io=io)
    disp = InputDispatcher(io=io, buffer_editor=be, parser=parser)
    return disp, be


def test_ctrl_h_without_callback_falls_back_to_backspace():
    """未注入轨迹回调时 Ctrl+H（\x08）回退 backspace——0x08 传统 BS 语义。"""
    disp, be = _make_dispatcher()
    be.set_buffer("abc")
    disp._handle_ctrl_key("\x08")
    assert be.get_current_text() == "ab"


def test_ctrl_h_with_callback_invokes_trace_toggle():
    """注入轨迹回调后 Ctrl+H（\x08）调用回调（不再回退 backspace）。"""
    disp, be = _make_dispatcher()
    calls = []
    disp.set_trace_toggle_callback(lambda: calls.append(1))
    be.set_buffer("abc")
    disp._handle_ctrl_key("\x08")
    assert calls == [1]
    assert be.get_current_text() == "abc"  # 未删字符


def test_ctrl_h_callback_clear_restores_backspace():
    """set_trace_toggle_callback(None) 清除注入后回退 backspace。"""
    disp, be = _make_dispatcher()
    disp.set_trace_toggle_callback(lambda: None)
    disp.set_trace_toggle_callback(None)
    be.set_buffer("xy")
    disp._handle_ctrl_key("\x08")
    assert be.get_current_text() == "x"


def test_ctrl_h_callback_exception_safe():
    """回调异常被吞（不打断输入分发）。"""
    disp, be = _make_dispatcher()

    def _boom():
        raise RuntimeError("boom")

    disp.set_trace_toggle_callback(_boom)
    be.set_buffer("abc")
    disp._handle_ctrl_key("\x08")  # 不抛
    assert be.get_current_text() == "abc"


# ═══════════════════════════════════════════════════════════
# 3. 记录构建
# ═══════════════════════════════════════════════════════════

def _make_model_with_blocks() -> AppModel:
    m = AppModel()
    m.append_committed("user", [AnsiLine.of("> 你好")])
    m.append_committed("reasoning", [AnsiLine.of("思考中...")])
    m.append_committed("content", [AnsiLine.of("回答内容")])
    # 真实工具块结构：原始标题行（lines[0]）+ 输出行 + 状态数据行（末行）
    b = m.append_block("tool")
    b.lines.append(AnsiLine.of("  \u00b7 Bash \u00b7 ls -la"))
    b.lines.append(AnsiLine.of("file1.txt"))
    b.lines.append(AnsiLine.of("file2.txt"))
    b.lines.append(AnsiLine.of("  \u2714"))
    b.extra.update(
        tool_name="bash", tool_detail="ls -la", tool_status="done",
        _tool_started_at=1.0, _tool_duration=2.5, _status_line_index=3,
    )
    b.closed = True
    m.commit_block(len(m.blocks) - 1)
    return m


def test_build_records_system_prompt_first():
    """系统提词为首条 system 记录（摘要 = 提示词首行；lines = 全文）。"""
    records, rows = build_trace_records(_make_model_with_blocks())
    sys_rec = records[0]
    assert sys_rec.kind == "system"
    assert sys_rec.index == 1
    assert sys_rec.summary, "系统提词摘要应非空"
    assert sys_rec.lines, "系统提词详情应非空"
    assert rows[0] is sys_rec


def test_build_records_kind_mapping_and_turn_separator():
    """块 → 记录种类映射 + 新用户消息插入轮次分隔行（系统提词为首条）。"""
    records, rows = build_trace_records(_make_model_with_blocks())
    assert [r.kind for r in records] == ["system", "user", "reasoning", "content", "tool"]
    assert [r.index for r in records] == [1, 2, 3, 4, 5]
    # 系统提词后、首个用户块前有轮次分隔行
    assert rows[0] is records[0]      # system 记录
    assert rows[1] is None            # 轮次 1 分隔
    assert rows[2] is records[1]      # user 记录
    # 无第二个用户块 → 仅 1 个分隔行
    assert sum(1 for r in rows if r is None) == 1


def test_build_records_tool_metadata():
    """工具记录：摘要 = 调用（工具名+detail）；result = 返回首行预览。"""
    records, _ = build_trace_records(_make_model_with_blocks())
    tool = records[4]
    assert tool.kind == "tool"
    assert tool.summary == "bash ls -la"      # 调用
    assert tool.result == "file1.txt"         # 返回首行
    assert tool.status == "done"
    assert tool.time_seconds == 2.5


def test_build_records_tool_detail_merged():
    """工具详情 = 调用行 + 返回输出行（剔除原始标题行/状态数据行）——
    「工具调用跟返回合并成一条」的完整详情。"""
    m = _make_model_with_blocks()
    records, _ = build_trace_records(m)
    tool = records[4]
    lines = block_detail_lines(tool.source_block)
    assert lines[0] == "bash ls -la"          # 调用行（从 extra 重建）
    assert lines[1:] == ["file1.txt", "file2.txt"]  # 返回输出
    assert "\u2714" not in lines               # 状态数据行剔除
    assert "\u00b7" not in lines[0]            # 旧式标题前缀剔除


def test_build_records_skips_separator_and_splash():
    """separator/splash 块跳过（非业务记录）。"""
    m = _make_model_with_blocks()
    m.append_committed("separator", [])
    m.append_committed("splash", [AnsiLine.of("brand")])
    records, rows = build_trace_records(m)
    assert [r.kind for r in records] == ["system", "user", "reasoning", "content", "tool"]


def test_build_records_detail_lazy():
    """块记录详情惰性：records.lines 为空，block_detail_lines 按需提取。"""
    m = _make_model_with_blocks()
    records, _ = build_trace_records(m)
    user = records[1]
    assert user.lines == []
    lines = block_detail_lines(user.source_block)
    assert lines == ["> 你好"]


def test_build_records_running_tool_duration():
    """运行中工具耗时 = now - _tool_started_at（未关闭时）。"""
    m = AppModel()
    b = m.append_block("tool")
    b.extra.update(tool_name="bash", tool_status="running", _tool_started_at=10.0)
    records, _ = build_trace_records(m)
    tool = records[1]  # 系统提词之后的工具记录
    assert tool.time_seconds > 0.0
    assert tool.status == "running"


def test_build_records_subagent():
    """subagent 槽位 → 子代理记录（状态/token/详情；追加于块记录之后）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("plan", "规划代理", status="done")
        slot = ctl._store._agents["plan"]
        slot.end_time = slot.start_time + 3.0
        slot.input_tokens = 100
        slot.output_tokens = 50
        slot.result_text = "计划完成"
        records, rows = build_trace_records(_make_model_with_blocks())
        sub = records[-1]
        assert sub.kind == "subagent"
        assert sub.status == "done"
        assert "plan" in sub.summary
        assert sub.time_seconds == pytest.approx(3.0, abs=0.01)
        assert sub.tokens["input"] == 100
        assert sub.tokens["output"] == 50
        assert "计划完成" in sub.lines
        assert rows[-1] is sub
    finally:
        ctl._store.clear()


# ═══════════════════════════════════════════════════════════
# 4. TraceView 渲染
# ═══════════════════════════════════════════════════════════

def _render(component, props, fiber=None):
    """在 hook 环境下渲染函数组件（与 test_review_app 同模式）。"""
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    hooks._push_current(fiber)
    try:
        return component(props), fiber
    finally:
        hooks._pop_current()


def _walk(el, out):
    """收集元素树中全部组件（递归）。"""
    out.append(el)
    for c in getattr(el, "children", None) or ():
        if isinstance(c, tuple):
            for x in c:
                _walk(x, out)
        elif c is not None:
            _walk(c, out)


def test_ledger_row_runs_basic():
    """台账行：索引/图标/摘要/右对齐耗时；未选中无 ▶。"""
    rec = TraceRecord(index=3, kind="tool", summary="bash ls -la", time_seconds=2.5)
    runs = _ledger_row_runs(rec, sel=False, left_w=40)
    text = "".join(r.text for r in runs)
    assert text.startswith("  #")
    assert "# 3" in text
    assert "bash ls -la" in text
    assert text.endswith("2.5s")
    assert len(text) <= 40


def test_ledger_row_selected_highlight():
    """选中行：▶ 标记 + 全部 run 合并背景色。"""
    rec = TraceRecord(index=1, kind="user", summary="你好")
    runs = _ledger_row_runs(rec, sel=True, left_w=40)
    assert runs[0].text.startswith("\u25b6")
    for r in runs:
        assert r.style is not None and r.style.bg is not None


def test_ledger_row_truncates_to_width():
    """台账行截断至左栏宽（不超宽，行级 diff 宽度不变量）。"""
    rec = TraceRecord(index=1, kind="content", summary="x" * 200)
    runs = _ledger_row_runs(rec, sel=False, left_w=24)
    from src.tui._width import wcswidth_simple
    assert sum(wcswidth_simple(r.text) for r in runs) <= 24


def test_ledger_row_tool_call_and_result_merged():
    """工具行：调用 + 返回预览合并一条（``调用 · 返回…`` + 右对齐耗时）。"""
    rec = TraceRecord(
        index=2, kind="tool", summary="bash ls -la",
        result="总用量 4462 drwxrwxr-x", time_seconds=2.5,
    )
    runs = _ledger_row_runs(rec, sel=False, left_w=50)
    text = "".join(r.text for r in runs)
    assert "bash ls -la" in text, "调用应显示"
    assert "\u00b7" in text and "总用量 4462" in text, "返回预览应合并显示"
    assert text.endswith("2.5s")
    assert len(text) <= 50


def test_ledger_row_tool_result_truncated():
    """工具返回预览按预算截断（长返回不撑爆台账行）。"""
    rec = TraceRecord(
        index=1, kind="tool", summary="bash",
        result="x" * 500, time_seconds=1.0,
    )
    runs = _ledger_row_runs(rec, sel=False, left_w=30)
    from src.tui._width import wcswidth_simple
    assert sum(wcswidth_simple(r.text) for r in runs) <= 30


def test_inspector_system_record():
    """系统提词记录检查器：标题 #N 系统 + 内容行。"""
    rec = TraceRecord(index=1, kind="system", summary="核心目标", lines=[
        "你是一位乐于助人的软件工程师助手。", "## 安全红线", "- 禁止 rm -rf",
    ])
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert texts[0].startswith("#1 系统")
    assert any("你是一位乐于助人" in t for t in texts)
    assert any("安全红线" in t for t in texts)


def test_inspector_children_structure():
    """检查器：标题 + 元信息 + 内容行（换行/截断）。"""
    rec = TraceRecord(
        index=2, kind="content", summary="回答",
        time_seconds=1.25, tokens={"input": 100, "output": 50},
    )
    rec._detail_lines = ["第一行内容", "第二行内容很长" * 30]
    children = _inspector_children(rec, right_w=20, vh=8)
    texts = [str(c.props.get("children", "")) for c in children]
    assert texts[0].startswith("#2 回答")
    assert any("耗时 1.2s" in t for t in texts)
    assert any("输入 100" in t for t in texts)
    assert any("省略" in t for t in texts)  # 超视口 → 省略提示
    # 行宽不超右栏
    from src.tui._width import wcswidth_simple
    for c in children[2:]:
        w = wcswidth_simple(str(c.props.get("children", "")))
        assert w <= 20


def test_inspector_empty_record():
    """无选中记录 → 空台账提示。"""
    children = _inspector_children(None, right_w=30, vh=8)
    assert len(children) == 1
    assert "无轨迹记录" in str(children[0].props.get("children", ""))


def test_trace_view_renders_ledger_and_inspector():
    """TraceView：头部 + 左右布局（左台账 / 分隔 / 右检查器）。"""
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 3
    el, _ = _render(TraceView, {"model": m, "width": 100})
    # 头部（第一子元素 TEXT）+ Row（第二子元素）
    assert el.type.__name__ == "Column"  # 根 Column
    children = list(el.children)
    assert len(children) == 2
    header = children[0]
    assert "轨迹" in "".join(r.text for r in header.props.get("styled", []))
    row_el = children[1]
    from src.tui.ink.widgets.layout import Row
    assert row_el.type is Row  # 左右布局容器（React Ink Row）
    parts = list(row_el.children)
    assert len(parts) == 3
    left_col, sep, right_col = parts
    # 左栏：系统提词 + 轮次分隔 + 5 条记录（窗口全量可见）
    left_children = list(left_col.children)
    assert len(left_children) == 6
    # 首行 = 系统提词记录（⚙ 图标 + 提示词首行摘要）
    sys_text = "".join(r.text for r in left_children[0].props.get("styled", []))
    assert "\u2699" in sys_text
    assert sys_text.startswith("  # 1")
    # 第二行 = 轮次 1 分隔
    assert "轮次 1" in "".join(r.text for r in left_children[1].props.get("styled", []))
    # 选中行（records[3] = content 回答 #4）带 ▶
    sel_text = "".join(r.text for r in left_children[4].props.get("styled", []))
    assert sel_text.startswith("\u25b6")
    assert "# 4" in sel_text
    # 工具行（末行 #5）：调用 + 返回预览合并一条（· file1.txt）
    tool_text = "".join(r.text for r in left_children[5].props.get("styled", []))
    assert "# 5" in tool_text
    assert "· file1.txt" in tool_text
    # 右栏：检查器标题为 #4 回答
    assert str(right_col.children[0].props.get("children", "")).startswith("#4 回答")


def test_trace_view_tail_follow():
    """trace_selected=-1（跟随尾部）→ 选中最新记录。"""
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = -1
    el, _ = _render(TraceView, {"model": m, "width": 100})
    row_el = list(el.children)[1]
    parts = list(row_el.children)
    left_children = list(parts[0].children)
    # 尾部跟随：窗口最后一行（工具记录 #5）选中
    last = left_children[-1]
    text = "".join(r.text for r in last.props.get("styled", []))
    assert text.startswith("\u25b6")
    assert "# 5" in text


def _input_handler(fiber):
    """从 fiber hooks 中取出 use_input 注册的 InputHook handler。"""
    for hook in fiber.hooks:
        if getattr(hook, "is_active", None) is not None and hasattr(hook, "handler"):
            return hook.handler
    raise AssertionError("fiber 中无 use_input hook")


def test_trace_view_navigation_writes_model():
    """↑↓ 导航写入 model.trace_selected（退出尾部跟随）。"""
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = -1
    el, fiber = _render(TraceView, {"model": m, "width": 100})
    handler = _input_handler(fiber)
    # 记录：system(0) user(1) reasoning(2) content(3) tool(4)
    # 上移：从尾部（#5 工具）到 #4 回答
    assert handler(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 3
    # 上移继续
    assert handler(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 2
    # 下移
    assert handler(KeyEvent(kind="arrow_down", raw=b"\x1b[B")) is True
    assert m.trace_selected == 3
    # End → 尾部
    assert handler(KeyEvent(kind="end", raw=b"\x1b[F")) is True
    assert m.trace_selected == 4
    # g → 首条（系统提词）
    assert handler(KeyEvent(kind="char", char="g", raw=b"g")) is True
    assert m.trace_selected == 0


def test_trace_view_close_keys():
    """Esc / Ctrl+H 关闭轨迹视图（trace_open=False）；Enter/字符放行。"""
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 1
    el, fiber = _render(TraceView, {"model": m, "width": 100})
    handler = _input_handler(fiber)
    # Esc 关闭
    assert handler(KeyEvent(kind="escape", raw=b"\x1b")) is True
    assert m.trace_open is False
    # 重新打开后 Ctrl+H 关闭
    m.trace_open = True
    assert handler(KeyEvent(kind="ctrl_key", char="\x08", raw=b"\x08")) is True
    assert m.trace_open is False
    # Enter 放行（非模态——提交消息）
    m.trace_open = True
    assert handler(KeyEvent(kind="enter", raw=b"\r")) is False
    assert m.trace_open is True
    # 普通字符放行（输入区打字）
    assert handler(KeyEvent(kind="char", char="a", raw=b"a")) is False


# ═══════════════════════════════════════════════════════════
# 5. App 集成（trace_open 时消息区替换）
# ═══════════════════════════════════════════════════════════

def test_app_swaps_message_area_for_trace_view():
    """trace_open=False：完整聊天界面（ChatView/StatusBar/InputArea）；
    True：整屏只渲染 TraceView——其他 TUI 组件全部不显示。"""
    from src.tui.app.app import App
    from src.tui.app.chat_view import ChatView
    from src.tui.app.input_area import InputArea
    from src.tui.app.status_bar import StatusBar
    from src.tui.app.trace_view import TraceView
    m = _make_model_with_blocks()
    el, _ = _render(App, {"model": m, "width": 100})
    all_els = []
    _walk(el, all_els)
    assert any(e.type is ChatView for e in all_els)
    assert any(e.type is StatusBar for e in all_els)
    assert any(e.type is InputArea for e in all_els)
    assert not any(e.type is TraceView for e in all_els)
    # 打开轨迹视图：整屏只显示 TraceView（其他 TUI 组件全部不渲染）
    m.trace_open = True
    el2, _ = _render(App, {"model": m, "width": 100})
    all_els2 = []
    _walk(el2, all_els2)
    assert any(e.type is TraceView for e in all_els2)
    assert not any(e.type is ChatView for e in all_els2)
    assert not any(e.type is StatusBar for e in all_els2)
    assert not any(e.type is InputArea for e in all_els2)
    # 根元素即 TraceView（无 APP 包装/底部区）
    assert el2.type is TraceView


# ═══════════════════════════════════════════════════════════
# 7. 消息列表数据源（agent.messages——轨迹主数据源）
# ═══════════════════════════════════════════════════════════

def _sample_messages():
    """真实会话消息形态（对齐 .chat/_checkpoint.json 结构）。"""
    return [
        {"role": "system", "content": "你是一位乐于助人的软件工程师助手。"},
        {"role": "system", "content": "# 当前执行环境\n- OS: Windows"},
        {"role": "user", "content": "列出文件"},
        {
            "role": "assistant", "content": None, "reasoning_content": "用户想列出文件。",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "bash", "arguments": '{"command": "ls -la"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "总用量 4462\ndrwxrwxr-x"},
        {"role": "assistant", "content": "目录内容如上。", "reasoning_content": None},
    ]


def test_records_from_messages_structure():
    """消息列表 → 记录：system×2 / 轮次分隔 / user / 思考 / 工具 / 回答。"""
    records, rows = _records_from_messages(_sample_messages())
    assert [r.kind for r in records] == ["system", "system", "user", "reasoning", "tool", "content"]
    assert [r.index for r in records] == [1, 2, 3, 4, 5, 6]
    # 轮次分隔：system 记录后、user 记录前
    assert rows[0] is records[0]
    assert rows[1] is records[1]
    assert rows[2] is None
    assert rows[3] is records[2]
    # system 记录：摘要 = 首行；lines = 全文
    assert records[0].summary == "你是一位乐于助人的软件工程师助手。"
    assert records[1].lines == ["# 当前执行环境", "- OS: Windows"]


def test_records_from_messages_tool_call_result_merged():
    """工具调用 + 返回合并一条：summary = 调用；result = 返回首行；
    lines = 调用行 + 返回行。"""
    records, _ = _records_from_messages(_sample_messages())
    tool = records[4]
    assert tool.kind == "tool"
    assert tool.summary == "bash ls -la"          # 调用（关键参数提取）
    assert tool.result == "总用量 4462"           # 返回首行预览
    assert tool.lines == ["bash ls -la", "总用量 4462", "drwxrwxr-x"]  # 调用+返回


def test_records_from_messages_orphan_tool_result():
    """无匹配调用的 tool 返回（异常/截断）→ 独立「工具返回」记录。"""
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "ghost", "content": "孤儿返回"},
    ]
    records, _ = _records_from_messages(messages)
    assert [r.kind for r in records] == ["user", "tool"]
    assert records[1].summary == "工具返回"
    assert records[1].result == "孤儿返回"


def test_build_trace_records_message_source_preferred():
    """build_trace_records 优先使用 message_source（agent 消息列表）；
    未注入时回退块路径。"""
    m = _make_model_with_blocks()
    m.message_source = lambda: _sample_messages()
    records, _ = build_trace_records(m)
    assert [r.kind for r in records] == ["system", "system", "user", "reasoning", "tool", "content"]
    # 清除消息源 → 回退块路径（系统提词 + 块记录）
    m.message_source = None
    records2, _ = build_trace_records(m)
    assert [r.kind for r in records2] == ["system", "user", "reasoning", "content", "tool"]


def test_messages_fingerprint_tracks_growth_and_edit():
    """消息指纹：内容增长（流式）/ 追加 / 尾消息编辑触发变化。"""
    m = AppModel()
    msgs = _sample_messages()
    m.message_source = lambda: msgs
    fp1 = _messages_fingerprint(m)
    # 流式增长：尾消息 content 变长
    msgs[-1]["content"] = "目录内容如上。\n\n更多内容"
    fp2 = _messages_fingerprint(m)
    assert fp1 != fp2
    # 追加新消息
    msgs.append({"role": "user", "content": "再来一次"})
    fp3 = _messages_fingerprint(m)
    assert fp2 != fp3
    # 无变化 → 指纹稳定
    assert _messages_fingerprint(m) == fp3


def test_session_setup_injects_message_source_to_chat_ui():
    """装配链：_register_session_handlers 把 agent 消息列表注入 chat_ui
    （轨迹视图数据源 = 真实会话消息）。"""
    from src.app_loop._session_setup import _register_session_handlers

    class _StubUI:
        def __init__(self):
            self.source = None

        def set_message_source(self, source):
            self.source = source

        def bottom_bar(self):  # _make_round_callbacks 访问
            raise AttributeError

    class _StubMonitor:
        def is_active(self):
            return False

    class _StubSession:
        def __init__(self):
            self.messages = [{"role": "user", "content": "hi"}]

        def on(self, *a, **k):
            pass

        def off(self, *a, **k):
            pass

    ui = _StubUI()
    session = _StubSession()
    _register_session_handlers(session, _StubMonitor(), chat_ui=ui)
    assert ui.source is not None, "chat_ui 应注入消息源"
    assert ui.source() == session.messages, "消息源应返回 agent 消息列表"


# ═══════════════════════════════════════════════════════════
# 6. 端到端（真实渲染循环 + pyte 终端）
# ═══════════════════════════════════════════════════════════

def _make_session(height=24, width=80):
    import io

    from src.tui._config import TuiConfig
    from src.tui._screen import TerminalWidthCache
    from src.tui.app.apply import apply_cmd
    from src.tui.ink.session import InkSession

    cache = TerminalWidthCache.get_default()
    cache._width = width
    cache._height = height
    model = AppModel()
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    session.set_line_tracker(None)
    return session, model, stream


def _push_history(session, rounds=3):
    import time as _t

    from src.tui._const import (
        ContentCmd,
        MainPhaseCmd,
        PhaseDoneCmd,
        ReasoningCmd,
        UserMsgCmd,
    )

    for i in range(rounds):
        session.push_cmd(UserMsgCmd(text=f"用户问题 {i}"))
        session.push_cmd(MainPhaseCmd(phase="thinking"))
        session.push_cmd(ReasoningCmd(text=f"思考 {i}"))
        session.push_cmd(PhaseDoneCmd(phase="reasoning"))
        session.push_cmd(ContentCmd(text=f"回答 {i}"))
        session.push_cmd(PhaseDoneCmd(phase="content"))
        _t.sleep(0.05)


def test_e2e_trace_view_replaces_message_area():
    """端到端：Ctrl+H 装配回调打开轨迹视图——消息区（✦ 标题/聊天）隐藏，
    台账 + 检查器显示；再按关闭恢复聊天。

    注：渲染命令按优先级出队（USER_MSG 为 NORMAL，REASONING/CONTENT 为
    CRITICAL）——批处理内块顺序可能不同于推送顺序；断言保持顺序无关。
    """
    import time as _t

    import pyte

    from src.tui._assembly_steps import _make_trace_toggle_cb

    session, model, stream = _make_session()
    session.start()
    _t.sleep(0.15)
    try:
        _push_history(session, rounds=3)
        _t.sleep(0.5)
        toggle = _make_trace_toggle_cb(model, session)

        # ── 打开轨迹视图 ──
        toggle()
        _t.sleep(0.3)
        out = stream.getvalue()
        screen = pyte.Screen(80, 24)
        pyte.Stream(screen).feed(out)
        lines = screen.display
        joined = "\n".join(lines)
        assert "轨迹 Trace" in joined, "轨迹头部应显示"
        assert "轮次 1" in joined, "轮次分隔应显示"
        assert "回答 2" in joined, "台账摘要/检查器应含最新回答"
        assert "\u2726" not in joined, "消息区标题栏（✦）不应显示"
        assert "输入消息" not in joined, "全屏模式：输入区不应显示"
        assert "标准模式" not in joined, "全屏模式：输入区模式行不应显示"

        # ── 关闭轨迹视图 ──
        toggle()
        _t.sleep(0.3)
        out2 = stream.getvalue()
        screen2 = pyte.Screen(80, 24)
        pyte.Stream(screen2).feed(out2)
        lines2 = screen2.display
        joined2 = "\n".join(lines2)
        assert "轨迹 Trace" not in joined2, "轨迹头部应消失"
        # 非全屏流动模型：长聊天文档顶部（✦ 标题栏）滚出可见区——以聊天
        # 内容恢复为准（思考/回答角色头可见即消息区已恢复）
        assert "思考 2" in joined2, "聊天推理内容应恢复显示"
        assert "回答 2" in joined2, "聊天回答内容应恢复显示"
    finally:
        session.stop()


def test_e2e_trace_view_streaming_tail_follow():
    """端到端：轨迹视图打开期间流式追加内容 → 尾部跟随（最新记录选中 ▶）。"""
    import time as _t

    import pyte

    from src.tui._assembly_steps import _make_trace_toggle_cb
    from src.tui._const import ContentCmd, MainPhaseCmd, PhaseDoneCmd

    session, model, stream = _make_session()
    session.start()
    _t.sleep(0.15)
    try:
        _push_history(session, rounds=1)
        _t.sleep(0.3)
        toggle = _make_trace_toggle_cb(model, session)
        toggle()
        _t.sleep(0.3)
        # 轨迹视图打开期间流式追加（内容通道经 MainPhase answering 重开；
        # 不推送新用户消息——USER_MSG 低优先级批内后置，追加内容保证为末条）
        session.push_cmd(MainPhaseCmd(phase="answering"))
        session.push_cmd(ContentCmd(text="追加回答"))
        session.push_cmd(PhaseDoneCmd(phase="content"))
        _t.sleep(0.5)
        out = stream.getvalue()
        screen = pyte.Screen(80, 24)
        pyte.Stream(screen).feed(out)
        joined = "\n".join(screen.display)
        assert "追加回答" in joined, "流式追加记录应进入台账/检查器"
        # 尾部跟随：追加内容为最新记录 → 台账选中行（▶ 前缀）含「追加」
        # （检查器内容与台账行同屏并排——按 ▶ 前缀定位台账选中行）
        sel_lines = [ln for ln in screen.display if ln.startswith("\u25b6")]
        assert sel_lines, "应有台账选中行（▶）"
        assert "追加" in sel_lines[-1], "尾部跟随应选中最新追加记录"
    finally:
        session.stop()
