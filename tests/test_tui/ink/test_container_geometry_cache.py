"""容器几何解析缓存测试（布局性能优化：padding/border/gap 解析复用）。

``_container_geometry(fiber)`` 按「props 引用 + 原始值快照」缓存解析结果：
无变化帧跳过重复 ``props.get`` + int/异常解析；props 引用或原始值变化
（含原地修改）时重新解析，正确性不依赖引用稳定性。
"""

from __future__ import annotations

from src.tui.ink import BOX, TEXT, h
from src.tui.ink import _layout_measure as LM
from src.tui.ink.fiber import Fiber
from tests.test_tui.ink._harness import Harness


def _fiber(props: dict) -> Fiber:
    return Fiber("host", type="box", props=props)


def test_geometry_cache_hits_without_reparse(monkeypatch):
    calls = []
    real = LM._resolve_padding

    def _spy(fiber):
        calls.append(1)
        return real(fiber)

    monkeypatch.setattr(LM, "_resolve_padding", _spy)
    fiber = _fiber({"padding": 2, "border": 1, "gap": 3})
    first = LM._container_geometry(fiber)
    second = LM._container_geometry(fiber)
    assert first == second
    assert first is second          # 缓存命中返回同一结果对象
    assert len(calls) == 1          # padding 只解析一次


def test_geometry_cache_invalidated_on_props_replacement(monkeypatch):
    calls = []
    real = LM._resolve_padding

    def _spy(fiber):
        calls.append(1)
        return real(fiber)

    monkeypatch.setattr(LM, "_resolve_padding", _spy)
    fiber = _fiber({"padding": 2})
    LM._container_geometry(fiber)
    fiber.props = {"padding": 5}
    result = LM._container_geometry(fiber)
    assert len(calls) == 2
    assert result[0] == 5           # 新 padding 生效


def test_geometry_cache_invalidated_on_inplace_mutation(monkeypatch):
    calls = []
    real = LM._resolve_padding

    def _spy(fiber):
        calls.append(1)
        return real(fiber)

    monkeypatch.setattr(LM, "_resolve_padding", _spy)
    props = {"padding": 1, "border": 0, "gap": 0}
    fiber = _fiber(props)
    first = LM._container_geometry(fiber)
    props["border"] = 2                      # 原地修改（引用不变）
    second = LM._container_geometry(fiber)
    assert len(calls) == 2
    assert second[4] == 2 and first[4] == 0  # border 重新解析


def test_geometry_column_row_gap_override():
    fiber = _fiber({"gap": 1, "columnGap": 3, "rowGap": 4})
    pad_l, pad_r, pad_t, pad_b, border, margin, col_gap, row_gap = LM._container_geometry(fiber)
    assert col_gap == 3 and row_gap == 4

    fiber2 = _fiber({"gap": 2})
    assert LM._container_geometry(fiber2)[6:] == (2, 2)


def test_geometry_invalid_values_fall_back():
    fiber = _fiber({"padding": "x", "border": None, "margin": object(), "gap": "y"})
    pad_l, pad_r, pad_t, pad_b, border, margin, col_gap, row_gap = LM._container_geometry(fiber)
    assert border == 0
    assert isinstance(pad_l, int) and isinstance(margin, int)
    assert col_gap == row_gap


def test_geometry_cache_reused_across_render_frames():
    harness = Harness(30)

    def _build():
        return h(BOX, {"padding": 1, "border": 1, "gap": 1},
                 h(TEXT, {"children": "x"}))

    harness.render(_build())
    root = harness.root
    # 找到 BOX host fiber，确认其几何缓存已建立且复用同一结果对象
    stack = [root]
    box_fiber = None
    while stack:
        node = stack.pop()
        while node is not None:
            if getattr(node, "type", None) == "box":
                box_fiber = node
            if node.child is not None:
                stack.append(node.sibling)
                node = node.child
            else:
                node = node.sibling
    assert box_fiber is not None
    cached_before = box_fiber._geom_cache
    assert cached_before is not None
    harness.render(_build())
    assert box_fiber._geom_cache is cached_before   # 同 props 值 → 缓存对象保留
