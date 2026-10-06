"""窗口输入动作的数据模型与解析（跨平台语义）。

一个「输入动作」是与平台无关的纯数据描述，各平台后端负责把它翻译成
具体的注入调用（SendInput / xdotool / osascript+cliclick）。

支持的动作（``INPUT_OPS``）：

  - ``move``    鼠标移动到窗口内某点
  - ``click``   鼠标点击（左/右/中键，可双击、可带修饰键）
  - ``drag``    按住鼠标从一点拖到另一点（带轨迹插值）
  - ``scroll``  滚轮滚动（上下左右）
  - ``key``     键盘按键（支持 ``ctrl+shift+s`` 组合、功能键）
  - ``type``    文本输入（逐字符，支持任意 Unicode）

坐标语义：以**窗口截图左上角**为原点（与 ``op=screenshot`` 产物一致），
单位像素；``click`` / ``scroll`` 省略坐标时默认窗口中心；越界坐标报错。

动作是冻结（frozen）数据类，可安全共享与单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Union

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

#: 全部输入动作名（bash_opt 的 op 取值集合）
INPUT_OPS: tuple[str, ...] = ("click", "move", "drag", "scroll", "key", "type")


@dataclass(frozen=True)
class Point:
    """窗口内像素坐标（原点为窗口截图左上角）。"""

    x: int
    y: int

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class MoveAction:
    """鼠标移动到窗口内某点。"""

    name: ClassVar[str] = "move"
    x: int
    y: int
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD


@dataclass(frozen=True)
class ClickAction:
    """鼠标点击（``count=2`` 即双击）。``x``/``y`` 省略时点击窗口中心。"""

    name: ClassVar[str] = "click"
    button: str = DEFAULT_BUTTON
    count: int = DEFAULT_CLICK_COUNT
    x: int | None = None
    y: int | None = None
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD


@dataclass(frozen=True)
class DragAction:
    """按住鼠标从起点拖到终点（``steps`` 控制轨迹插值粒度）。"""

    name: ClassVar[str] = "drag"
    to_x: int = 0
    to_y: int = 0
    from_x: int | None = None
    from_y: int | None = None
    button: str = DEFAULT_BUTTON
    duration: float = DEFAULT_DRAG_DURATION
    steps: int = DEFAULT_DRAG_STEPS
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD


@dataclass(frozen=True)
class ScrollAction:
    """滚轮滚动。``x``/``y`` 省略时在窗口中心滚动。"""

    name: ClassVar[str] = "scroll"
    direction: str = DEFAULT_SCROLL_DIRECTION
    amount: int = DEFAULT_SCROLL_AMOUNT
    x: int | None = None
    y: int | None = None
    modifiers: tuple[str, ...] = ()
    method: str = DEFAULT_METHOD


@dataclass(frozen=True)
class KeyAction:
    """键盘按键（可带修饰键）。"""

    name: ClassVar[str] = "key"
    shortcut: Shortcut = field(default_factory=lambda: Shortcut((), "enter"))
    method: str = DEFAULT_METHOD


@dataclass(frozen=True)
class TextAction:
    """文本输入（逐字符注入，``\n`` / ``\t`` 转义为对应按键）。"""

    name: ClassVar[str] = "type"
    text: str = ""
    method: str = DEFAULT_METHOD


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


def _point_args(params: Mapping[str, Any], *, label: str = "坐标") -> Point | None:
    """取可选的 (x, y) 点：都缺省返回 None，只给一个报错。"""
    x = _raw(params, "x")
    y = _raw(params, "y")
    if x is None and y is None:
        return None
    if x is None or y is None:
        raise ActionError(f"{label}必须同时提供 x 与 y（当前 x={x!r}, y={y!r}）")
    x_value = _int_arg(params, "x", minimum=0, label="x")
    y_value = _int_arg(params, "y", minimum=0, label="y")
    if x_value is None or y_value is None:  # pragma: no cover - 上面的 None 检查已保证
        raise ActionError(f"{label}必须同时提供 x 与 y")
    return Point(x=x_value, y=y_value)


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
    point = _point_args(params)
    if point is None:
        raise ActionError("move 需要 x 与 y 参数指定窗口内坐标")
    return MoveAction(x=point.x, y=point.y,
                      modifiers=parse_modifiers(_raw(params, "modifiers")),
                      method=_method_arg(params))


def _build_click(params: dict) -> ClickAction:
    point = _point_args(params)
    count = _int_arg(params, "count", minimum=1, maximum=MAX_CLICK_COUNT)
    return ClickAction(
        button=_button_arg(params),
        count=DEFAULT_CLICK_COUNT if count is None else count,
        x=point.x if point else None,
        y=point.y if point else None,
        modifiers=parse_modifiers(_raw(params, "modifiers")),
        method=_method_arg(params),
    )


def _build_drag(params: dict) -> DragAction:
    to_x = _int_arg(params, "to_x", minimum=0, label="to_x")
    to_y = _int_arg(params, "to_y", minimum=0, label="to_y")
    if to_x is None or to_y is None:
        raise ActionError("drag 需要 to_x 与 to_y 参数指定拖动终点（窗口内坐标）")
    from_x = _int_arg(params, "from_x", minimum=0, label="from_x")
    from_y = _int_arg(params, "from_y", minimum=0, label="from_y")
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
    )


def _build_key(params: dict) -> KeyAction:
    raw = _raw(params, "key", "combo", "shortcut")
    if raw is None:
        raise ActionError("key 操作需要 key 参数指定按键，如 key='ctrl+s' / key='enter'")
    shortcut = parse_shortcut(str(raw))
    # modifiers 参数可与组合键语法叠加（模型两种写法都支持，避免静默忽略）
    extra = parse_modifiers(_raw(params, "modifiers"))
    if extra:
        merged = set(shortcut.modifiers) | set(extra)
        shortcut = Shortcut(
            modifiers=tuple(name for name in MODIFIER_ORDER if name in merged),
            key=shortcut.key,
        )
    return KeyAction(shortcut=shortcut, method=_method_arg(params))


def _build_text(params: dict) -> TextAction:
    return TextAction(
        text=_text_arg(params, "text"),
        method=_method_arg(params),
    )


# ── 坐标解析（后端共用） ────────────────────────────────

def resolve_point(x: int | None, y: int | None, width: int, height: int,
                  *, label: str = "坐标") -> Point:
    """把可选坐标解析为窗口内的绝对点（缺省取窗口中心），并做越界校验。

    Raises:
        ActionError: 窗口尺寸非法、只给一个坐标、或坐标越界。
    """
    if width <= 0 or height <= 0:
        raise ActionError(f"窗口尺寸非法，无法定位{label}: {width}x{height}")
    if x is None and y is None:
        return Point(width // 2, height // 2)
    if x is None or y is None:
        raise ActionError(f"{label}必须同时提供 x 与 y（当前 x={x!r}, y={y!r}）")
    resolved = Point(int(x), int(y))
    _validate_point(resolved, width, height, label=label)
    return resolved


def validate_point(point: Point, width: int, height: int,
                   *, label: str = "坐标") -> Point:
    """校验点落在窗口内，返回该点。

    Raises:
        ActionError: 窗口尺寸非法或点越界。
    """
    if width <= 0 or height <= 0:
        raise ActionError(f"窗口尺寸非法，无法定位{label}: {width}x{height}")
    _validate_point(point, width, height, label=label)
    return point


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
    elif isinstance(action, MoveAction):
        payload = {"position": {"x": action.x, "y": action.y}}
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
    return payload


__all__ = [
    "BUTTONS",
    "ClickAction",
    "DEFAULT_BUTTON",
    "DEFAULT_CLICK_COUNT",
    "DEFAULT_DRAG_DURATION",
    "DEFAULT_DRAG_STEPS",
    "DEFAULT_METHOD",
    "DEFAULT_SCROLL_AMOUNT",
    "DEFAULT_SCROLL_DIRECTION",
    "DragAction",
    "INPUT_OPS",
    "InputAction",
    "KeyAction",
    "METHODS",
    "MoveAction",
    "Point",
    "SCROLL_DIRECTIONS",
    "ScrollAction",
    "TextAction",
    "build_action",
    "describe_action",
    "interpolate",
    "resolve_point",
    "validate_point",
]
