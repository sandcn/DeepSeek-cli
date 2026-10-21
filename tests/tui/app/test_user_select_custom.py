"""user_select 自定义输入回归测试（2026-10-21 用户需求）。

覆盖：
  - ``UserSelectState`` 新增字段默认值 / 终态 custom 写入；
  - 工具轮询的自动超时暂停（输入中暂停、退出后补回剩余时间）；
  - 弹窗「✎ 其他（自定义输入…）」伪选项的完整交互（单选/多选/多问题）；
  - 输入模式 Esc/Tab 退出语义、空文本不提交、伪选项空格不可勾选；
  - ``MultiSelect`` 新增可选 props（onSelect/onChange/canToggle）零回归。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.user_select_state import UserSelectState
from src.tui.ink import h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.widgets.interactive import MultiSelect
from src.tui.app.user_select import UserSelectPopup
from src.tools.user_select import _poll_deadline


class _Event:
    """最小按键事件（与框架 KeyEvent 字段对齐）。"""

    def __init__(self, kind: str, char: str = "", modifier: int = 0) -> None:
        self.kind = kind
        self.char = char
        self.modifier = modifier
        self.kitty_bits = -1
        self.event_type = ""


def _find_fiber(root, type_name: str):
    stack = [root]
    while stack:
        fiber = stack.pop()
        while fiber is not None:
            if fiber.is_function and getattr(fiber.type, "__name__", "") == type_name:
                return fiber
            if fiber.child is not None:
                stack.append(fiber.child)
            fiber = fiber.sibling
    return None


def _plain(element, width: int = 80) -> str:
    return strip_ansi(renderToString(element, {"columns": width}))


def _mount(element, width: int = 80):
    """挂载元素并返回可复用挂载器（保持 fiber state / 最新 router）。"""
    reconciler = Reconciler(None)
    routers = []
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()

    def redraw() -> None:
        reconciler.render(root, element, width, 24)

    def press(kind: str, char: str = "", modifier: int = 0) -> bool:
        ok = routers[-1](_Event(kind, char, modifier))
        redraw()
        return ok

    redraw()
    return root, press


def _mount_popup(states, width: int = 80):
    model = SimpleNamespace(
        user_selects=list(states),
        user_select=states[0],
        bottom_view="user_select",
    )
    element = h(UserSelectPopup, {"model": model, "width": width})
    root, press = _mount(element, width)
    return model, element, root, press


def _state(title: str = "Q", options=None, **kwargs) -> UserSelectState:
    return UserSelectState(
        visible=True,
        seq=int(kwargs.pop("seq", 1)),
        title=title,
        options=list(options or ["A", "B"]),
        selected=0,
        allow_custom=bool(kwargs.pop("allow_custom", True)),
        **kwargs,
    )


# ══════════════════════════════════════════════════════════
# 状态 / 超时
# ══════════════════════════════════════════════════════════


def test_state_custom_fields_default_and_final_write() -> None:
    st = UserSelectState()
    assert st.allow_custom is False
    assert st.typing is False
    assert st.custom == ""

    assert st.try_set_final("confirmed", ["X"], custom="X") is True
    assert st.done is True
    assert st.custom == "X"

    st2 = UserSelectState()
    assert st2.mark_answered("confirmed", ["Y"], custom="Y") is True
    assert st2.answered is True and st2.custom == "Y"
    # 普通回答（不传 custom）清空此前的自定义标记。
    assert st2.mark_answered("confirmed", ["Y"]) is True
    assert st2.custom == ""


def test_poll_deadline_pauses_while_typing() -> None:
    st = UserSelectState(visible=True, options=["A"], allow_custom=True, deadline=100.0)
    st.typing = True
    paused, expired = _poll_deadline(st, 0.0, 50.0)
    assert paused == 50.0 and expired is False
    assert st.deadline == 100.0  # 输入中 deadline 不动

    paused, expired = _poll_deadline(st, paused, 60.0)
    assert paused == 50.0 and expired is False

    st.typing = False
    paused, expired = _poll_deadline(st, paused, 70.0)
    assert paused == 0.0 and expired is False
    assert st.deadline == 120.0  # 暂停 20s 补回剩余时间

    paused, expired = _poll_deadline(st, paused, 120.0)
    assert expired is True


def test_poll_deadline_zero_deadline_never_expires() -> None:
    st = UserSelectState(visible=True, options=["A"], allow_custom=True, deadline=0.0)
    st.typing = False
    paused, expired = _poll_deadline(st, 0.0, 10.0)
    assert paused == 0.0 and expired is False


# ══════════════════════════════════════════════════════════
# 弹窗交互
# ══════════════════════════════════════════════════════════


def test_render_contains_custom_entry_and_hint() -> None:
    st = _state()
    model = SimpleNamespace(
        user_selects=[st], user_select=st, bottom_view="user_select",
    )
    text = _plain(h(UserSelectPopup, {"model": model, "width": 80}))
    assert "\u270e 其他（自定义输入…）" in text
    assert "\u270e 其他=自定义" in text


def test_disabled_custom_not_rendered() -> None:
    st = _state(allow_custom=False)
    model = SimpleNamespace(
        user_selects=[st], user_select=st, bottom_view="user_select",
    )
    text = _plain(h(UserSelectPopup, {"model": model, "width": 80}))
    assert "\u270e 其他" not in text


def test_single_select_custom_input_and_submit() -> None:
    st = _state()
    model, element, root, press = _mount_popup([st])
    # 导航到列表末尾「✎ 其他」行（两个普通选项）。
    assert press("arrow_down") is True
    assert press("arrow_down") is True
    assert st.selected == 2

    assert press("enter") is True
    assert st.typing is True
    assert _find_fiber(root, "TextInput") is not None

    assert press("char", "自") is True
    assert press("char", "定义") is True
    assert press("enter") is True

    assert st.done is True
    assert st.action == "confirmed"
    assert st.result == ["自定义"]
    assert st.custom == "自定义"
    assert st.typing is False
    # 输入行退出后 TextInput 从树中移除。
    assert _find_fiber(root, "TextInput") is None


def test_escape_exits_custom_input_then_cancels() -> None:
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")
    press("char", "X")
    assert st.typing is True

    assert press("escape") is True
    assert st.typing is False
    assert st.done is False

    # 再次 Esc：取消问题（默认选项为空）。
    assert press("escape") is True
    assert st.done is True
    assert st.action == "cancel"
    assert st.result == []


def test_empty_custom_input_is_not_submitted() -> None:
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")
    assert st.typing is True

    assert press("enter") is True
    assert st.done is False
    assert st.typing is True  # 保持输入模式
    assert st.result == []


def test_multi_select_custom_appends_checked_values() -> None:
    st = _state(multi_select=True)
    model, element, root, press = _mount_popup([st])
    # 勾选 A。
    assert press("char", " ") is True
    assert st.checked == [0]
    # 导航到自定义行。
    press("arrow_down")
    press("arrow_down")
    # 伪选项行空格不勾选（仅消费）。
    assert press("char", " ") is True
    assert st.checked == [0]

    assert press("enter") is True
    assert st.typing is True
    press("char", "Z")
    assert press("enter") is True

    assert st.done is True
    assert st.result == ["A", "Z"]
    assert st.custom == "Z"


def test_multi_question_custom_answer_advances() -> None:
    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    model, element, root, press = _mount_popup([s1, s2])

    press("arrow_down")  # 自定义行（1 个普通选项）
    press("enter")
    assert s1.typing is True

    press("char", "X")
    assert press("enter") is True

    assert s1.answered is True
    assert s1.result == ["X"]
    assert s1.custom == "X"
    assert s1.typing is False
    assert s2.answered is False

    # 焦点已自动推进到 Q2：Enter 提交其默认选项 B。
    assert press("enter") is True
    assert s2.answered is True
    assert s2.result == ["B"]


def test_tab_switch_exits_custom_input() -> None:
    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    model, element, root, press = _mount_popup([s1, s2])

    press("arrow_down")
    press("enter")
    press("char", "X")
    assert s1.typing is True

    assert press("tab") is True
    assert s1.typing is False
    assert s1.done is False
    assert s1.answered is False

    # 已切到 Q2（非输入模式）：Enter 直接提交 Q2 默认项。
    assert press("enter") is True
    assert s2.answered is True
    assert s2.result == ["B"]


def test_arrow_in_input_mode_does_not_switch_tab() -> None:
    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    model, element, root, press = _mount_popup([s1, s2])

    press("arrow_down")
    press("enter")
    assert press("arrow_right") is True
    assert s1.typing is True
    assert s1.answered is False
    assert s2.answered is False


def test_split_description_mode_custom_row() -> None:
    st = _state(option_descriptions=["说明一", "说明二"])
    model = SimpleNamespace(
        user_selects=[st], user_select=st, bottom_view="user_select",
    )
    text = _plain(h(UserSelectPopup, {"model": model, "width": 80}))
    assert "\u270e 其他（自定义输入…）" in text
    assert "输入不在选项中的答案" in text


# ══════════════════════════════════════════════════════════
# MultiSelect 控件新增 props（零回归）
# ══════════════════════════════════════════════════════════


def test_multiselect_on_select_on_change_and_can_toggle() -> None:
    selected_items = []
    changes = []

    def can_toggle(idx, item):
        return idx != 2  # 第 3 项为伪选项，不可勾选

    element = h(MultiSelect, {
        "items": ["A", "B", "C"],
        "canToggle": can_toggle,
        "onSelect": selected_items.append,
        "onChange": changes.append,
    })
    root, press = _mount(element)

    assert press("char", " ") is True
    assert changes[-1] == ["A"]

    press("arrow_down")
    press("arrow_down")
    assert press("char", " ") is True
    # 不可勾选：选中集合未变，也不触发 onChange。
    assert changes[-1] == ["A"]

    assert press("enter") is True
    assert selected_items[-1]["value"] == "C"


def test_multiselect_legacy_on_submit_still_works() -> None:
    submitted = []
    element = h(MultiSelect, {
        "items": ["A", "B"],
        "onSubmit": submitted.append,
    })
    root, press = _mount(element)
    assert press("char", " ") is True
    assert press("enter") is True
    assert submitted[-1] == ["A"]


# ══════════════════════════════════════════════════════════
# Ctrl+G 用 vim 输入（2026-10-21 用户需求）
# ══════════════════════════════════════════════════════════


def _collect_texts(root) -> list:
    """收集 fiber 树中的文本 children（用于显示断言）。"""
    out: list = []
    stack = [root]
    while stack:
        fiber = stack.pop()
        while fiber is not None:
            if isinstance(fiber.props, dict):
                child = fiber.props.get("children")
                if isinstance(child, str) and child.strip():
                    out.append(child)
            if fiber.child is not None:
                stack.append(fiber.child)
            fiber = fiber.sibling
    return out


def _enter_custom_for(st, press) -> None:
    """导航到列表末尾伪选项并 Enter 进入输入模式（2 个普通选项）。"""
    press("arrow_down")
    press("arrow_down")
    press("enter")


def test_ctrl_g_editor_fills_multiline_and_enter_submits(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    calls: list = []

    def fake_editor(text):
        calls.append(text)
        return "第一行\n第二行"

    monkeypatch.setattr(us_mod, "_run_external_editor", fake_editor)
    st = _state()
    model, element, root, press = _mount_popup([st])
    _enter_custom_for(st, press)
    press("char", "初始")

    assert press("ctrl_key", "\x07") is True
    assert calls == ["初始"]
    assert st.typing is True
    assert st.done is False

    # 输入行显示单行折叠（⏎），原始文本保留换行。
    text_input = _find_fiber(root, "TextInput")
    assert text_input is not None
    assert text_input.props.get("value") == "第一行\u23ce第二行"

    # 再次 Ctrl+G：编辑器应收到原始多行文本（不是折叠显示文本）。
    press("ctrl_key", "\x07")
    assert calls[-1] == "第一行\n第二行"

    assert press("enter") is True
    assert st.result == ["第一行\n第二行"]
    assert st.custom == "第一行\n第二行"
    assert st.typing is False


def test_ctrl_g_editor_failure_keeps_text_and_shows_error(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    monkeypatch.setattr(us_mod, "_run_external_editor", lambda text: None)
    st = _state()
    model, element, root, press = _mount_popup([st])
    _enter_custom_for(st, press)
    press("char", "X")

    assert press("ctrl_key", "\x07") is True
    assert st.typing is True
    assert st.done is False

    texts = _collect_texts(root)
    assert any("编辑器不可用" in t for t in texts)

    # 原文本保留，Enter 提交的仍是原文本。
    assert press("enter") is True
    assert st.result == ["X"]
    assert st.custom == "X"


def test_ctrl_g_outside_input_mode_is_ignored(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    calls: list = []
    monkeypatch.setattr(
        us_mod, "_run_external_editor",
        lambda text: calls.append(text) or text,
    )
    st = _state()
    model, element, root, press = _mount_popup([st])

    # 选项界面（未进入自定义输入）Ctrl+G 不触发编辑器。
    assert press("ctrl_key", "\x07") is True  # 被弹窗 consumeAll 消费
    assert calls == []
    assert st.typing is False
    assert st.done is False


def test_ctrl_g_multiline_multi_question_only_current(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    monkeypatch.setattr(
        us_mod, "_run_external_editor", lambda text: "第一行\n第二行",
    )
    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    model, element, root, press = _mount_popup([s1, s2])

    press("arrow_down")  # 自定义行（1 个普通选项）
    press("enter")
    assert s1.typing is True

    press("ctrl_key", "\x07")
    press("enter")
    assert s1.result == ["第一行\n第二行"]
    assert s1.custom == "第一行\n第二行"
    assert s2.answered is False
    assert s2.custom == ""


def test_ctrl_g_preserves_multi_select_checked(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    monkeypatch.setattr(
        us_mod, "_run_external_editor", lambda text: "Z\nW",
    )
    st = _state(multi_select=True)
    model, element, root, press = _mount_popup([st])

    # 勾选 A。
    assert press("char", " ") is True
    assert st.checked == [0]
    # 导航到自定义行并进入输入。
    press("arrow_down")
    press("arrow_down")
    press("enter")
    assert st.typing is True

    press("ctrl_key", "\x07")
    assert press("enter") is True

    # 勾选状态保持不变，自定义多行文本追加在勾选值之后。
    assert st.checked == [0]
    assert st.result == ["A", "Z\nW"]
    assert st.custom == "Z\nW"


def test_ctrl_g_hint_and_done_result_render_newline_mark(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    monkeypatch.setattr(
        us_mod, "_run_external_editor", lambda text: "A\nB",
    )
    st = _state()
    model, element, root, press = _mount_popup([st])
    _enter_custom_for(st, press)

    texts = _collect_texts(root)
    assert any("Ctrl+G vim 编辑" in t for t in texts)

    press("ctrl_key", "\x07")
    press("enter")
    assert st.done is True

    # 已提交终态只读行折叠显示换行。
    monkeypatch.setattr(
        us_mod, "_run_external_editor", lambda text: text,
    )
    done_text = _plain(
        h(UserSelectPopup, {"model": model, "width": 80})
    )
    assert "A\u23ceB" in done_text

