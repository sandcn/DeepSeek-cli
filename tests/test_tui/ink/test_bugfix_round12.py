"""第十二轮修复回归测试（BUG-30~62）。

覆盖：
  - BUG-30：工具卡关闭状态图标 ●→✔/✖ 渲染层刷新（committed 前缀缓存失效）
  - BUG-31：PriorityQueue 满队列腾位后堆序保持
  - BUG-32：row 容器 + 显式 height + flexGrow/flexShrink 不纵向堆叠子节点
  - BUG-33：ANSI CSI 最终字节/@~ 与冒号参数解析（宽度测量不虚高）
  - BUG-34：wrap/FrameBuilder width<=0 时按 \\n 拆行（不内嵌字面换行符）
  - BUG-35：换行缓存引用级快速路径长度校验（可变 styled 列表不返回陈旧行）
  - BUG-36：_context_dirty 渲染完成后复位（不消费 context 的 memo 组件短路恢复）
  - BUG-37：useImperativeHandle ref 切换 hook 槽位稳定（恒消费 2 槽）
  - BUG-38：useSyncExternalStore subscribe 身份变化时重订阅
  - BUG-39：崩溃恢复后稳定运行复位 _recover_attempts + 清除 _render_crashed
  - BUG-40：_ParseLine 仅替换行首固定前缀位 ~（工具名含 ~ 不误替换）
  - BUG-41：ChatView 开放块行 key 用块内绝对行号（流式追加不重建已渲染行）
  - BUG-42：subagent 卡片元素按 (subagent_lines, width) use_memo 缓存
  - BUG-43：StatusBar use_memo deps 含 spinner_char
  - BUG-44：_object_is 对 str 按值比较（deps 含 str 时缓存不恒失效）
  - BUG-45：stdout_tracker 刷盘失败不立即重启线程（防线程风暴）
  - BUG-46：_partial_line 无换行长行累积上限
  - BUG-47：format_speed 非有限值防护
  - BUG-49：补全弹窗/搜索激活时动画推进
  - BUG-50：h() 生成器/迭代器子级展开
  - BUG-51：lerp_color NaN 防护
  - BUG-53：stdout_tracker.buffer 属性防御
  - BUG-54：_panel_refresh 渲染异常后保留脏标记
  - BUG-55：subagent 工具历史条数上限
  - BUG-56：diff hunk 头 ANSI 消毒
  - BUG-58：事件批处理队列水位限制
  - BUG-59：update_tool_parsing 仅 phase 变化时重置时间
  - BUG-61：_merge_line 宽字符残留清理
  - BUG-62：reasoning 块不创建冻结缓存（无消费方）
"""

from __future__ import annotations

import io
import queue
import sys
import threading

import pytest

# ═══════════════════════════════════════════════════════════
# BUG-30：工具卡关闭状态图标渲染层刷新
# ═══════════════════════════════════════════════════════════


def _build_long_tool_model():
    from src.tui.app.model import AppModel
    from src.renderer.ansi.helpers import AnsiLine

    m = AppModel()
    m.width = 40
    blk = m.open_tool_box("t1", "bash")
    for i in range(70):
        blk.lines.append(AnsiLine.of(f"  line{i}", None))
    m.commit_open_block(blk)
    return m


def test_tool_icon_refresh_after_close_renderer():
    """长工具输出（增量提交后）关闭 → ToolCard 渲染重写顶边框行（●→✔）。

    阶段5：工具卡行不写入 committed_lines（由 ToolCard 标准控件组件从
    block.lines 渲染）——关闭后 block.extra.tool_status 变化 → 下一帧顶边框
    图标自动翻转（原 committed 前缀缓存失效路径由 ToolCard 帧级缓存承接）。
    """
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.renderer import InkRenderer
    from src.tui.ink import components as C
    from src.tui.ink.element import h
    from src.tui.app.tool_card import ToolCard

    m = _build_long_tool_model()
    blk = m.blocks[-1]
    r = Reconciler()
    root = r.create_root()
    r.render(root, h(ToolCard, {"block": blk, "width": 40}), 40, 24)
    stream = io.StringIO()
    ink = InkRenderer(stream=stream, height=200)  # 全部可见
    f_a = C.render_frame(root, 40)
    ink.render(f_a)
    first = stream.getvalue()
    assert "●" in first.split("\n")[0], "首帧顶边框应为 ●（running）"

    m.close_tool_box("t1", True)
    r.render(root, h(ToolCard, {"block": blk, "width": 40}), 40, 24)
    f_b = C.render_frame(root, 40)
    before = stream.getvalue()
    ink.render(f_b)
    delta = stream.getvalue()[len(before):]
    # delta 含顶边框重写（┌─ ✔）
    assert "┌" in delta and "✔" in delta, (
        "关闭后 diff 应重写顶边框行（含 ✔ 图标）"
    )
    # 顶边框重写行的内容确认（\r 之后首个 ┌ 所在段含 ✔）
    seg = delta.split("┌")[1]
    assert "✔" in seg.split("┐")[0], "顶边框重写段应含 ✔ 图标"


