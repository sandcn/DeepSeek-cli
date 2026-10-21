"""通用浮动窗口 — 规格 / 渲染包装 / 层级排序 / ``/loop``、``/usage`` 浮窗内容。

设计目标（2026-10-21 用户需求）：

  - **通用**：任何调用方都能在任意位置开一个浮动窗口，窗口内部可以是任意
    React Ink 组件（函数组件 + hooks + 标准控件全部可用——渲染包装把
    ``spec.render`` 作为**函数组件**挂进 fiber 树，而不是直接调用）；
  - **不破坏原内容**：浮窗是绝对定位层（不占文档高度），关闭后组件从树中
    移除，下一帧整帧重建 → 行级 diff 自动把被覆盖区域恢复为原内容；
  - **任意坐标**：``x``（列 / left / center / right）+ ``bottom``（底行 /
    ``bottom_area``（默认）/ ``input_top`` / ``input_bottom`` / ``doc_bottom``
    / ``doc_top``）或 ``top``（顶行）——底行锚点时窗口**向上展开**；也支持
    绝对行列；
  - **多窗口叠加**：按 ``priority`` 升序加入 App 根（高优先级后绘制 →
    覆盖低优先级），可叠在同一位置；
  - 边框 + 不透明背景（默认黑色）保证覆盖区域不穿透底层内容；
  - **尺寸约束**：内容超高时按 ``max_height`` / 底部框 / 可视区**裁剪**
    （绝不把窗口向下推去覆盖底部框），``scroll_top`` 可查看后续内容；
  - **交互**：``geometry``（运行时矩形）+ ``window_hit_test``（鼠标命中）+
    ``close_on_escape``（Esc 关闭）；其它键位/焦点由内容组件用
    ``use_input``/``useFocus``/``use_modal`` 自行声明（浮窗在主组件树中，
    hooks 全部有效）；
  - **全屏视图**：默认隐藏（模态独占），``over_fullscreen=True`` 显式覆盖。

层级关系：
  ``app.floating``（本模块）→ ``src.tui.ink``（Element / 组件）+ 同层纯数据
  ``_state_types``；定位计算在 ``ink/_floating.py``（布局后按 props 解析并
  平移子树）。本模块不反向依赖 ``app.app``（App 单向导入本模块）。

浮窗 props（写入窗口 host 元素，供 ``ink._floating`` 读取）：
  ``dataFloatWindow`` / ``floatX`` / ``floatBottom`` / ``floatTop`` /
  ``floatOffsetX`` / ``floatOffsetY`` / ``floatMaxHeight`` / ``floatScrollTop`` /
  ``floatAllowCoverBottom`` / ``floatOverFullscreen`` / ``floatGeometry``。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable

from src.tui._format import format_duration
from src.tui._width import truncate_width, wcswidth_simple
from src.tui.core.style import Style
from src.tui.ink import BOX, FRAGMENT, TEXT, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, truncate_runs_ellipsis
from src.tui.ink.hooks import HookStateError

_logger = logging.getLogger(__name__)

#: 默认浮窗宽度（列，含边框）。内容自适应（spec.width=None）时不设置。
DEFAULT_FLOAT_WIDTH = 30

#: 循环浮窗固定宽度（列）——容纳「⟳ /loop … 第 R/N 次」标题行、
#: 进度条、「⏱ 本次 mm:ss … 生成状态」计时行与当前 / 下一轮提词。
LOOP_FLOAT_WIDTH = 34

#: ``/usage`` 用量浮窗 id。
USAGE_WINDOW_ID = "usage"
#: ``/usage`` 浮窗宽度比例（终端宽度的 60%；随终端 resize 每帧自适应）。
USAGE_FLOAT_WIDTH_RATIO = 0.6
#: ``/usage`` 浮窗宽度上限（列）——60% 终端宽但不无限变宽。
USAGE_FLOAT_MAX_WIDTH = 60
#: ``/usage`` 浮窗边框色（亮青）。
_S_USAGE_BORDER = 45

#: 浮窗默认背景色（不透明覆盖；None = 透明，底层内容会从空格处透出）。
DEFAULT_FLOAT_BACKGROUND = "black"

#: ``/loop`` 浮窗配色（与状态栏强调色同族：亮青标题 / 绿色进度 / 浅蓝计时）。
_S_LOOP_ACCENT = Style(fg=45, bold=True)
_S_LOOP_TEXT = Style(fg=252)
_S_LOOP_MUTED = Style(fg=242)
_S_LOOP_BAR_FILL = Style(fg=42)
_S_LOOP_BAR_EMPTY = Style(fg=238)
_S_LOOP_TIME = Style(fg=110, bold=True)
_S_LOOP_PROMPT = Style(fg=250, italic=True)
#: 循环浮窗边框色（亮青，与标题强调色一致；``borderStyle="round"`` 圆角）。
_S_LOOP_BORDER = 45

#: 默认底行锚点（2026-10-21 修复）：底部框（状态栏 + 输入区 / 模态底部视图）
#: **上方一行**——窗口向上展开，底行绝不落在底部框内。修复前默认 ``input_top``
#: 会把窗口底边框正压在输入区顶部分隔线（CPU/MEM 行）上，静默抹掉该行。
DEFAULT_FLOAT_BOTTOM = "bottom_area"


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
        width_ratio: 窗口宽度占**终端宽度**的比例（0<ratio<=1；None = 不使用）。
            与 ``width`` 同时给出时本字段优先；每帧按当前终端宽度重算，因此
            terminal resize 后窗口自动缩放（``/usage`` 浮窗使用 0.6）。
        max_width: 宽度上限（列；None = 不限制）。与 ``width_ratio`` 配合使用
            （如「终端 60%，但不超过 60 列」）。
        x: 左边界列——int / ``"left"`` / ``"center"`` / ``"right"``（默认右）。
        bottom: 窗口**底行**锚点——int / ``"bottom_area"``（默认，底部框上方
            一行）/ ``"input_top"`` / ``"input_bottom"`` / ``"doc_bottom"`` /
            ``"doc_top"``。注意 ``input_top`` 的锚点行就是输入区顶部分隔线
            本身——窗口底行落在该行会**覆盖**它，需要保留分隔线时用
            ``"bottom_area"``。``bottom_area`` / ``input_*`` 锚点需要底部框
            存在，模态全屏视图（无底部框）下窗口自动隐藏。
        top: 窗口**顶行**锚点（与 ``bottom`` 同时给出时优先）。
        offset_x / offset_y: 额外列/行偏移（``bottom`` 锚点 + ``offset_y=-1``
            = 窗口底边位于锚点行上方一行）。
        border: 是否绘制单线边框。
        border_color: 边框颜色（颜色名或 256 色号；None = 默认深青）。
        border_style: 边框变体（``single``/``double``/``round``/``bold``/
            ``classic``/``dashed`` 或自定义字符 dict；None = ``single``）——
            透传给 BOX 的 ``borderStyle``（2026-10-21 美化 ``/loop`` 浮窗时
            新增，圆角边框）。
        background: 背景色（默认黑色，保证覆盖不透明）。**None = 透明**：
            只有窗口实际绘制了字符的列覆盖底层内容，空白列透出下方原内容
            （2026-10-21 修复：此前帧末合成层会清掉整个窗口矩形，透明实际
            等同于抹成空格）。注意：窗口覆盖到**跨列宽字符**（CJK/emoji）时，
            终端无法只覆盖它的某一列——相邻那一列会被清成空白。
        padding_x / padding_y: 边框内边距。
        title: 可选标题行（显示在内容上方）；空串不显示。

    尺寸/交互（2026-10-21 完善）：

        max_height: 窗口**可见高度**上限（行，含边框）；None = 不限制（仍受
            底部框/可视区裁剪，见下）。内容超出可见高度时**裁剪**而不是把
            窗口撑到底部框上面去——保证「浮窗绝不遮挡底部框」恒成立。
        scroll_top: 内容滚动偏移（行）——``max_height`` 裁剪后可用它查看后续
            内容（0 = 从内容顶行开始）。也可经 ``update_floating_window`` 在
            运行时更新（配合内容组件自身的翻页键处理）。
        allow_cover_bottom: True 时允许窗口覆盖底部框（状态栏/输入区）——
            默认 False（窗口被裁到``dataFloatAnchor="bottom_area"`` 上方）。
            覆盖输入区会让终端光标落在窗口内容上，非必要不要打开。
        over_fullscreen: True 时允许窗口在**模态全屏视图**（``model.fullscreen``）
            之上显示；默认 False（全屏视图独占画面，浮窗一律隐藏，与
            ``use_modal`` 的模态语义一致）。
        close_on_escape: True 时窗口内容在树中监听 Esc 并调用 ``on_close``
            关闭窗口（见下）；默认 False（按键仍走输入区——浮窗默认是纯展示层）。
        on_close: ``close_on_escape`` 的关闭回调——由 ``InkBridge.open_floating_window``
            在未显式提供时自动注入（``lambda: bridge.close_floating_window(id)``）。
        idle_static: True 表示窗口内容**只依赖窗口自身 props**（不读 model
            等其他每帧可变状态）→ 允许空闲帧复用（无浮窗时的 60Hz 全量重建
            可省掉）。默认 False（保守：有浮窗即每帧重建，内容不会冻结在旧值）。
        refresh_interval: **时间驱动内容的刷新间隔**（秒；None = 每帧刷新，
            默认）。内容依赖 ``time.monotonic()``（计时器、进度动画）但可接受
            有限帧率时**按需声明**：空闲态仍允许帧复用（不再因「有浮窗」永久
            关掉空闲帧复用），由会话在**间隔到期**时强制重建一帧。非正数 /
            非法值归一化为 None（保持每帧刷新语义）。与 ``idle_static`` 的
            区别：``idle_static`` 声明「永不随时间变化」，本字段声明「变化但按
            固定间隔采样」；两者都不阻止 model 变更经
            ``request_bottom_redraw`` 触发的即时重建。
            ★ ``/loop`` 循环浮窗**不使用**本字段（2026-10-22 用户确认：保持
            每帧刷新，计时/流动进度条逐帧推进）。
        geometry: **运行时**窗口矩形（由 ``ink._floating.apply_floating_layout``
            每帧更新，内容组件经 props["geometry"] 读取）：
            ``{"x", "y", "width", "height", "content_height",
            "screen_x", "screen_y", "visible"}``——文档坐标 + 终端可见坐标
            （screen_* 可直接与鼠标事件 ``mouse_x``/``mouse_y`` 比较，
            见 ``window_hit_test``）。初始为空 dict。
    """

    __slots__ = (
        "id", "render", "priority", "width", "width_ratio", "max_width",
        "x", "bottom", "top",
        "offset_x", "offset_y", "border", "border_color", "border_style",
        "background", "padding_x", "padding_y", "title",
        "max_height", "scroll_top", "allow_cover_bottom", "over_fullscreen",
        "close_on_escape", "on_close", "idle_static", "refresh_interval",
        "geometry",
    )

    def __init__(
        self,
        id: str,
        render: Callable[[dict], Any] | None = None,
        *,
        priority: int = 0,
        width: int | None = DEFAULT_FLOAT_WIDTH,
        width_ratio: float | None = None,
        max_width: int | None = None,
        x: Any = "right",
        bottom: Any = DEFAULT_FLOAT_BOTTOM,
        top: Any = None,
        offset_x: int = 0,
        offset_y: int = 0,
        border: bool = True,
        border_color: Any = None,
        border_style: Any = None,
        background: Any = DEFAULT_FLOAT_BACKGROUND,
        padding_x: int = 1,
        padding_y: int = 0,
        title: str = "",
        max_height: int | None = None,
        scroll_top: int = 0,
        allow_cover_bottom: bool = False,
        over_fullscreen: bool = False,
        close_on_escape: bool = False,
        on_close: Callable[[], None] | None = None,
        idle_static: bool = False,
        refresh_interval: float | None = None,
        geometry: dict | None = None,
    ) -> None:
        self.id = str(id)
        self.render = render
        self.priority = priority
        self.width = width
        self.width_ratio = _safe_ratio(width_ratio)
        self.max_width = None if max_width is None else max(1, _safe_int(max_width, 1))
        self.x = x
        self.bottom = bottom
        self.top = top
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.border = bool(border)
        self.border_color = border_color
        self.border_style = border_style
        self.background = background
        self.padding_x = max(0, int(padding_x))
        self.padding_y = max(0, int(padding_y))
        self.title = str(title or "")
        self.max_height = max_height
        self.scroll_top = scroll_top
        self.allow_cover_bottom = bool(allow_cover_bottom)
        self.over_fullscreen = bool(over_fullscreen)
        self.close_on_escape = bool(close_on_escape)
        self.on_close = on_close
        self.idle_static = bool(idle_static)
        self.refresh_interval = _safe_interval(refresh_interval)
        # 运行时矩形（布局阶段原地更新；copy_with 共享同一 dict，内容组件
        # 跨帧读到的永远是最新值）
        self.geometry = geometry if isinstance(geometry, dict) else {}

    # ── 复制（更新窗口字段用：不可变替换，避免调用方原地改共享对象） ──
    def copy_with(self, **changes) -> FloatingWindowSpec:
        """返回修改了指定字段的新规格（未知字段忽略，记 warning 便于发现拼写错误）。

        ★ 2026-10-21：未知字段此前**静默**忽略——``update_floating_window``
        传错字段名（如 ``x_="left"``）会返回 True 却什么都没改，调用方无从
        察觉。行为保持（不污染对象），但补 warning 日志。
        """
        values = {name: getattr(self, name) for name in self.__slots__}
        for key, value in changes.items():
            if key in values:
                values[key] = value
            else:
                _logger.warning(
                    "浮窗规格字段不存在，已忽略：id=%r key=%r", self.id, key,
                )
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


