"""TUI 模块缺陷回归测试（2026-10 全量模块审查）。

覆盖本轮审查发现并修复的缺陷：

- Gradient 渐变进度按**显示列**归一化（宽字符混排时色标偏移）；
- 输入缓冲 Delete 跨代理对删除后光标位置未前移（光标越界/错位）；
- 粘贴文本中的 ANSI / 控制字节进入输入缓冲（终端控制码注入风险），
  统一由 ``src._ansi_text.sanitize_paste`` 清洗（主缓冲/命令面板/配置/模型字段）；
- 列表类控件显式 None label 渲染出字面 "None"；
- CodeBlock ``wrap=True`` 含 ANSI 序列时换行拆断转义序列（渲染乱码/超宽）；
- StaticLines / LiveLines 行元素非 ``Line``（如 str）时 paint 异常、整块空白；
- Viewport / FocusGroup 同批连续按键丢失（偏移/焦点只前进一格）；
- disableFocus 期间 focusNext/focusPrevious 仍可切换焦点；
- 极窄终端 ``narrow_sep_width`` 返回宽度超过终端；
- SubAgent usage 非数值字段中断整条 token 更新；
- 配置/主题数值读取对非有限浮点（inf/NaN）的 ``OverflowError`` 兜底。
"""

from __future__ import annotations

from pathlib import Path

from src.tui._input_buffer import InputBufferEditor
from src.tui.app._model_helpers import _ui_int
from src.tui.app.header import _breath_params
from src.tui.core.color import lerp_color
from src.tui.ink import h
from src.tui.ink import widgets as W
from src.tui.ink.widgets.gradient import _gradient_runs
from src.tui.ink.widgets.livelines import LiveLines
from tests.tui.ink._harness import Harness

# ═══════════════════════════════════════════════════════════
# 1. Gradient：渐变进度按显示列归一化
# ═══════════════════════════════════════════════════════════

def test_gradient_uses_display_column_progress():
    """宽字符混排时中间色标按显示列位置插值（修复前按当前簇宽作分母偏移）。"""
    runs = _gradient_runs("中中a", [0, 255])
    assert [r.text for r in runs] == ["中", "中", "a"]
    # 显示列：0 / 2 / 4；末簇起点 = 5 - 1 = 4
    assert runs[0].style.fg == lerp_color(0, 255, 0.0)
    assert runs[1].style.fg == lerp_color(0, 255, 0.5)
    assert runs[2].style.fg == lerp_color(0, 255, 1.0)


def test_gradient_ascii_progress_unchanged():
    """纯 ASCII 文本渐变进度保持线性（零回归）。"""
    runs = _gradient_runs("abcd", [0, 255])
    assert [r.style.fg for r in runs] == [
        lerp_color(0, 255, 0.0),
        lerp_color(0, 255, 1 / 3),
        lerp_color(0, 255, 2 / 3),
        lerp_color(0, 255, 1.0),
    ]


# ═══════════════════════════════════════════════════════════
# 2. InputBufferEditor：Delete 跨代理对删除后的光标位置
# ═══════════════════════════════════════════════════════════

def test_delete_between_surrogate_pair_moves_cursor_back():
    """光标落在代理对中间按 Delete：删除整个代理对后光标须前移一位。"""
    editor = InputBufferEditor(Path("/tmp/tui-bugfix-history"))
    editor.set_buffer("\ud83d\ude00abc")
    with editor._lock:
        editor._cursor_pos = 1  # 高代理与低代理之间
    editor._delete()
    assert editor.get_current_text() == "abc"
    assert editor._cursor_pos == 0


def test_delete_after_surrogate_pair_unchanged():
    """光标在代理对之后按 Delete：删除后一个字符，光标不动（零回归）。"""
    editor = InputBufferEditor(Path("/tmp/tui-bugfix-history"))
    editor.set_buffer("\ud83d\ude00abc")
    with editor._lock:
        editor._cursor_pos = 2
    editor._delete()
    assert editor.get_current_text() == "\ud83d\ude00bc"
    assert editor._cursor_pos == 2


def test_paste_strips_ansi_and_control_bytes():
    """粘贴内容剥离 ANSI 序列并删除控制字节（保留 \n / \t）。"""
    editor = InputBufferEditor(Path("/tmp/tui-bugfix-history"))
    editor.handle_chars("a\x1b[31mb\x07c\x00d\ne\tf\x1b")
    assert editor.get_current_text() == "abcd\ne\tf"
    assert editor._cursor_pos == len("abcd\ne\tf")


