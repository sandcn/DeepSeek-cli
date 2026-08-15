"""Code Review 修复验证测试（widgets 组件库 P1/P2 全量）。

覆盖修复点：
  - P1-1  gradient 标量/str colors 保留文本（不整体消失）
  - P1-2  codeblock 短行右竖线与边框对齐（行宽不变量）
  - P1-3  focus Key 不吞 arrow_left（子组件左移键可达）
  - P2-1  focus Shift+Tab（modifier==2）走后退
  - P2-2  focus 渲染期不 set_active（事件期钳制 state）
  - P2-3  _panel paddingLeft/paddingRight 生效
  - P2-4  search_input 空结果不消费方向键/回车
  - P2-5  _text_input 多字符 mask 光标定位正确
  - P2-6  menu shortcutAlign="left" 应用 minShortcutGap
  - P2-7  _widget_common._color int 钳制 [0,255] + bool 排除
  - P2-8  _table columns=[] 空表头不追加行/不置 has_header
  - P2-9  listview renderItem 异常降级占位文本
  - P2-10 spinner dict frames 不抛 KeyError
  - P2-11 codeblock width<4 不溢出（行宽不变量）
"""

from __future__ import annotations

from src.tui.ink.element import TEXT, Element, h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.output import StyledRun
from src.tui.ink.fiber import StateHook
from src.tui._input_parser import KeyEvent

from src.tui.ink.widgets.gradient import Gradient
from src.tui.ink.widgets.codeblock import CodeBlock
from src.tui.ink.widgets.focus import FocusGroup, Key
from src.tui.ink.widgets._panel import Panel
from src.tui.ink.widgets.search_input import SearchInput
from src.tui.ink.widgets._text_input import TextInput
from src.tui.ink.widgets.menu import Menu
from src.tui.ink.widgets._widget_common import _color
from src.tui.ink.widgets._table import Table
from src.tui.ink.widgets.listview import ListView
from src.tui.ink.widgets.spinner import InlineSpinner


# ═══════════════════════════════════════════════════════════
# 测试辅助
# ═══════════════════════════════════════════════════════════


def _render(element, width: int = 80, height: int = 24):
    """调和 + 布局 + 渲染 Element 树，返回 (reconciler, root_fiber, frame)。"""
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, element, width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    """Frame 各行纯文本。"""
    return [ln.plain for ln in frame.lines]


def _find_state_hooks(fiber):
    """收集 fiber 树中全部 StateHook（检查 queue 用）。"""
    out = []
    stack = [fiber]
    while stack:
        f = stack.pop()
        while f is not None:
            if f.is_function:
                for hook in f.hooks:
                    if isinstance(hook, StateHook):
                        out.append(hook)
            if f.child is not None:
                stack.append(f.sibling)
                f = f.child
            else:
                f = f.sibling
    return out


def _focus_probe_factory(log: list):
    """构造记录 ``(id, focus)`` 的子组件函数。"""
    def probe(props):
        log.append((props.get("id"), props.get("focus")))
        return h(TEXT, {"children": "x"})
    return probe


# ═══════════════════════════════════════════════════════════
# P1-1 gradient
# ═══════════════════════════════════════════════════════════


def test_gradient_scalar_colors_keeps_text():
    """colors=45（int 标量）：保留文本无样式，而非渲染空文本。"""
    el = Gradient({"text": "hello", "colors": 45})
    assert el.type is TEXT
    runs = el.props.get("styled")
    assert runs == [StyledRun("hello", None)]


def test_gradient_str_colors_keeps_text():
    """colors="red"（str）：保留文本无样式。"""
    el = Gradient({"text": "hi", "colors": "red"})
    runs = el.props.get("styled")
    assert [r.text for r in runs] == ["hi"]
    assert runs[0].style is None


def test_gradient_scalar_colors_with_style_keeps_style():
    """colors 标量 + style prop：保留文本并应用基础样式（渐变 fg 无则用 style）。"""
    from src.tui.core.style import Style
    el = Gradient({"text": "hi", "colors": 45, "style": Style(fg=12)})
    runs = el.props.get("styled")
    assert [r.text for r in runs] == ["hi"]
    assert runs[0].style == Style(fg=12)


# ═══════════════════════════════════════════════════════════
# P1-2 codeblock 短行对齐
# ═══════════════════════════════════════════════════════════


