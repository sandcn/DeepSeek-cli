"""user_select 连续弹出显示错乱修复测试（2026-08-18）。

bug：多次弹出 user_select 并用回车回复后，再次弹出显示错乱——标题 (n/N)/
高亮行/勾选标记残留上一次弹窗的选中状态（如两个标题行叠加 (1/16) 与
(7/16)）。

根因：工具 finally 清理 ``model.user_select = UserSelectState()`` 使 seq 归零
——连续两次弹出之间若「关闭帧」被渲染节流（10Hz）合并跳过，第二次打开的 seq
与第一次相同 → App 的 key_fn（``us-{seq}``）返回相同 key → 调和器复用旧
fiber → UserSelectPopup 内部 use_state（selected/checked）残留上一次的值。

修复：
  1. 全部清理点（user_select 工具 / CommandUiAdapter / message_editor /
     model.reset_display）清理时**保留 seq**（seq 单调递增 → key 永不重复 →
     调和器每次强制重挂载弹窗组件，重置内部 selected/checked state）；
  2. 清理顺序改为先关 bottom_view 再清状态（消除「状态已重置但 bottom_view
     仍指向弹窗」的空白帧窗口）；
  3. 组件级双保险：UserSelectPopup / EditMsgSelectPopup 检测 us/es 实例变化
     （新 UserSelectState 对象）时，本帧即以新 selected 计算高亮并排队
     set_selected 收敛——即使 fiber 因 key 复用被保留，也不残留旧选中。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.tui.app.model import AppModel, UserSelectState, EditMsgSelectState
from src.tui.app.user_select import UserSelectPopup
from src.tui.app.editmsg_select import EditMsgSelectPopup
from src.tui.ink import h, TEXT
from src.tui.ink import hooks
from src.tui.ink.fiber import Fiber, TAG_FUNCTION
from src.tui.ink.reconciler import Reconciler

#: 16 个选项（对齐用户报障「多选测试 · 16个选项」）
_OPTS16 = [f"城市{i}" for i in range(1, 17)]


# ── 测试辅助 ──────────────────────────────────────────────

class _FakeInput:
    def flush_stdin_buffer(self):
        pass


class _FakeBottomBar:
    is_completion_visible = False


class _FakeChatUI:
    """最小 ChatUIConsumer 桩（get_model / request_bottom_redraw 协议）。"""

    def __init__(self, model):
        self._model = model
        self.bottom_bar = _FakeBottomBar()

    def get_model(self):
        return self._model

    def get_input_component(self):
        return _FakeInput()

    def request_bottom_redraw(self):
        pass


def _render_component(component, model, fiber=None):
    """在手动 fiber 上下文渲染弹窗组件（返回 fiber + 元素树）。

    复用传入 fiber（模拟调和器 fiber 复用——key 相同时 use_state 保留）；
    不传则新建 fiber（模拟重挂载）。
    """
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, {"model": model, "width": 80})
    hooks._push_current(fiber)
    try:
        el = component({"model": model, "width": 80})
    finally:
        hooks._pop_current()
    return fiber, el


def _title(el) -> str:
    """弹窗组件返回 Column，children[0] 为标题行 TEXT。"""
    return el.children[0].props["children"]


# ── 1. 清理点保留 seq（单调递增） ─────────────────────────

@pytest.mark.asyncio
async def test_user_select_tool_cleanup_preserves_seq(monkeypatch):
    """user_select 工具 finally 清理：保留 seq（不归零）+ bottom_view 复位。"""
    from src.tools import user_select as us_mod

    monkeypatch.setattr(us_mod, "HAS_TERMIOS", True)
    monkeypatch.setattr(us_mod.os, "isatty", lambda fd: True)
    # pytest capture 会把 sys.stdin 替换为 DontReadFromInput（fileno 抛异常）——
    # 在模块命名空间注入伪 sys（stdin.fileno()=0），只影响本模块内的
    # ``sys.stdin.fileno()`` 查找，不污染全局 sys。
    class _FakeStdin:
        def fileno(self):
            return 0

    class _FakeSys:
        def __init__(self, stdin):
            self.stdin = stdin

    monkeypatch.setattr(us_mod, "sys", _FakeSys(_FakeStdin()))
    model = AppModel()
    fake = _FakeChatUI(model)
    monkeypatch.setattr(us_mod, "get_active_chat_ui", lambda: fake)

    async def fake_sleep(_sec):
        # 模拟组件确认（first-write-wins：done 已置位则放弃）
        model.user_select.try_set_final("confirmed", ["城市1"])

    monkeypatch.setattr(us_mod.asyncio, "sleep", fake_sleep)

    func = us_mod.UserSelectFunc(
        title="想去哪个城市旅游？",
        options=list(_OPTS16),
        multi_select=True,
        default_options=["城市1"],
        timeout=120,
    )
    data = json.loads(await func.execute())
    assert data["action"] == "confirmed"
    assert data["selected"] == ["城市1"]
    # ★ 修复断言：清理保留 seq（原 1），不归零——下次打开 seq=2（key 变化
    #   → 强制重挂载）。修复前归零（seq=0）→ 连续打开 key 复用 → fiber 复用
    #   → use_state 残留旧选中。
    assert model.user_select.seq == 1
    assert not model.user_select.visible
    assert model.bottom_view == ""


def test_command_ui_adapter_cleanup_preserves_seq(monkeypatch):
    """CommandUiAdapter 清理：保留 seq + bottom_view 复位。"""
    from src.core.commands import _ui_adapter as uia

    model = AppModel()
    fake = _FakeChatUI(model)
    monkeypatch.setattr(
        uia.CommandUiAdapter, "_get_active_chat_ui", lambda self: fake,
    )

    def fake_sleep(_sec):
        # 模拟组件确认：写 done（first-write-wins）
        model.user_select.try_set_final("confirmed", [])

    monkeypatch.setattr(uia.time, "sleep", fake_sleep)

    adapter = uia.CommandUiAdapter()
    result = adapter.run_bottom_bar_selection(
        items=["A", "B"], display_items=["A", "B"], title="选择", bottom_bar=None,
    )
    assert result["action"] == "confirmed"
    assert model.user_select.seq == 1
    assert model.bottom_view == ""


def test_message_editor_cleanup_preserves_seq(monkeypatch):
    """message_editor（/editmsg）清理：保留 seq + bottom_view 复位。"""
    from src.tui.pipeline import message_editor as me

    model = AppModel()
    fake_session = _FakeChatUI(model)

    class _BB:
        _model = model
        _session = fake_session

    editor = me.MessageEditor(bottom_bar=_BB(), input_=object())

    def fake_sleep(_sec):
        # 模拟组件确认：写 done（first-write-wins）
        model.editmsg_select.try_set_final("confirmed", ["0. ● │ 你好"])

    monkeypatch.setattr(me.time, "sleep", fake_sleep)

    user_msgs = [(0, {"role": "user", "content": "你好"})]
    display_items = ["0. ● │ 你好"]
    idx = editor._interactive_message_select(user_msgs, display_items)
    assert idx == 0
    assert model.editmsg_select.seq == 1
    assert not model.editmsg_select.visible
    assert model.bottom_view == ""


def test_reset_display_preserves_editmsg_seq():
    """清屏（reset_display）重置 editmsg 弹窗时保留 seq（key 唯一）。"""
    model = AppModel()
    model.editmsg_select = EditMsgSelectState(visible=True, seq=5, options=["x"])
    model.reset_display()
    assert model.editmsg_select.seq == 5
    assert not model.editmsg_select.visible
    assert model.bottom_view == ""


# ── 2. 组件级防御：us/es 实例变化时重置 selected ──────────

def test_popup_mount_uses_us_selected():
    """新 fiber 挂载：use_state 初始值 = us.selected（标题正确）。"""
    model = AppModel()
    model.user_select = UserSelectState(
        visible=True, seq=1, title="想去哪个城市旅游？",
        options=list(_OPTS16), multi_select=True, selected=6,
    )
    _, el = _render_component(UserSelectPopup, model)
    assert "(7/16)" in _title(el)


def test_popup_fiber_reuse_resets_selected():
    """同一 fiber 复用 + 新 us 实例（key 复用场景）：不残留旧选中。

    复现路径：弹窗导航到第 7 项 → 关闭（seq 归零）→ 再开（seq 相同、key
    复用）→ fiber 复用。修复后组件检测 us 实例变化，本帧即以新 selected
    计算高亮（标题回 (1/16)）。
    """
    model = AppModel()
    model.user_select = UserSelectState(
        visible=True, seq=1, title="想去哪个城市旅游？",
        options=list(_OPTS16), multi_select=True,
    )
    fiber, el = _render_component(UserSelectPopup, model)
    assert "(1/16)" in _title(el)

    # 模拟用户导航 ↓6 次（控件 onHighlight → set_selected + us.selected=6）
    control = el.children[1]
    control.props["onHighlight"](6)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "(7/16)" in _title(el)

    # 模拟旧 bug：清理归零后重开（seq 相同 → key 复用 → 同一 fiber）
    model.user_select = UserSelectState(
        visible=True, seq=1, title="想去哪个城市旅游？",
        options=list(_OPTS16), multi_select=True,
    )
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    # ★ 修复断言：us 实例变化 → 组件防御 → 标题回 (1/16)，不残留 (7/16)
    assert "(1/16)" in _title(el)


def test_editmsg_popup_fiber_reuse_resets_selected():
    """EditMsgSelectPopup 同机制防御：es 实例变化不残留旧选中。"""
    model = AppModel()
    opts = ["消息摘要1", "消息摘要2", "消息摘要3"]
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=1, title="选择要编辑的消息", options=opts, selected=2,
    )
    fiber, el = _render_component(EditMsgSelectPopup, model)
    assert "(3/3)" in _title(el)

    # 导航到第 1 项
    el.children[1].props["onHighlight"](0)
    fiber, el = _render_component(EditMsgSelectPopup, model, fiber)
    assert "(1/3)" in _title(el)

    # 新 es 实例（selected=2）：组件防御 → 标题回 (3/3)
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=1, title="选择要编辑的消息", options=opts, selected=2,
    )
    fiber, el = _render_component(EditMsgSelectPopup, model, fiber)
    assert "(3/3)" in _title(el)


def test_popup_control_key_includes_seq():
    """弹窗内部控件 key 携带 seq——seq 变化时控件也强制重挂载（勾选重置）。"""
    model = AppModel()
    model.user_select = UserSelectState(
        visible=True, seq=7, title="T", options=["a", "b"], multi_select=True,
    )
    _, el = _render_component(UserSelectPopup, model)
    assert el.children[1].props.get("key") == "us-multiselect-7"

    model2 = AppModel()
    model2.user_select = UserSelectState(
        visible=True, seq=7, title="T", options=["a", "b"],
    )
    _, el2 = _render_component(UserSelectPopup, model2)
    assert el2.children[1].props.get("key") == "us-select-7"

    model3 = AppModel()
    model3.editmsg_select = EditMsgSelectState(
        visible=True, seq=3, title="选择", options=["a", "b"],
    )
    _, el3 = _render_component(EditMsgSelectPopup, model3)
    assert el3.children[1].props.get("key") == "em-select-3"


# ── 3. 调和器端到端：seq 变化 → key 变化 → 重挂载 ─────────

def test_seq_key_change_forces_remount():
    """调和器：seq 单调递增 → key（us-{seq}）变化 → 强制重挂载 → use_state 重置。

    这是本 bug 的根因级验证：seq 相同（归零）时 fiber 复用、use_state 残留；
    seq 递增后每次打开都重挂载、use_state 重新初始化。
    """
    captured = {}

    def Popup(props):
        us = props["us"]
        sel, set_sel = hooks.use_state(us.selected)
        captured["sel"] = sel
        captured["set_sel"] = set_sel
        return h(TEXT, {"children": f"sel={sel}"})

    def Root(props):
        model = props["model"]
        if getattr(model, "bottom_view", "") == "user_select":
            return h(Popup, {
                "us": model.user_select,
                "key": f"us-{model.user_select.seq}",
            })
        return h(TEXT, {"children": "normal"})

    model = AppModel()
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()

    def render():
        rec.render(root, h(Root, {"model": model}), 80, 24)

    # 打开弹窗（seq=1）
    model.user_select = UserSelectState(visible=True, seq=1, selected=0)
    model.bottom_view = "user_select"
    render()
    assert captured["sel"] == 0

    # 导航到第 7 项
    captured["set_sel"](6)
    render()
    assert captured["sel"] == 6

    # 清理（修复后保留 seq=1）→ 关闭底部视图
    model.bottom_view = ""
    model.user_select = UserSelectState(seq=1)
    render()

    # 再次打开（seq=2 → key=us-2 ≠ us-1 → 强制重挂载 → use_state 重置为 0）
    model.user_select = UserSelectState(visible=True, seq=2, selected=0)
    model.bottom_view = "user_select"
    render()
    assert captured["sel"] == 0
