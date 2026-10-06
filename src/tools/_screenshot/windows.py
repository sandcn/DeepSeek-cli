"""窗口目录与目标窗口选择（截图 / 输入注入共用）。

一个「窗口目录」是该进程树当前全部可操作顶层窗口的列表
（:class:`WindowInfo`）。平台后端负责产出（Windows 走 EnumWindows、X11 走
xdotool/wmctrl、macOS 走 Quartz），本模块负责**跨平台一致的选择语义**：

  - ``main``（缺省）：主窗口（非工具窗口 > 未最小化 > 有标题 > 面积大 > PID 小）
  - ``active``：当前前台 / 聚焦窗口
  - ``#N``：按 Z 序第 N 个（1 = 最靠前——弹出菜单、下拉浮层、对话框常用）
  - ``handle:0x1a2b`` / ``id:1234``：按平台窗口句柄精确指定
  - ``title:子串`` / ``class:子串`` / ``pid:1234``：按属性匹配（忽略大小写）
  - ``popup``：无标题的非主窗口（右键菜单、下拉浮层等自绘弹层）
  - ``dialog``：对话框类窗口（类名含 dialog / Win32 ``#32770``）
  - ``all`` / ``*``：全部窗口（仅列举用）

截图与输入注入共用本模块的选择规则，因此模型用 ``op=windows`` 看到窗口
清单后，可用同一选择器把截图或输入精确投向任意窗口——右键菜单、下拉浮层、
文件对话框这些**独立顶层窗口**不再是盲区。

平台后端只需产出一致的 :class:`WindowInfo` 列表，选择与排序逻辑全部在此，
新增平台无需改动既有选择规则。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from .result import ScreenshotError

logger = logging.getLogger(__name__)

#: 缺省选择器文本（主窗口）
DEFAULT_SELECTOR = "main"

#: 选择器类型（:class:`WindowSelector.kind`）
SELECTOR_KINDS: tuple[str, ...] = (
    "main", "active", "index", "handle", "title", "class", "pid",
    "popup", "dialog", "all",
)

#: ``前缀:值`` 形式的选择器 → 类型
_PREFIX_KINDS: dict[str, str] = {
    "handle": "handle", "hwnd": "handle", "id": "handle", "window": "handle",
    "title": "title", "name": "title", "caption": "title", "text": "title",
    "class": "class", "classname": "class", "class_name": "class",
    "pid": "pid", "process": "pid", "processid": "pid",
}

#: 关键字选择器（整体匹配）→ 类型
_KEYWORD_KINDS: dict[str, str] = {
    "main": "main", "primary": "main", "mainwindow": "main", "default": "main",
    "active": "active", "foreground": "active", "focused": "active",
    "focus": "active", "front": "active", "frontmost": "active",
    "popup": "popup", "pop-up": "popup", "popupmenu": "popup",
    "overlay": "popup", "menu": "popup", "dropdown": "popup",
    "dialog": "dialog", "modal": "dialog", "alert": "dialog",
    "all": "all", "*": "all", "any": "all",
}

#: 对话框类窗口的类名特征（Windows ``#32770`` 为系统对话框类）
_DIALOG_CLASS_HINTS: tuple[str, ...] = ("#32770", "dialog", "modal")

#: 列举窗口时默认最多返回的条数（防止极端情况下输出过长）
DEFAULT_LIST_LIMIT = 40


class SelectorError(ScreenshotError):
    """窗口选择器非法，或没有匹配的窗口。

    消息面向大模型：带原始选择器文本与当前窗口清单摘要，便于立即改用
    正确的选择器（如 ``#1``、``title:设置``）。
    """


@dataclass(frozen=True)
class WindowInfo:
    """一个可操作的顶层窗口（跨平台统一描述）。

    Attributes:
        handle: 平台窗口句柄（Windows HWND / X11 window id / macOS CGWindowNumber）。
        pid: 窗口所属进程的**平台 PID**（Windows 下为 WINPID）。
        title: 窗口标题（可能为空串）。
        class_name: 窗口类名 / 所属应用名（平台能力不同时可能为空串）。
        left: 窗口左上角屏幕坐标 X（含 DWM 边框）。
        top: 窗口左上角屏幕坐标 Y。
        width: 窗口宽度（像素）。
        height: 窗口高度（像素）。
        tool_window: 是否为工具窗口（通常不出现在任务栏）。
        minimized: 是否已最小化。
        visible: 是否可见。
        foreground: 是否为当前前台窗口。
        order: 枚举顺序（0 = 最靠前的窗口，近似 Z 序）。
        main: 是否为该进程树的主窗口（由 :func:`mark_main` 标注）。
    """

    handle: int
    pid: int
    title: str
    class_name: str
    width: int
    height: int
    left: int = 0
    top: int = 0
    tool_window: bool = False
    minimized: bool = False
    visible: bool = True
    foreground: bool = False
    order: int = 0
    main: bool = False

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def handle_hex(self) -> str:
        return f"0x{self.handle:X}" if self.handle else "0x0"

    def summary(self) -> str:
        """一行摘要（用于错误提示与 ``op=windows`` 输出）。"""
        parts = [f"#{self.order + 1}", self.handle_hex]
        parts.append(f"「{self.title}」" if self.title else "「无标题」")
        if self.class_name:
            parts.append(f"class={self.class_name}")
        parts.append(f"{self.width}x{self.height}")
        parts.append(f"@({self.left},{self.top})")
        flags = []
        if self.main:
            flags.append("main")
        if self.foreground:
            flags.append("foreground")
        if self.minimized:
            flags.append("minimized")
        if self.tool_window:
            flags.append("tool")
        if not self.visible:
            flags.append("hidden")
        if flags:
            parts.append("[" + ",".join(flags) + "]")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "handle": self.handle,
            "handle_hex": self.handle_hex,
            "pid": self.pid,
            "title": self.title,
            "class": self.class_name,
            "x": self.left,
            "y": self.top,
            "width": self.width,
            "height": self.height,
            "area": self.area,
            "order": self.order,
            "index": self.order + 1,
            "tool_window": self.tool_window,
            "minimized": self.minimized,
            "visible": self.visible,
            "foreground": self.foreground,
            "main": self.main,
            "summary": self.summary(),
        }


@dataclass(frozen=True)
class WindowSelector:
    """解析后的窗口选择器。"""

    kind: str
    value: Any = None
    raw: str = DEFAULT_SELECTOR

    def __post_init__(self) -> None:
        if self.kind not in SELECTOR_KINDS:
            raise SelectorError(
                f"窗口选择器类型非法: {self.kind!r}。支持: {', '.join(SELECTOR_KINDS)}"
            )

    @property
    def is_default(self) -> bool:
        return self.kind == "main"

    def describe(self) -> str:
        return self.raw or self.kind


# ── 选择器解析 ──────────────────────────────────────────

def parse_selector(value: Any) -> WindowSelector:
    """把用户输入的窗口选择器解析为 :class:`WindowSelector`。

    支持形式见模块文档；空值 / ``None`` / ``"main"`` 均表示主窗口。

    Raises:
        SelectorError: 选择器类型未知、缺值或取值非法（如 ``#0``、``handle:xyz``）。
    """
    if value is None:
        return WindowSelector("main", None, DEFAULT_SELECTOR)
    if isinstance(value, bool):
        raise SelectorError("窗口选择器需要字符串或整数，当前: 布尔值")
    if isinstance(value, int):
        return WindowSelector("handle", value, str(value))
    text = str(value).strip()
    if not text:
        return WindowSelector("main", None, DEFAULT_SELECTOR)
    lowered = text.lower()
    if lowered.startswith("#"):
        index = _parse_int(text[1:], label="窗口序号")
        if index < 1:
            raise SelectorError(f"窗口序号从 1 开始，当前: {text!r}（如 '#1'）")
        return WindowSelector("index", index, text)
    if ":" in text:
        prefix, _, rest = text.partition(":")
        kind = _PREFIX_KINDS.get(prefix.strip().lower())
        if kind is not None:
            payload = rest.strip()
            if not payload:
                raise SelectorError(f"窗口选择器缺少取值: {text!r}（如 '{prefix}:值'）")
            if kind == "handle":
                return WindowSelector("handle", _parse_int(payload, label="窗口句柄"), text)
            if kind == "pid":
                return WindowSelector("pid", _parse_int(payload, label="进程号"), text)
            return WindowSelector(kind, payload, text)
    keyword = _KEYWORD_KINDS.get(lowered)
    if keyword is not None:
        return WindowSelector(keyword, None, text)
    number = _try_int(text)
    if number is not None:
        return WindowSelector("handle", number, text)
    # 裸字符串：按标题子串匹配（最常用），匹配不到时再按类名子串兜底
    return WindowSelector("title", text, text)


def _parse_int(text: str, *, label: str) -> int:
    value = _try_int(text)
    if value is None:
        raise SelectorError(f"{label}必须是整数，当前: {text!r}")
    return value


def _try_int(text: str) -> int | None:
    """解析十进制 / ``0x`` 十六进制整数，失败返回 None。"""
    raw = str(text).strip()
    if not raw:
        return None
    try:
        if raw.lower().startswith(("0x", "-0x")):
            return int(raw, 16)
        return int(raw, 10)
    except ValueError:
        return None


# ── 主窗口与标注 ────────────────────────────────────────

def main_window(windows: Sequence[WindowInfo]) -> WindowInfo | None:
    """按「非工具窗口 > 未最小化 > 有标题 > 面积大 > PID 小」选出主窗口。

    纯函数，无副作用；空列表返回 ``None``。
    """
    if not windows:
        return None
    return max(windows, key=main_rank)


def main_rank(info: WindowInfo) -> tuple:
    """主窗口排序键（越大越优先）。"""
    return (
        not info.tool_window,
        not info.minimized,
        bool(info.title.strip()),
        info.area,
        -info.pid,
    )


def mark_main(windows: Sequence[WindowInfo]) -> list[WindowInfo]:
    """给列表标注主窗口（``main=True``），返回新列表。"""
    items = list(windows)
    target = main_window(items)
    if target is None:
        return items
    return [replace(item, main=(item is target)) for item in items]


def sort_by_z(windows: Iterable[WindowInfo]) -> list[WindowInfo]:
    """按 Z 序（``order`` 升序，前面 = 更靠上）排列。"""
    return sorted(windows, key=lambda item: item.order)


# ── 匹配 ────────────────────────────────────────────────

def filter_windows(windows: Sequence[WindowInfo],
                   selector: WindowSelector) -> list[WindowInfo]:
    """返回与选择器匹配的窗口（按 Z 序）。

    ``main`` / ``popup`` / ``dialog`` / ``active`` 等「单一目标」语义在无匹配时
    返回空列表，由 :func:`pick_window` 统一报错。
    """
    if selector.kind == "all":
        return sort_by_z(windows)
    if selector.kind == "main":
        target = main_window(windows)
        return [target] if target is not None else []
    if selector.kind == "active":
        return [item for item in sort_by_z(windows) if item.foreground]
    if selector.kind == "index":
        ordered = sort_by_z(windows)
        index = int(selector.value)
        return [ordered[index - 1]] if 1 <= index <= len(ordered) else []
    if selector.kind == "handle":
        handle = int(selector.value)
        return [item for item in windows if item.handle == handle]
    if selector.kind == "pid":
        pid = int(selector.value)
        return [item for item in windows if item.pid == pid]
    if selector.kind == "title":
        needle = str(selector.value).strip().lower()
        matched = [item for item in sort_by_z(windows) if needle in item.title.lower()]
        if matched:
            return matched
        # 标题匹配不到时按类名兜底（模型常把窗口类名当标题用）
        return [item for item in sort_by_z(windows)
                if needle in item.class_name.lower()]
    if selector.kind == "class":
        needle = str(selector.value).strip().lower()
        return [item for item in sort_by_z(windows)
                if needle in item.class_name.lower()]
    if selector.kind == "popup":
        return _match_popup(windows)
    if selector.kind == "dialog":
        return _match_dialog(windows)
    return []  # pragma: no cover - SELECTOR_KINDS 已封闭


def _match_popup(windows: Sequence[WindowInfo]) -> list[WindowInfo]:
    """匹配浮层 / 菜单类窗口：无标题且非主窗口，按 Z 序最靠前者优先。"""
    ordered = sort_by_z(windows)
    untitled = [item for item in ordered
                if not item.title.strip() and not item.minimized and not item.main]
    if untitled:
        return untitled
    return [item for item in ordered
            if not item.title.strip() and not item.minimized]


def _match_dialog(windows: Sequence[WindowInfo]) -> list[WindowInfo]:
    """匹配对话框类窗口（类名含 ``#32770`` / ``dialog`` / ``modal``）。"""
    ordered = sort_by_z(windows)
    matched = [
        item for item in ordered
        if any(hint in item.class_name.lower() for hint in _DIALOG_CLASS_HINTS)
    ]
    if matched:
        return matched
    # 工具窗口且带标题（对话框常见形态）作为次选
    return [item for item in ordered
            if item.tool_window and item.title.strip() and not item.main]


