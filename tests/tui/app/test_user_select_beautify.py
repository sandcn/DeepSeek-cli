"""user_select TUI 美化 / 操作体验回归测试（2026-10-21 用户需求）。

覆盖：
  - 标题元信息（位置 / 多选已选数 / 超时倒计时 / 已完成）；
  - Claude 对齐布局（header chip + 独立问题行 / ❯ 指针 / 说明在选项下方 /
    取消分栏表头与整行分割线 / 多选 [x] [ ] 标记）；
  - 数字键快选（单选立即确认、多选切换勾选并移动光标、输入模式放行）；
  - Home/End/PgUp/PgDn 导航（仅模态弹窗 consumeAll 启用）；
  - 分栏说明每行显示各自说明 + 选中项完整说明块；
  - Submit 页未回答项显示默认值、已完成结果行；
  - 窄屏/大量并发问题下每行显示宽度 <= 终端宽（不换行溢出）；
  - 既有文案契约（`✎ 其他=自定义`、`输入不在选项中的答案`、`A⏎B`）。
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace

from src.core.user_select_state import UserSelectState
from src.tui._width import wcswidth_simple
from src.tui.ink import Column, h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.reconciler import Reconciler
from src.tui.app.user_select import UserSelectPopup


class _Event:
    """最小按键事件（与框架 KeyEvent 字段对齐）。"""

    def __init__(self, kind: str, char: str = "", modifier: int = 0) -> None:
        self.kind = kind
        self.char = char
        self.modifier = modifier
        self.kitty_bits = -1
        self.event_type = ""


def _plain(element, width: int = 80) -> str:
    return strip_ansi(renderToString(element, {"columns": width}))


def _mount(element, width: int = 80):
    """挂载元素并返回 ``(root, press, snapshot)``（保持 fiber state）。"""
    from src.tui.ink import components as _components

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

    def snapshot() -> str:
        frame = _components.render_frame(root, width)
        return strip_ansi("\n".join(line.render() for line in frame.lines))

    redraw()
    return root, press, snapshot


def _mount_popup(states, width: int = 80):
    model = SimpleNamespace(
        user_selects=list(states),
        user_select=states[0],
        bottom_view="user_select",
    )
    element = h(UserSelectPopup, {"model": model, "width": width})
    root, press, snapshot = _mount(element, width)
    return model, element, root, press, snapshot


def _frame_lines(root, width: int = 80):
    """取挂载 fiber 树的渲染行（保留样式，用于断言高亮背景）。"""
    from src.tui.ink import components as _components

    return _components.render_frame(root, width).lines


def _state(title: str = "Q", options=None, **kwargs) -> UserSelectState:
    return UserSelectState(
        visible=True,
        seq=int(kwargs.pop("seq", 1)),
        title=title,
        options=list(options or ["A", "B"]),
        selected=int(kwargs.pop("selected", 0)),
        allow_custom=bool(kwargs.pop("allow_custom", True)),
        **kwargs,
    )


def _render(states, width: int = 80) -> str:
    model = SimpleNamespace(
        user_selects=list(states), user_select=states[0], bottom_view="user_select",
    )
    return _plain(h(UserSelectPopup, {"model": model, "width": width}), width)


# ══════════════════════════════════════════════════════════
# 标题元信息
# ══════════════════════════════════════════════════════════


def test_header_shows_position_checked_and_countdown() -> None:
    st = _state(
        "优先级", ["A", "B"], multi_select=True,
        deadline=time.monotonic() + 42,
    )
    text = _render([st], width=100)
    assert "(1/2)" in text
    assert "已选 0/2" in text
    assert re.search(r"⏱ \d+s", text)


def test_header_hides_countdown_while_typing() -> None:
    st = _state("Q", ["A"], deadline=time.monotonic() + 42)
    st.typing = True
    text = _render([st], width=100)
    assert "⏱" not in text


def test_done_header_and_result_row() -> None:
    st = _state("Q", ["A"], allow_custom=False)
    st.try_set_final("confirmed", ["A"])
    text = _render([st], width=80)
    assert "✓ 已确认" in text
    assert "✓ 已选择: A" in text


# ══════════════════════════════════════════════════════════
# 数字键快选
# ══════════════════════════════════════════════════════════


def test_number_key_single_select_confirms() -> None:
    st = _state("Q", ["A", "B", "C"], allow_custom=False)
    model, element, root, press, snapshot = _mount_popup([st])
    assert press("char", "2") is True
    assert st.done is True
    assert st.action == "confirmed"
    assert st.result == ["B"]
    assert st.selected == 1


def test_number_key_out_of_range_is_ignored() -> None:
    st = _state("Q", ["A", "B"], allow_custom=False)
    model, element, root, press, snapshot = _mount_popup([st])
    # 越界数字由控件 consumeAll 吞掉（返回 True），但不得触发选择/提交。
    press("char", "9")
    assert st.done is False
    assert st.selected == 0


def test_number_key_multi_toggles_and_moves_cursor() -> None:
    st = _state("Q", ["A", "B", "C"], multi_select=True, allow_custom=False)
    model, element, root, press, snapshot = _mount_popup([st])
    assert press("char", "2") is True
    assert st.checked == [1]
    assert st.selected == 1
    assert st.done is False
    # 光标/高亮跟随到第 2 项（renderItem 选中行背景 237）。
    lines = _frame_lines(root, 80)
    row_b = next(line for line in lines if "B" in line.render())
    assert any(
        run.style is not None and run.style.bg == 237 for run in row_b.runs
    )
    # 再按一次取消勾选。
    assert press("char", "2") is True
    assert st.checked == []


def test_number_key_in_custom_input_is_text() -> None:
    st = _state("Q", ["A"], allow_custom=True)
    model, element, root, press, snapshot = _mount_popup([st])
    # 导航到「✎ 其他」并进入输入模式。
    assert press("arrow_down") is True
    assert press("enter") is True
    # ★ 2026-10-22：输入模式不再暂停计时（typing 仅外部编辑器期间为 True）。
    assert st.typing is False
    assert press("char", "1") is True
    assert press("char", "2") is True
    assert st.done is False
    assert st.result == []
    assert st.selected == 1  # 光标仍在伪选项行，未被数字键改成第 1 项
    assert press("enter") is True
    assert st.result == ["12"]
    assert st.custom == "12"


def test_number_key_multi_question_advances() -> None:
    s1 = _state("Q1", ["A"], seq=1, allow_custom=False)
    s2 = _state("Q2", ["B"], seq=2, allow_custom=False)
    model, element, root, press, snapshot = _mount_popup([s1, s2])
    assert press("char", "1") is True
    assert s1.answered is True and s1.result == ["A"]
    assert s2.answered is False
    assert press("char", "1") is True
    assert s2.answered is True and s2.result == ["B"]


# ══════════════════════════════════════════════════════════
# Home/End/PgUp/PgDn
# ══════════════════════════════════════════════════════════


def test_home_end_and_page_navigation_modal() -> None:
    st = _state("Q", [f"O{i:02d}" for i in range(30)], allow_custom=False)
    model, element, root, press, snapshot = _mount_popup([st])
    assert press("end") is True
    assert st.selected == 29
    assert press("home") is True
    assert st.selected == 0
    assert press("page_down") is True
    assert 0 < st.selected < 29
    assert press("page_up") is True
    assert st.selected == 0


# ══════════════════════════════════════════════════════════
# 分栏说明 / Submit 页
# ══════════════════════════════════════════════════════════


def test_descriptions_below_each_option_and_selected_expanded() -> None:
    long_desc = "很长的说明文本" * 12
    st = _state(
        "Q", ["A", "B", "C"],
        option_descriptions=["说明甲", "说明乙", long_desc],
        allow_custom=True,
    )
    st.selected = 2
    text = _render([st], width=80)
    assert "说明甲" in text
    assert "说明乙" in text
    # 每个选项自己的说明都在右栏出现（而非只有选中项）。
    assert text.count("说明甲") >= 1 and text.count("说明乙") >= 1
    # 选中的长说明在列表下方完整展开（▸ 块）。
    assert "▸" in text
    assert "输入不在选项中的答案" in text


def test_submit_page_shows_defaults_for_unanswered() -> None:
    s1 = _state("Q1", ["A"], seq=1, allow_custom=False)
    s1.mark_answered("confirmed", ["A"])
    s2 = _state("Q2", ["B"], seq=2, allow_custom=False, default_options=["B"])
    model, element, root, press, snapshot = _mount_popup([s1, s2])
    # Q1 → Q2 → Submit（Tab 两次）
    assert press("tab") is True
    assert press("tab") is True
    text = snapshot()
    assert "提交全部答案" in text
    assert "✓ A" in text
    assert "未回答（默认：B）" in text
    # Submit 页 Enter：已回答按结果、未回答按默认值统一提交。
    assert press("enter") is True
    assert s1.done is True and s1.result == ["A"]
    assert s2.done is True and s2.result == ["B"]


def test_multi_zero_checked_hint() -> None:
    st = _state("Q", ["A", "B"], multi_select=True, allow_custom=False)
    text = _render([st], width=80)
    assert "未勾选" in text
    assert "提交空" in text


def test_done_result_folds_newline() -> None:
    st = _state("Q", ["A"], allow_custom=False)
    st.try_set_final("confirmed", ["A\nB"])
    text = _render([st], width=80)
    assert "A⏎B" in text


# ══════════════════════════════════════════════════════════
# 宽度不变量
# ══════════════════════════════════════════════════════════


def _assert_width(text: str, width: int) -> None:
    for line in text.split("\n"):
        assert wcswidth_simple(line) <= width, (
            f"行宽超限 width={width}: {wcswidth_simple(line)} {line!r}"
        )


def test_width_invariant_single_split() -> None:
    st = _state(
        "一个很长很长的问题标题" * 2, ["Python", "JavaScript", "Rust"],
        option_descriptions=["解释型动态语言", "前端脚本", "系统级语言"],
        allow_custom=True,
    )
    for width in (30, 40, 60, 80, 120):
        _assert_width(_render([st], width=width), width)


def test_width_invariant_multi_and_many_tabs() -> None:
    st = _state("多选", [f"选项{i}" for i in range(8)], multi_select=True, allow_custom=True)
    states = [
        _state("很长的并发问题标题" * 2, ["A"], seq=i + 1, allow_custom=False)
        for i in range(10)
    ]
    for width in (30, 40, 60, 80, 120):
        _assert_width(_render([st], width=width), width)
        _assert_width(_render(states, width=width), width)


def test_width_invariant_input_mode() -> None:
    st = _state("Q", ["A", "B"], allow_custom=True)
    model, element, root, press, snapshot = _mount_popup([st], width=40)
    press("arrow_down")
    press("arrow_down")
    press("enter")
    _assert_width(snapshot(), 40)

    # 正常宽度输入模式：主提示包含 Ctrl+G 说明。
    st2 = _state("Q", ["A", "B"], allow_custom=True)
    m2, e2, r2, press2, snapshot2 = _mount_popup([st2], width=80)
    press2("arrow_down")
    press2("arrow_down")
    press2("enter")
    text = snapshot2()
    assert "Ctrl+G vim 编辑" in text
    assert "Ctrl+U 清空" in text
    assert "Ctrl+W 删词" in text
    _assert_width(text, 80)


# ══════════════════════════════════════════════════════════
# 共享控件：consumeAll 门控（零回归）
# ══════════════════════════════════════════════════════════


def test_select_input_home_only_in_consume_all_mode() -> None:
    from src.tui.ink.widgets.interactive import SelectInput

    items = [f"O{i}" for i in range(20)]
    # 非弹窗模式（consumeAll=False）：Home 放行（输入区移动光标），不吞事件。
    element = h(SelectInput, {"items": items, "consumeAll": False})
    root, press, snapshot = _mount(element)
    assert press("home") is False
    # 弹窗模式：Home 被消费并移动选中项。
    element = h(SelectInput, {"items": items, "consumeAll": True})
    root, press, snapshot = _mount(element)
    assert press("end") is True
    assert press("home") is True


# ══════════════════════════════════════════════════════════
# 继续优化：长列表指示 / 批量键 / 输入快捷键 / Submit 分页
# ══════════════════════════════════════════════════════════


def test_long_list_scroll_indicator(monkeypatch) -> None:
    from src.tui.app import user_select as us_mod

    monkeypatch.setattr(us_mod, "_popup_item_rows", lambda: 10)
    st = _state("长列表", [f"O{i:02d}" for i in range(30)], allow_custom=False)
    text = _render([st], width=80)
    assert "▼ 下方" in text
    st.selected = 20
    text = _render([st], width=80)
    assert "▲ 上方" in text
    assert "▼ 下方" in text
    _assert_width(text, 80)


def test_batch_keys_select_all_and_clear() -> None:
    st = _state("批量", ["A", "B", "C"], multi_select=True, allow_custom=True)
    model, element, root, press, snapshot = _mount_popup([st])
    text = snapshot()
    assert "a 全选" in text
    assert "c 清空" in text
    assert press("char", "a") is True
    assert st.checked == [0, 1, 2]
    assert press("char", "a") is True
    assert st.checked == []
    assert press("char", "1") is True
    assert st.checked == [0]
    assert press("char", "c") is True
    assert st.checked == []
    # 输入模式中 a/c 放行给 TextInput（不触发批量）。
    press("arrow_down")
    press("arrow_down")
    press("arrow_down")
    press("enter")
    # ★ 2026-10-22：输入模式不再暂停计时（typing 仅外部编辑器期间为 True）。
    assert st.typing is False
    assert press("char", "a") is True
    assert st.checked == []


def test_input_ctrl_w_deletes_word() -> None:
    st = _state("输入", ["A"], allow_custom=True)
    model, element, root, press, snapshot = _mount_popup([st])
    press("arrow_down")
    press("enter")
    for ch in "ab cd":
        press("char", ch)
    assert press("ctrl_key", "\x17") is True
    assert press("enter") is True
    assert st.result == ["ab"]
    assert st.custom == "ab"


def test_input_ctrl_u_clears_buffer() -> None:
    st = _state("输入", ["A"], allow_custom=True)
    model, element, root, press, snapshot = _mount_popup([st])
    press("arrow_down")
    press("enter")
    for ch in "xyz":
        press("char", ch)
    assert press("ctrl_key", "\x15") is True
    # 空文本 Enter 不提交，保持输入模式。
    assert press("enter") is True
    assert st.done is False
    # ★ 2026-10-22：输入模式不再暂停计时（typing 仅外部编辑器期间为 True）。
    assert st.typing is False
    for ch in "ok":
        press("char", ch)
    assert press("enter") is True
    assert st.result == ["ok"]
    assert st.custom == "ok"


def test_input_ctrl_w_u_real_delete_events() -> None:
    """真实终端 Ctrl+W/Ctrl+U 解析为 delete modifier=1/2。"""
    st = _state("输入", ["A"], allow_custom=True)
    model, element, root, press, snapshot = _mount_popup([st])
    press("arrow_down")
    press("enter")
    for ch in "ab cd":
        press("char", ch)
    assert press("delete", "", 1) is True  # Ctrl+W
    assert press("enter") is True
    assert st.result == ["ab"]

    st2 = _state("输入2", ["A"], allow_custom=True)
    m2, e2, r2, press2, snapshot2 = _mount_popup([st2])
    press2("arrow_down")
    press2("enter")
    for ch in "xyz":
        press2("char", ch)
    assert press2("delete", "", 2) is True  # Ctrl+U
    for ch in "ok":
        press2("char", ch)
    assert press2("enter") is True
    assert st2.result == ["ok"]


def test_tab_summaries_for_confirmed_cancel_timeout() -> None:
    s1 = _state("Q1", ["A"], seq=1, allow_custom=False)
    s1.mark_answered("confirmed", ["Python"])
    s2 = _state("Q2", ["B"], seq=2, allow_custom=False)
    s2.mark_answered("cancel", [])
    s3 = _state("Q3", ["C"], seq=3, allow_custom=False)
    s3.try_set_final("timeout", [])
    text = _render([s1, s2, s3], width=100)
    assert "✓ Python" in text
    assert "✗ 取消" in text
    assert "⏱ 超时" in text


def test_claude_layout_chip_pointer_and_description_below() -> None:
    """Claude 对齐布局：header chip + ❯ 指针 + 说明缩进在选项下方。"""
    st = _state(
        "问题正文", ["A", "B"], allow_custom=True,
        option_descriptions=["说明甲", "说明乙"],
    )
    st.header = "短标签"
    text = _render([st], width=80)
    assert "短标签" in text
    assert "问题正文" in text
    assert "\u276f" in text  # ❯ 指针
    # 分栏表头/竖线已移除。
    assert "选项 \u2502" not in text
    assert "\u2502" not in text
    lines = [line for line in text.splitlines() if line.strip()]
    idx_a = next(i for i, line in enumerate(lines) if "1. A" in line)
    assert "说明甲" in lines[idx_a + 1]
    idx_b = next(i for i, line in enumerate(lines) if "2. B" in line)
    assert "说明乙" in lines[idx_b + 1]


def test_header_chip_and_question_on_separate_lines() -> None:
    st = _state("完整问题文本", ["A"], allow_custom=False)
    st.header = "认证方式"
    text = _render([st], width=80)
    lines = [line for line in text.splitlines() if line.strip()]
    chip_line = next(line for line in lines if "认证方式" in line)
    assert "完整问题文本" not in chip_line
    assert any("完整问题文本" in line for line in lines)
    # 无整行 ─ 分割线（Claude 风格）。
    assert "\u2500" not in text


def test_multiline_title_and_description_folded_one_line() -> None:
    """多行 title/说明折叠为单行（不破坏行数预算与行宽不变量）。"""
    st = _state(
        "第一行\n第二行", ["A"], allow_custom=False,
        option_descriptions=["说明一\n说明二"],
    )
    text = _render([st], width=40)
    assert "第一行 第二行" in text
    assert "说明一 说明二" in text
    _assert_width(text, 40)


def test_header_same_as_title_not_duplicated() -> None:
    """header 与 title 相同时（模型重复传参）不渲染两行相同文本。"""
    st = _state("同一个标题", ["A"], allow_custom=False)
    st.header = "同一个标题"
    text = _render([st], width=80)
    assert text.count("同一个标题") == 1


def test_multiselect_checkbox_marks() -> None:
    st = _state("Q", ["A", "B"], multi_select=True, allow_custom=False)
    model, element, root, press, snapshot = _mount_popup([st])
    text = snapshot()
    assert "[ ]" in text
    assert press("char", " ") is True
    text = snapshot()
    assert "[x]" in text


def test_submit_page_scroll_and_digit_jump(monkeypatch) -> None:
    from src.tui.app import user_select as us_mod

    # 8 个问题，Submit 页每页 5 行 → 可滚动。
    monkeypatch.setattr(us_mod, "_submit_rows_limit", lambda: 5)
    states = [
        _state(f"Q{i + 1}", ["A"], seq=i + 1, allow_custom=False)
        for i in range(8)
    ]
    model, element, root, press, snapshot = _mount_popup(states, width=80)
    # Q1 → ... → Submit（8 次 Tab）
    for _ in range(8):
        assert press("tab") is True
    text = snapshot()
    assert "提交全部答案" in text
    assert "显示 1-5/8" in text
    # PgDn 滚到底部。
    assert press("page_down") is True
    text = snapshot()
    assert "显示 4-8/8" in text
    # 数字键跳到 Q2 修改。
    assert press("char", "2") is True
    text = snapshot()
    assert "提交全部答案" not in text
    assert "Q2" in text


def test_new_features_width_invariant(monkeypatch) -> None:
    from src.tui.app import user_select as us_mod

    monkeypatch.setattr(us_mod, "_popup_item_rows", lambda: 10)
    st = _state(
        "很长的问题标题" * 3, [f"选项{i}" for i in range(30)],
        multi_select=True, allow_custom=True,
        option_descriptions=["很长的说明文本" * 8] * 30,
    )
    # header 超长（>12 显示宽）在渲染层截断，不得撑破窄屏行宽。
    st.header = "很长的短标签超过十二个字符"
    for width in (30, 40, 60, 80, 120):
        _assert_width(_render([st], width=width), width)

    states = [
        _state(f"Q{i + 1}", ["A"], seq=i + 1, allow_custom=False,
               answered=(i % 2 == 0), action="confirmed" if i % 2 == 0 else "",
               result=["A"] if i % 2 == 0 else [])
        for i in range(12)
    ]
    for width in (30, 40, 60, 80):
        text = _plain(
            h(Column, None, us_mod._build_submit_page(
                states, width, scroll=0, max_rows=4,
            )),
            width,
        )
        _assert_width(text, width)