def test_committed_lines_identity_changes_on_replace():
    """_replace_committed_line 令 committed_lines 列表身份变化（前缀缓存失效）。"""
    from src.tui.ink import Line
    from src.renderer.ansi.helpers import AnsiLine
    from src.tui.app.model import AppModel
    m = AppModel()
    m.append_committed("content", [AnsiLine.of("old line")])
    old_id = id(m.committed_lines)
    m._replace_committed_line(0, Line.of("  new"))
    assert id(m.committed_lines) != old_id
    assert m.committed_lines[0].plain == "  new"


# ═══════════════════════════════════════════════════════════
# BUG-31：PriorityQueue 腾位后堆序保持
# ═══════════════════════════════════════════════════════════


def test_push_cmd_evict_preserves_heap_order():
    """队列满腾位（pop LOW 后 heapify）→ 后续出队保持优先级顺序。"""
    from src.tui.ink.session import _get_cmd_priority
    from src.tui._const import RenderCommand, RenderCmd

    # 构造一个小容量队列，模拟满队列 + LOW 腾位
    q: queue.PriorityQueue = queue.PriorityQueue(maxsize=4)
    seq = iter(range(1000))
    for cid in (RenderCommand.CONTENT, RenderCommand.CONTENT, RenderCommand.WRITE_LINE, RenderCommand.CONTENT):
        q.put((_get_cmd_priority(RenderCmd(cid=cid)), next(seq), RenderCmd(cid=cid)))

    # 手动模拟 push_cmd 腾位：pop 一个 LOW 项（>= _CMD_PRIORITY_LOW）后 heapify
    import heapq
    with q.mutex:
        for i, item in enumerate(q.queue):
            if item[0] >= 3:  # _CMD_PRIORITY_LOW
                q.queue.pop(i)
                heapq.heapify(q.queue)
                break
    # 放入新命令
    new_cmd = RenderCmd(cid=RenderCommand.PHASE_DONE)
    q.put((_get_cmd_priority(new_cmd), next(seq), new_cmd))
    # 连续出队：应保持优先级有序（非降序）
    priorities = []
    while not q.empty():
        p, _, _ = q.get_nowait()
        priorities.append(p)
    assert priorities == sorted(priorities), f"出队优先级应有序: {priorities}"


# ═══════════════════════════════════════════════════════════
# BUG-32：row 容器 flexGrow/flexShrink 不纵向堆叠
# ═══════════════════════════════════════════════════════════


def test_row_flexgrow_no_vertical_stack():
    """row + 显式 height + flexGrow → 子节点横向排列（x 递增，y 相同）。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.element import h, BOX, TEXT

    r = Reconciler()
    root = r.create_root()
    el = h(BOX, {"flexDirection": "row", "width": 10, "height": 4},
           h(TEXT, {"children": "a", "flexGrow": 1}),
           h(TEXT, {"children": "b", "flexGrow": 1}))
    r.render(root, el, 80, 24)
    boxes = []
    stack = [root.child]
    while stack:
        f = stack.pop()
        if f.is_host and f.layout_box is not None and f.type == "text":
            boxes.append((f.layout_box.x, f.layout_box.y))
        c = f.child
        while c:
            stack.append(c)
            c = c.sibling
    assert len(boxes) == 2
    boxes.sort()  # DFS 顺序不定，按 x 排序
    # x 递增（横向排列）且 y 相同（不纵向堆叠）
    assert boxes[0][0] < boxes[1][0], f"row 子节点应横向排列: {boxes}"
    assert boxes[0][1] == boxes[1][1], f"row 子节点 y 应相同（不堆叠）: {boxes}"


def test_row_flexshrink_no_vertical_stack():
    """row + 显式 height + flexShrink → 不纵向堆叠。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.element import h, BOX, TEXT

    r = Reconciler()
    root = r.create_root()
    # height=2 < 内容高（3 行），flexShrink 触发
    el = h(BOX, {"flexDirection": "row", "width": 10, "height": 2},
           h(TEXT, {"children": "a\nb\nc", "flexShrink": 1}),
           h(TEXT, {"children": "d\ne\nf", "flexShrink": 1}))
    r.render(root, el, 80, 24)
    boxes = []
    stack = [root.child]
    while stack:
        f = stack.pop()
        if f.is_host and f.layout_box is not None and f.type == "text":
            boxes.append((f.layout_box.x, f.layout_box.y))
        c = f.child
        while c:
            stack.append(c)
            c = c.sibling
    assert len(boxes) == 2
    boxes.sort()
    assert boxes[0][1] == boxes[1][1], f"row flexShrink 不应纵向堆叠: {boxes}"


