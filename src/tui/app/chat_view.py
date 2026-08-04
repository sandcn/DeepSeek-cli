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

from src.tui.app.model import _role_header_line
from src.tui.core.style import Style
from src.tui.ink import h, TEXT, StyledRun, register_host, Column
from .tool_card import ToolCard
from .subagent_panel import use_subagent_children

_S_REASONING = Style(fg=242)

#: 开放块 live 渲染行数上限（PERF-7 防御）：未提交尾超过该行数时只渲染
#: 最后 N 行（对齐终端 tail 语义）——content/reasoning 块被未提交工具卡
#: 夹住无法增量提交（BUG-4 连续窗口守卫）时，未提交尾随流式持续增长，
#: 每帧全量渲染 O(未提交尾) 导致渲染线程卡顿。限制后中间行暂不显示，
#: 块关闭提交（commit_block）时全部行进入 committed_lines 完整显示。
#: 工具卡不适用（边框渲染依赖 start==0 顶边框逻辑，且自身有 64 行增量
#: 提交阈值）。
_LIVE_TAIL_LINES = 64


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
        ——冻结语义（dim）与即时渲染（fg=242）不同，保持即时路径。
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
        #   时 committed_line_count=0 → 未提交部分=全量）。
        # ★ BUG-69（review 方向，渲染行数超限）：start（ChatView 传入的
        #   live_start）可能被 ``_LIVE_TAIL_LINES`` 截断到 > committed_line_count
        #   ——旧实现 ``cache[0:]`` 恒从头返回（start 参数被忽略）→ 冻结尾超过
        #   截断上限时**整段未提交尾全部渲染**（_LIVE_TAIL_LINES 防御失效，
        #   大尾块每帧全量重建）；且 ChatView 行 key（``live_start + row``）与
        #   实际渲染行错位 → 调和器复用错 fiber → 换行缓存 miss。修复：按
        #   ``start - committed_line_count`` 偏移切片——start==committed_line_count
        #   （正常路径）行为不变（cache[0:]）；start 被截断时只渲染最后
        #   ``len(cache)-offset`` 行（对齐 _LIVE_TAIL_LINES 语义）。偏移恒
        #   >=0（live_start 初始为 committed_line_count，截断只增不减），
        #   max(0,...) 仅防御。
        offset = max(0, start - block.committed_line_count)
        return [line.runs for line in cache[offset:]]
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
                # 推理行叠加 dim 基础样式（不斜体）
                runs = [StyledRun(r.text, (r.style or Style()).merge(_S_REASONING)) for r in runs]
            open_cache[line] = runs
        out.append(runs)
    return out


# ── committed-chat host：直接发射已缓存行 ────────────────


def _measure(fiber, avail_w):
    lines = fiber.props.get("lines") or []
    start = fiber.props.get("start", 0)
    end = fiber.props.get("end", len(lines))
    return (avail_w, max(0, end - start))