def test_codeblock_short_line_right_border_aligned():
    """短行 content 填充到 inner_w——所有行宽 == width，右竖线对齐边框。"""
    _, _, frame = _render(h(CodeBlock, {"code": "a\nbb", "width": 12}))
    lines = _frame_plain(frame)
    assert len(lines) == 4  # 顶边框 + 2 代码行 + 底边框
    for ln in frame.lines:
        assert ln.width == 12  # 行宽不变量
    # 代码行右竖线对齐（末字符为边框竖线）
    assert lines[1].endswith("│")
    assert lines[2].endswith("│")


def test_codeblock_short_line_cjk_alignment():
    """CJK 宽字符短行：按显示宽度填充（内容宽 2 的中文行右侧竖线对齐）。"""
    _, _, frame = _render(h(CodeBlock, {"code": "中", "width": 10}))
    for ln in frame.lines:
        assert ln.width == 10


# ═══════════════════════════════════════════════════════════
# P1-3 / P2-1 / P2-2 focus
# ═══════════════════════════════════════════════════════════


def test_focus_key_does_not_consume_arrow_left():
    """Key 不吞 arrow_left——子组件 TextInput 收到左移事件（光标左移）。"""
    rec, root, frame = _render(h(FocusGroup, None, [
        h(Key, None, h(TextInput, {"value": "A"})),
        h(Key, None, h(TextInput, {"value": "B"})),
    ]))
    # 初始：value="A" 光标在末尾 → "A" + 光标空格
    assert _frame_plain(frame)[0] == "A "
    router = rec._build_input_router(root)
    # arrow_left 被 TextInput 消费（返回 True）——而非 Key 焦点后退
    assert router(KeyEvent(kind="arrow_left")) is True
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(TextInput, {"value": "A"})),
        h(Key, None, h(TextInput, {"value": "B"})),
    ]), 80, 24)
    frame = render_frame(root, 80)
    # 光标左移到 0：光标覆盖首字符 → "A" + "A"（显示 'AA'）
    assert _frame_plain(frame)[0] == "AA"


def test_focus_shift_tab_goes_backward():
    """Shift+Tab（modifier==2）后退——焦点从第 2 个 Key 回到第 1 个。"""
    log: list = []
    probe = _focus_probe_factory(log)
    rec, root, _ = _render(h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]))
    # 初始焦点在 k1
    assert ("k1", True) in log and ("k2", False) in log
    # Tab 前进 → k2
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="tab", modifier=1)) is True
    log.clear()
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]), 80, 24)
    assert ("k1", False) in log and ("k2", True) in log
    # Shift+Tab 后退 → k1
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="tab", modifier=2)) is True
    log.clear()
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]), 80, 24)
    assert ("k1", True) in log and ("k2", False) in log


def test_focus_tab_no_modifier_goes_forward():
    """普通 Tab（modifier 0）仍前进（P2-1 回归：非 modifier==2 走前进）。"""
    log: list = []
    probe = _focus_probe_factory(log)
    rec, root, _ = _render(h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="tab", modifier=0)) is True
    log.clear()
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]), 80, 24)
    assert ("k1", False) in log and ("k2", True) in log


def test_focus_render_no_state_side_effect():
    """渲染期 active 越界只显示钳制、不 set_active（StateHook.queue 为 None）；
    事件期钳制同步 state。"""
    log: list = []
    probe = _focus_probe_factory(log)
    rec, root, _ = _render(h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]))
    # Tab 前进到第 2 个（active=1）
    router = rec._build_input_router(root)
    router(KeyEvent(kind="tab", modifier=1))
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
        h(Key, None, h(probe, {"id": "k2"})),
    ]), 80, 24)
    # 收缩为 1 个 Key → active=1 >= n_keys=1 越界
    log.clear()
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
    ]), 80, 24)
    # 显示钳制：k1 激活
    assert log == [("k1", True)]
    # 渲染期未 set_active：FocusGroup 的 StateHook queue 为 None
    fg_states = [s for s in _find_state_hooks(root) if s.state in (0, 1)]
    assert fg_states, "未找到 FocusGroup 的 StateHook"
    assert all(s.queue is None for s in fg_states), "渲染期不应排队 state 更新"
    # 事件期钳制：任意事件触发 set_active(0)，不消费事件
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="escape")) is False
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
    ]), 80, 24)
    # 钳制后 state=0，k1 仍激活（且不再越界）
    log.clear()
    rec.render(root, h(FocusGroup, None, [
        h(Key, None, h(probe, {"id": "k1"})),
    ]), 80, 24)
    assert log == [("k1", True)]


