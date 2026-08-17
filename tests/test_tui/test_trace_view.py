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
    _live_fingerprint,
    _messages_fingerprint,
    _records_from_messages,
    block_detail_lines,
    build_subagent_trace_records,
    build_trace_records,
)
from src.tui.app.trace_view import (
    TraceView,
    _args_to_tree,
    _block_styled_rows,
    _inspector_children,
    _ledger_row_runs,
    _md_detail_rows,
    _parse_tree_text,
    _subagent_trace_deps,
    _to_tui_style,
    _tool_tree_rows,
    _value_to_tree,
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


def test_build_records_tools_first_system_second():
    """台账首条 = # 0 工具列表（用户需求 2026-08-17）；系统提词次条 #1。"""
    records, rows = build_trace_records(_make_model_with_blocks())
    tools_rec = records[0]
    assert tools_rec.kind == "tools"
    assert tools_rec.index == 0
    assert tools_rec.summary == "工具列表"
    assert tools_rec.lines, "工具列表应非空（agent 全部工具，一行一个）"
    assert rows[0] is tools_rec
    sys_rec = records[1]
    assert sys_rec.kind == "system"
    assert sys_rec.index == 1
    assert sys_rec.summary, "系统提词摘要应非空"
    assert sys_rec.lines, "系统提词详情应非空"
    assert rows[1] is sys_rec


def test_build_records_kind_mapping_and_turn_separator():
    """块 → 记录种类映射 + 新用户消息插入轮次分隔行（工具列表 + 系统提词
    为首两条）。"""
    records, rows = build_trace_records(_make_model_with_blocks())
    assert [r.kind for r in records] == ["tools", "system", "user", "reasoning", "content", "tool"]
    assert [r.index for r in records] == [0, 1, 2, 3, 4, 5]
    # 工具列表/系统提词后、首个用户块前有轮次分隔行
    assert rows[0] is records[0]      # tools 记录
    assert rows[1] is records[1]      # system 记录
    assert rows[2] is None            # 轮次 1 分隔
    assert rows[3] is records[2]      # user 记录
    # 无第二个用户块 → 仅 1 个分隔行
    assert sum(1 for r in rows if r is None) == 1


def test_build_records_tool_metadata():
    """工具记录：摘要 = 调用（工具名+detail）；result = 返回首行预览。"""
    records, _ = build_trace_records(_make_model_with_blocks())
    tool = next(r for r in records if r.kind == "tool")
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
    tool = next(r for r in records if r.kind == "tool")
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
    assert [r.kind for r in records] == ["tools", "system", "user", "reasoning", "content", "tool"]


def test_build_records_detail_lazy():
    """块记录详情惰性：records.lines 为空，block_detail_lines 按需提取。"""
    m = _make_model_with_blocks()
    records, _ = build_trace_records(m)
    user = next(r for r in records if r.kind == "user")
    assert user.lines == []
    lines = block_detail_lines(user.source_block)
    assert lines == ["> 你好"]


def test_build_records_running_tool_duration():
    """运行中工具耗时 = now - _tool_started_at（未关闭时）。"""
    m = AppModel()
    b = m.append_block("tool")
    b.extra.update(tool_name="bash", tool_status="running", _tool_started_at=10.0)
    records, _ = build_trace_records(m)
    tool = next(r for r in records if r.kind == "tool")
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
    """TraceView：头部 + 左右布局（左台账 / 分隔 / 右检查器）。

    ★ 全面控件化（方案B）：台账左栏经标准控件 ``ListView`` 表达——检查
    控件 props（items/cursor/renderItem）与 renderItem 行渲染。
    """
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 4  # records[4]（content 回答）
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
    left, sep, right_col = parts
    # 左栏 = ListView 标准控件（items = rows 含分隔行；cursor = 选中记录行号）
    from src.tui.ink.widgets.listview import ListView
    assert left.type is ListView, f"左栏应为 ListView 控件: {left.type}"
    lv = left.props
    items = lv["items"]
    # tools + system + 轮次分隔 + user/reasoning/content/tool
    assert len(items) == 7
    assert lv["cursor"] == 5  # records[4]（content 回答）在 rows 中下标 5
    # renderItem：工具列表行（🧰 + # 0 + 工具列表）
    tools_el = lv["renderItem"](items[0], 0, False)
    tools_text = "".join(r.text for r in tools_el.props.get("styled", []))
    assert "\U0001F9F0" in tools_text
    assert "# 0" in tools_text
    assert "工具列表" in tools_text
    # renderItem：系统提词行（⚙ + #1）
    sys_el = lv["renderItem"](items[1], 1, False)
    sys_text = "".join(r.text for r in sys_el.props.get("styled", []))
    assert "\u2699" in sys_text
    assert sys_text.startswith("  # 1")
    # renderItem：轮次分隔行
    sep_el = lv["renderItem"](items[2], 2, False)
    assert "轮次 1" in "".join(r.text for r in sep_el.props.get("styled", []))
    # renderItem：选中行（content #4，isSelected=True）带 ▶
    sel_el = lv["renderItem"](items[5], 5, True)
    sel_text = "".join(r.text for r in sel_el.props.get("styled", []))
    assert sel_text.startswith("\u25b6")
    assert "# 4" in sel_text
    # renderItem：工具行（#5）：调用 + 返回预览合并一条（· file1.txt）
    tool_el = lv["renderItem"](items[6], 6, False)
    tool_text = "".join(r.text for r in tool_el.props.get("styled", []))
    assert "# 5" in tool_text
    assert "· file1.txt" in tool_text
    # 右栏：检查器标题为 #4 回答
    assert str(right_col.children[0].props.get("children", "")).startswith("#4 回答")


def test_trace_view_tail_follow():
    """trace_selected=-1（跟随尾部）→ ListView 受控光标指向最新记录。"""
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = -1
    el, _ = _render(TraceView, {"model": m, "width": 100})
    row_el = list(el.children)[1]
    parts = list(row_el.children)
    left = parts[0]
    # 尾部跟随：ListView cursor = 最新记录（tool #5）在 rows 中下标 6
    assert left.props["cursor"] == 6, f"跟随尾部应定位 tool 记录: {left.props['cursor']}"
    # renderItem 验证该行为 ▶ 选中
    items = left.props["items"]
    tool_el = left.props["renderItem"](items[6], 6, True)
    text = "".join(r.text for r in tool_el.props.get("styled", []))
    assert text.startswith("\u25b6")
    assert "# 5" in text


def _input_handler(fiber):
    """从 fiber hooks 中取出 use_input 注册的 InputHook handler。"""
    for hook in fiber.hooks:
        if getattr(hook, "is_active", None) is not None and hasattr(hook, "handler"):
            return hook.handler
    raise AssertionError("fiber 中无 use_input hook")


def test_trace_view_navigation_writes_model():
    """↑↓ 导航（经 ListView 控件）写入 model.trace_selected（退出尾部跟随）。

    ★ 全面控件化（方案B）：导航由 ListView 消费，onNavigate 回调写回
    model.trace_selected——经 Reconciler 完整渲染（含 ListView hooks）
    验证 router 事件链路；事件后重建元素树模拟渲染循环（cursor prop 更新）。
    """
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = -1
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    # 记录：tools(0) system(1) user(2) reasoning(3) content(4) tool(5)
    # 上移：从尾部（#5 工具，rows 下标 6）到 #4 回答
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 4
    # 下一帧重建（渲染循环）→ 上移继续
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 3
    # 下移
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down", raw=b"\x1b[B")) is True
    assert m.trace_selected == 4
    # End → 末行（工具 #5）：导航到最后一条 → 写 -1（尾部跟随语义——新行
    # 追加自动选择最新行，用户需求 2026-08-17）
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="end", raw=b"\x1b[F")) is True
    assert m.trace_selected == -1, "导航到末行应进入尾部跟随（-1）"
    # g → 首条（工具列表 #0）
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="g", raw=b"g")) is True
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
    """消息列表 → 记录：工具列表 / system×2 / 轮次分隔 / user / 思考 /
    工具 / 回答。"""
    records, rows = _records_from_messages(_sample_messages())
    assert [r.kind for r in records] == ["tools", "system", "system", "user", "reasoning", "tool", "content"]
    assert [r.index for r in records] == [0, 1, 2, 3, 4, 5, 6]
    # 轮次分隔：system 记录后、user 记录前
    assert rows[0] is records[0]  # tools 记录
    assert rows[1] is records[1]
    assert rows[2] is records[2]
    assert rows[3] is None
    assert rows[4] is records[3]
    # tools 记录：index=0 + summary=工具列表 + 每行一个工具（原名）
    tools_rec = records[0]
    assert tools_rec.index == 0
    assert tools_rec.summary == "工具列表"
    assert tools_rec.lines, "工具列表应非空（一行一个工具原名）"
    # system 记录：摘要 = 首行；lines = 全文
    assert records[1].summary == "你是一位乐于助人的软件工程师助手。"
    assert records[2].lines == ["# 当前执行环境", "- OS: Windows"]


def test_records_from_messages_tool_call_result_merged():
    """工具调用 + 返回合并一条：summary = 调用；result = 返回首行；
    lines = 调用行 + 返回行。"""
    records, _ = _records_from_messages(_sample_messages())
    tool = next(r for r in records if r.kind == "tool")
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
    assert [r.kind for r in records] == ["tools", "user", "tool"]
    assert records[2].summary == "工具返回"
    assert records[2].result == "孤儿返回"


def test_build_trace_records_message_source_preferred():
    """build_trace_records 优先使用 message_source（agent 消息列表）；
    未注入时回退块路径。"""
    m = _make_model_with_blocks()
    m.message_source = lambda: _sample_messages()
    records, _ = build_trace_records(m)
    assert [r.kind for r in records] == ["tools", "system", "system", "user", "reasoning", "tool", "content"]
    # 清除消息源 → 回退块路径（工具列表 + 系统提词 + 块记录）
    m.message_source = None
    records2, _ = build_trace_records(m)
    assert [r.kind for r in records2] == ["tools", "system", "user", "reasoning", "content", "tool"]


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
# 8. 实时生成内容动态显示（2026-08-19 用户需求）
# ═══════════════════════════════════════════════════════════
# 消息源（agent.messages）仅在流式完成后才追加 assistant 消息/工具返回——
# 模型生成期间（思考/回答/工具执行中）轨迹台账须动态显示正在生成的内容：
# 开放块（reasoning/content 未关闭）→ running 记录；运行中工具 → running
# tool 记录；流式完成（块关闭 + 消息追加）后实时记录消失由消息记录接管
# （无重复）；实时指纹驱动 use_memo 重建（流式期间消息指纹不变）。

def _open_content_block(model, text: str):
    """构造一个开放（未关闭）的 content 块——模拟流式生成中。"""
    model.content_block_index = len(model.blocks)
    block = model.append_block("content")
    block.lines.append(AnsiLine.of(text))
    return block


def test_live_records_message_source_streaming_content():
    """消息源模式：流式生成中的 content 块 → 台账尾部 running content 记录。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    _open_content_block(m, "正在生成的第一行")
    records, rows = build_trace_records(m)
    assert [r.kind for r in records] == ["tools", "user", "content"]
    live = records[-1]
    assert live.kind == "content"
    assert live.status == "running"
    assert live.summary == "正在生成的第一行"
    assert live.lines == ["正在生成的第一行"]
    assert live.index == 3
    assert rows[-1] is live


def test_live_records_message_source_streaming_reasoning():
    """消息源模式：流式生成中的 reasoning 块 → running reasoning 记录。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    m.reasoning_block_index = len(m.blocks)
    block = m.append_block("reasoning")
    block.lines.append(AnsiLine.of("先思考一下"))
    records, _ = build_trace_records(m)
    assert [r.kind for r in records] == ["tools", "user", "reasoning"]
    live = records[-1]
    assert live.status == "running"
    assert live.summary == "先思考一下"
    assert live.lines == ["先思考一下"]


def test_live_records_message_source_running_tool():
    """消息源模式：运行中的工具 → running tool 记录（调用/输出预览/耗时）。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "查一下"}]
    m.open_tool_box("call_1", "bash", "ls -la")
    m.append_tool_output("call_1", "file1.txt\nfile2.txt")
    records, rows = build_trace_records(m)
    assert [r.kind for r in records] == ["tools", "user", "tool"]
    tool = records[-1]
    assert tool.status == "running"
    assert tool.summary == "bash ls -la"
    assert tool.result == "file1.txt"                      # 输出首行预览
    assert tool.time_seconds is not None and tool.time_seconds >= 0
    assert tool.lines[0] == "bash ls -la"                  # 调用行
    assert [ln.strip() for ln in tool.lines[1:]] == ["file1.txt", "file2.txt"]
    assert rows[-1] is tool


def test_live_records_disappear_after_stream_done():
    """流式完成（块关闭 + 消息追加）→ 实时记录消失，消息记录接管（无重复）。"""
    msgs = [{"role": "user", "content": "hi"}]
    m = AppModel()
    m.message_source = lambda: msgs
    block = _open_content_block(m, "生成中...")
    records, _ = build_trace_records(m)
    assert records[-1].kind == "content" and records[-1].status == "running"
    # 流式完成：块关闭 + assistant 消息追加到消息源
    block.closed = True
    msgs.append({"role": "assistant", "content": "生成完成", "reasoning_content": None})
    records2, _ = build_trace_records(m)
    assert [r.kind for r in records2] == ["tools", "user", "content"]
    done = records2[-1]
    assert done.kind == "content" and done.status != "running"
    assert done.summary == "生成完成"
    assert len(records2) == 3  # 无重复：仅消息记录，实时记录已消失


def test_live_records_skip_closed_blocks():
    """实时记录仅覆盖未关闭块——已关闭块（历史/已完成）不重复追加。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    b = _open_content_block(m, "生成中")
    b.closed = True  # 已关闭（如异常路径未关闭时不被计为实时）
    records, _ = build_trace_records(m)
    assert [r.kind for r in records] == ["tools", "user"]
    assert all(r.status != "running" for r in records)


def test_live_fingerprint_tracks_streaming_growth():
    """实时指纹：开放块内容增长 / 工具输出增长触发变化；完成后回退基线。"""
    m = AppModel()
    m.message_source = lambda: []
    fp_base = _live_fingerprint(m)
    assert fp_base == ()
    block = _open_content_block(m, "第一行")
    fp1 = _live_fingerprint(m)
    assert fp1 != fp_base
    block.lines.append(AnsiLine.of("第二行（流式追加）"))
    fp2 = _live_fingerprint(m)
    assert fp1 != fp2, "开放块行数增长应触发指纹变化"
    m.open_tool_box("t1", "bash", "pwd")
    fp3 = _live_fingerprint(m)
    assert fp2 != fp3, "新增运行中工具应触发指纹变化"
    # 流式完成：关闭块 → 指纹回退（仅剩运行中工具）
    block.closed = True
    fp4 = _live_fingerprint(m)
    assert fp4 != fp3 and fp4 != fp_base
    m.close_tool_box("t1", True)
    assert _live_fingerprint(m) == fp_base, "全部完成后指纹回退基线"


def test_record_from_block_open_block_running_status():
    """块回退路径（无消息源）：开放 reasoning/content 块 → running 状态。"""
    m = AppModel()
    rb = m.append_block("reasoning")
    rb.lines.append(AnsiLine.of("思考中"))
    cb = m.append_block("content")
    cb.lines.append(AnsiLine.of("生成中"))
    records, _ = build_trace_records(m)
    live = [r for r in records if r.kind in ("reasoning", "content") and r.status == "running"]
    assert len(live) == 2
    assert live[0].kind == "reasoning" and live[0].summary == "思考中"
    assert live[1].kind == "content" and live[1].summary == "生成中"


