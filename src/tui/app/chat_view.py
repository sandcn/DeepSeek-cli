"""ChatView — 聊天块渲染组件（增量渲染）。

静态历史（已提交块）渲染一次并缓存到 ``model.committed_lines``，每帧经
``committed-chat`` host **直接发射**（免逐字符重绘）；仅未提交块（当前
流式块）常规渲染。大历史下渲染成本 O(live + 新增)，不再 O(全部历史)。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, StyledRun, Line, register_host
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


# ── committed-chat host：直接发射已缓存行 ────────────────


def _measure(fiber, avail_w):
    lines = fiber.props.get("lines") or []
    return (avail_w, len(lines))


def _paint(fiber, canvas):
    box = fiber.layout_box
    lines = fiber.props.get("lines") or []
    for i, line in enumerate(lines):
        row = box.y + i
        if 0 <= row < len(canvas):
            if box.x == 0:
                canvas[row] = line  # 直接引用缓存行（免逐字符重绘）
            else:
                padded = Line()
                padded.append(" " * box.x)
                for run in line.runs:
                    padded.append_run(run)
                canvas[row] = padded


def register() -> None:
    """注册 committed-chat host 组件。"""
    register_host("committed-chat", _measure, _paint)


def ChatView(props) -> object:
    """ChatView 组件：缓存已提交块 + 渲染未提交块。"""
    model = props["model"]
    children = []
    if model.committed_lines:
        children.append(h("committed-chat", {"lines": model.committed_lines}))
    for block in model.blocks[model.committed_count:]:
        for runs in _block_styled_lines(block):
            children.append(h(TEXT, {"styled": runs}))
    return h(BOX, None, children)


__all__ = ["ChatView", "register"]