def _paint(fiber, canvas):
    box = fiber.layout_box
    lines = fiber.props.get("lines") or []
    start = fiber.props.get("start", 0)
    end = fiber.props.get("end", len(lines))
    n = max(0, end - start)
    if box.x == 0:
        # ★ 增量快路径（大历史 O(1)/帧）：committed 静态行跨帧身份复用——
        #   前缀缓存挂在 fiber（fiber 复用即命中，替换/重建自然失效）。
        #   方向1 步骤4（非顶部前缀缓存）：前缀键 ``(id(lines), n, box.y)``
        #   覆盖非顶部路径（box.y != 0）——非顶部同样维护 ``_committed_prefix``
        #   （命中即跳过画布重写）；render_frame 消费前缀时校验
        #   ``committed.layout_box.y == 0``——顶部才允许前缀复用，非顶部前缀
        #   与画布尾部重建偏移语义不一致时由 render_frame 回退全量（防御层，
        #   成本 O(1)）。
        #   ★ 阶段5（工具卡元素树化 + committed 区间发射）：ChatView 按块
        #   经 committed-chat 发射 ``committed_lines[start:end]`` 区间（前缀
        #   缓存键含 start/end——不同块区间独立缓存；块行原地 extend 增长时
        #   仅追加新增行）。``n`` 为区间行数（非整表行数）。
        key = (id(lines), n, box.y, box.w, start, end)
        cached = getattr(fiber, "_committed_prefix", None)
        if cached is not None and cached[0] == key:
            return  # 前缀未变：跳过画布重写（render_frame 复用缓存）
        if (
            cached is not None
            and cached[0][0] == key[0]
            and cached[0][2] == key[2]
            and cached[0][3] == key[3]
            and cached[0][4] == start
            and end > cached[0][5]
        ):
            # 块 committed 区间原地 extend（引用不变、end 增长）→ 仅追加新增行
            prefix = cached[1]
            prefix.extend(lines[start + cached[0][1]:end])
            all_ok = bool(cached[2]) and all(
                ln.width <= box.w for ln in lines[start + cached[0][1]:end]
            )
        else:
            prefix = list(lines[start:end])
            # ★ 行宽守卫（E-COMMITTED-OVERFLOW 防御）：reflow_committed 未执行
            #   /失败（终端宽度变化后 committed_lines 按旧宽度 wrap）时前缀含
            #   超宽行——render_frame 的前缀复用路径不经 E-OVERFLOW-GUARD
            #   （复用 Line 对象免截断），超宽行直接进入帧破坏行宽不变量。
            #   此处仅在缓存重建时 O(n) 检查一次（非每帧，缓存命中零开销），
            #   标记 all_ok=False 供 render_frame 回退全量路径（经 _to_line
            #   截断）。宽度基准 = committed-chat 布局宽度（fill 语义下=文档宽）。
            #   ★ 渲染错误（BUG-74）：缓存键含 box.w（见上方 key 构建）——
            #   修复前键为 ``(id(lines), n, box.y)``：终端宽度变化（reflow 前/
            #   失败/布局宽度与 model.width 不一致）时 id/lines 引用、行数、y
            #   均未变 → 缓存错误命中 → 旧宽度超宽行直接进入帧（本防线被
            #   缓存绕过）。key 含布局宽度后宽度变化强制重建并重新检查 all_ok。
            all_ok = bool(box.w > 0) and all(
                ln.width <= box.w for ln in prefix
            )
        fiber._committed_prefix = (key, prefix, all_ok)
        return
    # box.x != 0（缩进/padded）：逐行合并（保留已有边框/内容——修复前
    # ``canvas[row] = padded`` 整体替换：父容器边框（行内已写 cols x0/x1）
    # 被 padded 空格覆盖，缩进框内 committed 行丢失左/右边框）。
    for i in range(n):
        line = lines[start + i]
        row = box.y + i
        if 0 <= row < len(canvas):
            from src.tui.ink.components import _merge_line
            canvas[row] = _merge_line(canvas[row], box.x, line)


def register() -> None:
    """注册 committed-chat host 组件。"""
    register_host("committed-chat", _measure, _paint)


