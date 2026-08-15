"""Review 修复测试：paint/hooks 相关文件 Code Review 问题。

修复背景（2026-08-15 review）：
  - P1-1 [_paint_canvas.py] 零宽字符（组合标记 U+0300-036F / ZWJ U+200D /
    变体选择符 U+FE00 等，宽度 0）在画布转换/合并中被后续字符覆盖丢失
    （``"e\\u0301x"`` 渲染成 ``"ex"``）；tab（宽度 0）同根因。
  - P2-1 [_paint_border.py] _border_style 单边分支 fg 非 None 时丢失 base.dim。
  - P2-2 [_hooks_focus.py] autoFocus 不检查 _focus_enabled；隐藏组件焦点悬挂。
  - P2-3 [_hooks_env.py] useSyncExternalStore 渲染期同步调用 subscribe（重入风险）。
  - P3-1 [_cursor.py] find_input_fiber 递归无深度限制。
  - P3-2 [_paint_border.py] _paint_border 的 x0/x1 无负坐标防御。
"""

from __future__ import annotations

import pytest

from src.tui._width import wcswidth_simple
from src.tui.core.style import Style
from src.tui.ink import hooks
from src.tui.ink.fiber import (
    Fiber,
    InputHook,
    SyncStoreHook,
    EffectHook,
    TAG_FUNCTION,
    TAG_HOST,
)
from src.tui.ink._paint_canvas import (
    _line_as_dict,
    _merge_line,
    _canvas_row_to_line,
)
from src.tui.ink._paint_border import _border_style, _paint_border
from src.tui.ink._hooks_focus import useFocus, _reset_focus_ids
from src.tui.ink._hooks_env import useSyncExternalStore
from src.tui.ink._hooks_core import _push_current, _pop_current
from src.tui.ink._cursor import find_input_fiber
from src.tui.ink.output import Line


# ═══════════════════════════════════════════════════════════
# 共享夹具
# ═══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _preserve_hooks_state():
    """保存/恢复 hooks 模块级焦点状态（测试间隔离，避免污染后续用例）。"""
    saved = (
        hooks._focus_enabled,
        hooks._focus_active,
        list(hooks._focus_ids),
    )
    yield
    hooks._focus_enabled, hooks._focus_active, hooks._focus_ids = (
        saved[0], saved[1], saved[2],
    )


def _render_use_focus(fiber: Fiber, options) -> dict:
    """在 hook 环境下调用 useFocus（与 reconciler 渲染路径一致）。"""
    _push_current(fiber)
    try:
        return useFocus(options)
    finally:
        _pop_current()


# ═══════════════════════════════════════════════════════════
# P1-1 零宽字符画布转换/合并
# ═══════════════════════════════════════════════════════════


def test_zero_width_char_merged_to_prev_key():
    """P1-1 组合标记合并到前键不丢失（"e\\u0301x" 往返渲染保持原文本）。"""
    text = "e\u0301x"
    d = _line_as_dict(Line.of(text))
    assert d[0] == ("e\u0301", None), d
    assert d[1] == ("x", None), d
    out = _canvas_row_to_line(d)
    assert out.plain == text
    assert out.width == wcswidth_simple(text) == 2


def test_zero_width_zwj_and_variation_selector_preserved():
    """P1-1 ZWJ（U+200D）/变体选择符（U+FE0F）/组合重音均合并到前键。"""
    for zw in ("\u200d", "\ufe0f", "\u0301", "\u0308"):
        text = f"a{zw}b"
        d = _line_as_dict(Line.of(text))
        assert d[0] == (f"a{zw}", None), d
        assert d[1] == ("b", None), d
        assert _canvas_row_to_line(d).plain == text


def test_tab_does_not_override_next_char():
    """P1-1 tab（宽度 0）同根因处理：合并到前键、不覆盖后续字符。"""
    text = "a\tb"
    d = _line_as_dict(Line.of(text))
    assert d[0] == ("a\t", None), d
    assert d[1] == ("b", None), d
    assert _canvas_row_to_line(d).plain == text