def test_trace_view_message_source_shows_live_records():
    """消息源模式 + 流式生成中：TraceView 台账动态显示 ● running 记录。"""
    from src.tui.ink.widgets.listview import ListView
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    _open_content_block(m, "正在流式生成的内容")
    m.trace_open = True
    m.trace_selected = -1  # 跟随尾部
    el, _ = _render(TraceView, {"model": m, "width": 100})
    row_el = list(el.children)[1]
    left = list(row_el.children)[0]
    assert left.type is ListView
    items = left.props["items"]
    # rows: [工具列表, 轮次分隔, user 记录, content 实时记录]——尾部跟随选中实时记录
    assert len(items) == 4
    live_el = left.props["renderItem"](items[3], 3, True)
    text = "".join(r.text for r in live_el.props.get("styled", []))
    assert text.startswith("\u25b6"), "尾部跟随应选中实时记录"
    assert "\u25cf" in text, "运行中 ● 图标应显示"
    assert "正在流式生成的内容" in text


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

    # e2e 前置（review 方向）：工具列表断言依赖 ToolRegistry 自动发现成功
    # ——显式初始化注册表并确认非空（自动发现个别模块失败被吞时降级为空，
    #   台账无「工具列表」行，避免隐性测试耦合）。
    from src.tools.registry import ToolRegistry
    assert ToolRegistry.default().get_tools(), "e2e 前置：注册表应非空（工具列表断言依赖）"

    session, model, stream = _make_session()
    session.start()
    _t.sleep(0.15)
    try:
        _push_history(session, rounds=3)
        _t.sleep(0.5)
        toggle = _make_trace_toggle_cb(model, session)

        # ★ 稳定性（2026-08-16）：真实渲染循环（10Hz）在 xdist 并行/高负载
        #   下可能延迟——固定 sleep 偶发不足导致断言误报。改为轮询等待
        #   pyte 重放屏幕状态满足条件（带超时兜底），保留原断言语义。
        def _screen_text():
            scr = pyte.Screen(80, 24)
            pyte.Stream(scr).feed(stream.getvalue())
            return "\n".join(scr.display)

        def _wait_text(cond, timeout=5.0):
            deadline = _t.time() + timeout
            while _t.time() < deadline:
                if cond(_screen_text()):
                    return True
                _t.sleep(0.1)
            return cond(_screen_text())

        # ── 打开轨迹视图 ──
        toggle()
        assert _wait_text(lambda t: "轨迹 Trace" in t), "轨迹头部应显示"
        joined = _screen_text()
        assert "工具列表" in joined, "# 0 工具列表应在台账显示"
        assert "轮次 1" in joined, "轮次分隔应显示"
        assert "回答 2" in joined, "台账摘要/检查器应含最新回答"
        assert "\u2726" not in joined, "消息区标题栏（✦）不应显示"
        assert "输入消息" not in joined, "全屏模式：输入区不应显示"
        assert "标准模式" not in joined, "全屏模式：输入区模式行不应显示"

        # ── 关闭轨迹视图 ──
        toggle()
        assert _wait_text(lambda t: "轨迹 Trace" not in t), "轨迹头部应消失"
        joined2 = _screen_text()
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


# ═══════════════════════════════════════════════════════════
# 9. subagent 轨迹嵌套（2026-08-16 用户需求）
# ═══════════════════════════════════════════════════════════
# 在轨迹 Trace 界面按回车，如果选中的是 subagent 记录，轨迹就显示该
# subagent 的轨迹 Trace（内容与 mainagent 一样：左台账 + 右检查器，
# 数据源 = SubAgent 完整消息列表 → system/user/思考/回答/工具记录）。

def test_register_subagent_injects_messages():
    """register_subagent 把 SubAgent.messages/prompt 引用注入槽位（实时增长）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="running")

        class _StubAgent:
            def __init__(self):
                self.messages = [{"role": "user", "content": "读取 user.py"}]
                self.prompt = "读取 user.py"

        agent = _StubAgent()
        ctl.register_subagent("agent-1", agent)
        slot = ctl._store._agents["agent-1"]
        assert slot.messages is agent.messages, "messages 应为同一列表引用"
        assert slot.prompt == "读取 user.py"
        # 实时增长（同一引用——轨迹视图跟随显示最新内容）
        agent.messages.append({"role": "assistant", "content": "解析完成。",
                               "reasoning_content": None})
        assert slot.messages is agent.messages
        assert len(slot.messages) == 2
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（register_subagent 写入）


def test_build_subagent_trace_records_from_messages():
    """subagent 轨迹：slot.messages → 与 mainagent 同构记录（消息列表构建）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [
            {"role": "system", "content": "你是子代理。"},
            {"role": "user", "content": "读取 user.py"},
            {"role": "assistant", "content": None,
             "reasoning_content": "先读文件。",
             "tool_calls": [{"id": "c1", "function": {
                 "name": "read_file", "arguments": '{"path": "user.py"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "class User: ..."},
            {"role": "assistant", "content": "解析完成。", "reasoning_content": None},
        ]
        records, rows = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in records] == ["tools", "system", "user", "reasoning", "tool", "content"]
        # 首条 # 0 工具列表（用户需求 2026-08-17：subagent 轨迹同主轨迹）
        assert records[0].kind == "tools"
        assert records[0].index == 0
        assert records[0].summary == "工具列表"
        assert records[0].lines, "工具列表应非空（一行一个工具原名）"
        # 工具调用 + 返回合并一条（与 mainagent 语义一致）
        tool = next(r for r in records if r.kind == "tool")
        assert tool.summary == "read_file user.py"
        assert tool.result == "class User: ..."
        # 与 mainagent 的 _records_from_messages 完全同构
        main_records, _ = _records_from_messages(slot.messages)
        assert [r.kind for r in main_records] == [r.kind for r in records]
        assert next(r for r in main_records if r.kind == "tool").summary == tool.summary
    finally:
        ctl._store.clear()


def test_build_subagent_trace_records_fallback_slot():
    """无 messages（未注册）→ 回退槽位活动记录（工具列表 + 提词 + 工具
    历史 + 结果）。"""
    from src.tui._subagent_state import _ToolRecord
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        slot = ctl._store._agents["agent-1"]
        slot.prompt = "读取 user.py"
        slot.result_text = "解析完成"
        rec = _ToolRecord(tool_name="read_file", detail="user.py")
        rec.phase = "done"
        slot.tool_history.append(rec)
        records, rows = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in records] == ["tools", "user", "tool", "content"]
        assert records[0].kind == "tools" and records[0].summary == "工具列表"
        assert records[1].summary == "读取 user.py"
        assert records[2].summary == "read_file user.py"
        assert records[2].status == "done"
        assert records[3].summary == "解析完成"
        assert rows[-1] is records[-1]
    finally:
        ctl._store.clear()


def test_build_subagent_trace_records_missing_slot():
    """槽位不存在 → 空记录（防御，不崩溃）。"""
    records, rows = build_subagent_trace_records("ghost-agent", None)
    assert records == []
    assert rows == []


def test_subagent_trace_deps_tracks_growth_and_status():
    """subagent 轨迹指纹：消息增长 / 状态变化触发重建。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析", status="running")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [{"role": "user", "content": "hi"}]
        fp1 = _subagent_trace_deps("agent-1")
        # 消息增长 → 指纹变化
        slot.messages.append({"role": "assistant", "content": "ok",
                              "reasoning_content": None})
        fp2 = _subagent_trace_deps("agent-1")
        assert fp1 != fp2, "消息增长应触发指纹变化"
        # 状态变化 → 指纹变化
        slot.status = "done"
        fp3 = _subagent_trace_deps("agent-1")
        assert fp2 != fp3, "状态变化应触发指纹变化"
        # 无变化 → 稳定
        assert _subagent_trace_deps("agent-1") == fp3
        # 槽位缺失 → 指纹含 missing 标记（区别于正常）
        assert _subagent_trace_deps("ghost") != fp3
    finally:
        ctl._store.clear()


def test_build_trace_records_message_source_appends_subagent():
    """消息源模式：subagent 记录也追加（subagent_label 填充——Enter 下钻数据源）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        records, rows = build_trace_records(m)
        assert records[-1].kind == "subagent", "消息源模式应含 subagent 记录"
        sub = records[-1]
        assert sub.subagent_label == "agent-1"
        assert rows[-1] is sub
    finally:
        ctl._store.clear()


def test_inspector_subagent_hint():
    """subagent 记录检查器：末尾追加「Enter 查看子代理轨迹」提示。"""
    rec = TraceRecord(
        index=1, kind="subagent", summary="agent-1 · 解析模块",
        subagent_label="agent-1", lines=["解析模块"],
    )
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert any("Enter" in t and "子代理" in t for t in texts), "应有下钻提示"


def test_inspector_non_subagent_no_hint():
    """非 subagent 记录检查器无下钻提示（零回归）。"""
    rec = TraceRecord(index=1, kind="content", summary="回答", lines=["hi"])
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert not any("子代理" in t for t in texts)


def test_trace_view_enter_subagent_opens_subagent_trace():
    """主轨迹 Enter subagent 记录 → 进入 subagent 轨迹（嵌套 TraceView）：
    标题变「子代理轨迹」；Esc 返回主轨迹；再次 Esc 关闭整个视图。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [
            {"role": "user", "content": "读取 user.py"},
            {"role": "assistant", "content": "解析完成。", "reasoning_content": None},
        ]
        m = _make_model_with_blocks()
        m.trace_open = True
        m.trace_selected = -1  # 跟随尾部（subagent 记录为末条）

        rec = Reconciler()
        root = rec.create_root()
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        # Enter（选中 subagent 记录）→ 进入 subagent 轨迹
        assert router(KeyEvent(kind="enter", raw=b"\r")) is True
        assert m.trace_subagent_label == "agent-1"
        assert m.trace_selected == -1
        assert m.trace_open is True

        # 下一帧渲染 → subagent 轨迹（数据源 = subagent messages）
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        el, fiber = _render(TraceView, {"model": m, "width": 100})
        header = list(el.children)[0]
        header_text = "".join(r.text for r in header.props.get("styled", []))
        assert "子代理轨迹 agent-1" in header_text, "subagent 轨迹标题应显示 label"
        assert "轨迹 Trace" not in header_text.split("·")[0], "主轨迹标题应替换"

        # subagent 轨迹台账数据源 = subagent messages（user + 回答）
        row_el = list(el.children)[1]
        from src.tui.ink.widgets.listview import ListView
        left = list(row_el.children)[0]
        assert left.type is ListView
        items = left.props["items"]
        texts = []
        for item in items:
            if item is None:
                continue
            texts.append(item.summary)
        assert "读取 user.py" in texts
        assert "解析完成。" in texts

        # Esc → 返回主轨迹（trace_open 保持 True）
        handler = _input_handler(fiber)
        assert handler(KeyEvent(kind="escape", raw=b"\x1b")) is True
        assert m.trace_subagent_label is None
        assert m.trace_open is True

        # 再次 Esc → 关闭整个轨迹视图
        el2, fiber2 = _render(TraceView, {"model": m, "width": 100})
        handler2 = _input_handler(fiber2)
        assert handler2(KeyEvent(kind="escape", raw=b"\x1b")) is True
        assert m.trace_open is False
    finally:
        ctl._store.clear()


def test_trace_view_enter_non_subagent_passes_through():
    """主轨迹 Enter 非 subagent 记录 → 放行（不进入 subagent 轨迹）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = _make_model_with_blocks()  # 无 subagent 记录
    m.trace_open = True
    m.trace_selected = -1
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="enter", raw=b"\r")) is False
    assert getattr(m, "trace_subagent_label", None) is None
    assert m.trace_open is True


def test_trace_view_ctrl_h_returns_from_subagent_trace():
    """subagent 轨迹内 Ctrl+H → 返回主轨迹（不直接关闭整个视图）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [{"role": "user", "content": "读取 user.py"}]
        m = _make_model_with_blocks()
        m.trace_open = True
        m.trace_selected = -1
        m.trace_subagent_label = "agent-1"  # 已在 subagent 轨迹
        rec = Reconciler()
        root = rec.create_root()
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        # Ctrl+H → 返回主轨迹（不关闭 trace_open）
        assert router(KeyEvent(kind="ctrl_key", char="\x08", raw=b"\x08")) is True
        assert m.trace_subagent_label is None
        assert m.trace_open is True
    finally:
        ctl._store.clear()


# ═══════════════════════════════════════════════════════════
# 9.5 已完成 subagent 轨迹保留（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════
# 用户需求：subagent 已完成后仍能按 Enter 查看其轨迹（历史复盘）。修复前
# ParallelExecutor 完成后 ``_panel.stop()`` 清空面板 store → 主轨迹 subagent
# 记录消失、Enter 无法下钻。修复：``register_subagent`` 写入**轨迹存档**
# （``_trace_archive``），``stop()`` 清空 store 后存档保留 → 主轨迹仍显示
# 已完成 subagent 记录、Enter 仍可进入查看完整轨迹（同 label 新任务注册
# 时覆盖为最近一次）。

class _StubSubAgent:
    """SubAgent 桩（messages/prompt 属性，对齐 register_subagent 读取面）。"""

    def __init__(self, messages, prompt):
        self.messages = messages
        self.prompt = prompt


def _register_done_slot(ctl, label="agent-1", description="解析模块"):
    """构造「已完成 subagent」：面板槽位（done）+ 轨迹存档（store 已清空）。

    ★ 模拟语义：用 ``ctl._store.clear()`` 模拟 ``ParallelExecutor`` 完成后
    ``stop()`` 清空面板 store 的效果（``stop()`` 在测试环境因 ``_active``
    为 False 直接返回，且其完整路径——取消订阅/推空帧/动画回调注销——与
    存档保留语义无关；本测试聚焦「store 清空后存档保留」）。
    """
    ctl._store.clear()
    ctl.clear_trace_archive()
    ctl._store.add_agent(label, description, status="running")
    agent = _StubSubAgent(
        messages=[
            {"role": "user", "content": "读取 user.py"},
            {"role": "assistant", "content": "解析完成。", "reasoning_content": None},
        ],
        prompt="读取 user.py",
    )
    ctl.register_subagent(label, agent)
    ctl._store.update_status(label, "done")
    ctl._store.clear()  # 模拟 ParallelExecutor 完成后 stop() 清空面板 store
    return ctl


def test_stop_preserves_trace_archive_completed_subagent():
    """stop() 清空面板 store 后轨迹存档保留 → 主轨迹仍显示已完成 subagent
    记录（done）且可构建完整 subagent 轨迹。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = _register_done_slot(SubAgentPanelController.get_default())
    try:
        # ① 面板 store 已清空（渲染状态复位）
        assert ctl._store._order == []
        assert ctl._store._agents == {}
        # ② 轨迹存档保留（label → 槽位引用）
        assert "agent-1" in ctl._trace_archive
        slot = ctl._trace_archive["agent-1"]
        assert slot.status == "done"
        # ③ 主轨迹（消息源模式）仍追加已完成 subagent 记录
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        records, rows = build_trace_records(m)
        sub = records[-1]
        assert sub.kind == "subagent"
        assert sub.status == "done"
        assert sub.subagent_label == "agent-1"
        assert rows[-1] is sub
        # ④ 完成后仍可构建完整 subagent 轨迹（消息 → 台账记录）
        sub_records, _ = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in sub_records] == ["tools", "user", "content"]
        assert sub_records[0].kind == "tools" and sub_records[0].summary == "工具列表"
        assert sub_records[1].summary == "读取 user.py"
        assert sub_records[2].summary == "解析完成。"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_subagent_slot_prefers_store_then_archive():
    """_subagent_slot：优先面板 store（运行中），store 缺失回退轨迹存档
    （已完成——store 清空后仍可构建轨迹）。"""
    from src.tui.app.trace import _subagent_slot
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        # 仅存档有槽位（store 清空模拟已完成）→ 回退存档
        ctl._store.add_agent("archived", "存档任务", status="done")
        ctl.register_subagent("archived", _StubSubAgent(
            messages=[{"role": "user", "content": "hi"}], prompt="hi"))
        ctl._store.clear()
        slot = _subagent_slot("archived")
        assert slot is not None and slot.status == "done"
        # store 有新槽位（运行中）而存档为旧槽位 → 优先 store
        ctl._store.add_agent("archived", "新任务", status="running")
        new_slot = ctl._store._agents["archived"]
        assert _subagent_slot("archived") is new_slot
        # 均不存在 → None
        assert _subagent_slot("ghost") is None
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_trace_view_enter_completed_subagent_after_store_clear():
    """主轨迹 Enter 已完成 subagent（store 已清空）→ 仍进入 subagent 轨迹
    显示完整内容；Esc 返回主轨迹。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.widgets.listview import ListView
    from src.tui.subagent import SubAgentPanelController
    ctl = _register_done_slot(SubAgentPanelController.get_default())
    try:
        m = _make_model_with_blocks()
        m.trace_open = True
        m.trace_selected = -1  # 跟随尾部（subagent 记录为末条）
        rec = Reconciler()
        root = rec.create_root()
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        # Enter（选中已完成 subagent 记录）→ 进入 subagent 轨迹
        assert router(KeyEvent(kind="enter", raw=b"\r")) is True
        assert m.trace_subagent_label == "agent-1"
        assert m.trace_open is True
        # 下一帧渲染 → subagent 轨迹（数据源 = 存档槽位消息）
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        el, _ = _render(TraceView, {"model": m, "width": 100})
        header = list(el.children)[0]
        header_text = "".join(r.text for r in header.props.get("styled", []))
        assert "子代理轨迹 agent-1" in header_text, "已完成 subagent 轨迹标题应显示 label"
        row_el = list(el.children)[1]
        left = list(row_el.children)[0]
        assert left.type is ListView
        texts = [item.summary for item in left.props["items"] if item is not None]
        assert "读取 user.py" in texts
        assert "解析完成。" in texts
        # Esc → 返回主轨迹（trace_open 保持 True）
        handler = _input_handler(_render(TraceView, {"model": m, "width": 100})[1])
        assert handler(KeyEvent(kind="escape", raw=b"\x1b")) is True
        assert m.trace_subagent_label is None
        assert m.trace_open is True
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_clear_trace_archive_removes_completed_records():
    """clear_trace_archive 清空存档 → 主轨迹不再显示已完成 subagent 记录。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = _register_done_slot(SubAgentPanelController.get_default())
    try:
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        records, _ = build_trace_records(m)
        assert records[-1].kind == "subagent", "存档保留时主轨迹应含 subagent 记录"
        ctl.clear_trace_archive()
        records2, _ = build_trace_records(m)
        assert all(r.kind != "subagent" for r in records2), \
            "存档清空后主轨迹不应再有 subagent 记录"
        assert build_subagent_trace_records("agent-1", None) == ([], [])
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