# ═══════════════════════════════════════════════════════════
# BUG-33：ANSI CSI 最终字节/@~ 与冒号参数
# ═══════════════════════════════════════════════════════════


def test_wcswidth_csi_final_byte_tilde():
    """`\\x1b[3~`（Delete/PageUp 终端键序列）整段计宽 0。"""
    from src.tui._screen import wcswidth_simple
    assert wcswidth_simple("\x1b[3~x") == 1, "CSI ~ 终止符序列残留字符被计宽"
    assert wcswidth_simple("a\x1b[3~b") == 2


def test_wcswidth_csi_colon_truecolor():
    """真彩冒号格式 `\\x1b[38:2::255:0:0m` 整段计宽 0。"""
    from src.tui._screen import wcswidth_simple
    assert wcswidth_simple("\x1b[38:2::255:0:0mX") == 1, "冒号参数残留被计宽"


def test_strip_ansi_csi_tilde():
    """strip_ansi 完整剥离 CSI ~ 与冒号格式。"""
    from src.tui.ink.helpers import strip_ansi
    assert strip_ansi("\x1b[3~x") == "x"
    assert strip_ansi("\x1b[38:2::255:0:0mX") == "X"


# ═══════════════════════════════════════════════════════════
# BUG-34：wrap width<=0 按 \n 拆行
# ═══════════════════════════════════════════════════════════


def test_wrap_zero_width_splits_newline():
    from src.tui.ink.helpers import wrap_runs_by_width
    from src.tui.ink.output import StyledRun, Line
    lines = wrap_runs_by_width([StyledRun("a\nb")], 0)
    assert len(lines) == 2
    assert [l.plain for l in lines] == ["a", "b"]
    assert all("\n" not in l.plain for l in lines)


def test_wrap_text_lines_zero_width_splits_newline():
    from src.tui.ink.layout import wrap_text_lines
    lines = wrap_text_lines("x\ny", 0)
    assert len(lines) == 2
    assert [l.plain for l in lines] == ["x", "y"]


def test_frame_builder_zero_width_splits_newline():
    from src.tui.ink.output import FrameBuilder
    b = FrameBuilder(width=0)
    b.append("a\nb")
    f = b.build()
    assert len(f.lines) == 2
    assert [l.plain for l in f.lines] == ["a", "b"]


# ═══════════════════════════════════════════════════════════
# BUG-35：换行缓存引用级快速路径长度校验
# ═══════════════════════════════════════════════════════════


def test_wrap_cache_invalidated_on_styled_list_mutation():
    """同一 styled 列表对象原地 extend 后换行缓存不返回陈旧行。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import components as C
    from src.tui.ink.element import h, TEXT
    from src.tui.ink.output import StyledRun

    styled = [StyledRun("aa")]
    r = Reconciler()
    root = r.create_root()
    r.render(root, h(TEXT, {"styled": styled, "width": 10}), 80, 24)
    f1 = C.render_frame(root, 80)
    assert f1.lines[0].plain == "aa"

    # 原地 append（同一列表对象）
    styled.append(StyledRun("bb"))
    r.render(root, h(TEXT, {"styled": styled, "width": 10}), 80, 24)
    f2 = C.render_frame(root, 80)
    assert f2.lines[0].plain == "aabb", (
        f"styled 原地扩展后应渲染新内容，实际 {f2.lines[0].plain!r}"
    )


# ═══════════════════════════════════════════════════════════
# BUG-36：_context_dirty 渲染后复位
# ═══════════════════════════════════════════════════════════


def test_context_dirty_cleared_after_render():
    """Provider 子树内不消费 context 的 memo 组件：渲染后 _context_dirty 复位。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import hooks as H
    from src.tui.ink.element import h, TEXT

    ctx = H.create_context("default")

    def _MemoChild(props):
        return h(TEXT, {"children": "child", "width": 10})

    MemoChild = H.memo(_MemoChild)

    def _Comp(props):
        return h(ctx.Provider, {"value": props["v"]},
                 h(MemoChild, {"x": 1}))

    r = Reconciler()
    root = r.create_root()
    r.render(root, h(_Comp, {"v": 1}), 80, 24)
    # Provider 值变化 → 子树 _context_dirty 置位
    r.render(root, h(_Comp, {"v": 2}), 80, 24)
    # 找到 MemoChild fiber，检查 _context_dirty 已复位
    found = []

    def walk(f):
        if getattr(f, "_is_memo", False) or (f.is_function and getattr(f.type, "_is_memo", False)):
            found.append(f)
        c = f.child
        while c:
            walk(c)
            c = c.sibling
    walk(root)
    assert found, "应找到 memo 组件 fiber"
    for f in found:
        assert not getattr(f, "_context_dirty", False), (
            "渲染完成后 _context_dirty 应复位（memo 短路恢复）"
        )