def pick_window(windows: Sequence[WindowInfo], selector: Any = None) -> WindowInfo:
    """按选择器挑出一个窗口，返回 :class:`WindowInfo`。

    Args:
        windows: 窗口目录（通常来自 ``op=windows`` / 平台后端枚举）。
        selector: 选择器文本（``None`` = 主窗口）；也可以是已解析的
            :class:`WindowSelector`。

    Raises:
        SelectorError: 目录为空、选择器非法，或没有匹配窗口。
    """
    resolved = selector if isinstance(selector, WindowSelector) else parse_selector(selector)
    items = list(windows)
    if not items:
        raise SelectorError("目标进程及其子进程没有可操作的可见窗口")
    if resolved.kind == "all":
        raise SelectorError(
            f"选择器 {resolved.describe()!r} 会命中全部窗口，请改用具体窗口"
            f"（如 '#1' / 'title:子串' / 'handle:0x…'）或先用 op=windows 查看清单"
        )
    matched = filter_windows(items, resolved)
    if not matched:
        raise SelectorError(
            f"窗口选择器 {resolved.describe()!r} 没有匹配的窗口。当前可用窗口: "
            f"{window_hint(items)}"
        )
    return matched[0]


def window_hint(windows: Sequence[WindowInfo], limit: int = 8) -> str:
    """把窗口清单压缩为一行提示文本（错误信息与结果附注用）。"""
    items = sort_by_z(windows)
    if not items:
        return "（无）"
    shown = "; ".join(item.summary() for item in items[:limit])
    if len(items) > limit:
        shown += f"; …（共 {len(items)} 个窗口）"
    return shown


