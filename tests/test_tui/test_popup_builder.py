"""弹窗 sel_bg 死代码清理测试（L5）。

修复背景（2026-08-15 L5）：``_popup_builder.py`` 模块级 ``sel_bg = 237``
（原 L35）与 ``_build_popup_lines`` 函数内局部 ``sel_bg = 237``（原 L247）
重复——模块级被局部遮蔽且无外部引用（已 search 确认仅本文件内
L272/L313 使用局部）。修复：删除模块级定义（死代码清理）。

本测试锁定：选中行高亮背景 ``Style(fg=15, bg=237)`` 生效（局部 sel_bg）、
模块级 ``sel_bg`` 已删除（负向断言）、窄屏截断回归（每行宽 <= width）。
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
