"""user_select 弹窗选项重复渲染回归测试（BUG：控件 renderItem 返回整列表）。

背景（2026-08-16 用户报障）：UserSelectPopup 控件化（阶段6 方案B）后，
``_split_renderer`` / ``_regular_renderer`` 在 SelectInput/MultiSelect 的
renderItem 中循环渲染**整个选项列表**——控件对每个 item 调用一次
renderItem（每 item 返回 total 行）→ 弹窗选项重复 total 份（如 4 选项
显示 4×4=16 行，且每份内 ▶ 高亮同现）。

修复：renderItem 按单 item 语义只构建 item 索引对应的一行；行数预算由
控件 ``limit`` 折算（超屏防护保留，交互仍可导航隐藏项）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app._state_types import UserSelectState
from src.tui.app.user_select import UserSelectPopup
from src.tui.ink import h
from src.tui.ink.components import render_frame
from src.tui.ink.reconciler import Reconciler


def _us(**kw):
    base = dict(
        visible=True, seq=1, title="测试", options=["A", "B", "C", "D"],
        default_options=["A"], selected=0, option_descriptions=[],
    )
    base.update(kw)
    return UserSelectState(**base)


def _popup_frame(props, width: int = 80, height: int = 24):
    """调和 + 布局 + 渲染 UserSelectPopup，返回 (reconciler, root, frame)。"""
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h(UserSelectPopup, props), width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    return [ln.plain for ln in frame.lines]


# ═══════════════════════════════════════════════════════════
# BUG：选项重复渲染（用户报障场景）
# ═══════════════════════════════════════════════════════════

def test_split_no_duplicate_rows():
    """分栏说明模式：4 选项 + 4 说明 → 弹窗 6 行（1 标题 + 4 选项 + 1 提示）。

    复现用户场景：选项带说明（option_descriptions），当前高亮第 2 项——
    修复前每个 item 渲染整份选项列表（4×4=16 选项行，每份内 ▶ 同现）。
    """
    us = _us(
        options=["第一人称", "生物模型", "第三人称", "整个世界方块"],
        option_descriptions=["d1", "d2", "d3", "d4"],
        selected=1,
    )
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 6, f"应为 1 标题 + 4 选项 + 1 提示 = 6 行: {lines}"

    # 每行对应各自选项（不重复）
    assert "第一人称" in lines[1]
    assert "生物模型" in lines[2]
    assert "第三人称" in lines[3]
    assert "整个世界方块" in lines[4]
    # ▶ 只在当前高亮行（第 2 项）
    assert "▶" in lines[2]
    assert "▶" not in lines[1] and "▶" not in lines[3] and "▶" not in lines[4]


def test_regular_single_no_duplicate():
    """普通模式单选：3 选项（无说明）→ 弹窗 5 行（1 标题 + 3 选项 + 1 提示）。"""
    us = _us(options=["A", "B", "C"], selected=1)
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 5, f"应为 1 标题 + 3 选项 + 1 提示 = 5 行: {lines}"
    assert "A" in lines[1] and "B" in lines[2] and "C" in lines[3]
    assert "▶" in lines[2]
    assert "▶" not in lines[1] and "▶" not in lines[3]


def test_regular_multi_no_duplicate():
    """普通模式多选：3 选项（勾选 0/2）→ 5 行，勾选标记 ● 正确分布。"""
    us = _us(options=["A", "B", "C"], multi_select=True, checked=[0, 2])
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 5, f"应为 1 标题 + 3 选项 + 1 提示 = 5 行: {lines}"
    assert "●" in lines[1] and "●" in lines[3], "勾选项应显示 ●"
    assert "○" in lines[2], "未勾选项应显示 ○"


# ═══════════════════════════════════════════════════════════
# 高亮跟随选中项（导航回归）
# ═══════════════════════════════════════════════════════════

def test_split_highlight_follows_selected():
    """分栏模式：selected 变化 → ▶ 跟随到对应行（单选高亮语义保持）。"""
    us1 = _us(option_descriptions=["d1", "d2", "d3"], selected=0)
    _, _, frame1 = _popup_frame({"model": SimpleNamespace(user_select=us1), "width": 80})
    lines1 = _frame_plain(frame1)
    assert "▶" in lines1[1] and "▶" not in lines1[2]

    us2 = _us(option_descriptions=["d1", "d2", "d3"], selected=1)
    _, _, frame2 = _popup_frame({"model": SimpleNamespace(user_select=us2), "width": 80})
    lines2 = _frame_plain(frame2)
    assert "▶" in lines2[2] and "▶" not in lines2[1]


def test_regular_highlight_follows_selected():
    """普通模式：selected 变化 → ▶ 跟随到对应行。"""
    us1 = _us(options=["A", "B", "C"], selected=0)
    _, _, frame1 = _popup_frame({"model": SimpleNamespace(user_select=us1), "width": 80})
    lines1 = _frame_plain(frame1)
    assert "▶" in lines1[1] and "▶" not in lines1[2]

    us2 = _us(options=["A", "B", "C"], selected=2)
    _, _, frame2 = _popup_frame({"model": SimpleNamespace(user_select=us2), "width": 80})
    lines2 = _frame_plain(frame2)
    assert "▶" in lines2[3] and "▶" not in lines2[2]


# ═══════════════════════════════════════════════════════════
# 分栏多选勾选标记（is_checked 驱动）
# ═══════════════════════════════════════════════════════════

def test_split_multi_checkmark_from_is_checked():
    """分栏多选：勾选标记经控件 is_checked 驱动（初始 checked=[0] → ●○○）。"""
    us = _us(
        options=["A", "B", "C"],
        option_descriptions=["d1", "d2", "d3"],
        multi_select=True, checked=[0],
    )
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 5, f"应为 1 标题 + 3 选项 + 1 提示 = 5 行: {lines}"
    assert "●" in lines[1] and "○" in lines[2] and "○" in lines[3]


# ═══════════════════════════════════════════════════════════
# 超屏防护 limit
# ═══════════════════════════════════════════════════════════

def test_split_limit_budget(monkeypatch):
    """分栏模式：大量选项时控件 limit 截断显示（行数预算）。"""
    monkeypatch.setattr(
        "src.tui.app.user_select._popup_item_rows", lambda: 2,
    )
    us = _us(options=["A", "B", "C", "D", "E"], option_descriptions=["d"] * 5)
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 4, f"limit=min(total, budget)=2 → 1+2+1=4 行: {lines}"
    assert "A" in lines[1] and "B" in lines[2]
    assert "C" not in lines[1] and "C" not in lines[2]


def test_regular_limit_budget(monkeypatch):
    """普通模式：大量选项时控件 limit 按行数预算折算。"""
    monkeypatch.setattr(
        "src.tui.app.user_select._popup_item_rows", lambda: 2,
    )
    us = _us(options=["A", "B", "C", "D", "E"])
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    assert len(lines) == 4, f"行数预算折算 limit=2 → 1+2+1=4 行: {lines}"


def test_regular_limit_budget_multiline(monkeypatch):
    """普通模式多行 item：limit 按实际行数累计（预算 3 → 仅容纳 1 项 × 2 行）。

    ★ 2026-08-18（editmsg 拆分）：/editmsg 多行 option_lines 已移除——本
    测试改为单行 item 场景：预算 3 → 可见 3 项。
    """
    monkeypatch.setattr(
        "src.tui.app.user_select._popup_item_rows", lambda: 3,
    )
    us = _us(options=["A", "B", "C", "D"])
    _, _, frame = _popup_frame({"model": SimpleNamespace(user_select=us), "width": 80})
    lines = _frame_plain(frame)
    # 单行 item：预算 3 → limit=3 → 1+3+1=5 行（第 4 项隐藏）
    assert len(lines) == 5, f"3 项可见 → 1+3+1=5 行: {lines}"
    assert "A" in lines[1] and "B" in lines[2] and "C" in lines[3]
    assert "D" not in lines
