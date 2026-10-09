"""窗口输入动作的数据模型与解析（跨平台语义）。

一个「输入动作」是与平台无关的纯数据描述，各平台后端负责把它翻译成
具体的注入调用（SendInput / xdotool / osascript+cliclick）。

支持的动作（``INPUT_OPS``）：

  - ``move``    鼠标移动（绝对坐标 x/y，或相对当前光标的 dx/dy 像素偏移）
  - ``hover``   鼠标悬停（移动到目标点后保持 dwell 秒不动，触发 tooltip 等）
  - ``click``   鼠标点击（左/右/中键，可双击、可带修饰键；hold 长按、
                interval 控制多次点击间隔）
  - ``drag``    按住鼠标从一点拖到另一点（带轨迹插值）
  - ``scroll``  滚轮滚动（上下左右）
  - ``key``     键盘按键（支持 ``ctrl+shift+s`` 组合、功能键）
  - ``type``    文本输入（逐字符，支持任意 Unicode）

坐标语义：以**窗口截图左上角**为原点（与 ``op=screenshot`` 产物一致），
单位像素；``click`` / ``scroll`` 省略坐标时默认窗口中心；越界坐标报错。

坐标取值除像素整数外，还支持**语义值**（无需读图算像素）：

  - 关键字 ``center`` / ``middle``：该轴中点；``left`` / ``top``：0；
    ``right`` / ``bottom``：该轴最大像素（尺寸 - 1）；
  - 百分比 ``50%`` / ``25%``：按该轴尺寸比例取值（自动夹到有效范围）；
  - 基准偏移 ``center+20`` / ``left+20`` / ``right-10`` / ``bottom-30``：
    以中点 / 起点 / 终点为基准再加减像素（结果自动夹到有效范围）。

语义值在**注入时**按目标窗口实际尺寸解析（``resolve_point`` /
``validate_point``），因此同一个动作既可以用于不同尺寸的窗口，也不受
``op=windows`` 与输入之间窗口缩放的影响；像素整数保持原语义不变。

动作是冻结（frozen）数据类，可安全共享与单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Union

from .._screenshot.windows import SelectorError, parse_selector
from .keys import MODIFIER_ORDER, Shortcut, parse_modifiers, parse_shortcut
from .result import ActionError

# ── 语义常量（避免散落的魔法值） ─────────────────────────

BUTTONS: tuple[str, ...] = ("left", "right", "middle")
SCROLL_DIRECTIONS: tuple[str, ...] = ("up", "down", "left", "right")
METHODS: tuple[str, ...] = ("auto", "sendinput", "message")

DEFAULT_BUTTON = "left"
DEFAULT_CLICK_COUNT = 1
MAX_CLICK_COUNT = 10
DEFAULT_SCROLL_DIRECTION = "down"
DEFAULT_SCROLL_AMOUNT = 3
MAX_SCROLL_AMOUNT = 100
DEFAULT_DRAG_DURATION = 0.3
MAX_DRAG_DURATION = 10.0
DEFAULT_METHOD = "auto"
DEFAULT_DRAG_STEPS = 20
MIN_DRAG_STEPS = 2
MAX_DRAG_STEPS = 200

#: ``hover``（悬停）默认停留时长与上限（秒）：移动到目标点后保持不动，
#: 等待 tooltip / 悬浮菜单 / 延迟加载出现。
DEFAULT_HOVER_DWELL = 0.6
MAX_HOVER_DWELL = 30.0

#: ``click`` 长按时长（秒）：>0 时按下后保持指定时间再弹起（长按 / 拖选的
#: 「点住不放」场景；配合 ``count=1`` 使用）。
DEFAULT_CLICK_HOLD = 0.0
MAX_CLICK_HOLD = 30.0

#: ``click`` 多次点击（双击 / 三击）之间的间隔（秒）默认与上限。
#: 默认值需小于系统双击时间（约 0.5s），才能被目标程序识别为双击。
DEFAULT_CLICK_INTERVAL = 0.05
MAX_CLICK_INTERVAL = 10.0

#: 鼠标相对移动（``move`` 的 ``dx`` / ``dy``）单次偏移的像素绝对值上限，
#: 防御误传超大值把光标甩到屏幕外。
MAX_MOVE_OFFSET = 100000

#: 全部输入动作名（bash_opt 的 op 取值集合）
INPUT_OPS: tuple[str, ...] = ("click", "move", "hover", "drag", "scroll", "key", "type")

#: ``key`` 动作的按键阶段：
#:   ``press`` 按下后立即弹起（完整一次按键，默认）
#:   ``down``  只发送按下消息（配合后续 ``up`` 实现长按）
#:   ``up``    只发送弹起消息
KEY_PHASES: tuple[str, ...] = ("press", "down", "up")
DEFAULT_KEY_PHASE = "press"

#: ``key`` 动作的重复次数：一次调用连按 N 次，避免「多次调用之间窗口失焦 /
#: 链路中断」导致漏按。仅对 ``phase='press'``（完整按键）生效。
DEFAULT_KEY_REPEAT = 1
MAX_KEY_REPEAT = 100

#: ``phase`` 参数别名 → 规范阶段
KEY_PHASE_ALIASES: dict[str, str] = {
    "press": "press", "click": "press", "tap": "press", "full": "press",
    "down": "down", "keydown": "down", "key_down": "down", "hold": "down",
    "up": "up", "release": "up", "keyup": "up", "key_up": "up",
}

# ── 坐标语义值（免读图算像素） ───────────────────────────

#: 语义坐标关键字 → 轴比例：0.0 = 轴起点，1.0 = 轴终点（尺寸 - 1）
COORD_KEYWORDS: dict[str, float] = {
    "left": 0.0, "top": 0.0,
    "center": 0.5, "middle": 0.5, "centre": 0.5,
    "right": 1.0, "bottom": 1.0,
}

#: 百分比坐标（``50%`` / ``12.5 %``）
_PERCENT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%\s*$")

#: 基准 + 像素偏移（``center+20`` / ``left+20`` / ``right-10`` / ``bottom-30``）：
#: 基准可以是中点（center/middle/centre）、起点（left/top）或终点（right/bottom）。
_ANCHOR_OFFSET_RE = re.compile(
    r"^(?P<anchor>center|middle|centre|left|right|top|bottom)"
    r"\s*(?P<offset>[+-]\s*\d+(?:\.\d+)?)$"
)

#: 坐标语义值的说明文本（错误提示与 schema 共用）
COORD_HELP = (
    "像素整数（>= 0）、'center'、'left'/'right'/'top'/'bottom'、百分比 '50%'、"
    "中心偏移 'center+20'/'center-20'，或基准偏移 'left+20'/'right-10'/"
    "'top+5'/'bottom-30'"
)


def parse_coordinate(value: Any, size: int, *, label: str = "坐标") -> int:
    """把坐标值解析为该轴上的像素位置。

    支持像素整数（或数字字符串）、语义关键字 / 百分比 / 相对中心偏移
    （见 :data:`COORD_KEYWORDS`）。关键字与百分比按 ``size`` 换算并夹到
    ``[0, size - 1]``；像素整数原样返回（是否越界由调用方校验）。

    Args:
        value: 坐标值（int / 数字字符串 / 语义关键字）。
        size: 该轴尺寸（像素，需 > 0）。
        label: 错误提示用的名称。

    Raises:
        ActionError: 取值无法识别，或轴尺寸非法。
    """
    if size <= 0:
        raise ActionError(f"窗口尺寸非法，无法解析{label}: {size}")
    if isinstance(value, bool):
        raise ActionError(f"{label} 取值非法: {value!r}。支持 {COORD_HELP}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ActionError(f"{label} 不能为空（支持 {COORD_HELP}）")
    try:
        return int(text, 10)
    except ValueError:
        pass
    lowered = text.lower()
    anchor = COORD_KEYWORDS.get(lowered)
    if anchor is not None:
        return _axis_pixels(_anchor_position(lowered, size), size)
    percent = _PERCENT_RE.match(text)
    if percent is not None:
        return _axis_pixels(size * float(percent.group(1)) / 100.0, size)
    offset = _ANCHOR_OFFSET_RE.match(lowered)
    if offset is not None:
        base = _anchor_position(offset.group("anchor"), size)
        return _axis_pixels(base + float(offset.group("offset")), size)
    raise ActionError(
        f"{label} 取值无法识别: {value!r}。支持 {COORD_HELP}"
    )


def _anchor_position(keyword: str, size: int) -> float:
    """语义基准在轴上的像素位置：起点 ``0``、终点 ``size - 1``、中点 ``size // 2``。"""
    ratio = COORD_KEYWORDS[keyword]
    if ratio <= 0.0:
        return 0.0
    if ratio >= 1.0:
        return float(size - 1)
    return float(size // 2)


def _axis_pixels(position: float, size: int) -> int:
    """把浮点位置夹到该轴的有效像素范围（``0..size-1``）。"""
    return max(0, min(int(round(position)), size - 1))


@dataclass(frozen=True)
class Point:
    """窗口内像素坐标（原点为窗口截图左上角）。

    ``x`` / ``y`` 也接受语义坐标值（``'center'`` / ``'50%'`` / ``'center+20'``）：
    构建动作时原样保留，注入时由 :func:`resolve_point` 按窗口实际尺寸解析。
    """

    x: int | str
    y: int | str

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class MoveAction:
    """鼠标移动。

    两种模式（互斥）：

      - **绝对**：给 ``x`` / ``y``（窗口内坐标，支持语义值），移动到该点；
      - **相对**：给 ``dx`` / ``dy``（相对**当前光标屏幕位置**的像素偏移，
        可为负），在当前光标基础上平移，便于「微调一小段距离」而不必先知道
        绝对位置。

    ``dx`` / ``dy`` 与 ``x`` / ``y`` 不能同时提供。
    """

    name: ClassVar[str] = "move"
    x: int | str | None = None
    y: int | str | None = None
    dx: int | None = None
    dy: int | None = None
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD
    #: 目标窗口选择器（空串 = 主窗口；见 ``windows`` 模块）
    window: str = ""

    @property
    def is_relative(self) -> bool:
        """是否为相对移动（给了 ``dx`` / ``dy``）。"""
        return self.dx is not None or self.dy is not None


@dataclass(frozen=True)
class HoverAction:
    """鼠标悬停：移动到目标点后保持 ``dwell`` 秒不动。

    用于触发鼠标悬停才出现的界面（tooltip、悬浮菜单、延迟加载的子项）；
    本身不点击，只是「移过去并停留」。
    """

    name: ClassVar[str] = "hover"
    x: int | str = 0
    y: int | str = 0
    dwell: float = DEFAULT_HOVER_DWELL
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD
    window: str = ""


@dataclass(frozen=True)
class ClickAction:
    """鼠标点击（``count=2`` 即双击）。``x``/``y`` 省略时点击窗口中心。

    ``hold`` > 0 表示「长按」：按下后保持指定秒数再弹起；
    ``interval`` 控制多次点击（双击 / 三击）之间的间隔。
    """

    name: ClassVar[str] = "click"
    button: str = DEFAULT_BUTTON
    count: int = DEFAULT_CLICK_COUNT
    x: int | str | None = None
    y: int | str | None = None
    hold: float = DEFAULT_CLICK_HOLD
    interval: float = DEFAULT_CLICK_INTERVAL
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD
    window: str = ""


@dataclass(frozen=True)
class DragAction:
    """按住鼠标从起点拖到终点（``steps`` 控制轨迹插值粒度）。"""

    name: ClassVar[str] = "drag"
    to_x: int | str = 0
    to_y: int | str = 0
    from_x: int | str | None = None
    from_y: int | str | None = None
    button: str = DEFAULT_BUTTON
    duration: float = DEFAULT_DRAG_DURATION
    steps: int = DEFAULT_DRAG_STEPS
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD
    window: str = ""


@dataclass(frozen=True)
class ScrollAction:
    """滚轮滚动。``x``/``y`` 省略时在窗口中心滚动。"""

    name: ClassVar[str] = "scroll"
    direction: str = DEFAULT_SCROLL_DIRECTION
    amount: int = DEFAULT_SCROLL_AMOUNT
    x: int | str | None = None
    y: int | str | None = None
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD
    window: str = ""


@dataclass(frozen=True)
class KeyAction:
    """键盘按键（可带修饰键；``phase`` 区分按下 / 弹起 / 完整按键）。

    ``repeat`` 为连按次数（``phase='press'`` 时生效），用于一次调用完成
    「连按 N 次」而不必多次调用。
    """

    name: ClassVar[str] = "key"
    shortcut: Shortcut = field(default_factory=lambda: Shortcut((), "enter"))
    phase: str = DEFAULT_KEY_PHASE
    method: str = DEFAULT_METHOD
    repeat: int = DEFAULT_KEY_REPEAT
    window: str = ""

    @property
    def effective_repeat(self) -> int:
        """实际重复次数：仅 ``press``（完整按键）阶段支持连按。

        ``down`` / ``up`` 是长按语义的「按下 / 弹起分离」，重复发送同一个
        阶段没有意义（会产生按键自动重复或悬空弹起），故恒为 1。
        """
        return self.repeat if self.phase == "press" else 1


@dataclass(frozen=True)
class TextAction:
    """文本输入（逐字符注入，``\n`` / ``\t`` 转义为对应按键）。"""

    name: ClassVar[str] = "type"
    text: str = ""
    method: str = DEFAULT_METHOD
    window: str = ""


InputAction = Union[MoveAction, ClickAction, DragAction, ScrollAction,
                    KeyAction, TextAction]


# ── 参数取值辅助 ────────────────────────────────────────

def _raw(params: Mapping[str, Any], *names: str) -> Any:
    """按顺序取第一个非 None 的参数值。"""
    for name in names:
        if name in params and params[name] is not None:
            return params[name]
    return None


def _int_arg(params: Mapping[str, Any], *names: str, minimum: int | None = None,
             maximum: int | None = None, label: str | None = None) -> int | None:
    """取整数参数（接受数字字符串），范围非法/类型非法抛 ActionError。"""
    raw = _raw(params, *names)
    if raw is None:
        return None
    name = label or names[0]
    if isinstance(raw, bool):
        raise ActionError(f"{name} 必须是整数，当前: {raw!r}")
    if isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip()
        try:
            value = int(text)
        except ValueError:
            raise ActionError(f"{name} 必须是整数，当前: {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ActionError(f"{name} 不能小于 {minimum}，当前: {value}")
    if maximum is not None and value > maximum:
        raise ActionError(f"{name} 不能大于 {maximum}，当前: {value}")
    return value


def _float_arg(params: Mapping[str, Any], *names: str, minimum: float | None = None,
               maximum: float | None = None, label: str | None = None) -> float | None:
    """取数值参数（接受数字字符串），范围非法/类型非法抛 ActionError。"""
    raw = _raw(params, *names)
    if raw is None:
        return None
    name = label or names[0]
    if isinstance(raw, bool):
        raise ActionError(f"{name} 必须是数值，当前: {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ActionError(f"{name} 必须是数值，当前: {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ActionError(f"{name} 不能小于 {minimum}，当前: {value}")
    if maximum is not None and value > maximum:
        raise ActionError(f"{name} 不能大于 {maximum}，当前: {value}")
    return value


def _text_arg(params: Mapping[str, Any], *names: str,
              allow_empty: bool = False, label: str | None = None) -> str:
    """取文本参数，空文本（除 allow_empty）抛 ActionError。"""
    raw = _raw(params, *names)
    name = label or names[0]
    if raw is None:
        raise ActionError(f"{name} 参数必填")
    text = str(raw)
    if not allow_empty and not text:
        raise ActionError(f"{name} 参数不能为空")
    return text


def _button_arg(params: Mapping[str, Any], default: str = DEFAULT_BUTTON) -> str:
    raw = _raw(params, "button")
    if raw is None:
        return default
    button = str(raw).strip().lower()
    if button not in BUTTONS:
        raise ActionError(
            f"鼠标按钮非法: {raw!r}。支持: {', '.join(BUTTONS)}"
        )
    return button


def _method_arg(params: Mapping[str, Any]) -> str:
    raw = _raw(params, "method")
    if raw is None:
        return DEFAULT_METHOD
    method = str(raw).strip().lower()
    if method not in METHODS:
        raise ActionError(
            f"投递方式非法: {raw!r}。支持: {', '.join(METHODS)}"
            f"（sendinput = 合成真实输入事件；message = 直接投递窗口消息）"
        )
    return method


def _phase_arg(params: Mapping[str, Any]) -> str:
    """取按键阶段参数（``press`` / ``down`` / ``up``，接受常见别名）。

    ``down`` 只发送按下消息、``up`` 只发送弹起消息（长按场景可先 down 后 up，
    无需在两次调用间保持系统状态）；缺省 ``press`` 表示按下后立即弹起。
    """
    raw = _raw(params, "phase", "key_phase", "state")
    if raw is None:
        return DEFAULT_KEY_PHASE
    phase = KEY_PHASE_ALIASES.get(str(raw).strip().lower())
    if phase is None:
        raise ActionError(
            f"按键阶段非法: {raw!r}。支持: press（按下并弹起）、"
            f"down（只按下）、up（只弹起）"
        )
    return phase


def _window_arg(params: Mapping[str, Any]) -> str:
    """取目标窗口选择器（空串 = 主窗口；见 ``_screenshot.windows``）。

    ``window`` 支持 ``main`` / ``active`` / ``#N`` / ``handle:0x…`` /
    ``title:子串`` / ``class:子串`` / ``popup`` / ``dialog``；格式非法时
    在构建动作阶段即报错，避免把错误留到注入时才发现。

    Raises:
        ActionError: 选择器格式非法。
    """
    raw = _raw(params, "window", "target_window", "window_selector")
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    try:
        parse_selector(text)
    except SelectorError as exc:
        raise ActionError(f"窗口选择器非法: {exc}") from exc
    return text


def _point_args(params: Mapping[str, Any], *, label: str = "坐标") -> Point | None:
    """取可选的 (x, y) 点：都缺省返回 None，只给一个报错。

    坐标接受像素整数与语义值（``'center'`` / ``'50%'`` / ``'center+20'``，见
    :func:`parse_coordinate`）：构建阶段只做格式校验，实际解析在注入时按
    窗口尺寸完成（窗口可能被缩放，提前算好的像素会失准）。
    """
    x = _raw(params, "x")
    y = _raw(params, "y")
    if x is None and y is None:
        return None
    if x is None or y is None:
        raise ActionError(f"{label}必须同时提供 x 与 y（当前 x={x!r}, y={y!r}）")
    return Point(x=_coord_arg(params, "x", label="x"),
                 y=_coord_arg(params, "y", label="y"))


def _offset_arg(params: Mapping[str, Any], name: str, *,
                label: str | None = None) -> int | None:
    """取相对偏移参数（``dx`` / ``dy``）：整数、允许为负、绝对值有上限。

    Raises:
        ActionError: 类型非法、超出 ``MAX_MOVE_OFFSET`` 或不是整数。
    """
    raw = _raw(params, name)
    if raw is None:
        return None
    name = label or name
    if isinstance(raw, bool):
        raise ActionError(f"{name} 取值非法: {raw!r}（相对偏移需为整数像素）")
    if isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip()
        try:
            value = int(text, 10)
        except ValueError:
            raise ActionError(f"{name} 必须是整数像素，当前: {raw!r}") from None
    if abs(value) > MAX_MOVE_OFFSET:
        raise ActionError(
            f"{name} 超出上限（绝对值 <= {MAX_MOVE_OFFSET} 像素），当前: {value}"
        )
    return value


def _coord_arg(params: Mapping[str, Any], name: str, *,
               label: str | None = None) -> int | str | None:
    """取坐标参数：数字（含数字字符串）转为整数并校验非负，语义值原样保留。

    Raises:
        ActionError: 取值为负数、类型非法，或语义值无法识别。
    """
    raw = _raw(params, name)
    if raw is None:
        return None
    name = label or name
    if isinstance(raw, bool):
        raise ActionError(f"{name} 取值非法: {raw!r}。支持 {COORD_HELP}")
    if isinstance(raw, int):
        if raw < 0:
            raise ActionError(
                f"{name} 不能为负数（{raw}）。像素坐标需 >= 0；相对中心偏移请用 "
                f"'center+20' / 'center-20'"
            )
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(text, 10)
    except ValueError:
        parse_coordinate(text, 1000, label=name)  # 仅格式校验（尺寸在注入时确定）
        return text
    if value < 0:
        raise ActionError(
            f"{name} 不能为负数（{value}）。像素坐标需 >= 0；相对中心偏移请用 "
            f"'center+20' / 'center-20'"
        )
    return value


# ── 动作构建 ────────────────────────────────────────────

def build_action(op: str, params: Mapping[str, Any]) -> InputAction:
    """按 op 名从扁平参数构建输入动作。

    Args:
        op: 动作名（``INPUT_OPS`` 之一，大小写不敏感）。
        params: 扁平参数字典（bash_opt 的入参）。

    Raises:
        ActionError: 动作未知或参数非法。
    """
    name = str(op or "").strip().lower()
    builders: dict = {
        "move": _build_move,
        "hover": _build_hover,
        "click": _build_click,
        "drag": _build_drag,
        "scroll": _build_scroll,
        "key": _build_key,
        "type": _build_text,
    }
    builder = builders.get(name)
    if builder is None:
        raise ActionError(
            f"未知输入动作: {op!r}。支持: {', '.join(INPUT_OPS)}"
        )
    return builder(dict(params or {}))


def _build_move(params: dict) -> MoveAction:
    """构建 ``move``：绝对坐标（x/y）与相对偏移（dx/dy）互斥。"""
    has_offset = _raw(params, "dx") is not None or _raw(params, "dy") is not None
    if has_offset:
        if _raw(params, "x") is not None or _raw(params, "y") is not None:
            raise ActionError(
                "move 的绝对坐标（x/y）与相对偏移（dx/dy）不能同时提供："
                "要移动到窗口内某点给 x/y，要在当前光标基础上平移给 dx/dy"
            )
        dx = _offset_arg(params, "dx")
        dy = _offset_arg(params, "dy")
        if dx is None or dy is None:
            raise ActionError(
                f"move 相对移动需要同时提供 dx 与 dy（当前 dx={dx!r}, dy={dy!r}）；"
                f"不移动的轴请显式给 0"
            )
        return MoveAction(dx=dx, dy=dy,
                          modifiers=parse_modifiers(_raw(params, "modifiers")),
                          method=_method_arg(params),
                          window=_window_arg(params))
    point = _point_args(params)
    if point is None:
        raise ActionError(
            "move 需要 x 与 y 参数指定窗口内坐标（或 dx/dy 相对当前光标偏移）"
        )
    return MoveAction(x=point.x, y=point.y,
                      modifiers=parse_modifiers(_raw(params, "modifiers")),
                      method=_method_arg(params),
                      window=_window_arg(params))


def _build_hover(params: dict) -> HoverAction:
    """构建 ``hover``（移动到目标点并停留）。"""
    point = _point_args(params)
    if point is None:
        raise ActionError("hover 需要 x 与 y 参数指定窗口内坐标")
    dwell = _float_arg(params, "dwell", "duration", minimum=0.0,
                       maximum=MAX_HOVER_DWELL)
    return HoverAction(x=point.x, y=point.y,
                       dwell=DEFAULT_HOVER_DWELL if dwell is None else dwell,
                       modifiers=parse_modifiers(_raw(params, "modifiers")),
                       method=_method_arg(params),
                       window=_window_arg(params))


def _build_click(params: dict) -> ClickAction:
    point = _point_args(params)
    count = _int_arg(params, "count", minimum=1, maximum=MAX_CLICK_COUNT)
    hold = _float_arg(params, "hold", minimum=0.0, maximum=MAX_CLICK_HOLD)
    interval = _float_arg(params, "interval", minimum=0.0,
                          maximum=MAX_CLICK_INTERVAL)
    return ClickAction(
        button=_button_arg(params),
        count=DEFAULT_CLICK_COUNT if count is None else count,
        x=point.x if point else None,
        y=point.y if point else None,
        hold=DEFAULT_CLICK_HOLD if hold is None else hold,
        interval=DEFAULT_CLICK_INTERVAL if interval is None else interval,
        modifiers=parse_modifiers(_raw(params, "modifiers")),
        method=_method_arg(params),
        window=_window_arg(params),
    )


def _build_drag(params: dict) -> DragAction:
    to_x = _coord_arg(params, "to_x")
    to_y = _coord_arg(params, "to_y")
    if to_x is None or to_y is None:
        raise ActionError("drag 需要 to_x 与 to_y 参数指定拖动终点（窗口内坐标）")
    from_x = _coord_arg(params, "from_x")
    from_y = _coord_arg(params, "from_y")
    if (from_x is None) != (from_y is None):
        raise ActionError(
            f"drag 起点必须同时提供 from_x 与 from_y（当前 from_x={from_x!r}, "
            f"from_y={from_y!r}）；都省略时从窗口中心开始拖动"
        )
    duration = _float_arg(params, "duration", minimum=0.0,
                          maximum=MAX_DRAG_DURATION)
    steps = _int_arg(params, "steps", minimum=MIN_DRAG_STEPS,
                     maximum=MAX_DRAG_STEPS)
    return DragAction(
        to_x=to_x,
        to_y=to_y,
        from_x=from_x,
        from_y=from_y,
        button=_button_arg(params),
        duration=DEFAULT_DRAG_DURATION if duration is None else duration,
        steps=DEFAULT_DRAG_STEPS if steps is None else steps,
        modifiers=parse_modifiers(_raw(params, "modifiers")),
        method=_method_arg(params),
        window=_window_arg(params),
    )


def _build_scroll(params: dict) -> ScrollAction:
    raw_direction = _raw(params, "direction")
    if raw_direction is None:
        direction = DEFAULT_SCROLL_DIRECTION
    else:
        direction = str(raw_direction).strip().lower()
        if direction not in SCROLL_DIRECTIONS:
            raise ActionError(
                f"滚动方向非法: {raw_direction!r}。支持: {', '.join(SCROLL_DIRECTIONS)}"
            )
    amount = _int_arg(params, "amount", minimum=1, maximum=MAX_SCROLL_AMOUNT)
    point = _point_args(params)
    return ScrollAction(
        direction=direction,
        amount=DEFAULT_SCROLL_AMOUNT if amount is None else amount,
        x=point.x if point else None,
        y=point.y if point else None,
        modifiers=parse_modifiers(_raw(params, "modifiers")),
        method=_method_arg(params),
        window=_window_arg(params),
    )


def _build_key(params: dict) -> KeyAction:
    raw = _raw(params, "key", "combo", "shortcut")
    if raw is None:
        raise ActionError("key 操作需要 key 参数指定按键，如 key='ctrl+s' / key='enter'")
    # 允许单个修饰键作为主键（key='ctrl' 长按 / 弹起 Ctrl 键本身）
    shortcut = parse_shortcut(str(raw), allow_modifier_key=True)
    # modifiers 参数可与组合键语法叠加（模型两种写法都支持，避免静默忽略）
    extra = parse_modifiers(_raw(params, "modifiers"))
    if extra:
        merged = set(shortcut.modifiers) | set(extra)
        shortcut = Shortcut(
            modifiers=tuple(name for name in MODIFIER_ORDER if name in merged),
            key=shortcut.key,
        )
    repeat = _int_arg(params, "repeat", "times", minimum=1,
                      maximum=MAX_KEY_REPEAT, label="repeat")
    return KeyAction(shortcut=shortcut, phase=_phase_arg(params),
                     method=_method_arg(params),
                     repeat=DEFAULT_KEY_REPEAT if repeat is None else repeat,
                     window=_window_arg(params))


def _build_text(params: dict) -> TextAction:
    return TextAction(
        text=_text_arg(params, "text"),
        method=_method_arg(params),
        window=_window_arg(params),
    )


# ── 坐标解析（后端共用） ────────────────────────────────

def resolve_point(x: int | str | None, y: int | str | None,
                  width: int, height: int, *, label: str = "坐标") -> Point:
    """把可选坐标解析为窗口内的绝对点（缺省取窗口中心），并做越界校验。

    坐标支持像素整数与语义值（``'center'`` / ``'50%'`` / ``'center+20'``），
    语义值按 ``width`` / ``height`` 换算后夹到有效范围。

    Raises:
        ActionError: 窗口尺寸非法、只给一个坐标、坐标取值无法识别或越界。
    """
    if width <= 0 or height <= 0:
        raise ActionError(f"窗口尺寸非法，无法定位{label}: {width}x{height}")
    if x is None and y is None:
        return Point(width // 2, height // 2)
    if x is None or y is None:
        raise ActionError(f"{label}必须同时提供 x 与 y（当前 x={x!r}, y={y!r}）")
    resolved = Point(
        parse_coordinate(x, width, label=f"{label} x"),
        parse_coordinate(y, height, label=f"{label} y"),
    )
    _validate_point(resolved, width, height, label=label)
    return resolved


def validate_point(point: Point, width: int, height: int,
                   *, label: str = "坐标") -> Point:
    """校验点落在窗口内，返回解析后的点（语义坐标按窗口尺寸换算）。

    Raises:
        ActionError: 窗口尺寸非法、坐标取值无法识别或点越界。
    """
    if width <= 0 or height <= 0:
        raise ActionError(f"窗口尺寸非法，无法定位{label}: {width}x{height}")
    resolved = Point(
        parse_coordinate(point.x, width, label=f"{label} x"),
        parse_coordinate(point.y, height, label=f"{label} y"),
    )
    _validate_point(resolved, width, height, label=label)
    return resolved


def _validate_point(point: Point, width: int, height: int, *, label: str) -> None:
    if not (0 <= point.x < width) or not (0 <= point.y < height):
        raise ActionError(
            f"{label}超出窗口范围: ({point.x}, {point.y})，"
            f"需满足 0<=x<{width} 且 0<=y<{height}"
        )


def interpolate(from_point: Point, to_point: Point, steps: int) -> list[Point]:
    """在起终点之间线性插值（含终点，不含起点），用于拖动轨迹。"""
    count = max(int(steps), MIN_DRAG_STEPS)
    points: list[Point] = []
    for index in range(1, count + 1):
        ratio = index / count
        points.append(Point(
            x=round(from_point.x + (to_point.x - from_point.x) * ratio),
            y=round(from_point.y + (to_point.y - from_point.y) * ratio),
        ))
    return points


def describe_action(action: InputAction) -> dict:
    """把动作转成可序列化摘要（用于工具返回值与显示）。"""
    if isinstance(action, ClickAction):
        payload = {
            "button": action.button,
            "count": action.count,
            "position": "center" if action.x is None else {"x": action.x, "y": action.y},
        }
        if action.hold != DEFAULT_CLICK_HOLD:
            payload["hold"] = action.hold
        if action.interval != DEFAULT_CLICK_INTERVAL:
            payload["interval"] = action.interval
    elif isinstance(action, MoveAction):
        if action.is_relative:
            payload = {"relative": {"dx": action.dx, "dy": action.dy}}
        else:
            payload = {"position": {"x": action.x, "y": action.y}}
    elif isinstance(action, HoverAction):
        payload = {
            "position": {"x": action.x, "y": action.y},
            "dwell": action.dwell,
        }
    elif isinstance(action, DragAction):
        payload = {
            "button": action.button,
            "from": ("center" if action.from_x is None
                     else {"x": action.from_x, "y": action.from_y}),
            "to": {"x": action.to_x, "y": action.to_y},
            "duration": action.duration,
            "steps": action.steps,
        }
    elif isinstance(action, ScrollAction):
        payload = {
            "direction": action.direction,
            "amount": action.amount,
            "position": "center" if action.x is None else {"x": action.x, "y": action.y},
        }
    elif isinstance(action, KeyAction):
        payload = {
            "key": action.shortcut.display(),
            "modifiers": list(action.shortcut.modifiers),
            "main_key": action.shortcut.key,
        }
        if action.phase != DEFAULT_KEY_PHASE:
            payload["phase"] = action.phase
        if action.repeat != DEFAULT_KEY_REPEAT:
            payload["repeat"] = action.repeat
            if action.effective_repeat != action.repeat:
                payload["effective_repeat"] = action.effective_repeat
    elif isinstance(action, TextAction):
        payload = {"text": action.text, "length": len(action.text)}
    else:  # pragma: no cover - 动作类型封闭
        payload = {}
    payload["action"] = action.name
    modifiers = getattr(action, "modifiers", None)
    if modifiers:
        payload["modifiers"] = list(modifiers)
    method = getattr(action, "method", DEFAULT_METHOD)
    if method != DEFAULT_METHOD:
        payload["method"] = method
    window = getattr(action, "window", "")
    if window:
        payload["window"] = window
    return payload


__all__ = [
    "BUTTONS",
    "COORD_HELP",
    "COORD_KEYWORDS",
    "ClickAction",
    "DEFAULT_BUTTON",
    "DEFAULT_CLICK_COUNT",
    "DEFAULT_CLICK_HOLD",
    "DEFAULT_CLICK_INTERVAL",
    "DEFAULT_DRAG_DURATION",
    "DEFAULT_DRAG_STEPS",
    "DEFAULT_HOVER_DWELL",
    "DEFAULT_KEY_PHASE",
    "DEFAULT_KEY_REPEAT",
    "DEFAULT_METHOD",
    "DEFAULT_SCROLL_AMOUNT",
    "DEFAULT_SCROLL_DIRECTION",
    "DragAction",
    "HoverAction",
    "INPUT_OPS",
    "InputAction",
    "KEY_PHASES",
    "KeyAction",
    "MAX_CLICK_HOLD",
    "MAX_CLICK_INTERVAL",
    "MAX_HOVER_DWELL",
    "MAX_KEY_REPEAT",
    "MAX_MOVE_OFFSET",
    "METHODS",
    "MoveAction",
    "Point",
    "SCROLL_DIRECTIONS",
    "ScrollAction",
    "TextAction",
    "build_action",
    "describe_action",
    "interpolate",
    "parse_coordinate",
    "resolve_point",
    "validate_point",
]