def test_cjk_wide_char_behavior_unchanged():
    """P1-1 CJK 宽字符既有行为不变（占 2 列键）。"""
    d = _line_as_dict(Line.of("中文"))
    assert d == {0: ("中", None), 2: ("文", None)}, d
    out = _canvas_row_to_line(d)
    assert out.plain == "中文"
    assert out.width == 4


def test_zero_width_after_wide_char_merges_to_wide_prev():
    """P1-1 宽字符后跟零宽字符：合并到宽字符键（col-2，非 col-1）。"""
    text = "\u4e2d\u0301\u6587"  # 中 + 组合重音 + 文
    d = _line_as_dict(Line.of(text))
    assert d[0] == ("\u4e2d\u0301", None), d
    assert d[2] == ("\u6587", None), d
    out = _canvas_row_to_line(d)
    assert out.plain == text
    assert out.width == 4


def test_zero_width_style_follows_base_char():
    """P1-1 零宽字符合并后样式保留基字符样式。"""
    st = Style(fg=12)
    d = _line_as_dict(Line.of("e\u0301", st))
    assert d[0] == ("e\u0301", st), d


def test_merge_line_zero_width_preserved():
    """P1-1 _merge_line 片段构造零宽字符合并到前键（含 x 偏移）。"""
    row = _merge_line({}, 0, Line.of("e\u0301x"))
    assert row[0] == ("e\u0301", None), row
    assert row[1] == ("x", None), row
    assert _canvas_row_to_line(row).plain == "e\u0301x"

    row2 = _merge_line({}, 3, Line.of("e\u0301x"))
    assert row2[3] == ("e\u0301", None), row2
    assert row2[4] == ("x", None), row2


def test_merge_line_zero_width_overlay_keeps_new_text():
    """P1-1 合并覆盖场景：零宽字符随新字符写入，不残留/不丢失。"""
    row = {0: ("a", None), 1: ("b", None)}
    merged = _merge_line(row, 1, Line.of("X\u0301"))
    assert merged[1] == ("X\u0301", None), merged
    assert _canvas_row_to_line(merged).plain == "aX\u0301"


# ═══════════════════════════════════════════════════════════
# P2-1 单边边框 dim 保留
# ═══════════════════════════════════════════════════════════


def test_border_style_single_edge_fg_keeps_base_dim():
    """P2-1 单边边框 fg 非 None 时保留 base.dim。"""
    base = Style(fg=23, dim=True)
    st = _border_style({"borderStyle": base, "borderColor": 12}, "top")
    assert st.dim is True
    assert st.fg == 12
    # 其余字型属性亦保留（既有 P3 修复行为）
    assert st.bg == base.bg


def test_border_style_no_fg_keeps_base_dim():
    """P2-1 单边边框无 fg 分支既有行为（base.dim 保留）。"""
    base = Style(fg=23, dim=True)
    st = _border_style({"borderStyle": base}, "top")
    assert st.dim is True
    assert st.fg == 23


def test_border_style_dim_color_merged_with_base():
    """P2-1 borderDimColor 显式置 dim 与 base.dim 合并生效。"""
    base = Style(fg=23, dim=True)
    st = _border_style(
        {"borderStyle": base, "borderColor": 12, "borderDimColor": True}, "top"
    )
    assert st.dim is True
    assert st.fg == 12


# ═══════════════════════════════════════════════════════════
# P2-2 autoFocus 与焦点悬挂
# ═══════════════════════════════════════════════════════════


def test_autofocus_ignored_when_focus_disabled():
    """P2-2 disableFocus 期间 autoFocus 不写入 _focus_active。"""
    f = Fiber(TAG_FUNCTION, props={})
    f.hooks.append(InputHook())
    hooks._focus_enabled = False
    hooks._focus_active = None
    hooks._focus_ids = []
    _render_use_focus(f, {"autoFocus": True, "id": "a"})
    assert hooks._focus_active is None
    # 组件仍注册（enableFocus 后可参与焦点路由）
    assert hooks._focus_ids == ["a"]


