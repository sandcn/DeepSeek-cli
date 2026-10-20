"""布局等价回归基线（B5 子树缓存前置安全网）。

在引入子树布局缓存前，先用 ``renderToString`` 锁定一组布局结果：
  - 嵌套 column/row 容器的行内容与顺序；
  - 重复渲染结果完全一致（缓存不得改变输出）；
  - 固定宽度下的换行结果。
"""

from __future__ import annotations

from src.tui.ink import BOX, TEXT, h, renderToString


def _tree():
    return h(
        BOX,
        {"flexDirection": "column", "width": 20},
        [
            h(TEXT, {"children": "hello"}),
            h(
                BOX,
                {"flexDirection": "row"},
                [
                    h(TEXT, {"children": "a"}),
                    h(TEXT, {"children": "b"}),
                ],
            ),
        ],
    )


def test_nested_layout_stable_across_repeated_render():
    first = renderToString(_tree(), {"columns": 20})
    second = renderToString(_tree(), {"columns": 20})
    assert first == second
    assert "hello" in first
    assert "ab" in first


def test_fixed_width_wrap_layout():
    tree = h(
        BOX,
        {"flexDirection": "column", "width": 6},
        [h(TEXT, {"children": "abcdefgh"})],
    )
    out = renderToString(tree, {"columns": 6})
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    assert lines == ["abcdef", "gh"]
