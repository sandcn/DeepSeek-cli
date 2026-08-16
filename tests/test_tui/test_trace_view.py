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
    _inspector_children,
    _ledger_row_runs,
    _subagent_trace_deps,
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
    """TraceView：头部 + 左右布局（左台账 / 分隔 / 右检查器）。

    ★ 全面控件化（方案B）：台账左栏经标准控件 ``ListView`` 表达——检查
    控件 props（items/cursor/renderItem）与 renderItem 行渲染。
    """
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
    left, sep, right_col = parts
    # 左栏 = ListView 标准控件（items = rows 含分隔行；cursor = 选中记录行号）
    from src.tui.ink.widgets.listview import ListView
    assert left.type is ListView, f"左栏应为 ListView 控件: {left.type}"
    lv = left.props
    items = lv["items"]
    assert len(items) == 6  # system + 轮次分隔 + user/reasoning/content/tool
    assert lv["cursor"] == 4  # records[3]（content 回答）在 rows 中下标 4
    # renderItem：系统提词行（⚙ + #1）
    sys_el = lv["renderItem"](items[0], 0, False)
    sys_text = "".join(r.text for r in sys_el.props.get("styled", []))
    assert "\u2699" in sys_text
    assert sys_text.startswith("  # 1")
    # renderItem：轮次分隔行
    sep_el = lv["renderItem"](items[1], 1, False)
    assert "轮次 1" in "".join(r.text for r in sep_el.props.get("styled", []))
    # renderItem：选中行（content #4，isSelected=True）带 ▶
    sel_el = lv["renderItem"](items[4], 4, True)
    sel_text = "".join(r.text for r in sel_el.props.get("styled", []))
    assert sel_text.startswith("\u25b6")
    assert "# 4" in sel_text
    # renderItem：工具行（#5）：调用 + 返回预览合并一条（· file1.txt）
    tool_el = lv["renderItem"](items[5], 5, False)
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
    # 尾部跟随：ListView cursor = 最新记录（tool #5）在 rows 中下标 5
    assert left.props["cursor"] == 5, f"跟随尾部应定位 tool 记录: {left.props['cursor']}"
    # renderItem 验证该行为 ▶ 选中
    items = left.props["items"]
    tool_el = left.props["renderItem"](items[5], 5, True)
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
    # 记录：system(0) user(1) reasoning(2) content(3) tool(4)
    # 上移：从尾部（#5 工具）到 #4 回答
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 3
    # 下一帧重建（渲染循环）→ 上移继续
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_selected == 2
    # 下移
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down", raw=b"\x1b[B")) is True
    assert m.trace_selected == 3
    # End → 尾部
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="end", raw=b"\x1b[F")) is True
    assert m.trace_selected == 4
    # g → 首条（系统提词）
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
    assert [r.kind for r in records] == ["user", "content"]
    live = records[-1]
    assert live.kind == "content"
    assert live.status == "running"
    assert live.summary == "正在生成的第一行"
    assert live.lines == ["正在生成的第一行"]
    assert live.index == 2
    assert rows[-1] is live


def test_live_records_message_source_streaming_reasoning():
    """消息源模式：流式生成中的 reasoning 块 → running reasoning 记录。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    m.reasoning_block_index = len(m.blocks)
    block = m.append_block("reasoning")
    block.lines.append(AnsiLine.of("先思考一下"))
    records, _ = build_trace_records(m)
    assert [r.kind for r in records] == ["user", "reasoning"]
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
    assert [r.kind for r in records] == ["user", "tool"]
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
    assert [r.kind for r in records2] == ["user", "content"]
    done = records2[-1]
    assert done.kind == "content" and done.status != "running"
    assert done.summary == "生成完成"
    assert len(records2) == 2  # 无重复：仅消息记录，实时记录已消失


def test_live_records_skip_closed_blocks():
    """实时记录仅覆盖未关闭块——已关闭块（历史/已完成）不重复追加。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    b = _open_content_block(m, "生成中")
    b.closed = True  # 已关闭（如异常路径未关闭时不被计为实时）
    records, _ = build_trace_records(m)
    assert [r.kind for r in records] == ["user"]
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
    # rows: [轮次分隔, user 记录, content 实时记录]——尾部跟随选中实时记录
    assert len(items) == 3
    live_el = left.props["renderItem"](items[2], 2, True)
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
        assert [r.kind for r in records] == ["system", "user", "reasoning", "tool", "content"]
        # 工具调用 + 返回合并一条（与 mainagent 语义一致）
        assert records[3].summary == "read_file user.py"
        assert records[3].result == "class User: ..."
        # 与 mainagent 的 _records_from_messages 完全同构
        main_records, _ = _records_from_messages(slot.messages)
        assert [r.kind for r in main_records] == [r.kind for r in records]
        assert main_records[3].summary == records[3].summary
    finally:
        ctl._store.clear()


def test_build_subagent_trace_records_fallback_slot():
    """无 messages（未注册）→ 回退槽位活动记录（提词 + 工具历史 + 结果）。"""
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
        assert [r.kind for r in records] == ["user", "tool", "content"]
        assert records[0].summary == "读取 user.py"
        assert records[1].summary == "read_file user.py"
        assert records[1].status == "done"
        assert records[2].summary == "解析完成"
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
        assert [r.kind for r in sub_records] == ["user", "content"]
        assert sub_records[0].summary == "读取 user.py"
        assert sub_records[1].summary == "解析完成。"
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
# 用户需求：load 命令（/load、--load 启动、webui 加载）也要支持「已完成
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
        assert [r.kind for r in sub_records] == ["system", "user", "content"]
        assert sub_records[1].summary == "读取 user.py"
        assert sub_records[2].summary == "解析完成。"
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
    """SessionPersistenceManager.load（--load / webui 路径）→ 恢复轨迹存档。"""
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
        assert [r.kind for r in sub_records] == ["system", "user", "content"]
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
        assert [r.kind for r in sub_records] == ["system", "user", "content"]
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
        assert build_subagent_trace_records("agent-1", None)[0][0].summary == "任务二提词"
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
        # user + content（消息记录）→ tool running（动态）→ reasoning（动态）
        assert kinds == ["user", "content", "tool", "reasoning"]
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
        assert [r.kind for r in records2] == ["user", "content"]
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
    """工具（tool）：保持从头部显示 + 「… 后 N 行省略」（零回归）。"""
    rec = TraceRecord(index=1, kind="tool", summary="bash ls")
    rec._detail_lines = [f"输出行{i}" for i in range(80)]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert any("省略" in t and "后" in t for t in texts)
    assert "输出行0" in texts, "工具从头部显示"
    assert "输出行79" not in texts


def test_inspector_short_content_no_omitted_hint():
    """短内容（思考/回答）不截断 → 无省略提示（零回归）。"""
    rec = TraceRecord(index=1, kind="content", summary="短回答")
    rec._detail_lines = ["短内容"]
    children = _inspector_children(rec, right_w=40, vh=10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert not any("省略" in t for t in texts)
    assert "短内容" in texts