# ═══════════════════════════════════════════════════════════
# 9.6 load 命令恢复 subagent 轨迹（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════
# 用户需求：load 命令（/load、--load 启动）也要支持「已完成
# subagent 仍可 Enter 查看轨迹」，且不实现第二份构建逻辑。方案：会话数据
# 中的 ``subagents``（``_subagent_records`` 条目，含完整 messages）经
# ``SubAgentPanelController.restore_trace_archive`` 转换为槽位注入轨迹存档
# ——主轨迹显示、Enter 下钻、轨迹构建全部复用运行时同一套（``_trace_archive``
# → ``_subagent_records``/``build_subagent_trace_records``）。

def _sample_subagent_record(label="agent-1", status="done"):
    """会话数据 subagents 条目（对齐 SubAgent._record_to_parent 结构）。"""
    return {
        "label": label,
        "description": "解析模块",
        "agent_type": "execute",
        "prompt": "读取 user.py",
        "status": status,
        "result": "解析完成。",
        "error": "",
        "tool_calls_count": 1,
        "messages": [
            {"role": "system", "content": "你是子代理。"},
            {"role": "user", "content": "读取 user.py"},
            {"role": "assistant", "content": "解析完成。", "reasoning_content": None},
        ],
    }


def test_restore_trace_archive_from_session_records():
    """restore_trace_archive：会话 subagents 记录 → 存档槽位（字段映射），
    主轨迹显示历史 subagent、可构建完整轨迹。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        ctl.restore_trace_archive([_sample_subagent_record()])
        # ① 存档槽位字段映射
        slot = ctl._trace_archive["agent-1"]
        assert slot.status == "done"
        assert slot.description == "解析模块"
        assert slot.prompt == "读取 user.py"
        assert slot.result_text == "解析完成。"
        assert slot.result_error == ""
        assert len(slot.messages) == 3
        # ② 主轨迹（消息源模式）显示历史 subagent 记录
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        records, rows = build_trace_records(m)
        sub = records[-1]
        assert sub.kind == "subagent"
        assert sub.status == "done"
        assert sub.subagent_label == "agent-1"
        assert rows[-1] is sub
        # ③ 可构建完整 subagent 轨迹（复用 _records_from_messages）
        sub_records, _ = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in sub_records] == ["tools", "system", "user", "content"]
        assert sub_records[0].kind == "tools" and sub_records[0].summary == "工具列表"
        assert sub_records[2].summary == "读取 user.py"
        assert sub_records[3].summary == "解析完成。"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_restore_trace_archive_replaces_previous():
    """restore_trace_archive 为替换语义：新会话存档取代旧会话（同/异 label）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        # 先注入旧会话存档（两个 label）
        ctl.restore_trace_archive([
            _sample_subagent_record("agent-1"),
            _sample_subagent_record("agent-2"),
        ])
        assert set(ctl._trace_archive) == {"agent-1", "agent-2"}
        # 再恢复新会话（仅 agent-1，且内容更新）→ 旧 agent-2 移除、agent-1 覆盖
        rec = _sample_subagent_record("agent-1")
        rec["result"] = "新会话结果。"
        ctl.restore_trace_archive([rec])
        assert set(ctl._trace_archive) == {"agent-1"}
        assert ctl._trace_archive["agent-1"].result_text == "新会话结果。"
        # 空列表 → 清空存档
        ctl.restore_trace_archive([])
        assert ctl._trace_archive == {}
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_trace_view_enter_restored_subagent_after_load():
    """load 恢复存档后：主轨迹 Enter 历史 subagent → 进入 subagent 轨迹
    显示完整内容；Esc 返回主轨迹。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.widgets.listview import ListView
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        ctl.restore_trace_archive([_sample_subagent_record()])
        m = _make_model_with_blocks()
        m.trace_open = True
        m.trace_selected = -1  # 跟随尾部（subagent 记录为末条）
        rec = Reconciler()
        root = rec.create_root()
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        # Enter（选中历史 subagent 记录）→ 进入 subagent 轨迹
        assert router(KeyEvent(kind="enter", raw=b"\r")) is True
        assert m.trace_subagent_label == "agent-1"
        # 下一帧渲染 → subagent 轨迹（数据源 = 存档槽位消息）
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        el, _ = _render(TraceView, {"model": m, "width": 100})
        header = list(el.children)[0]
        header_text = "".join(r.text for r in header.props.get("styled", []))
        assert "子代理轨迹 agent-1" in header_text, "恢复的 subagent 轨迹标题应显示 label"
        row_el = list(el.children)[1]
        left = list(row_el.children)[0]
        assert left.type is ListView
        texts = [item.summary for item in left.props["items"] if item is not None]
        assert "读取 user.py" in texts
        assert "解析完成。" in texts
        # Esc → 返回主轨迹
        handler = _input_handler(_render(TraceView, {"model": m, "width": 100})[1])
        assert handler(KeyEvent(kind="escape", raw=b"\x1b")) is True
        assert m.trace_subagent_label is None
        assert m.trace_open is True
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_session_persistence_load_restores_trace_archive():
    """SessionPersistenceManager.load（--load 路径）→ 恢复轨迹存档。"""
    from src.core.internal.session._session_persistence_manager import (
        SessionPersistenceManager,
    )
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    ctl._store.clear()
    ctl.clear_trace_archive()

    class _StubPersistence:
        def load_session(self, sid):
            return {
                "messages": [{"role": "user", "content": "历史消息"}],
                "subagents": [_sample_subagent_record()],
                "model": "deepseek",
            }

        def save_session(self, *args, **kwargs):
            return "saved-id"

        def list_sessions(self):
            return []

    class _StubObs:
        def gauge(self, *a, **k):
            pass

    msgs = [{"role": "system", "content": "sys"}]
    mgr = SessionPersistenceManager(
        messages_getter=lambda: msgs,
        model_getter=lambda: "deepseek",
        model_setter=lambda v: None,
        session_id_getter=lambda: "",
        session_id_setter=lambda v: None,
        persistence_port=_StubPersistence(),
        checkpoint_port=None,
        state_machine=None,
        emit_fn=lambda *a, **k: None,
        observability_port=_StubObs(),
        subagents_getter=lambda: [],
        subagents_setter=lambda v: None,
    )
    try:
        data = mgr.load("hist-1")
        assert data is not None
        assert ctl._trace_archive["agent-1"].status == "done"
        sub_records, _ = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in sub_records] == ["tools", "system", "user", "content"]
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_cmd_load_restores_trace_archive():
    """/load 命令（_cmd_load）→ 恢复轨迹存档（主轨迹可查看历史 subagent 轨迹）。"""
    from src.core.commands._data_cmd import _cmd_load
    from src.core.internal.commands._command_core import CommandContext
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    ctl._store.clear()
    ctl.clear_trace_archive()

    class _StubPersistence:
        def load_session(self, sid):
            return {
                "messages": [{"role": "user", "content": "历史消息"}],
                "subagents": [_sample_subagent_record()],
                "model": "deepseek",
                "title": "历史会话",
            }

        def save_session(self, *args, **kwargs):
            return "saved-id"

        def list_sessions(self):
            return []

    msgs = [{"role": "system", "content": "sys"}]
    ctx = CommandContext(
        messages=msgs, state={}, arg="hist-1",
        build_system_prompt=None, get_user_input=None, context_manager=None,
        session=None, persistence_port=_StubPersistence(), ui_adapter=None,
    )
    try:
        assert _cmd_load(ctx) is True
        assert "agent-1" in ctl._trace_archive
        sub_records, _ = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in sub_records] == ["tools", "system", "user", "content"]
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_cmd_clear_clears_trace_archive():
    """/clear 命令（_cmd_clear）→ 同步清空轨迹存档（跨会话残留一致性，
    review 方向：/clear 清空 _subagent_records 但不同步清 _trace_archive）。"""
    from src.core.commands._session_cmd import _cmd_clear
    from src.core.internal.commands._command_core import CommandContext
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        ctl.restore_trace_archive([_sample_subagent_record()])
        assert "agent-1" in ctl._trace_archive
        ctx = CommandContext(
            messages=[{"role": "system", "content": "sys"}],
            state={}, arg="",
            build_system_prompt=lambda: ["sys"],
            get_user_input=None, context_manager=None,
            session=None, persistence_port=None, ui_adapter=None,
        )
        assert _cmd_clear(ctx) is True
        assert ctl._trace_archive == {}, "/clear 应同步清空轨迹存档"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_register_same_label_overwrites_archive():
    """同 label 新批次注册覆盖存档为最近一次（用户确认取舍：仅保留最近
    一次——与持久化 _subagent_records 同 label 覆盖语义一致）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        # 批次 1：注册 agent-1（任务一）
        ctl._store.add_agent("agent-1", "任务一", status="running")
        ctl.register_subagent("agent-1", _StubSubAgent(
            messages=[{"role": "user", "content": "任务一提词"}], prompt="任务一提词"))
        assert ctl._trace_archive["agent-1"].prompt == "任务一提词"
        # 批次 1 完成（store 清空）→ 批次 2 同 label 注册（任务二）
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "任务二", status="running")
        ctl.register_subagent("agent-1", _StubSubAgent(
            messages=[{"role": "user", "content": "任务二提词"}], prompt="任务二提词"))
        # 存档仅保留最近一次（任务二）
        assert len(ctl._trace_archive) == 1
        assert ctl._trace_archive["agent-1"].prompt == "任务二提词"
        sub_records, _ = build_subagent_trace_records("agent-1", None)
        assert sub_records[0].kind == "tools", "subagent 轨迹首条仍为 # 0 工具列表"
        assert sub_records[1].summary == "任务二提词"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_restore_trace_archive_error_status():
    """restore 映射 status="error"（_record_to_parent 终态之一）→ 主轨迹
    subagent 记录状态为 error（✖ 图标）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        rec = _sample_subagent_record()
        rec["status"] = "error"
        rec["error"] = "模型调用失败"
        rec["result"] = ""
        ctl.restore_trace_archive([rec])
        slot = ctl._trace_archive["agent-1"]
        assert slot.status == "error"
        assert slot.result_error == "模型调用失败"
        assert slot.result_text == ""
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        records, _ = build_trace_records(m)
        sub = records[-1]
        assert sub.status == "error"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_restore_trace_archive_messages_defensive():
    """restore messages 逐条防御：非 dict 元素跳过，其余消息保留；非 list
    消息字段 → 空列表（review 方向：单条损坏不整批置空）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        rec = _sample_subagent_record()
        rec["messages"] = [
            {"role": "user", "content": "正常消息"},
            None,
            "损坏",
            {"role": "assistant", "content": "正常回答", "reasoning_content": None},
        ]
        ctl.restore_trace_archive([rec])
        slot = ctl._trace_archive["agent-1"]
        assert [m["role"] for m in slot.messages] == ["user", "assistant"]
        # 非 list messages → 空列表（防御）
        rec2 = _sample_subagent_record("agent-2")
        rec2["messages"] = "not-a-list"
        ctl.restore_trace_archive([rec2])
        assert ctl._trace_archive["agent-2"].messages == []
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


# ═══════════════════════════════════════════════════════════
# 10. subagent 动态部分显示（2026-08-16 用户需求：跟 mainagent 一样）
# ═══════════════════════════════════════════════════════════
# subagent 轨迹的动态部分（运行中内容）与 mainagent 轨迹语义一致：
# 运行中工具 → running tool 记录；运行中模型阶段（思考/生成）→ running
# reasoning/content 占位记录；完成后由消息记录接管（无重复）。

def _make_running_slot(label="agent-1", description="实时解析"):
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    ctl._store.clear()
    ctl.clear_trace_archive()  # 轨迹存档一并清理（防旧槽位遮蔽/泄漏）
    ctl._store.add_agent(label, description, status="running")
    return ctl, ctl._store._agents[label]


def test_subagent_live_records_running_tool():
    """subagent 动态部分：运行中工具 → running tool 记录（调用/耗时/● running）。"""
    from src.tui._subagent_state import _ToolRecord
    from src.tui.app.trace import _subagent_live_records
    ctl, slot = _make_running_slot()
    try:
        rec = _ToolRecord(tool_name="read_file", detail="user.py")
        rec.phase = "running"
        slot.tool_history.append(rec)
        out, rows = [], []
        _subagent_live_records([0], out, rows, slot)
        assert len(out) == 1
        assert out[0].kind == "tool"
        assert out[0].status == "running"
        assert out[0].summary == "read_file user.py"
        assert out[0].result == "（运行中…）"
        assert out[0].time_seconds is not None and out[0].time_seconds >= 0
        assert rows[-1] is out[0]
    finally:
        ctl._store.clear()


def test_subagent_live_records_thinking_and_answering():
    """subagent 动态部分：thinking → 实际思考内容；answering → 思考+回答内容。"""
    from src.tui.app.trace import _subagent_live_records
    ctl, slot = _make_running_slot()
    try:
        slot.model_phase = "thinking"
        slot.live_reasoning = "第一行思考\n第二行思考"
        out, rows = [], []
        _subagent_live_records([0], out, rows, slot)
        assert [r.kind for r in out] == ["reasoning"]
        assert out[0].status == "running"
        assert out[0].summary == "第一行思考"
        assert out[0].lines == ["第一行思考", "第二行思考"]
        assert rows[-1] is out[0]
        # answering → 思考（本轮若有）+ 回答（正在生成的实际内容）
        slot.model_phase = "answering"
        slot.live_content = "正在生成回答"
        out2, rows2 = [], []
        _subagent_live_records([0], out2, rows2, slot)
        assert [r.kind for r in out2] == ["reasoning", "content"]
        assert out2[0].kind == "reasoning" and out2[0].summary == "第一行思考"
        assert out2[1].kind == "content"
        assert out2[1].summary == "正在生成回答"
        assert out2[1].lines == ["正在生成回答"]
    finally:
        ctl._store.clear()


