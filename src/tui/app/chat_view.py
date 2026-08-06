"""ChatView — 聊天块渲染组件（增量渲染）。

静态历史（已提交块）渲染一次并缓存到 ``model.committed_lines``，每帧经
``committed-chat`` host **直接发射**（免逐字符重绘）；仅未提交块（当前
流式块）常规渲染。大历史下渲染成本 O(live + 新增)，不再 O(全部历史)。

卡片结构：committed_lines 为「卡片文档」（角色头 + 正文 + 空行），经
``committed-chat`` host 原样发射（``props["lines"]`` 即卡片行列表）。
未提交（live）块的**角色头**经 ``_role_header_line`` 在正文行之前发射
（仅 ``committed_line_count == 0`` 时——已增量提交的头已在 committed_lines，
互斥不重复）；正文行仍走 ``_block_styled_lines``（正文-only，不带头）。
content/tool 无角色头（content 对齐 Claude Code 无头回答；tool 由卡片标题行
替代）——live content 直接渲染正文，live 工具块经 ``ToolCard`` 组件
（React Ink 组件化，内部 ``tool_card_lines`` 行）发射，与 committed
首次提交互斥。
"""

from __future__ import annotations

from src.tui.app.model import _role_header_line
from src.tui.app.toolcard import ToolCard
from src.tui.core.style import Style
from src.tui.ink import h, TEXT, StyledRun, StaticLines, use_memo, Column, FRAGMENT
from .subagent_panel import SubAgentCard

_S_REASONING = Style(fg=242)

#: 空状态欢迎提示（2026-08-05 美化）：模块级单例 styled runs——✦ 强调青 +
#: 欢迎文本亮白 + 操作提示 dim。静态样式（无时间基呼吸——空状态渲染循环
#: 空闲跳过，避免每帧重建）。
#: ★ BEAUTY-25（2026-08-05 体验动效）：**活跃期**（模型已配置 + 流式/工具
#:   执行中）欢迎行 ✦ 图标呼吸化——渲染循环已因动画状态持续 10Hz 推进，零
#:   额外渲染成本；空闲期（无动画状态）回退本静态单例（CPU ~0）。
_WELCOME_STYLED = [
    StyledRun("\u2726 ", Style(fg=45, bold=True)),
    StyledRun("欢迎使用 DeepSeek CLI", Style(fg=252)),
    StyledRun("  \u00b7  ", Style(fg=242)),
    StyledRun("/help 查看命令 · Ctrl+N 切换模型 · Tab 补全", Style(fg=242)),
]

#: 欢迎行 ✦ 呼吸色域（亮青 45 邻域脉动，8s 周期——与工具卡标题/模型名呼吸同步）
_WELCOME_DOT_LO = 45
_WELCOME_DOT_HI = 61
_WELCOME_DOT_PERIOD = 8.0


def _welcome_element(model, width: int) -> object:
    """空状态欢迎行元素（活跃期 ✦ 呼吸，空闲静态单例）。

    ★ BEAUTY-25（体验动效）：渲染循环仅在动画状态（status_active/工具运行/
    弹窗等，见 session._needs_animation）下持续 10Hz 推进——欢迎行 ✦ 图标
    仅在活跃期呼吸（渲染已推进，零额外成本）；空闲期返回模块级静态单例
    ``_WELCOME_STYLED``（同引用跨帧复用，TEXT ``_wrap_cache`` 引用级命中
    零重建）。返回 ``(children, key)``——ChatView 直接 ``h(TEXT, ...)``。
    """
    st = getattr(model, "status", None)
    active = bool(st is not None and getattr(st, "status_active", False))
    if active:
        from src.tui.app._theme import time_glow
        dot = time_glow(_WELCOME_DOT_LO, _WELCOME_DOT_HI, _WELCOME_DOT_PERIOD)
        styled = [
            StyledRun("\u2726 ", Style(fg=dot, bold=True)),
            StyledRun("欢迎使用 DeepSeek CLI", Style(fg=252)),
            StyledRun("  \u00b7  ", Style(fg=242)),
            StyledRun("/help 查看命令 · Ctrl+N 切换模型 · Tab 补全", Style(fg=242)),
        ]
    else:
        styled = _WELCOME_STYLED
    return h(TEXT, {"key": "welcome", "styled": styled, "height": 1})

