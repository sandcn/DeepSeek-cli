"""user_select 自定义输入回归测试（2026-10-21 用户需求）。

覆盖：
  - ``UserSelectState`` 新增字段默认值 / 终态 custom 写入；
  - 空闲超时（2026-10-22 用户需求：删除 ``timeout`` 参数，改成「不操作界面
    一分钟后才算超时」）——每次按键/鼠标操作重置 1 分钟倒计时、共享计时、
    已提交问题不刷新、固定 deadline 调用方不受影响；外部编辑器期间暂停；
  - 弹窗「其他（自定义输入…）」伪选项的完整交互（单选/多选/多问题）；
  - 输入模式 Esc/Tab 退出语义、空文本不提交、伪选项空格不可勾选；
  - ``MultiSelect`` 新增可选 props（onSelect/onChange/canToggle）零回归。
"""

from __future__ import annotations

import time
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


def _collect_styled_text(root) -> list:
    """收集 fiber 树中的普通 children 与 styled runs 文本（多行编辑区断言）。"""
    out: list = []
    stack = [root]
    while stack:
        fiber = stack.pop()
        while fiber is not None:
            props = fiber.props if isinstance(fiber.props, dict) else {}
            child = props.get("children")
            if isinstance(child, str) and child.strip():
                out.append(child)
            styled = props.get("styled")
            if isinstance(styled, list):
                for run in styled:
                    text = getattr(run, "text", "")
                    if isinstance(text, str) and text.strip():
                        out.append(text)
            if fiber.child is not None:
                stack.append(fiber.child)
            fiber = fiber.sibling
    return out


def _norm_options(options) -> list:
    """测试便捷：字符串 → ``{label}`` 对象（``options`` 严格只接受对象）。"""
    return [
        item if isinstance(item, dict) else {"label": str(item)}
        for item in (options or ["A", "B"])
    ]