def ChatView(props) -> object:
    """ChatView 组件：缓存已提交块 + 渲染未提交块。

    ★ 阶段5（工具卡元素树化 + committed 区间发射）：
      - 工具卡（kind=="tool"）**不再走 committed_lines 行级发射**——由
        ``ToolCard`` 标准控件组件渲染（完整卡片从 block.lines 构建，实时+
        历史统一；``_tool_card_styled_lines`` 帧级缓存兜底，视觉输出等价）。
      - content/reasoning 已提交块经 committed-chat **区间发射**其在
        ``model.committed_lines`` 中的连续行（``[start, end)``，含卡片尾空行
        trailer）——前缀缓存按 ``(id(lines), start, end, ...)`` 命中，大历史
        O(块数)/帧（块数远小于行数，可接受）。
      - 未提交（live）块只渲染未提交尾（已提交行在 committed_lines 区间中）。

    未提交块的行给**索引 key**——调和器据此复用 fiber，换行缓存才能命中
    （否则无唯一 key → 每帧重建 → 开放大块整块重包裹，流式卡顿）。
    """
    model = props["model"]
    # ★ 截断宽度与布局宽度同源：优先用 props width（App 传入，= reconciler
    #   布局宽度），回退 model.width——修复 model.width 与实际布局宽度不一致
    #   （TTL 缓存 / resize 时序 / 默认 80）时，截断到 model.width 的行在
    #   布局按 box.w wrap，第二行只剩尾部边框字符（显示错乱）。
    width = props.get("width") or getattr(model, "width", 0)
    # ★ BUG-42（review 方向）：subagent 卡片子树按 ``(subagent_lines, width)``
    #   引用级 use_memo 缓存——修复前每帧重建全部 StyledRun/Element（活跃
    #   subagent 期间每帧重包裹）。无条件调用（hook 顺序稳定）。
    subagent_children = use_subagent_children(model, width)
    children = []
    # ★ 兼容路径（阶段5）：无 blocks 但 committed_lines 非空——渲染器级测试/
    #   外部手工构造模型（只有 committed_lines、无块结构）→ 整表发射（旧行为）。
    #   真实运行中 blocks 与 committed_lines 由 commit_block 同步维护（按块
    #   遍历渲染），不走此路径。
    if not model.blocks and model.committed_lines:
        children.append(h("committed-chat", {"lines": model.committed_lines}))
    # ★ 阶段5：遍历**所有块**（含已提交）——工具卡用 ToolCard 标准控件组件
    #   渲染；content/reasoning 已提交块经区间发射；未提交块渲染 live 尾。
    for block_idx, block in enumerate(model.blocks):
        if block.kind == "tool":
            # 工具卡片：标准控件组件（实时+历史统一从 block 状态渲染）
            children.append(h(ToolCard, {
                "key": f"chat-{block_idx}-tool",
                "block": block,
                "width": width,
            }))
            continue
        if block.committed_line_count > 0:
            offset = block.extra.get("_first_committed_offset", 0)
            n = block.committed_line_count
            end = offset + n
            # 卡片尾空行（trailer）：块完全提交且末行非空 → committed_lines[end]
            # 为该块的 trailer（下一个块 ``_first_committed_offset`` 覆盖其位置）
            if (block.closed and n >= len(block.lines) and block.lines
                    and getattr(block.lines[-1], "plain", "") != ""):
                end += 1
            children.append(h("committed-chat", {
                "key": f"chat-{block_idx}-c",
                "lines": model.committed_lines,
                "start": offset,
                "end": end,
            }))
        # 卡片角色头（live 路径）：块尚未有任何增量提交（committed_line_count
        # == 0）时在正文行前发射——已提交的头在 committed_lines 中，此处不再
        # 重复（互斥）。头独立 key ``chat-{block_idx}-h``（不与整数行号冲突）。
        if block.committed_line_count == 0:
            header_line = _role_header_line(block, model, width)
            if header_line is not None:
                children.append(h(TEXT, {
                    "key": f"chat-{block_idx}-h",
                    "styled": header_line.runs,
                }))
        # 开放块只渲染未提交尾（已增量提交的行在缓存中，不再重建）
        if block.committed_line_count < len(block.lines):
            # ★ 方向D 步骤14 + PERF-7（live 尾部截断）：content/reasoning 块被
            #   未提交工具卡夹住（无法增量提交）时未提交尾随流式增长——仅渲染
            #   最后 ``_LIVE_TAIL_LINES`` 行（中间行块关闭提交时经 committed_lines
            #   完整显示，非全屏流动模型无视觉跳变）。
            # ★ BUG-41（review 方向，性能）：行 key 用**块内绝对行号**（修复前
            #   ``row_in_block`` 从 committed_line_count 起重新编号——块被增量提交
            #   N 行后，旧 ``chat-{i}-0`` 的 fiber 改渲染绝对行号 N → 换行缓存/style
            #   缓存全部 miss，流式期间每帧重包裹）。绝对行号 key 在流式追加时保持
            #   稳定（已渲染行 key 不变，调和器复用 fiber；仅新增行创建新 fiber）。
            live_start = block.committed_line_count
            if len(block.lines) - live_start > _LIVE_TAIL_LINES:
                live_start = len(block.lines) - _LIVE_TAIL_LINES
            for row_in_block, runs in enumerate(
                _block_styled_lines(
                    block, live_start, width,
                )
            ):
                children.append(h(TEXT, {
                    "key": f"chat-{block_idx}-{live_start + row_in_block}",
                    "styled": runs,
                }))
    # 子代理活动卡片（并入消息流，对齐 Claude Code）：subagent_lines 为
    # _subagent_render 产出的逐 agent 卡片 ANSI 行，经 use_subagent_children
    # 缓存元素（原独立 SubAgentPanel 组件已移除）。
    if model.subagent_lines:
        children.extend(subagent_children)
    # ★ 阶段2（标准布局容器重构）：BOX(None) → Column（默认 flexDirection=
    #   column，输出与重构前一致；committed-chat host 子节点不受容器 type
    #   变化影响——容器仍是 "box" host）。
    return h(Column, None, children)


__all__ = ["ChatView", "register"]