def test_subagent_live_records_no_live_content_skips():
    """subagent 动态部分：阶段已到但无流式内容（尚未生成）→ 不显示占位
    （对齐 mainagent _live_records：开放块无行不追加记录）。"""
    from src.tui.app.trace import _subagent_live_records
    ctl, slot = _make_running_slot()
    try:
        slot.model_phase = "thinking"
        out, rows = [], []
        _subagent_live_records([0], out, rows, slot)
        assert out == []
        slot.model_phase = "answering"
        _subagent_live_records([0], out, rows, slot)
        assert out == []
    finally:
        ctl._store.clear()


def test_subagent_live_records_skips_non_running():
    """subagent 动态部分：非 running 状态（done/fail/error）无动态记录——
    即使残留 live 内容/阶段也不显示（消息记录已接管）。"""
    from src.tui.app.trace import _subagent_live_records
    ctl, slot = _make_running_slot()
    try:
        slot.status = "done"
        slot.model_phase = "thinking"
        slot.live_reasoning = "残留思考内容"  # 终态不应显示
        out, rows = [], []
        _subagent_live_records([0], out, rows, slot)
        assert out == []
    finally:
        ctl._store.clear()


def test_store_append_live_accumulates_and_phase_resets():
    """StateStore.append_live 累积流式内容；set_model_phase 新阶段重置。"""
    ctl, slot = _make_running_slot()
    try:
        ctl._store.append_live("agent-1", "reasoning", "思考")
        ctl._store.append_live("agent-1", "reasoning", "内容")
        ctl._store.append_live("agent-1", "content", "回答")
        assert slot.live_reasoning == "思考内容"
        assert slot.live_content == "回答"
        # 非 subagent label（main agent）无槽位 → 零成本跳过
        ctl._store.append_live("main", "reasoning", "x")
        assert slot.live_reasoning == "思考内容"
        # 空文本跳过
        ctl._store.append_live("agent-1", "reasoning", "")
        assert slot.live_reasoning == "思考内容"
        # 新阶段 thinking → 重置 live_reasoning
        ctl._store.set_model_phase("agent-1", "thinking", "")
        assert slot.live_reasoning == ""
        assert slot.live_content == "回答"  # content 不受 thinking 影响
        ctl._store.append_live("agent-1", "reasoning", "新思考")
        # 新阶段 answering → 重置 live_content
        ctl._store.set_model_phase("agent-1", "answering", "")
        assert slot.live_content == ""
        assert slot.live_reasoning == "新思考"  # reasoning 不受 answering 影响
        ctl._store.append_live("agent-1", "content", "新回答")
        ctl._store.append_live("agent-1", "content", "续")
        assert slot.live_content == "新回答续"
        # 同阶段重复事件不重置（accumulate 继续）
        ctl._store.set_model_phase("agent-1", "answering", "")
        assert slot.live_content == "新回答续"
    finally:
        ctl._store.clear()


def test_build_subagent_trace_records_with_live_dynamic():
    """subagent 轨迹：消息记录 + 动态部分（运行中工具 / 流式思考内容）合并。"""
    from src.tui._subagent_state import _ToolRecord
    ctl, slot = _make_running_slot()
    try:
        slot.messages = [
            {"role": "user", "content": "读取 user.py"},
            {"role": "assistant", "content": "解析完成。", "reasoning_content": None},
        ]
        # 运行中工具 + 思考阶段（实际流式内容动态累积）
        rec = _ToolRecord(tool_name="grep", detail="class User")
        rec.phase = "running"
        slot.tool_history.append(rec)
        slot.model_phase = "thinking"
        slot.live_reasoning = "先搜索 class User 的定义"
        records, rows = build_subagent_trace_records("agent-1", None)
        kinds = [r.kind for r in records]
        # tools + user + content（消息记录）→ tool running（动态）→ reasoning（动态）
        assert kinds == ["tools", "user", "content", "tool", "reasoning"]
        assert records[0].kind == "tools" and records[0].summary == "工具列表"
        assert records[-2].status == "running"
        assert records[-2].summary == "grep class User"
        assert records[-1].kind == "reasoning" and records[-1].status == "running"
        assert records[-1].summary == "先搜索 class User 的定义"
        # 完成后：工具 phase done + 阶段清空 + live 被 messages 接管 → 动态消失
        rec.phase = "done"
        slot.model_phase = ""
        slot.live_reasoning = ""
        slot.status = "done"
        records2, _ = build_subagent_trace_records("agent-1", None)
        assert [r.kind for r in records2] == ["tools", "user", "content"]
        assert all(r.status != "running" for r in records2)
    finally:
        ctl._store.clear()


def test_subagent_trace_deps_tracks_live_dynamic():
    """subagent 轨迹指纹：模型阶段 / 工具 phase / 流式内容长度变化触发重建。"""
    from src.tui._subagent_state import _ToolRecord
    ctl, slot = _make_running_slot()
    try:
        slot.messages = [{"role": "user", "content": "hi"}]
        fp1 = _subagent_trace_deps("agent-1")
        # 工具 parsing → running → done 状态推进
        rec = _ToolRecord(tool_name="read_file", detail="a.py")
        rec.phase = "parsing"
        slot.tool_history.append(rec)
        fp2 = _subagent_trace_deps("agent-1")
        assert fp1 != fp2, "新增工具记录应触发指纹变化"
        rec.phase = "running"
        fp3 = _subagent_trace_deps("agent-1")
        assert fp2 != fp3, "工具 phase 变化应触发指纹变化"
        rec.phase = "done"
        slot.model_phase = "thinking"
        fp4 = _subagent_trace_deps("agent-1")
        assert fp3 != fp4, "模型阶段变化应触发指纹变化"
        # 流式内容增长 → 指纹变化（动态部分逐帧重建）
        slot.live_reasoning = "思考第一段"
        fp5 = _subagent_trace_deps("agent-1")
        assert fp4 != fp5, "流式思考内容增长应触发指纹变化"
        slot.live_reasoning = "思考第一段续写"
        fp6 = _subagent_trace_deps("agent-1")
        assert fp5 != fp6, "流式思考内容续写应触发指纹变化"
        slot.live_content = "回答内容"
        fp7 = _subagent_trace_deps("agent-1")
        assert fp6 != fp7, "流式回答内容出现应触发指纹变化"
        # 无变化 → 稳定
        assert _subagent_trace_deps("agent-1") == fp7
    finally:
        ctl._store.clear()


# ═══════════════════════════════════════════════════════════
# 11. 检查器省略提示（2026-08-16 用户需求：思考/回答改「前 N 行省略」）
# ═══════════════════════════════════════════════════════════
# 思考（reasoning）/回答（content）为流式生成内容——检查器优先显示最新
# 内容（尾部），被截断时置顶提示「… 前 N 行省略」；其余种类从头部显示
# （省略尾部，「… 后 N 行省略」）。

def test_inspector_content_tail_first_omitted_front():
    """回答（content）：长内容尾部优先显示 + 「… 前 N 行省略」置顶。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = [f"旧行{i}" for i in range(80)]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    # 省略提示置顶（元信息之后、内容之前）且为「前 N 行省略」
    assert "省略" in texts[1] and "前" in texts[1], texts[:3]
    assert "后" not in texts[1]
    # 旧内容不显示、最新内容显示（尾部优先）
    assert "旧行0" not in texts
    assert "旧行79" in texts


def test_inspector_reasoning_tail_first_omitted_front():
    """思考（reasoning）：长内容尾部优先显示 + 「… 前 N 行省略」置顶。"""
    rec = TraceRecord(index=1, kind="reasoning", summary="思考")
    rec._detail_lines = [f"思考行{i}" for i in range(50)]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert any("省略" in t and "前" in t for t in texts)
    assert "思考行0" not in texts
    assert "思考行49" in texts


def test_inspector_tool_keeps_tail_omitted():
    """工具（tool）：保持从头部显示 + 「… 后 N 行省略」**后置在内容之后**。

    省略的是尾部内容 → 提示应显示在内容行之后的最后一行（对齐 toolcard
    头显示语义；修复前统一前置在内容上方与语义不符）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash ls")
    rec._detail_lines = [f"输出行{i}" for i in range(80)]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert any("省略" in t and "后" in t for t in texts)
    assert "输出行0" in texts, "工具从头部显示"
    assert "输出行79" not in texts
    # 位置：省略提示在内容行之后（最后一行——非 tail_first 后置语义）
    omitted_idx = next(i for i, t in enumerate(texts) if "省略" in t)
    assert omitted_idx == len(texts) - 1, f"省略提示应在最后一行: {texts}"
    assert all("输出行" not in t for t in texts[omitted_idx + 1:]), "提示之后不应有内容行"


def test_inspector_user_head_first_omitted_after_content():
    """用户（user）：head-first 省略尾部 → 「… 后 N 行省略」在内容行之后。"""
    rec = TraceRecord(index=1, kind="user", summary="长提问")
    rec._detail_lines = [f"用户行{i}" for i in range(60)]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    omitted = [t for t in texts if "省略" in t]
    assert omitted and "后" in omitted[0], f"应显示后 N 行省略: {texts}"
    assert texts[-1] == omitted[0], f"省略提示应在最后一行: {texts}"
    assert "用户行0" in texts, "head-first 从头部显示"
    assert "用户行59" not in texts, "尾部省略"


def test_inspector_short_content_no_omitted_hint():
    """短内容（思考/回答）不截断 → 无省略提示（零回归）。"""
    rec = TraceRecord(index=1, kind="content", summary="短回答")
    rec._detail_lines = ["短内容"]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert not any("省略" in t for t in texts)
    assert "短内容" in texts


# ═══════════════════════════════════════════════════════════
# 12. 检查器流式 markdown 渲染（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════
# 用户需求：轨迹 Trace 的回答（content）/思考（reasoning）在右侧检查器
# 用流式 markdown 显示——标题/粗体/行内码/代码高亮/表格等格式化（与聊天区
# 内容渲染同管线），内容增长（流式生成中）自动重渲染（rec 缓存）。


def _styled_texts(children):
    """收集检查器内容行的纯文本（children 文本 = styled 行的纯文本）。"""
    return [str(c.props.get("children", "")) for c in children]


def test_inspector_content_markdown_rendered():
    """回答（content）：markdown 语法渲染为格式化内容行（styled StyledRun）。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = ["**粗体文字** 和 `行内码`", "普通段落"]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = _styled_texts(children)
    assert any("粗体文字" in t for t in texts), "粗体语法应被渲染（语法字符剥离）"
    assert any("行内码" in t for t in texts), "行内码语法应被渲染（语法字符剥离）"
    assert any("普通段落" in t for t in texts)
    assert not any("**" in t or "`" in t for t in texts), "markdown 语法字符不应残留"
    # 含「粗体文字」的内容行：粗体 run 带 bold 样式（styled StyledRun）
    styled_rows = [c.props.get("styled") for c in children]
    bold_row = next(
        (r for r in styled_rows if r and any("粗体文字" in rr.text for rr in r)),
        None,
    )
    assert bold_row is not None, "markdown 行应携带 styled runs"
    assert any(getattr(r.style, "bold", False) for r in bold_row), "粗体应带 bold 样式"


def test_inspector_reasoning_markdown_dim():
    """思考（reasoning）：markdown 渲染 + 暗灰弱化样式叠加（fg=242）。"""
    rec = TraceRecord(index=1, kind="reasoning", summary="思考")
    rec._detail_lines = ["**粗体** 思考内容"]
    children = _inspector_children(rec, right_w=40, vh=10)
    styled_rows = [c.props.get("styled") for c in children]
    styled = next((r for r in styled_rows if r), None)
    assert styled, "应渲染出内容行"
    for r in styled:
        assert getattr(r.style, "fg", None) == 242, "reasoning 行应全部叠加暗灰弱化"
    texts = _styled_texts(children)
    assert any("粗体" in t for t in texts)
    assert any("思考内容" in t for t in texts)


def test_inspector_content_markdown_code_block_highlight():
    """回答含代码块：pygments 高亮色保留（StyledRun 带 fg 色）。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = ["```python", "print(1)", "```"]
    children = _inspector_children(rec, right_w=40, vh=10)
    styled_rows = [c.props.get("styled", []) for c in children]
    code_rows = [r for r in styled_rows
                 if any("print" in rr.text for rr in r)]
    assert code_rows, "代码行应显示"
    assert any(getattr(rr.style, "fg", None) is not None
               for rr in code_rows[0]), "代码高亮应保留 fg 色"


def test_md_detail_rows_wraps_long_lines():
    """markdown 渲染行按 right_w 样式安全换行（超长行不超宽）。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = ["x" * 200]  # 无空格断点长行
    rows = _md_detail_rows(rec, 30, "content")
    from src.tui._width import wcswidth_simple
    for runs in rows:
        w = sum(wcswidth_simple(r.text) for r in runs)
        assert w <= 30, f"行宽 {w} 超过 30"
    assert len(rows) >= 7, "200 字符应拆成多行"


def test_md_detail_rows_cache_hit_and_invalidation():
    """markdown 渲染缓存：同 lines 引用命中；lines 变化（新 list）重渲染。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = ["第一版内容"]
    rows1 = _md_detail_rows(rec, 40, "content")
    # 同 rec + 同 lines → 缓存命中（同对象）
    rows2 = _md_detail_rows(rec, 40, "content")
    assert rows1 is rows2, "同 deps 应返回同一缓存对象（零重渲染）"
    # lines 变化（流式增长——live 记录每次重建新 list）→ 重渲染
    rec._detail_lines = ["第一版内容", "第二版（流式追加）"]
    rows3 = _md_detail_rows(rec, 40, "content")
    assert rows3 is not rows1, "lines 变化应重渲染"
    joined = "".join(r.text for runs in rows3 for r in runs)
    assert "第二版（流式追加）" in joined
    # 宽度变化 → 重渲染（换行结果不同）
    rows4 = _md_detail_rows(rec, 20, "content")
    assert rows4 is not rows3


def test_inspector_content_markdown_streaming_growth():
    """流式增长：内容追加后检查器显示最新（markdown 重渲染 + tail 优先）。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = ["初始内容"]
    texts1 = _styled_texts(_inspector_children(rec, right_w=40, vh=10))
    assert any("初始内容" in t for t in texts1)
    # 流式追加（新 list——live 记录重建语义）
    rec._detail_lines = ["初始内容", "**追加回答**"]
    texts2 = _styled_texts(_inspector_children(rec, right_w=40, vh=10))
    assert any("追加回答" in t for t in texts2), "流式追加内容应显示"


def test_inspector_content_markdown_from_block_falls_back_plain():
    """块回退路径（source_block）：直接复用块渲染输出行（不二次解析）。

    块内容是**渲染后输出**（如代码块标题行 `` ```python [python]``）——不
    二次 markdown 解析（否则标题行被误判为「语言 + 首行内容」出现多余
    ``[python]`` 行）；检查器直接显示块 AnsiLine（runs 转 StyledRun）。
    """
    m = AppModel()
    # 模拟渲染后输出：代码块标题行 + 代码内容（块路径——无内联 markdown 源码）
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("```python [python]"))
    b.lines.append(AnsiLine.of("print('hi')"))
    b.lines.append(AnsiLine.of("```"))
    b.closed = True
    m.commit_block(0)
    records, _ = build_trace_records(m)
    content = next(r for r in records if r.kind == "content")
    assert content.lines == [], "块记录无内联源码（惰性提取）"
    # md 渲染 = 块输出行直接复用（一行 ```python [python]，不拆成两行）
    from src.tui.app.trace_view import _md_detail_rows
    rows = _md_detail_rows(content, 40, "content")
    assert len(rows) == 3, "块输出行原样复用（不二次解析拆行）"
    assert any("```python [python]" in "".join(r.text for r in runs) for runs in rows)
    assert not any("".join(r.text for r in runs).strip() == "[python]"
                   for runs in rows), "不应二次解析出 [python] 行"
    children = _inspector_children(content, right_w=40, vh=10)
    texts = _styled_texts(children)
    assert any("```python [python]" in t for t in texts), "块渲染输出行原样显示"
    assert any("print('hi')" in t for t in texts)