def _state(title: str = "Q", options=None, **kwargs) -> UserSelectState:
    return UserSelectState(
        visible=True,
        seq=int(kwargs.pop("seq", 1)),
        title=title,
        options=_norm_options(options),
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
    # ★ 2026-10-22：``typing`` 仅表示「外部编辑器（Ctrl+G vim）运行中」——
    #   编辑器独占终端、界面收不到按键，轮询暂停计时并在返回后补回。
    st = UserSelectState(
        visible=True, options=[{"label": "A"}],
        allow_custom=True, deadline=100.0,
    )
    st.typing = True
    paused, expired = _poll_deadline(st, 0.0, 50.0)
    assert paused == 50.0 and expired is False
    assert st.deadline == 100.0  # 编辑器运行中 deadline 不动

    paused, expired = _poll_deadline(st, paused, 60.0)
    assert paused == 50.0 and expired is False

    st.typing = False
    paused, expired = _poll_deadline(st, paused, 70.0)
    assert paused == 0.0 and expired is False
    assert st.deadline == 120.0  # 暂停 20s 补回剩余时间

    paused, expired = _poll_deadline(st, paused, 120.0)
    assert expired is True


def test_poll_deadline_zero_deadline_never_expires() -> None:
    st = UserSelectState(
        visible=True, options=[{"label": "A"}],
        allow_custom=True, deadline=0.0,
    )
    st.typing = False
    paused, expired = _poll_deadline(st, 0.0, 10.0)
    assert paused == 0.0 and expired is False


def test_idle_deadline_expires_only_after_one_minute_idle() -> None:
    """★ 2026-10-22：连续 1 分钟无操作才超时；期间一次操作即续期。"""
    st = UserSelectState(
        visible=True, options=[{"label": "A"}],
        idle_timeout=60.0, deadline=60.0,
    )
    assert _poll_deadline(st, 0.0, 59.9) == (0.0, False)
    # 30s 时发生一次界面操作 → deadline 顺延到 90s。
    assert st.touch_activity(30.0) is True
    assert st.deadline == 90.0
    assert _poll_deadline(st, 0.0, 89.9) == (0.0, False)
    assert _poll_deadline(st, 0.0, 90.0) == (0.0, True)


def test_idle_timeout_constant_and_display_text() -> None:
    """★ 2026-10-22（用户需求）：空闲窗口 = 60s，展示文案「1 分钟」。"""
    from src.tools.user_select import _idle_timeout_text
    from src.core.user_select_state import IDLE_TIMEOUT_SECONDS

    assert IDLE_TIMEOUT_SECONDS == 60.0
    assert _idle_timeout_text() == "1 分钟"


def test_tool_schema_mentions_one_minute_idle_timeout() -> None:
    """工具 schema（模型可见提词）与常量一致：1 分钟无操作超时。"""
    from src.tools.user_select import UserSelectFunc

    description = UserSelectFunc.to_tool_schema()["function"]["description"]
    assert "1 分钟没有任何操作" in description
    assert "3 分钟" not in description


def test_touch_activity_ignores_zero_idle_and_done_state() -> None:
    """固定 deadline 调用方（idle_timeout=0）与已终态问题不被刷新。"""
    fixed = UserSelectState(
        visible=True, options=[{"label": "A"}], deadline=100.0,
    )
    assert fixed.touch_activity(10.0) is False
    assert fixed.deadline == 100.0

    finished = UserSelectState(
        visible=True, options=[{"label": "A"}],
        idle_timeout=60.0, deadline=100.0,
    )
    finished.try_set_final("confirmed", ["A"])
    assert finished.touch_activity(10.0) is False
    assert finished.deadline == 100.0


def test_tool_schema_has_no_timeout_param_and_ignores_legacy_timeout() -> None:
    """★ 2026-10-22：工具 schema/构造器删除 ``timeout``（历史调用被忽略）。"""
    from src.tools.user_select import UserSelectFunc

    props = UserSelectFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "timeout" not in props
    assert "default_options" in props
    func = UserSelectFunc.from_args(
        {"title": "Q", "options": [{"label": "A"}], "timeout": 5},
    )
    assert not hasattr(func, "timeout")
    assert func.title == "Q"


def test_tool_schema_has_header_param_and_state_carries_it() -> None:
    """★ 2026-10-22（TUI 与 Claude 对齐）：可选 ``header`` 短标签。"""
    from src.tools.user_select import UserSelectFunc

    props = UserSelectFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "header" in props
    assert "12" in props["header"]["description"]
    func = UserSelectFunc.from_args(
        {"title": "Q", "options": [{"label": "A"}], "header": "认证方式"},
    )
    assert func.header == "认证方式"
    # 缺省 header 为空串（渲染层回退 title 截断）。
    func2 = UserSelectFunc.from_args(
        {"title": "Q", "options": [{"label": "A"}]},
    )
    assert func2.header == ""


def test_state_header_defaults_empty() -> None:
    assert UserSelectState().header == ""


def test_options_schema_inlines_description_and_drops_legacy_list() -> None:
    """★ 2026-10-23（用户需求）：说明并入 options，删除 option_descriptions。"""
    from src.tools.user_select import UserSelectFunc

    props = UserSelectFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "option_descriptions" not in props
    options = props["options"]
    assert options["type"] == "array"
    item = options["items"]
    assert item["type"] == "object"
    assert item["required"] == ["label"]
    assert "label" in item["properties"]
    assert "description" in item["properties"]
    # description 说明中明确支持多行显示。
    assert "多行" in item["properties"]["description"]["description"]


def test_state_rejects_plain_string_options() -> None:
    """★ 2026-10-23：``options`` 严格只接受对象（纯字符串报错）。"""
    import pytest

    with pytest.raises(ValueError):
        UserSelectState(visible=True, options=["A"])
    with pytest.raises(ValueError):
        UserSelectState(visible=True, options=[{"description": "无标签"}])


def test_options_carry_multiline_description() -> None:
    st = UserSelectState(visible=True, options=[
        {"label": "A", "description": "第一行\n第二行"},
        {"label": "B"},
    ])
    assert st.options[0].label == "A"
    assert st.options[0].description == "第一行\n第二行"
    assert st.options[1].description == ""
    # 结果协议仍以 label 文本为准（说明不参与匹配）。
    st.try_set_final("confirmed", ["A"])
    assert st.result == ["A"]


def test_tool_init_and_defaults_match_labels_with_descriptions() -> None:
    """★ 2026-10-23：默认项按 label 匹配；selected 返回 label。"""
    from src.tools.user_select import UserSelectFunc

    func = UserSelectFunc(
        title="Q",
        options=[
            {"label": "A", "description": "说明甲\n第二行"},
            {"label": "B"},
        ],
        default_options=["B"],
    )
    assert [o.label for o in func.options] == ["A", "B"]
    assert func.options[0].description == "说明甲\n第二行"


def test_any_key_resets_idle_deadline_for_all_pending_questions() -> None:
    """★ 2026-10-22（共享计时）：任一 tab 的操作刷新全部未提交问题的倒计时。"""
    st1 = _state("Q1", options=["A", "B"], seq=1, idle_timeout=60.0,
                 deadline=time.monotonic() - 10)
    st2 = _state("Q2", options=["C", "D"], seq=2, idle_timeout=60.0,
                 deadline=time.monotonic() - 10)
    done = _state("Q3", options=["E"], seq=3, idle_timeout=60.0,
                  deadline=time.monotonic() - 10)
    done.try_set_final("confirmed", ["E"])
    model, element, root, press = _mount_popup([st1, st2, done])
    stale = done.deadline

    assert press("arrow_down") is True
    now = time.monotonic()
    assert st1.deadline >= now + 60 - 5
    assert st2.deadline >= now + 60 - 5  # 其它 tab 一并刷新
    assert done.deadline == stale  # 已提交问题不再刷新


def test_fixed_deadline_state_is_not_reset_by_input() -> None:
    """命令系统选择（idle_timeout=0）保持固定 deadline（零回归）。"""
    st = _state(deadline=time.monotonic() + 30)
    model, element, root, press = _mount_popup([st])
    before = st.deadline
    assert press("arrow_down") is True
    assert st.deadline == before


def test_mouse_activity_also_resets_idle_deadline() -> None:
    """★ 2026-10-22：鼠标操作（滚轮等）同样算「操作界面」。"""
    st = _state(idle_timeout=60.0, deadline=time.monotonic() - 5)
    model, element, root, press = _mount_popup([st])
    before = st.deadline
    press("mouse")
    assert st.deadline > before


# ══════════════════════════════════════════════════════════
# 弹窗交互
# ══════════════════════════════════════════════════════════


def test_render_contains_custom_entry_and_hint() -> None:
    st = _state()
    model = SimpleNamespace(
        user_selects=[st], user_select=st, bottom_view="user_select",
    )
    text = _plain(h(UserSelectPopup, {"model": model, "width": 80}))
    # ★ 2026-10-22（Claude 对齐）：自定义项为普通编号项（无 ✎ 图标）。
    assert "其他（自定义输入…）" in text
    assert "其他=自定义" in text


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
    # 导航到列表末尾「其他（自定义输入…）」行（两个普通选项）。
    assert press("arrow_down") is True
    assert press("arrow_down") is True
    assert st.selected == 2

    assert press("enter") is True
    # ★ 2026-10-23（用户需求：输入中定时器不要继续倒计时）：输入模式
    #   期间暂停空闲计时（typing=True，标题区不显示 ⏱ 倒计时）。
    assert st.typing is True

    assert press("char", "自") is True
    assert press("char", "定义") is True
    assert press("enter") is True

    assert st.done is True
    assert st.action == "confirmed"
    assert st.result == ["自定义"]
    assert st.custom == "自定义"
    assert st.typing is False


def test_escape_exits_custom_input_then_cancels() -> None:
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")
    press("char", "X")
    assert st.typing is True  # 输入模式暂停空闲计时

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
    assert st.typing is True  # 输入模式暂停空闲计时

    assert press("enter") is True
    assert st.done is False
    assert st.typing is True  # 空文本不提交，保持输入模式
    assert st.result == []
    # ★ 2026-10-23（用户需求：空 Enter 要有反馈）：提示「输入不能为空」。
    assert any("输入不能为空" in t for t in _collect_styled_text(root))


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
    assert st.typing is True  # 输入模式（暂停计时）
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
    assert s1.typing is True  # 输入模式（暂停计时）

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


def test_tab_switch_suspends_custom_input_preserving_draft() -> None:
    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    model, element, root, press = _mount_popup([s1, s2])

    press("arrow_down")
    press("enter")
    press("char", "X")
    assert s1.typing is True  # 输入模式暂停空闲计时

    assert press("tab") is True
    # ★ 2026-10-23：切 tab 只挂起，保留草稿并在后台暂停计时。
    assert s1.typing is True
    assert s1.custom_active is True
    assert s1.custom_draft == "X"
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
    assert s1.typing is True  # 输入模式（暂停计时）
    assert s1.answered is False
    assert s2.answered is False


def test_custom_row_has_no_description_like_claude() -> None:
    """★ 2026-10-22（Claude 对齐）：自定义项是普通编号项，无说明行。"""
    st = _state(options=[
        {"label": "A", "description": "说明一"},
        {"label": "B", "description": "说明二"},
    ])
    model = SimpleNamespace(
        user_selects=[st], user_select=st, bottom_view="user_select",
    )
    text = _plain(h(UserSelectPopup, {"model": model, "width": 80}))
    assert "其他（自定义输入…）" in text
    assert "输入不在选项中的答案" not in text
    lines = [line for line in text.splitlines() if line.strip()]
    idx = next(i for i, line in enumerate(lines) if "其他（自定义输入…）" in line)
    # 下一行不是说明行（是空行/提示行）。
    assert "输入不在选项中的答案" not in lines[idx + 1]


def test_tool_returns_explicit_empty_multi_submit(monkeypatch) -> None:
    """★ 2026-10-22：用户明确提交空选择时不得回退 default_options。

    实测发现（本次 Claude TUI 对齐）：修复前工具返回分支
    ``result if result else default_opts`` 把「多选未勾选提交」变成默认值，
    与提示「Enter 提交空」矛盾。
    """
    import asyncio
    import json
    import threading

    import src.tools.user_select as tool_mod

    model = SimpleNamespace(
        user_select=UserSelectState(), user_selects=[], bottom_view="",
    )

    class _UI:
        bottom_bar = None

        def get_model(self):
            return model

        def request_bottom_redraw(self):
            pass

        def get_input_component(self):
            return None

        def flush_input_router(self, timeout):
            pass

    monkeypatch.setattr(tool_mod, "_interactive_stdin", lambda: True)
    monkeypatch.setattr(tool_mod, "get_display_target", lambda: _UI())

    out: list = []

    def run() -> None:
        func = tool_mod.UserSelectFunc(
            title="Q",
            options=[{"label": "A"}, {"label": "B"}],
            multi_select=True,
        )
        out.append(asyncio.run(func.execute()))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(200):
        if model.user_selects:
            break
        time.sleep(0.02)
    assert model.user_selects
    # 显式提交空选择（多选未勾选）。
    model.user_selects[0].try_set_final("confirmed", [])
    thread.join(timeout=5)
    assert not thread.is_alive()
    payload = json.loads(out[0])
    assert payload["action"] == "confirmed"
    assert payload["selected"] == []
    # 用户主动提交（含空选择）算已作答；空选择语义由提词判为未授权。
    assert payload["answered"] is True


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
    paused_during_editor: list = []

    def fake_editor(text):
        calls.append(text)
        # ★ 2026-10-22：外部编辑器运行期间暂停空闲计时（typing=True）。
        paused_during_editor.append(st.typing)
        return "第一行\n第二行"

    monkeypatch.setattr(us_mod, "_run_external_editor", fake_editor)
    st = _state()
    model, element, root, press = _mount_popup([st])
    _enter_custom_for(st, press)
    press("char", "初始")

    assert press("ctrl_key", "\x07") is True
    assert calls == ["初始"]
    assert paused_during_editor == [True]
    assert st.typing is True  # 输入模式继续暂停空闲计时
    assert st.done is False

    # ★ 2026-10-23：多行编辑区按原文逐行显示（不再折叠为 ⏎）。
    texts = _collect_styled_text(root)
    assert any("第一行" in t for t in texts)
    assert any("第二行" in t for t in texts)
    assert not any("\u23ce" in t for t in texts)

    # 再次 Ctrl+G：编辑器应收到原始多行文本（不是折叠显示文本）。
    press("ctrl_key", "\x07")
    assert calls[-1] == "第一行\n第二行"
    assert paused_during_editor == [True, True]

    assert press("enter") is True
    assert st.result == ["第一行\n第二行"]
    assert st.custom == "第一行\n第二行"
    assert st.typing is False


def test_ctrl_g_editor_failure_keeps_text_and_shows_error(monkeypatch) -> None:
    import src.tui.app.user_select as us_mod

    paused: list = []

    def fake_editor(text):
        paused.append(st.typing)
        return None

    monkeypatch.setattr(us_mod, "_run_external_editor", fake_editor)
    st = _state()
    model, element, root, press = _mount_popup([st])
    _enter_custom_for(st, press)
    press("char", "X")

    assert press("ctrl_key", "\x07") is True
    assert paused == [True]  # 编辑器运行期间暂停空闲计时
    assert st.typing is True  # 仍处于输入模式，继续暂停计时
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
    assert s1.typing is True  # 输入模式（暂停计时）

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
    assert st.typing is True  # 输入模式（暂停计时）

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

    # ★ 2026-10-23（用户需求：输入完了要显示所有的输入（多行））：
    #   已提交终态按原文逐行显示（不再折叠为 ⏎）。
    monkeypatch.setattr(
        us_mod, "_run_external_editor", lambda text: text,
    )
    done_text = _plain(
        h(UserSelectPopup, {"model": model, "width": 80})
    )
    assert "A\u23ceB" not in done_text
    assert any(line.rstrip().endswith("A") for line in done_text.splitlines())
    assert any(line.strip().startswith("B") for line in done_text.splitlines())



# ══════════════════════════════════════════════════════════
# 2026-10-23 用户需求：空输入光标 / Shift+Enter 多行 / 输入中暂停计时
# ══════════════════════════════════════════════════════════


def test_text_input_empty_value_renders_cursor_block() -> None:
    """★ 空输入框也必须显示光标块（修复前直接返回 placeholder）。"""
    from src.tui.ink.widgets.interactive import TextInput

    raw = renderToString(
        h(TextInput, {"value": "", "placeholder": "输入自定义答案", "focus": True}),
        {"columns": 40},
    )
    assert "\x1b[48;2;0;128;128m" in raw
    assert "输入自定义答案" in raw

    # 未聚焦时不显示光标（保持原有语义）。
    raw_off = renderToString(
        h(TextInput, {"value": "", "placeholder": "输入自定义答案", "focus": False}),
        {"columns": 40},
    )
    assert "\x1b[48;2;0;128;128m" not in raw_off


def test_shift_enter_inserts_newline_and_enter_submits() -> None:
    """★ Shift+Enter 换行、Enter 提交，提交结果保留原始换行。"""
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")

    assert press("char", "A") is True
    assert press("enter", "", 2) is True  # Shift+Enter
    assert press("char", "B") is True
    assert st.done is False

    texts = _collect_styled_text(root)
    assert any("A" in t for t in texts)
    assert any("B" in t for t in texts)
    assert not any("\u23ce" in t for t in texts)

    assert press("enter") is True
    assert st.done is True
    assert st.result == ["A\nB"]
    assert st.custom == "A\nB"
    assert st.typing is False


def test_switch_tab_preserves_custom_draft_and_resumes() -> None:
    """★ 并发 tab：切走保留草稿，切回自动恢复编辑区与内容。"""
    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    model, element, root, press = _mount_popup([s1, s2])

    press("arrow_down")  # 自定义行（1 个普通选项）
    press("enter")
    for ch in "ABC":
        press("char", ch)
    assert s1.custom_draft == "ABC"

    assert press("tab") is True  # 切到 Q2
    assert s1.custom_draft == "ABC"
    assert s1.custom_active is True
    assert s1.typing is True  # 后台草稿暂停计时

    assert press("tab", "", 2) is True  # Shift+Tab 切回 Q1
    assert s1.typing is True
    texts = _collect_styled_text(root)
    assert any("ABC" in t for t in texts)

    press("char", "D")
    assert press("enter") is True
    assert s1.result == ["ABCD"]
    assert s1.custom == "ABCD"
    assert s1.custom_draft == "ABCD"


def test_reenter_custom_restores_draft_after_escape() -> None:
    """★ Esc 退出后再进入自定义输入，恢复原草稿而不是清空。"""
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")
    press("char", "X")
    press("char", "Y")
    assert press("escape") is True
    assert st.custom_active is False
    assert st.custom_draft == "XY"

    assert press("enter") is True  # 重新进入自定义输入
    texts = _collect_styled_text(root)
    assert any("XY" in t for t in texts)
    assert press("enter") is True
    assert st.result == ["XY"]


def test_custom_draft_rendered_as_description_when_not_editing() -> None:
    """★ 没在输入模式时，自定义草稿按选项说明样式显示。"""
    st = _state("Q", options=["A", "B"])
    st.custom_draft = "已输入草稿"
    st.custom_active = False
    model = SimpleNamespace(
        user_selects=[st], user_select=st, bottom_view="user_select",
    )
    text = _plain(h(UserSelectPopup, {"model": model, "width": 80}))
    assert "已输入草稿" in text

    # 选中自定义行时多行展开；未选中时折叠单行。
    st2 = _state("Q", options=["A", "B"])
    st2.custom_draft = "第一行\n第二行"
    st2.custom_active = False
    st2.selected = 2
    model2 = SimpleNamespace(
        user_selects=[st2], user_select=st2, bottom_view="user_select",
    )
    text2 = _plain(h(UserSelectPopup, {"model": model2, "width": 80}))
    assert "第一行" in text2
    assert "第二行" in text2

    st3 = _state("Q", options=["A", "B"])
    st3.custom_draft = "第一行\n第二行"
    st3.custom_active = False
    st3.selected = 0
    model3 = SimpleNamespace(
        user_selects=[st3], user_select=st3, bottom_view="user_select",
    )
    text3 = _plain(h(UserSelectPopup, {"model": model3, "width": 80}))
    assert "第一行 第二行" in text3


def test_custom_draft_description_appears_after_escape() -> None:
    """★ 按 Esc 退出编辑后，草稿以说明行显示（不丢失）。"""
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")
    for ch in "ABC":
        press("char", ch)
    assert press("escape") is True
    texts = _collect_texts(root)
    assert any("ABC" in t for t in texts)


def test_ctrl_o_inserts_newline_fallback() -> None:
    """★ 传统终端（mintty/Cygwin）不区分 Shift+Enter 时，Ctrl+O 换行。"""
    st = _state()
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")

    assert press("char", "A") is True
    assert press("ctrl_key", "\x0f") is True  # Ctrl+O
    assert press("char", "B") is True
    assert st.done is False
    assert press("enter") is True
    assert st.result == ["A\nB"]
    assert st.custom == "A\nB"


def test_input_mode_pauses_idle_timeout() -> None:
    """★ 输入模式期间暂停空闲计时（typing=True，标题不显示倒计时）。"""
    st = _state(
        idle_timeout=60.0, deadline=time.monotonic() + 30,
    )
    model, element, root, press = _mount_popup([st])
    press("arrow_down")
    press("arrow_down")
    press("enter")

    assert st.typing is True
    before = st.deadline
    paused, expired = _poll_deadline(st, 0.0, time.monotonic() + 3600)
    assert expired is False
    assert paused > 0
    assert st.deadline == before

    from src.tui.app.user_select import _build_header

    header_rows = _build_header(st, 0, 1, False, 0, False, 80)
    header_text = "\n".join(
        renderToString(row, {"columns": 80}) for row in header_rows
    )
    assert "\u23f1" not in header_text

    # 退出输入模式后恢复计时。
    assert press("escape") is True
    assert st.typing is False


def test_multiline_editor_renders_all_lines() -> None:
    """★ 多行编辑区逐行显示完整内容（不折叠、不截断）。"""
    from src.tui.app.user_select import _build_custom_editor_rows

    rows = _build_custom_editor_rows("A\nB\nC", 5, 30, max_rows=10)
    raw = "\n".join(
        renderToString(row, {"columns": 30}) for row in rows
    )
    plain = strip_ansi(raw)
    assert "A" in plain
    assert "B" in plain
    assert "C" in plain
    assert "\u23ce" not in plain
    assert "\x1b[48;2;0;128;128m" in raw  # 光标块仍在最后一行


def test_submit_page_renders_multiline_answer() -> None:
    """★ Submit 页的自定义多行答案逐行显示。"""
    from src.tui.app.user_select import _build_submit_page

    s1 = _state("Q1", options=["A"], seq=1)
    s2 = _state("Q2", options=["B"], seq=2)
    s1.mark_answered("confirmed", ["X\nY"], custom="X\nY")
    rows = _build_submit_page([s1, s2], 80, max_rows=40)
    text = "\n".join(
        strip_ansi(renderToString(row, {"columns": 80})) for row in rows
    )
    assert "X" in text
    assert "Y" in text
    assert "X\u23ceY" not in text


def test_non_interactive_fallback_marks_unanswered(monkeypatch) -> None:
    """非交互回退 answered=False：用户没答，不等于拒绝或未授权。"""
    import asyncio
    import json

    import src.tools.user_select as tool_mod

    monkeypatch.setattr(tool_mod, "_interactive_stdin", lambda: False)
    func = tool_mod.UserSelectFunc(
        title="Q",
        options=[{"label": "A"}, {"label": "B"}],
        default_options=["A"],
    )
    payload = json.loads(asyncio.run(func.execute()))
    assert payload == {
        "selected": ["A"],
        "action": "non_interactive",
        "answered": False,
    }


def test_error_fallback_marks_unanswered(monkeypatch) -> None:
    """ChatUI 未激活时 answered=False，且回退默认项不丢失。"""
    import asyncio
    import json

    import src.tools.user_select as tool_mod

    monkeypatch.setattr(tool_mod, "_interactive_stdin", lambda: True)
    monkeypatch.setattr(tool_mod, "get_display_target", lambda: None)
    func = tool_mod.UserSelectFunc(
        title="Q",
        options=[{"label": "A"}],
        default_options=["A"],
    )
    payload = json.loads(asyncio.run(func.execute()))
    assert payload["action"] == "error: ChatUI 未激活"
    assert payload["selected"] == ["A"]
    assert payload["answered"] is False


def test_timeout_fallback_marks_unanswered(monkeypatch) -> None:
    """空闲超时 answered=False；回退结果采用非空 default_options。"""
    import asyncio
    import json
    import threading

    import src.tools.user_select as tool_mod

    model = SimpleNamespace(
        user_select=UserSelectState(), user_selects=[], bottom_view="",
    )

    class _UI:
        bottom_bar = None

        def get_model(self):
            return model

        def request_bottom_redraw(self):
            pass

        def get_input_component(self):
            return None

        def flush_input_router(self, timeout):
            pass

    monkeypatch.setattr(tool_mod, "_interactive_stdin", lambda: True)
    monkeypatch.setattr(tool_mod, "get_display_target", lambda: _UI())
    # 把空闲窗口压到 0.05s，避免测试真的等 60 秒（只验证语义，不验证时长）。
    monkeypatch.setattr(tool_mod, "IDLE_TIMEOUT_SECONDS", 0.05)

    out: list = []

    def run() -> None:
        func = tool_mod.UserSelectFunc(
            title="Q",
            options=[{"label": "A"}, {"label": "B"}],
            default_options=["A"],
        )
        out.append(asyncio.run(func.execute()))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    payload = json.loads(out[0])
    assert payload["action"] == "timeout"
    assert payload["selected"] == ["A"]
    assert payload["answered"] is False
