"""弹窗 sel_bg 死代码清理测试（L5）+ 滚动窗口自动滚动测试（2026-08-15）。

修复背景（2026-08-15 L5）：``_popup_builder.py`` 模块级 ``sel_bg = 237``
（原 L35）与 ``_build_popup_lines`` 函数内局部 ``sel_bg = 237``（原 L247）
重复——模块级被局部遮蔽且无外部引用（已 search 确认仅本文件内
L272/L313 使用局部）。修复：删除模块级定义（死代码清理）。

修复背景（2026-08-15 滚动）：/load 会话候选多时一直按下键，补全弹窗固定
从 ``items[0]`` 渲染——选中项移出首屏后不可见（无自动滚动）。修复：
``_build_popup_lines`` 按选中项计算渲染窗口起始偏移
（``_completion_scroll_offset``），选中项越过首屏底部时窗口跟随（选中项
贴底），回首屏内时窗口回顶。本测试锁定滚动行为与 ``_completion_scroll_offset``
纯函数语义。

本测试锁定：选中行高亮背景 ``Style(fg=15, bg=237)`` 生效（局部 sel_bg）、
模块级 ``sel_bg`` 已删除（负向断言）、窄屏截断回归（每行宽 <= width）、
候选超屏时选中项自动滚动到可见区域（分栏/非分栏）。
"""

from __future__ import annotations

import pytest

import src.tui.app._popup_builder as pb
from src.tui.app._popup_builder import _build_popup_lines
from src.tui.core.style import Style


class _CompletionStub:
    """CompletionState 最小鸭子类型（复用 test_input_metrics 桩结构）。"""

    def __init__(self, items, descriptions=None, selected=0, visible=True,
                 split_desc=True, texts=None, types=None, title="", match_prefix="",
                 locked_height=0):
        self.items = items
        self.descriptions = descriptions or []
        self.selected = selected
        self.visible = visible
        self.split_desc = split_desc
        self.texts = texts if texts is not None else items
        self.types = types or []
        self.title = title
        self.match_prefix = match_prefix or ""
        self.locked_height = locked_height


def _sel_line(lines):
    """返回选中行（含 ▶ 前缀）。"""
    return next(l for l in lines if "▶" in l.plain)


# ── L5：选中行高亮背景（局部 sel_bg） ─────────────────────

def test_selected_line_highlight_bg_split_regression():
    """L5：分栏模式选中行高亮背景 Style(fg=15, bg=237) 生效（局部 sel_bg）。"""
    comp = _CompletionStub(
        items=["item0", "item1"], descriptions=["desc0", "desc1"],
        selected=0, split_desc=True, title="test",
    )
    lines = _build_popup_lines(comp, 40, now=0.0)
    sel = _sel_line(lines)
    assert any(r.style == Style(fg=15, bg=237) for r in sel.runs)


def test_selected_line_highlight_bg_non_split_regression():
    """L5：非分栏模式选中行高亮背景同样生效。"""
    comp = _CompletionStub(
        items=["item0", "item1"], descriptions=[],
        selected=0, split_desc=False, title="test",
    )
    lines = _build_popup_lines(comp, 40, now=0.0)
    sel = _sel_line(lines)
    assert any(r.style == Style(fg=15, bg=237) for r in sel.runs)


def test_module_level_sel_bg_removed_regression():
    """L5：模块级 ``sel_bg`` 已删除（负向断言——不再有被遮蔽的死代码）。"""
    assert not hasattr(pb, "sel_bg")


# ── L5 回归：窄屏截断 / 既有渲染 ─────────────────────────

def test_popup_lines_narrow_width_truncate_regression():
    """L5 回归：窄屏（width=10）弹窗每行宽 <= width（截断防御不破坏）。"""
    comp = _CompletionStub(
        items=["item0", "item1"], descriptions=["d0", "d1"],
        selected=0, split_desc=False, title="test", locked_height=3,
    )
    lines = _build_popup_lines(comp, 10, now=0.0)
    assert lines  # 有行输出
    for line in lines:
        assert line.width <= 10, f"行超宽: {line.plain!r} ({line.width})"


def test_popup_lines_basic_render_regression():
    """L5 回归：分栏弹窗渲染结构完整——标题 + 候选项 + 提示行。"""
    comp = _CompletionStub(
        items=["item0", "item1"], descriptions=["desc0", "desc1"],
        selected=0, split_desc=True, title="test", locked_height=3,
    )
    lines = _build_popup_lines(comp, 40, now=0.0)
    # 弹窗 = 标题行 + 候选项行（高度锁定 3 → n_rows=1）+ 提示行
    assert len(lines) >= 3
    assert lines[0].plain.startswith(" ▍")
    assert "test" in lines[0].plain
    assert "Tab" in lines[-1].plain  # 底部提示
    # 右栏说明 = descs[desc_sel=0]
    assert "desc0" in "\n".join(l.plain for l in lines)


# ── 滚动窗口：_completion_scroll_offset 纯函数 ─────────────