def test_inspector_reasoning_markdown_no_inline_fallback():
    """思考（reasoning）无内联源码（块回退）→ 纯文本渲染（零回归）。"""
    m = AppModel()
    b = m.append_block("reasoning")
    b.lines.append(AnsiLine.of("思考中..."))
    b.closed = True
    m.commit_block(0)
    records, _ = build_trace_records(m)
    reasoning = next(r for r in records if r.kind == "reasoning")
    children = _inspector_children(reasoning, right_w=40, vh=10)
    texts = _styled_texts(children)
    assert any("思考中..." in t for t in texts)


def test_to_tui_style_maps_renderer_style():
    """renderer.ansi.Style → tui Style：字段映射 + RGB 元组 → TrueColor。"""
    from src.renderer.ansi.style import Style as Rs
    # RGB 元组 fg → TrueColor
    st = _to_tui_style(Rs(fg=(255, 128, 0), bold=True, dim=True), "content")
    assert st is not None
    assert st.bold is True and st.dim is True
    assert getattr(st.fg, "r", None) == 255
    # int fg 直接映射
    st2 = _to_tui_style(Rs(fg=45, underline=True), "content")
    assert st2.fg == 45 and st2.underline is True
    # None → None（content 不叠加）
    assert _to_tui_style(None, "content") is None
    # reasoning 叠加暗灰弱化（None → fg=242；渲染色被覆盖 + 布尔属性保留）
    st3 = _to_tui_style(None, "reasoning")
    assert st3 is not None and st3.fg == 242
    st4 = _to_tui_style(Rs(fg=45, bold=True), "reasoning")
    assert st4.fg == 242, "reasoning 弱化样式 fg 应覆盖渲染色"
    assert st4.bold is True, "布尔属性（bold）应保留"


def test_inspector_system_markdown_rendered():
    """系统（system）记录：markdown 语法渲染（标题/列表/粗体剥离）——用户需求
    「system 也用 markdown」。"""
    rec = TraceRecord(index=1, kind="system", summary="核心目标", lines=[
        "你是一位乐于助人的软件工程师助手。",
        "## 安全红线",
        "- 禁止 rm -rf",
        "**重要** 说明",
    ])
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = _styled_texts(children)
    assert texts[0].startswith("#1 系统"), "标题行保持"
    assert any("你是一位乐于助人的软件工程师助手。" in t for t in texts)
    assert any(t.strip() == "安全红线" for t in texts), "标题 ## 语法应剥离（渲染为标题）"
    assert any("• 禁止 rm -rf" in t for t in texts), "列表 - 应渲染为 • 列表项"
    assert any("重要 说明" in t for t in texts), "** 粗体语法应剥离"
    assert not any("**" in t for t in texts), "markdown 语法字符不应残留"
    # 标题渲染带青色粗体样式（markdown 格式化）
    styled_rows = [c.props.get("styled") for c in children]
    title_row = next(r for r in styled_rows if r and any(rr.text == "安全红线" for rr in r))
    assert any(getattr(rr.style, "bold", False) for rr in title_row), "标题应带 bold 样式"


def test_inspector_system_block_reused():
    """system 块记录（error 块 → source_block）：直接复用块输出行（不二次解析）。"""
    from src.tui.app.trace_view import _md_detail_rows
    m = AppModel()
    m.append_committed("error", [AnsiLine.of("  ! 错误信息")])
    records, _ = build_trace_records(m)
    sys_from_block = next(r for r in records
                          if r.kind == "system" and r.source_block is not None)
    rows = _md_detail_rows(sys_from_block, 40, "system")
    assert rows, "块记录应直接复用块输出行"
    assert any("错误信息" in "".join(r.text for r in runs) for runs in rows)
    children = _inspector_children(sys_from_block, right_w=40, vh=10)
    texts = _styled_texts(children)
    assert any("错误信息" in t for t in texts)


def test_inspector_system_tail_omitted_kept_head_first():
    """system 记录（head-first）：长提示词从头部显示 + 「… 后 N 行省略」。
    markdown 渲染后行数超视口时省略尾部（system 非流式内容——零回归）；
    省略提示后置在内容行之后（最后一行）。"""
    rec = TraceRecord(index=1, kind="system", summary="提示词")
    rec._detail_lines = [f"提示词行{i}" for i in range(80)]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = _styled_texts(children)
    assert any("省略" in t and "后" in t for t in texts), "system 保持后 N 行省略"
    assert any("提示词行0" in t for t in texts), "system 从头部显示"
    assert not any("提示词行79" in t for t in texts), "尾部省略"
    # 位置：省略提示在内容行之后（最后一行——head-first 后置语义）
    omitted_idx = next(i for i, t in enumerate(texts) if "省略" in t)
    assert omitted_idx == len(texts) - 1, f"省略提示应在最后一行: {texts}"
    assert all("提示词行" not in t for t in texts[omitted_idx + 1:]), "提示之后不应有内容行"


# ═══════════════════════════════════════════════════════════
# 13. 流式 live 记录的 markdown 渲染（2026-08-17 用户需求补充）
# ═══════════════════════════════════════════════════════════
# live 记录（流式生成中）挂 source_block → 检查器直接复用块渲染输出行
# （AnsiLine 已带 markdown 样式），不二次解析；端到端验证流式追加结构化
# 内容（标题/列表）在检查器动态显示。

def test_live_records_carry_source_block():
    """live 记录（消息源流式）挂 source_block → 检查器直接复用块渲染输出。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    block = _open_content_block(m, "生成中")
    records, _ = build_trace_records(m)
    live = records[-1]
    assert live.kind == "content" and live.status == "running"
    assert live.source_block is block, "live 记录应挂 source_block（块渲染输出复用）"
    from src.tui.app.trace_view import _md_detail_rows
    rows = _md_detail_rows(live, 40, "content")
    assert rows, "块输出行应直接复用"
    assert any("生成中" in "".join(r.text for r in runs) for runs in rows)
    # 复用块行（行数 = 块行数——不二次解析拆行）
    assert len(rows) == len(block.lines)


def test_block_styled_rows_cache_and_growth():
    """_block_styled_rows：同 block.lines 引用命中；行数增长（流式）增量转换。"""
    from src.tui.app.trace_view import _block_styled_rows
    m = AppModel()
    block = m.append_block("content")
    block.lines.append(AnsiLine.of("第一行"))
    rows1 = _block_styled_rows(block, 40, "content")
    rows2 = _block_styled_rows(block, 40, "content")
    assert rows1 is rows2, "同 deps 应命中缓存（零重渲染）"
    # 流式增长：block.lines 原地 extend → 增量转换新增行（同一列表对象，
    # 内容更新——避免每帧全量重建 O(n²)）
    block.lines.append(AnsiLine.of("**追加**"))
    rows3 = _block_styled_rows(block, 40, "content")
    assert rows3 is rows1, "增量路径复用同一列表对象（仅追加新增行）"
    assert len(rows3) == 2, "新增行应已转换追加"
    assert any("追加" in "".join(r.text for r in runs) for runs in rows3)
    # 宽度变化 → 全量重建（换行结果不同，同一引用列表不够）
    rows4 = _block_styled_rows(block, 20, "content")
    assert rows4 is not rows3, "宽度变化应全量重建"


def test_e2e_trace_view_streaming_markdown_live():
    """端到端：轨迹打开期间流式追加结构化 markdown（标题/列表——段落缓冲
    除外即时 flush）→ 检查器动态显示渲染后的 markdown 文本。"""
    import time as _t

    import pyte

    from src.tui._assembly_steps import _make_trace_toggle_cb
    from src.tui._const import ContentCmd, MainPhaseCmd

    session, model, stream = _make_session(height=30, width=120)
    session.start()
    _t.sleep(0.15)
    try:
        _push_history(session, rounds=1)
        _t.sleep(0.3)
        toggle = _make_trace_toggle_cb(model, session)
        toggle()
        _t.sleep(0.3)
        # 流式追加结构化内容（标题/列表项即时 flush——段落缓冲除外）
        session.push_cmd(MainPhaseCmd(phase="answering"))
        session.push_cmd(ContentCmd(text="## 追加标题\n\n- 流式列表项\n"))

        def _screen_text():
            scr = pyte.Screen(120, 30)
            pyte.Stream(scr).feed(stream.getvalue())
            return "\n".join(scr.display)

        def _wait_text(cond, timeout=5.0):
            deadline = _t.time() + timeout
            while _t.time() < deadline:
                if cond(_screen_text()):
                    return True
                _t.sleep(0.1)
            return cond(_screen_text())

        assert _wait_text(lambda t: "追加标题" in t), "流式追加标题应显示（markdown 渲染）"
        joined = _screen_text()
        assert "流式列表项" in joined, "流式列表项应渲染显示"
        # 尾部跟随：最新 live 记录选中（▶ 前缀行含标题摘要）
        sel_lines = [ln for ln in joined.split("\n") if ln.startswith("\u25b6")]
        assert sel_lines, "应有台账选中行（▶）"
        assert "追加标题" in sel_lines[-1], "尾部跟随应选中最新追加记录"
    finally:
        session.stop()


# ═══════════════════════════════════════════════════════════
# 14. review 修复回归（2026-08-17）
# ═══════════════════════════════════════════════════════════
# 覆盖 review 发现并修复的问题：markdown 空行占位（P1-1）、省略提示数值
# 不为负且用渲染/换行后总行数（P1-2）、流式期间静态记录内容指纹缓存命中
# 零重渲染（P1-3）、_to_tui_style 色号越界防御（P2-2）、块增量缓存（P2-1）。

def test_md_detail_rows_blank_line_placeholder():
    """markdown 空行（段落/结构分隔）→ 空格占位行（P1-1：空行不丢失）。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = ["第一段", "", "第二段"]
    rows = _md_detail_rows(rec, 40, "content")
    assert len(rows) == 3, "空行应保留为占位行（结构分隔）"
    assert any("".join(r.text for r in runs).strip() == "第一段" for runs in rows)
    assert any("".join(r.text for r in runs) == " " for runs in rows), "空行应显示为空格占位"
    assert any("".join(r.text for r in runs).strip() == "第二段" for runs in rows)


def test_convert_ansi_row_blank_line_placeholder():
    """_convert_ansi_row：空 AnsiLine（无 runs / 空文本）→ 空格占位行。"""
    from src.tui.app.trace_view import _convert_ansi_row
    from src.renderer.ansi.helpers import AnsiLine
    from src.tui.ink import StyledRun as _SR
    rows = _convert_ansi_row(AnsiLine(), 40, "content")
    assert rows == [[_SR(" ", None)]]
    rows2 = _convert_ansi_row(AnsiLine.of(""), 40, "content")
    assert rows2 == [[_SR(" ", None)]]


def test_inspector_tool_omitted_count_not_negative():
    """省略提示数值不为负（P1-2）：tool 单行拆多行显示截断 → 「后 N 行省略」
    用换行后总行数计算（修复前 1-8+1=-2）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash", lines=["x" * 200])
    rec._detail_lines = ["x" * 200]
    children = _inspector_children(rec, right_w=20, vh=8)
    texts = [str(c.props.get("children", "")) for c in children]
    omitted = [t for t in texts if "省略" in t]
    assert omitted, "应显示省略提示"
    assert "后" in omitted[0] and "-" not in omitted[0], f"省略数值不应为负: {omitted[0]}"


def test_md_detail_rows_content_fingerprint_cache():
    """内容指纹缓存（P1-3）：records 流式重建（新 lines 引用、内容未变）→
    命中缓存零重渲染；内容变化 → 重建。"""
    rec = TraceRecord(index=2, kind="content", summary="回答")
    rec._detail_lines = ["内容A", "内容B"]
    rows1 = _md_detail_rows(rec, 40, "content")
    # 新引用同内容（流式期间 records 整体重建语义）→ 指纹命中（同对象）
    rec._detail_lines = ["内容A", "内容B"]
    rows2 = _md_detail_rows(rec, 40, "content")
    assert rows1 is rows2, "同内容新引用应命中缓存（零重渲染）"
    # 内容变化 → 指纹变化 → 重建
    rec._detail_lines = ["内容A", "内容B", "内容C"]
    rows3 = _md_detail_rows(rec, 40, "content")
    assert rows3 is not rows2, "内容变化应重建"
    assert any("内容C" in "".join(r.text for r in runs) for runs in rows3)


def test_lines_fp_stable_for_same_content():
    """_lines_fp：同内容（不同引用）指纹稳定；内容变化指纹变化。"""
    from src.tui.app.trace_view import _lines_fp
    assert _lines_fp(["a", "b"]) == _lines_fp(["a", "b"])
    assert _lines_fp(["a", "b"]) != _lines_fp(["a", "c"])
    # 非 hashable 元素（防御）回退 id（不抛）
    fp = _lines_fp([["a"], ["b"]])
    assert fp is not None


def test_to_tui_style_clamps_invalid_colors():
    """_to_tui_style 色号越界防御（P2-2）：越界 int/RGB 回退 None（不崩溃）。"""
    from src.renderer.ansi.style import Style as Rs
    st = _to_tui_style(Rs(fg=999), "content")
    assert st.fg is None
    st2 = _to_tui_style(Rs(fg=(300, 0, 0)), "content")
    assert st2.fg is None
    st3 = _to_tui_style(Rs(bg=-5), "content")
    assert st3.bg is None
    # 合法值不受影响
    st4 = _to_tui_style(Rs(fg=45), "content")
    assert st4.fg == 45


# ═══════════════════════════════════════════════════════════
# 15. review 二轮修复回归（2026-08-17）
# ═══════════════════════════════════════════════════════════
# 覆盖：模块级内容指纹缓存跨 rec 命中（P1-3 修复目标场景）、bool 色号钳制
# （P1-2 防御缺陷）、_lines_fp 返回类型、md 分支省略提示数值、全空白行、
# 块增量缓存行数倒退/kind 切换全量重建。

def test_md_detail_rows_cross_rec_fingerprint_cache():
    """模块级内容指纹缓存：**不同 rec 对象同内容**（流式期间 records 每帧
    重建 → 新 TraceRecord 的真实场景）→ 命中缓存零重渲染（P1-3 修复目标）。"""
    rec1 = TraceRecord(index=1, kind="system", summary="提示词", lines=["# 标题", "- 项"])
    rows1 = _md_detail_rows(rec1, 40, "system")
    # 流式期间 records 重建：新 rec 对象 + 新 lines 引用，内容相同
    rec2 = TraceRecord(index=1, kind="system", summary="提示词", lines=["# 标题", "- 项"])
    rows2 = _md_detail_rows(rec2, 40, "system")
    assert rows1 is rows2, "跨 rec 同内容应命中模块级缓存（零重渲染）"
    # 内容变化（live 增长）→ miss → 重建
    rec3 = TraceRecord(index=1, kind="system", summary="提示词", lines=["# 标题", "- 项", "追加"])
    rows3 = _md_detail_rows(rec3, 40, "system")
    assert rows3 is not rows2, "内容变化应重建"


def test_to_tui_style_bool_color_clamped():
    """_to_tui_style bool 色号钳制（P1-2）：bool 色号回退 None 不抛
    （Style.__post_init__ 对 bool 显式抛 ValueError——原样放行会崩溃）。"""
    from src.renderer.ansi.style import Style as Rs
    st = _to_tui_style(Rs(fg=True), "content")
    assert st.fg is None, "bool 色号应钳制为 None"
    st2 = _to_tui_style(Rs(fg=False), "content")
    assert st2.fg is None
    # 非 int 非 tuple 色号（float）同样回退 None
    st3 = _to_tui_style(Rs(fg=128.5), "content")
    assert st3.fg is None


def test_lines_fp_returns_int():
    """_lines_fp 返回 int（hash 或 id），非 tuple（review 二轮 P2 类型修复）。"""
    from src.tui.app.trace_view import _lines_fp
    fp = _lines_fp(["a", "b"])
    assert isinstance(fp, int), f"指纹应为 int: {type(fp)}"
    assert _lines_fp(["a", "b"]) == fp  # 同内容稳定


def test_inspector_content_omitted_count_md_tail_first():
    """md 分支省略提示数值（tail_first）：长回答 → 「… 前 N 行省略」N 为正。"""
    rec = TraceRecord(index=1, kind="content", summary="回答")
    rec._detail_lines = [f"内容行{i}" for i in range(50)]
    children = _inspector_children(rec, right_w=40, vh=6)
    texts = [str(c.props.get("children", "")) for c in children]
    omitted = [t for t in texts if "省略" in t]
    assert omitted and "前" in omitted[0], f"应显示前 N 行省略: {omitted}"
    assert "-" not in omitted[0], f"省略数值不应为负: {omitted[0]}"


def test_convert_ansi_row_whitespace_only_line():
    """_convert_ansi_row 全空白行（plain='   '）→ 保留为占位（不丢行）。"""
    from src.tui.app.trace_view import _convert_ansi_row
    from src.renderer.ansi.helpers import AnsiLine
    rows = _convert_ansi_row(AnsiLine.of("   "), 40, "content")
    assert rows, "全空白行应有输出行（空格占位）"
    assert "".join(r.text for r in rows[0]).strip() == "", "内容为空白"


def test_block_styled_rows_rewind_and_kind_switch():
    """_block_styled_rows 行数倒退 / kind 切换 → 全量重建（增量防御分支）。"""
    from src.tui.app.trace_view import _block_styled_rows
    m = AppModel()
    block = m.append_block("content")
    block.lines.append(AnsiLine.of("行一"))
    block.lines.append(AnsiLine.of("行二"))
    rows1 = _block_styled_rows(block, 40, "content")
    assert len(rows1) == 2
    # 行数倒退（非 append-only 异常：pop）→ 全量重建（内容同步）
    block.lines.pop()
    rows2 = _block_styled_rows(block, 40, "content")
    assert rows2 is not rows1, "行数倒退应全量重建"
    assert len(rows2) == 1
    # kind 切换 → 全量重建（样式不同：reasoning 弱化）
    rows3 = _block_styled_rows(block, 40, "reasoning")
    assert rows3 is not rows2, "kind 切换应全量重建"
    assert all(getattr(r.style, "fg", None) == 242 for runs in rows3 for r in runs), \
        "reasoning 行应叠加暗灰弱化"


# ═══════════════════════════════════════════════════════════
# 9. agent 内容合并到 subagent（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════

def _dispatch_messages():
    """一次并行派发两个 subagent 的真实会话消息形态。

    与用户场景（#38-#44 subagent 调用 + #45-#51 agent 完成）一致：
    assistant 一轮 tool_calls 派发多个 subagent（tool_call_id =
    call_d1/call_d2），随后各 tool 返回（"## desc\\n结果" 格式）。
    """
    return [
        {"role": "user", "content": "审查代码"},
        {
            "role": "assistant", "content": None, "reasoning_content": "并行派发审查。",
            "tool_calls": [
                {"id": "call_d1", "function": {"name": "subagent",
                 "arguments": '{"description": "审查 API 层", "prompt": "审吧", "type": "review"}'}},
                {"id": "call_d2", "function": {"name": "subagent",
                 "arguments": '{"description": "审查 core 层", "prompt": "审吧", "type": "review"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_d1", "content": "## 审查 API 层\n结果A"},
        {"role": "tool", "tool_call_id": "call_d2", "content": "## 审查 core 层\n结果B"},
    ]


def test_records_from_messages_tool_call_id_preserved():
    """tool 记录保存 tool_call_id（subagent 合并关联键）。"""
    records, _ = _records_from_messages(_dispatch_messages())
    tool = next(r for r in records if r.kind == "tool")
    assert tool.kind == "tool"
    assert tool.tool_call_id == "call_d1"
    assert "subagent" in tool.summary and "审查 API 层" in tool.summary
    assert tool.result == "## 审查 API 层"  # 返回首行预览（合并前）


def test_add_agent_stores_dispatch_label():
    """StateStore.add_agent 透传 dispatch_label 存入槽位。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="done",
                             dispatch_label="call_d1")
        slot = ctl._store._agents["agent-1"]
        assert slot.dispatch_label == "call_d1"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（对齐既有测试风格，防顺序耦合）