def _safe_interval(value) -> float | None:
    """刷新间隔归一化：None / 非正数 / 非有限值 / 非法类型 → None（每帧刷新）。"""
    if value is None:
        return None
    try:
        interval = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(interval) or interval <= 0:
        return None
    return interval


def _safe_ratio(value) -> float | None:
    """宽度比例归一化：None / 非有限值 / <=0 / >1 / 非法类型 → None。"""
    if value is None:
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(ratio) or ratio <= 0 or ratio > 1:
        return None
    return ratio


def FloatingWindow(props: dict) -> object:
    """浮窗渲染包装（函数组件）：边框 + 背景 + 内容组件。

    Props:
        window: ``FloatingWindowSpec``。
        model: AppModel（透传给内容组件）。
        width: 终端宽度（窗口宽度钳制与内容宽度计算）。

    Returns:
        绝对定位 BOX（带 ``dataFloatWindow`` 标记，布局后由
        ``ink._floating.apply_floating_layout`` 定位）。

    ★ Esc 关闭（2026-10-21 交互完善）：``close_on_escape=True`` 时本组件注册
    一个 ``use_input`` 钩子——只消费 Esc 并调用 ``on_close``（由 bridge 注入），
    其余按键原样放行给输入区/其它 hook。``isActive=False`` 时钩子不参与路由
    （零影响），保证未开启该能力的浮窗不改变任何按键语义。
    """
    win = props.get("window")

    def _on_key(event) -> bool:
        """Esc → 关闭窗口（仅 close_on_escape 生效时被路由）。"""
        if win is None or not getattr(win, "close_on_escape", False):
            return False
        if getattr(event, "kind", "") != "escape":
            return False
        callback = getattr(win, "on_close", None)
        if not callable(callback):
            return False
        try:
            callback()
        except Exception:  # pragma: no cover - 关闭回调异常不阻断输入路由
            _logger.debug("浮窗 on_close 回调异常 id=%r", getattr(win, "id", None),
                          exc_info=True)
            return False
        return True

    # 钩子必须无条件调用（hooks 顺序稳定）：未开启时 isActive=False 不参与路由。
    # ★ 直接调用组件（非渲染期，如测试/工具做宽度测量）没有 hook 上下文——
    #   ``use_input`` 会抛 ``HookStateError``，此处按「无按键钩子」跳过（元素
    #   结构不变，不影响返回值）。
    try:
        use_input(_on_key, {"isActive": bool(getattr(win, "close_on_escape", False))})
    except HookStateError:
        _logger.debug(
            "FloatingWindow 非渲染期调用，跳过按键钩子：id=%r",
            getattr(win, "id", None),
        )

    if win is None:
        # 兜底：无规格时不绘制任何内容。★ 2026-10-21：此前的兜底是一个**普通**
        #   BOX（无 position/dataFloatWindow）——若真被走到会占一个正常流位置。
        #   FRAGMENT 是透明分组容器，零尺寸零占位（``layout_children`` 展开）。
        return h(FRAGMENT, {"key": "float-empty"}, [])
    term_width = max(1, _safe_int(props.get("width"), 80))
    pad_x = max(0, _safe_int(getattr(win, "padding_x", 0), 0))
    pad_y = max(0, _safe_int(getattr(win, "padding_y", 0), 0))
    border = 1 if getattr(win, "border", True) else 0
    geometry = getattr(win, "geometry", None)
    if not isinstance(geometry, dict):
        geometry = {}
    max_height = getattr(win, "max_height", None)
    try:
        max_height = None if max_height is None else max(1, int(max_height))
    except (TypeError, ValueError, OverflowError):
        max_height = None

    # ── 窗口宽度：比例宽 / 显式宽钳制到终端宽度；None = 内容自适应 ──
    raw_width = getattr(win, "width", None)
    width_ratio = getattr(win, "width_ratio", None)
    max_width = getattr(win, "max_width", None)
    box_w: int | None = None
    if width_ratio is not None:
        # 比例宽每帧按当前终端宽度重算——resize 后自动缩放（/usage 浮窗）。
        box_w = max(1, min(term_width, int(round(term_width * float(width_ratio)))))
        if max_width is not None:
            box_w = max(1, min(box_w, _safe_int(max_width, box_w)))
    elif raw_width is not None:
        want = _safe_int(raw_width, DEFAULT_FLOAT_WIDTH)
        # ★ 至少 1 列、不超过终端宽度（2026-10-21 行宽不变量修复）：修复前
        #   下限写成 ``max(3, ...)``——终端宽度 < 3 列时窗口宽度反超终端宽，
        #   合成后的帧行宽 > 文档宽，破坏行级 diff / 光标定位的不变量。
        box_w = max(1, min(want, term_width))
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
            # ★ 运行时矩形（2026-10-21 交互完善）：布局阶段原地更新——内容
            #   组件可据此做鼠标命中/相对定位/按可见高度自行分页。
            "geometry": geometry,
            # 可见高度上限（None = 未限制；实际可见高度见 geometry["height"]）
            "height": max_height,
        }))

    box_props: dict = {
        "position": "absolute",
        # 默认锚点：布局第二遍放在左上角，随后由 apply_floating_layout 平移
        "left": 0,
        "top": 0,
        "dataFloatWindow": win.id,
        "floatX": getattr(win, "x", "right"),
        "floatBottom": getattr(win, "bottom", DEFAULT_FLOAT_BOTTOM),
        "floatTop": getattr(win, "top", None),
        "floatOffsetX": _safe_int(getattr(win, "offset_x", 0), 0),
        "floatOffsetY": _safe_int(getattr(win, "offset_y", 0), 0),
        "floatMaxHeight": max_height,
        "floatScrollTop": _safe_int(getattr(win, "scroll_top", 0), 0),
        "floatAllowCoverBottom": bool(getattr(win, "allow_cover_bottom", False)),
        "floatOverFullscreen": bool(getattr(win, "over_fullscreen", False)),
        "floatGeometry": geometry,
        "flexDirection": "column",
        "border": border,
        "paddingX": pad_x,
        "paddingY": pad_y,
        "key": f"float-{win.id}",
    }
    # ★ 可见高度由合成层按 ``floatMaxHeight`` / 底部框 / 可视区裁剪——布局盒
    #   保持**内容自然高度**（不能设 maxHeight：盒高被钳后 ``content_h`` 变小，
    #   ``scroll_top`` 就滚不到溢出内容）。
    if box_w is not None:
        box_props["width"] = box_w
    background = getattr(win, "background", DEFAULT_FLOAT_BACKGROUND)
    if background is not None:
        box_props["backgroundColor"] = background
    border_color = getattr(win, "border_color", None)
    if border_color is not None:
        box_props["borderColor"] = border_color
    border_style = getattr(win, "border_style", None)
    if border_style is not None:
        box_props["borderStyle"] = border_style
    return h(BOX, box_props, children)


