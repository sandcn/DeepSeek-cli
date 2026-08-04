"""布局模糊不变量回归测试 — 随机组件树 + 行宽不变量 + 极端值防御。

覆盖渲染引擎的布局健壮性（随机生成组件树——嵌套容器/宽字符/极端尺寸/
flexWrap/justifyContent/alignItems——渲染后验证）：

  - 行宽不变量：所有行宽 <= 文档宽（超宽行破坏行级 diff/光标定位；
    E-ROW-OVERFLOW/E-FILL-OVERFLOW/E-OVERFLOW-GUARD 三层防线）；
  - 不崩溃：畸形 props（宽度/高度/border/padding/gap 传负值/字符串/超值）
    不抛异常中断整帧渲染（``_resolve_*`` 系列 try/except 兜底）；
  - 高度合理性：frame.height 非负。

模糊测试复用 ``Reconciler`` + ``render_frame`` 完整渲染管线（与
test_overflow_invariants.py 的行宽不变量语义一致，随机化覆盖更广）。
"""

from __future__ import annotations

import random

from src.tui.core.style import Style
from src.tui.ink import (
    h, TEXT, BOX, Row, Column, Stack, Center, Grid, ZStack, StyledRun,
)
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


def _rand_text(rng: random.Random) -> str:
    pool = [
        "中文中文中文", "a" * rng.randint(1, 30), "héllo wörld",
        "emoji👍测试", "  spaced  ", "", "中a中b中c",
        "测试文本更长一些用于换行行为验证",
    ]
    return rng.choice(pool)


def _rand_tree(rng: random.Random, depth: int = 0):
    """随机生成组件树（深度受限；畸形 props 随机注入）。"""
    if depth > 4 or rng.random() < 0.45:
        style = None
        if rng.random() < 0.3:
            style = Style(fg=rng.randint(0, 255))
        return h(TEXT, {"styled": [StyledRun(_rand_text(rng), style)]})
    comp = rng.choice([BOX, Row, Column, Stack, Center, Grid, ZStack])
    props: dict = {}
    if rng.random() < 0.25:
        props["width"] = rng.choice([0, 1, -5, 9999, "150%", "abc"])
    if rng.random() < 0.2:
        props["height"] = rng.choice([0, 1, -3, 100, "50%"])
    if rng.random() < 0.25:
        b = rng.choice([0, 1, 2, -1, "x"])
        props["border"] = 1 if b == "x" else b
    if rng.random() < 0.2:
        p = rng.choice([0, 1, 3, -2, "p"])
        props["padding"] = 1 if p == "p" else p
    if rng.random() < 0.15:
        g = rng.choice([0, 1, 2, -1, "g"])
        props["gap"] = 1 if g == "g" else g
    if rng.random() < 0.1:
        props["flexWrap"] = "wrap"
    if rng.random() < 0.1:
        props["flexDirection"] = rng.choice(["row", "column"])
    if rng.random() < 0.1:
        props["justifyContent"] = rng.choice(
            ["flex-start", "center", "flex-end", "space-between", "space-around"],
        )
    if rng.random() < 0.1:
        props["alignItems"] = rng.choice(["stretch", "center", "flex-end"])
    n = rng.randint(0, 4)
    children = [_rand_tree(rng, depth + 1) for _ in range(n)]
    return h(comp, props, children)


class TestLayoutFuzzInvariants:
    """随机组件树：行宽不变量 + 不崩溃 + 高度合理。"""

    def test_random_trees_width_invariant(self):
        for seed in range(60):
            rng = random.Random(seed * 7919 + 13)
            width = rng.choice([1, 5, 10, 40, 80])
            el = _rand_tree(rng)
            r = Reconciler()
            root = r.create_root()
            r.render(root, el, width, 40)
            frame = render_frame(root, width)
            # 行宽不变量
            for ln in frame.lines:
                assert ln.width <= width, (
                    f"seed={seed} width={width} 行宽超限: {ln.width} {ln.plain[:60]!r}"
                )
            assert frame.height >= 0, f"seed={seed} 负高度: {frame.height}"

    def test_random_trees_no_crash(self):
        """畸形 props（负值/字符串/超值）不抛异常中断整帧渲染。"""
        for seed in range(60):
            rng = random.Random(seed * 104729 + 7)
            width = rng.choice([1, 8, 40])
            el = _rand_tree(rng)
            r = Reconciler()
            root = r.create_root()
            r.render(root, el, width, 40)  # 不抛异常即通过
            render_frame(root, width)

    def test_extreme_wide_nested(self):
        """深嵌套 + 超长文本 + 窄屏：不崩溃且行宽受控。"""
        el = h(Column, {"width": 20}, [
            h(BOX, {"border": 1}, [
                h(Row, None, [
                    h(TEXT, {"children": "x" * 50}),
                    h(TEXT, {"children": "中文" * 10}),
                    h(Stack, None, [
                        h(TEXT, {"children": "y" * 40}),
                        h(Column, {"width": 5}, [h(TEXT, {"children": "z" * 30})]),
                    ]),
                ]),
            ]),
            h(Grid, {"width": 15}, [
                h(TEXT, {"children": "a" * 20}),
                h(TEXT, {"children": "b" * 20}),
                h(TEXT, {"children": "c" * 20}),
            ]),
        ])
        r = Reconciler()
        root = r.create_root()
        r.render(root, el, 20, 40)
        frame = render_frame(root, 20)
        for ln in frame.lines:
            assert ln.width <= 20, f"行宽超限: {ln.width} {ln.plain[:60]!r}"


__all__ = ["TestLayoutFuzzInvariants", "_rand_tree"]