def test_sanitize_paste_single_and_multiline():
    """公共粘贴清洗：多行保留 \n/\t；单行额外删除换行。"""
    from src._ansi_text import sanitize_paste

    raw = "a\x1b[31mb\x07c\x00d\ne\tf\x1b]52;c;x\x07g"
    assert sanitize_paste(raw, multiline=True) == "abcd\ne\tfg"
    assert sanitize_paste(raw, multiline=False) == "abcde\tfg"
    assert sanitize_paste(None) == ""


def test_widget_none_labels_do_not_render_literal_none():
    """列表类控件的显式 None label 渲染为空串（不显示字面 "None"）。"""
    cases = [
        h(W.SelectInput, {"items": [{"label": None, "value": 1}]}),
        h(W.MultiSelect, {"items": [{"label": None, "value": 1}]}),
        h(W.RadioList, {"items": [{"label": None, "value": 1}]}),
        h(W.SearchInput, {"items": [{"label": None, "value": 1}]}),
        h(W.Breadcrumbs, {"items": [{"label": None}]}),
        h(W.Tabs, {"tabs": [{"label": None, "key": "a"}]}),
        h(W.Tree, {"data": [{"label": None}]}),
        h(W.Menu, {"items": [{"label": None}]}),
    ]
    for element in cases:
        text = "\n".join(line.plain for line in Harness(40).frame(element).lines)
        assert "None" not in text


# ═══════════════════════════════════════════════════════════
# 3. CodeBlock：wrap=True 时 ANSI 序列不得被换行拆断
# ═══════════════════════════════════════════════════════════

def test_codeblock_wrap_strips_ansi_before_wrapping():
    """含 ANSI 的代码行在 wrap 模式下先剥离序列再换行（行宽不超宽、无残缺 ESC）。"""
    harness = Harness(30)
    frame = harness.frame(h(W.CodeBlock, {
        "code": "x\x1b[31mabcdef\x1b[0m",
        "width": 12,
        "wrap": True,
    }))
    text = "\n".join(line.plain for line in frame.lines)
    assert "\x1b" not in text
    assert "abcdef" in text
    for line in frame.lines:
        assert line.width <= 12


# ═══════════════════════════════════════════════════════════
# 4. StaticLines / LiveLines：非 Line 行元素不得导致整块空白
# ═══════════════════════════════════════════════════════════

def test_static_lines_accepts_plain_strings():
    """lines 传 str 列表时正常渲染（修复前 paint 抛 AttributeError）。"""
    harness = Harness(20)
    frame = harness.frame(h(W.StaticLines, {"lines": ["hello", "world"]}))
    text = "\n".join(line.plain for line in frame.lines)
    assert "hello" in text
    assert "world" in text
    # 第二帧（前缀缓存复用）同样正常
    frame2 = harness.frame(h(W.StaticLines, {"lines": ["hello", "world"]}))
    assert "hello" in "\n".join(line.plain for line in frame2.lines)


def test_live_lines_accepts_plain_strings():
    """LiveLines 同样对非 Line 行元素做归一化。"""
    harness = Harness(20)
    frame = harness.frame(h(LiveLines, {"lines": ["live"]}))
    assert "live" in "\n".join(line.plain for line in frame.lines)
    frame2 = harness.frame(h(LiveLines, {"lines": ["live"]}))
    assert "live" in "\n".join(line.plain for line in frame2.lines)


def test_static_lines_skips_none_lines():
    """None 行按空行处理（不渲染字面 "None"）。"""
    harness = Harness(20)
    frame = harness.frame(h(W.StaticLines, {"lines": [None, "x"]}))
    text = "\n".join(line.plain for line in frame.lines)
    assert "None" not in text
    assert "x" in text


# ═══════════════════════════════════════════════════════════
# 5. 非有限数值配置：int/float 转换不得抛 OverflowError
# ═══════════════════════════════════════════════════════════

def test_ui_int_falls_back_on_non_finite(monkeypatch):
    import src.presentation_data as pd

    monkeypatch.setattr(pd, "ui_default", lambda key, default: float("inf"))
    assert _ui_int("threshold", 7) == 7
    monkeypatch.setattr(pd, "ui_default", lambda key, default: float("nan"))
    assert _ui_int("threshold", 9) == 9


def test_breath_params_falls_back_on_non_finite(monkeypatch):
    import src.presentation_data as pd

    monkeypatch.setattr(
        pd, "gradient_param",
        lambda key, default: {"lo": float("inf"), "hi": 252, "period": 8.0},
    )
    assert _breath_params("version", (242, 252, 8.0)) == (242, 252, 8.0)


