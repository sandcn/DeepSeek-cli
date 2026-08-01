"""ChatView — 聊天块渲染组件。

将 AppModel.blocks 中每个块的 AnsiLine 行渲染为 Text 元素。
块类型渲染差异：
  - reasoning：dim 斜体前缀「…」
  - user：保持原样（已含 > 图标）
  - 其余：保持原样
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, StyledRun
from src.renderer.ansi.helpers import Run

_S_REASONING = Style(fg=242, italic=True)


def _to_styled_runs(line) -> list[StyledRun]:
    """AnsiLine → ink StyledRun 列表（Run.style 直接复用）。"""
    runs = getattr(line, "runs", None)
    if runs is None:
        # 兼容纯文本行
        return [StyledRun(str(line), None)]
    return [StyledRun(r.text, r.style) for r in runs if r.text]


def _block_styled_lines(block) -> list[list[StyledRun]]:
    """将块的行转为 styled run 列表（块级样式叠加）。"""
    kind = block.kind
    out: list[list[StyledRun]] = []
    for line in block.lines:
        runs = _to_styled_runs(line)
        if kind == "reasoning" and runs:
            # 推理行叠加 dim/italic 基础样式
            merged = [StyledRun(r.text, (r.style or Style()).merge(_S_REASONING)) for r in runs]
            out.append(merged)
        else:
            out.append(runs)
    return out


def ChatView(props) -> object:
    """ChatView 组件：渲染全部聊天块。"""
    model = props["model"]
    children = []
    for block in model.blocks:
        for runs in _block_styled_lines(block):
            children.append(h(TEXT, {"styled": runs}))
    return h(BOX, None, children)


__all__ = ["ChatView"]
