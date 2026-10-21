"""通用浮动窗口 — 规格 / 渲染包装 / 层级排序 / ``/loop`` 循环浮窗内容。

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

#: 循环浮窗固定宽度（列）——容纳「⟳ /loop … 第 N/M 轮」标题行、
#: 进度条、「⏱ 本轮 mm:ss … 第 A/B 次」计时行与用户提词。
LOOP_FLOAT_WIDTH = 34

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
        geometry: **运行时**窗口矩形（由 ``ink._floating.apply_floating_layout``
            每帧更新，内容组件经 props["geometry"] 读取）：
            ``{"x", "y", "width", "height", "content_height",
            "screen_x", "screen_y", "visible"}``——文档坐标 + 终端可见坐标
            （screen_* 可直接与鼠标事件 ``mouse_x``/``mouse_y`` 比较，
            见 ``window_hit_test``）。初始为空 dict。
    """

    __slots__ = (
        "id", "render", "priority", "width", "x", "bottom", "top",
        "offset_x", "offset_y", "border", "border_color", "border_style",
        "background", "padding_x", "padding_y", "title",
        "max_height", "scroll_top", "allow_cover_bottom", "over_fullscreen",
        "close_on_escape", "on_close", "idle_static", "geometry",
    )

    def __init__(
        self,
        id: str,
        render: Callable[[dict], Any] | None = None,
        *,
        priority: int = 0,
        width: int | None = DEFAULT_FLOAT_WIDTH,
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
        geometry: dict | None = None,
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

    # ── 窗口宽度：显式宽钳制到终端宽度；None = 内容自适应 ──
    raw_width = getattr(win, "width", None)
    box_w: int | None = None
    if raw_width is not None:
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
    """本轮已运行时长文本（``--`` = 尚未开始）。"""
    started = _safe_float(started_at, 0.0)
    if started <= 0.0:
        return "--"
    return format_duration(max(0.0, now - started))


def LoopFloatingWindow(props: dict) -> object:
    """``/loop`` 循环浮窗内容（美化版：标题 + 进度条 + 动态计时 + 提词）。

    数据源：``model.loop_status``（``LoopStatusState``，由 /loop 每轮/每次
    运行前经 InkBridge 更新）。四行内容：

      1. ``⟳ /loop … 第 R/N 轮``（两端对齐标题行）
      2. ``████████░░░░░░░  40%``（轮次进度：当前轮计入进行中）
      3. ``⏱ 本轮 1:23 … 第 1/2 次``（本轮已运行时间**每帧动态刷新**）
      4. ``❯ 用户提词``（超宽省略号截断，暗色斜体）

    动态刷新机制：本组件每帧从 ``model`` 重建（``loop_window_spec`` 不声明
    ``idle_static`` → 空闲帧复用被关闭），第 3 行用
    ``time.monotonic() - round_started_at`` 实时计算，因此浮窗打开期间计时
    持续走秒（与状态栏 elapsed 同一渲染期读时钟的模式）。

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
    run_no = max(0, _safe_int(getattr(st, "run", 0), 0))
    per_round = max(1, _safe_int(getattr(st, "runs_per_round", 2), 2))
    now = time.monotonic()

    # ── 1. 标题行：⟳ /loop（左）… 第 R/N 轮（右） ──
    if round_no and total:
        shown_round = min(round_no, total)
        round_label = f"第 {shown_round}/{total} 轮"
    elif round_no:
        round_label = f"第 {round_no} 轮"
    else:
        round_label = "准备中"
    header_runs = _right_aligned(
        [StyledRun("⟳ /loop", _S_LOOP_ACCENT)],
        [StyledRun(round_label, _S_LOOP_TEXT if round_no else _S_LOOP_MUTED)],
        width,
    )

    # ── 2. 进度条：轮次进度（当前轮计入进行中，最后一轮达到 100%） ──
    percent = (min(round_no, total) / total) if total and round_no else 0.0
    # 进度条占满整行（右侧留 5 列给「 100%」）——窄终端下至少 4 格。
    bar_width = max(4, min(40, width - 5))
    filled = max(0, min(int(round(percent * bar_width)), bar_width))
    bar_runs: list = []
    if filled:
        bar_runs.append(StyledRun("█" * filled, _S_LOOP_BAR_FILL))
    if bar_width > filled:
        bar_runs.append(StyledRun("░" * (bar_width - filled), _S_LOOP_BAR_EMPTY))
    bar_runs.append(StyledRun(f" {int(round(percent * 100)):3d}%", _S_LOOP_ACCENT))

    # ── 3. 计时行：⏱ 本轮 <已运行时长>（每帧刷新）… 第 A/B 次 ──
    elapsed_text = _loop_elapsed_text(getattr(st, "round_started_at", 0.0), now)
    run_label = f"第 {run_no}/{per_round} 次" if round_no else "等待开始"
    time_runs = _right_aligned(
        [
            StyledRun("⏱ ", _S_LOOP_ACCENT),
            StyledRun("本轮 ", _S_LOOP_MUTED),
            StyledRun(elapsed_text, _S_LOOP_TIME),
        ],
        [StyledRun(run_label, _S_LOOP_MUTED)],
        width,
    )

    # ── 4. 用户提词（单行，超宽省略号） ──
    prompt_runs = [
        StyledRun("❯ ", _S_LOOP_ACCENT),
        StyledRun(str(getattr(st, "prompt", "") or ""), _S_LOOP_PROMPT),
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
    ])


def loop_window_spec() -> FloatingWindowSpec:
    """``/loop`` 循环浮窗规格：底部框（状态栏 + 输入区）上方、向上展开、圆角边框。

    ``bottom="bottom_area"`` 锚定整个底部框**上方一行**（``_resolve_row`` 对
    该锚点返回 ``bottom_area_top - 1``）——**窗口所有行都不覆盖底部框**
    （2026-10-21 用户需求：循环浮窗不能遮盖底部框）。水平右对齐终端右侧。
    内容读 ``model.loop_status``（每帧可变，含动态计时）→ 不声明
    ``idle_static``：有浮窗时保守每帧重建，内容不会冻结在旧值。
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
    "floating_layer_elements",
    "loop_window_spec",
    "window_hit_test",
]