#: 开放块 live 渲染行数上限（PERF-7 防御）：未提交尾超过该行数时只渲染
#: 最后 N 行（对齐终端 tail 语义）——content/reasoning 块被未提交工具卡
#: 夹住无法增量提交（BUG-4 连续窗口守卫）时，未提交尾随流式持续增长，
#: 每帧全量渲染 O(未提交尾) 导致渲染线程卡顿。限制后中间行暂不显示，
#: 块关闭提交（commit_block）时全部行进入 committed_lines 完整显示。
#: 工具卡不适用（卡片渲染依赖 start==0 标题行逻辑，且自身有 64 行增量
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
      - 工具块短路：直接返回 ``tool_card_lines`` 行（open 卡无
        状态行）。**不走** per-line ``_open_styled_cache``——卡片行数与输入行
        非 1:1（wrap/标题/状态），缓存键失效。
      - 其余（reasoning/content）保持原 per-line styled 引用缓存逻辑。

    Args:
        block: 聊天块。
        start: 起始 AnsiLine 下标。
        width: 文档宽度（工具卡片宽度约束；调用方传 model.width）。
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
        # 开放工具卡：卡片行（live 仅 committed_line_count==0 发标题行——
        # 与 committed 首次提交互斥；start>0 已增量提交 → 仅主体行）。
        # ★ ToolCard React Ink 组件化（2026-08-05）：ChatView live 路径已改
        #   用 ``h(ToolCard, ...)`` 组件渲染；本分支保留供冻结缓存测试
        #   （``_block_styled_lines`` 对关闭工具块复用冻结 runs）与外部调用面。
        from src.tui.app.toolcard import tool_card_lines
        return tool_card_lines(block, width, start, None)
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


# ── StaticLines：已缓存行批量发射（标准组件） ────────────────
# ★ 标准 React Ink 组件化（2026-08-05）：committed-chat 自定义 host 迁移为
#   标准组件 ``StaticLines``（``src/tui/ink/widgets/staticlines.py``）——组件树
#   表达 ``h(StaticLines, {"lines": ...})``；measure/paint + 帧前缀缓存性能
#   机制随组件迁移（host "static-lines" 注册于 staticlines 模块）。本文件
#   不再注册 host（旧 "committed-chat" 别名已彻底移除——无例外）。


def _with_stream_indicator(styled: list, width: int, sp: str) -> list:
    """为 live content 最后一行追加流式指示 spinner（BEAUTY-32）。

    截断原内容到 ``width-1``（给 spinner 留位）再追加亮青 spinner 帧——
    流式回答末尾显示动态「生成中」指示（对齐 Claude Code 末尾光标语义，
    spinner 帧比静态光标更生动）。宽度守卫：最后一行原内容可能恰好满宽，
    不截断直接追加会破坏行级 diff 宽度不变量。

    Args:
        styled: 行 StyledRun 列表（_block_styled_lines 产出）。
        width: 布局宽度（>0 时截断基准）。
        sp: spinner 帧字符（非空才追加）。

    Returns:
        新 StyledRun 列表（原内容截断 + spinner）；sp 为空返回原列表。
    """
    if not sp or not styled:
        return styled
    from src.tui.ink.helpers import truncate_runs
    if width and width > 0:
        budget = max(1, width - 1)
        runs = truncate_runs(styled, budget)
    else:
        runs = list(styled)
    runs = list(runs)
    runs.append(StyledRun(sp, Style(fg=45, bold=True)))
    return runs


def _build_open_children(
    block, live_start: int, width: int, block_idx: int,
    is_live_content: bool, sp: str,
) -> tuple:
    """构建开放块行 TEXT 元素元组（OpenBlockLines use_memo 缓存计算体）。

    独立函数（use_memo lambda 内部调用）——``_block_styled_lines`` 仅在
    use_memo miss 时调用（deps 未变帧零计算，与 PERF-26 契约一致）。
    BEAUTY-32：live content 时最后一行经 ``_with_stream_indicator`` 追加
    spinner（截断防溢出）。
    """
    rows = _block_styled_lines(block, live_start, width)
    n_rows = len(rows)
    return tuple(
        h(TEXT, {
            "key": f"chat-{block_idx}-{live_start + i}",
            "styled": (
                _with_stream_indicator(runs, width, sp)
                if is_live_content and i == n_rows - 1 else runs
            ),
        })
        for i, runs in enumerate(rows)
    )


