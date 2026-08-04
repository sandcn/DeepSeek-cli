"""测试 src/tui/app/user_select.py — UserSelectPopup React Ink 组件。

覆盖：
  - 不可见时零高度（空 TEXT，不占行）；
  - 单选渲染（标题 + ▶ 高亮 + 提示行）；
  - 多选渲染（●/○ 勾选 + 光标高亮）；
  - 分栏说明模式（右栏显示当前选中项说明）；
  - 按键交互：↑↓ 导航 / Enter 确认 / 空格切换（多选）/ Esc 取消；
  - 弹窗激活期间其余按键被消费（阻断输入框副作用）；
  - key=seq 重挂载重置内部 state（连续多次打开不残留旧选中）；
  - done 后组件隐藏。
"""

from __future__ import annotations

import os

from src.tui.app.model import AppModel, UserSelectState
from src.tui.app.user_select import UserSelectPopup
from src.tui.ink import h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.hooks import set_input_router_callback
from src.tui._input_parser import KeyEvent


def _render(r, root, element, width=80, height=24):
    r.render(root, element, width, height)
    return render_frame(root, width)


class _Router:
    """捕获 reconciler 发布的 input router。"""

    def __init__(self):
        self.router = None
        set_input_router_callback(lambda router: setattr(self, "router", router))

    def key(self, kind, char=""):
        return self.router(KeyEvent(kind=kind, char=char))


def _popup(model, width=80):
    return h(UserSelectPopup, {
        "model": model,
        "width": width,
        "key": f"us-{model.user_select.seq}",
    })


def _plain(frame):
    return [ln.plain for ln in frame.lines]


class TestUserSelectPopupRender:
    def test_hidden_zero_height(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=False)
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m))
        assert _plain(frame) == [""]

    def test_done_hidden(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A"], done=True)
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m))
        assert _plain(frame) == [""]

    def test_single_render(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="测试",
                                        options=["A", "B", "C"], selected=1)
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m))
        lines = _plain(frame)
        assert lines[0] == " ▍ 测试 (2/3)"
        assert lines[1] == "    A"
        assert lines[2] == " ▶  B"
        assert lines[3] == "    C"
        assert "↑↓ 选择" in lines[4]

    def test_multi_render(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="多选",
                                        multi_select=True,
                                        options=["A", "B", "C"],
                                        selected=0, checked=[0, 2])
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m))
        lines = _plain(frame)
        assert lines[1] == " ●  A"
        assert lines[2] == " ○  B"
        assert lines[3] == " ●  C"
        assert "切换选中" in lines[4]

    def test_split_desc_mode(self):
        """分栏说明模式：右侧显示当前选中项说明。"""
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="带说明",
            options=["A", "B"],
            option_descriptions=["说明A", "说明B"],
            selected=1,
        )
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m), width=60)
        lines = _plain(frame)
        # 左栏选项 + │ + 右栏说明
        assert "│说明B" in lines[1] or "│ 说明B" in lines[1] or "说明B" in lines[1]

    def test_narrow_width_no_overflow(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="窄",
                                        options=["超长选项文本" * 10], selected=0)
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m), width=15)
        for ln in frame.lines:
            # 行宽不超过文档宽（渲染器行级 diff 宽度不变量）
            assert ln.width <= 15, f"超宽行: {ln.plain!r} ({ln.width})"


