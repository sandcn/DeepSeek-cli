"""布局测量核心 — ``_measure`` 递归测量 + 行宽/对齐/收缩辅助。

模块边界（2026-08-05 架构优化）：从 ``ink/layout.py`` 拆分——测量主循环
（``_measure``）及其直接调用的辅助（行宽测量 ``_runs_natural_width`` /
文本对齐 ``_apply_text_align`` / row 超宽收缩 ``_shrink_row_children`` /
文本换行入口 ``wrap_text_lines``）归入本模块；尺寸解析/坐标变换/flexbox
分布/绝对定位分别独立（见 ``_layout_sizing`` / ``_layout_transform`` /
``_layout_flex`` / ``_layout_absolute``）。

依赖方向（单向无环）：本模块 → sizing/tree/transform/flex；不反向依赖
（``layout`` 公共门面 re-export 本模块符号）。

注意：``_shrink_row_children`` 调用 ``_measure``（同模块），与 ``_measure``
的 row 超宽分支互相递归——保持同模块避免跨模块循环。
"""

from __future__ import annotations

from src._compat import dataclass

from src.tui._width import wcswidth_simple
from .fiber import Fiber
from .output import StyledRun, Line
from ._style_fp import style_fingerprint
from ._layout_sizing import (
    _resolve_width,
    _clamp_width,
    _resolve_height,
    _resolve_length,
    _resolve_padding,
    _flex_grow,
    _flex_shrink,
    _apply_aspect_ratio,
)
from ._layout_tree import layout_children
from ._layout_transform import (
    _reflow_subtree,
    _translate_subtree_y,
    _translate_subtree_x,
)
from ._layout_flex import (
    _distribute_extra,
    _compute_weight_shares,
    _reflow_row_justify,
)


# ═══════════════════════════════════════════════════════════
# LayoutBox — 布局结果（2026-08-05 拆分自 layout.py，本模块为唯一
# 构造方；layout.py 门面 re-export 保持 ``from src.tui.ink.layout import
# LayoutBox`` 旧导入路径兼容）
# ═══════════════════════════════════════════════════════════


@dataclass
class LayoutBox:
    """布局盒（文档坐标系，0-based）。

    Attributes:
        x: 左列偏移。
        y: 顶行偏移。
        w: 显示宽度。
        h: 显示高度（行数）。
    """

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


def _runs_natural_width(runs: list) -> int:
    """styled runs 的自然内容宽度（单行拼接宽度，按 ``\\n`` 拆行取最大行宽）。

    ★ BUG-28（review 方向 P2）：``\\n`` 为强制换行符——修复前直接累加所有
    run 的 ``wcswidth_simple``（``\\n`` 宽度 0），含换行文本的自然宽度高估为
    全拼接宽；与 TEXT 文本分支（``text.split("\\n")`` 取最大行宽）不一致 →
    row 内 styled 含换行文本时子节点宽度/后续兄弟位置偏大。改为按 ``\\n``
    拆行后取最大行宽（与文本分支一致）。

    ★ 性能（PERF-10）：免 ``join`` + ``split`` 分配——旧实现先拼接全部 run
    文本（大字符串分配）再 split 成段列表（二次分配），渲染热路径（状态栏/
    标题栏等 row 容器测自然宽）每帧调用。新实现逐 run 累积当前段宽，遇
    ``\\n`` 重置段宽（语义与拆行取最大行宽一致）；纯文本 run 直接
    ``wcswidth_simple`` 累加（ASCII 快路径 O(1)）。
    """
    max_w = 0
    cur_w = 0
    for r in runs:
        # ★ 性能（PERF-12）：``getattr(r, "text", str(r))`` 的默认参数
        #   ``str(r)`` 被**提前求值**——即使 ``text`` 属性存在（StyledRun 等
        #   常规 run），每次迭代也执行 ``str(r)``（repr 构造，含 5 字段
        #   dataclass 打印）→ 热路径（状态栏/标题栏每帧测自然宽）严重浪费。
        #   改为先取 text，None 时才回退 str(r)（仅防御非标准 run 对象）。
        #   实测 50-run 列表 5 万次迭代提速 ~14x。
        text = getattr(r, "text", None)
        if text is None:
            text = str(r)
        if "\n" in text:
            # 含换行（拆段语义，与 ``text.split("\\n")`` 取最大行宽一致）：
            #   - 第一段 = 活动行的延续（承接之前 run 的 ``cur_w``——文本
            #     末尾 ``\\n`` 后下一个 run 的头部是同一行，不能从 0 重算）；
            #   - 中间段 = 独立完整行（测宽取最大）；
            #   - 最后一段 = 新的活动行（后续 run 继续累加）。
            segs = text.split("\n")
            first_w = cur_w + wcswidth_simple(segs[0])
            if first_w > max_w:
                max_w = first_w
            for seg in segs[1:-1]:
                w = wcswidth_simple(seg)
                if w > max_w:
                    max_w = w
            cur_w = wcswidth_simple(segs[-1])
            if cur_w > max_w:
                max_w = cur_w
        else:
            # ★ 性能（PERF-13）：无换行 run 直接用 ``r.width`` 缓存
            #   （StyledRun frozen 构造期已算——免重复 ``wcswidth_simple``；
            #   ``text`` 为空时宽度 0，累加无副作用）。修复前每次热路径
            #   （row 容器测自然宽）重新调 ``wcswidth_simple``。
            rw = getattr(r, "width", None)
            if rw is None:
                rw = wcswidth_simple(text)
            cur_w += rw
            if cur_w > max_w:
                max_w = cur_w
    return max(max_w, cur_w)


def wrap_text_lines(text: str, width: int, style=None, hard: bool = False) -> list[Line]:
    """将文本按显示宽度换行为 Line 列表（CJK 安全）。

    width<=0 时不换行但按 ``\\n`` 拆行（BUG-34 同族修复——统一经
    ``wrap_runs_by_width`` 的 max_width<=0 分支处理，含换行文本不产生
    内嵌字面换行符）。hard=True 时字符级硬拆（react-ink ``wrap="hard"``）。
    """
    from .helpers import wrap_runs_by_width
    return wrap_runs_by_width([StyledRun(text, style)] if text else [], width, hard=hard)


def _apply_text_align(lines: list[Line], width: int, align: str) -> list[Line]:
    """应用 TEXT align（文本对齐，完善 react ink）——right/center 前导空格。

    left（默认）返回原列表（零分配）。right/center 对宽度差 > 0 的行创建
    新 Line（前导空格 run + 原 runs）；宽度差为 0 的行原样返回（无对齐
    需求，身份引用保持）。结果随 ``_wrap_cache`` 缓存——同 align 跨帧命中
    返回对齐行对象，diff 身份短路保持。

    Args:
        lines: 换行后的 Line 列表。
        width: 布局宽度（对齐基准）。
        align: "left" / "right" / "center"（调用方已归一化）。

    Returns:
        对齐后的 Line 列表（left 或无需对齐时原列表）。
    """
    if align == "left" or width <= 0:
        return lines
    out: list[Line] = []
    for line in lines:
        pad = width - line.width
        if pad <= 0:
            out.append(line)
            continue
        left = pad // 2 if align == "center" else pad
        if left <= 0:
            out.append(line)
            continue
        aligned = Line()
        aligned.append(" " * left)
        for run in line.runs:
            aligned.append_run(run)
        out.append(aligned)
    return out


