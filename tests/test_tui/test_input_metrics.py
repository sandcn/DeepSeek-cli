"""输入度量 selected 钳制统一与类型防御测试（M4 + M5）。

修复背景（2026-08-15）：
  - M4（selected 钳制不一致）：``_completion_height`` 分栏分支按
    ``min(selected, len(descs)-1)`` 钳制，``_popup_builder._build_popup_lines``
    按 ``min(sel, len(items)-1)`` 再 ``min(len(descs)-1)``（desc_sel）钳制——
    descs/items 长度不齐（异常数据）时高度测量与绘制不一致 → 弹窗截断或
    底部空白。修复：钳制统一按 ``min(len(descs)-1, len(items)-1)``（与绘制
    desc_sel 语义同源）。
  - M5（无类型防御）：selected 非 int（None/str 外部注入）时
    ``min(selected, ...)`` 抛 TypeError。修复：``int()`` 归一化 + try/except
    回退 0（与 ``_popup_builder`` 一致）。
"""

from __future__ import annotations

import pytest

from src.tui._input_metrics import _completion_height
from src.tui.app._popup_builder import _build_popup_lines


class _CompletionStub:
    """CompletionState 最小鸭子类型（分栏说明模式字段）。"""

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


# ── M5：类型防御 ─────────────────────────────────────────

def test_completion_height_selected_none_regression():
    """M5：selected=None 不抛 TypeError，回退 0 计算高度。"""
    comp = _CompletionStub(
        items=["a", "b"], descriptions=["d1", "d2"], selected=None,
    )
    h = _completion_height(comp, 40)
    assert h >= 0
    assert h == 4  # max(n=2, desc_lines=1)+2 = 4（sel=0 → descs[0]）


def test_completion_height_selected_str_regression():
    """M5：selected="abc"（非 int 外部注入）不抛异常，回退 0。"""
    comp = _CompletionStub(
        items=["a", "b"], descriptions=["d1", "d2"], selected="abc",
    )
    h = _completion_height(comp, 40)
    assert h >= 0


def test_completion_height_selected_negative_clamp_regression():
    """M5+M4：selected=-5（越界负值）钳制到 0。"""
    comp = _CompletionStub(
        items=["a", "b"], descriptions=["d1", "d2"], selected=-5,
    )
    h = _completion_height(comp, 40)
    assert h == 4  # sel=0 → descs[0]


# ── M4：长度不齐钳制一致 ─────────────────────────────────

def test_completion_height_descs_shorter_than_items_regression():
    """M4：descs 长度 < items 且 selected 越界时按 min(len(descs)-1,
    len(items)-1) 钳制（descs[1] 说明行数计算高度，不越界不抛异常）。"""
    descs = ["desc0", "x" * 40]  # descs[1] 长 40 > desc_w=13 → 4 行
    comp = _CompletionStub(
        items=[f"item{i}" for i in range(5)],
        descriptions=descs,
        selected=4,  # 越界（descs 只有 2 项）→ 钳到 descs[1]
    )
    h = _completion_height(comp, 40)
    # sel = min(4, len(descs)-1=1, len(items)-1=4) = 1 → desc_lines(descs[1])
    # = 4 行 → need = max(n=5, 4)+2 = 7
    assert h == 7


def test_completion_height_matches_popup_desc_sel_regression():
    """M4：descs 长度 < items 且 selected 越界时，高度测量与弹窗绘制
    desc_sel 同源——弹窗候选项行数 = _completion_height - 2，选中行高亮 +
    右栏显示 descs[desc_sel]（selected 钳制一致，无底部空白/截断）。"""
    comp = _CompletionStub(
        items=[f"item{i}" for i in range(5)],
        descriptions=["desc0", "desc1"],
        selected=4,
        split_desc=True,
        title="test",
    )
    h = _completion_height(comp, 40)
    lines = _build_popup_lines(comp, 40, now=0.0)
    # 弹窗 = 标题 + 候选项 + 提示；候选项行数 = 高度 - 2
    assert len(lines) == h
    assert len(lines) - 2 == h - 2
    # 选中行（▶）位于候选项区最后一行（sel 钳到 items-1=4）
    assert "▶" in lines[5].plain
    # 右栏说明 = descs[desc_sel]：desc_sel = min(sel=4, len(descs)-1=1) = 1
    assert "desc1" in lines[1].plain
    # 归一化回写（_build_popup_lines 写回 completion.selected = sel）
    assert comp.selected == 4