def test_scroll_offset_no_scroll_when_fits():
    """候选总数 ≤ 可见行数时不滚动（offset=0，全部可见）。"""
    assert pb._completion_scroll_offset(0, 3, 5) == 0
    assert pb._completion_scroll_offset(2, 3, 5) == 0
    assert pb._completion_scroll_offset(0, 5, 5) == 0
    assert pb._completion_scroll_offset(4, 5, 5) == 0
    # 边界：空候选 / 无可见行
    assert pb._completion_scroll_offset(0, 0, 5) == 0
    assert pb._completion_scroll_offset(0, 5, 0) == 0


def test_scroll_offset_first_screen_no_scroll():
    """选中项在首屏内（sel < n_rows）时窗口保持在顶部。"""
    assert pb._completion_scroll_offset(0, 20, 5) == 0
    assert pb._completion_scroll_offset(4, 20, 5) == 0


def test_scroll_offset_follows_selection_bottom():
    """选中项越过首屏底部时窗口跟随，选中项贴底（offset = sel - n_rows + 1）。"""
    assert pb._completion_scroll_offset(5, 20, 5) == 1
    assert pb._completion_scroll_offset(6, 20, 5) == 2
    assert pb._completion_scroll_offset(12, 20, 5) == 8
    assert pb._completion_scroll_offset(19, 20, 5) == 15


def test_scroll_offset_last_screen_clamped():
    """末屏不越界（offset ≤ total - n_rows）；回首屏内窗口回顶。"""
    assert pb._completion_scroll_offset(19, 20, 5) == 15
    assert pb._completion_scroll_offset(18, 20, 5) == 14
    # 向上回到首屏内 → 窗口回顶
    assert pb._completion_scroll_offset(3, 20, 5) == 0
    # 末项（sel=total-1）选中项仍可见（offset=total-n_rows，可见区末行为最后项）
    offset = pb._completion_scroll_offset(19, 20, 5)
    assert 0 <= offset <= 15
    assert offset + 5 - 1 == 19


# ── 滚动窗口：_build_popup_lines 渲染集成 ─────────────────

def test_popup_scrolls_selected_into_view_non_split(monkeypatch):
    """非分栏模式：/load 候选多时选中项超出首屏，弹窗滚动窗口使选中项可见。

    模拟：候选 20 项、可见候选项行数 7（_completion_item_rows=7 → 弹窗高
    9 → n_rows=7）；selected=12 已超出首屏——修复前 item12 不可见，修复后
    滚动窗口 [6,12] 使选中项贴底可见。
    """
    monkeypatch.setattr("src.tui._input_metrics._completion_item_rows", lambda: 7)
    items = [f"item{i}" for i in range(20)]
    comp = _CompletionStub(
        items=items, descriptions=[], selected=12, split_desc=False,
        title="test",
    )
    lines = _build_popup_lines(comp, 40, now=0.0)
    sel = _sel_line(lines)
    assert "item12" in sel.plain  # 选中项已滚动进可见区
    plain = "\n".join(l.plain for l in lines)
    assert "item0" not in plain  # 窗口已离开顶部
    assert "item6" in plain  # 窗口起点 = scroll = 6
    assert "item12" in plain  # 窗口终点 = 选中项（贴底）
    # 标题位置指示不变（13/20）
    assert "(13/20)" in lines[0].plain


def test_popup_scrolls_selected_into_view_split(monkeypatch):
    """分栏模式：候选多时选中项超出首屏，弹窗滚动窗口使选中项可见且右栏说明跟随。"""
    monkeypatch.setattr("src.tui._input_metrics._completion_item_rows", lambda: 7)
    items = [f"item{i}" for i in range(20)]
    descs = [f"desc{i}" for i in range(20)]
    comp = _CompletionStub(
        items=items, descriptions=descs, selected=12, split_desc=True,
        title="test",
    )
    lines = _build_popup_lines(comp, 80, now=0.0)
    sel = _sel_line(lines)
    assert "item12" in sel.plain  # 选中项已滚动进可见区
    plain = "\n".join(l.plain for l in lines)
    assert "item0" not in plain  # 窗口已离开顶部
    assert "item12" in plain
    assert "desc12" in plain  # 右栏说明跟随当前选中项


def test_popup_no_scroll_when_selection_in_first_screen(monkeypatch):
    """候选超屏但选中项仍在首屏内时不滚动（兼容既有行为）。"""
    monkeypatch.setattr("src.tui._input_metrics._completion_item_rows", lambda: 7)
    items = [f"item{i}" for i in range(20)]
    comp = _CompletionStub(
        items=items, descriptions=[], selected=2, split_desc=False,
        title="test",
    )
    lines = _build_popup_lines(comp, 40, now=0.0)
    sel = _sel_line(lines)
    assert "item2" in sel.plain
    plain = "\n".join(l.plain for l in lines)
    assert "item0" in plain  # 窗口仍在顶部


def test_popup_scroll_last_item_visible(monkeypatch):
    """末项选中（sel=total-1）时滚动窗口末行为最后项（贴底）。"""
    monkeypatch.setattr("src.tui._input_metrics._completion_item_rows", lambda: 7)
    items = [f"item{i}" for i in range(20)]
    comp = _CompletionStub(
        items=items, descriptions=[], selected=19, split_desc=False,
        title="test",
    )
    lines = _build_popup_lines(comp, 40, now=0.0)
    sel = _sel_line(lines)
    assert "item19" in sel.plain
    assert "(20/20)" in lines[0].plain