# ═══════════════════════════════════════════════════════════
# P2-3 _panel
# ═══════════════════════════════════════════════════════════


def test_panel_padding_left_right():
    """Panel 分别读取 paddingLeft/paddingRight（缺省回退 padding）。"""
    el = Panel({
        "title": "T",
        "width": 20,
        "paddingLeft": 3,
        "paddingRight": 5,
        "children": [h(TEXT, {"children": "x"})],
    })
    assert el.props["paddingLeft"] == 3
    assert el.props["paddingRight"] == 5


def test_panel_padding_left_right_default_fallback():
    """未提供 paddingLeft/paddingRight 时回退统一 padding。"""
    el = Panel({
        "width": 20,
        "padding": 2,
        "children": [h(TEXT, {"children": "x"})],
    })
    assert el.props["paddingLeft"] == 2
    assert el.props["paddingRight"] == 2


# ═══════════════════════════════════════════════════════════
# P2-4 search_input
# ═══════════════════════════════════════════════════════════


def test_search_input_empty_result_not_consume_arrows():
    """空结果时方向键/回车不消费事件（返回 False）。"""
    rec, root, _ = _render(h(SearchInput, {"items": [], "focus": True}))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is False
    assert router(KeyEvent(kind="arrow_up")) is False
    assert router(KeyEvent(kind="enter")) is False


def test_search_input_no_match_not_consume_arrows():
    """查询后无匹配（filtered 空）时方向键/回车不消费。"""
    rec, root, _ = _render(h(SearchInput, {"items": ["apple"], "focus": True}))
    router = rec._build_input_router(root)
    # 输入查询 "zzz"（无匹配）
    assert router(KeyEvent(kind="char", char="z")) is True
    assert router(KeyEvent(kind="char", char="z")) is True
    assert router(KeyEvent(kind="char", char="z")) is True
    assert router(KeyEvent(kind="arrow_down")) is False
    assert router(KeyEvent(kind="enter")) is False


def test_search_input_backspace_empty_query_not_consume():
    """空查询 backspace 不消费（P3 顺带）。"""
    rec, root, _ = _render(h(SearchInput, {"items": ["apple"], "focus": True}))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="backspace")) is False


# ═══════════════════════════════════════════════════════════
# P2-5 _text_input 多字符 mask
# ═══════════════════════════════════════════════════════════


def test_text_input_multi_char_mask_cursor_position():
    """mask 长度>1 时光标按 eff*len(mask) 定位（不切在错误字符边界）。

    value="xy" mask="ab"：display="abab"，cursor=2（末尾）→ 'abab'+光标空格；
    arrow_left 后 cursor=1 → before='ab'、光标覆盖 display[2]='a'、after='ab'
    → 'abaab'（修复前 disp_eff=1 → before='a' 光标错位）。
    """
    el = h(TextInput, {"value": "xy", "mask": "ab", "showCursor": True})
    rec, root, frame = _render(el)
    assert _frame_plain(frame)[0] == "abab "
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_left")) is True
    rec.render(root, el, 80, 24)
    frame = render_frame(root, 80)
    assert _frame_plain(frame)[0] == "abaab"


# ═══════════════════════════════════════════════════════════
# P2-6 menu shortcut 间距
# ═══════════════════════════════════════════════════════════


def test_menu_shortcut_left_align_min_gap():
    """shortcutAlign="left" 用 ``max(minShortcutGap, 2)`` 拼接间距。"""
    _, _, frame = _render(h(Menu, {
        "items": [
            {"label": "open", "shortcut": "O"},
            {"label": "quit", "shortcut": "Q"},
        ],
        "shortcutAlign": "left",
        "minShortcutGap": 5,
    }))
    lines = _frame_plain(frame)
    assert lines[0] == "open     O"  # open + 5 空格 + O
    assert lines[1] == "quit     Q"


def test_menu_shortcut_left_align_min_gap_floor():
    """minShortcutGap < 2 时仍保持至少 2 空格。"""
    _, _, frame = _render(h(Menu, {
        "items": [{"label": "open", "shortcut": "O"}],
        "shortcutAlign": "left",
        "minShortcutGap": 0,
    }))
    assert _frame_plain(frame)[0] == "open  O"


# ═══════════════════════════════════════════════════════════
# P2-7 _color
# ═══════════════════════════════════════════════════════════