def OpenBlockLines(props) -> object:
    """开放块行组件（PERF-26）：use_memo 缓存行 TEXT 元素列表。

    Props:
        block: 开放块（content/reasoning，非 tool）。
        width: 布局宽度（截断宽度同源）。
        live_start: 未提交尾起始行号（块内绝对行号；ChatView 已应用
            ``_LIVE_TAIL_LINES`` 截断）。
        block_idx: 块索引（行 key 前缀，与 chat_view 复合 key 语义一致）。

    Returns:
        Fragment（透明分组容器）——行 TEXT 子元素直接流入父容器布局
        （不引入额外布局盒/高度，渲染输出与 ChatView 直接逐行
        ``h(TEXT, ...)`` 完全等价）。

    ★ 性能（PERF-26）：use_memo 缓存 children（deps = ``id(block.lines)`` /
    行数 / live_start / width / block_idx）——无新增行帧（流式暂停、工具卡
    运行、动画帧）返回**同一 children 元组**（跨帧同 Element 对象）→
    reconciler ``_try_reuse_stable`` / ``_set_props`` props 引用级命中 →
    免每帧重建 64+ 行 TEXT Element（``__post_init__`` / dict 构造）+ 调和
    比较；行追加（n 变化）/ live_start / width 变化自动重建。styled runs
    引用由 ``_block_styled_lines`` 的 ``_open_styled_cache`` 保证稳定
    （同 line 对象同一 runs 列表）——同 deps 时 children 内容确定。

    ★ BEAUTY-32（2026-08-05 体验动效）：live content 流式指示——开放
    content 块（未关闭且有内容）最后一行追加时间基 spinner 帧（10Hz
    推进）。deps 含 spinner 帧字符（``sp``）——流式推进时每 0.1s 重建
    children（Element 构造开销可忽略）；非 live content 时 ``sp`` 为空串
    （常量）→ 缓存行为与修复前完全一致（PERF-26 契约保持）。
    """
    block = props["block"]
    width = props["width"]
    live_start = props["live_start"]
    block_idx = props["block_idx"]
    n = len(block.lines)
    # ★ BEAUTY-32：live content 判定（kind == content 且未关闭且非空）
    is_live_content = block.kind == "content" and not block.closed and n > 0
    sp = ""
    if is_live_content:
        from src.tui.app import _fx
        sp = _fx.spinner_char()
    children = use_memo(
        lambda: _build_open_children(
            block, live_start, width, block_idx, is_live_content, sp,
        ),
        (id(block.lines), n, live_start, width, block_idx, sp),
    )
    return h(FRAGMENT, None, children)