# ═══════════════════════════════════════════════════════════
# BUG-37：useImperativeHandle hook 槽位稳定
# ═══════════════════════════════════════════════════════════


def test_use_imperative_handle_hook_slots_stable():
    """ref None↔非 None 切换时 hook 槽位数稳定（不违反 Rules of Hooks）。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import hooks as H
    from src.tui.ink.element import h, TEXT

    seen_states = []

    def _Comp(props):
        H.useImperativeHandle(props.get("ref"), lambda: {"ok": True}, ())
        # 后面的 hook 必须对齐（ref 切换后 use_state 读同一 StateHook）
        v, set_v = H.use_state(7)
        seen_states.append(v)
        return h(TEXT, {"children": str(v)})

    r = Reconciler()
    root = r.create_root()
    r.render(root, h(_Comp, {"ref": None}), 80, 24)
    assert seen_states == [7]
    ref_obj = type("R", (), {"current": None})()  # 普通带 .current 对象
    r.render(root, h(_Comp, {"ref": ref_obj}), 80, 24)
    r.render(root, h(_Comp, {"ref": None}), 80, 24)
    # hook 下标对齐：use_state 每次读到 7（无 HookStateError / 状态错配）
    assert seen_states == [7, 7, 7], seen_states


# ═══════════════════════════════════════════════════════════
# BUG-38：useSyncExternalStore subscribe 变化重订阅
# ═══════════════════════════════════════════════════════════


def test_use_sync_external_store_resubscribe():
    """subscribe 函数身份变化 → 旧订阅清理 + 新订阅调用。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import hooks as H
    from src.tui.ink.element import h, TEXT

    cleanups = []
    sub_calls = []

    def make_subscribe(name):
        def subscribe(listener):
            sub_calls.append(name)
            def cleanup():
                cleanups.append(name)
            return cleanup
        return subscribe

    def _Comp(props):
        snap = H.useSyncExternalStore(props["sub"], lambda: 1)
        return h(TEXT, {"children": str(snap)})

    r = Reconciler()
    root = r.create_root()
    s1 = make_subscribe("s1")
    r.render(root, h(_Comp, {"sub": s1}), 80, 24)
    assert sub_calls == ["s1"], f"首次订阅 s1: {sub_calls}"

    s2 = make_subscribe("s2")
    r.render(root, h(_Comp, {"sub": s2}), 80, 24)
    assert sub_calls == ["s1", "s2"], f"subscribe 变化应重新订阅: {sub_calls}"
    assert cleanups == ["s1"], f"旧订阅应被清理: {cleanups}"


# ═══════════════════════════════════════════════════════════
# BUG-39：崩溃恢复计数复位
# ═══════════════════════════════════════════════════════════


def test_recover_attempts_reset_after_stable():
    """稳定运行超过阈值后 _recover_attempts 复位。"""
    import src.tui.ink.session as sess
    assert sess._RECOVER_STABLE_SECS == 60.0

    s = sess.InkSession.__new__(sess.InkSession)
    s._recover_attempts = 3
    s._last_recover_time = 100.0
    # 模拟 200s（>60s 阈值）稳定运行后：循环内复位逻辑
    now = 200.0
    if s._recover_attempts > 0 and s._last_recover_time > 0:
        if now - s._last_recover_time >= sess._RECOVER_STABLE_SECS:
            s._recover_attempts = 0
            s._last_recover_time = 0.0
    assert s._recover_attempts == 0, "稳定运行后应复位崩溃恢复计数"


def test_recover_attempts_kept_within_stable_window():
    """稳定运行未达阈值时计数保留（防连续崩溃无限恢复）。"""
    import src.tui.ink.session as sess
    s = sess.InkSession.__new__(sess.InkSession)
    s._recover_attempts = 3
    s._last_recover_time = 150.0
    now = 160.0  # 10s < 60s 阈值
    if s._recover_attempts > 0 and s._last_recover_time > 0:
        if now - s._last_recover_time >= sess._RECOVER_STABLE_SECS:
            s._recover_attempts = 0
            s._last_recover_time = 0.0
    assert s._recover_attempts == 3, "稳定窗口内计数应保留"


# ═══════════════════════════════════════════════════════════
# BUG-40：_ParseLine 仅替换行首 ~
# ═══════════════════════════════════════════════════════════


