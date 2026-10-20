"""layout_children 缓存回归测试（B5 第一步）。

覆盖：
  1. 同一 child 链重复调用复用同一列表对象；
  2. 稳定列表调和（``_try_reuse_stable``）不失效缓存；
  3. 子链变化（新增）后缓存失效并重建。
"""

from __future__ import annotations

from src.tui.ink import TEXT, h
from src.tui.ink._layout_tree import layout_children
from src.tui.ink.fiber import TAG_ROOT, Fiber
from src.tui.ink.reconciler import Reconciler


def test_layout_children_cache_reuses_list():
    root = Fiber(TAG_ROOT, props={})
    rec = Reconciler()
    els = [h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})]
    rec._reconcile_children(root, els)

    first = layout_children(root)
    assert len(first) == 2
    assert layout_children(root) is first


def test_layout_children_cache_survives_stable_reconcile():
    root = Fiber(TAG_ROOT, props={})
    rec = Reconciler()
    els = [h(TEXT, {"children": "a"}), h(TEXT, {"children": "b"})]
    rec._reconcile_children(root, els)
    cached = layout_children(root)

    # 相同元素列表：_try_reuse_stable 命中，不失效缓存
    rec._reconcile_children(root, els)
    assert layout_children(root) is cached


def test_layout_children_cache_invalidated_on_change():
    root = Fiber(TAG_ROOT, props={})
    rec = Reconciler()
    els = [h(TEXT, {"children": "a"})]
    rec._reconcile_children(root, els)
    cached = layout_children(root)

    rec._reconcile_children(root, els + [h(TEXT, {"children": "b"})])
    rebuilt = layout_children(root)
    assert rebuilt is not cached
    assert len(rebuilt) == 2