def test_autofocus_works_when_focus_enabled():
    """P2-2 焦点启用时 autoFocus 正常写入 _focus_active。"""
    f = Fiber(TAG_FUNCTION, props={})
    f.hooks.append(InputHook())
    hooks._focus_enabled = True
    hooks._focus_active = None
    hooks._focus_ids = []
    _render_use_focus(f, {"autoFocus": True, "id": "b"})
    assert hooks._focus_active == "b"


def test_reset_focus_ids_clears_stale_active():
    """P2-2 隐藏组件焦点清空：active 不在收集列表则 _reset_focus_ids 清空。"""
    hooks._focus_active = "stale"
    hooks._focus_ids = ["visible"]
    _reset_focus_ids()
    assert hooks._focus_active is None
    assert hooks._focus_ids == []


def test_reset_focus_ids_keeps_valid_active():
    """P2-2 有效 active（在收集列表中）保留。"""
    hooks._focus_active = "a"
    hooks._focus_ids = ["a", "b"]
    _reset_focus_ids()
    assert hooks._focus_active == "a"
    assert hooks._focus_ids == []


# ═══════════════════════════════════════════════════════════
# P2-3 useSyncExternalStore 订阅时机
# ═══════════════════════════════════════════════════════════


def _render_store(fiber: Fiber, subscribe, get_snapshot):
    """在 hook 环境下调用 useSyncExternalStore（渲染期），返回 snapshot。"""
    _push_current(fiber)
    try:
        return useSyncExternalStore(subscribe, get_snapshot)
    finally:
        _pop_current()


def _commit_layout_effects(fiber: Fiber) -> None:
    """模拟 reconciler 提交期：执行全部 layout effect 的 create。"""
    for h in fiber.hooks:
        if isinstance(h, EffectHook) and h.layout and h.create is not None:
            h.create()


def test_sync_external_store_subscribes_in_commit_phase():
    """P2-3 渲染期不调用 subscribe，提交期（layout effect）才订阅。"""
    calls = []

    def subscribe(listener):
        calls.append(listener)
        return lambda: None

    f = Fiber(TAG_FUNCTION, props={})
    snap = _render_store(f, subscribe, lambda: 42)
    assert calls == []  # 渲染期未订阅
    assert snap == 42
    _commit_layout_effects(f)
    assert len(calls) == 1  # 提交期订阅
    sync = next(h for h in f.hooks if isinstance(h, SyncStoreHook))
    assert sync.subscribed is True
    assert sync.cleanup is not None


def test_sync_external_store_no_resubscribe_on_same_identity():
    """P2-3 subscribe 身份不变时不重订阅（跨渲染复用 hook）。"""
    calls = []

    def subscribe(listener):
        calls.append(listener)
        return lambda: None

    f = Fiber(TAG_FUNCTION, props={})
    _render_store(f, subscribe, lambda: 1)
    _commit_layout_effects(f)
    assert len(calls) == 1

    # 第二帧（同一 fiber，hook 复用）
    f.reset_hooks()
    _render_store(f, subscribe, lambda: 2)
    _commit_layout_effects(f)
    assert len(calls) == 1  # 身份相同不重订阅


def test_sync_external_store_resubscribe_on_new_identity():
    """P2-3 subscribe 身份变化时重订阅（先清理旧订阅再订阅新 store）。"""
    cleaned = []

    def subscribe(listener):
        return lambda: cleaned.append("cleanup")

    def subscribe2(listener):
        return lambda: cleaned.append("cleanup2")

    f = Fiber(TAG_FUNCTION, props={})
    _render_store(f, subscribe, lambda: 1)
    _commit_layout_effects(f)

    f.reset_hooks()
    _render_store(f, subscribe2, lambda: 2)
    _commit_layout_effects(f)
    assert cleaned == ["cleanup"]  # 旧 cleanup 被调用


def test_sync_external_store_subscribe_error_retry_next_frame():
    """P2-3 订阅抛异常后 last_subscribe 复位，下帧可重试。"""
    calls = []

    def flaky_subscribe(listener):
        calls.append(listener)
        if len(calls) == 1:
            raise RuntimeError("subscribe failed")
        return lambda: None

    f = Fiber(TAG_FUNCTION, props={})
    _render_store(f, flaky_subscribe, lambda: 1)
    _commit_layout_effects(f)
    sync = next(h for h in f.hooks if isinstance(h, SyncStoreHook))
    assert sync.subscribed is False
    assert sync.last_subscribe is None  # 复位可重试

    # 第二帧：重试订阅成功
    f.reset_hooks()
    _render_store(f, flaky_subscribe, lambda: 1)
    _commit_layout_effects(f)
    assert sync.subscribed is True
    assert sync.cleanup is not None


