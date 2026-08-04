"""布局 — flexbox 子集 + 文本换行。

布局模型（内容驱动 / 非全屏流动）：
  整个文档高度由内容推导（无视口 pin）——每个组件按其内容换行后
  累加得到高度；宽度由 ``width`` 属性或父容器宽度决定。

  树形布局采用后序遍历：先测量叶子（Text 换行行数 / Spacer 高度），
  再累加容器（BOX/STATIC/APP）子节点高度，为每个 host fiber 赋值
  ``LayoutBox(x, y, w, h)``（文档坐标系，0-based）。

支持的 flexbox 子集：
  - flexDirection: column（默认）| row
  - justifyContent: flex-start（默认）| center | flex-end（column 纵向，已实现）
  - alignItems: stretch（默认）| center | flex-end（row 横向，已实现）
  - flexGrow / flexShrink: int
  - width / height / minHeight / maxHeight: int
  - padding / border / margin: int（均一值）
  - 文本换行/截断（用 wcswidth_simple）

方向3 评估（样式继承，预期不做）：``Style.merge``（core/style.py）已实现但
ink 内未使用——引入父子样式继承需在 _measure/paint 阶段沿 return_ 链合并
Style（每帧 O(树深) 额外成本），且当前组件树全部显式传 Style（无继承需求）；
评估不做（收益低 + 每帧 O(树) 成本违反性能边界）。

方向3 评估（绝对定位 / borderStyle 变体，预期不做）：绝对定位需引入坐标系
基础设施（脱离文档流定位），borderStyle 变体需扩展边框绘制协议——均无消费方，
成本高收益低；评估不做（注释保留可追溯）。
"""

from __future__ import annotations

from src._compat import dataclass
from typing import Callable

from src.tui._screen import wcswidth_simple
from .fiber import Fiber
from .output import StyledRun, Line
from ._style_fp import style_fingerprint


# ═══════════════════════════════════════════════════════════
# LayoutBox — 布局结果
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


# ═══════════════════════════════════════════════════════════
# host 树遍历辅助
# ═══════════════════════════════════════════════════════════


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
            cur_w += wcswidth_simple(text)
            if cur_w > max_w:
                max_w = cur_w
    return max(max_w, cur_w)


def _skip_function(fiber: Fiber | None) -> Fiber | None:
    """沿 function 链下降，返回首个 host fiber（或 None）。"""
    f = fiber
    while f is not None and f.is_function:
        f = f.child
    return f


def layout_children(fiber: Fiber) -> list[Fiber]:
    """返回 fiber 的直接 host 子节点（跳过 function 链）。

    方向1（Fragment 支持）：Fragment host（``fragment``）为透明分组容器——
    其子节点递归扁平化直接流入父容器布局（不产生独立布局盒）。嵌套 Fragment
    经递归自然展开。
    """
    result: list[Fiber] = []
    child = fiber.child
    while child is not None:
        host = _skip_function(child)
        if host is not None:
            if host.is_host and host.type == "fragment":
                result.extend(layout_children(host))
            else:
                result.append(host)
        child = child.sibling
    return result


# ═══════════════════════════════════════════════════════════
# 文本换行
# ═══════════════════════════════════════════════════════════


def wrap_text_lines(text: str, width: int, style=None) -> list[Line]:
    """将文本按显示宽度换行为 Line 列表（CJK 安全）。

    width<=0 时不换行但按 ``\n`` 拆行（BUG-34 同族修复——统一经
    ``wrap_runs_by_width`` 的 max_width<=0 分支处理，含换行文本不产生
    内嵌字面换行符）。
    """
    from .helpers import wrap_runs_by_width
    return wrap_runs_by_width([StyledRun(text, style)] if text else [], width)


# ═══════════════════════════════════════════════════════════
# 布局算法
# ═══════════════════════════════════════════════════════════


def _resolve_width(fiber: Fiber, avail: int) -> int:
    """解析宽度：显式 width 优先，否则内容/可用宽度（含 min/max 夹取）。

    完善 react ink：``minWidth``/``maxWidth`` 对解析结果钳制（与
    ``_resolve_height`` 的 minHeight/maxHeight 对称）。宽高属性解析
    收敛——width 显式 + min/max 夹取。
    """
    w = fiber.props.get("width")
    if w is None:
        resolved = avail
    else:
        try:
            resolved = max(0, int(w))
        except (TypeError, ValueError):
            resolved = avail
    return _clamp_width(fiber, resolved)