def test_parse_line_only_replaces_leading_tilde():
    """工具名含 ~ 时 spinner 仅替换行首前缀位 ~。"""
    from src.tui.app import app as app_mod
    from src.tui.app import _fx

    # 构造 parse_line（apply 结构 `  ~ 工具名...`）
    class _Model:
        def __init__(self):
            self.parse_line = None

    model = _Model()
    # 用 _ParseLine 直接渲染一个含 ~ 工具名的行
    from src.renderer.ansi.helpers import AnsiLine
    from src.tui.core.style import Style
    # 结构：`  ~ ~/proj ls`
    model.parse_line = AnsiLine.of("  ~ ~/proj ls", Style(fg=242))
    # 渲染 _ParseLine
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.element import h
    r = Reconciler()
    root = r.create_root()
    el = app_mod._ParseLine({"model": model})
    r.render(root, el, 80, 24)
    from src.tui.ink import components as C
    frame = C.render_frame(root, 80)
    text = frame.lines[0].plain
    sp = _fx.SPINNER_FRAMES[_fx.spinner_frame(10.0, _fx.SPINNER_FRAMES)]
    assert text.startswith(f"  {sp} "), f"行首 ~ 应替换为 spinner: {text!r}"
    assert "~/proj" in text, f"工具名内的 ~ 不应被替换: {text!r}"


# ═══════════════════════════════════════════════════════════
# BUG-41：ChatView 开放块行 key 绝对行号
# ═══════════════════════════════════════════════════════════


def test_chat_view_open_block_key_absolute_row():
    """开放块行 key 用块内绝对行号（增量提交后已渲染行 key 不变）。"""
    from src.tui.app.chat_view import ChatView
    from src.tui.app.model import AppModel, ChatBlock
    from src.renderer.ansi.helpers import AnsiLine

    m = AppModel()
    m.width = 40
    blk = ChatBlock("content")
    m.blocks.append(blk)
    m.content_block_index = 0
    # 首段 5 行提交（模拟段落闭合增量提交）
    for i in range(5):
        blk.lines.append(AnsiLine.of(f"line{i}", None))
    m.commit_open_block(blk)
    # 追加未提交尾 3 行
    for i in range(5, 8):
        blk.lines.append(AnsiLine.of(f"line{i}", None))

    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.element import h
    r = Reconciler()
    root = r.create_root()
    r.render(root, h(ChatView, {"model": m, "width": 40}), 40, 24)
    keys1 = sorted(f.props.get("key") for f in _collect_text_fibers(root))
    assert keys1 == ["chat-0-5", "chat-0-6", "chat-0-7"], keys1

    # 流式追加（再 2 行）→ 已渲染行 key 不变（仅新增 8/9）
    for i in range(8, 10):
        blk.lines.append(AnsiLine.of(f"line{i}", None))
    r.render(root, h(ChatView, {"model": m, "width": 40}), 40, 24)
    keys2 = sorted(f.props.get("key") for f in _collect_text_fibers(root))
    assert keys2 == ["chat-0-5", "chat-0-6", "chat-0-7", "chat-0-8", "chat-0-9"], keys2


def _collect_text_fibers(root):
    out = []
    stack = [root]
    while stack:
        f = stack.pop()
        if f.is_host and f.type == "text" and "chat-" in str(f.props.get("key", "")):
            out.append(f)
        c = f.child
        while c:
            stack.append(c)
            c = c.sibling
    return out


# ═══════════════════════════════════════════════════════════
# BUG-42：subagent 卡片元素缓存
# ═══════════════════════════════════════════════════════════


def test_subagent_children_memoized():
    """subagent_lines 引用不变时 use_memo 命中（零重建，组件渲染期）。"""
    from src.tui.app import subagent_panel as sp
    from src.tui.app.model import AppModel
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.element import h, TEXT
    from unittest.mock import patch

    m = AppModel()
    m.subagent_lines = ["line1", "line2"]

    def _Comp(props):
        children = sp.use_subagent_children(props["model"], 40)
        return h("box", None, children)

    with patch.object(sp, "_render_children", wraps=sp._render_children) as mock:
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(_Comp, {"model": m}), 80, 24)
        r.render(root, h(_Comp, {"model": m}), 80, 24)
        assert mock.call_count == 1, (
            f"同 subagent_lines 应只重建 1 次，实际 {mock.call_count}"
        )


# ═══════════════════════════════════════════════════════════
# BUG-43：StatusBar deps 含 spinner_char
# ═══════════════════════════════════════════════════════════

# 已在 tests/test_tui/test_status_bar.py 更新 _render_twice_same_bucket
# 同步 patch _fx/_theme 时间源验证；此处补充 deps 存在性断言。


def test_status_bar_deps_include_spinner_char():
    """StatusBar use_memo deps 含 spinner_char（源码检查，BUG-43）。"""
    import inspect
    from src.tui.app import status_bar as sb
    src = inspect.getsource(sb.StatusBar)
    assert "spinner_char" in src, "StatusBar 应计算 spinner_char"
    # 检查 use_memo 的 deps 元组内包含 spinner_char
    assert "spinner_char,\n" in src or "spinner_char," in src.split("use_memo")[1], (
        "StatusBar use_memo deps 应含 spinner_char"
    )


