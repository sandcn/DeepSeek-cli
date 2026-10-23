"""迭代调和（显式工作栈）不改变 effect 提交顺序（2026-10-23 回归测试）。"""

from __future__ import annotations

from src.tui.ink import Box, Text, h
from src.tui.ink.hooks import use_effect, useLayoutEffect
from src.tui.ink.reconciler import Reconciler


def test_effects_commit_child_before_parent() -> None:
    order: list[str] = []

    def Child(_props):
        useLayoutEffect(lambda: order.append("child-layout"), [])
        use_effect(lambda: order.append("child-passive"), [])
        return h(Text, None, "c")

    def Parent(_props):
        useLayoutEffect(lambda: order.append("parent-layout"), [])
        use_effect(lambda: order.append("parent-passive"), [])
        return h(Box, None, h(Child, None))

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, h(Parent, None), 20, 10)

    # React 语义：layout effects 先于 passive；子先于父（后序提交）。
    assert order == [
        "child-layout",
        "parent-layout",
        "child-passive",
        "parent-passive",
    ]