def floating_layer_elements(model, width: int) -> list:
    """构建浮窗层元素（App 根的直接子元素，按优先级升序）。

    App 把返回值追加在正常界面/全屏视图之后：绝对定位层不占高度；后绘制
    的窗口覆盖先绘制的（高优先级在上）。无浮窗时返回空列表（零额外节点）。

    ★ id 防御（2026-10-21）：``None`` / 空串 id 与**重复 id** 直接跳过——
    空串 id 此前会生成 ``dataFloatWindow=""``：既不被 ``_collect_float_fibers``
    收集（也进不了合成层），又仍是 absolute 节点 → 固定画在文档左上角且被
    committed 前缀拼接截断。重复 id 会在同一父容器下产生重复 key（调和器
    warning + 两个窗口同时绘制），bridge 的 open 有去重但直接改 model 的
    路径没有，这里统一兜底（保留先出现的，高优先级排序后仍然生效）。
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
    seen: set = set()
    for win in ordered:
        wid = getattr(win, "id", None)
        if wid is None:
            continue
        wid_text = str(wid)
        if not wid_text.strip():
            continue
        if wid_text in seen:
            _logger.warning("浮窗 id 重复，已忽略后者：id=%r", wid_text)
            continue
        seen.add(wid_text)
        elements.append(h(FloatingWindow, {
            "key": f"float-{wid_text}",
            "window": win,
            "model": model,
            "width": width,
        }))
    return elements


def _safe_float(value, default: float = 0.0) -> float:
    """float 归一化（异常值 / 非有限值回退默认）。"""
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _runs_width(runs) -> int:
    """StyledRun 序列的显示宽度（CJK/emoji 安全）。"""
    return sum(wcswidth_simple(run.text) for run in runs)


def _right_aligned(left_runs: list, right_runs: list, width: int) -> list:
    """左段 + 空格填充 + 右段（单行两端对齐；空间不足时丢弃右段）。

    用于循环浮窗的标题行 / 计时行——右侧信息（轮次、第几次运行）靠右，
    视觉上像一个小面板。返回的 runs 未截断，由调用方统一按可用宽度裁剪。
    """
    left_w = _runs_width(left_runs)
    right_w = _runs_width(right_runs)
    gap = width - left_w - right_w
    if gap < 1:
        return list(left_runs)
    return list(left_runs) + [StyledRun(" " * gap)] + list(right_runs)


def _loop_elapsed_text(started_at, now: float) -> str:
    """本次已运行时长文本（``--`` = 尚未开始）。"""
    started = _safe_float(started_at, 0.0)
    if started <= 0.0:
        return "--"
    return format_duration(max(0.0, now - started))


def LoopFloatingWindow(props: dict) -> object:
    """``/loop`` 循环浮窗内容（标题 + 进度条 + 动态计时 + 提词 + 生成提词）。

    数据源：``model.loop_status``（``LoopStatusState``，由 /loop 每次运行前经
    InkBridge 更新）。五行内容：

      1. ``⟳ /loop … 第 R/N 次``（无限时 ``第 R 次``；两端对齐）
      2. ``████████░░░░░░░  40%``（有限次数显示进度；无限次显示流动条 + ``∞``）
      3. ``⏱ 本次 1:23 … ✎ 生成提词中…``（本次已运行时间**每帧动态刷新**）
      4. ``❯ 当前提词``（超宽省略号截断，暗色斜体）
      5. ``↳ 下一轮生成提词`` / ``✎ 生成提词中…`` / ``↳ 等待生成``

    动态刷新机制：本组件每帧从 ``model`` 重建（``loop_window_spec`` 不声明
    ``idle_static`` / ``refresh_interval`` → 有该浮窗时每帧重建），第 2/3 行
    使用 ``time.monotonic()`` 实时计算，因此浮窗打开期间计时与无限进度条
    逐帧推进。

    ``active`` 为 False 时返回空 TEXT（窗口无内容，正常情况下窗口已关闭）。
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
    generating = bool(getattr(st, "generating", False))
    now = time.monotonic()

    # ── 1. 标题行：⟳ /loop（左）… 第 R/N 次（右） ──
    if round_no and total:
        shown_round = min(round_no, total)
        round_label = f"第 {shown_round}/{total} 次"
    elif round_no:
        round_label = f"第 {round_no} 次"
    else:
        round_label = "准备中"
    header_runs = _right_aligned(
        [StyledRun("⟳ /loop", _S_LOOP_ACCENT)],
        [StyledRun(round_label, _S_LOOP_TEXT if round_no else _S_LOOP_MUTED)],
        width,
    )

    # ── 2. 进度条：有限次数显示比例；无限次数显示流动块 + ∞ ──
    # 进度条占满整行（右侧留 5 列给「 100%」）——窄终端下至少 4 格。
    bar_width = max(4, min(40, width - 5))
    bar_runs: list = []
    if total > 0:
        percent = (min(round_no, total) / total) if round_no else 0.0
        filled = max(0, min(int(round(percent * bar_width)), bar_width))
        if filled:
            bar_runs.append(StyledRun("█" * filled, _S_LOOP_BAR_FILL))
        if bar_width > filled:
            bar_runs.append(StyledRun("░" * (bar_width - filled), _S_LOOP_BAR_EMPTY))
        bar_runs.append(
            StyledRun(f" {int(round(percent * 100)):3d}%", _S_LOOP_ACCENT)
        )
    else:
        # 无限：一个约 1/4 宽的高亮块每 0.25s 前进一格，循环流动。
        block = max(2, bar_width // 4)
        offset = int(now * 4) % bar_width
        on_cells = {(offset + index) % bar_width for index in range(block)}
        current_on: bool | None = None
        buffer: list[str] = []

        def _flush() -> None:
            if not buffer:
                return
            bar_runs.append(StyledRun(
                "".join(buffer),
                _S_LOOP_BAR_FILL if current_on else _S_LOOP_BAR_EMPTY,
            ))
            buffer.clear()

        for index in range(bar_width):
            is_on = index in on_cells
            if current_on is not None and is_on != current_on:
                _flush()
            current_on = is_on
            buffer.append("█" if is_on else "░")
        _flush()
        bar_runs.append(StyledRun("  ∞", _S_LOOP_ACCENT))

    # ── 3. 计时行：⏱ 本次 <已运行时长>（每帧刷新）… 生成状态 / 第 R 次 ──
    elapsed_text = _loop_elapsed_text(getattr(st, "round_started_at", 0.0), now)
    if generating:
        run_label = "✎ 生成提词中…"
    elif round_no:
        run_label = f"第 {round_no} 次"
    else:
        run_label = "等待开始"
    time_runs = _right_aligned(
        [
            StyledRun("⏱ ", _S_LOOP_ACCENT),
            StyledRun("本次 ", _S_LOOP_MUTED),
            StyledRun(elapsed_text, _S_LOOP_TIME),
        ],
        [StyledRun(run_label, _S_LOOP_MUTED)],
        width,
    )

    # ── 4. 当前提词（单行，超宽省略号） ──
    prompt_runs = [
        StyledRun("❯ ", _S_LOOP_ACCENT),
        StyledRun(str(getattr(st, "prompt", "") or ""), _S_LOOP_PROMPT),
    ]

    # ── 5. 下一轮提词 / 生成状态（单行，超宽省略号） ──
    next_prompt = str(getattr(st, "next_prompt", "") or "").strip()
    if generating:
        next_runs = [
            StyledRun("✎ ", _S_LOOP_ACCENT),
            StyledRun("生成提词中…", _S_LOOP_MUTED),
        ]
    elif next_prompt:
        next_runs = [
            StyledRun("↳ ", _S_LOOP_ACCENT),
            StyledRun(next_prompt, _S_LOOP_PROMPT),
        ]
    else:
        next_runs = [
            StyledRun("↳ ", _S_LOOP_MUTED),
            StyledRun("等待生成", _S_LOOP_MUTED),
        ]

    def _line(key: str, runs: list, *, ellipsis: bool = False):
        """单行 TEXT（显式截断到可用宽度，防换行破坏浮窗高度）。"""
        trimmed = (
            truncate_runs_ellipsis(runs, width) if ellipsis
            else truncate_runs(runs, width)
        )
        return h(TEXT, {"key": key, "height": 1, "styled": trimmed})

    return h(FRAGMENT, {"key": "loop-body"}, [
        _line("loop-header", header_runs),
        _line("loop-progress", bar_runs),
        _line("loop-time", time_runs),
        _line("loop-prompt", prompt_runs, ellipsis=True),
        _line("loop-next", next_runs, ellipsis=True),
    ])


def loop_window_spec() -> FloatingWindowSpec:
    """``/loop`` 循环浮窗规格：底部框（状态栏 + 输入区）上方、向上展开、圆角边框。

    ``bottom="bottom_area"`` 锚定整个底部框**上方一行**（``_resolve_row`` 对
    该锚点返回 ``bottom_area_top - 1``）——**窗口所有行都不覆盖底部框**
    （2026-10-21 用户需求：循环浮窗不能遮盖底部框）。水平右对齐终端右侧。
    内容读 ``model.loop_status``（每帧可变，含动态计时）→ **不声明**
    ``idle_static`` / ``refresh_interval``：有该浮窗时按每帧重建（计时与流动
    进度条逐帧推进，内容不会冻结）——2026-10-22 用户确认保持每帧刷新语义。
    """
    return FloatingWindowSpec(
        id="loop",
        render=LoopFloatingWindow,
        priority=100,
        width=LOOP_FLOAT_WIDTH,
        x="right",
        bottom="bottom_area",
        border=True,
        border_color=_S_LOOP_BORDER,
        border_style="round",
        background=DEFAULT_FLOAT_BACKGROUND,
    )


#: ``/usage`` 浮窗配色（与全屏用量视图同族：青标题 / 蓝标签 / 绿黄红状态）。
_S_USAGE_GROUP = Style(fg=110, bold=True)
_S_USAGE_HINT = Style(fg=242)
_S_USAGE_SEP = Style(fg=238)
_S_USAGE_LABEL = Style(fg=75)
_S_USAGE_VALUE = Style(fg=252)
_S_USAGE_OK = Style(fg=40)
_S_USAGE_WARN = Style(fg=214)
_S_USAGE_ERR = Style(fg=196)
_S_USAGE_BAR_FILL = Style(fg=40)
_S_USAGE_BAR_MID = Style(fg=214)
_S_USAGE_BAR_LOW = Style(fg=196)
_S_USAGE_BAR_EMPTY = Style(fg=238)


def _usage_value_style(kind: str) -> Style:
    """统计值配色（kind: info/ok/warn/error）。"""
    if kind == "error":
        return _S_USAGE_ERR
    if kind == "warn":
        return _S_USAGE_WARN
    if kind == "ok":
        return _S_USAGE_OK
    return _S_USAGE_VALUE


def _usage_item_fields(item):
    """统计条目 → ``(label, value, kind, ratio)``；非法条目返回 None。"""
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return (
            item[0], item[1],
            item[2] if len(item) > 2 else "",
            item[3] if len(item) > 3 else None,
        )
    if isinstance(item, dict):
        return (
            item.get("label", ""), item.get("value", ""),
            item.get("kind", ""), item.get("bar"),
        )
    return None


def _usage_label_col(sections: list) -> int:
    """标签列宽（显示列）：最宽标签 + 1 列间隔，钳制到 [8, 18]。"""
    widest = 0
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("rows") or []:
            fields = _usage_item_fields(item)
            if fields is None:
                continue
            widest = max(widest, wcswidth_simple(str(fields[0])))
    return max(8, min(18, widest + 1))


def _usage_pad_label(label, width: int) -> str:
    """按显示宽度截断并右补空格（CJK 标签对齐；单行）。"""
    text = truncate_width(str(label), max(0, width))
    return text + " " * max(0, width - wcswidth_simple(text))


def _usage_bar_runs(ratio, bar_width: int) -> list:
    """进度条 runs（>90% 红、>70% 黄、其余绿；窄条省略百分比文本）。"""
    try:
        r = max(0.0, min(1.0, float(ratio)))
    except (TypeError, ValueError):
        r = 0.0
    bar_width = max(1, int(bar_width))
    filled = max(0, min(bar_width, int(round(r * bar_width))))
    fill_style = _S_USAGE_BAR_FILL
    if r >= 0.9:
        fill_style = _S_USAGE_BAR_LOW
    elif r >= 0.7:
        fill_style = _S_USAGE_BAR_MID
    runs = [
        StyledRun("█" * filled, fill_style),
        StyledRun("░" * (bar_width - filled), _S_USAGE_BAR_EMPTY),
    ]
    if bar_width >= 8:
        runs.append(StyledRun(f" {r * 100:.0f}%", _S_USAGE_VALUE))
    return runs


def _usage_item_by_label(rows, label: str):
    """按标签取统计条目 ``(label, value, kind, ratio)``；未找到返回 None。"""
    for item in rows or []:
        fields = _usage_item_fields(item)
        if fields is not None and str(fields[0]) == label:
            return fields
    return None


def _compact_usage_sections(sections: list) -> list:
    """把完整统计区块压缩为浮窗可容纳的精简区块（合并次要行，不丢关键指标）。

    浮窗从底部向上展开，内容超过可视高度时顶部会被裁掉——因此浮窗只展示
    「输入/输出 · 合计/调用/速度 · 缓存命中率」「容量/使用率」
    「输入/输出费用 · 合计（含节省）」「时长」等关键行；无法识别的区块
    原样保留（向前兼容）。
    """
    compact: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", ""))
        rows = list(sec.get("rows") or [])
        fields = []
        if title == "Token 用量":
            miss = _usage_item_by_label(rows, "输入（未命中）")
            # 展示输入优先用「Agent 新增」（各 Agent 上下文增量累计）；
            # 旧统计 / 外部调用方未提供该行时回退未命中行（保持向后兼容）。
            agent_input = _usage_item_by_label(rows, "输入（Agent 新增）") or miss
            output = _usage_item_by_label(rows, "输出")
            total = _usage_item_by_label(rows, "合计")
            calls = _usage_item_by_label(rows, "API 调用")
            speed = _usage_item_by_label(rows, "输出速度")
            hit_rate = _usage_item_by_label(rows, "缓存命中率")
            if agent_input and output and total:
                fields.append((
                    "输入/输出", f"{agent_input[1]} / {output[1]}", "info", None,
                ))
                summary = str(total[1])
                if calls:
                    summary += f" · {calls[1]} 次"
                if speed:
                    summary += f" · {speed[1]}"
                fields.append(("合计", summary, "info", None))
                # 缓存命中率（dsh 口径）单独一行：保留精度，不塞进可能被
                # 窄浮窗截断的「合计」长文本。
                if hit_rate:
                    fields.append(hit_rate)
        elif title == "上下文窗口":
            cap = _usage_item_by_label(rows, "容量")
            usage = _usage_item_by_label(rows, "使用率")
            if cap and usage:
                # 合并为一行：为 Token 区块新增的「缓存命中率」腾出一行，
                # 保持浮窗总高度不变——浮窗自底部向上展开，多一行会把最顶部
                # 的分区标题裁出可视区（实测 70 列 / 50 行终端均会丢失
                # 「▸ Token 用量」标题）。紧凑文本比进度条更省列：去掉
                # 「 tokens」后缀并保留百分比，窄浮窗下两个数字都能完整显示。
                cap_text = str(cap[1])
                if cap_text.endswith(" tokens"):
                    cap_text = cap_text[: -len(" tokens")]
                if usage[3] is not None:
                    try:
                        ratio = max(0.0, min(1.0, float(usage[3])))
                        cap_text += f" · {ratio * 100:.0f}%"
                    except (TypeError, ValueError, OverflowError):
                        pass
                elif usage[1]:
                    cap_text += f" · {usage[1]}"
                fields.append(("容量/使用率", cap_text, usage[2] or "info", None))
            elif cap:
                fields.append(cap)
            elif usage:
                fields.append(usage)
        elif title == "费用估算":
            input_cost = _usage_item_by_label(rows, "输入费用")
            output_cost = _usage_item_by_label(rows, "输出费用")
            total = _usage_item_by_label(rows, "合计")
            saved = _usage_item_by_label(rows, "缓存节省")
            if input_cost and output_cost and total:
                fields.append((
                    "输入/输出", f"{input_cost[1]} / {output_cost[1]}",
                    "info", None,
                ))
                summary = str(total[1])
                if saved:
                    summary += f"（省 {saved[1]}）"
                fields.append(("合计", summary, "warn", None))
        elif title == "会话":
            duration = _usage_item_by_label(rows, "时长")
            if duration:
                fields.append(("时长", duration[1], "info", None))
        if fields:
            compact.append({"title": title, "rows": fields})
        else:
            compact.append(sec)
    return compact


def _usage_content_rows(sections: list, width: int) -> list:
    """统计区块 → 浮窗内容行（每项单行；宽度不足时按终端宽度截断）。"""
    width = max(1, int(width))
    label_col = _usage_label_col(sections)
    rows: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", ""))
        prefix = f"\u25b8 {title} "
        pad = max(0, width - wcswidth_simple(prefix) - 1)
        rows.append([
            StyledRun(prefix, _S_USAGE_GROUP),
            StyledRun("\u2500" * pad, _S_USAGE_SEP),
        ])
        for item in sec.get("rows") or []:
            fields = _usage_item_fields(item)
            if fields is None:
                continue
            label, value, kind, ratio = fields
            value_text = str(value)
            runs = [
                StyledRun("  " + _usage_pad_label(label, label_col), _S_USAGE_LABEL),
                StyledRun(value_text, _usage_value_style(str(kind))),
            ]
            if ratio is not None:
                try:
                    r = max(0.0, min(1.0, float(ratio)))
                except (TypeError, ValueError):
                    r = 0.0
                used = 2 + label_col + wcswidth_simple(value_text)
                # 进度条实际占用 = 1 列间隔 + bar_width + （宽度 >= 8 时
                # " NN%" 4 列）。预留 5 列，修复前只预留 3 列——窄浮窗下
                # 整行会超宽 2 列，百分比被 truncate_runs 截掉。
                avail = width - used - 5
                if avail >= 4:
                    bar_width = max(4, min(12, avail))
                    runs.append(StyledRun(" ", None))
                    runs.extend(_usage_bar_runs(r, bar_width))
                else:
                    pct_text = f" {r * 100:.0f}%"
                    if width - used >= wcswidth_simple(pct_text):
                        runs.append(StyledRun(pct_text, _S_USAGE_VALUE))
            rows.append(runs)
    if not rows:
        rows.append([StyledRun("(暂无统计)", _S_USAGE_HINT)])
    return rows


def UsageFloatingWindow(props: dict, provider=None) -> object:
    """``/usage`` 用量浮窗内容（token / 上下文 / 费用，按帧重读）。

    ``provider`` 为无参可调用对象（由命令层注入 ``build_usage_sections``）——
    每次组件重建都重新构建统计区块，因此窗口打开期间数值随生成进度实时变化。

    Args:
        props: 浮窗渲染 props（``width`` = 内容可用宽度，``window`` 等）。
        provider: ``Callable[[], list]``；异常时显示「暂无统计」而不是崩溃。
    """
    width = max(1, _safe_int(props.get("width"), 1))
    sections: list = []
    if callable(provider):
        try:
            sections = list(provider() or [])
        except Exception:
            _logger.debug("/usage 浮窗统计构建失败", exc_info=True)
            sections = []
    rows = _usage_content_rows(_compact_usage_sections(sections), width)
    rows.append([StyledRun("↻ 实时刷新 · /usage 关闭", _S_USAGE_HINT)])

    children: list = []
    for index, runs in enumerate(rows):
        trimmed = truncate_runs(runs, width)
        children.append(h(TEXT, {
            "key": f"usage-{index}",
            "height": 1,
            "styled": trimmed or [StyledRun(" ", None)],
        }))
    return h(FRAGMENT, {"key": "usage-body"}, children)


def usage_window_spec(provider=None) -> FloatingWindowSpec:
    """``/usage`` 用量浮窗规格：右侧、底框上方、宽度自适应（60%，上限 60 列）。

    - ``width=None`` + ``width_ratio=0.6`` + ``max_width=60``：每帧按终端宽度
      重算窗口宽度，resize 后自动缩放；
    - **不声明** ``refresh_interval``：浮窗打开期间按帧重建（用户确认「每帧
      刷新」），provider 每帧重读 token / 上下文 / 费用；
    - 无 ``close_on_escape``：关闭语义固定为「再次输入 /usage」（Esc 继续承担
      中断 / 清空输入）。
    """
    def _render(props: dict) -> object:
        return UsageFloatingWindow(props, provider)

    return FloatingWindowSpec(
        id=USAGE_WINDOW_ID,
        render=_render,
        priority=130,
        width=None,
        width_ratio=USAGE_FLOAT_WIDTH_RATIO,
        max_width=USAGE_FLOAT_MAX_WIDTH,
        x="right",
        bottom=DEFAULT_FLOAT_BOTTOM,
        border=True,
        border_color=_S_USAGE_BORDER,
        border_style="round",
        background=DEFAULT_FLOAT_BACKGROUND,
    )


def window_hit_test(window_or_geometry, col, row) -> tuple[int, int] | None:
    """鼠标命中测试：``(终端列, 终端行)`` 落在窗口内时返回窗口内局部坐标。

    2026-10-21 交互完善：浮窗默认是纯展示层，鼠标/键盘交互由内容组件自行
    决定——本函数提供**唯一**的坐标换算真源，避免每个调用方重复实现：

      - 输入坐标为**终端可见坐标**（与 ``KeyEvent.mouse_x`` /
        ``KeyEvent.mouse_y`` 同一空间，0-based）；
      - 返回 ``(local_x, local_y)``——相对窗口**左上角**（含边框）的列/行；
      - 窗口隐藏 / 不可见 / 坐标越界返回 None。

    用法（内容组件内）::

        use_mouse_input(lambda ev: window_hit_test(props["window"], ev.mouse_x, ev.mouse_y) is not None)

    Args:
        window_or_geometry: ``FloatingWindowSpec``（读 ``.geometry``）或 geometry
            dict 本身。
        col: 终端列（0-based）。
        row: 终端行（0-based）。
    """
    geometry = (
        window_or_geometry if isinstance(window_or_geometry, dict)
        else getattr(window_or_geometry, "geometry", None)
    )
    if not isinstance(geometry, dict) or not geometry.get("visible", False):
        return None
    try:
        sx = int(geometry.get("screen_x", 0))
        sy = int(geometry.get("screen_y", 0))
        w = int(geometry.get("width", 0))
        h = int(geometry.get("height", 0))
        cx = int(col)
        cy = int(row)
    except (TypeError, ValueError, OverflowError):
        return None
    if w <= 0 or h <= 0:
        return None
    if not (sx <= cx < sx + w and sy <= cy < sy + h):
        return None
    return cx - sx, cy - sy


__all__ = [
    "DEFAULT_FLOAT_BACKGROUND",
    "DEFAULT_FLOAT_BOTTOM",
    "DEFAULT_FLOAT_WIDTH",
    "FloatingWindow",
    "FloatingWindowSpec",
    "LOOP_FLOAT_WIDTH",
    "LoopFloatingWindow",
    "USAGE_FLOAT_MAX_WIDTH",
    "USAGE_FLOAT_WIDTH_RATIO",
    "USAGE_WINDOW_ID",
    "UsageFloatingWindow",
    "floating_layer_elements",
    "loop_window_spec",
    "usage_window_spec",
    "window_hit_test",
]
