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
    """styled runs 的自然内容宽度（单行拼接宽度）。"""
    total = 0
    for r in runs:
        total += wcswidth_simple(getattr(r, "text", str(r)))
    return total


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
    """将文本按显示宽度换行为 Line 列表（CJK 安全）。"""
    if width <= 0:
        return [Line.of(text, style)] if text else []
    from .helpers import wrap_runs_by_width
    return wrap_runs_by_width([StyledRun(text, style)], width)


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
    """对宽度应用 minWidth/maxWidth 钳制（内容推导/填充宽度共用）。"""
    mn = fiber.props.get("minWidth")
    if mn is not None:
        try:
            w = max(int(mn), w)
        except (TypeError, ValueError):
            pass
    mx = fiber.props.get("maxWidth")
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


def _resolve_padding(fiber: Fiber) -> tuple[int, int]:
    """解析 padding，返回 (pad_h, pad_v)（React Ink 语义，方向3 完善）。

    - ``padding`` 设置四边（均一值）；``paddingX`` 覆盖左右（横向）；
      ``paddingY`` 覆盖上下（纵向）——``paddingX/Y`` 缺省回退 ``padding``。
    - 畸形值（None/对象/畸形串）兜底 0，与 width/height/border/margin 一致。
    """
    def _int(v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    pad = _int(fiber.props.get("padding", 0))
    pad_h = _int(fiber.props.get("paddingX", pad))
    pad_v = _int(fiber.props.get("paddingY", pad))
    return (pad_h, pad_v)


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

    Args:
        children: 直接 host 子节点（已测量，layout_box 非 None）。
        justify: space-between / space-around / space-evenly。
        start_x: 内边距后的起始 x。
        margin: 子节点间距（每子累计）。
        extra: 待分配的剩余宽度（>0 才有意义）。
    """
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
            cb.x = cx
            cx += cb.w
            if i < gaps:
                cx += margin + per + (1 if i < rem else 0)
            child.layout_box = cb
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
            cb.x = cx
            cx += cb.w + margin + gaps[i + 1]
            child.layout_box = cb
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
            cb.x = cx
            cx += cb.w + margin + gaps[i + 1]
            child.layout_box = cb


def _reflow_subtree(fiber: Fiber, new_y: int) -> None:
    """递归重排 fiber 子树孙节点 y 坐标（flexShrink 高度修改后使用，方向1）。

    flexShrink 修改直接子节点高度后仅重排直接子节点 y——孙节点 y 在 shrink
    前按原高度推算，shrink 后陈旧（下一帧 ``_measure`` 才会按新高度重排）。
    本函数在 shrink 路径内逐层将孙节点 y 累加重排（与 flexGrow 分支的
    ``cb.y = cursor_y`` 重排语义一致；仅本帧 shrink 路径内生效，``_measure``
    仍是布局唯一真源）。

    Args:
        fiber: 待重排的 fiber（其 layout_box 非 None）。
        new_y: 该 fiber 的新 y 坐标。
    """
    cb = fiber.layout_box
    if cb is None:
        return
    cb.y = new_y
    fiber.layout_box = cb
    pad_h, pad_v = _resolve_padding(fiber)
    border = fiber.props.get("border", 0)
    margin = fiber.props.get("margin", 0)
    try:
        border = max(0, int(border))
    except (TypeError, ValueError):
        border = 0
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
    cursor_y = new_y + pad_v + border
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

    Args:
        fiber: 待平移的子树根（其 layout_box 非 None）。
        delta_y: y 偏移量（像素/行）。
    """
    f = fiber
    while f is not None:
        if f.layout_box is not None:
            cb = f.layout_box
            cb.y += delta_y
            f.layout_box = cb
        _translate_subtree_y(f.child, delta_y)
        f = f.sibling


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
        )
        styled = fiber.props.get("styled")
        text = str(fiber.props.get("children", ""))
        style = fiber.props.get("style")
        # ★ textWrap 模式（方向B 步骤12 / 完善 ink）：
        #   "wrap"（默认，现行为）/ "truncate" / "truncate-end"（单行截断省略号，
        #   末尾省略号）/"truncate-start"（省略号在开头，保留尾部）/
        #   "truncate-middle"（保留头尾，中间省略号）——react-ink 完整语义。
        text_wrap = fiber.props.get("textWrap", "wrap")
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
        # ★ 换行缓存（方向2 P1+P3）：结构 ``(ref, (width, text_wrap), style_fp, lines)``
        #   - cache[0] = ref：styled 列表引用（引用级快速路径）或 text 字符串
        #   - cache[1] = (width, text_wrap)
        #   - cache[2] = style_fp：稳定样式指纹（值驱动，BUG-T1——替代 id()）
        #   - cache[3] = lines：换行结果（跨帧复用）
        #   旧结构 ``(key, lines)`` 的 key 含 ``cache_text``（每帧先 join 再比较的
        #   完整拼接文本副本）——新结构不再持有该副本（P3 内存优化；ref 已被组件
        #   树引用，不额外占用）。P1 热路径优化：styled 静态历史（model 冻结行
        #   引用，如 committed_lines）同引用跨帧复用时直接复用 lines，免每帧
        #   O(chars) join + O(runs) 指纹计算（仅首次 miss 时计算）。
        cache = getattr(fiber, "_wrap_cache", None)
        cache_wt = (width, text_wrap)
        if (
            styled is not None
            and cache is not None
            and cache[0] is styled
            and cache[1] == cache_wt
        ):
            # 引用级快速路径：同 styled 引用 + (width, text_wrap) 不变 → 复用 lines
            # （runs 在本分支无下游消费——h 仅由 lines 推导；死拷贝移除）
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
                fiber._wrap_cache = (ref, cache_wt, style_fp, lines)
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
    # ★ paddingX/paddingY（方向3 完善 react ink）：横向/纵向独立内边距；
    #   缺省回退 ``padding`` 均一值（既有行为不变）。
    pad_h, pad_v = _resolve_padding(fiber)
    border = fiber.props.get("border", 0)
    try:
        border = max(0, int(border))
    except (TypeError, ValueError):
        border = 0
    margin = fiber.props.get("margin", 0)
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

    inner_x = x + pad_h + border
    inner_y = y + pad_v + border
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
            row_inner_w = max(0, width - 2 * (pad_h + border))
        else:
            row_inner_w = max(0, avail_w - 2 * (pad_h + border))
        cursor_x = inner_x
        row_h = 0
        n_children = len(children)
        for i, child in enumerate(children):
            remaining = max(0, row_inner_w - (cursor_x - inner_x))
            cbox = _measure(child, cursor_x, inner_y, remaining, fill=False)
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
                fiber, max(0, min(avail_w, content_w + 2 * (pad_h + border))),
            )
        # ★ row flexGrow（方向1 完善 flexbox）：显式宽度富余时按 flexGrow 分配
        #   额外宽度（横向主轴 grow——修复前 flexGrow 仅作用于 column 高度）。
        inner_w_row = max(0, width - 2 * (pad_h + border))
        used_w = cursor_x - inner_x
        extra_w = max(0, inner_w_row - used_w)
        grow_total = 0
        for child in children:
            grow_total += _flex_grow(child)
        if grow_total > 0 and extra_w > 0:
            per = extra_w // grow_total
            remainder = extra_w % grow_total
            g_idx = 0
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
                cb.x = cx
                cx += cb.w
                if i < len(children) - 1:
                    cx += spacing
                child.layout_box = cb
            # grow 消费全部剩余 → justify 无偏移（CSS flexbox 语义）
            used_w = inner_w_row
            extra_w = 0
        # ★ row justifyContent（方向1 完善 flexbox）：横向主轴剩余宽度分布——
        #   center → 所有子节点 x += extra//2；flex-end → x += extra；
        #   space-between/space-around/space-evenly → 按间隔重排（_reflow_row_justify）。
        #   flex-start（默认）不变。与 row flexGrow 语义重叠（grow 先分）。
        justify = fiber.props.get("justifyContent", "flex-start")
        if justify in ("center", "flex-end") and extra_w > 0:
            offset = extra_w // 2 if justify == "center" else extra_w
            for child in children:
                cb = child.layout_box
                if cb is not None:
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
                        cb.y += (row_h - cb.h) // 2
                    elif eff_align == "flex-end":
                        cb.y += (row_h - cb.h)
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
                    max(0, avail_w - 2 * (pad_h + border)), fill=False,
                )
                probe_boxes.append(probe_box)
                if probe_box.w > probe_w:
                    probe_w = probe_box.w
            width = _clamp_width(
                fiber, max(0, min(avail_w, probe_w + 2 * (pad_h + border))),
            )
        inner_w = max(0, width - 2 * (pad_h + border))
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
                        cb.x += (inner_w - cb.w) // 2
                    elif eff_align == "flex-end":
                        cb.x += (inner_w - cb.w)
                    # flex-start / stretch：不偏移
                    child.layout_box = cb

    content_h = total_h if children else 0
    h = content_h + 2 * (pad_v + border)
    h = _resolve_height(fiber, h)

    # flexShrink：显式高度不足（h < 内容高）且 children 含 shrink>0 时，按
    #   shrink 权重比例缩减子节点高度（每子至少保留 1 行，deficit 按 shrink
    #   权重分配）——与 flexGrow 余数分配对称（方向2 U4）。方向1：余数分配
    #   收敛至 _distribute_extra（余数仅分配给 shrink>0 节点，权重 0 节点不
    #   参与）+ shrink 后孙节点递归重排（_reflow_subtree）。
    if h < content_h + 2 * (pad_v + border) and children:
        shrink_total = 0
        for child in children:
            shrink_total += _flex_shrink(child)
        deficit = (content_h + 2 * (pad_v + border)) - h
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
    if h > content_h + 2 * (pad_v + border) and children:
        grow_total = 0
        for child in children:
            grow_total += _flex_grow(child)
        remaining = h - (content_h + 2 * (pad_v + border))
        if grow_total > 0 and remaining > 0:
            # P1-4：余数分配修改子节点高度后 helper 内**重排 y 坐标**——
            # _measure 按原高度分配 y（如 BOX(height=10)+两个 TEXT flexGrow
            # 2/1 → text0.h=6 但 y=0、text1.h=4 但 y=1 垂直重叠）；写回
            # cb.y = cursor_y 后光标再按新高度累加。
            _distribute_extra(
                children, _flex_grow, remaining, inner_y, spacing,
            )

    # ★ justifyContent（方向3，已实现）：column 纵向对齐基于 flexGrow 分配后
    #   剩余空间——center → 所有子节点 y += extra//2；flex-end → y += extra；
    #   flex-start（默认）不变。与 flexGrow 语义重叠（都消费剩余空间）：
    #   flexGrow 先分（子节点高度增长），justify 基于分配后剩余；grow 分尽则
    #   extra≈0 无偏移（符合 CSS flexbox 语义）。性能：仅在有剩余空间时计算
    #   （不引入每帧 O(树) 无条件遍历）。
    #   方向1（完善 flexbox）：仅 column 走本块——row 的横向 justifyContent
    #   已在 row 分支处理（本块 n 仅在 column 分支定义，row 路径引用会
    #   UnboundLocalError，原实现隐含依赖「row 无 justifyContent 消费方」）。
    justify = fiber.props.get("justifyContent", "flex-start")
    if direction == "column" and justify in ("center", "flex-end") and children:
        n = len(children)
        children_total = 0
        for child in children:
            cb = child.layout_box
            children_total += cb.h if cb is not None else 0
        if n > 1:
            children_total += spacing * (n - 1)
        extra = max(0, h - 2 * (pad_v + border) - children_total)
        if extra > 0:
            offset = extra // 2 if justify == "center" else extra
            for child in children:
                cb = child.layout_box
                if cb is not None:
                    cb.y += offset
                    child.layout_box = cb

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
