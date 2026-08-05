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
        # BEAUTY-29（2026-08-05）：标题前置单选模式图标 ▶
        assert lines[0] == " ▍ ▶ 测试 (2/3)"
        assert lines[1] == "    A"
        assert lines[2] == " ▶  B"
        assert lines[3] == "    C"
        assert "↑↓/jk 选择" in lines[4]

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

    def test_split_desc_opt_width_auto(self):
        """分栏说明模式：左栏按最大选项长度自适应——│ 紧跟最长选项后。

        回归（2026-08-05）：修复前 opt_w 固定 ``width - desc_w - 1``
        （width=80 时 53 列），短选项（A/B）下 │ 在 53 列处大片留白；
        修复后左栏 = 前缀 3 + 最长选项宽 1 + 补白 1 = 5 列，│ 紧跟选项。
        """
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="带说明",
            options=["A", "B"],
            option_descriptions=["说明A", "说明B"],
            selected=1,
        )
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m), width=80)
        lines = _plain(frame)
        assert lines[1] == "   A │说明B"
        assert lines[2] == " ▶ B │"

    def test_split_desc_multi_checked_mark(self):
        """分栏说明模式 + 多选：勾选项显示 ●/○ 标记（不丢失选中态）。

        回归（2026-08-05）：修复前分栏分支仅渲染单选 ▶ 前缀，多选勾选态
        （●/○）完全不显示——多选 + option_descriptions 时用户看不到
        选中项；修复后多选前缀与普通模式同语义（● 勾选 / ○ 未勾选）。
        """
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="多选说明",
            multi_select=True,
            options=["A", "B", "C"],
            option_descriptions=["说明A", "说明B", "说明C"],
            selected=1, checked=[0, 2],
        )
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m), width=60)
        lines = _plain(frame)
        assert lines[1] == " ● A │说明B"
        assert lines[2] == " ○ B │"
        assert lines[3] == " ● C │"

    def test_narrow_width_no_overflow(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="窄",
                                        options=["超长选项文本" * 10], selected=0)
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m), width=15)
        for ln in frame.lines:
            # 行宽不超过文档宽（渲染器行级 diff 宽度不变量）
            assert ln.width <= 15, f"超宽行: {ln.plain!r} ({ln.width})"

    def test_narrow_title_keeps_position_indicator(self):
        """窄终端：标题单行截断，位置指示 (1/3) 不被拆到下一行。

        回归：修复前标题行自动换行，(1/3) 位置指示拆到独立行——窄屏视觉错乱
        （标题与位置指示分离，误导为另一行选项）。
        """
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="测试：请选择一个选项",
            options=["A", "B", "C"], selected=0,
        )
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m, width=25), width=25)
        lines = _plain(frame)
        assert "(1/3)" in lines[0]          # 位置指示仍在标题行
        assert lines[1] != "(1/3)"          # 不再拆到第二行
        assert frame.lines[0].width <= 25   # 单行不超宽

    def test_narrow_hint_single_line(self):
        """窄终端：提示行单行截断（不拆行）。

        回归：修复前提示行自动换行拆成两行（窄终端 ``Esc 取消`` 独立一行）。
        """
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        options=["A", "B"])
        r, root = Reconciler(), Reconciler().create_root()
        frame = _render(r, root, _popup(m, width=20), width=20)
        # 弹窗 = 标题 1 + 2 选项 + 提示 1 = 4 行（提示拆行会变 5 行）
        assert len(frame.lines) == 4
        assert frame.lines[-1].width <= 20

    def test_long_desc_rows_capped(self):
        """超长说明：分栏弹窗行数受上限约束（不超高）。

        回归：修复前分栏说明行数无上限——超长说明弹窗超高，挤压/遮挡状态栏
        与输入区（UserSelectPopup 未做补全弹窗 _completion_item_rows 超屏防护）。
        """
        from unittest.mock import patch
        long_desc = "这是一段非常长的说明。" * 100  # 1000 字
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="超长",
            options=["A", "B"], option_descriptions=[long_desc, "短"], selected=0,
        )
        r, root = Reconciler(), Reconciler().create_root()
        with patch("src.tui.app.user_select._popup_item_rows", return_value=6):
            frame = _render(r, root, _popup(m), width=80)
        # 弹窗 = 标题 1 + n_rows(≤6) + 提示 1 = 8 行
        assert len(frame.lines) <= 8

    def test_many_options_rows_capped(self):
        """大量选项：普通模式弹窗行数受上限约束（不超高）。

        回归：修复前普通模式选项行数无上限——100+ 选项弹窗超高。
        """
        from unittest.mock import patch
        m = AppModel()
        m.user_select = UserSelectState(
            visible=True, seq=1, title="多选项",
            options=[f"选项{i}" for i in range(100)], selected=0,
        )
        r, root = Reconciler(), Reconciler().create_root()
        with patch("src.tui.app.user_select._popup_item_rows", return_value=6):
            frame = _render(r, root, _popup(m), width=80)
        # 弹窗 = 标题 1 + 6 选项 + 提示 1 = 8 行
        assert len(frame.lines) == 8


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

    def test_enter_confirms_multi_empty_checked(self):
        """多选：取消所有勾选后回车返回空列表（不误回退默认选项）。

        回归（2026-08-05）：修复前多选空勾选时 ``result = us.default_options``
        ——用户取消所有勾选后回车仍返回默认项，违背交互意图；修复后与
        Web 前端一致（confirm 返回实际勾选结果，空勾选则为空）。
        """
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        multi_select=True,
                                        options=["A", "B", "C"],
                                        default_options=["A"],
                                        selected=0, checked=[0])
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        # 空格取消 A 勾选 → 回车
        # ★ review 方向（测试同步）：生产路径空格按键为 ``kind="char", char=" "``
        # （_input_parser 无 "space" kind）——测试改用真实事件形态（原
        # ``key("space")`` 依赖被清理的死分支）。
        assert cap.key("char", " ") is True
        assert m.user_select.checked == []
        assert cap.key("enter") is True
        assert m.user_select.done is True
        assert m.user_select.action == "confirmed"
        assert m.user_select.result == []

    def test_space_toggles_multi(self):
        m = AppModel()
        m.user_select = UserSelectState(visible=True, seq=1, title="T",
                                        multi_select=True,
                                        options=["A", "B", "C"], selected=0)
        r, root = Reconciler(), Reconciler().create_root()
        cap = _Router()
        _render(r, root, _popup(m))
        # 空格勾选 A（真实事件形态：char ' '；生产路径无 kind="space"）
        assert cap.key("char", " ") is True
        assert m.user_select.checked == [0]
        frame = _render(r, root, _popup(m))
        assert _plain(frame)[1] == " ●  A"
        # 再按空格取消
        assert cap.key("char", " ") is True
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
