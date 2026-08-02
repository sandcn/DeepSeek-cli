"""ChatView — 聊天块渲染组件（增量渲染）。

静态历史（已提交块）渲染一次并缓存到 ``model.committed_lines``，每帧经
``committed-chat`` host **直接发射**（免逐字符重绘）；仅未提交块（当前
流式块）常规渲染。大历史下渲染成本 O(live + 新增)，不再 O(全部历史)。

卡片结构：committed_lines 为「卡片文档」（角色头 + 正文 + 空行），经
``committed-chat`` host 原样发射（``props["lines"]`` 即卡片行列表）。
未提交（live）块的**角色头**经 ``_role_header_line`` 在正文行之前发射
（仅 ``committed_line_count == 0`` 时——已增量提交的头已在 committed_lines，
互斥不重复）；正文行仍走 ``_block_styled_lines``（正文-only，不带头）。
content/tool 无角色头（content 对齐 Claude Code 无头回答；tool 由卡片顶边框
替代）——live content 直接渲染正文，live 工具顶边框经 ``_block_styled_lines``
工具短路（``_tool_card_styled_lines``）发射，与 committed 首次提交互斥。
"""

from __future__ import annotations

from src.renderer.ansi.helpers import Run
from src.tui.app.model import _role_header_line
from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, StyledRun, Line, register_host, use_memo

_S_REASONING = Style(fg=242, italic=True)


def _to_styled_runs(line) -> list[StyledRun]:
    """AnsiLine → ink StyledRun 列表（Run.style 直接复用）。"""
    runs = getattr(line, "runs", None)
    if runs is None:
        # 兼容纯文本行
        return [StyledRun(str(line), None)]
    return [StyledRun(r.text, r.style) for r in runs if r.text]


def _block_styled_lines(block, start: int = 0, width: int = 0) -> list[list[StyledRun]]:
    """将块的行（从 start 起）转为 styled run 列表（块级样式叠加）。

    分支顺序：
      - 关闭块（``_cached_ink_lines`` 非 None）直接复用冻结 ``Line.runs``
        引用（同一 runs 列表对象跨帧复用，免每帧 Style merge）；推理块除外
        ——冻结语义（dim italic）与即时渲染（fg=242 italic）不同，保持即时路径。
      - 工具块短路：直接返回 ``_tool_card_styled_lines`` 边框行（open 卡无
        底边框）。**不走** per-line ``_open_styled_cache``——卡片行数与输入行
        非 1:1（wrap/边框），缓存键失效。
      - 其余（reasoning/content）保持原 per-line styled 引用缓存逻辑。

    Args:
        block: 聊天块。
        start: 起始 AnsiLine 下标。
        width: 文档宽度（工具卡片边框宽度约束；调用方传 model.width）。
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
    if kind == "tool":
        # 开放工具卡：边框行（live 仅 committed_line_count==0 发顶边框——
        # 与 committed 首次提交互斥；start>0 已增量提交 → 仅主体行）
        from src.tui.app.model import _tool_card_styled_lines
        return _tool_card_styled_lines(block, width, start, None)
    slice_lines = block.lines[start:]
    # ★ 方向1（open 块 styled 引用缓存）：开放块行转换结果按**行对象**缓存于
    #   block——修复前每帧 ``_to_styled_runs`` 重建全部 StyledRun 列表（新对象
    #   每帧），``_measure`` 的 ``cache[0] is styled`` 身份快路径恒 miss →
    #   每帧 O(chars) style_fingerprint + 列表比较 + 潜在重包裹（大 open 块
    #   帧成本 O(全部行)）。缓存后同 line 引用返回同一 runs 列表对象 → 身份
    #   命中 → 零重建。行对象被 block.lines 持有，dict 随 block GC 自然释放。
    open_cache = getattr(block, "_open_styled_cache", None)
    if open_cache is None:
        open_cache = {}
        block._open_styled_cache = open_cache
    out: list[list[StyledRun]] = []
    for line in slice_lines:
        runs = open_cache.get(line)
        if runs is None:
            runs = _to_styled_runs(line)
            if kind == "reasoning" and runs:
                # 推理行叠加 dim/italic 基础样式
                runs = [StyledRun(r.text, (r.style or Style()).merge(_S_REASONING)) for r in runs]
            open_cache[line] = runs
        out.append(runs)
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
        #   方向1 步骤4（非顶部前缀缓存）：前缀键 ``(id(lines), n, box.y)``
        #   覆盖非顶部路径（box.y != 0）——非顶部同样维护 ``_committed_prefix``
        #   （命中即跳过画布重写）；render_frame 消费前缀时校验
        #   ``committed.layout_box.y == 0``——顶部才允许前缀复用，非顶部前缀
        #   与画布尾部重建偏移语义不一致时由 render_frame 回退全量（防御层，
        #   成本 O(1)）。
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
        # 卡片角色头（live 路径）：块尚未有任何增量提交（committed_line_count
        # == 0）时在正文行前发射——已提交的头在 committed_lines 中，此处不再
        # 重复（互斥）。头独立 key ``chat-{block_idx}-h``（不与整数行号冲突）。
        if block.committed_line_count == 0:
            header_line = _role_header_line(block, model, getattr(model, "width", 0))
            if header_line is not None:
                children.append(h(TEXT, {
                    "key": f"chat-{block_idx}-h",
                    "styled": header_line.runs,
                }))
        # 开放块只渲染未提交尾（已增量提交的行在缓存中，不再重建）
        for row_in_block, runs in enumerate(
            _block_styled_lines(
                block, block.committed_line_count, getattr(model, "width", 0),
            )
        ):
            children.append(h(TEXT, {
                "key": f"chat-{block_idx}-{row_in_block}",
                "styled": runs,
            }))
    # 子代理活动卡片（并入消息流，对齐 Claude Code）：subagent_lines 为
    # _subagent_render 产出的逐 agent 卡片 ANSI 行，经 _render_children 转换
    # 后追加到消息文档（原独立 SubAgentPanel 组件已移除）。
    if model.subagent_lines:
        from .subagent_panel import _render_children
        children.extend(_render_children(model, getattr(model, "width", 0)))
    return h(BOX, None, children)


__all__ = ["ChatView", "register"]