def _shrink_row_children(
    children: list[Fiber],
    used_w: int,
    target_w: int,
    spacing: int,
    inner_x: int,
    inner_y: int,
) -> None:
    """row 内容超宽时按 flexShrink 权重收缩子节点宽度（E-ROW-OVERFLOW 根因修复）。

    row 内容自然宽超容器内宽时（``used_w > target_w``）触发：按 flexShrink
    权重（**默认 1，对齐 React Ink 标准语义**——与 column 方向显式 shrink
    语义不同：row 超宽属异常布局，默认收缩防溢出）迭代收缩子节点宽度，
    钳制每子 >= 1 列；收缩后对宽度变化的子节点**重新测量**（fill=True——
    内部内容按新宽度约束 wrap/截断，修复嵌套容器内部孙节点自然宽超容器
    的溢出），最后重排子节点 x（含 spacing）并平移整棵子树（后代随动）。

    修复前：row 无 flexShrink 逻辑，容器子节点（fill=False 内容自适应）自然
    宽超容器时溢出 box——超宽行破坏行级 diff 宽度不变量（嵌套 row/边框/
    ZStack 等容器子节点常见）。

    Args:
        children: row 直接子节点（已测量，layout_box 非 None）。
        used_w: 当前内容总宽（含 spacing）。
        target_w: 目标内宽（收缩上限）。
        spacing: 兄弟间距。
        inner_x: 内容区起始 x。
        inner_y: 内容区起始 y。
    """
    weights = []
    for c in children:
        if "flexShrink" in c.props:
            # 显式提供（含显式 0）：权重即解析值——显式 0 表示禁缩
            # （不参与收缩循环），保持 0。
            weights.append(_flex_shrink(c))
        else:
            # 未设置 → 默认权重 1（row 超宽默认收缩防溢出）。
            weights.append(1)
    widths = [c.layout_box.w for c in children]
    deficit = float(used_w - target_w)
    if deficit <= 0:
        return
    # 迭代收缩：每轮按权重比例缩减，钳制每子 >= 1；
    # 权重 0（显式 flexShrink: 0）的子节点不参与收缩（宽度保持）。
    while deficit > 0.01:
        shrinkable = [i for i, w in enumerate(widths) if w > 1 and weights[i] > 0]
        if not shrinkable:
            break
        w_total = sum(weights[i] for i in shrinkable)
        if w_total <= 0:
            break
        per = deficit / w_total
        progressed = False
        for i in shrinkable:
            new_w = max(1.0, widths[i] - per * weights[i])
            if new_w < widths[i]:
                deficit -= (widths[i] - new_w)
                widths[i] = new_w
                progressed = True
        if not progressed:
            break
    # 写回 + 重新测量（宽度变化子节点按新宽度约束内部内容 wrap/截断）
    # ★ P3-4 舍入语义说明（review 方向）：收缩循环以 float 迭代（``widths[i]``
    #   可带小数），写回时 ``int(round(widths[i]))`` 四舍五入——多子节点各自
    #   舍入后收缩总量可能偏差 ±1 列（如两子各缩 0.5 → 各 round 到 1，合计
    #   缩 2 列而 deficit 仅 1 列；或 0.4+0.4 合计 0.8 → 各 round 到 0 欠缩）。
    #   保持 float 迭代（改为纯整数运算需重构 deficit 收敛语义，收益低风险
    #   高）；舍入偏大导致的超宽由下方 L4 预算内补偿修正（行宽不变量不再
    #   放宽，宁欠勿超）。
    for i, child in enumerate(children):
        new_w = max(1, int(round(widths[i])))
        cb = child.layout_box
        if cb is None:
            continue
        if cb.w != new_w:
            # 重新测量子树（fill=True：内部内容按新宽度约束 wrap/截断——
            # 修复嵌套容器内部孙节点自然宽超容器后的溢出）
            # ★ P2-2 修复（review 方向，行宽不变量）：子节点显式 minWidth
            #   > new_w 时，``_clamp_width`` 仍会把宽度提升回 minWidth（React
            #   Ink min-width 语义——见 ``_clamp_width`` docstring「minWidth 可
            #   超宽」契约）——若以 new_w 为可用宽度测量，内部内容按 new_w
            #   换行但 box.w = minWidth（宽高不一致），且收缩后行内总宽仍超
            #   容器（行宽不变量被破坏）。修复：以 minWidth 为**实际可用宽度**
            #   测量（内部内容按最终 box 宽度布局）——该子节点允许超宽（显式
            #   minWidth 优先于收缩约束，与 React Ink 语义一致），行宽不变量
            #   在显式 minWidth 场景下局部让位（注释记录，不静默破坏）。
            avail_for_remeasure = new_w
            mn_w = child.props.get("minWidth")
            if mn_w is not None:
                try:
                    mn_resolved = _resolve_length(mn_w, new_w)
                except (TypeError, ValueError, OverflowError):
                    mn_resolved = 0
                if mn_resolved > new_w:
                    avail_for_remeasure = mn_resolved
            cb.w = new_w
            child.layout_box = cb
            _measure(child, inner_x, inner_y, avail_for_remeasure, fill=True)
    # ★ L4 舍入预算内补偿（2026-08-15，行宽不变量）：写回后核算实际总宽
    #   （含 spacing），若仍 > target_w（多子节点各自 round 偏大——如两子
    #   各需缩 0.5 列 → 各 round 到 1，合计缩 2 列超 deficit 1 列），对
    #   shrinkable 子节点（权重>0 且宽 >1）循环减 1 直到总宽 <= target_w
    #   （宁欠勿超，预算内补偿；补偿后行宽不变量保持）。仅修正写回宽度、
    #   不重新测量（内部内容超宽由 paint 截断兜底，与 P2-2 minWidth 让位
    #   语义同族）。
    used_new = sum(c.layout_box.w for c in children if c.layout_box is not None)
    if children:
        used_new += spacing * (len(children) - 1)
    _guard = 0
    while used_new > target_w:
        pick = -1
        for i, child in enumerate(children):
            cb = child.layout_box
            if cb is not None and cb.w > 1 and weights[i] > 0:
                pick = i
                break
        if pick < 0:
            break
        cb = children[pick].layout_box
        cb.w -= 1
        children[pick].layout_box = cb
        used_new -= 1
        _guard += 1
        # 有界保护：每轮至少减 1，正常远小于 len*4（防 weights 与 children
        # 长度不一致等异常导致死循环）
        if _guard > max(1, len(children) * 4):
            break
    # 重排 x + 平移整棵子树（后代随动，BUG-15 语义）
    cx = inner_x
    for i, child in enumerate(children):
        cb = child.layout_box
        if cb is None:
            continue
        delta = cx - cb.x
        if delta:
            _translate_subtree_x(child, delta)
        else:
            cb.x = cx
            child.layout_box = cb
        cx += cb.w
        if i < len(children) - 1:
            cx += spacing


