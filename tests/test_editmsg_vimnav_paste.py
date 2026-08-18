"""editmsg 弹窗 vim 导航（j/k）在粘贴流中被吞——「很多上文时按回车不能
编辑对应消息」根因修复回归测试。

★ 背景（2026-08-19 bug，vim 导航键与 Enter 同批累积）：

渲染线程忙（大量上文一帧 100ms~1s）时，用户在 /editmsg 消息选择弹窗用
vim 导航键（``j``/``k``，提示行「↑↓/jk 选择」明确支持）快速移动后按
Enter——这些字节在同一次批量 ``os.read`` 中累积（stdin 缓冲合并）：

  ``InputDispatcher._dispatch_byte(ord('j'))`` → ``InputIO.try_read_paste``
  把 pending 中的 ``j\\r`` 判为粘贴突发（``\\r`` 不可打印，短突发降级
  不适用）；774a2f7 的末尾 Enter 剥离只把尾部 ``\\r`` 回写 pending，剩余
  body ``j`` 仍与首字符拼成多字符粘贴文本 ``"jj"``。

  paste_event(kind="char", char="jj") 进 input router →
  ``SelectInput._handle`` 的 ``_is_vim_nav`` 只匹配**单字符** j/J →
  ``"jj"`` 不匹配 → ``consumeAll`` 吞掉 → **导航全部丢失** → 回写的
  ``\\r`` 正常分发为 enter → 确认的是**未导航的默认选中（最后一条）**。

用户可见症状（/editmsg 场景，很多上文时**大概率**复现）：
  - 在弹窗按 jj 导航 + Enter → 编辑的却是最后一条消息（导航丢失）；
  - 渲染越慢（上文越多），按键同批累积概率越大，失败率越高。

修复（``_select_input.py`` / ``_multi_select.py``）：
  - 提取 ``_nav_for_char(ch)`` 单字符导航判定；
  - char 事件**多字符**（粘贴流）时逐字符收集导航并循环应用（每个导航
    更新 ref/state/onHighlight），单字符行为零变化；
  - 弹窗场景粘贴流中的导航键不再丢失（Enter 由尾部剥离链路正常分发）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.tui._input_io import InputIO
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_parser import InputParser
from src.tui._input_dispatcher import InputDispatcher
from src.tui.app.model import AppModel, EditMsgSelectState
from src.tui.app.editmsg_select import EditMsgSelectPopup
from src.tui.ink import hooks
from src.tui.ink.element import h
from src.tui.ink.fiber import Fiber, TAG_FUNCTION
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.widgets import SelectInput, MultiSelect
from src.tui._input_parser import KeyEvent


# ── 测试辅助 ──────────────────────────────────────────────

def _make_dispatcher(pipe_r: int):
    """构造真实 InputDispatcher（pipe fd 模拟 stdin）。"""
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


def _drain_all(dispatcher: InputDispatcher, rounds: int = 6, gap: float = 0.03):
    """模拟渲染线程忙一帧后统一 process_events（含后续 pending 轮次）。"""
    for _ in range(rounds):
        time.sleep(gap)
        dispatcher.process_events()


def _render_component(component, props, fiber=None):
    """在手动 fiber 上下文渲染控件（返回 fiber + 元素树）。

    复用传入 fiber（模拟调和器 fiber 复用——hook 按下标复用保留状态）；
    不传则新建 fiber（模拟挂载）。
    """
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    else:
        fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        el = component(dict(props))
    finally:
        hooks._pop_current()
    return fiber, el


def _get_input_handler(fiber: Fiber):
    """取 fiber 上注册的 use_input handler（InputHook.handler）。"""
    for hook in fiber.hooks:
        if getattr(hook, "handler", None) is not None:
            return hook.handler
    raise AssertionError("fiber 上未找到 use_input handler")


def _char_event(text: str) -> KeyEvent:
    """构造多字符 char 事件（模拟 try_read_paste 产出的粘贴事件）。"""
    return KeyEvent(
        kind="char", char=text,
        raw=text.encode("utf-8", errors="replace"),
    )


# ═══════════════════════════════════════════════════════════
# 单元级：SelectInput 粘贴流逐字符导航
# ═══════════════════════════════════════════════════════════

def test_select_input_paste_stream_vim_nav_jj():
    """SelectInput 收到粘贴流 char="jj" → 逐字符导航 2 步（修复前整段吞掉）。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b", "c", "d", "e"],
        "initialIndex": 0,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    consumed = handler(_char_event("jj"))
    assert consumed is True
    # 修复前：navs 为空（"jj" 不是单字符 j）→ consumeAll 吞掉、无导航
    assert highlights == [1, 2]