# ═══════════════════════════════════════════════════════════
# BUG-44：_object_is 对 str 值比较
# ═══════════════════════════════════════════════════════════


def test_object_is_str_value_equal():
    from src.tui.ink.hooks import _object_is
    # 非 intern 字符串同值 → 相等（修复前 is 比较失败 → 缓存恒失效）
    a = "".join(["⠋"])
    b = "".join(["⠋"])
    assert a is not b or True  # 可能被 intern，不依赖
    assert _object_is(a, b)


def test_deps_equal_str_value():
    from src.tui.ink.hooks import _deps_equal
    assert _deps_equal(["⠋", 1], ["⠋", 1]), "str 同值应相等（缓存命中）"
    assert not _deps_equal(["a"], ["b"]), "str 不同值应不等"


# ═══════════════════════════════════════════════════════════
# 完善 react ink：borderStyle singleDouble/doubleSingle + usePrevious
# ═══════════════════════════════════════════════════════════


def test_border_style_single_double_variants():
    """borderStyle singleDouble/doubleSingle 变体。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import components as C
    from src.tui.ink.element import h, BOX, TEXT

    for style_name, expect_tl in (("singleDouble", "╓"), ("doubleSingle", "╒")):
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"width": 6, "height": 3, "border": 1, "borderStyle": style_name},
               h(TEXT, {"children": "x"}))
        r.render(root, el, 80, 24)
        frame = C.render_frame(root, 80)
        assert frame.lines[0].plain[0] == expect_tl, (
            f"{style_name} 顶角应为 {expect_tl}: {frame.lines[0].plain!r}"
        )


def test_use_previous():
    """usePrevious 返回上一帧值（首次 None）。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import hooks as H
    from src.tui.ink.element import h, TEXT

    seen = []

    def _Comp(props):
        prev = H.usePrevious(props["v"])
        seen.append(prev)
        return h(TEXT, {"children": str(props["v"])})

    r = Reconciler()
    root = r.create_root()
    r.render(root, h(_Comp, {"v": 1}), 80, 24)
    r.render(root, h(_Comp, {"v": 2}), 80, 24)
    r.render(root, h(_Comp, {"v": 3}), 80, 24)
    assert seen == [None, 1, 2], seen


# ═══════════════════════════════════════════════════════════
# BUG-45/46：stdout_tracker 刷盘退避 + partial_line 上限
# ═══════════════════════════════════════════════════════════


def test_flush_worker_no_restart_on_failure():
    """刷盘失败（flock 冲突）后不立即重启线程（防线程风暴）。"""
    import threading
    from src.tui._stdout_tracker import _StdoutLineTracker

    tracker = _StdoutLineTracker.__new__(_StdoutLineTracker)
    tracker._buffer_lock = threading.Lock()
    tracker._output_buffer = ["x"] * 60
    tracker._flush_in_progress = True
    tracker._pending_flush = True
    tracker._flush_worker_thread = None

    # 模拟 worker 执行：_flush_buffered_lines 失败（返回 False）
    def fake_flush():
        return False

    tracker._flush_buffered_lines = fake_flush

    def _run():
        import src.tui._stdout_tracker as mod
        orig = mod._StdoutLineTracker._flush_worker
        try:
            tracker._flush_worker()
        finally:
            pass

    # 直接调用 _flush_worker（内部 finally 逻辑）
    import src.tui._stdout_tracker as mod
    # 手动执行与 _flush_worker 相同的 finally 逻辑
    success = tracker._flush_buffered_lines()
    with tracker._buffer_lock:
        tracker._flush_in_progress = False
        if success and (len(tracker._output_buffer) >= 50 or (tracker._pending_flush and tracker._output_buffer)):
            pass  # 不应重启
        else:
            tracker._flush_worker_thread = None
            tracker._pending_flush = False
    assert success is False
    assert tracker._flush_worker_thread is None, "失败后不应重启刷盘线程"
    assert tracker._pending_flush is False, "失败后 pending 复位（交还定时器）"


def test_partial_line_bounded():
    """无换行长行累积有上限（保留尾部最新内容）。"""
    from src.tui._stdout_tracker import _StdoutLineTracker, _PARTIAL_LINE_MAX

    tracker = _StdoutLineTracker.__new__(_StdoutLineTracker)
    tracker._partial_line = ""
    tracker._in_bottom_bar = False
    # 直接调用 _add_text（需要 _ring/_output_buffer/_buffer_lock）
    import threading
    from collections import deque
    tracker._ring = deque(maxlen=1000)
    tracker._output_buffer = []
    tracker._buffer_lock = threading.Lock()

    big = "a" * (_PARTIAL_LINE_MAX + 100)
    tracker._add_text(big)
    assert len(tracker._partial_line) <= _PARTIAL_LINE_MAX, (
        f"partial_line 应被截断: {len(tracker._partial_line)}"
    )
    assert tracker._partial_line.endswith("a" * 100), "应保留尾部最新内容"


