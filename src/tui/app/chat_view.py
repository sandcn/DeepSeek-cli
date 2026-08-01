"""ChatView — 聊天块渲染组件（增量渲染）。

静态历史（已提交块）渲染一次并缓存到 ``model.committed_lines``，每帧经
``committed-chat`` host **直接发射**（免逐字符重绘）；仅未提交块（当前
流式块）常规渲染。大历史下渲染成本 O(live + 新增)，不再 O(全部历史)。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, StyledRun, Line, register_host, use_memo
from src.renderer.ansi.helpers import Run

_S_REASONING = Style(fg=242, italic=True)


def _to_styled_runs(line) -> list[StyledRun]:
    """AnsiLine → ink StyledRun 列表（Run.style 直接复用）。"""
    runs = getattr(line, "runs", None)
    if runs is None:
        # 兼容纯文本行
        return [StyledRun(str(line), None)]
    return [StyledRun(r.text, r.style) for r in runs if r.text]


def _block_styled_lines(block, start: int = 0) -> list[list[StyledRun]]:
    """将块的行（从 start 起）转为 styled run 列表（块级样式叠加）。

    方向D 步骤15：
      - 关闭块（``_cached_ink_lines`` 非 None）直接复用冻结 ``Line.runs``
        引用（同一 runs 列表对象跨帧复用，免每帧 Style merge）；推理块除外
        ——冻结语义（dim italic）与即时渲染（fg=242 italic）不同，保持即时路径。
      - 工具块标题行前置状态图标（running ● / done ✔ / fail ✖）。
    """
    kind = block.kind
    cache = getattr(block, "_cached_ink_lines", None)
    if cache is not None and kind != "reasoning":
        # 冻结缓存：Line.runs 引用级复用（同一 runs 列表对象，跨帧不重建）
        return [line.runs for line in cache[start:]]
    slice_lines = block.lines[start:]
    out: list[list[StyledRun]] = []
    for line in slice_lines:
        runs = _to_styled_runs(line)
        if kind == "reasoning" and runs:
            # 推理行叠加 dim/italic 基础样式
            merged = [StyledRun(r.text, (r.style or Style()).merge(_S_REASONING)) for r in runs]
            out.append(merged)
        else:
            out.append(runs)
    if kind == "tool" and start == 0 and out:
        # 开放工具块：标题前置状态图标（running ●；关闭块已在冻结缓存中）
        from src.tui.app.model import _tool_icon_runs
        icon = _tool_icon_runs(block)
        if icon:
            out[0] = icon + out[0]
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
    """ChatView 组件：缓存已提交块 + 渲染未提交块。

    未提交块的行给**索引 key**——调和器据此复用 fiber，换行缓存才能命中
    （否则无唯一 key → 每帧重建 → 开放大块整块重包裹，流式卡顿）。

    方向② 步骤6：committed-chat 部分 use_memo 缓存——``committed_lines``
    引用不变（模型无新提交）时返回同一 Element → reconciler 复用同一 props
    → host 调和跳过（免每帧重建 committed-chat 元素）；流式增量提交
    （committed_lines 变化）时 memo 失效重算。use_memo 须在所有条件分支前
    调用（hook 顺序不变式；ChatView 仅此一个 hook，无顺序风险）。
    """
    model = props["model"]
    committed_el = use_memo(
        lambda: h("committed-chat", {"lines": model.committed_lines}),
        (model.committed_lines,),
    )
    children = []
    if model.committed_lines:
        children.append(committed_el)
    line_idx = 0
    for block in model.blocks[model.committed_count:]:
        # 开放块只渲染未提交尾（已增量提交的行在缓存中，不再重建）
        for runs in _block_styled_lines(block, block.committed_line_count):
            children.append(h(TEXT, {"key": f"chat-{line_idx}", "styled": runs}))
            line_idx += 1
    return h(BOX, None, children)


__all__ = ["ChatView", "register"]