def test_color_clamps_int_range():
    """int 色号钳制到 [0, 255]。"""
    assert _color(300) == 255
    assert _color(-5) == 0
    assert _color(255) == 255
    assert _color(0) == 0
    assert _color(45) == 45


def test_color_excludes_bool():
    """bool 是 int 子类——显式排除，回退 default。"""
    assert _color(True) == 6
    assert _color(False) == 6
    assert _color(True, 12) == 12


def test_color_name_and_none():
    """颜色名/None 行为保持。"""
    assert _color("red") == 1
    assert _color(None) == 6
    assert _color(None, 12) == 12
    assert _color("not-a-color") == 6


# ═══════════════════════════════════════════════════════════
# P2-8 _table 空表头
# ═══════════════════════════════════════════════════════════


def test_table_empty_columns_no_header_row():
    """columns=[] 时不追加空表头行、不置 has_header——无分隔行。"""
    _, _, frame = _render(h(Table, {
        "columns": [],
        "data": [["a", "b"], ["c", "d"]],
        "border": "single",
    }))
    lines = _frame_plain(frame)
    # 顶边框 + 2 数据行 + 底边框（无表头行/分隔行）
    assert lines == ["┌───┬───┐", "│ a │ b │", "│ c │ d │", "└───┴───┘"]


def test_table_empty_columns_data_rows_use_cell_style():
    """columns=[] 时数据行不使用表头样式（has_header False）。"""
    el = Table({"columns": [], "data": [["a"]], "border": "single"})
    # 直接检查返回的 Column children：数据行 TEXT 样式非 header_style
    # （Column children：顶边框、数据行、底边框——无分隔行）
    assert len(el.children) == 3


def test_table_has_header_normal_still_works():
    """非空 columns 时表头行为保持（含分隔行）。"""
    _, _, frame = _render(h(Table, {
        "columns": ["h1", "h2"],
        "data": [["a", "b"]],
        "border": "single",
    }))
    lines = _frame_plain(frame)
    # 顶边框 + 表头 + 分隔 + 数据行 + 底边框
    assert lines == ["┌────┬────┐", "│ h1 │ h2 │", "├────┼────┤",
                     "│ a  │ b  │", "└────┴────┘"]


# ═══════════════════════════════════════════════════════════
# P2-9 listview renderItem 异常
# ═══════════════════════════════════════════════════════════


def test_listview_render_item_exception_degrades():
    """renderItem 抛异常时降级为占位文本（str(item)），不崩溃。"""
    def bad(item, index):
        raise RuntimeError("boom")

    _, _, frame = _render(h(ListView, {
        "items": ["alpha", "beta"],
        "renderItem": bad,
        "height": 2,
    }))
    lines = _frame_plain(frame)
    assert lines[0] == "alpha"
    assert lines[1] == "beta"


# ═══════════════════════════════════════════════════════════
# P2-10 spinner dict frames
# ═══════════════════════════════════════════════════════════


def test_spinner_dict_frames_no_keyerror():
    """frames 为 dict 且索引不在键中时捕获 KeyError（回退空格），不抛异常。"""
    el = InlineSpinner({"frames": {"x": 1}})
    assert el.type is TEXT
    assert el.props.get("children") in (" ", "x")


def test_spinner_list_frames_still_works():
    """frames 为 list 时行为保持。"""
    el = InlineSpinner({"frames": ["a", "b"]})
    assert el.props.get("children") in ("a", "b")


# ═══════════════════════════════════════════════════════════
# P2-11 codeblock width<4
# ═══════════════════════════════════════════════════════════


def test_codeblock_width_less_than_min_no_overflow():
    """显式 width<5 时钳制最小有效宽度——所有行宽一致且不溢出。"""
    for w in (1, 2, 3, 4):
        _, _, frame = _render(h(CodeBlock, {"code": "hi", "width": w}))
        widths = [ln.width for ln in frame.lines]
        assert len(set(widths)) == 1, f"width={w} 行宽不一致: {widths}"
        # 行宽不变量：不超过钳制后的实际渲染宽
        assert widths[0] == 5


def test_codeblock_width_less_than_min_with_line_numbers():
    """width 小于行号栏最小宽度时钳制（含行号场景不溢出）。"""
    _, _, frame = _render(h(CodeBlock, {
        "code": "a\nbb", "width": 2, "lineNumbers": True,
    }))
    widths = [ln.width for ln in frame.lines]
    assert len(set(widths)) == 1