def test_build_trace_records_subagent_merged():
    """用户需求：agent 内容合并到 subagent——带 dispatch_label 的
    subagent 槽位合并进对应 tool 调用记录，**不再生成独立 subagent 记录**。

    台账记录：⚡ ✔ subagent 审查 API 层 · agent-1 审查 API 层（一条）。
    """
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="done",
                             dispatch_label="call_d1")
        ctl._store.add_agent("agent-2", "审查 core 层", status="done",
                             dispatch_label="call_d2")
        slot1 = ctl._store._agents["agent-1"]
        slot1.end_time = slot1.start_time + 3.0
        slot1.input_tokens = 100
        slot1.output_tokens = 50
        slot1.result_text = "P0 发现 1 处"
        slot2 = ctl._store._agents["agent-2"]
        slot2.end_time = slot2.start_time + 2.0

        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, rows = build_trace_records(m)
        kinds = [r.kind for r in records]
        # 无独立 subagent 记录（合并到 tool 记录）
        assert "subagent" not in kinds, f"应无独立 subagent 记录: {kinds}"
        tools = [r for r in records if r.kind == "tool"]
        assert len(tools) == 2, f"两条 subagent 各合并为一条 tool 记录: {len(tools)}"
        # agent-1 → call_d1 合并
        t1 = tools[0]
        assert t1.tool_call_id == "call_d1"
        assert "subagent" in t1.summary and "审查 API 层" in t1.summary
        assert t1.status == "done"
        assert "agent-1" in t1.result and "审查 API 层" in t1.result
        assert t1.subagent_label == "agent-1"       # Enter 可下钻
        assert t1.time_seconds == pytest.approx(3.0, abs=0.01)
        assert t1.tokens["input"] == 100
        assert t1.tokens["output"] == 50
        assert "审查 API 层" in "".join(t1.lines)    # 调用行
        assert "P0 发现 1 处" in t1.lines            # subagent 结果详情
        # agent-2 → call_d2 合并（并行多次 dispatch 精确关联不串位）
        t2 = tools[1]
        assert t2.tool_call_id == "call_d2"
        assert t2.subagent_label == "agent-2"
        assert t2.time_seconds == pytest.approx(2.0, abs=0.01)
        assert rows.count(t1) == 1 and rows.count(t2) == 1
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（对齐既有测试风格，防顺序耦合）


def test_subagent_without_dispatch_label_stays_separate():
    """无 dispatch_label（独立执行/历史恢复槽位）→ 仍生成独立 subagent
    记录（合并仅限 dispatch 关联槽位，零回归）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        records, rows = build_trace_records(m)
        assert records[-1].kind == "subagent", "无 dispatch_label 应保持独立 subagent 记录"
        assert records[-1].subagent_label == "agent-1"
        assert rows[-1] is records[-1]
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（对齐既有测试风格，防顺序耦合）


def test_dispatch_merge_running_status_live():
    """合并记录运行中状态：subagent running → tool 记录 ● running。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="running",
                             dispatch_label="call_d1")
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, _ = build_trace_records(m)
        tools = [r for r in records if r.kind == "tool"]
        assert tools[0].status == "running"
        assert tools[0].time_seconds is None  # 未完成无耗时
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（对齐既有测试风格，防顺序耦合）


def test_dispatch_merge_fail_status():
    """合并记录失败状态：subagent fail/error → tool 记录 ✖（状态图标语义）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="fail",
                             dispatch_label="call_d1")
        slot = ctl._store._agents["agent-1"]
        slot.end_time = slot.start_time + 1.5
        slot.result_error = "超时"
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, _ = build_trace_records(m)
        tools = [r for r in records if r.kind == "tool"]
        assert tools[0].status == "fail"
        assert "错误: 超时" in tools[0].lines
        assert tools[0].time_seconds == pytest.approx(1.5, abs=0.01)
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（对齐既有测试风格，防顺序耦合）


def test_ledger_row_merged_dispatch_shows_agent():
    """合并记录台账行：调用 + agent 摘要预览（``· agent-1 审查 API 层``）。"""
    rec = TraceRecord(
        index=2, kind="tool", summary="subagent 审查 API 层",
        status="done", result="agent-1 · 审查 API 层", time_seconds=3.0,
    )
    runs = _ledger_row_runs(rec, sel=False, left_w=100)
    text = "".join(r.text for r in runs)
    assert "subagent" in text and "审查 API 层" in text, "调用应显示"
    assert "\u2714" in text, "完成状态图标应显示"
    assert "agent-1" in text, "agent 内容应合并显示"
    assert text.endswith("3.0s")


def test_inspector_merged_dispatch_hint():
    """合并后的 subagent 工具记录检查器：显示 Enter 下钻提示。"""
    rec = TraceRecord(
        index=1, kind="tool", summary="subagent 审查 API 层",
        status="done", result="agent-1 · 审查 API 层", subagent_label="agent-1",
        lines=["subagent 审查 API 层", "agent-1 · 审查 API 层", "P0 发现 1 处"],
    )
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert any("Enter" in t and "子代理" in t for t in texts), "合并 tool 记录应有下钻提示"


def test_trace_view_enter_merged_dispatch_opens_subagent_trace():
    """主轨迹 Enter 合并后的 dispatch 记录 → 进入 subagent 轨迹（嵌套）。

    合并记录 kind 为 tool 但携带 subagent_label——下钻条件放宽为
    subagent_label 非空（独立 subagent 记录与合并 tool 记录均可进入）。
    """
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="done",
                             dispatch_label="call_d1")
        ctl._store.add_agent("agent-2", "审查 core 层", status="done",
                             dispatch_label="call_d2")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [
            {"role": "user", "content": "审查 API 层"},
            {"role": "assistant", "content": "审查完成。", "reasoning_content": None},
        ]
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        m.trace_open = True
        m.trace_selected = -1  # 跟随尾部（末条 = 合并后的 call_d2 记录）

        rec = Reconciler()
        root = rec.create_root()
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        assert router(KeyEvent(kind="enter", raw=b"\r")) is True
        assert m.trace_subagent_label == "agent-2", "合并记录 Enter 应进入 subagent 轨迹"
        assert m.trace_open is True
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()  # 轨迹存档清理（对齐既有测试风格，防顺序耦合）


def test_dispatch_merge_error_status():
    """合并记录 error 状态：subagent error → tool 记录 ✖ + 错误详情。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="error",
                             dispatch_label="call_d1")
        slot = ctl._store._agents["agent-1"]
        slot.end_time = slot.start_time + 0.8
        slot.result_error = "模型调用崩溃"
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, _ = build_trace_records(m)
        tools = [r for r in records if r.kind == "tool"]
        assert tools[0].status == "error"
        assert "错误: 模型调用崩溃" in tools[0].lines
        assert tools[0].time_seconds == pytest.approx(0.8, abs=0.01)
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_dispatch_label_without_matching_tool_stays_separate():
    """有 dispatch_label 但消息源无匹配 tool 记录（消息未入源/异常）→
    降级独立 subagent 记录（零丢失，P3 防御）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="done",
                             dispatch_label="call_ghost")
        m = AppModel()
        m.message_source = lambda: _sample_messages()  # 无 subagent 调用
        records, rows = build_trace_records(m)
        assert records[-1].kind == "subagent", "无匹配 tool 记录应降级独立 subagent 记录"
        assert records[-1].subagent_label == "agent-1"
        assert "agent-1" in records[-1].summary
        assert rows[-1] is records[-1]
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_duplicate_dispatch_label_second_slot_stays_separate():
    """同一 dispatch_label 多个槽位（异常数据）→ 首个合并进 tool 记录，
    其余降级独立 subagent 记录——不覆盖首个、不静默丢失（P3 防御）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="done",
                             dispatch_label="call_d1")
        ctl._store.add_agent("agent-2", "重复审查", status="done",
                             dispatch_label="call_d1")
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, _ = build_trace_records(m)
        kinds = [r.kind for r in records]
        assert kinds.count("subagent") == 1, "第二个同 label 槽位应降级独立: {kinds}"
        tools = [r for r in records if r.kind == "tool"]
        assert tools[0].subagent_label == "agent-1", "首个合并不被覆盖"
        assert "agent-2" not in tools[0].result
        sub = next(r for r in records if r.kind == "subagent")
        assert sub.subagent_label == "agent-2"
        assert "重复审查" in sub.summary
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_live_records_skips_merged_dispatch_box():
    """运行期 _live_records 跳过已合并的 dispatch box——dispatch 执行中
    台账也只显示一条（用户需求：不要分两个；修复前运行期两条相同调用行）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "审查 API 层", status="running",
                             dispatch_label="call_d1")
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        # 未关闭的 subagent box（运行中工具，_live_records 会扫描到）
        m.open_tool_box("call_d1", "subagent", "审查 API 层")
        records, rows = build_trace_records(m)
        # call_d1 在台账中只出现一次（合并记录），无 _live_records 重复追加
        call_d1_recs = [r for r in records
                        if getattr(r, "tool_call_id", "") == "call_d1"]
        assert len(call_d1_recs) == 1, f"运行期应只有一条合并记录: {len(call_d1_recs)}"
        rec = call_d1_recs[0]
        assert rec.subagent_label == "agent-1"
        assert rec.status == "running"
        # 未合并的 call_d2 box 正常运行（_live_records 照常追加 running 记录）
        m.open_tool_box("call_d2", "subagent", "审查 core 层")
        records2, _ = build_trace_records(m)
        live_calls = [r.summary for r in records2
                      if r.kind == "tool" and r.status == "running"]
        assert any("审查 core 层" in s for s in live_calls), \
            f"未合并 box 应保留 live 运行记录: {live_calls}"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_restore_trace_archive_preserves_dispatch_label():
    """restore 恢复槽位保留 dispatch_label（load 后合并关联键）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        rec = _sample_subagent_record()
        rec["dispatch_label"] = "call_d1"
        ctl.restore_trace_archive([rec])
        assert ctl._trace_archive["agent-1"].dispatch_label == "call_d1"
        # 旧会话无该字段 → 空串（兼容独立记录）
        ctl.restore_trace_archive([_sample_subagent_record()])
        assert ctl._trace_archive["agent-1"].dispatch_label == ""
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_restored_dispatch_label_merges_after_load():
    """用户需求：load 命令后也要合并——restore 恢复的带 dispatch_label
    槽位合并进主轨迹 subagent 工具记录（不分两条）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        rec = _sample_subagent_record()
        rec["dispatch_label"] = "call_d1"
        rec["description"] = "审查 API 层"
        ctl.restore_trace_archive([rec])
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, rows = build_trace_records(m)
        kinds = [r.kind for r in records]
        assert "subagent" not in kinds, f"load 恢复后应合并（无独立记录）: {kinds}"
        tools = [r for r in records if r.kind == "tool"]
        t1 = tools[0]
        assert t1.tool_call_id == "call_d1"
        assert t1.subagent_label == "agent-1"
        assert "agent-1" in t1.result and "审查 API 层" in t1.result
        assert "解析完成。" in t1.lines  # subagent 结果详情
        assert rows.count(t1) == 1
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


def test_restored_without_dispatch_label_stays_separate():
    """旧会话（无 dispatch_label 字段）恢复 → 独立 subagent 记录（兼容
    load 不破坏既有行为）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        ctl.restore_trace_archive([_sample_subagent_record()])
        m = AppModel()
        m.message_source = lambda: _dispatch_messages()
        records, _ = build_trace_records(m)
        assert records[-1].kind == "subagent", "无 dispatch_label 恢复槽位应独立显示"
        assert records[-1].subagent_label == "agent-1"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


# ═══════════════════════════════════════════════════════════
# 16. # 0 工具列表（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════
# 用户需求：轨迹 Trace 增加一个 # 0 工具列表——右侧检查器显示 agent 所有
# 工具列表，一行一个（显示原名）。用户确认范围：主轨迹 + 子代理轨迹都加。