def _measure(fiber: Fiber, x: int, y: int, avail_w: int, fill: bool = True) -> LayoutBox:
    """递归测量并赋值 layout_box。返回该 fiber 的 LayoutBox。

    Args:
        fiber: host fiber。
        x, y: 父容器内偏移（文档坐标系）。
        avail_w: 可用宽度。
        fill: True=填充可用宽度（column 默认）；False=内容自适应宽度（row）。
    """
    ftype = fiber.type
    # ★ P2-1 修复（review 方向）：**通用** ``_measure_cache`` 提前检查分支已
    #   删除——原分支要求 ``ftype != "text"`` 但**只有 TEXT 分支写缓存**
    #   （容器/自定义 host 不缓存，见 TEXT 写回处），``ftype != "text"`` 条件
    #   恒 miss（死代码，每帧空转 O(1)）。TEXT 缓存命中由 TEXT 分支自身检查
    #   （含 styled 长度快照校验——styled 列表可能被测试契约原地修改）。
    explicit_w = fiber.props.get("width")

    # ── display: none（完善 react ink）──
    # 隐藏组件：返回零尺寸盒且不布局子节点（display:none 语义——不占布局
    # 空间、不绘制）。子节点 layout_box 保留上一帧值（布局唯一真源仍是
    # _measure；display:none 时子节点不参与布局，无正确性风险）。
    if fiber.props.get("display") == "none":
        box = LayoutBox(x, y, 0, 0)
        fiber.layout_box = box
        return box

    # ── 自定义 host（注册表） ──
    from .registry import get_host
    host = get_host(ftype)
    if host is not None:
        # ★ 性能（PERF-15）：静态行 host（static-lines/committed-chat）
        #   存在性标记——layout_tree 整树遍历时沿 return_ 链找到 root 并置位，
        #   供 render_frame 的 _find_committed_chat O(1) 判定（无静态行 host
        #   的组件树每帧零 DFS）。静态行 host 每帧仅一个，向上 O(树深) 完全
        #   可接受。
        if ftype == "static-lines":
            _f = fiber.return_
            while _f is not None:
                if getattr(_f, "tag", None) == "root":
                    _f._committed_chat_present = True
                    break
                _f = _f.return_
        measure_fn = host[0]
        w, h = measure_fn(fiber, avail_w)
        box = LayoutBox(x, y, w, h)
        fiber.layout_box = box
        return box

    # ── 叶子：TEXT ──
    if ftype == "text":
        # ★ 性能（PERF-14 + props 引用级缓存）：TEXT 内容由 props 完全决定
        #   （styled/text/width/min/max/textWrap/align/transform），props 引用
        #   相同 + avail_w 相同 + fill 相同时 w/h 必然相同——reconciler 经
        #   ``_set_props`` 内容相等时保持引用稳定 → 引用级命中跳过全部 props
        #   解析与换行计算。
        #   ★ styled 长度快照校验：props 值中的 styled 列表可被测试契约**原地
        #   修改**（``test_wrap_cache_invalidated_on_styled_list_mutation`` 原地
        #   append）——引用级命中无法检测内容变化，须校验长度快照（与
        #   ``_wrap_cache`` 的 BUG-35 语义一致；长度变化 → miss → 值驱动重算；
        #   同长替换元素（罕见，测试契约外）不检测——与 ``_wrap_cache``
        #   同契约）。缓存结构含 styled_len（mc[4]）与宽高（mc[5], mc[6]）。
        mc = getattr(fiber, "_measure_cache", None)
        styled_len = 0
        if mc is not None:
            # 快速长度快照比较（styled 引用需先取——props 引用稳定时直接读）
            pstyled = fiber.props.get("styled")
            if pstyled is not None:
                styled_len = len(pstyled)
            if (
                mc[1] is fiber.props
                and mc[2] == avail_w
                and mc[3] == fill
                and mc[4] == styled_len
            ):
                box = LayoutBox(x, y, mc[5], mc[6])
                fiber.layout_box = box
                return box
        from .helpers import (
            wrap_runs_by_width,
            truncate_runs_ellipsis,
            truncate_runs_start,
            truncate_runs_middle,
            resolve_text_style,
            apply_text_transform,
        )
        styled = fiber.props.get("styled")
        # ★ 完善 react ink：TEXT shorthand 样式（color/bold/...）+ transform
        #   （uppercase/lowercase/capitalize）——resolve_text_style 合并
        #   ``style`` prop 与 shorthand；transform 在换行前作用于文本。
        # ★ 性能（方向4 优化）：styled 非 None（聊天历史/开放块等已带完整
        #   样式 runs）时 **transform 与 resolve_text_style 无下游消费**——
        #   styled 优先于 children（runs 构造走 styled 分支），style/transform
        #   仅纯文本（styled is None）分支使用。跳过两者避免大组件树（1500+
        #   TEXT 行）每帧 O(rows) 的 style 解析与字符串变换。
        if styled is not None:
            text = str(fiber.props.get("children", ""))  # 仅用于空判断
            style = None
        else:
            transform = fiber.props.get("transform")
            text = apply_text_transform(
                str(fiber.props.get("children", "")), transform,
            )
            style = resolve_text_style(fiber.props)
        # ★ textWrap 模式（方向B 步骤12 / 完善 ink）：
        #   "wrap"（默认，现行为）/ "truncate" / "truncate-end"（单行截断省略号，
        #   末尾省略号）/"truncate-start"（省略号在开头，保留尾部）/
        #   "truncate-middle"（保留头尾，中间省略号）——react-ink 完整语义。
        #   ★ 完善 react ink：``wrap`` prop 为 ``textWrap`` 的别名（react-ink
        #   用 ``<Text wrap={...}>``，本框架历史用 ``textWrap``）——优先
        #   textWrap（显式意图），缺省回退 wrap，再回退默认 "wrap"。
        text_wrap = fiber.props.get("textWrap")
        if text_wrap is None:
            text_wrap = fiber.props.get("wrap", "wrap")
        # ★ 完善 react ink：TEXT align（文本对齐）——left（默认）/right/center。
        #   对齐在换行后按布局宽度调整行内容（前导空格），多行各自对齐；
        #   宽度差为 0 的行原样返回（零分配）。对齐结果随 ``_wrap_cache``
        #   缓存（align 入缓存键——align 变化触发重算，同 align 跨帧命中
        #   返回对齐行，diff 身份短路保持）。
        align = fiber.props.get("align")
        if align not in ("right", "center"):
            align = "left"
        if explicit_w is not None:
            # 方向1 步骤3（width 畸形兜底收敛）：复用 _resolve_width（含
            # try/except TypeError/ValueError 兜底）——width 传 "abc"/对象/None
            # 不抛异常（回退 avail）。
            width = _resolve_width(fiber, avail_w)
        elif fill:
            width = _resolve_width(fiber, avail_w)  # 填充可用宽度（min/max 夹取）
        else:
            # row 方向内容自适应宽度（快速路径前不构造 runs 列表——直接测自然宽）
            if styled is not None and styled:
                content_w = _runs_natural_width(styled)
            elif text:
                content_w = max((wcswidth_simple(line) for line in text.split("\n")), default=0)
            else:
                content_w = 0
            width = _clamp_width(fiber, max(0, min(avail_w, content_w)), avail_w)
        # ★ 换行缓存（方向2 P1+P3）：结构 ``(ref, (width, text_wrap), style_fp, lines, ref_len)``
        #   - cache[0] = ref：styled 列表引用（引用级快速路径）或 text 字符串
        #   - cache[1] = (width, text_wrap)
        #   - cache[2] = style_fp：稳定样式指纹（值驱动，BUG-T1——替代 id()）
        #   - cache[3] = lines：换行结果（跨帧复用）
        #   - cache[4] = ref_len：写入时 styled 列表长度快照（BUG-35 修订——原地
        #     修改检测基准；cache[0] 与 styled 是同一对象时 ``len`` 会同步变化，
        #     须保存**写入时**长度快照独立比较）
        #   旧结构 ``(key, lines)`` 的 key 含 ``cache_text``（每帧先 join 再比较的
        #   完整拼接文本副本）——新结构不再持有该副本（P3 内存优化；ref 已被组件
        #   树引用，不额外占用）。P1 热路径优化：styled 静态历史（model 冻结行
        #   引用，如 committed_lines）同引用跨帧复用时直接复用 lines，免每帧
        #   O(chars) join + O(runs) 指纹计算（仅首次 miss 时计算）。
        cache = getattr(fiber, "_wrap_cache", None)
        cache_wt = (width, text_wrap, align)
        if (
            styled is not None
            and cache is not None
            and cache[0] is styled
            and len(styled) == cache[4]  # ★ BUG-35：原地修改检测——比较当前长度
            #   与**写入时**长度快照（cache[0] 与 styled 为同一对象时 ``len``
            #   同步变化，不能用 ``len(cache[0]) == len(styled)``）；长度变化 →
            #   miss → 值驱动分支按 style_fp 重算。
            and cache[1] == cache_wt
        ):
            # 引用级快速路径：同 styled 引用 + (width, text_wrap, align) 不变
            # → 复用 lines（runs 在本分支无下游消费——h 仅由 lines 推导；
            # 死拷贝移除；lines 已含 align 结果，不再二次对齐）。
            # ★ 契约说明（BUG-71 复核 + P3-3 强制约定）：引用级命中依赖
            #   「styled 列表不可变」——同引用 + 同长度 + 内容被**原地替换**
            #   （如 ``styled[3] = new_run``）不会触发重算（长度/指纹均未
            #   检查）。**强制约定：新增 styled 原地修改的调用方必须先换新
            #   引用**（``list.copy()``/新建列表——否则缓存不失效，渲染结果
            #   陈旧且难以排查）。当前全部调用方遵守契约
            #   （model._replace_committed_line 刻意用 ``list.copy()`` 换引用，
            #   chat_view 冻结 runs 列表不被修改）；React 同语义（props 不可变）。
            lines = cache[3]
        else:
            if styled is not None:
                runs = list(styled)
                # BUG-T1：稳定样式指纹（值驱动），替代 id() 对象身份——
                #   id() 在对象 GC 后可能复用导致错误缓存命中/未命中
                #   注意：style 可为 None（无样式 run）→ 记 None（hashable 常量）
                style_fp = tuple(
                    style_fingerprint(r.style) if r.style is not None else None
                    for r in runs
                )
                ref = styled
            else:
                runs = [StyledRun(text, style)] if text else []
                style_fp = (style_fingerprint(style),) if style is not None else (None,)
                ref = text
            if (
                cache is not None
                # ★ P3-2 修复（review 方向）：值比较前加**长度快照短路**——
                #   ``cache[0] == ref`` 对 styled 列表逐 run 比较（O(runs)），
                #   长度不同直接 miss（O(1)）——styled 原地增删（测试契约）
                #   必然先改长度；仅同长元素替换（罕见）才落到值比较。字符串
                #   ref（styled is None 分支）len 同样 O(1) 短路。
                and len(cache[0]) == len(ref)
                and cache[0] == ref
                and cache[1] == cache_wt
                and cache[2] == style_fp
            ):
                lines = cache[3]
            else:
                if text_wrap in ("truncate", "truncate-end", "truncate-start", "truncate-middle"):
                    # 单行截断：内容超宽 → 截断 + 省略号（位置随模式）；未超宽 → 原样单行
                    if text_wrap == "truncate-start":
                        lines = [Line(truncate_runs_start(runs, width))]
                    elif text_wrap == "truncate-middle":
                        lines = [Line(truncate_runs_middle(runs, width))]
                    else:
                        lines = [Line(truncate_runs_ellipsis(runs, width))]
                elif text_wrap == "hard":
                    # react-ink ``wrap="hard"``（方向 G）：字符级硬拆填满行宽
                    lines = wrap_runs_by_width(runs, width, hard=True)
                else:
                    lines = wrap_runs_by_width(runs, width)
                lines = _apply_text_align(lines, width, align)
                # ★ BUG-35：写入时保存 styled 长度快照（原地修改检测基准）
                fiber._wrap_cache = (ref, cache_wt, style_fp, lines, len(ref) if isinstance(ref, list) else 0)
        fiber._wrapped_lines = lines  # 供 paint 复用（免二次包裹）
        # ★ 空 TEXT 高度修复（方向1）：``max(1, len(lines))`` 恒 ≥1——空文本
        #   （无 lines/runs/text）高度 1 会在文档中产生多余空行；改为纯内容
        #   推导 ``h = len(lines)``（空文本 h=0；text 非空但 width<=0 时
        #   wrap_runs_by_width 返回单行 h=1 不变）。_paint TEXT 分支对 h=0
        #   的 box 零绘制安全（lines 为空循环不执行），layout_children 对
        #   空 TEXT 高度 0 不产生空行。
        # ★ 1.7 修复：row 内剩余宽度 0（fill=False 且 width==0）时高度视为 0
        #   ——wrap_runs_by_width(runs, 0) 返回单行令 h=1，子节点 0 宽仍占 1 行
        #   高度使 row_h 虚增；零宽且非 fill 子节点不占位（fill=True 的
        #   column 容器零宽不受影响，容器本身高度仍按内容）。
        if width == 0 and not fill:
            h = 0
        else:
            h = len(lines)
        # ★ 性能（PERF-14 + props 引用级缓存）：写回 props 引用级测量缓存
        #   （下次同 props 引用 + styled 长度 + avail_w + fill 直接复用 w/h，
        #   跳过全部解析与换行）。缓存结构含 ftype（归属校验）、styled 长度
        #   快照（styled 原地修改检测——mc[4]）、宽高（mc[5], mc[6]）。
        #   ★ P2-1 契约（review 方向）：**仅文本可缓存**——容器/自定义 host
        #   不写缓存（布局有子节点副作用，缓存复用会跳过子节点测量）；本
        #   模块唯一的 ``_measure_cache`` 写入点即此 TEXT 分支（函数开头
        #   通用检查已删除，见上）。
        styled_cache_len = len(styled) if styled is not None else 0
        fiber._measure_cache = (
            "text", fiber.props, avail_w, fill, styled_cache_len, width, h,
        )
        box = LayoutBox(x, y, width, h)
        fiber.layout_box = box
        return box

    # ── 叶子：SPACER ──
    if ftype == "spacer":
        if explicit_w is not None:
            # 方向1 步骤3：width 畸形兜底（复用 _resolve_width）
            width = _resolve_width(fiber, avail_w)
        else:
            width = _clamp_width(fiber, avail_w if fill else 1, avail_w)
        h = fiber.props.get("height", 1)
        try:
            h = max(0, int(h))
        except (TypeError, ValueError, OverflowError):
            h = 1
        # ★ P3-1 修复（review 方向）：SPACER 应用 minHeight/maxHeight 钳制
        #   （与容器分支 ``_resolve_height`` 一致）——修复前 SPACER 仅解析显式
        #   height，minHeight/maxHeight 被静默忽略（SPACER 显式 ``minHeight``
        #   参与 row 高度累加时行为与 BOX 不一致）。
        h = _resolve_height(fiber, h)
        # ★ 1.7 修复：零宽 SPACER（显式 width=0 或剩余宽度 0）高度视为 0——
        #   不参与 row 高度累加（row_h 不虚增；fill=False 时 width=1 不受影响）。
        if width == 0:
            h = 0
        box = LayoutBox(x, y, width, h)
        fiber.layout_box = box
        return box

    # ── 容器：BOX / STATIC / APP ──
    # ★ paddingLeft/Right/Top/Bottom（方向8 完善 react ink）：单边内边距；
    #   ``paddingX/paddingY`` 控制横向/纵向，缺省回退 ``padding`` 均一值。
    pad_l, pad_r, pad_t, pad_b = _resolve_padding(fiber)
    # ★ 健壮性（PERF-12 同批）：``fiber.props.get("border", 0)`` 在 props 显式
    #   传 ``None``（键存在但值为 None）时返回 None → ``if border:`` 为 False
    #   → border 保持 None → ``inner_x = x + pad_l + border`` 崩溃。统一用
    #   ``or 0`` 兜底（None/0 归 0；非法值走 try/except 归 0）。
    border = fiber.props.get("border") or 0
    if border:
        try:
            border = max(0, int(border))
        except (TypeError, ValueError, OverflowError):
            border = 0
    margin = fiber.props.get("margin") or 0
    if margin:
        try:
            margin = max(0, int(margin))
        except (TypeError, ValueError, OverflowError):
            margin = 0
    # ★ gap（完善 ink flexbox）：子节点间距——``gap`` 优先于 ``margin``
    #   （React Ink 现代 flexbox 语义：gap 仅影响兄弟间距，不影响外边距）。
    #   同时存在时 gap 胜出（显式 gap 意图明确）；缺省回退 margin。
    gap = fiber.props.get("gap")
    if gap is not None:
        try:
            gap = max(0, int(gap))
        except (TypeError, ValueError, OverflowError):
            gap = margin
    else:
        gap = margin
    # ── columnGap / rowGap（完善 react ink v6）──
    # gap 为两者 shorthand（缺省回退 margin）；columnGap 显式存在时覆盖
    # gap（row 容器水平间距），rowGap 显式存在时覆盖 gap（column 容器垂直
    # 间距 / wrap 行间距）。畸形值回退 gap。
    col_gap = gap
    row_gap = gap
    if "columnGap" in fiber.props:
        try:
            col_gap = max(0, int(fiber.props.get("columnGap")))
        except (TypeError, ValueError, OverflowError):
            col_gap = gap
    if "rowGap" in fiber.props:
        try:
            row_gap = max(0, int(fiber.props.get("rowGap")))
        except (TypeError, ValueError, OverflowError):
            row_gap = gap
    #: 兄弟间距统一值（row 用横向、column 用纵向）——direction 归一化后设置

    inner_x = x + pad_l + border
    inner_y = y + pad_t + border
    pad_h = pad_l + pad_r  # 横向 padding 总量
    pad_v = pad_t + pad_b  # 纵向 padding 总量
    children = layout_children(fiber)
    # ── 绝对定位子节点分离（完善 react ink position="absolute"）──
    # 绝对定位元素脱离文档流：不参与父容器正常流布局（不占宽度/高度）。
    # 具体定位由 ``_layout_absolute_pass``（layout_tree 第二遍）在整树测量
    # 完成后执行（定位基准 = 最近 position="relative" 祖先的确定尺寸）。
    # ★ P-H3（性能）：绝大多数容器无 absolute 子节点——先短路检测，无 absolute
    #   时零额外列表分配（修复前无条件 2 次 O(n) 列表推导，10Hz 下大组件树
    #   每容器每帧重复分配）。原实现 ``abs_children`` 列表仅用于「非空判断」
    #   （第二遍 ``_layout_absolute_pass`` 独立遍历定位，不消费该列表），
    #   简化后仅保留判断语义。
    has_abs = False
    for c in children:
        if c.props.get("position") == "absolute":
            has_abs = True
            # ★ 性能（PERF-17）：置位 root absolute 存在标志——layout_tree
            #   据此跳过绝对定位第二遍（无 absolute 组件树每帧省整树遍历）。
            #   沿 return_ 链向上 O(树深) 完全可接受（仅 absolute 首次出现
            #   时触发一次；同树多 absolute 节点重复置位幂等）。
            _f = fiber.return_
            while _f is not None:
                if getattr(_f, "tag", None) == "root":
                    _f._has_absolute_present = True
                    break
                _f = _f.return_
            break
    if has_abs:
        children = [c for c in children if c.props.get("position") != "absolute"]
    # ── 父可用高度传播（height="50%" 百分比解析）──
    # 容器自身显式数字 height 时，子节点百分比 height 可相对它解析；
    # 父高度未知（内容驱动/百分比）时不传播（子节点百分比回退内容高度）。
    explicit_h = fiber.props.get("height")
    parent_avail_h = None
    if explicit_h is not None:
        if not (isinstance(explicit_h, str) and explicit_h.endswith("%")):
            try:
                parent_avail_h = max(0, int(explicit_h))
            except (TypeError, ValueError, OverflowError):
                parent_avail_h = None
    if parent_avail_h is not None:
        for child in children:
            child._parent_avail_h = parent_avail_h
    else:
        # ★ P1 修复（review 方向）：容器从「显式数字 height」变为「无 height」
        #   时必须清零子节点 ``_parent_avail_h``——否则残留旧父高度，子节点
        #   ``height="50%"`` 继续用陈旧父高度解析（状态未重置）。
        for child in children:
            child._parent_avail_h = None
    direction = fiber.props.get("flexDirection", "column")

    # ── flexDirection="row-reverse"/"column-reverse"（完善 react ink）──
    # CSS flexbox 语义：主轴方向反转——视觉顺序反转。实现：归一化 direction
    # 为 row/column 并翻转 children 列表（首子排最右/最下，与 CSS 视觉一致；
    # justifyContent 基于反转后的顺序——flex-start 在视觉右侧/底部，正确）。
    # 注意：absolute 子节点已在上方分离，此处翻转仅作用于正常流子节点。
    if direction in ("row-reverse", "column-reverse"):
        direction = direction[:-8]  # 去掉 "-reverse" 后缀
        children = list(reversed(children))

    # ── flexWrap="wrap-reverse"（完善 react ink）──
    # wrap-reverse：换行方向反转——多行时行序从下往上（视觉：首行在最下）。
    # 实现：flex_wrap=True + flex_wrap_reverse=True；行 y 重排时反向累加
    # （wrap 分支见下）。
    flex_wrap_reverse = fiber.props.get("flexWrap") == "wrap-reverse"
    flex_wrap = fiber.props.get("flexWrap") in ("wrap", "wrap-reverse")

    # ── flexWrap="wrap"（换行流式布局，完善 react ink flexbox）──
    # 子节点内容自适应宽度，超出行内宽换到下一行；行高 = 该行最大子高；
    # 总高 = 各行累加 + 行间距（gap）。行内顶对齐（简化——不做行内
    # alignItems 纵向偏移）；flexGrow/justifyContent 不适用（每行独立，文档
    # 注明）。换行后重排各行 y（``_translate_subtree_y`` 保证嵌套容器后代
    # 坐标正确）。宽度：显式 width 优先；否则占满可用宽度（wrap 通常配合
    # 明确容器宽度）。
    # ── columnGap/rowGap（完善 react ink v6）──
    # gap 为 columnGap+rowGap shorthand（两者均设置）；columnGap 影响
    # row 容器子节点水平间距 / wrap 行内水平间距；rowGap 影响 column 容器
    # 子节点垂直间距 / wrap 行间垂直间距。行内水平用 col_gap、行间垂直用
    # row_gap（方向 B5）。
    if direction == "row":
        spacing = col_gap
    else:
        spacing = row_gap
    if direction == "row" and flex_wrap and children:
        if explicit_w is not None:
            width = _resolve_width(fiber, avail_w)
            wrap_inner_w = max(0, width - (pad_h + 2 * border))
        else:
            wrap_inner_w = max(0, avail_w - (pad_h + 2 * border))
        wrap_lines: list[list[Fiber]] = [[]]
        wrap_heights: list[int] = []
        cur_x = inner_x

        def _apply_wrap_flex_basis(child: Fiber, cbox: LayoutBox) -> LayoutBox:
            """wrap 分支 flexBasis 应用（与 row 分支同逻辑）。

            L4（2026-08-15）：wrap 场景测量后应用 ``flexBasis`` 覆盖测量
            宽度——修复前 wrap 分支漏应用（row 分支有、wrap 场景静默失效），
            flexWrap 容器子节点 ``flexBasis`` 不生效（测量宽度恒覆盖）。
            换行判断基于应用后宽度（flexBasis 使子节点超宽时正确换行）；
            flexBasis 超 wrap_inner_w 时子节点单独成行（与 row 分支超宽
            语义一致）。
            """
            fb = child.props.get("flexBasis")
            if fb is not None:
                try:
                    fb_w = max(0, int(fb))
                except (TypeError, ValueError, OverflowError):
                    fb_w = 0
                if fb_w > 0 and fb_w != cbox.w:
                    cbox.w = fb_w
                    child.layout_box = cbox
            return cbox

        for child in children:
            # 先以整行内宽测量（内容自然宽，不被剩余宽度截断——换行判断须
            # 用自然宽：剩余宽为 0 时测量宽为 0，换行判断恒 False）
            cbox = _measure(child, cur_x, inner_y, wrap_inner_w, fill=False)
            # L4：测量后应用 flexBasis（与 row 分支同逻辑；换行判断基于
            # 应用后宽度——flexBasis 使子节点超宽时正确换行）
            cbox = _apply_wrap_flex_basis(child, cbox)
            if wrap_lines[-1] and (cur_x - inner_x) + cbox.w > wrap_inner_w:
                wrap_heights.append(
                    max((c.layout_box.h for c in wrap_lines[-1]), default=0)
                )
                wrap_lines.append([])
                cur_x = inner_x
                # 换行后重新测量（y 不影响宽度；x 影响嵌套 relative/绝对定位
                # 后代坐标——统一以最终 x 测量保证后代坐标正确）
                cbox = _measure(child, cur_x, inner_y, wrap_inner_w, fill=False)
                # L4：换行后重测同样应用 flexBasis（两次测量点一致）
                cbox = _apply_wrap_flex_basis(child, cbox)
            wrap_lines[-1].append(child)
            cur_x += cbox.w + col_gap
        wrap_heights.append(
            max((c.layout_box.h for c in wrap_lines[-1]), default=0)
        )
        row_h = sum(wrap_heights)
        if len(wrap_lines) > 1:
            row_h += row_gap * (len(wrap_lines) - 1)
        # 先计算容器高度（alignContent/wrap-reverse 需要 avail_h 才能分布行）
        if explicit_w is None:
            width = _resolve_width(fiber, avail_w)
        content_h = row_h
        h = content_h + (pad_v + 2 * border)
        h = _resolve_height(fiber, h)
        avail_h = max(0, h - (pad_v + 2 * border))
        # ── alignContent（完善 react ink v6）：多行在交叉轴（垂直）的分布 ──
        #   flex-start（默认）：行靠上（当前行为）；
        #   flex-end：行靠下（整体下移 extra）；center：行居中；
        #   space-between：首行顶、末行底、中间等间隔；
        #   space-around：行间等间隔（含边缘半间隔）；space-evenly：含边缘等间隔；
        #   stretch：行高增加填满（各行按 extra 均分）。
        #   wrap-reverse：行序反转（首行在最下，与 CSS 一致）——先反转行序
        #   再按 alignContent 分布（flex-start + reverse 视觉 = 首行底部）。
        align_content = fiber.props.get("alignContent", "flex-start")
        if flex_wrap_reverse:
            wrap_lines = list(reversed(wrap_lines))
            wrap_heights = list(reversed(wrap_heights))
        line_y = inner_y
        if align_content != "flex-start" and len(wrap_lines) > 0 and avail_h > row_h:
            extra = avail_h - row_h
            n_lines = len(wrap_lines)
            if align_content == "flex-end":
                line_y += extra
            elif align_content == "center":
                line_y += extra // 2
            elif align_content == "space-between" and n_lines > 1:
                per = extra // (n_lines - 1)
                rem = extra % (n_lines - 1)
                # 行 y 不变，间隔通过逐行累加实现（下面统一重排循环处理）
                gaps = [0] * (n_lines - 1)
                for i in range(n_lines - 1):
                    gaps[i] = per + (1 if i < rem else 0)
                # 直接重排：首行 line_y，后续行累加 lh + row_gap + gaps[i]
                cy = line_y
                for i, line_children in enumerate(wrap_lines):
                    lh = wrap_heights[i]
                    for child in line_children:
                        cb = child.layout_box
                        if cb.y != cy:
                            _translate_subtree_y(child, cy - cb.y)
                    cy += lh + row_gap
                    if i < n_lines - 1:
                        cy += gaps[i]
                content_h = row_h
                h = content_h + (pad_v + 2 * border)
                h = _resolve_height(fiber, h)
                width, h = _apply_aspect_ratio(fiber, width, h)
                box = LayoutBox(x, y, width, h)
                fiber.layout_box = box
                return box
            elif align_content in ("space-around", "space-evenly"):
                # space-evenly：n+1 个槽位等间隔；space-around：2n 半间隔
                if align_content == "space-evenly":
                    slots = n_lines + 1
                    per = extra // slots
                    rem = extra % slots
                    gaps = [per] * slots
                    for i in range(rem):
                        gaps[i] += 1
                    cy = line_y + gaps[0]
                    for i, line_children in enumerate(wrap_lines):
                        lh = wrap_heights[i]
                        for child in line_children:
                            cb = child.layout_box
                            if cb.y != cy:
                                _translate_subtree_y(child, cy - cb.y)
                        cy += lh + row_gap + gaps[i + 1]
                    content_h = row_h
                    h = content_h + (pad_v + 2 * border)
                    h = _resolve_height(fiber, h)
                    width, h = _apply_aspect_ratio(fiber, width, h)
                    box = LayoutBox(x, y, width, h)
                    fiber.layout_box = box
                    return box
                else:  # space-around
                    half_units = 2 * n_lines
                    per = extra // half_units
                    rem = extra % half_units
                    gaps = [per if i in (0, n_lines) else per * 2 for i in range(n_lines + 1)]
                    for i in range(rem):
                        gaps[i % (n_lines + 1)] += 1
                    cy = line_y + gaps[0]
                    for i, line_children in enumerate(wrap_lines):
                        lh = wrap_heights[i]
                        for child in line_children:
                            cb = child.layout_box
                            if cb.y != cy:
                                _translate_subtree_y(child, cy - cb.y)
                        cy += lh + row_gap + gaps[i + 1]
                    content_h = row_h
                    h = content_h + (pad_v + 2 * border)
                    h = _resolve_height(fiber, h)
                    width, h = _apply_aspect_ratio(fiber, width, h)
                    box = LayoutBox(x, y, width, h)
                    fiber.layout_box = box
                    return box
            elif align_content == "stretch":
                # 行高增加填满（extra 均分到各航）
                per = extra // n_lines
                rem = extra % n_lines
                cy = line_y
                for i, line_children in enumerate(wrap_lines):
                    lh = wrap_heights[i] + per + (1 if i < rem else 0)
                    for child in line_children:
                        cb = child.layout_box
                        if cb.y != cy:
                            _translate_subtree_y(child, cy - cb.y)
                        # 行内子节点高度同步拉伸
                        if cb.h < lh:
                            cb.h = lh
                            child.layout_box = cb
                    cy += lh + row_gap
                content_h = sum(wrap_heights) + (row_gap * (len(wrap_lines) - 1) if len(wrap_lines) > 1 else 0) + extra
                h = content_h + (pad_v + 2 * border)
                h = _resolve_height(fiber, h)
                width, h = _apply_aspect_ratio(fiber, width, h)
                box = LayoutBox(x, y, width, h)
                fiber.layout_box = box
                return box
        # 默认 flex-start / 无富余：正常从上到下堆叠
        for line_children, lh in zip(wrap_lines, wrap_heights):
            for child in line_children:
                cb = child.layout_box
                if cb.y != line_y:
                    _translate_subtree_y(child, line_y - cb.y)
            line_y += lh + row_gap
        width, h = _apply_aspect_ratio(fiber, width, h)
        box = LayoutBox(x, y, width, h)
        fiber.layout_box = box
        return box

    if direction == "row":
        # 子节点横向排列（内容自适应宽度），高度为最大子高
        # ★ 显式宽度先解析（约束子节点可用宽度）——修复前 ``row_inner_w``
        #   用父容器 ``avail_w`` 计算：显式 ``width=8`` 的 row 子节点仍按
        #   父可用宽度测量，内容溢出 box（显式 width 失效，与 column 分支
        #   不对称——column 先解析 width 再约束 inner_w）。显式宽度场景下
        #   子节点按 box 内宽测量（超宽换行/截断，不溢出）。
        if explicit_w is not None:
            width = _resolve_width(fiber, avail_w)
            row_inner_w = max(0, width - (pad_h + 2 * border))
        else:
            row_inner_w = max(0, avail_w - (pad_h + 2 * border))
        cursor_x = inner_x
        row_h = 0
        n_children = len(children)
        for i, child in enumerate(children):
            remaining = max(0, row_inner_w - (cursor_x - inner_x))
            cbox = _measure(child, cursor_x, inner_y, remaining, fill=False)
            # ★ flexBasis（完善 react ink flexbox）：row 主轴=横向——
            #   子节点 ``flexBasis`` 作为初始宽度（覆盖内容自适应宽度）。
            #   与 flexGrow/flexShrink 协同：flexBasis 先应用（影响 used_w），
            #   flexGrow 基于新 used_w 分配剩余；flexShrink 仍按权重缩减。
            #   非数字/<=0 值忽略（保持内容宽度）。
            fb = child.props.get("flexBasis")
            if fb is not None:
                try:
                    fb_w = max(0, int(fb))
                except (TypeError, ValueError, OverflowError):
                    fb_w = 0
                if fb_w > 0 and fb_w != cbox.w:
                    cbox.w = fb_w
                    child.layout_box = cbox
            # ★ row margin 修复（方向1）：最后一个子节点不计 margin——与 column
            #   分支 ``if i < n - 1: total_h += margin`` 一致（原实现无条件累加
            #   margin，最后一个子节点后多出 margin 宽度）。
            cursor_x += cbox.w
            if i < n_children - 1:
                cursor_x += spacing
            row_h = max(row_h, cbox.h)
        if explicit_w is None:
            content_w = cursor_x - inner_x
            width = _clamp_width(
                fiber, max(0, min(avail_w, content_w + pad_h + 2 * border)), avail_w,
            )
        # ★ row flexGrow（方向1 完善 flexbox）：显式宽度富余时按 flexGrow 分配
        #   额外宽度（横向主轴 grow——修复前 flexGrow 仅作用于 column 高度）。
        inner_w_row = max(0, width - (pad_h + 2 * border))
        used_w = cursor_x - inner_x
        # ★ E-ROW-OVERFLOW（行宽不变量）：内容自然宽超容器内宽时按 flexShrink
        #   权重收缩子节点（默认 flexShrink=1，React Ink 标准语义）——修复前
        #   row 无 shrink 逻辑，容器/边框子节点（fill=False 内容自适应）自然宽
        #   超容器时溢出 box（超宽行破坏行级 diff 宽度不变量，嵌套 row/ZStack/
        #   边框等容器子节点常见）。收缩后重新测量子节点（fill=True 约束内部
        #   内容 wrap/截断）→ 行内总宽恒 <= 容器内宽。
        if used_w > inner_w_row and children:
            _shrink_row_children(
                children, used_w, inner_w_row, spacing, inner_x, inner_y,
            )
            used_w = inner_w_row
            # 收缩后子节点高度可能变化（重测量 wrap）——row_h 同步更新
            row_h = max((c.layout_box.h for c in children), default=0)
        extra_w = max(0, inner_w_row - used_w)
        grow_total = 0
        for child in children:
            grow_total += _flex_grow(child)
        if grow_total > 0 and extra_w > 0:
            # ★ 余数分配修复（review 方向）：与 _distribute_extra（column 高度）
            #   共用 _compute_weight_shares——修复前按「grow 子节点序号 <
            #   remainder」逐子 +1：remainder 可超过 grow 子节点数（如单个
            #   flexGrow=3 子节点 + extra_w=2 → remainder=2 > 1 子节点），
            #   欠分配导致剩余列未被填满（row 宽度不足 inner_w_row）。
            weights = [_flex_grow(child) for child in children]
            per, extra_shares = _compute_weight_shares(weights, extra_w)
            old_xs = [child.layout_box.x for child in children]
            for i, child in enumerate(children):
                if weights[i] > 0:
                    cb = child.layout_box
                    cb.w += per * weights[i] + extra_shares[i]
                    child.layout_box = cb
            # 重排 x（grow 改变宽度后；最后子节点不计 spacing）
            cx = inner_x
            for i, child in enumerate(children):
                cb = child.layout_box
                delta = cx - old_xs[i]
                if delta:
                    # ★ BUG-15：x 变化后整棵子树平移（``_translate_subtree_x``
                    #   内部 cb.x += delta + 后代随动）——修复前仅改直接子节点
                    #   cb.x，嵌套容器内后代停留旧 x。
                    _translate_subtree_x(child, delta)
                else:
                    cb.x = cx
                    child.layout_box = cb
                cx += cb.w
                if i < len(children) - 1:
                    cx += spacing
            # grow 消费全部剩余 → justify 无偏移（CSS flexbox 语义）
            used_w = inner_w_row
            extra_w = 0
        # ★ row justifyContent（方向1 完善 flexbox）：横向主轴剩余宽度分布——
        #   center → 所有子节点 x += extra//2；flex-end → x += extra；
        #   space-between/space-around/space-evenly → 按间隔重排（_reflow_row_justify）。
        #   flex-start（默认）不变。与 row flexGrow 语义重叠（grow 先分）。
        #   ★ BUG-15：偏移后整棵子树平移（``_translate_subtree_x`` 内部
        #   cb.x += offset + 后代随动）——修复前仅改直接子节点 cb.x。
        justify = fiber.props.get("justifyContent", "flex-start")
        if justify in ("center", "flex-end") and extra_w > 0:
            offset = extra_w // 2 if justify == "center" else extra_w
            for child in children:
                if offset:
                    _translate_subtree_x(child, offset)
                else:
                    cb = child.layout_box
                    cb.x += offset
                    child.layout_box = cb
        elif justify in ("space-between", "space-around", "space-evenly") and extra_w > 0:
            _reflow_row_justify(children, justify, inner_x, spacing, extra_w)
        # ★ alignItems（方向3，已实现）：row 横向对齐子节点 y 偏移——
        #   center → 每子 y += (row_h - cbox.h)//2；flex-end → y += (row_h - cbox.h)；
        #   stretch（默认）无偏移（当前行为）。
        # ★ baseline（完善 react ink v6）：文本基线对齐——终端文本无字体度量
        #   信息，近似为「底部对齐」（flex-end 行为；单行文本视觉等价，多行
        #   文本近似——文档注明）。
        # ★ alignSelf（方向3 完善 react ink）：子级 ``alignSelf`` 覆盖父
        #   alignItems（row 横轴——纵向偏移；center/flex-end/flex-start/
        #   baseline）；``auto`` 跟随父 alignItems。
        align = fiber.props.get("alignItems", "stretch")
        if (align in ("center", "flex-end", "baseline") or any(
            child.props.get("alignSelf") in ("center", "flex-end", "flex-start", "baseline")
            for child in children
        )) and row_h > 0:
            for child in children:
                cb = child.layout_box
                if cb is not None and cb.h < row_h:
                    self_align = child.props.get("alignSelf")
                    eff_align = self_align if self_align in (
                        "center", "flex-end", "flex-start", "baseline",
                    ) else align
                    if eff_align == "center":
                        delta = (row_h - cb.h) // 2
                        # 整棵子树平移（仅改自身 box.y 会让后代 y 停留在原
                        # 基准——嵌套容器内 TEXT/边框错位，alignItems 偏移修复）
                        if delta:
                            _translate_subtree_y(child, delta)
                    elif eff_align in ("flex-end", "baseline"):
                        delta = row_h - cb.h
                        if delta:
                            _translate_subtree_y(child, delta)
                    # flex-start / stretch：不偏移
                    child.layout_box = cb
        total_h = row_h
    else:
        # 子节点纵向堆叠（填充宽度），高度为内容累加
        #: 探针测量结果（fill=False 分支缓存；其他分支保持 None）
        probe_boxes: list | None = None
        if explicit_w is not None:
            # 方向1 步骤3：width 畸形兜底（复用 _resolve_width）
            width = _resolve_width(fiber, avail_w)
        elif fill:
            width = _resolve_width(fiber, avail_w)  # 填充可用宽度（min/max 夹取）
        else:
            # ★ 1.7 修复：row 内 column/BOX 子节点（fill=False）内容自适应宽度——
            #   先以 fill=False 测量子节点自然宽度（内容宽度 = 子节点自然宽度，
            #   纵向堆叠取最大），加 padding/border 且不超可用宽度。修复前 column
            #   分支忽略 fill 恒填满剩余行宽（row 内 BOX 子节点错误占满整行）。
            # ★ 性能（方向1）：探针测量结果缓存到 ``probe_boxes``——主循环
            #   （fill=False 且非 stretch）复用探针盒（仅更新 y），避免同一
            #   子树测量两次（修复前 fill=False 列容器每帧子节点测量两遍）。
            probe_boxes = []
            probe_w = 0
            for child in children:
                probe_box = _measure(
                    child, inner_x, inner_y,
                    max(0, avail_w - (pad_h + 2 * border)), fill=False,
                )
                probe_boxes.append(probe_box)
                if probe_box.w > probe_w:
                    probe_w = probe_box.w
            width = _clamp_width(
                fiber, max(0, min(avail_w, probe_w + pad_h + 2 * border)), avail_w,
            )
            # ★ E-FILL-OVERFLOW（行宽不变量）：容器被钳制（内容自然宽超可用
            #   宽：``width < probe_w + pad_h + 2*border``）时，内部子节点按容器
            #   实际宽度**重新测量**（fill=True——内部内容按新宽度约束
            #   wrap/截断）。修复前探针测量保持内容自然宽（子节点 box 复用
            #   探针盒），容器 box.w 钳制到 avail_w 但内部孙节点自然宽超容器
            #   → 嵌套容器内容溢出（超宽行破坏行级 diff 宽度不变量）。
            #   主循环复用 ``child.layout_box``（探针盒已更新），零额外测量。
            if width < probe_w + pad_h + 2 * border and children:
                inner_w_probe = max(0, width - (pad_h + 2 * border))
                for child in children:
                    _measure(child, inner_x, inner_y, inner_w_probe, fill=True)
        inner_w = max(0, width - (pad_h + 2 * border))
        cursor_y = inner_y
        total_h = 0
        n = len(children)
        # ★ column alignItems（方向1 完善 flexbox）：横轴对齐——center/flex-end
        #   时子节点按**自然宽度**测量（不填充 stretch）再横向偏移；stretch
        #   （默认）保持现状填充。子节点显式 width 不受影响（_measure 优先
        #   显式宽度）。仅子节点自然宽度 < 容器内宽时产生偏移。
        # ★ alignSelf（方向3 完善 react ink）：子级 ``alignSelf`` 覆盖父
        #   alignItems——center/flex-end/flex-start 时该子按内容宽度测量
        #   （不填充）并在横轴偏移；stretch（默认）跟随父 alignItems。
        #   仅任一子带 alignSelf 或父 align 非 stretch 时进入偏移块（省 O(n)）。
        align = fiber.props.get("alignItems", "stretch")
        has_align_self = False
        for i, child in enumerate(children):
            # fill 沿树传播：fill=False（row 内）时子节点内容自适应（孙 TEXT 不
            # 填满 BOX 内部，BOX 宽度才能由内容决定而非固定填充）。
            # ★ 探针复用：fill=False 且已探针测量（probe_boxes 非 None）时直接
            #   复用探针盒更新 y（探针与主循环测量参数等价——inner_w >= 子节点
            #   自然宽度，fill=False 下结果相同），免二次测量。
            self_align = child.props.get("alignSelf")
            if self_align in ("center", "flex-end", "flex-start"):
                has_align_self = True
                eff_align = self_align
            else:
                eff_align = align
            child_fill = fill if eff_align == "stretch" else False
            if child_fill is False and probe_boxes is not None:
                # 探针测量把**子树全部**按 inner_y 测（y 重叠）——仅更新自身
                # box 不够，后代 y 停留在 inner_y 基准（多子节点时第 2+ 个子树
                # 与首个重叠，方向3 修复）。整棵子树平移 delta_y。
                delta_y = cursor_y - inner_y
                if delta_y:
                    _translate_subtree_y(child, delta_y)
                cbox = child.layout_box
            else:
                cbox = _measure(child, inner_x, cursor_y, inner_w, fill=child_fill)
            # ★ flexBasis（完善 react ink flexbox）：column 主轴=纵向——
            #   子节点 ``flexBasis`` 作为初始高度（覆盖内容自适应高度）。
            #   与 flexGrow/flexShrink 协同：flexBasis 先应用（影响 total_h），
            #   flexGrow 基于新 total_h 分配剩余；flexShrink 仍按权重缩减。
            #   非数字/<=0 值忽略（保持内容高度）。
            fb = child.props.get("flexBasis")
            if fb is not None:
                try:
                    fb_h = max(0, int(fb))
                except (TypeError, ValueError, OverflowError):
                    fb_h = 0
                if fb_h > 0 and fb_h != cbox.h:
                    cbox.h = fb_h
                    child.layout_box = cbox
            cursor_y += cbox.h + spacing
            total_h += cbox.h
            if i < n - 1:
                total_h += spacing
        if (align in ("center", "flex-end") or has_align_self) and inner_w > 0:
            for child in children:
                cb = child.layout_box
                if cb is not None and cb.w < inner_w:
                    self_align = child.props.get("alignSelf")
                    eff_align = self_align if self_align in (
                        "center", "flex-end", "flex-start",
                    ) else align
                    if eff_align == "center":
                        delta = (inner_w - cb.w) // 2
                        # 整棵子树平移（仅改自身 box.x 会让后代 x 停留在原
                        # 基准——嵌套容器内 TEXT/边框错位，alignItems 偏移修复）
                        if delta:
                            _translate_subtree_x(child, delta)
                    elif eff_align == "flex-end":
                        delta = inner_w - cb.w
                        if delta:
                            _translate_subtree_x(child, delta)
                    # flex-start / stretch：不偏移
                    child.layout_box = cb

    content_h = total_h if children else 0
    h = content_h + (pad_v + 2 * border)
    h = _resolve_height(fiber, h)

    # flexShrink：显式高度不足（h < 内容高）且 children 含 shrink>0 时，按
    #   shrink 权重比例缩减子节点高度（每子至少保留 1 行，deficit 按 shrink
    #   权重分配）——与 flexGrow 余数分配对称（方向2 U4）。方向1：余数分配
    #   收敛至 _distribute_extra（余数仅分配给 shrink>0 节点，权重 0 节点不
    #   参与）+ shrink 后孙节点递归重排（_reflow_subtree）。
    #   ★ BUG-32（review 方向）：共享块仅对 **column** 生效——row 容器主轴
    #   （宽度）的 grow/shrink 已在 row 分支处理完毕；此处对 row 是第二次
    #   grow/shrink 且 ``_distribute_extra`` 按纵向堆叠重排子节点 y（交叉轴
    #   应为 top/center/flex-end 对齐，非纵向堆叠）——row + 显式 height +
    #   flexGrow/flexShrink 子节点被错误竖排（确定性渲染错误）。加
    #   ``direction == "column"`` 守卫后 row 保持 row 分支结果。
    if direction == "column" and h < content_h + (pad_v + 2 * border) and children:
        shrink_total = 0
        for child in children:
            shrink_total += _flex_shrink(child)
        deficit = (content_h + (pad_v + 2 * border)) - h
        if shrink_total > 0 and deficit > 0:
            _distribute_extra(
                children, _flex_shrink, deficit, inner_y, spacing,
                direction=-1, clamp_min=1,
            )
            # ★ flexShrink 孙节点重排（方向1）：shrink 修改直接子节点高度后
            #   仅重排直接子节点 y——孙节点 y 在 shrink 前按原高度推算，陈旧；
            #   逐层递归重排子树孙节点 y（与 flexGrow 分支 cb.y 重排语义一致，
            #   仅本帧 shrink 路径内生效，_measure 是唯一真源）。
            for child in children:
                cb = child.layout_box
                if cb is not None:
                    _reflow_subtree(child, cb.y)

    # flexGrow：显式高度富余时按 flexGrow 比例分配（方向1：余数分配收敛至
    #   _distribute_extra——余数仅分配给 grow>0 节点，权重 0 节点不参与）。
    #   ★ BUG-32：仅 column（row 主轴 grow 已在 row 分支处理，见 flexShrink
    #   守卫注释）。
    if direction == "column" and h > content_h + (pad_v + 2 * border) and children:
        grow_total = 0
        for child in children:
            grow_total += _flex_grow(child)
        remaining = h - (content_h + (pad_v + 2 * border))
        if grow_total > 0 and remaining > 0:
            # P1-4：余数分配修改子节点高度后 helper 内**重排 y 坐标**——
            # _measure 按原高度分配 y（如 BOX(height=10)+两个 TEXT flexGrow
            # 2/1 → text0.h=6 但 y=0、text1.h=4 但 y=1 垂直重叠）；写回
            # cb.y = cursor_y 后光标再按新高度累加。
            _distribute_extra(
                children, _flex_grow, remaining, inner_y, spacing,
            )
            # ★ BUG-15（review 方向）：flexGrow 修改直接子节点高度/重排 y 后
            #   **递归重排子树孙节点**——与 flexShrink 分支（``_reflow_subtree``
            #   对称）。修复前孙节点 y 在 grow 前按旧高度推算，第 2+ 个 grow
            #   子节点（嵌套容器）内部后代 y 陈旧 → 文本与自身边框错位。
            for child in children:
                cb = child.layout_box
                if cb is not None:
                    _reflow_subtree(child, cb.y)

    # ★ justifyContent（方向3，已实现）：column 纵向对齐基于 flexGrow 分配后
    #   剩余空间——center → 所有子节点 y += extra//2；flex-end → y += extra；
    #   flex-start（默认）不变。与 flexGrow 语义重叠（都消费剩余空间）：
    #   flexGrow 先分（子节点高度增长），justify 基于分配后剩余；grow 分尽则
    #   extra≈0 无偏移（符合 CSS flexbox 语义）。性能：仅在有剩余空间时计算
    #   （不引入每帧 O(树) 无条件遍历）。
    #   方向1（完善 flexbox）：仅 column 走本块——row 的横向 justifyContent
    #   已在 row 分支处理（本块 n 仅在 column 分支定义，row 路径引用会
    #   UnboundLocalError，原实现隐含依赖「row 无 justifyContent 消费方」）。
    #   ★ BUG-15：offset 后整棵子树平移（``_translate_subtree_y``）——修复前
    #   仅改直接子节点 cb.y，嵌套容器内后代 y 陈旧 → 文本与边框错位。
    justify = fiber.props.get("justifyContent", "flex-start")
    if direction == "column" and justify in ("center", "flex-end") and children:
        n = len(children)
        children_total = 0
        for child in children:
            cb = child.layout_box
            children_total += cb.h if cb is not None else 0
        if n > 1:
            children_total += spacing * (n - 1)
        extra = max(0, h - (pad_v + 2 * border) - children_total)
        if extra > 0:
            offset = extra // 2 if justify == "center" else extra
            for child in children:
                cb = child.layout_box
                if cb is not None and offset:
                    # ★ BUG-15：整棵子树平移（``_translate_subtree_y`` 内部
                    #   cb.y += offset + 后代随动）——修复前先 ``cb.y += offset``
                    #   再调用会双重偏移；嵌套容器内后代 y 陈旧 → 错位。
                    _translate_subtree_y(child, offset)

    # aspectRatio（完善 react ink v6）：宽/高缺省维度由比例推导——
    # 容器分支统一应用（wrap 分支已在各自 return 点应用）。
    width, h = _apply_aspect_ratio(fiber, width, h)
    box = LayoutBox(x, y, width, h)
    fiber.layout_box = box
    return box


__all__ = [
    "_runs_natural_width",
    "wrap_text_lines",
    "_apply_text_align",
    "_shrink_row_children",
    "_measure",
]