class TestUserSelectPopupInteract:
    def test_arrow_navigation(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B", "C"], selected=0)
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[1] == " ▶  A"
        assert cap.key("arrow_down") is True
        assert cap.key("arrow_down") is True
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[3] == " ▶  C"
        assert m.user_select.selected == 2
        # 上边界
        assert cap.key("arrow_up") is True
        assert cap.key("arrow_up") is True
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[1] == " ▶  A"
        # 超出边界不移动
        cap.key("arrow_up")
        assert m.user_select.selected == 0

    def test_enter_confirms_single(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B", "C"], selected=1)
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        assert cap.key("enter") is True
        assert m.user_select.done is True
        assert m.user_select.action == "confirmed"
        assert m.user_select.result == ["B"]

    def test_enter_confirms_multi(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        multi_select=True,
                                        options=["A", "B", "C"],
                                        selected=0, checked=[0, 2])
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        assert cap.key("enter") is True
        assert m.user_select.done is True
        assert m.user_select.action == "confirmed"
        assert m.user_select.result == ["A", "C"]  # 按索引排序

    def test_space_toggles_multi(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        multi_select=True,
                                        options=["A", "B", "C"], selected=0)
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        # 空格勾选 A
        assert cap.key("space") is True
        assert m.user_select.checked == [0]
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[1] == " ●  A"
        # 再按空格取消
        assert cap.key("space") is True
        assert m.user_select.checked == []
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[1] == " ○  A"

    def test_escape_cancels(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B"],
                                        default_options=["A"])
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        assert cap.key("escape") is True
        assert m.user_select.done is True
        assert m.user_select.action == "cancel"
        assert m.user_select.result == ["A"]

    def test_other_keys_consumed(self):
        """弹窗激活期间普通字符按键被消费（阻断输入框副作用）。"""
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B"])
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        assert cap.key("char", "x") is True
        assert cap.key("backspace") is True
        assert cap.key("arrow_left") is True
        # 不触发提交/取消
        assert m.user_select.done is False

    def test_enter_when_done_noop(self):
        """组件完成后按键不再生效（handler 检查 done）。"""
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B"])
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        assert cap.key("enter") is True
        assert m.user_select.done is True
        # done 后重新渲染 → 组件隐藏，router 不再收集（后续按键走旧路径）
        frame = _render(r, root, _popup(m))
        assert _plain(frame) == [""]

    def test_hidden_after_close_releases_input(self):
        """弹窗关闭（visible=False）后 use_input 不再收集——按键放行旧路径。

        回归：修复前 use_input 仅在可见分支调用——visible=False 时 InputHook
        残留 active 在 fiber.hooks，router 仍收集 → 弹窗关闭后所有输入被吞
        （用户无法输入）。修复后 use_input 无条件注册且 is_active=visible。
        """
        m = AppModel()
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()

        # 打开 → 提交
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B"])
        _render(r, root, _popup(m))
        assert cap.key("enter") is True
        assert m.user_select.done is True

        # 工具清理（visible=False）→ 渲染 → 输入不再被消费
        m.user_select = UserSelectState()
        _render(r, root, _popup(m))
        # 渲染后 router 更新：无 active hooks → router 为 None（放行旧路径）
        assert cap.router is None or cap.router(_key("char", "x")) is False


class TestUserSelectPopupRemount:
    def test_key_remount_resets_state(self):
        """seq 变化 → key 变化 → 重挂载，重置内部选中（连续打开不残留）。"""
        m = AppModel()
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()

        # 第一次打开：导航到 C
        m.user_select = UserSelectState(visible=True, seq=1, title="T1",
                                        options=["A", "B", "C"], selected=0)
        _render(r, root, _popup(m))
        cap.key("arrow_down")
        cap.key("arrow_down")
        assert m.user_select.selected == 2

        # 关闭
        m.user_select = UserSelectState()
        _render(r, root, _popup(m))

        # 第二次打开：seq=2 → 重挂载 → 高亮重置为 A
        m.user_select = UserSelectState(visible=True, seq=2, title="T2",
                                        options=["A", "B", "C"], selected=0)
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[1] == " ▶  A"
        assert m.user_select.selected == 0


class TestUserSelectPopupEscEndToEnd:
    """端到端：stdin Esc 字节 → InputDispatcher → router → 弹窗取消。

    回归（2026-08-05）：修复前 InputDispatcher 的 ESC 内联分支直接走中断
    （从未询问 input router）——UserSelectPopup 的 use_input handler 收不到
    escape 事件，弹窗按 Esc 无法取消。修复后 ESC 事件先经 router 分发。
    """

    def _session(self, tmp_path):
        import io
        from src.tui._input import Input
        from src.tui.ink.session import InkSession
        from src.tui.app.app import build_app_element

        r_fd, w_fd = os.pipe()
        input_ = Input(fd=r_fd, history_file=tmp_path / "history")
        session = InkSession(
            model=AppModel(),
            apply_cmd=None,
            build_tree=build_app_element,
            stream=io.StringIO(),
        )
        session.set_input(input_)
        session._render_frame()
        return session, input_, r_fd, w_fd

    def test_escape_cancels_popup(self, tmp_path):
        """弹窗打开时按 Esc → 取消（done=True, action=cancel, 默认选项）。"""
        import os as _os
        session, input_, r_fd, w_fd = self._session(tmp_path)
        try:
            m = session._model
            m.user_select = UserSelectState(
                visible=True, seq=1, title="测试",
                options=["A", "B", "C"], default_options=["A"],
            )
            session._render_frame()
            _os.write(w_fd, b"\x1b")
            assert input_.read_stdin_once() is True
            assert m.user_select.done is True
            assert m.user_select.action == "cancel"
            assert m.user_select.result == ["A"]
        finally:
            _os.close(w_fd)
            _os.close(r_fd)

    def test_escape_after_close_releases(self, tmp_path):
        """弹窗清理后 Esc 不再被组件消费（走旧中断路径，组件不误改状态）。"""
        import os as _os
        session, input_, r_fd, w_fd = self._session(tmp_path)
        try:
            m = session._model
            m.user_select = UserSelectState(visible=True, seq=1, title="测试",
                                            options=["A", "B"])
            session._render_frame()
            # 清理（visible=False）+ 重渲染 → router 释放
            m.user_select = UserSelectState()
            session._render_frame()
            _os.write(w_fd, b"\x1b")
            input_.read_stdin_once()
            # 组件未消费 → user_select 保持关闭（走旧中断路径）
            assert m.user_select.visible is False
            assert m.user_select.done is False
        finally:
            _os.close(w_fd)
            _os.close(r_fd)