def test_tools_record_lists_all_registry_tools():
    """# 0 工具列表记录：kind=tools / index=0 / summary=工具列表；
    lines = ToolRegistry 全部工具原名（一行一个，与注册表一致）。"""
    from src.tools.registry import ToolRegistry
    from src.tui.app.trace import _tools_record
    rec = _tools_record()
    assert rec is not None
    assert rec.kind == "tools"
    assert rec.index == 0
    assert rec.summary == "工具列表"
    assert rec.status == ""  # 无状态
    assert rec.time_seconds is None  # 无耗时
    assert rec.tokens == {}  # 无 token 统计
    tools = list(ToolRegistry.default().get_tools())
    assert rec.lines == tools, "工具列表应等于注册表全部工具（原名，一行一个）"
    assert len(rec.lines) >= 10, "内置工具应多于 10 个"
    assert "read_file" in rec.lines and "bash" in rec.lines


def test_tools_record_defensive_registry_failure(monkeypatch):
    """_tools_record 注册表异常 → 返回 None（台账不显示，静默降级零成本）；
    异常结果不缓存（临时状态可在 TTL 内恢复）。"""
    from src.tui.app.trace import _tools_record
    monkeypatch.setattr("src.tui.app.trace._tools_cache", None)  # 清缓存（防前置命中）

    class _Boom:
        @staticmethod
        def default():
            raise RuntimeError("registry broken")

    monkeypatch.setattr("src.tools.registry.ToolRegistry", _Boom)
    assert _tools_record() is None
    # 异常不缓存：再次调用仍走异常路径（返回 None，不抛）
    assert _tools_record() is None


def test_tools_record_empty_registry_returns_none(monkeypatch):
    """注册表为空（自动发现失败/清空）→ _tools_record 返回 None——三处
    插入点（消息源/回退/subagent）均静默降级：build_trace_records 首条
    为 system（#1）、无 tools 记录（review P2：空注册表降级路径）。"""
    from src.tui.app.trace import _tools_record
    monkeypatch.setattr("src.tui.app.trace._tools_cache", None)  # 清缓存

    class _EmptyReg:
        @staticmethod
        def get_tools():
            return {}

    class _EmptyRegistry:
        @staticmethod
        def default():
            return _EmptyReg()

    monkeypatch.setattr("src.tools.registry.ToolRegistry", _EmptyRegistry)
    assert _tools_record() is None
    # 回退路径：无 tools 记录，首条 system（#1）
    records, _ = build_trace_records(_make_model_with_blocks())
    assert records[0].kind == "system", f"空注册表应降级（首条 system）: {records[0].kind}"
    assert records[0].index == 1
    assert all(r.kind != "tools" for r in records)
    # 消息源路径同样降级
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    records2, _ = build_trace_records(m)
    assert records2[0].kind == "user", f"消息源模式空注册表应降级: {records2[0].kind}"
    assert all(r.kind != "tools" for r in records2)


def test_inspector_tools_record_one_per_line():
    """# 0 工具列表检查器：标题 #0 工具列表 + 内容每行一个工具（原名）。"""
    rec = TraceRecord(index=0, kind="tools", summary="工具列表",
                      lines=["read_file", "bash", "web_search"])
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert texts[0].startswith("#0 工具列表"), f"标题应为 #0 工具列表: {texts[0]}"
    # 每行一个工具（原名）
    assert "read_file" in texts
    assert "bash" in texts
    assert "web_search" in texts
    # 无耗时/token 元信息（标题后直接内容行）
    assert not any("耗时" in t or "输入" in t or "输出" in t for t in texts)


def test_ledger_row_tools_record():
    """# 0 工具列表台账行：# 0 + 🧰 图标 + 工具列表摘要；选中高亮。"""
    rec = TraceRecord(index=0, kind="tools", summary="工具列表")
    runs = _ledger_row_runs(rec, sel=False, left_w=40)
    text = "".join(r.text for r in runs)
    assert "# 0" in text
    assert "\U0001F9F0" in text, "工具列表行应显示 🧰 图标"
    assert "工具列表" in text
    # 选中态：▶ 标记 + 背景高亮
    sel_runs = _ledger_row_runs(rec, sel=True, left_w=40)
    assert sel_runs[0].text.startswith("\u25b6")
    for r in sel_runs:
        assert r.style is not None and r.style.bg is not None


def test_tools_record_in_main_and_subagent_trace():
    """主轨迹（消息源模式）与 subagent 轨迹首条均为 # 0 工具列表（用户
    确认范围：主轨迹 + 子代理轨迹都加）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl.clear_trace_archive()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        ctl._store._agents["agent-1"].messages = [
            {"role": "user", "content": "读取 user.py"},
        ]
        m = AppModel()
        m.message_source = lambda: _sample_messages()
        main_records, _ = build_trace_records(m)
        assert main_records[0].kind == "tools", "主轨迹首条应为 # 0 工具列表"
        assert main_records[0].index == 0
        assert main_records[0].summary == "工具列表"
        sub_records, _ = build_subagent_trace_records("agent-1", None)
        assert sub_records[0].kind == "tools", "subagent 轨迹首条应为 # 0 工具列表"
        assert sub_records[0].index == 0
        assert sub_records[0].summary == "工具列表"
    finally:
        ctl._store.clear()
        ctl.clear_trace_archive()


# ═══════════════════════════════════════════════════════════
# 14. 轨迹 Trace 工具调用：参数/返回值用树控件显示（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════
# 用户需求：轨迹 Trace 的工具调用修改——参数用树控件显示，然后分割线，
# 返回值用树控件显示。数据层（trace.py）：tool 记录保存原始参数
# （tool_args）与返回文本（tool_result）；渲染层（trace_view.py）：选中
# tool 记录时检查器内容 = 参数树 + 分割线 + 返回值树（JSON 树形展开，
# 非 JSON 文本每行一个叶子；无树数据回退纯文本 lines 零回归）。

def test_records_from_messages_tool_args_result_preserved():
    """消息源 tool 记录保存原始参数（arguments JSON 串）与返回文本
    （树显示数据源）。"""
    records, _ = _records_from_messages(_sample_messages())
    tool = next(r for r in records if r.kind == "tool")
    assert tool.tool_args == '{"command": "ls -la"}', "应保存原始 arguments"
    assert tool.tool_result == "总用量 4462\ndrwxrwxr-x", "应保存原始返回文本"


def test_build_records_tool_args_result_filled():
    """块路径 tool 记录填充树数据：tool_args = 工具卡标题 detail；
    tool_result = 输出行（剔除原始标题行/状态数据行）。"""
    records, _ = build_trace_records(_make_model_with_blocks())
    tool = next(r for r in records if r.kind == "tool")
    assert tool.tool_args == "ls -la"
    assert tool.tool_result == "file1.txt\nfile2.txt"


def test_inspector_tool_tree_args_divider_result():
    """tool 记录检查器：**参数树 → 分割线 → 返回值树**（用户需求核心）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash ls -la")
    rec.tool_args = '{"command": "ls -la", "cwd": "/tmp"}'
    rec.tool_result = "file1.txt\nfile2.txt"
    children = _inspector_children(rec, right_w=40, vh=20)
    texts = [str(c.props.get("children", "")) for c in children]
    joined = "\n".join(texts)
    # 参数小节 + 参数树（JSON 键值叶子）
    assert texts[1] == "\u25b8 参数", f"参数小节标题应紧随标题: {texts[:3]}"
    assert "command: ls -la" in joined
    assert "cwd: /tmp" in joined
    # 分割线（参数与返回值之间）
    sep_idx = next(i for i, t in enumerate(texts) if "\u2500" in t)
    assert any("\u2500" in t for t in texts), "应有分割线"
    # 返回值小节 + 树（非 JSON 文本 → 每行一个叶子）
    assert "\u25b8 返回值" in joined
    assert "file1.txt" in joined
    assert "file2.txt" in joined
    # 顺序：参数 → 分割线 → 返回值
    assert texts.index("\u25b8 参数") < sep_idx < texts.index("\u25b8 返回值")


def test_inspector_tool_tree_nested_json():
    """嵌套 JSON 参数/返回值 → 树形层级（▾ 展开指示符 + 缩进 + 下标）。"""
    rec = TraceRecord(index=1, kind="tool", summary="read_file")
    rec.tool_args = '{"path": "src/main.py", "meta": {"lines": [1, 2], "lang": "py"}}'
    rec.tool_result = '{"ok": true, "count": 2}'
    children = _inspector_children(rec, right_w=40, vh=30)
    texts = [str(c.props.get("children", "")) for c in children]
    joined = "\n".join(texts)
    # 参数树：标量叶子 + 嵌套 dict 节点 + list 下标节点
    assert "path: src/main.py" in joined
    assert "meta (2 项)" in joined
    assert "lines (2 项)" in joined
    assert "[0]: 1" in joined and "[1]: 2" in joined
    assert "lang: py" in joined
    # 返回值 JSON 树：布尔/数字标量（JSON 字面量语义）
    assert "ok: true" in joined
    assert "count: 2" in joined
    # ▾ 展开指示符（有 children 的节点）
    assert "\u25be" in joined


def test_inspector_tool_tree_non_json_result():
    """非 JSON 返回值 → 文本行叶子树形展示（每行一个叶子，对齐 Tree 控件）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash ls")
    rec.tool_args = "ls -la"  # 非 JSON 参数 → 单叶子
    rec.tool_result = "drwxrwxr-x 1 user\n-rw-r--r-- 1 user"
    children = _inspector_children(rec, right_w=40, vh=20)
    joined = "\n".join(str(c.props.get("children", "")) for c in children)
    assert "\u25b8 返回值" in joined
    assert "drwxrwxr-x 1 user" in joined
    assert "-rw-r--r-- 1 user" in joined
    assert "ls -la" in joined, "非 JSON 参数以单叶子显示"


def test_inspector_tool_tree_budget_truncated():
    """树内容超视口预算 → 「… 后 N 行省略」**后置**（head-first，对齐 tool
    语义——省略的是尾部内容）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash")
    rec.tool_args = '{"command": "x"}'
    rec.tool_result = "\n".join(f"输出行{i}" for i in range(50))
    children = _inspector_children(rec, right_w=40, vh=8)
    texts = [str(c.props.get("children", "")) for c in children]
    omitted = [t for t in texts if "省略" in t]
    assert omitted and "后" in omitted[0], f"应显示后 N 行省略: {texts}"
    assert texts[-1] == omitted[0], f"省略提示应在最后一行: {texts}"
    assert any("输出行0" in t for t in texts), "head-first 从头部显示"
    assert not any("输出行49" in t for t in texts), "尾部省略"


def test_inspector_tool_without_tree_falls_back_lines():
    """无树数据（tool_args/tool_result 均空——手动构造/异常路径）→ 回退
    纯文本 lines（零回归：既有 tool 检查器语义保持）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash ls")
    rec._detail_lines = ["调用行", "返回行"]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert "调用行" in texts
    assert "返回行" in texts
    assert not any("\u25b8 参数" in t for t in texts), "无树数据不应显示参数小节"


def test_inspector_tool_tree_placeholder():
    """参数/返回值单侧缺失 → 对应小节占位提示（(无参数)/(无返回)）。"""
    # 仅参数（无返回）
    rec = TraceRecord(index=1, kind="tool", summary="bash")
    rec.tool_args = '{"command": "pwd"}'
    rec.tool_result = ""
    children = _inspector_children(rec, right_w=40, vh=20)
    texts = [str(c.props.get("children", "")) for c in children]
    assert any("(无返回)" in t for t in texts)
    assert "command: pwd" in "\n".join(texts)
    # 仅返回（无参数）
    rec2 = TraceRecord(index=2, kind="tool", summary="bash")
    rec2.tool_args = None
    rec2.tool_result = "/home/user"
    children2 = _inspector_children(rec2, right_w=40, vh=20)
    texts2 = [str(c.props.get("children", "")) for c in children2]
    assert any("(无参数)" in t for t in texts2)
    assert "/home/user" in "\n".join(texts2)


def test_tool_tree_rows_cache_hit_and_invalidation():
    """树行缓存：同内容命中（同一列表引用）；result 变化重生成。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash")
    rec.tool_args = '{"a": 1}'
    rec.tool_result = "x"
    rows1 = _tool_tree_rows(rec, 40)
    rows2 = _tool_tree_rows(rec, 40)
    assert rows1 is rows2, "同内容应命中缓存（同一列表引用）"
    rec2 = TraceRecord(index=2, kind="tool", summary="bash")
    rec2.tool_args = '{"a": 1}'
    rec2.tool_result = "xy"
    rows3 = _tool_tree_rows(rec2, 40)
    assert rows3 is not rows1, "result 变化应重生成"


def test_trace_view_tool_record_shows_tree():
    """TraceView 集成：选中消息源 tool 记录 → 检查器显示
    参数树 + 分割线 + 返回值树。"""
    m = _make_model_with_blocks()
    m.message_source = lambda: _sample_messages()
    m.trace_open = True
    m.trace_selected = 5  # records[5] = tool 记录
    el, _ = _render(TraceView, {"model": m, "width": 100})
    row_el = list(el.children)[1]
    right_col = list(row_el.children)[2]
    texts = [str(c.props.get("children", "")) for c in right_col.children]
    joined = "\n".join(texts)
    assert any(t.startswith("#5 工具") for t in texts), f"标题应为 #5 工具: {texts[0]}"
    assert "\u25b8 参数" in joined
    assert "command: ls -la" in joined
    assert "\u2500" in joined, "应有分割线"
    assert "\u25b8 返回值" in joined
    assert "总用量 4462" in joined
    assert "drwxrwxr-x" in joined


# ═══════════════════════════════════════════════════════════
# 17. 性能优化回归（2026-08-19 用户需求：轨迹 Trace 优化性能）
# ═══════════════════════════════════════════════════════════
# 覆盖：
#   1. use_memo deps 展平为原子值（嵌套 tuple 按 is 恒 miss → 缓存永久失效
#      → 每帧全量重建 records + ListView 全重渲染）——_records_deps /
#      _messages_fingerprint / _live_fingerprint / _subagent_trace_deps；
#   2. 台账行 runs 缓存（同内容跨 rec 命中同一引用——TEXT wrap 引用级命中）；
#   3. 内容消毒/换行拆分缓存（_content_str / _split_lines——流式重建时历史
#      消息零重复消毒/拆分）；
#   4. 块级增量缓存（_block_plain_lines / _block_content_len / block_detail_lines
#      ——append-only 增量，长内容每帧 O(新增) 而非 O(全部)）；
#   5. subagent live 内容换行缓存（_slot_live_lines——无新 chunk 帧零拆分）。

def _assert_atomic_tuple(fp):
    """断言指纹为展平原子值元组（元素 int/float/str/bool/None——无嵌套
    tuple/list/dict；use_memo deps 逐项 _object_is 按值比较的前提）。"""
    assert isinstance(fp, tuple), f"应为 tuple: {type(fp)}"
    for v in fp:
        assert isinstance(v, (int, float, str, bool)) or v is None, \
            f"非原子值: {type(v)}: {v!r}"


def test_records_deps_flat_atomic():
    """_records_deps 返回展平原子值（块路径 + 消息源路径——use_memo 命中前提）。"""
    from src.tui.app.trace_view import _records_deps
    _assert_atomic_tuple(_records_deps(_make_model_with_blocks()))
    m = AppModel()
    m.message_source = lambda: _sample_messages()
    _assert_atomic_tuple(_records_deps(m))
    # 空消息源（无消息）也原子
    m2 = AppModel()
    m2.message_source = lambda: []
    _assert_atomic_tuple(_records_deps(m2))


def test_records_deps_stable_when_content_unchanged():
    """同内容 deps 逐项相等（_deps_equal True）——use_memo 缓存命中的前提；
    内容增长 → deps 变化（重建触发）。"""
    from src.tui.ink._hooks_core import _deps_equal
    from src.tui.app.trace_view import _records_deps
    m = _make_model_with_blocks()
    d1 = _records_deps(m)
    d2 = _records_deps(m)
    assert _deps_equal(list(d1), list(d2)) is True, "同内容 deps 应相等（命中）"
    # 开放 content 块增长（流式）→ deps 变化
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("新内容"))
    d3 = _records_deps(m)
    assert _deps_equal(list(d1), list(d3)) is False, "内容增长应触发重建"
    # 消息源模式：尾消息 content 增长 → deps 变化
    msgs = _sample_messages()
    m2 = AppModel()
    m2.message_source = lambda: msgs
    e1 = _records_deps(m2)
    msgs[-1]["content"] = msgs[-1]["content"] + "追加"
    e2 = _records_deps(m2)
    assert _deps_equal(list(e1), list(e2)) is False


