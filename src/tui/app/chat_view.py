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
        # 冻结缓存：Line.runs 引用级复用（同一 runs 列表对象，跨帧不重建）。
        # ★ 方向4（增量提交协同）：冻结缓存即「未提交部分」（close_tool_box
        #   冻结自 committed_line_count 起；close_reasoning/close_content 关闭
        #   时 committed_line_count=0 → 未提交部分=全量）——``cache[0:]`` 从头
        #   返回（start 参数对冻结缓存无意义；修复前按 ``cache[start:]`` 切片，
        #   增量提交后 start=committed_line_count 越界返回空 → 尾部渲染丢失）。
        return [line.runs for line in cache[0:]]
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
    n = len(lines)
    if box.x == 0:
        # ★ 增量快路径（大历史 O(1)/帧）：committed 静态行跨帧身份复用——
        #   前缀缓存挂在 fiber（fiber 复用即命中，替换/重建自然失效）。
        #   仅 box.y==0（文档顶部、无重叠）才允许跳过画布重写：render_frame
        #   经缓存前缀 + 尾部重建 Frame。修复长回答 + 子代理期间每帧全量
        #   重建画布 → 渲染线程持续占用 CPU 100%。
        if box.y == 0:
            key = (id(lines), n, box.y)
            cached = getattr(fiber, "_committed_prefix", None)
            if cached is not None and cached[0] == key:
                return  # 前缀未变：跳过画布重写（render_frame 复用缓存）
            if (
                cached is not None
                and cached[0][0] == key[0]
                and cached[0][2] == key[2]
                and n > cached[0][1]
            ):
                # committed_lines 原地 extend（引用不变、长度增长）→ 仅追加新增行
                prefix = cached[1]
                prefix.extend(lines[cached[0][1]:])
            else:
                prefix = list(lines)
            fiber._committed_prefix = (key, prefix)
            return
        # 非顶部（box.y != 0）：直接引用缓存行（历史兼容，O(n) 每帧）
        for i, line in enumerate(lines):
            row = box.y + i
            if 0 <= row < len(canvas):
                canvas[row] = line
        return
    # box.x != 0（缩进/padded）：逐行重建（历史兼容，O(n) 每帧）
    for i, line in enumerate(lines):
        row = box.y + i
        if 0 <= row < len(canvas):
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
    # ★ 方向5（chat_view 复合 key）：开放块行 key 用「块索引 + 行内序号」
    #   复合（修复前 ``chat-{line_idx}`` 位置索引——流式追加使行号前移导致
    #   已渲染行重建）；block_idx = 块在 model.blocks 中的索引（块只追加、
    #   索引稳定）；row_in_block = 块内行号（已提交行不参与开放块渲染，
    #   未提交尾从 committed_line_count 起行号稳定）→ 流式追加新行时已渲染
    #   行 key 不变，调和器复用 fiber。
    for block_idx, block in enumerate(
        model.blocks[model.committed_count:], start=model.committed_count,
    ):
        # 开放块只渲染未提交尾（已增量提交的行在缓存中，不再重建）
        for row_in_block, runs in enumerate(
            _block_styled_lines(block, block.committed_line_count)
        ):
            children.append(h(TEXT, {
                "key": f"chat-{block_idx}-{row_in_block}",
                "styled": runs,
            }))
    return h(BOX, None, children)


__all__ = ["ChatView", "register"]
