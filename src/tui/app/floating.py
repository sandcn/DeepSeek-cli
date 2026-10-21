"""通用浮动窗口 — 规格 / 渲染包装 / 层级排序 / ``/loop`` 循环浮窗内容。

设计目标（2026-10-21 用户需求）：

  - **通用**：任何调用方都能在任意位置开一个浮动窗口，窗口内部可以是任意
    React Ink 组件（函数组件 + hooks + 标准控件全部可用——渲染包装把
    ``spec.render`` 作为**函数组件**挂进 fiber 树，而不是直接调用）；
  - **不破坏原内容**：浮窗是绝对定位层（不占文档高度），关闭后组件从树中
    移除，下一帧整帧重建 → 行级 diff 自动把被覆盖区域恢复为原内容；
  - **任意坐标**：``x``（列 / left / center / right）+ ``bottom``（底行 /
    ``input_top`` / ``input_bottom`` / ``doc_bottom`` / ``doc_top``）或
    ``top``（顶行）——底行锚点时窗口**向上展开**；也支持绝对行列；
  - **多窗口叠加**：按 ``priority`` 升序加入 App 根（高优先级后绘制 →
    覆盖低优先级），可叠在同一位置；
  - 边框 + 不透明背景（默认黑色）保证覆盖区域不穿透底层内容。

层级关系：
  ``app.floating``（本模块）→ ``src.tui.ink``（Element / 组件）+ 同层纯数据
  ``_state_types``；定位计算在 ``ink/_floating.py``（布局后按 props 解析并
  平移子树）。本模块不反向依赖 ``app.app``（App 单向导入本模块）。

浮窗 props（写入窗口 host 元素，供 ``ink._floating`` 读取）：
  ``dataFloatWindow`` / ``floatX`` / ``floatBottom`` / ``floatTop`` /
  ``floatOffsetX`` / ``floatOffsetY``。
"""

from __future__ import annotations

from typing import Any, Callable

from src.tui._width import truncate_width
from src.tui.ink import BOX, FRAGMENT, TEXT, h

#: 默认浮窗宽度（列，含边框）。内容自适应（spec.width=None）时不设置。
DEFAULT_FLOAT_WIDTH = 30

#: 循环浮窗固定宽度（列）——容纳「总次数 · 第 N/M 轮」「当前运行次数」。
LOOP_FLOAT_WIDTH = 30

#: 浮窗默认背景色（不透明覆盖；None = 透明，底层内容会从空格处透出）。
DEFAULT_FLOAT_BACKGROUND = "black"


class FloatingWindowSpec:
    """浮动窗口规格（纯数据）。

    Attributes:
        id: 窗口唯一标识（重复打开同 id 时替换；关闭按 id 移除）。
        render: 内容组件——``Callable[[dict], Element]``，props 为
            ``{"model", "width", "window"}``（``width`` = 内容可用宽度，已扣除
            边框与内边距）。作为**函数组件**挂载，hooks/use_state 可用。
        priority: 层级优先级（数值越大越靠上——App 按升序拼装，后者绘制在
            上层；同优先级按加入顺序）。
        width: 窗口总宽（含边框）；None = 内容自适应宽度。
        x: 左边界列——int / ``"left"`` / ``"center"`` / ``"right"``（默认右）。
        bottom: 窗口**底行**锚点——int / ``"input_top"``（默认，输入区顶部分隔
            线）/ ``"input_bottom"`` / ``"doc_bottom"`` / ``"doc_top"``。
        top: 窗口**顶行**锚点（与 ``bottom`` 同时给出时优先）。
        offset_x / offset_y: 额外列/行偏移（``bottom`` 锚点 + ``offset_y=-1``
            = 窗口底边位于锚点行上方一行）。
        border: 是否绘制单线边框。
        border_color: 边框颜色（颜色名或 256 色号；None = 默认深青）。
        background: 背景色（默认黑色，保证覆盖不透明；None = 透明）。
        padding_x / padding_y: 边框内边距。
        title: 可选标题行（显示在内容上方）；空串不显示。
    """

    __slots__ = (
        "id", "render", "priority", "width", "x", "bottom", "top",
        "offset_x", "offset_y", "border", "border_color", "background",
        "padding_x", "padding_y", "title",
    )

    def __init__(
        self,
        id: str,
        render: Callable[[dict], Any] | None = None,
        *,
        priority: int = 0,
        width: int | None = DEFAULT_FLOAT_WIDTH,
        x: Any = "right",
        bottom: Any = "input_top",
        top: Any = None,
        offset_x: int = 0,
        offset_y: int = 0,
        border: bool = True,
        border_color: Any = None,
        background: Any = DEFAULT_FLOAT_BACKGROUND,
        padding_x: int = 1,
        padding_y: int = 0,
        title: str = "",
    ) -> None:
        self.id = str(id)
        self.render = render
        self.priority = priority
        self.width = width
        self.x = x
        self.bottom = bottom
        self.top = top
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.border = bool(border)
        self.border_color = border_color
        self.background = background
        self.padding_x = max(0, int(padding_x))
        self.padding_y = max(0, int(padding_y))
        self.title = str(title or "")

    # ── 复制（更新窗口字段用：不可变替换，避免调用方原地改共享对象） ──
    def copy_with(self, **changes) -> FloatingWindowSpec:
        """返回修改了指定字段的新规格（未知字段忽略，防拼写错误污染对象）。"""
        values = {name: getattr(self, name) for name in self.__slots__}
        for key, value in changes.items():
            if key in values:
                values[key] = value
        return FloatingWindowSpec(**values)

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (
            f"FloatingWindowSpec(id={self.id!r}, priority={self.priority!r}, "
            f"width={self.width!r}, x={self.x!r}, bottom={self.bottom!r}, "
            f"top={self.top!r})"
        )