# ═══════════════════════════════════════════════════════════
# 6. 同批连续按键：Viewport 偏移 / FocusGroup 焦点切换
# ═══════════════════════════════════════════════════════════

def _find_input_handler(fiber):
    from src.tui.ink.fiber import InputHook

    if fiber is None:
        return None
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return _find_input_handler(fiber.child) or _find_input_handler(fiber.sibling)


def test_viewport_consecutive_arrow_down_advances_offset(monkeypatch):
    """同一批内连续两次下键：offset 依次 1、2（修复前两次都从旧闭包 0 算 = 1）。"""
    from types import SimpleNamespace

    from src.tui.ink.reconciler import Reconciler

    changes: list = []
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    props = {
        "lines": [str(i) for i in range(10)],
        "height": 3,
        "onOffsetChange": changes.append,
    }
    rec.render(root, h(W.Viewport, dict(props)), 30, 20)
    handler = _find_input_handler(root.child)
    event = SimpleNamespace(kind="arrow_down", char="", modifier=0, keycode=0, raw=b"")
    assert handler(event) is True
    assert handler(event) is True
    assert changes == [1, 2]


def test_focus_group_consecutive_tab_advances_twice():
    """同批连续两次 Tab：焦点经函数式更新推进两格（修复前只推进一格）。"""
    from types import SimpleNamespace

    from src.tui.ink import hooks
    from src.tui.ink.fiber import TAG_FUNCTION, Fiber, InputHook

    state = [0]

    def _set(value) -> None:
        state[0] = value(state[0]) if callable(value) else value

    props = {
        "_focus_index": 0,
        "_focus_total": 3,
        "_focus_active": True,
        "_focus_set": _set,
        "children": None,
    }
    fiber = Fiber(TAG_FUNCTION, W.Key, dict(props))
    hooks._push_current(fiber)
    try:
        W.Key(props)
    finally:
        hooks._pop_current()
    handler = next(
        hook.handler for hook in fiber.hooks
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None
    )
    event = SimpleNamespace(kind="tab", char="", modifier=0, keycode=9, raw=b"\t")
    assert handler(event) is True
    assert handler(event) is True
    assert state[0] == 2


def test_focus_next_previous_noop_when_disabled():
    """disableFocus 期间 focusNext/focusPrevious 不得写入 active（与 focus(id) 一致）。"""
    from src.tui.ink import hooks
    from src.tui.ink._hooks_focus import _focus_next, _focus_previous

    saved_enabled = hooks._focus_enabled
    saved_active = hooks._focus_active
    saved_ids = hooks._focus_ids
    try:
        hooks._focus_enabled = False
        hooks._focus_active = None
        hooks._focus_ids = ["a", "b"]
        _focus_next()
        assert hooks._focus_active is None
        _focus_previous()
        assert hooks._focus_active is None
    finally:
        hooks._focus_enabled = saved_enabled
        hooks._focus_active = saved_active
        hooks._focus_ids = saved_ids


# ═══════════════════════════════════════════════════════════
# 7. 终端宽度辅助：narrow_sep_width 不得宽于终端
# ═══════════════════════════════════════════════════════════

def test_narrow_sep_width_never_exceeds_terminal():
    """极窄终端下分隔线宽度钳制到终端宽度（修复前 width<10 返回 10）。"""
    from src.tui._screen import narrow_sep_width

    assert narrow_sep_width(5) == 5
    assert narrow_sep_width(8) == 8
    assert narrow_sep_width(9) == 9
    assert narrow_sep_width(20) == 18
    assert narrow_sep_width(30) == 28
    assert narrow_sep_width(60) == 60
    assert narrow_sep_width(1) == 1


# ═══════════════════════════════════════════════════════════
# 8. SubAgent 状态：usage 非数值字段不得中断整条更新
# ═══════════════════════════════════════════════════════════

def test_subagent_usage_coerces_non_numeric_fields():
    """usage 的 input/output 为字符串/None 时按整数归一化（修复前 TypeError）。"""
    from src.tui._subagent_state import StateStore

    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.update_usage("agent-1", {
        "input": "100", "output": None, "live_input": "5",
        "live_output": object(), "speed": "fast",
    })
    slot = store._agents["agent-1"]
    assert slot.input_tokens == 100
    assert slot.output_tokens == 0
    assert slot.live_input_tokens == 5
    assert slot.live_output_tokens == 0
    assert slot.last_speed == 0.0
    # 后续合法更新继续累加（事件未中断）
    store.update_usage("agent-1", {"input": 10})
    assert slot.input_tokens == 110