def describe_windows(windows: Sequence[WindowInfo],
                    limit: int = DEFAULT_LIST_LIMIT) -> list[dict]:
    """把窗口清单转为可序列化列表（按 Z 序，``limit`` 截断）。"""
    return [item.to_dict() for item in sort_by_z(windows)[:max(int(limit), 0)]]


# ── 窗口控制（激活 / 最大化 / 移动 / 缩放 / 关闭） ───────

#: 窗口控制动作：改变窗口状态或几何，便于把坐标与布局固定下来后再操作
WINDOW_CONTROL_ACTIONS: tuple[str, ...] = (
    "activate", "maximize", "minimize", "restore", "close", "move", "resize", "fit",
)

#: 控制动作别名 → 规范动作
_CONTROL_ALIASES: dict[str, str] = {
    "activate": "activate", "focus": "activate", "front": "activate",
    "raise": "activate", "foreground": "activate", "top": "activate",
    "maximize": "maximize", "max": "maximize", "maximise": "maximize",
    "minimize": "minimize", "min": "minimize", "minimise": "minimize",
    "iconify": "minimize",
    "restore": "restore", "normal": "restore", "unminimize": "restore",
    "unmaximize": "restore",
    "close": "close", "quit": "close", "dismiss": "close",
    "move": "move", "position": "move",
    "resize": "resize", "size": "resize",
    "fit": "fit", "move_resize": "fit", "moveresize": "fit", "geometry": "fit",
    "set_bounds": "fit", "bounds": "fit",
}