def test_messages_fingerprint_flat_atomic():
    """_messages_fingerprint 展平原子值（含尾消息指纹展开）。"""
    m = AppModel()
    m.message_source = lambda: _sample_messages()
    _assert_atomic_tuple(_messages_fingerprint(m))
    m2 = AppModel()
    m2.message_source = lambda: []
    _assert_atomic_tuple(_messages_fingerprint(m2))


def test_live_fingerprint_flat_atomic():
    """_live_fingerprint 展平原子值（开放块/运行中工具逐字段展开）。"""
    m = AppModel()
    m.message_source = lambda: []
    _assert_atomic_tuple(_live_fingerprint(m))
    _open_content_block(m, "内容")
    _assert_atomic_tuple(_live_fingerprint(m))
    m.open_tool_box("t1", "bash", "pwd")
    _assert_atomic_tuple(_live_fingerprint(m))


def test_subagent_trace_deps_flat_atomic():
    """_subagent_trace_deps 展平原子值（消息指纹/动态元素/工具 phase 逐对展开）。"""
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析", status="running")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [{"role": "user", "content": "hi"}]
        from src.tui._subagent_state import _ToolRecord
        rec = _ToolRecord(tool_name="read_file", detail="a.py")
        rec.phase = "running"
        slot.tool_history.append(rec)
        _assert_atomic_tuple(_subagent_trace_deps("agent-1"))
        # 槽位缺失 → ("missing", label) 原子
        _assert_atomic_tuple(_subagent_trace_deps("ghost"))
    finally:
        ctl._store.clear()


def test_ledger_row_runs_cache_reuse():
    """台账行 runs 缓存：同内容跨 rec（records 流式重建新对象）→ 同一列表
    引用（TEXT wrap 引用级命中零重写）；内容/sel 变化 → 重建。"""
    rec1 = TraceRecord(index=1, kind="user", summary="你好", status="",
                       result="", time_seconds=None)
    runs1 = _ledger_row_runs(rec1, False, 40)
    rec2 = TraceRecord(index=1, kind="user", summary="你好", status="",
                       result="", time_seconds=None)
    runs2 = _ledger_row_runs(rec2, False, 40)
    assert runs1 is runs2, "同内容跨 rec 应命中缓存（同一引用）"
    # 内容变化 → 重建
    rec3 = TraceRecord(index=1, kind="user", summary="不同摘要", status="",
                       result="", time_seconds=None)
    runs3 = _ledger_row_runs(rec3, False, 40)
    assert runs3 is not runs1, "内容变化应重建"
    # 选中态变化 → 重建（背景高亮不同）
    runs4 = _ledger_row_runs(rec1, True, 40)
    assert runs4 is not runs1, "选中态变化应重建"
    assert runs4[0].text.startswith("\u25b6")
    # 栏宽变化 → 重建
    runs5 = _ledger_row_runs(rec1, False, 60)
    assert runs5 is not runs1, "栏宽变化应重建"


def test_ledger_row_runs_cache_time_second_granularity():
    """运行中耗时按整数秒入指纹——亚秒增长不重建（每秒刷新一次）。"""
    rec1 = TraceRecord(index=1, kind="tool", summary="bash", time_seconds=1.1)
    runs1 = _ledger_row_runs(rec1, False, 40)
    rec2 = TraceRecord(index=1, kind="tool", summary="bash", time_seconds=1.9)
    runs2 = _ledger_row_runs(rec2, False, 40)
    assert runs1 is runs2, "同整数秒耗时（1.1/1.9）应命中缓存"
    rec3 = TraceRecord(index=1, kind="tool", summary="bash", time_seconds=2.0)
    runs3 = _ledger_row_runs(rec3, False, 40)
    assert runs3 is not runs1, "跨整数秒应重建（耗时文本变化）"


def test_sep_row_runs_cache_reuse():
    """轮次分隔行 runs 缓存：同 n/left_w 命中同一引用；n 变化重建。"""
    from src.tui.app.trace_view import _sep_row_runs
    r1 = _sep_row_runs(1, 40)
    r2 = _sep_row_runs(1, 40)
    assert r1 is r2, "同 n/left_w 应命中缓存"
    r3 = _sep_row_runs(2, 40)
    assert r3 is not r1, "轮次变化应重建"


def test_content_str_cache_reuse():
    """_content_str 消毒结果缓存：str content 两次调用结果一致（缓存命中不
    破坏功能）；ANSI 消毒语义保持。"""
    from src.tui.app.trace import _content_str, _content_str_cache
    _content_str_cache.clear()
    s = "abc\x1b[31mdef"
    assert _content_str(s) == "abcdef", "ANSI 应被消毒"
    assert _content_str(s) == _content_str(s), "同 content 两次结果一致"
    assert len(_content_str_cache) == 1, "str content 应写入缓存"
    # list/None 不缓存（走原路径，不抛）
    assert _content_str(None) == ""
    assert _content_str(["a", "b"]) == "a b"


def test_split_lines_cached_shared():
    """_split_lines 缓存：同文本命中同一列表引用（rec.lines id 稳定 →
    检查器 use_memo 命中）；功能与 splitlines 一致。"""
    from src.tui.app.trace import _split_lines, _split_lines_cache
    _split_lines_cache.clear()
    text = "第一行\n第二行\n第三行"
    l1 = _split_lines(text)
    l2 = _split_lines(text)
    assert l1 == ["第一行", "第二行", "第三行"]
    assert l1 is l2, "同文本应命中缓存（共享引用——id 稳定供 use_memo 命中）"
    # 空文本（splitlines 语义：返回空列表）
    assert _split_lines("") == []


def test_block_plain_lines_incremental():
    """_block_plain_lines 块级增量缓存：同引用命中；append-only 增量（同一
    列表对象）；行数倒退全量重建。"""
    from src.tui.app.trace import _block_plain_lines
    m = AppModel()
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("行一"))
    r1 = _block_plain_lines(b)
    r2 = _block_plain_lines(b)
    assert r1 is r2, "同引用应命中缓存"
    # 流式增长：append-only → 增量转换（同一列表对象复用）
    b.lines.append(AnsiLine.of("行二"))
    r3 = _block_plain_lines(b)
    assert r3 is r1, "增量路径应复用同一列表对象"
    assert r3 == ["行一", "行二"]
    # 行数倒退（非 append-only 异常）→ 全量重建
    b.lines.pop()
    r4 = _block_plain_lines(b)
    assert r4 is not r1, "行数倒退应全量重建"
    assert r4 == ["行一"]


def test_block_content_len_incremental():
    """_block_content_len 增量长度缓存：append-only 累加；行数倒退全量重算。"""
    from src.tui.app.trace import _block_content_len
    m = AppModel()
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("abc"))
    assert _block_content_len(b, b.lines) == 3
    b.lines.append(AnsiLine.of("de"))
    assert _block_content_len(b, b.lines) == 5, "增量 3+2"
    b.lines.pop()
    assert _block_content_len(b, b.lines) == 3, "行数倒退全量重算"


def test_block_detail_lines_tool_cache():
    """block_detail_lines 工具块缓存：同状态命中同一引用；输出增长重建含新行；
    状态行下标变化重建。"""
    m = _make_model_with_blocks()
    records, _ = build_trace_records(m)
    tool = next(r for r in records if r.kind == "tool")
    block = tool.source_block
    d1 = block_detail_lines(block)
    d2 = block_detail_lines(block)
    assert d1 is d2, "同状态应命中缓存（同一引用）"
    # 输出增长（流式）→ 重建含新行
    block.lines.append(AnsiLine.of("file3.txt"))
    d3 = block_detail_lines(block)
    assert d3 is not d1, "行数增长应重建"
    assert d3[0] == "bash ls -la", "调用行保持"
    assert "file3.txt" in d3
    # 状态行下标变化（extra._status_line_index）→ 重建（剔除新状态行）
    block.extra["_status_line_index"] = len(block.lines) - 1
    d4 = block_detail_lines(block)
    assert d4 is not d3, "状态行下标变化应重建"
    assert "file3.txt" not in d4, "新状态行应被剔除"


def test_slot_live_lines_cached():
    """_slot_live_lines 槽位缓存：同内容命中同一引用；内容增长重新拆分；
    strip 语义与调用方一致（行为零变化）。"""
    from src.tui._subagent_state import _AgentSlot
    from src.tui.app.trace import _slot_live_lines
    slot = _AgentSlot("agent-1", "解析")
    slot.live_reasoning = " 第一行\n第二行 "
    l1 = _slot_live_lines(slot, "live_reasoning")
    l2 = _slot_live_lines(slot, "live_reasoning")
    assert l1 is l2, "同内容应命中缓存"
    assert l1 == ["第一行", "第二行"], "strip 后拆分（与调用方原语义一致）"
    # 内容增长（新 chunk）→ 重新拆分
    slot.live_reasoning = " 第一行\n第二行\n第三行 "
    l3 = _slot_live_lines(slot, "live_reasoning")
    assert l3 is not l1, "内容变化应重新拆分"
    assert l3 == ["第一行", "第二行", "第三行"]
    # 空内容 → []
    slot.live_reasoning = ""
    assert _slot_live_lines(slot, "live_reasoning") == []


def test_trace_view_use_memo_hits_second_render():
    """端到端：同一 fiber 连续两次渲染（同模型状态）→ use_memo 命中——
    第二次 ListView items（rows）引用与第一次相同（deps 展平原子值后缓存
    生效；修复前嵌套 tuple 按 is 恒 miss → 每帧全量重建 records/rows）。"""
    from src.tui.app.trace_view import TraceView as TV
    from src.tui.ink.widgets.listview import ListView
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 2  # 固定选中（非尾部跟随——避免重建依赖变化）
    # 同一 fiber 连续渲染两次（use_memo 的 last_deps 挂载在 fiber.hooks——
    # 跨渲染持久；新建 fiber 会重置 hook 状态，无法验证命中）。每次渲染前
    # reset_hooks()（对齐 reconciler：渲染函数组件前清零 hook_index 复用节点）。
    fiber = Fiber(TAG_FUNCTION, TV, {"model": m, "width": 100})

    def _render_once():
        fiber.reset_hooks()
        hooks._push_current(fiber)
        try:
            return TV({"model": m, "width": 100})
        finally:
            hooks._pop_current()

    def _listview_items(el):
        row_el = list(el.children)[1]
        left = list(row_el.children)[0]
        assert left.type is ListView
        return left.props["items"]

    el1 = _render_once()
    el2 = _render_once()
    items1 = _listview_items(el1)
    items2 = _listview_items(el2)
    assert items1 is items2, "同状态二次渲染应命中 use_memo（rows 引用稳定）"
    # 内容变化（新增块）→ deps 变化 → 重建（新 rows 引用）
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("追加块"))
    el3 = _render_once()
    items3 = _listview_items(el3)
    assert items3 is not items1, "内容变化应触发重建（新 rows 引用）"


# ═══════════════════════════════════════════════════════════
# 18. 选最后一行时新行自动选择最新行（2026-08-17 用户需求）
# ═══════════════════════════════════════════════════════════
# 用户需求：如果当前选择是最后一行，这时候多了一行，自动选择最新行。
# 实现：① 导航（↑↓/PgUp/PgDn/Home/End/g/G）到**最后一条记录** → 写 -1
# （尾部跟随语义——渲染期解析为最新记录，新记录出现自动跟进）；② 渲染期
# 兜底：上次选中最后一条（具体索引 == 上次 total-1）且本次记录增长 →
# 自动转为尾部跟随（覆盖非导航路径/历史遗留具体索引 == 末行）。

def test_navigate_to_last_row_sets_tail_follow():
    """End/↓ 导航到末行 → trace_selected 写 -1（尾部跟随——新行自动选择
    最新行）；离开末行 → 写具体索引（退出跟随）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 0
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    # 记录：tools(0) system(1) user(2) reasoning(3) content(4) tool(5)
    # End → 末行（tool #5）→ 尾部跟随（-1）
    assert router(KeyEvent(kind="end", raw=b"\x1b[F")) is True
    assert m.trace_selected == -1, "End 到末行应写 -1（尾部跟随）"
    # 下一帧渲染后 ↓ 在末行无操作（不消费）——仍在尾部跟随
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down", raw=b"\x1b[B")) is False
    assert m.trace_selected == -1
    # ↑ 离开末行（#4 回答）→ 写具体索引（退出跟随）
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 4, "离开末行应写具体索引"
    # G → 末行（尾部跟随）；g → 首条（具体索引）
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="G", raw=b"G")) is True
    assert m.trace_selected == -1, "G 到末行应写 -1"
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="g", raw=b"g")) is True
    assert m.trace_selected == 0, "g 到首条应写具体索引"


def test_selected_last_row_follows_new_record():
    """选中最后一条（具体索引 == 末行）时追加记录 → 自动选择最新行：
    trace_selected 转 -1（尾部跟随），ListView 光标指向新末行。"""
    from src.tui.app.trace_view import TraceView as TV
    from src.tui.ink.widgets.listview import ListView
    m = _make_model_with_blocks()  # 6 条记录（tools/system/user/reasoning/content/tool）
    m.trace_open = True
    m.trace_selected = 5  # 最后一条（tool #5）——模拟非导航路径的具体索引
    fiber = Fiber(TAG_FUNCTION, TV, {"model": m, "width": 100})

    def _render_once():
        fiber.reset_hooks()
        hooks._push_current(fiber)
        try:
            return TV({"model": m, "width": 100})
        finally:
            hooks._pop_current()

    def _listview_cursor(el):
        row_el = list(el.children)[1]
        left = list(row_el.children)[0]
        assert left.type is ListView
        return left.props["cursor"], len(left.props["items"])

    # 首次渲染：选中末行（cursor 指向 tool #5 在 rows 中的下标）
    el1 = _render_once()
    cur1, n1 = _listview_cursor(el1)
    assert m.trace_selected == 5  # 未追加 → 保持具体索引（无增长不转跟随）
    assert cur1 == n1 - 1, "首次渲染应选中末行"
    # 追加新记录（content 块——未关闭，running 动态记录）→ total 增长
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("新追加内容"))
    el2 = _render_once()
    cur2, n2 = _listview_cursor(el2)
    assert n2 == n1 + 1, "记录应增长一条"
    assert m.trace_selected == -1, "选中末行 + 增长 → 自动转尾部跟随（-1）"
    assert cur2 == n2 - 1, "光标应指向最新记录（新追加行）"
    # 新末行是追加的 content 记录（渲染行选中标记 ▶ + 摘要）
    items = list(el2.children)[1].children[0].props["items"]
    last_el = list(el2.children)[1].children[0].props["renderItem"](items[-1], len(items) - 1, True)
    last_text = "".join(r.text for r in last_el.props.get("styled", []))
    assert "新追加内容" in last_text, "最新选中行应为新追加记录"


def test_selected_last_row_stays_until_growth():
    """选中最后一条且无新记录 → 保持具体索引（不误转尾部跟随）；离开末行
    后增长 → 不跟随（停留在选中记录）。"""
    from src.tui.app.trace_view import TraceView as TV
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 5
    fiber = Fiber(TAG_FUNCTION, TV, {"model": m, "width": 100})

    def _render_once():
        fiber.reset_hooks()
        hooks._push_current(fiber)
        try:
            return TV({"model": m, "width": 100})
        finally:
            hooks._pop_current()

    _render_once()
    # 无增长：再渲染 → 保持具体索引（不误转）
    _render_once()
    assert m.trace_selected == 5, "无增长应保持具体索引"
    # 离开末行（选中倒数第二 #4）→ 增长 → 不跟随（停留 #4）
    m.trace_selected = 4
    _render_once()
    b = m.append_block("content")
    b.lines.append(AnsiLine.of("新增"))
    _render_once()
    assert m.trace_selected == 4, "未选中末行时增长不应跟随（停留选中记录）"