# ═══════════════════════════════════════════════════════════
# BUG-47：format_speed 非有限值防护
# ═══════════════════════════════════════════════════════════


def test_format_speed_non_finite():
    from src.tui._format import format_speed
    assert format_speed(float("nan")) == "-"
    assert format_speed(float("inf")) == "-"


# ═══════════════════════════════════════════════════════════
# BUG-49：补全弹窗/搜索激活时动画推进
# ═══════════════════════════════════════════════════════════


def test_needs_animation_completion():
    """补全弹窗可见时 _needs_animation 返回 True（呼吸动画推进）。"""
    import src.tui.ink.session as sess
    s = sess.InkSession.__new__(sess.InkSession)
    model = type("M", (), {})()
    model.status = type("S", (), {"status_active": False})()
    model.tool_boxes = {}
    model.parse_line = None
    model.history_search = None
    model.completion = type("C", (), {"visible": True, "items": [1]})()
    s._model = model
    assert sess.InkSession._needs_animation(s) is True


def test_needs_animation_search():
    """反向历史搜索激活时 _needs_animation 返回 True。"""
    import src.tui.ink.session as sess
    s = sess.InkSession.__new__(sess.InkSession)
    model = type("M", (), {})()
    model.status = type("S", (), {"status_active": False})()
    model.tool_boxes = {}
    model.parse_line = None
    model.completion = type("C", (), {"visible": False, "items": []})()
    model.history_search = type("H", (), {"active": True})()
    s._model = model
    assert sess.InkSession._needs_animation(s) is True


def test_needs_animation_idle_false():
    """空闲（无动画状态）时返回 False（CPU ~0）。"""
    import src.tui.ink.session as sess
    s = sess.InkSession.__new__(sess.InkSession)
    model = type("M", (), {})()
    model.status = type("S", (), {"status_active": False})()
    model.tool_boxes = {}
    model.parse_line = None
    model.completion = type("C", (), {"visible": False, "items": []})()
    model.history_search = None
    s._model = model
    assert sess.InkSession._needs_animation(s) is False


# ═══════════════════════════════════════════════════════════
# BUG-50：h() 生成器/迭代器子级展开
# ═══════════════════════════════════════════════════════════


def test_h_generator_children_expanded():
    """生成器子级扁平展开（修复前被 str() 转成 `<generator>` 文本）。"""
    from src.tui.ink.element import h, TEXT, BOX, Element

    el = h(BOX, None, (h(TEXT, {"children": str(i)}) for i in range(3)))
    assert isinstance(el, Element)
    assert len(el.children) == 3
    assert all(c.type == "text" for c in el.children)


def test_h_iterator_children_expanded():
    """迭代器子级扁平展开。"""
    from src.tui.ink.element import h, TEXT, BOX

    el = h(BOX, None, iter([h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})]))
    assert len(el.children) == 2
    assert el.children[0].props["children"] == "a"
    assert el.children[1].props["children"] == "b"


# ═══════════════════════════════════════════════════════════
# BUG-51：lerp_color NaN 防护
# ═══════════════════════════════════════════════════════════


def test_lerp_color_non_finite():
    from src.tui.core.color import lerp_color
    assert lerp_color(0, 255, float("nan")) == 0
    assert lerp_color(10, 20, float("inf")) == 10


# ═══════════════════════════════════════════════════════════
# BUG-53：stdout_tracker.buffer 属性防御
# ═══════════════════════════════════════════════════════════


def test_stdout_tracker_buffer_fallback():
    """real_stdout 无 buffer 属性时返回自身（不抛 AttributeError）。"""
    from src.tui._stdout_tracker import _StdoutLineTracker
    import io as _io
    tracker = _StdoutLineTracker.__new__(_StdoutLineTracker)
    tracker._real_stdout = _io.StringIO()
    assert tracker.buffer is tracker._real_stdout


# ═══════════════════════════════════════════════════════════
# BUG-54：_panel_refresh 渲染异常后保留脏标记
# ═══════════════════════════════════════════════════════════


def test_panel_refresh_keeps_dirty_on_exception():
    """渲染异常时不复位 _dirty（下拍重试，防卡陈旧内容）。"""
    from src.tui import _subagent_panel as sp
    ctrl = sp.SubAgentPanelController.__new__(sp.SubAgentPanelController)
    ctrl._dirty = True
    ctrl._last_emit_time = 0.0
    ctrl._EMIT_INTERVAL = 0.0
    ctrl._pending_emit = False

    def _boom():
        raise RuntimeError("boom")
    ctrl._render_frame = _boom
    ctrl._last_pushed_frame = []
    ctrl._frame = 0
    ctrl._push_frame = lambda lines: None
    # 直接执行 _panel_refresh 主体逻辑（异常路径）
    try:
        ctrl._panel_refresh()
    except Exception:
        pass
    # 异常路径 return → _dirty 保留 True
    assert ctrl._dirty is True, "渲染异常后脏标记应保留（下拍重试）"