def test_select_input_paste_stream_vim_nav_mixed():
    """混合字符流 "xkxk" → 仅 k 生效（导航 2 步 up，非导航字符忽略）。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b", "c", "d", "e"],
        "initialIndex": 4,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    assert handler(_char_event("xkxk")) is True
    assert highlights == [3, 2]


def test_select_input_paste_stream_vim_nav_gG():
    """粘贴流 "g"…"G" 逐字符生效：g 跳首、G 跳末。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b", "c", "d", "e"],
        "initialIndex": 2,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    assert handler(_char_event("gG")) is True
    # g → first(0)，G → last(4)
    assert highlights == [0, 4]


def test_select_input_paste_stream_no_nav_keeps_swallow():
    """非导航粘贴文本（无 j/k/g/G）不移动（保持吞掉，行为不回归）。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b", "c"],
        "initialIndex": 1,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    # "hello" 含 l/e/h/o——均非导航字符（小写 g 不在其中）
    assert handler(_char_event("hello")) is True  # consumeAll 吞掉
    assert highlights == []


def test_select_input_single_char_vim_nav_unchanged():
    """单字符 j/k 行为零变化（沿用 _is_vim_nav 原路径）。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b", "c"],
        "initialIndex": 0,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    assert handler(KeyEvent(kind="char", char="j", raw=b"j")) is True
    assert highlights == [1]
    assert handler(KeyEvent(kind="char", char="k", raw=b"k")) is True
    assert highlights == [1, 0]