def test_sync_external_store_sync_notify_in_commit_phase_safe():
    """P2-3 提交期订阅时 store 同步通知 listener：不抛异常（渲染已结束，无重入）。"""
    notified = []

    def subscribe(listener):
        # store 在订阅期间同步通知（渲染已结束，调度下帧渲染安全）
        listener()
        notified.append(True)
        return lambda: None

    f = Fiber(TAG_FUNCTION, props={})
    _render_store(f, subscribe, lambda: 1)
    _commit_layout_effects(f)
    assert notified == [True]


# ═══════════════════════════════════════════════════════════
# P3-1 find_input_fiber 深度防御
# ═══════════════════════════════════════════════════════════


def test_find_input_fiber_deep_tree_no_recursion():
    """P3-1 深层树（5000 层）不递归溢出，找到输入区 fiber。"""
    depth = 5000
    root = Fiber(TAG_HOST, props={})
    cur = root
    for _ in range(depth):
        child = Fiber(TAG_HOST, props={})
        cur.child = child
        child.return_ = cur
        cur = child
    cur.props = {"dataInputArea": True}
    assert find_input_fiber(root) is cur


def test_find_input_fiber_none_when_absent():
    """P3-1 无输入区时返回 None。"""
    root = Fiber(TAG_HOST, props={})
    assert find_input_fiber(root) is None


def test_find_input_fiber_dfs_order_child_first():
    """P3-1 DFS 顺序保持：child 深度优先、sibling 从左到右。"""
    r = Fiber(TAG_HOST, props={})
    a = Fiber(TAG_HOST, props={})
    b = Fiber(TAG_HOST, props={"dataInputArea": True})
    r.child = a
    a.sibling = b
    a1 = Fiber(TAG_HOST, props={"dataInputArea": True})
    a.child = a1
    # a 的子树先于 sibling b（原递归 child 深度优先语义）
    assert find_input_fiber(r) is a1


# ═══════════════════════════════════════════════════════════
# P3-2 _paint_border 负坐标防御
# ═══════════════════════════════════════════════════════════


def _box(x, y, w, h):
    return type("Box", (), {"x": x, "y": y, "w": w, "h": h})()


def test_paint_border_negative_x_no_negative_columns():
    """P3-2 负 x0 边框不写负列（钳制 x0/x1 >= 0）。"""
    canvas = [{} for _ in range(5)]
    f = Fiber(TAG_HOST, props={"borderStyle": "single"})
    f.layout_box = _box(-3, 0, 5, 2)
    _paint_border(f, canvas, 1)
    for r in canvas:
        assert all(k >= 0 for k in r), f"负列写入: {r}"


def test_paint_border_fully_offscreen_left_skipped():
    """P3-2 box 完全在屏幕左侧外（x1 < 0）时不绘制。"""
    canvas = [{} for _ in range(5)]
    f = Fiber(TAG_HOST, props={"borderStyle": "single"})
    f.layout_box = _box(-10, 0, 3, 2)
    _paint_border(f, canvas, 1)
    assert all(len(r) == 0 for r in canvas)


def test_paint_border_normal_box_unchanged():
    """P3-2 正常正坐标边框行为不变（四边框完整写入）。"""
    canvas = [{} for _ in range(3)]
    f = Fiber(TAG_HOST, props={"borderStyle": "single"})
    f.layout_box = _box(0, 0, 3, 3)
    _paint_border(f, canvas, 1)
    assert canvas[0][0][0] == "┌"
    assert canvas[0][2][0] == "┐"
    assert canvas[2][0][0] == "└"
    assert canvas[2][2][0] == "┘"
    assert canvas[1][0][0] == "│"
    assert canvas[1][2][0] == "│"
    assert canvas[0][1][0] == "─"
