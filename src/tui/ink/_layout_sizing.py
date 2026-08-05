"""布局尺寸解析 — width/height/padding/flexGrow/flexShrink 属性归一化。

模块边界（2026-08-05 架构优化）：从 ``ink/layout.py`` 拆分——尺寸解析为
纯 props → 整数换算（无布局副作用），独立成模块后 ``_measure`` 主循环专注
测量编排（单一职责）。本模块依赖 ``fiber``（Fiber 结构）与 ``_width``
（字符宽度，经 ``wcswidth_simple``），**不依赖任何其他 layout 子模块**
（布局算法入口在 ``_layout_measure``/``layout``）。

依赖方向（单向无环）：
  ``_layout_sizing`` → fiber / _width
  ``_layout_tree`` → fiber
  ``_layout_transform`` → _layout_tree / _layout_sizing
  ``_layout_flex`` → _layout_transform
  ``_layout_measure`` → sizing/tree/transform/flex
  ``_layout_absolute`` → sizing/tree/transform/measure
  ``layout``（公共门面）→ 全部
"""

from __future__ import annotations

from .fiber import Fiber


def _resolve_length(value, avail: int) -> int:
    """解析长度属性为整数（非负）。

    - int/数字字符串 → 原样（``max(0, int(value))``）；
    - ``"50%"`` 百分比 → ``avail * pct / 100``（React Ink 百分比尺寸语义，
      相对可用宽度/高度）；
    - 畸形值（None/对象/畸形串）→ 回退 avail。
    """
    if isinstance(value, str) and value.endswith("%"):
        try:
            pct = float(value[:-1])
            return max(0, int(avail * pct / 100.0))
        except (TypeError, ValueError, OverflowError):
            return avail
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return avail


def _resolve_width(fiber: Fiber, avail: int) -> int:
    """解析宽度：显式 width 优先（含百分比），否则内容/可用宽度（含 min/max 夹取）。

    完善 react ink：``minWidth``/``maxWidth`` 对解析结果钳制（与
    ``_resolve_height`` 的 minHeight/maxHeight 对称）。宽高属性解析
    收敛——width 显式 + min/max 夹取。width 支持 ``"50%"`` 百分比（相对
    avail）。

    ★ E1（显式 width 超 avail 钳制）：显式 ``width`` 解析结果超可用宽度时
    钳制到 avail——保证 ``box.w <= 文档宽``，维护行级 diff 宽度不变量
    （行宽 > 终端宽度会破坏 diff/光标定位）。钳制在 ``_clamp_width`` 之前：
    ``minWidth`` 显式要求超宽时仍由 ``_clamp_width`` 提升（保留既有
    minWidth/maxWidth 语义）。

    ★ 已知限制（review P3）：绝对整数值 ``minWidth > avail``（如 width=200/
    avail=80/minWidth=150）仍能把 ``box.w`` 抬到 > 文档宽（``_clamp_width``
    对 min 不钳制 avail）——这是 React Ink min-width 语义的有意保留（调用方
    显式声明最小宽度），本修复仅覆盖「显式 width 本身超宽」这一最常见路径。
    """
    w = fiber.props.get("width")
    if w is None:
        resolved = avail
    else:
        resolved = _resolve_length(w, avail)
        if resolved > avail:
            resolved = avail  # E1：显式 width 超可用宽度时钳制到 avail
    return _clamp_width(fiber, resolved, avail)


def _clamp_width(fiber: Fiber, w: int, avail: int | None = None) -> int:
    """对宽度应用 minWidth/maxWidth 钳制（内容推导/填充宽度共用）。

    min/max 百分比相对 ``avail``（可用宽度）；avail 缺省时百分比回退
    w（防御）。

    ★ 性能（PERF-7）：无 ``minWidth``/``maxWidth`` 属性（绝大多数节点）走
    快速路径直接 ``max(0, w)``——免 2 次 ``props.get`` + 类型兜底。
    """
    props = fiber.props
    if "minWidth" not in props and "maxWidth" not in props:
        return w if w > 0 else 0
    mn = props.get("minWidth")
    if mn is not None:
        try:
            w = max(_resolve_length(mn, avail if avail is not None else w), w)
        except (TypeError, ValueError, OverflowError):
            pass
    mx = props.get("maxWidth")
    if mx is not None:
        try:
            w = min(_resolve_length(mx, avail if avail is not None else w), w)
        except (TypeError, ValueError, OverflowError):
            pass
    return max(0, w)