def test_select_input_nav_bounds_clamped():
    """粘贴流导航越界钳制：首位连按 kk 不越界（-1 钳 0）。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b"],
        "initialIndex": 0,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    assert handler(_char_event("kk")) is True
    # 首项按 k 不移动：两次 k 均无效 → 无高亮回调、仍消费（consumeAll）
    assert highlights == []


def test_select_input_paste_nav_disabled_without_consume_all():
    """consumeAll=False（命令补全弹窗）：粘贴流不导航不消费——放行进输入
    缓冲（行为零回归——粘贴文本不被弹窗吞）。"""
    highlights = []
    fiber, _ = _render_component(SelectInput, {
        "items": ["a", "b", "c"],
        "initialIndex": 0,
        "consumeAll": False,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    # "jj" 粘贴流：非 consumeAll 模式放行（返回 False，不导航）
    assert handler(_char_event("jj")) is False
    assert highlights == []


# ═══════════════════════════════════════════════════════════
# 单元级：MultiSelect 粘贴流逐字符导航
# ═══════════════════════════════════════════════════════════

def test_multi_select_paste_stream_vim_nav_jj():
    """MultiSelect 收到粘贴流 char="jj" → 光标导航 2 步（修复前整段吞掉）。"""
    highlights = []
    fiber, _ = _render_component(MultiSelect, {
        "items": ["a", "b", "c", "d", "e"],
        "initialIndex": 0,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    assert handler(_char_event("jj")) is True
    assert highlights == [1, 2]


def test_multi_select_paste_stream_no_nav_keeps_swallow():
    """MultiSelect 非导航粘贴文本不移动光标（吞掉，行为不回归）。"""
    highlights = []
    fiber, _ = _render_component(MultiSelect, {
        "items": ["a", "b", "c"],
        "initialIndex": 1,
        "consumeAll": True,
        "onHighlight": highlights.append,
    })
    handler = _get_input_handler(fiber)

    assert handler(_char_event("xyz")) is True
    assert highlights == []


# ═══════════════════════════════════════════════════════════
# 端到端：渲染发布的 router + 真实 dispatcher（pipe 模拟同批按键）
# ═══════════════════════════════════════════════════════════

def _setup_e2e():
    """端到端基建：pipe + dispatcher + Reconciler 渲染 EditMsgSelectPopup。

    router 发布链路对齐生产：reconciler.render 构建 composite router →
    ``_publish_input_router`` → ``set_input_router_callback`` 注入的回调 →
    ``dispatcher.set_input_hook_router``。
    """
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)

    from src.tui.ink._hooks_input import set_input_router_callback
    set_input_router_callback(d.set_input_hook_router)

    model = AppModel()
    opts = [f"消息{i}" for i in range(1, 6)]  # 5 条用户消息摘要
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=1, title="选择要编辑的消息",
        options=opts, selected=4,  # 默认选中最后一条
    )

    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(EditMsgSelectPopup, {"model": model, "width": 80}), 80, 24)
    return r, w, d, model


def test_e2e_vim_nav_enter_same_batch_edits_correct_message():
    """kk + Enter 同批到达（渲染忙累积）→ 导航 2 步 + 编辑对应消息。

    editmsg 弹窗默认选中最后一条（最新消息），用户向上（k）导航 2 步后
    Enter。修复前：paste_event("kk") 被 SelectInput consumeAll 吞掉 →
    导航丢失 → Enter 确认默认最后一条（消息5）——「不能编辑对应的用户
    消息」。
    """
    r, w, d, model = _setup_e2e()
    try:
        os.write(w, b"kk\r")  # 一次 write 模拟同批 os.read 累积
        _drain_all(d)

        es = model.editmsg_select
        assert es.done is True
        assert es.action == "confirmed"
        # 导航 2 步：默认 4 → 3 → 2（编辑消息3，非默认最后一条消息5）
        assert es.result == ["消息3"]
        assert es.selected == 2
    finally:
        os.close(w)
        os.close(r)


def test_e2e_vim_nav_enter_same_batch_lf_terminal():
    """ICRNL 终端（Enter 读到 \\n）：kk + \\n 同批 → 同样编辑对应消息。"""
    r, w, d, model = _setup_e2e()
    try:
        os.write(w, b"kk\n")
        _drain_all(d)

        es = model.editmsg_select
        assert es.done is True
        assert es.action == "confirmed"
        assert es.result == ["消息3"]
    finally:
        os.close(w)
        os.close(r)


def test_e2e_arrow_enter_same_batch_no_regression():
    """方向键（ESC 序列）+ Enter 同批 → ESC 回写链路正常（不回归）。"""
    r, w, d, model = _setup_e2e()
    try:
        os.write(w, b"\x1b[B\x1b[B\r")  # ↓↓ + Enter 同批
        _drain_all(d)

        es = model.editmsg_select
        assert es.done is True
        assert es.action == "confirmed"
        # 默认选中最后一条（4），↓ 已在末项不移动——结果仍是消息5
        assert es.result == ["消息5"]
    finally:
        os.close(w)
        os.close(r)


def test_e2e_arrow_up_enter_same_batch_edits_correct_message():
    """↑↑ + Enter 同批（ESC 回写）→ 编辑导航后消息（端到端完整性）。"""
    r, w, d, model = _setup_e2e()
    try:
        os.write(w, b"\x1b[A\x1b[A\r")  # ↑↑ + Enter 同批
        _drain_all(d)

        es = model.editmsg_select
        assert es.done is True
        assert es.result == ["消息3"]  # 4 → 3 → 2
    finally:
        os.close(w)
        os.close(r)


def test_e2e_enter_only_confirms_default_selection():
    """仅 Enter（无导航）同批 → 确认默认最后一条（基础行为不回归）。"""
    r, w, d, model = _setup_e2e()
    try:
        os.write(w, b"\r")
        _drain_all(d)

        es = model.editmsg_select
        assert es.done is True
        assert es.result == ["消息5"]
    finally:
        os.close(w)
        os.close(r)
