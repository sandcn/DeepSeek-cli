"""editmsg 消息选择弹窗独立协议测试（2026-08-18 用户需求）。

用户需求：
  1. **editmsg 与 user_select 不能用同一份代码**——/editmsg 消息选择使用
     独立状态（EditMsgSelectState + model.editmsg_select）+ 独立组件
     （EditMsgSelectPopup）+ 独立底部视图（bottom_view="editmsg"），
     不复用 model.user_select / UserSelectPopup / bottom_view="user_select"。
  2. **editmsg 每条信息只显示一行**——options 为单行摘要（_user_msg_summary
     把多行消息折叠为单行），组件每选项渲染一行。

覆盖：EditMsgSelectPopup 渲染（单行/高亮/limit/不可见/空选项）、
_user_msg_summary 单行折叠、EditMsgSelectState.try_set_final 原子终态、
message_editor 标准路径设置 editmsg_select 且不触碰 user_select。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from src.tui.app._state_types import EditMsgSelectState, UserSelectState
from src.tui.app.editmsg_select import EditMsgSelectPopup
from src.tui.app.user_select import UserSelectPopup
from src.tui.ink import h
from src.tui.ink.components import render_frame
from src.tui.ink.reconciler import Reconciler


def _es(**kw):
    base = dict(
        visible=True, seq=1, title="选择要编辑的消息",
        options=["0. ● │ 第一条", "1. ● │ 第二条", "2. ● │ 第三条"],
        selected=2, deadline=0.0,
    )
    base.update(kw)
    return EditMsgSelectState(**base)


def _popup_frame(props, width: int = 80, height: int = 24):
    """调和 + 布局 + 渲染 EditMsgSelectPopup，返回 (reconciler, root, frame)。"""
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h(EditMsgSelectPopup, props), width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    return [ln.plain for ln in frame.lines]


# ═══════════════════════════════════════════════════════════
# 每条消息只显示一行
# ═══════════════════════════════════════════════════════════

def test_editmsg_each_message_single_line():
    """每条消息只显示一行：3 条消息 → 弹窗 5 行（1 标题 + 3 选项 + 1 提示），
    每条消息恰好一行（选项为单行摘要，不展开多行）。"""
    es = _es()
    _, _, frame = _popup_frame({"model": SimpleNamespace(editmsg_select=es), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 5, f"应为 1 标题 + 3 选项 + 1 提示 = 5 行: {lines}"
    assert "第一条" in lines[1]
    assert "第二条" in lines[2]
    assert "第三条" in lines[3]
    # 每行只含一条消息（不重复、不展开）
    assert "第一条" not in lines[2] and "第一条" not in lines[3]
    assert "第二条" not in lines[1] and "第二条" not in lines[3]
    assert "第三条" not in lines[1] and "第三条" not in lines[2]


def test_user_msg_summary_flattens_multiline():
    """_user_msg_summary 把多行/超长消息折叠为单行摘要（换行 → 空格，
    超宽截断 + "..."）——「每条信息只显示一行」的核心。"""
    from src.tui.pipeline.message_editor import _user_msg_summary
    s = _user_msg_summary({"role": "user", "content": "第一行\n第二行\n第三行"}, 0)
    assert "\n" not in s, "摘要必须是单行"
    assert "第一行 第二行 第三行" in s
    assert s.startswith("0. ● │ ")
    # 超长截断
    long_msg = _user_msg_summary({"role": "user", "content": "x" * 200}, 1, max_w=40)
    assert long_msg.endswith("...")
    assert len(long_msg) <= 60


def test_editmsg_popup_is_not_user_select_popup():
    """EditMsgSelectPopup 与 UserSelectPopup 是不同组件（不共用代码）。"""
    assert EditMsgSelectPopup is not UserSelectPopup
    # 组件消费独立状态字段（editmsg_select），不读 user_select
    import inspect
    src = inspect.getsource(EditMsgSelectPopup)
    assert "editmsg_select" in src
    assert "user_select" not in src


# ═══════════════════════════════════════════════════════════
# 高亮跟随选中项
# ═══════════════════════════════════════════════════════════

def test_editmsg_highlight_follows_selected():
    """selected 变化 → ▶ 跟随到对应行（单选高亮语义）。"""
    es1 = _es(selected=0)
    _, _, frame1 = _popup_frame({"model": SimpleNamespace(editmsg_select=es1), "width": 80})
    lines1 = _frame_plain(frame1)
    assert "▶" in lines1[1] and "▶" not in lines1[2]

    es2 = _es(selected=1)
    _, _, frame2 = _popup_frame({"model": SimpleNamespace(editmsg_select=es2), "width": 80})
    lines2 = _frame_plain(frame2)
    assert "▶" in lines2[2] and "▶" not in lines2[1]


# ═══════════════════════════════════════════════════════════
# 超屏防护 limit
# ═══════════════════════════════════════════════════════════

def test_editmsg_limit_budget(monkeypatch):
    """大量消息时控件 limit 截断显示（行数预算），交互仍可导航到隐藏项。"""
    monkeypatch.setattr(
        "src.tui.app.editmsg_select._editmsg_item_rows", lambda: 2,
    )
    es = _es(options=[f"{i}. ● │ msg{i}" for i in range(5)], selected=0)
    _, _, frame = _popup_frame({"model": SimpleNamespace(editmsg_select=es), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 4, f"limit=2 → 1+2+1=4 行: {lines}"
    assert "msg0" in lines[1] and "msg1" in lines[2]
    assert "msg2" not in lines


# ═══════════════════════════════════════════════════════════
# 不可见 / 空选项防御
# ═══════════════════════════════════════════════════════════

def test_editmsg_invisible_renders_empty():
    """visible=False（默认）→ 组件渲染空 TEXT 零高度（不占行）。"""
    es = EditMsgSelectState()
    _, _, frame = _popup_frame({"model": SimpleNamespace(editmsg_select=es), "width": 80})
    lines = _frame_plain(frame)
    assert lines == [""], f"不可见应零高度: {lines}"


def test_editmsg_empty_options_invisible():
    """options 为空（异常状态）→ 不可见（无可交互选项，不崩溃）——
    编辑器轮询 deadline 超时兜底，不会永久卡死。"""
    es = _es(options=[])
    _, _, frame = _popup_frame({"model": SimpleNamespace(editmsg_select=es), "width": 80})
    lines = _frame_plain(frame)
    assert lines == [""]


# ═══════════════════════════════════════════════════════════
# EditMsgSelectState 原子终态写入（first-write-wins）
# ═══════════════════════════════════════════════════════════

def test_editmsg_state_try_set_final_first_writer_wins():
    """try_set_final 原子终态写入：首次写入生效，后续写入不覆盖。"""
    es = EditMsgSelectState()
    assert es.try_set_final("confirmed", ["A"]) is True
    assert es.done is True and es.action == "confirmed" and es.result == ["A"]
    # 二次写入（如编辑器超时）被拒绝
    assert es.try_set_final("timeout", []) is False
    assert es.action == "confirmed" and es.result == ["A"]


def test_editmsg_state_result_copy():
    """result 入参浅拷贝：外部后续修改不影响已提交终态。"""
    es = EditMsgSelectState()
    r = ["A"]
    es.try_set_final("confirmed", r)
    r.append("B")
    assert es.result == ["A"]


# ═══════════════════════════════════════════════════════════
# message_editor 标准路径：使用独立协议，不触碰 user_select
# ═══════════════════════════════════════════════════════════

class _StubSession:
    """最小 session 桩（request_bottom_redraw 记录调用时的 bottom_view）。"""

    def __init__(self, model=None):
        self.model = model
        self.views = []
        self.calls = []

    def request_bottom_redraw(self):
        self.calls.append("redraw")
        if self.model is not None:
            self.views.append(getattr(self.model, "bottom_view", None))


def test_message_editor_uses_editmsg_not_user_select():
    """/editmsg 消息选择设置 editmsg_select + bottom_view="editmsg"，
    **不设置 user_select、不激活 "user_select" 底部视图**（独立协议）。"""
    from src.tui.pipeline.message_editor import MessageEditor

    m = SimpleNamespace(
        editmsg_select=EditMsgSelectState(),
        user_select=UserSelectState(),
        bottom_view="",
    )
    session = _StubSession(model=m)

    class _FakeBottomBar:
        _model = m
        _session = session

    editor = MessageEditor(bottom_bar=_FakeBottomBar())

    def _set_done():
        time.sleep(0.1)
        m.editmsg_select.done = True
        m.editmsg_select.action = "confirmed"
        m.editmsg_select.selected = 0

    t = threading.Thread(target=_set_done, daemon=True)
    t.start()
    idx = editor._interactive_message_select(
        [(0, {"role": "user", "content": "hi"})], ["0. ● │ hi"],
    )
    assert idx == 0
    assert "editmsg" in session.views, "打开时应激活独立底部视图"
    assert "user_select" not in session.views, "editmsg 不得激活 user_select 视图"
    assert m.editmsg_select.visible is False, "清理后 editmsg_select 应复位"
    assert m.user_select.visible is False, "user_select 应全程未被触碰"
    assert m.bottom_view == "", "清理后应恢复正常底部区"


def test_message_editor_options_are_single_line_summaries():
    """/editmsg 弹窗 options 为单行摘要（无 option_lines）——每条消息一行。"""
    from src.tui.pipeline.message_editor import MessageEditor, _user_msg_summary

    m = SimpleNamespace(
        editmsg_select=EditMsgSelectState(),
        user_select=UserSelectState(),
        bottom_view="",
    )
    session = _StubSession(model=m)

    class _FakeBottomBar:
        _model = m
        _session = session

    editor = MessageEditor(bottom_bar=_FakeBottomBar())

    def _set_done():
        time.sleep(0.1)
        m.editmsg_select.done = True
        m.editmsg_select.action = "confirmed"
        m.editmsg_select.selected = 0

    t = threading.Thread(target=_set_done, daemon=True)
    t.start()
    msgs = [(0, {"role": "user", "content": "第一行\n第二行"})]
    display = [_user_msg_summary(msgs[0][1], 0)]
    assert "\n" not in display[0], "摘要必须是单行"
    idx = editor._interactive_message_select(msgs, display)
    assert idx == 0
    assert m.editmsg_select.visible is False


def test_message_editor_does_not_set_option_lines():
    """/editmsg 不再向任何状态写入 option_lines 字段（user_select 状态已移除
    该字段——editmsg 与 user_select 完全解耦）。"""
    from src.tui.pipeline.message_editor import MessageEditor

    m = SimpleNamespace(
        editmsg_select=EditMsgSelectState(),
        user_select=UserSelectState(),
        bottom_view="",
    )
    session = _StubSession(model=m)

    class _FakeBottomBar:
        _model = m
        _session = session

    editor = MessageEditor(bottom_bar=_FakeBottomBar())

    def _set_done():
        time.sleep(0.1)
        m.editmsg_select.done = True
        m.editmsg_select.action = "confirmed"
        m.editmsg_select.selected = 0

    t = threading.Thread(target=_set_done, daemon=True)
    t.start()
    editor._interactive_message_select(
        [(0, {"role": "user", "content": "hi"})], ["0. ● │ hi"],
    )
    assert not hasattr(m.editmsg_select, "option_lines"), "editmsg 状态不得含 option_lines"
    assert not hasattr(UserSelectState(), "option_lines"), "user_select 状态已移除 option_lines"


def test_app_model_has_editmsg_select_field():
    """AppModel 具备独立 editmsg_select 字段（与 user_select 平行、独立）。"""
    from src.tui.app.model import AppModel
    m = AppModel()
    assert isinstance(m.editmsg_select, EditMsgSelectState)
    assert isinstance(m.user_select, UserSelectState)
    assert m.editmsg_select is not m.user_select, "两个状态必须独立实例"
    # 各自字段独立（不共用 option_lines）
    assert not hasattr(m.editmsg_select, "option_lines")
    assert not hasattr(m.user_select, "option_lines")


def test_reset_display_resets_editmsg_select():
    """reset_display（Ctrl+L 清屏）同时复位 editmsg_select。"""
    from src.tui.app.model import AppModel
    m = AppModel()
    m.editmsg_select.visible = True
    m.editmsg_select.seq = 5
    m.bottom_view = "editmsg"
    m.reset_display()
    assert m.editmsg_select.visible is False
    assert m.editmsg_select.seq == 0
    assert m.bottom_view == ""


__all__ = [
    "_es",
    "_popup_frame",
    "_StubSession",
]