def _resolve_height(fiber: Fiber, content_h: int) -> int:
    """解析高度：显式 height 属性优先（含百分比，相对 ``parent_h``），
    否则内容推导（含 min/max 夹取）。

    ``height="50%"`` 百分比需要父容器确定高度（``parent_h`` 非 None 时
    解析为 ``parent_h * pct / 100``）；父高度未知（内容驱动，parent_h 为
    None）时百分比回退内容高度（React Ink 语义：父高度未确定时百分比
    无效）。
    """
    h = content_h
    height = fiber.props.get("height")
    parent_h = getattr(fiber, "_parent_avail_h", None)
    if height is not None:
        if isinstance(height, str) and height.endswith("%"):
            if parent_h is not None:
                try:
                    pct = float(height[:-1])
                    h = max(0, int(parent_h * pct / 100.0))
                except (TypeError, ValueError, OverflowError):
                    pass
            # 父高度未知 → 百分比无效，保持内容高度（React Ink 语义）
        else:
            try:
                h = max(0, int(height))
            except (TypeError, ValueError, OverflowError):
                pass
    mn = fiber.props.get("minHeight")
    if mn is not None:
        try:
            h = max(int(mn), h)
        except (TypeError, ValueError, OverflowError):
            pass
    mx = fiber.props.get("maxHeight")
    if mx is not None:
        try:
            h = min(int(mx), h)
        except (TypeError, ValueError, OverflowError):
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
    except (TypeError, ValueError, OverflowError):
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
    except (TypeError, ValueError, OverflowError):
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
        except (TypeError, ValueError, OverflowError):
            return 0

    pad = _int(props.get("padding", 0))
    pad_x = _int(props.get("paddingX", pad))
    pad_y = _int(props.get("paddingY", pad))
    pad_l = _int(props.get("paddingLeft", pad_x))
    pad_r = _int(props.get("paddingRight", pad_x))
    pad_t = _int(props.get("paddingTop", pad_y))
    pad_b = _int(props.get("paddingBottom", pad_y))
    return (pad_l, pad_r, pad_t, pad_b)


def _abs_int(value) -> int | None:
    """解析绝对定位锚点值（top/left/right/bottom）为 int；畸形回退 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _apply_aspect_ratio(fiber: Fiber, width: int, h: int) -> tuple[int, int]:
    """aspectRatio（完善 react ink v6）：宽/高缺省维度由比例推导。

    ``aspectRatio = width / height``。React Ink/Yoga 语义：需配合至少一个
    尺寸约束（width/height/minWidth/maxWidth/minHeight/maxHeight）使用——
    维度之一由显式属性确定、另一缺省时由 ratio 推导。畸形值（None/<=0/
    非数字）忽略，原样返回。

    Args:
        fiber: 容器 host fiber。
        width: 已解析的宽度。
        h: 已解析的高度。

    Returns:
        (width, h)——应用 aspectRatio 后的尺寸。
    """
    ar = fiber.props.get("aspectRatio")
    if ar is None:
        return width, h
    try:
        ar = float(ar)
    except (TypeError, ValueError, OverflowError):
        return width, h
    if ar <= 0:
        return width, h
    if fiber.props.get("width") is not None and fiber.props.get("height") is None:
        return width, max(0, int(round(width / ar)))
    if fiber.props.get("height") is not None and fiber.props.get("width") is None:
        return max(0, int(round(h * ar))), h
    return width, h


__all__ = [
    "_resolve_length",
    "_resolve_width",
    "_clamp_width",
    "_resolve_height",
    "_flex_grow",
    "_flex_shrink",
    "_resolve_padding",
    "_abs_int",
    "_apply_aspect_ratio",
]