def ChatView(props) -> object:
    """ChatView 组件：缓存已提交块 + 渲染未提交块。

    未提交块的行给**索引 key**——调和器据此复用 fiber，换行缓存才能命中
    （否则无唯一 key → 每帧重建 → 开放大块整块重包裹，流式卡顿）。

    方向② 步骤6：StaticLines（committed 静态行批量发射）use_memo 缓存——
    ``committed_lines`` 引用不变（模型无新提交）时返回同一 Element →
    reconciler 复用同一 props → host 调和跳过（免每帧重建元素）；流式
    增量提交（committed_lines 变化）时 memo 失效重算。use_memo 须在所有
    条件分支前调用（hook 顺序不变式；ChatView 仅此一个 hook，无顺序风险）。
    """
    model = props["model"]
    # ★ 截断宽度与布局宽度同源：优先用 props width（App 传入，= reconciler
    #   布局宽度），回退 model.width——修复 model.width 与实际布局宽度不一致
    #   （TTL 缓存 / resize 时序 / 默认 80）时，截断到 model.width 的行在
    #   布局按 box.w wrap，第二行只剩尾部边框字符（显示错乱）。
    width = props.get("width") or getattr(model, "width", 0)
    committed_el = use_memo(
        lambda: h(StaticLines, {"lines": model.committed_lines}),
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
    # ★ 性能（PERF-24）：避免每帧 ``model.blocks[model.committed_count:]``
    #   切片分配——改用索引循环（块数量少，range 遍历成本更低；切片仅
    #   在 committed_count 处截断，逐索引跳过即可）。
    for block_idx in range(model.committed_count, len(model.blocks)):
        block = model.blocks[block_idx]
        # 卡片角色头（live 路径）：块尚未有任何增量提交（committed_line_count
        # == 0）时在正文行前发射——已提交的头在 committed_lines 中，此处不再
        # 重复（互斥）。头独立 key ``chat-{block_idx}-h``（不与整数行号冲突）。
        # ★ BEAUTY-27：live=True（每帧渲染路径）——推理头 spinner/呼吸生效；
        #   提交路径（模型 _card_lines）默认 live=False 回退静态 💭（防历史
        #   冻结随机 spinner 帧）。
        if block.committed_line_count == 0:
            header_line = _role_header_line(block, model, width, live=True)
            if header_line is not None:
                children.append(h(TEXT, {
                    "key": f"chat-{block_idx}-h",
                    "styled": header_line.runs,
                }))
        # 开放块只渲染未提交尾（已增量提交的行在缓存中，不再重建）
        # ★ 方向D 步骤14 + PERF-7（live 尾部截断）：content/reasoning 块被
        #   未提交工具卡夹住（无法增量提交）时未提交尾随流式增长——仅渲染
        #   最后 ``_LIVE_TAIL_LINES`` 行（中间行块关闭提交时经 committed_lines
        #   完整显示，非全屏流动模型无视觉跳变）；工具卡不截断（标题行渲染
        #   依赖 start==0）。
        # ★ BUG-41（review 方向，性能）：行 key 用**块内绝对行号**（修复前
        #   ``row_in_block`` 从 committed_line_count 起重新编号——块被增量提交
        #   N 行后，旧 ``chat-{i}-0`` 的 fiber 改渲染绝对行号 N → 换行缓存/style
        #   缓存全部 miss，流式期间每帧重包裹）。绝对行号 key 在流式追加时保持
        #   稳定（已渲染行 key 不变，调和器复用 fiber；仅新增行创建新 fiber）。
        live_start = block.committed_line_count
        if block.kind != "tool" and len(block.lines) - live_start > _LIVE_TAIL_LINES:
            live_start = len(block.lines) - _LIVE_TAIL_LINES
        # ★ ToolCard React Ink 组件化（2026-08-05）：工具块 live 渲染为单个
        #   ToolCard 组件（内部 Column + TEXT 行，行 key ``tool-{i}``）——替代
        #   原逐行 ``h(TEXT, {"styled": runs})``。组件 key 用块索引（稳定），
        #   流式追加输出时组件 fiber 复用（内部行按索引复用 + 新增行创建）。
        if block.kind == "tool":
            children.append(h(ToolCard, {
                "key": f"chat-{block_idx}-tool",
                "block": block,
                "width": width,
                "start": live_start,
            }))
            continue
        # ★ 性能（PERF-26）：open 块行经 ``OpenBlockLines`` 独立组件渲染
        #   （内部 use_memo 缓存行 TEXT 元素列表，deps = 行数/起始/宽度/块索引）
        #   ——无新增行帧（流式暂停/工具卡运行等）复用同一 children 元组，
        #   reconciler props 引用级命中 → 免每帧重建 64+ 行 Element + 调和
        #   比较（大 open 块场景 ~1.45ms → ~0.9ms）。组件 key 用块索引（稳定，
        #   与 ToolCard 同模式）；Fragment 透明分组不引入额外布局盒。
        children.append(h(OpenBlockLines, {
            "key": f"chat-{block_idx}-open",
            "block": block,
            "width": width,
            "live_start": live_start,
            "block_idx": block_idx,
        }))
    # 子代理活动卡片（并入消息流，对齐 Claude Code）：subagent_lines 为
    # _subagent_render 产出的树图 Line 行（无边框/无 emoji 图标），经标准组件
    # SubAgentCard 渲染（内部 use_memo 缓存——引用不变帧零重建；组件卸载
    # 由 subagent_lines 空/非空自动驱动，不占 ChatView hook 槽位）。
    if model.subagent_lines:
        children.append(h(SubAgentCard, {
            "key": "subagent-cards",
            "lines": model.subagent_lines,
            "width": width,
        }))
    # ★ 空状态欢迎提示（2026-08-05 美化 + BEAUTY-25）：聊天区无任何内容
    #   （启动/清屏后）时显示欢迎引导行——避免空白聊天区的冷启动感。
    #   ★ BEAUTY-25（体验动效）：活跃期（status_active——模型已配置且流式/
    #   工具执行中）✦ 图标时间基呼吸（渲染循环已推进，零额外成本）；空闲期
    #   回退静态单例（CPU ~0）。
    if not children:
        children.append(_welcome_element(model, width))
    # ★ 阶段2（标准布局容器重构）：BOX(None) → Column（默认 flexDirection=
    #   column，输出与重构前一致；committed-chat host 子节点不受容器 type
    #   变化影响——容器仍是 "box" host）。
    return h(Column, None, children)


__all__ = ["ChatView"]