#: 需要 ``x`` / ``y`` 的动作
_NEED_POSITION: frozenset[str] = frozenset({"move", "fit"})
#: 需要 ``width`` / ``height`` 的动作
_NEED_SIZE: frozenset[str] = frozenset({"resize", "fit"})


@dataclass(frozen=True)
class WindowControlRequest:
    """一次窗口控制请求（跨平台语义）。

    Attributes:
        action: :data:`WINDOW_CONTROL_ACTIONS` 之一。
        selector: 目标窗口选择器（见模块文档；``None`` = 主窗口）。
        x / y: 屏幕坐标（``move`` / ``fit`` 必填）。
        width / height: 目标尺寸（``resize`` / ``fit`` 必填）。
    """

    action: str
    selector: Any = None
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if self.action not in WINDOW_CONTROL_ACTIONS:
            raise SelectorError(
                f"窗口控制动作非法: {self.action!r}。支持: "
                f"{', '.join(WINDOW_CONTROL_ACTIONS)}"
            )
        if self.action in _NEED_POSITION and (self.x is None or self.y is None):
            raise SelectorError(f"窗口动作 {self.action} 需要同时提供 x 与 y（屏幕坐标）")
        if self.action in _NEED_SIZE and (self.width is None or self.height is None):
            raise SelectorError(f"窗口动作 {self.action} 需要同时提供 width 与 height（像素）")
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise SelectorError(f"窗口 {name} 必须为正整数，当前: {value}")

    def to_dict(self) -> dict:
        payload: dict = {"window_action": self.action}
        for name in ("x", "y", "width", "height"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


def parse_control_request(action: Any, *, window: Any = None, x: Any = None,
                          y: Any = None, width: Any = None,
                          height: Any = None) -> WindowControlRequest:
    """解析窗口控制参数（接受字符串数字，动作名大小写与别名不敏感）。

    Raises:
        SelectorError: 动作未知或几何参数缺失 / 非法。
    """
    text = str(action or "").strip().lower().replace("-", "_")
    if not text:
        raise SelectorError(
            f"窗口控制需要 window_action 参数（支持: "
            f"{', '.join(WINDOW_CONTROL_ACTIONS)}）"
        )
    normalized = _CONTROL_ALIASES.get(text)
    if normalized is None:
        raise SelectorError(
            f"窗口控制动作非法: {action!r}。支持: {', '.join(WINDOW_CONTROL_ACTIONS)}"
        )
    selector_value = None
    if window is not None and str(window).strip():
        parse_selector(window)  # 格式校验：非法选择器在此直接报错
        selector_value = str(window).strip()
    return WindowControlRequest(
        action=normalized,
        selector=selector_value,
        x=_optional_int(x, "x"),
        y=_optional_int(y, "y"),
        width=_optional_int(width, "width"),
        height=_optional_int(height, "height"),
    )


def _optional_int(value: Any, label: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise SelectorError(f"{label} 必须是整数，当前: {value!r}")
    try:
        return int(str(value).strip(), 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise SelectorError(f"{label} 必须是整数，当前: {value!r}") from None


__all__ = [
    "DEFAULT_LIST_LIMIT",
    "DEFAULT_SELECTOR",
    "SELECTOR_KINDS",
    "WINDOW_CONTROL_ACTIONS",
    "SelectorError",
    "WindowControlRequest",
    "WindowInfo",
    "WindowSelector",
    "describe_windows",
    "filter_windows",
    "main_rank",
    "main_window",
    "mark_main",
    "parse_control_request",
    "parse_selector",
    "pick_window",
    "sort_by_z",
    "window_hint",
]