def _safe_int(value, default: int = 0) -> int:
    """int 归一化（异常值回退默认；bool 按值）。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def FloatingWindow(props: dict) -> object:
    """浮窗渲染包装（函数组件）：边框 + 背景 + 内容组件。

    Props:
        window: ``FloatingWindowSpec``。
        model: AppModel（透传给内容组件）。
        width: 终端宽度（窗口宽度钳制与内容宽度计算）。

    Returns:
        绝对定位 BOX（带 ``dataFloatWindow`` 标记，布局后由
        ``ink._floating.apply_floating_layout`` 定位）。
    """
    win = props.get("window")
    if win is None:
        return h(BOX, {"key": "float-empty"})
    term_width = max(1, _safe_int(props.get("width"), 80))
    pad_x = max(0, _safe_int(getattr(win, "padding_x", 0), 0))
    pad_y = max(0, _safe_int(getattr(win, "padding_y", 0), 0))
    border = 1 if getattr(win, "border", True) else 0

    # ── 窗口宽度：显式宽钳制到终端宽度；None = 内容自适应 ──
    raw_width = getattr(win, "width", None)
    box_w: int | None = None
    if raw_width is not None:
        want = _safe_int(raw_width, DEFAULT_FLOAT_WIDTH)
        # 至少保留 3 列（边框 + 1 列内容）；不超过终端宽度
        box_w = max(3, min(want, term_width))
    # 内容可用宽度（扣除左右边框 + 左右内边距）
    if box_w is not None:
        content_w = max(1, box_w - 2 * border - 2 * pad_x)
    else:
        content_w = max(1, term_width - 2 * border - 2 * pad_x)

    children: list = []
    title = str(getattr(win, "title", "") or "")
    if title:
        children.append(h(TEXT, {
            "key": "float-title",
            "children": truncate_width(title, content_w),
            "bold": True,
        }))
    render = getattr(win, "render", None)
    if callable(render):
        # 作为函数组件挂载（内部 hooks / 标准控件可用）——key 固定保证
        # 跨帧 fiber 复用（窗口内容 use_state 不丢）。
        children.append(h(render, {
            "key": "float-content",
            "model": props.get("model"),
            "window": win,
            "width": content_w,
        }))

    box_props: dict = {
        "position": "absolute",
        # 默认锚点：布局第二遍放在左上角，随后由 apply_floating_layout 平移
        "left": 0,
        "top": 0,
        "dataFloatWindow": win.id,
        "floatX": getattr(win, "x", "right"),
        "floatBottom": getattr(win, "bottom", "input_top"),
        "floatTop": getattr(win, "top", None),
        "floatOffsetX": _safe_int(getattr(win, "offset_x", 0), 0),
        "floatOffsetY": _safe_int(getattr(win, "offset_y", 0), 0),
        "flexDirection": "column",
        "border": border,
        "paddingX": pad_x,
        "paddingY": pad_y,
        "key": f"float-{win.id}",
    }
    if box_w is not None:
        box_props["width"] = box_w
    background = getattr(win, "background", DEFAULT_FLOAT_BACKGROUND)
    if background is not None:
        box_props["backgroundColor"] = background
    border_color = getattr(win, "border_color", None)
    if border_color is not None:
        box_props["borderColor"] = border_color
    return h(BOX, box_props, children)


def floating_layer_elements(model, width: int) -> list:
    """构建浮窗层元素（App 根的直接子元素，按优先级升序）。

    App 把返回值追加在正常界面/全屏视图之后：绝对定位层不占高度；后绘制
    的窗口覆盖先绘制的（高优先级在上）。无浮窗时返回空列表（零额外节点）。
    """
    windows = getattr(model, "floating_windows", None) or []
    if not windows:
        return []
    # sorted 稳定：同优先级保持加入顺序（先加入的在下、后加入的在上）
    ordered = sorted(
        windows,
        key=lambda w: _safe_int(getattr(w, "priority", 0), 0),
    )
    elements: list = []
    for win in ordered:
        wid = getattr(win, "id", None)
        if wid is None:
            continue
        elements.append(h(FloatingWindow, {
            "key": f"float-{wid}",
            "window": win,
            "model": model,
            "width": width,
        }))
    return elements


def LoopFloatingWindow(props: dict) -> object:
    """``/loop`` 循环浮窗内容（总次数 / 提词 / 当前运行次数）。

    数据源：``model.loop_status``（``LoopStatusState``，由 /loop 每轮/每次
    运行前更新）。三行内容：

      1. ``总次数: N · 第 R/N 轮``
      2. 用户提词（超宽截断）
      3. ``当前运行次数: R/2``（每轮第 1 或第 2 次运行）

    ``active`` 为 False 时返回空列表（窗口无内容，正常情况下窗口已关闭）。
    """
    model = props.get("model")
    width = max(1, _safe_int(props.get("width"), 1))
    st = getattr(model, "loop_status", None)
    if st is None or not bool(getattr(st, "active", False)):
        # 函数组件必须返回**单个** Element（返回 list 会被规范化为其 repr
        # 文本）——空态返回空 TEXT（零高度内容）。
        return h(TEXT, {"key": "loop-empty", "children": ""})
    total = max(0, _safe_int(getattr(st, "total", 0), 0))
    round_no = max(0, _safe_int(getattr(st, "round", 0), 0))
    run_no = max(0, _safe_int(getattr(st, "run", 0), 0))
    per_round = max(1, _safe_int(getattr(st, "runs_per_round", 2), 2))

    first = f"总次数: {total}"
    if round_no:
        shown_round = min(round_no, total) if total else round_no
        first += f" · 第 {shown_round}/{total or '?'} 轮"
    prompt = truncate_width(str(getattr(st, "prompt", "") or ""), width)
    current = f"当前运行次数: {run_no or 1}/{per_round}"

    return h(FRAGMENT, {"key": "loop-body"}, [
        h(TEXT, {
            "key": "loop-total",
            "children": truncate_width(first, width),
            "bold": True,
        }),
        h(TEXT, {
            "key": "loop-prompt",
            "children": prompt,
            "dim": True,
        }),
        h(TEXT, {
            "key": "loop-run",
            "children": truncate_width(current, width),
        }),
    ])


def loop_window_spec() -> FloatingWindowSpec:
    """``/loop`` 循环浮窗规格：底部框（状态栏 + 输入区）上方、向上展开、带边框。

    ``bottom="bottom_area"`` 锚定整个底部框顶部（状态栏分隔线），
    ``offset_y=-1`` 使窗口底行位于其上方一行——**窗口所有行都不覆盖底部框**
    （2026-10-21 用户需求：循环浮窗不能遮盖底部框）。水平右对齐终端右侧。
    """
    return FloatingWindowSpec(
        id="loop",
        render=LoopFloatingWindow,
        priority=100,
        width=LOOP_FLOAT_WIDTH,
        x="right",
        bottom="bottom_area",
        offset_y=-1,
        border=True,
        background=DEFAULT_FLOAT_BACKGROUND,
    )


__all__ = [
    "DEFAULT_FLOAT_BACKGROUND",
    "DEFAULT_FLOAT_WIDTH",
    "FloatingWindow",
    "FloatingWindowSpec",
    "LOOP_FLOAT_WIDTH",
    "LoopFloatingWindow",
    "floating_layer_elements",
    "loop_window_spec",
]