def _clamp_width(fiber: Fiber, w: int) -> int:
    """对宽度应用 minWidth/maxWidth 钳制（内容推导/填充宽度共用）。

    ★ 性能（PERF-7）：无 ``minWidth``/``maxWidth`` 属性（绝大多数节点）走
    快速路径直接 ``max(0, w)``——免 2 次 ``props.get`` + 类型兜底。
    """
    props = fiber.props
    if "minWidth" not in props and "maxWidth" not in props:
        return w if w > 0 else 0
    mn = props.get("minWidth")
    if mn is not None:
        try:
            w = max(int(mn), w)
        except (TypeError, ValueError):
            pass
    mx = props.get("maxWidth")
    if mx is not None:
        try:
            w = min(int(mx), w)
        except (TypeError, ValueError):
            pass
    return max(0, w)


def _resolve_height(fiber: Fiber, content_h: int) -> int:
    """解析高度：显式 height 属性优先，否则内容推导（含 min/max 夹取）。"""
    h = content_h
    height = fiber.props.get("height")
    if height is not None:
        try:
            h = max(0, int(height))
        except (TypeError, ValueError):
            pass
    mn = fiber.props.get("minHeight")
    if mn is not None:
        try:
            h = max(int(mn), h)
        except (TypeError, ValueError):
            pass
    mx = fiber.props.get("maxHeight")
    if mx is not None:
        try:
            h = min(int(mx), h)
        except (TypeError, ValueError):
            pass
    return h


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


def _flex_grow(fiber: Fiber) -> int:
    """解析 flexGrow（非数字兜底为 0，与 _resolve_width 一致——P2-2 修复）。

    ``int(...)`` 对非数字值（如字符串 ``"2"`` 之外的 ``None``/对象/畸形串）会抛
    ValueError/TypeError 直接中断渲染；同文件 width/height/padding/border/margin
    均有 try/except 兜底，唯独 flexGrow 缺失——补上。
    """
    g = fiber.props.get("flexGrow", 0)
    try:
        return max(0, int(g))
    except (TypeError, ValueError):
        return 0


def _flex_shrink(fiber: Fiber) -> int:
    """解析 flexShrink（非数字兜底为 0，与 _flex_grow 对称——方向2 U4）。

    与 flexGrow 对称：``int(...)`` 对非数字值（None/对象/畸形串）会抛
    ValueError/TypeError 直接中断渲染；同文件 width/height/padding/border/
    margin/flexGrow 均有 try/except 兜底——补上。
    """
    g = fiber.props.get("flexShrink", 0)
    try:
        return max(0, int(g))
    except (TypeError, ValueError):
        return 0