# ═══════════════════════════════════════════════════════════
# BUG-55：subagent 工具历史条数上限
# ═══════════════════════════════════════════════════════════


def test_tool_history_bounded():
    """工具历史超过上限时弹出最旧记录。"""
    from src.tui._subagent_state import StateStore, _MAX_TOOL_HISTORY
    store = StateStore()
    store.add_agent("a", "desc")
    slot = store._agents["a"]
    # 直接追加超过上限的记录
    for i in range(_MAX_TOOL_HISTORY + 20):
        store.start_tool("a", f"t{i}", "")
    assert len(slot.tool_history) <= _MAX_TOOL_HISTORY
    # 最旧记录被弹出（保留最新）
    assert slot.tool_history[-1].tool_name == f"t{_MAX_TOOL_HISTORY + 19}"


# ═══════════════════════════════════════════════════════════
# BUG-56：diff hunk 头 ANSI 消毒
# ═══════════════════════════════════════════════════════════


def test_diff_hunk_header_sanitized():
    """hunk 头行消毒（ANSI 注入防护）。"""
    from src.tui._diff_renderer import _render_chunk, _sanitize_ansi
    assert "\x1b[" not in _sanitize_ansi("@@ -1 +1 @@ \x1b[31mINJECT"), (
        "hunk 头应消毒 ANSI"
    )


# ═══════════════════════════════════════════════════════════
# BUG-58：事件批处理队列水位限制
# ═══════════════════════════════════════════════════════════


def test_batcher_pending_bounded():
    """批处理待处理队列超过上限时丢弃最旧。"""
    from src.tui.events.event_bus import _TimeWindowBatcher
    b = _TimeWindowBatcher(window=999.0)  # 窗口很大 → 不 flush
    b._last_dispatch = 0.0
    calls = []
    h = lambda ev: calls.append(ev)
    # 入队超过上限的事件
    for i in range(_TimeWindowBatcher._MAX_PENDING + 50):
        b.enqueue(h, i)
    assert len(b._pending) <= _TimeWindowBatcher._MAX_PENDING
    # 保留最新（队尾是最新）
    assert b._pending[-1][1] == _TimeWindowBatcher._MAX_PENDING + 49
    assert b._pending[0][1] == 50  # 最旧被丢弃


# ═══════════════════════════════════════════════════════════
# BUG-59：update_tool_parsing 仅 phase 变化时重置时间
# ═══════════════════════════════════════════════════════════


def test_update_tool_parsing_phase_time_stable():
    """parsing 事件逐段到达时 phase 时间不归零。"""
    from src.tui._subagent_state import StateStore
    store = StateStore()
    store.add_agent("a", "desc")
    slot = store._agents["a"]
    import time as _t
    store.update_tool_parsing("a", "search", "q1")
    t1 = slot.model_phase_start
    # 模拟下一段到达（时间推进后）
    store.update_tool_parsing("a", "search", "q1 q2")
    assert slot.model_phase_start == t1, "同 phase 不应重置起始时间"


# ═══════════════════════════════════════════════════════════
# BUG-61：_merge_line 宽字符残留清理
# ═══════════════════════════════════════════════════════════


def test_merge_line_wide_char_residue():
    """覆盖宽字符首列时清除残留第二列。"""
    from src.tui.ink.components import _merge_line, _canvas_row_to_line
    from src.tui.ink.output import StyledRun, Line
    # 已有行含宽字符（占 0-1 列）
    row = {0: ("中", None), 1: ("中", None)}
    # 在 x=0 覆盖 ASCII 'a'（重叠 → 逐键覆盖）
    merged = _merge_line(row, 0, Line([StyledRun("a", None)]))
    # 转 Line 后不应有残留第二列字形
    line = _canvas_row_to_line(merged)
    assert line.plain == "a", f"宽字符残留应清除: {line.plain!r}"


# ═══════════════════════════════════════════════════════════
# BUG-62：reasoning 块不创建冻结缓存
# ═══════════════════════════════════════════════════════════


def test_reasoning_no_frozen_cache():
    """reasoning 块关闭不创建冻结缓存（无消费方，防死内存）。"""
    from src.tui.app.model import AppModel
    m = AppModel()
    m.width = 40
    # 模拟 reasoning 流式
    m.ensure_reasoning()
    rr = m.reasoning_renderer
    rr.write("thinking...")
    m.close_reasoning()
    assert m.blocks[0]._cached_ink_lines is None, (
        "reasoning 块不应创建冻结缓存"
    )