def _resolve_padding(fiber: Fiber) -> tuple[int, int, int, int]:
    """解析 padding，返回 (pad_left, pad_right, pad_top, pad_bottom)。

    React Ink 语义（方向8 完善）：
    - ``padding`` 设置四边（均一值）；``paddingX`` 覆盖左右（横向）；
      ``paddingY`` 覆盖上下（纵向）——``paddingX/Y`` 缺省回退 ``padding``。
    - ``paddingLeft``/``paddingRight``/``paddingTop``/``paddingBottom``
      单边覆盖（React Ink 支持单边 padding）——缺省回退 ``paddingX``/``paddingY``。
    - 畸形值（None/对象/畸形串）兜底 0，与 width/height/border/margin 一致。

    ★ 性能（PERF-7）：无任何 padding 属性的节点（绝大多数 BOX/STATIC）走
    快速路径直接返回全 0——免 8 次 ``_int`` 函数调用与 8 次 ``props.get``
    （大组件树每帧布局对每个容器调用本函数）。含 padding 属性的节点走
    原有完整解析（行为不变）。
    """
    props = fiber.props
    if (
        "padding" not in props
        and "paddingX" not in props and "paddingY" not in props
        and "paddingLeft" not in props and "paddingRight" not in props
        and "paddingTop" not in props and "paddingBottom" not in props
    ):
        return (0, 0, 0, 0)

    def _int(v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    pad = _int(props.get("padding", 0))
    pad_x = _int(props.get("paddingX", pad))
    pad_y = _int(props.get("paddingY", pad))
    pad_l = _int(props.get("paddingLeft", pad_x))
    pad_r = _int(props.get("paddingRight", pad_x))
    pad_t = _int(props.get("paddingTop", pad_y))
    pad_b = _int(props.get("paddingBottom", pad_y))
    return (pad_l, pad_r, pad_t, pad_b)


def _distribute_extra(
    children: list[Fiber],
    weight_fn: Callable[[Fiber], int],
    total_extra: int,
    inner_y: int,
    margin: int,
    direction: int = 1,
    clamp_min: int | None = None,
) -> None:
    """按权重分配余数给子节点高度并重排 y（flexGrow/flexShrink 共用，方向1）。

    统一 flexGrow 与 flexShrink 的余数分配逻辑（差异封装——两处相似循环
    收敛为单一 helper，满足「先封装差异再做功能」）：

      - ``per = total_extra // sum(weights)``；
      - 余数按 ``weighted_idx < remainder`` 分配给**权重 >0 的节点**（按权重
        节点序列计索引，非原 children 索引——原实现按 ``i < remainder`` 分配，
        权重 0 节点也会得分，权重不符）；
      - 权重 0 节点不参与分配（不增减高度），但 y 坐标仍参与重排；
      - 分配后重排子节点 y（累加 margin），保证无重叠（与 flexGrow 分支
        既有重排语义一致）。

    Args:
        children: 直接 host 子节点。
        weight_fn: 权重解析函数（``_flex_grow`` 或 ``_flex_shrink``）。
        total_extra: 待分配的总余数（>0 才有意义）。
        inner_y: 容器内边距后的起始 y（统一从 inner_y 重排）。
        margin: 子节点间距（每子累计）。
        direction: 分配方向——``1`` 增加高度（flexGrow）；``-1`` 缩减高度
            （flexShrink）。
        clamp_min: 缩减钳制下限（flexShrink 每子至少保留 1 行传 ``1``；
            flexGrow 传 None 不钳制）。
    """
    weights = [weight_fn(child) for child in children]
    total_weight = sum(weights)
    if total_weight <= 0 or total_extra <= 0:
        return
    per = total_extra // total_weight
    remainder = total_extra % total_weight
    weighted_idx = 0  # 权重 >0 节点序列索引（余数仅在这些节点间分配）
    cursor = inner_y
    for i, child in enumerate(children):
        cb = child.layout_box
        if weights[i] > 0:
            delta = per * weights[i] + (1 if weighted_idx < remainder else 0)
            weighted_idx += 1
            if delta > 0:
                if direction > 0:
                    cb.h += delta
                else:
                    # 每子至少保留 1 行（钳制 ≥1）
                    cb.h = max(clamp_min if clamp_min is not None else 1, cb.h - delta)
        cb.y = cursor
        child.layout_box = cb
        cursor += cb.h + margin


def _reflow_row_justify(
    children: list[Fiber],
    justify: str,
    start_x: int,
    margin: int,
    extra: int,
) -> None:
    """row justifyContent 重排子节点 x（space-between/space-around/space-evenly）。

    方向1（完善 flexbox）：横向主轴剩余宽度分布——与 column justifyContent
    （纵向，已实现）对称。三种模式均从 ``start_x`` 起重排 x（忽略 grow/align
    已产生的偏移；调用点在 row flexGrow 之后，grow 消费剩余则 extra≈0 不触发）：

      - ``space-between``：首子靠左、末子靠右，中间等间隔（gaps = n-1）；
      - ``space-evenly``：含边缘等间隔（slots = n+1）；
      - ``space-around``：每子两侧等半间隔（边缘半间隔、中间整间隔，2n 单位）。

    余数（extra % slots）逐个加到前若干个间隔上（视觉差 ≤1 列，可接受）。

    ★ BUG-15（review 方向）：x 重排后整棵子树平移（``_place_child_x`` 经
    ``_translate_subtree_x``）——修复前直接 ``cb.x = cx`` 仅改直接子节点，
    嵌套容器内后代 x 陈旧 → 文本与边框错位。

    Args:
        children: 直接 host 子节点（已测量，layout_box 非 None）。
        justify: space-between / space-around / space-evenly。
        start_x: 内边距后的起始 x。
        margin: 子节点间距（每子累计）。
        extra: 待分配的剩余宽度（>0 才有意义）。
    """

    def _place_child_x(child: Fiber, cx: int) -> None:
        """放置子节点到目标 x 并平移整棵子树（后代随动）。"""
        cb = child.layout_box
        delta = cx - cb.x
        if delta:
            _translate_subtree_x(child, delta)
        else:
            cb.x = cx
            child.layout_box = cb

    n = len(children)
    if n == 0:
        return
    if justify == "space-between":
        gaps = n - 1
        per = extra // gaps if gaps else 0
        rem = extra % gaps if gaps else 0
        cx = start_x
        for i, child in enumerate(children):
            cb = child.layout_box
            _place_child_x(child, cx)
            cx += cb.w
            if i < gaps:
                cx += margin + per + (1 if i < rem else 0)
    elif justify == "space-evenly":
        slots = n + 1
        per = extra // slots
        rem = extra % slots
        gaps = [per] * slots
        for i in range(rem):
            gaps[i] += 1
        cx = start_x + gaps[0]
        for i, child in enumerate(children):
            cb = child.layout_box
            _place_child_x(child, cx)
            cx += cb.w + margin + gaps[i + 1]
    else:  # space-around：2n 半间隔（边缘半间隔、中间整间隔）
        half_units = 2 * n
        per = extra // half_units
        rem = extra % half_units
        gaps = [per if i in (0, n) else per * 2 for i in range(n + 1)]
        for i in range(rem):
            gaps[i % (n + 1)] += 1
        cx = start_x + gaps[0]
        for i, child in enumerate(children):
            cb = child.layout_box
            _place_child_x(child, cx)
            cx += cb.w + margin + gaps[i + 1]


def _reflow_subtree(fiber: Fiber, new_y: int, new_x: int | None = None) -> None:
    """递归重排 fiber 子树孙节点坐标（flexShrink 高度修改后使用，方向1）。

    flexShrink 修改直接子节点高度后仅重排直接子节点 y——孙节点 y 在 shrink
    前按原高度推算，shrink 后陈旧（下一帧 ``_measure`` 才会按新高度重排）。
    本函数在 shrink 路径内逐层将孙节点坐标累加重排（与 flexGrow 分支的
    ``cb.y = cursor_y`` 重排语义一致；仅本帧 shrink 路径内生效，``_measure``
    仍是布局唯一真源）。

    BUG-3（方向3 修复）：区分 flexDirection——column 容器子节点纵向堆叠
    （y 累加），row 容器子节点横向排列（x 累加、y 保持内边距基准）。修复前
    一律按纵向堆叠，row 容器 flexShrink 后子节点被错误竖排。

    Args:
        fiber: 待重排的 fiber（其 layout_box 非 None）。
        new_y: 该 fiber 的新 y 坐标。
        new_x: 该 fiber 的新 x 坐标（None 表示保持原 x）。
    """
    cb = fiber.layout_box
    if cb is None:
        return
    cb.y = new_y
    if new_x is not None:
        cb.x = new_x
    fiber.layout_box = cb
    pad_l, pad_r, pad_t, pad_b = _resolve_padding(fiber)
    # ★ 健壮性（PERF-12 同批）：``fiber.props.get("border", 0)`` 在 props 显式
    #   传 ``None``（键存在但值为 None）时返回 None → ``if border:`` 为 False
    #   → border 保持 None → ``cursor_x = cb.x + pad_l + border`` 崩溃。统一
    #   用 ``or 0`` 兜底（None/0 归 0；非法值走 try/except 归 0）。
    border = fiber.props.get("border") or 0
    if border:
        try:
            border = max(0, int(border))
        except (TypeError, ValueError):
            border = 0
    margin = fiber.props.get("margin") or 0
    if margin:
        try:
            margin = max(0, int(margin))
        except (TypeError, ValueError):
            margin = 0
    gap = fiber.props.get("gap")
    if gap is not None:
        try:
            spacing = max(0, int(gap))
        except (TypeError, ValueError):
            spacing = margin
    else:
        spacing = margin
    direction = fiber.props.get("flexDirection", "column")
    if direction == "row":
        # row：横向排列——子节点 x 累加，y 保持内边距基准（纵向偏移由
        # alignItems 承担；与 _measure row 分支语义一致）。
        cursor_x = cb.x + pad_l + border
        for child in layout_children(fiber):
            _reflow_subtree(child, new_y + pad_t + border, cursor_x)
            ccb = child.layout_box
            if ccb is not None:
                cursor_x += ccb.w + spacing
    else:
        # column：纵向堆叠——子节点 y 累加（默认方向，与既有语义一致）。
        cursor_y = new_y + pad_t + border
        for child in layout_children(fiber):
            _reflow_subtree(child, cursor_y)
            ccb = child.layout_box
            if ccb is not None:
                cursor_y += ccb.h + spacing


def _translate_subtree_y(fiber: Fiber, delta_y: int) -> None:
    """整体平移 fiber 子树（含自身）的 y 坐标（方向3 探针复用修复）。

    探针测量（fill=False column 容器）把子树全部按 ``inner_y`` 测量（y 重叠），
    主循环复用盒时须将整棵子树平移到 ``cursor_y``——仅更新自身 box 会导致
    第 2+ 个子节点的后代 y 停留在 ``inner_y`` 基准（与首个子树重叠）。
    本函数保持 w/h/x 不变，只平移 y（delta_y 为相对偏移，可为负）。

    方向3（BUG-2 关联修复）：**不遍历子树根自身的 sibling 链**——调用方以
    单个直接子节点为参数（探针复用 / alignItems 偏移），仅须平移该子节点及
    其**全部后代**；遍历子树根自身 sibling 会把后续兄弟节点一并平移（其后
    再被循环各自平移 → 重复偏移）。
    ★ BUG-14 修复：**遍历后代 sibling 链**（``child + child.sibling``）——
    修复前仅递归 ``fiber.child``（首子链），嵌套容器内第 2+ 个子节点
    （child 的 sibling）停留在旧坐标 → alignItems/alignSelf/探针复用偏移
    后嵌套多子容器文本/边框错位（确定性渲染错误，见
    ``test_translate_subtree_multi_child``）。

    Args:
        fiber: 待平移的子树根（其 layout_box 非 None）。
        delta_y: y 偏移量（像素/行）。
    """
    if fiber is None:
        return
    if fiber.layout_box is not None:
        cb = fiber.layout_box
        cb.y += delta_y
        fiber.layout_box = cb
    child = fiber.child
    while child is not None:
        _translate_subtree_y(child, delta_y)
        child = child.sibling


def _translate_subtree_x(fiber: Fiber, delta_x: int) -> None:
    """整体平移 fiber 子树（含自身）的 x 坐标（alignItems/alignSelf 偏移修复）。

    column alignItems（center/flex-end）与 alignSelf 对子节点做横向偏移时，
    只改子容器自身 layout_box.x 会令其后代停留在原 x 基准（嵌套容器内
    TEXT/边框错位——TEXT 按未偏移 x 绘制、边框按偏移后 x 绘制）。整棵子树
    平移 delta_x 保持后代相对位置不变。

    本函数保持 w/h/y 不变，只平移 x（delta_x 为相对偏移，可为负）。不遍历
    子树根自身的 sibling 链（仅平移参数指定子树及全部后代，与
    ``_translate_subtree_y`` 一致）。★ BUG-14：遍历后代 sibling 链
    （``child + child.sibling``）——修复前仅递归首子链，嵌套多子容器错位。

    Args:
        fiber: 待平移的子树根（其 layout_box 非 None）。
        delta_x: x 偏移量（像素/列）。
    """
    if fiber is None:
        return
    if fiber.layout_box is not None:
        cb = fiber.layout_box
        cb.x += delta_x
        fiber.layout_box = cb
    child = fiber.child
    while child is not None:
        _translate_subtree_x(child, delta_x)
        child = child.sibling


def _measure(fiber: Fiber, x: int, y: int, avail_w: int, fill: bool = True) -> LayoutBox:
    """递归测量并赋值 layout_box。返回该 fiber 的 LayoutBox。

    Args:
        fiber: host fiber。
        x, y: 父容器内偏移（文档坐标系）。
        avail_w: 可用宽度。
        fill: True=填充可用宽度（column 默认）；False=内容自适应宽度（row）。
    """
    ftype = fiber.type
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
        measure_fn = host[0]
        w, h = measure_fn(fiber, avail_w)
        box = LayoutBox(x, y, w, h)
        fiber.layout_box = box
        return box

    # ── 叶子：TEXT ──
    if ftype == "text":
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
            width = _clamp_width(fiber, max(0, min(avail_w, content_w)))
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
            # ★ 契约说明（BUG-71 复核）：引用级命中依赖「styled 列表不可变」——
            #   同引用 + 同长度 + 内容被**原地替换**（如 ``styled[3] = new_run``）
            #   不会触发重算（长度/指纹均未检查）。当前全部调用方遵守契约
            #   （model._replace_committed_line 刻意用 ``list.copy()`` 换引用，
            #   chat_view 冻结 runs 列表不被修改）；React 同语义（props 不可变）。
            #   若未来新增原地修改 styled 的调用方，须先换新列表引用。
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
        box = LayoutBox(x, y, width, h)
        fiber.layout_box = box
        return box

    # ── 叶子：SPACER ──
    if ftype == "spacer":
        if explicit_w is not None:
            # 方向1 步骤3：width 畸形兜底（复用 _resolve_width）
            width = _resolve_width(fiber, avail_w)
        else:
            width = _clamp_width(fiber, avail_w if fill else 1)
        h = fiber.props.get("height", 1)
        try:
            h = max(0, int(h))
        except (TypeError, ValueError):
            h = 1
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
        except (TypeError, ValueError):
            border = 0
    margin = fiber.props.get("margin") or 0
    if margin:
        try:
            margin = max(0, int(margin))
        except (TypeError, ValueError):
            margin = 0
    # ★ gap（完善 ink flexbox）：子节点间距——``gap`` 优先于 ``margin``
    #   （React Ink 现代 flexbox 语义：gap 仅影响兄弟间距，不影响外边距）。
    #   同时存在时 gap 胜出（显式 gap 意图明确）；缺省回退 margin。
    gap = fiber.props.get("gap")
    if gap is not None:
        try:
            gap = max(0, int(gap))
        except (TypeError, ValueError):
            gap = margin
    else:
        gap = margin
    #: 兄弟间距统一值（row 用横向、column 用纵向）
    spacing = gap

    inner_x = x + pad_l + border
    inner_y = y + pad_t + border
    pad_h = pad_l + pad_r  # 横向 padding 总量
    pad_v = pad_t + pad_b  # 纵向 padding 总量
    children = layout_children(fiber)
    direction = fiber.props.get("flexDirection", "column")

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
                except (TypeError, ValueError):
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
                fiber, max(0, min(avail_w, content_w + pad_h + 2 * border)),
            )
        # ★ row flexGrow（方向1 完善 flexbox）：显式宽度富余时按 flexGrow 分配
        #   额外宽度（横向主轴 grow——修复前 flexGrow 仅作用于 column 高度）。
        inner_w_row = max(0, width - (pad_h + 2 * border))
        used_w = cursor_x - inner_x
        extra_w = max(0, inner_w_row - used_w)
        grow_total = 0
        for child in children:
            grow_total += _flex_grow(child)
        if grow_total > 0 and extra_w > 0:
            per = extra_w // grow_total
            remainder = extra_w % grow_total
            g_idx = 0
            old_xs = [child.layout_box.x for child in children]
            for child in children:
                g = _flex_grow(child)
                if g > 0:
                    cb = child.layout_box
                    cb.w += per * g + (1 if g_idx < remainder else 0)
                    child.layout_box = cb
                    g_idx += 1
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
        # ★ alignSelf（方向3 完善 react ink）：子级 ``alignSelf`` 覆盖父
        #   alignItems（row 横轴——纵向偏移；center/flex-end/flex-start）。
        align = fiber.props.get("alignItems", "stretch")
        if (align in ("center", "flex-end") or any(
            child.props.get("alignSelf") in ("center", "flex-end", "flex-start")
            for child in children
        )) and row_h > 0:
            for child in children:
                cb = child.layout_box
                if cb is not None and cb.h < row_h:
                    self_align = child.props.get("alignSelf")
                    eff_align = self_align if self_align in (
                        "center", "flex-end", "flex-start",
                    ) else align
                    if eff_align == "center":
                        delta = (row_h - cb.h) // 2
                        # 整棵子树平移（仅改自身 box.y 会让后代 y 停留在原
                        # 基准——嵌套容器内 TEXT/边框错位，alignItems 偏移修复）
                        if delta:
                            _translate_subtree_y(child, delta)
                    elif eff_align == "flex-end":
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
                fiber, max(0, min(avail_w, probe_w + pad_h + 2 * border)),
            )
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
                except (TypeError, ValueError):
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

    box = LayoutBox(x, y, width, h)
    fiber.layout_box = box
    return box


def layout_tree(root_fiber: Fiber, width: int) -> int:
    """布局整棵 host 树。

    Args:
        root_fiber: 根 fiber（ROOT 或 APP host）。
        width: 文档宽度（终端列宽）。

    Returns:
        文档总高度（行数）。
    """
    root = _skip_function(root_fiber) or root_fiber
    box = _measure(root, 0, 0, width)
    return box.h


__all__ = [
    "LayoutBox",
    "layout_tree",
    "layout_children",
    "wrap_text_lines",
]
