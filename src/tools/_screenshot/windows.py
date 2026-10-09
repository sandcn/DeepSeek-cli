"""窗口目录与目标窗口选择（截图 / 输入注入共用）。

一个「窗口目录」是该进程树当前全部可操作顶层窗口的列表
（:class:`WindowInfo`）。平台后端负责产出（Windows 走 EnumWindows、X11 走
xdotool/wmctrl、macOS 走 Quartz），本模块负责**跨平台一致的选择语义**：

  - ``main``（缺省）：主窗口（非工具窗口 > 未最小化 > 有标题 > 面积大 > PID 小）
  - ``active``：当前前台 / 聚焦窗口
  - ``#N``：按 Z 序第 N 个（1 = 最靠前——弹出菜单、下拉浮层、对话框常用）
  - ``handle:0x1a2b`` / ``id:1234``：按平台窗口句柄精确指定
  - ``title:子串`` / ``class:子串`` / ``pid:1234``：按属性子串匹配（忽略大小写）
  - ``title~:正则`` / ``class~:正则`` / ``re:正则``：按**正则**匹配标题 /
    类名（``re:`` 匹配标题或类名；大小写不敏感）
  - ``process:名字``（``proc:`` / ``exe:`` 亦可）：按**进程名**匹配
    （Windows 为 exe 名，含 ``.exe`` 与否都可；忽略大小写）
  - ``fuzzy:关键词``（``like:`` 亦可）：**模糊匹配**——先按子串匹配标题 /
    类名 / 进程名，未命中再按相似度（>= :data:`FUZZY_THRESHOLD`）排序返回
  - ``popup``：无标题的非主窗口（右键菜单、下拉浮层等自绘弹层）
  - ``dialog``：对话框类窗口（类名含 dialog / Win32 ``#32770``）
  - ``all`` / ``*``：全部窗口（仅列举用）

截图与输入注入共用本模块的选择规则，因此模型用 ``op=windows`` 看到窗口
清单后，可用同一选择器把截图或输入精确投向任意窗口——右键菜单、下拉浮层、
文件对话框这些**独立顶层窗口**不再是盲区。

平台后端只需产出一致的 :class:`WindowInfo` 列表，选择与排序逻辑全部在此，
新增平台无需改动既有选择规则。

展示与编号：``#N`` 选择器按**可操作窗口**（可见且未最小化）的 Z 序编号；
:func:`describe_windows` / :func:`window_hint` / :func:`indexed_summary` /
:func:`window_geometry` 都使用同一编号，保证窗口清单、错误提示与截图结果
三者互相对得上（模型可放心照抄清单里的 ``#N``）；几何换算（截图坐标系原点
与窗口外框）也由 :func:`window_geometry` 统一产出，避免各后端各自拼装。
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from .result import NoWindowError, ScreenshotError

logger = logging.getLogger(__name__)

#: 缺省选择器文本（主窗口）
DEFAULT_SELECTOR = "main"

#: 选择器类型（:class:`WindowSelector.kind`）
SELECTOR_KINDS: tuple[str, ...] = (
    "main", "active", "index", "handle", "title", "class", "pid",
    "popup", "dialog", "all", "title_re", "class_re", "regex", "process",
    "fuzzy",
)

#: ``前缀:值`` 形式的选择器 → 类型
_PREFIX_KINDS: dict[str, str] = {
    "handle": "handle", "hwnd": "handle", "id": "handle", "window": "handle",
    "title": "title", "name": "title", "caption": "title", "text": "title",
    "class": "class", "classname": "class", "class_name": "class",
    "pid": "pid", "processid": "pid",
    # 正则匹配：``title~:正则`` / ``class~:正则`` / ``re:正则``（标题或类名）
    "title~": "title_re", "title_re": "title_re", "titlere": "title_re",
    "class~": "class_re", "class_re": "class_re", "classre": "class_re",
    "re": "regex", "regex": "regex", "pattern": "regex",
    # 按进程名匹配（Windows 为 exe 名；含 .exe 与否都可）
    "process": "process", "proc": "process", "exe": "process",
    "process_name": "process", "processname": "process", "procname": "process",
    # 模糊匹配（子串未命中时按相似度）
    "fuzzy": "fuzzy", "like": "fuzzy", "approx": "fuzzy",
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
        tool_window: 是否为工具窗口（通常不出现在任务栏）。这类窗口（右键
            菜单、下拉浮层、Chrome/Electron 的弹出层）**不会成为前台窗口**，
            但仍可通过合成输入（真实光标 / 系统级按键）操作。
        minimized: 是否已最小化。
        visible: 是否可见。
        foreground: 是否为当前前台窗口。
        order: 枚举顺序（0 = 最靠前的窗口，近似 Z 序）。
        main: 是否为该进程树的主窗口（由 :func:`mark_main` 标注）。
        client_area: 是否有可换算的客户区（``ClientToScreen`` +
            ``GetClientRect`` 均可用）。``False`` 表示 PostMessage 投递
            无法换算坐标（Chrome / Electron 弹层、部分自绘窗口），需改用
            合成输入；``None`` = 平台未提供该信息。
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
    client_area: bool | None = None
    #: 所属进程的可执行名（Windows 为 exe 名，如 ``chrome.exe``；其它平台可能
    #: 为空或为应用名），供 ``process:`` 选择器与展示使用。
    process_name: str = ""
    #: 是否置顶（``WS_EX_TOPMOST``）。``None`` = 平台未提供该信息。
    topmost: bool | None = None
    #: 属主窗口句柄（弹层 / 对话框的属主主窗口；``0`` = 无属主或平台未提供）。
    owner: int = 0

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def handle_hex(self) -> str:
        return f"0x{self.handle:X}" if self.handle else "0x0"

    @property
    def owner_hex(self) -> str:
        """属主窗口句柄的十六进制文本（无属主返回空串）。"""
        return f"0x{self.owner:X}" if self.owner else ""

    def summary(self, z_index: int | None = None) -> str:
        """一行摘要（用于错误提示与 ``op=windows`` 输出）。

        Args:
            z_index: 该窗口在**可操作窗口**（可见且未最小化）中的 Z 序序号
                （1 = 最靠前），与 ``#N`` 选择器一致；``NO_SELECTABLE_INDEX``（0）
                表示该窗口当前不可被选中（隐藏 / 最小化），显示 ``#-``；
                ``None`` 表示调用方没有上下文，退回按枚举序显示。
        """
        if z_index is None:
            label = f"#{self.order + 1}"
        elif z_index <= NO_SELECTABLE_INDEX:
            label = "#-"
        else:
            label = f"#{z_index}"
        parts = [label, self.handle_hex]
        parts.append(f"「{self.title}」" if self.title else "「无标题」")
        if self.class_name:
            parts.append(f"class={self.class_name}")
        if self.process_name:
            parts.append(f"proc={self.process_name}")
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
        if self.client_area is False:
            flags.append("no-client")
        if self.topmost:
            flags.append("topmost")
        if not self.visible:
            flags.append("hidden")
        if flags:
            parts.append("[" + ",".join(flags) + "]")
        return " ".join(parts)

    def to_dict(self, z_index: int | None = None) -> dict:
        """可序列化描述（``z_index`` 见 :meth:`summary`）。

        ``z_index`` 为 ``None`` 或 :data:`NO_SELECTABLE_INDEX` 时，输出字段
        ``z_index`` 记为 ``None``（该窗口当前不能通过 ``#N`` 选中）。
        """
        selectable = is_selectable(self)
        return {
            "handle": self.handle,
            "handle_hex": self.handle_hex,
            "pid": self.pid,
            "process_name": self.process_name,
            "title": self.title,
            "class": self.class_name,
            "x": self.left,
            "y": self.top,
            "width": self.width,
            "height": self.height,
            "area": self.area,
            "order": self.order,
            "index": self.order + 1,
            "z_index": z_index if z_index else None,
            "selectable": selectable,
            "tool_window": self.tool_window,
            "client_area": self.client_area,
            "topmost": self.topmost,
            "owner": self.owner,
            "owner_hex": self.owner_hex,
            "minimized": self.minimized,
            "visible": self.visible,
            "foreground": self.foreground,
            "main": self.main,
            "summary": self.summary(z_index),
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
            if kind in ("title_re", "class_re", "regex"):
                _compile_pattern(payload, label=kind)
            return WindowSelector(kind, payload, text)
    keyword = _KEYWORD_KINDS.get(lowered)
    if keyword is not None:
        return WindowSelector(keyword, None, text)
    number = _try_int(text)
    if number is not None:
        return WindowSelector("handle", number, text)
    # 裸字符串：按标题子串匹配（最常用），匹配不到时再按类名子串兜底
    return WindowSelector("title", text, text)


def _compile_pattern(pattern: str, *, label: str = "正则"):
    """编译选择器正则（大小写不敏感），非法时抛 :class:`SelectorError`。"""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise SelectorError(f"窗口选择器{label}正则非法: {pattern!r}（{exc}）") from None


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


# ── 可操作窗口（可见且未最小化） ────────────────────────

def is_selectable(info: WindowInfo) -> bool:
    """窗口当前是否可作为选择 / 注入目标（可见且未最小化）。

    隐藏窗口（``IsWindowVisible`` 为假，如 Chrome 的 ``Chrome_WidgetWin_0``
    辅助窗口）与最小化窗口既截不到有效像素（产物全黑），也收不到鼠标 /
    键盘输入；选择时优先在可操作窗口集合中匹配，避免把这类「幽灵窗口」当
    成右键菜单 / 下拉浮层。
    """
    return bool(info.visible) and not info.minimized


def selectable_windows(windows: Sequence[WindowInfo]) -> list[WindowInfo]:
    """过滤出可操作窗口（可见且未最小化，保持原顺序）。"""
    return [item for item in windows if is_selectable(item)]


def require_capturable(info: WindowInfo, selector: Any = None) -> None:
    """校验窗口当前可作为**截图**目标（未最小化且可见），否则抛错。

    最小化窗口没有可渲染的客户区：Windows 会把它的窗口矩形写成
    ``(-32000, -32000, 237, 39)`` 这类占位值，截图得到的是一张与真实界面
    毫无关系的小图（既不黑也不像界面），比直接报错更具误导性。隐藏窗口同理。

    选择器 ``'main'`` / ``'#N'`` 通常会避开这类窗口，但**显式**给
    ``handle:0x…`` / ``title:子串`` / ``main``（当进程树内只剩最小化窗口）
    时仍可能命中，因此在截图前统一校验。

    Args:
        info: 已选中的目标窗口。
        selector: 本次使用的窗口选择器（仅用于错误提示）。

    Raises:
        NoWindowError: 窗口已最小化或不可见。
    """
    if info.minimized:
        raise NoWindowError(
            f"窗口「{info.title or '<无标题>'}」（{info.handle_hex}）当前已最小化，"
            f"截图得不到有效画面（最小化窗口没有可渲染的客户区）。"
            f"请先 op=window, window_action='restore' 还原窗口后再截图，"
            f"或改用 op=windows 里其它可操作窗口（本次选择器 "
            f"{selector or DEFAULT_SELECTOR!r}）"
        )
    if not info.visible:
        raise NoWindowError(
            f"窗口「{info.title or '<无标题>'}」（{info.handle_hex}）当前不可见，"
            f"截图得不到有效画面。可用 op=windows 查看可操作窗口清单，"
            f"或先用 op=window, window_action='restore' / 'activate' 让它显示出来"
        )


#: 「该窗口当前不可被选中」在清单里的序号占位（隐藏 / 最小化窗口）
NO_SELECTABLE_INDEX = 0


def selectable_index(windows: Sequence[WindowInfo]) -> dict[int, int]:
    """可操作窗口的 Z 序序号映射 ``{窗口句柄: 序号}``（1 = 最靠前）。

    与 ``#N`` 选择器语义一致（``#N`` = 第 N 个**可操作**窗口），供
    ``op=windows`` 清单与错误提示展示，使模型给出的 ``#N`` 与实际选中的
    窗口对得上。不可选窗口不出现在映射里，调用方用
    :data:`NO_SELECTABLE_INDEX` 占位。
    """
    ordered = sort_by_z(selectable_windows(windows))
    return {item.handle: index for index, item in enumerate(ordered, start=1)}


# ── 主窗口与标注 ────────────────────────────────────────

def main_window(windows: Sequence[WindowInfo]) -> WindowInfo | None:
    """按「可见 > 非工具窗口 > 未最小化 > 有标题 > 面积大 > PID 小」选出主窗口。

    纯函数，无副作用；空列表返回 ``None``。
    """
    if not windows:
        return None
    return max(windows, key=main_rank)


def main_rank(info: WindowInfo) -> tuple:
    """主窗口排序键（越大越优先）。

    可见性排在首位：隐藏 / 最小化的「幽灵窗口」即使面积更大、标题更全，
    也不应被当作主窗口（截图为全黑、输入打不进去）。
    """
    return (
        bool(info.visible) and not info.minimized,
        not info.tool_window,
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

#: 只在可操作窗口（可见且未最小化）中匹配的选择器类型——无匹配即报错，
#: 不退回隐藏 / 最小化窗口：这类窗口截图为全黑、输入也打不进去，
#: 退回只会让模型误以为「选中了弹层」
_SELECTABLE_ONLY_KINDS: frozenset[str] = frozenset({"popup", "dialog"})


def filter_windows(windows: Sequence[WindowInfo],
                   selector: WindowSelector) -> list[WindowInfo]:
    """返回与选择器匹配的窗口（按 Z 序）。

    匹配策略：

      - ``main`` / ``active`` / ``handle`` / ``pid``：按各自语义匹配（``main``
        已把可见性纳入排序，优先选中可见窗口）；
      - ``index``（``#N``）：在**可操作窗口**（可见且未最小化）中按 Z 序取第 N 个
        —— 与 ``op=windows`` 清单中标注的 ``#N`` 一致；
      - ``title`` / ``class``：优先在可操作窗口中匹配，无命中再回退全部窗口
        （按标题查隐藏窗口是合理需求，如排查辅助窗口）；
      - ``popup`` / ``dialog``：只在可操作窗口中匹配，无命中返回空列表由
        :func:`pick_window` 报错——避免把隐藏辅助窗口当成弹出的菜单 / 浮层。

    无匹配时返回空列表，由 :func:`pick_window` 统一报错（附窗口清单）。
    """
    if selector.kind == "all":
        return sort_by_z(windows)
    if selector.kind == "main":
        target = main_window(windows)
        return [target] if target is not None else []
    if selector.kind == "active":
        return [item for item in sort_by_z(windows) if item.foreground]
    if selector.kind == "handle":
        handle = int(selector.value)
        return [item for item in windows if item.handle == handle]
    if selector.kind == "pid":
        pid = int(selector.value)
        return [item for item in windows if item.pid == pid]
    if selector.kind in _SELECTABLE_ONLY_KINDS:
        pool = selectable_windows(windows)
        if selector.kind == "popup":
            return _match_popup(pool)
        return _match_dialog(pool)
    if selector.kind == "index":
        ordered = sort_by_z(selectable_windows(windows))
        index = int(selector.value)
        return [ordered[index - 1]] if 1 <= index <= len(ordered) else []
    if selector.kind in ("title", "class"):
        pool = selectable_windows(windows)
        matched = _match_needle(pool or windows, selector)
        if matched or not pool:
            return matched
        # 可操作窗口中没有命中：按标题 / 类名回退到全部窗口
        return _match_needle(windows, selector)
    if selector.kind in ("title_re", "class_re", "regex", "process", "fuzzy"):
        return _match_extended(windows, selector)
    return []  # pragma: no cover - SELECTOR_KINDS 已封闭


def _match_extended(windows: Sequence[WindowInfo],
                    selector: WindowSelector) -> list[WindowInfo]:
    """正则 / 进程名 / 模糊匹配（优先可操作窗口，无命中回退全部窗口）。"""
    pool = selectable_windows(windows)
    matched = _extended_filter(pool or windows, selector)
    if matched or not pool:
        return matched
    return _extended_filter(windows, selector)


def _extended_filter(windows: Sequence[WindowInfo],
                     selector: WindowSelector) -> list[WindowInfo]:
    ordered = sort_by_z(windows)
    if selector.kind == "process":
        needle = _normalize_process_name(str(selector.value))
        return [item for item in ordered
                if _normalize_process_name(item.process_name) == needle
                or needle in _normalize_process_name(item.process_name)]
    if selector.kind == "fuzzy":
        return _fuzzy_filter(ordered, str(selector.value))
    pattern = _compile_pattern(str(selector.value))
    if selector.kind == "title_re":
        return [item for item in ordered if pattern.search(item.title)]
    if selector.kind == "class_re":
        return [item for item in ordered if pattern.search(item.class_name)]
    return [item for item in ordered
            if pattern.search(item.title) or pattern.search(item.class_name)]


#: 模糊匹配的最低相似度（0..1）；低于该值不算命中
FUZZY_THRESHOLD = 0.6


def _fuzzy_filter(windows: Sequence[WindowInfo], needle: str) -> list[WindowInfo]:
    """先按子串匹配标题 / 类名 / 进程名，未命中再按相似度（``FUZZY_THRESHOLD``）。"""
    text = needle.strip().lower()
    exact = [item for item in windows
             if text in item.title.lower() or text in item.class_name.lower()
             or text in item.process_name.lower()]
    if exact:
        return exact
    scored: list[tuple[float, WindowInfo]] = []
    for item in windows:
        best = max(
            difflib.SequenceMatcher(None, text, item.title.lower()).ratio(),
            difflib.SequenceMatcher(None, text, item.class_name.lower()).ratio(),
            difflib.SequenceMatcher(None, text, item.process_name.lower()).ratio(),
        )
        if best >= FUZZY_THRESHOLD:
            scored.append((best, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored]


def _normalize_process_name(name: str) -> str:
    """归一化进程名：小写、去掉路径与 ``.exe`` 后缀。"""
    text = str(name or "").strip().lower().replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def _match_needle(windows: Sequence[WindowInfo],
                  selector: WindowSelector) -> list[WindowInfo]:
    """按标题（``title``）或类名（``class``）子串匹配；标题未命中时用类名兜底。"""
    needle = str(selector.value).strip().lower()
    if selector.kind == "class":
        return [item for item in sort_by_z(windows)
                if needle in item.class_name.lower()]
    matched = [item for item in sort_by_z(windows) if needle in item.title.lower()]
    if matched:
        return matched
    # 标题匹配不到时按类名兜底（模型常把窗口类名当标题用）
    return [item for item in sort_by_z(windows)
            if needle in item.class_name.lower()]


def _match_popup(windows: Sequence[WindowInfo]) -> list[WindowInfo]:
    """匹配浮层 / 菜单类窗口：无标题窗口优先，且非主窗口优先，按 Z 序最靠前者优先。

    调用方传入的已是**可操作窗口**（可见且未最小化），因此不会命中隐藏的
    辅助窗口（那类窗口截图为全黑、输入也无效）。
    """
    ordered = sort_by_z(windows)
    untitled = [item for item in ordered if not item.title.strip()]
    if not untitled:
        return []
    non_main = [item for item in untitled if not item.main]
    return non_main or untitled


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
    """把窗口清单压缩为一行提示文本（错误信息与结果附注用）。

    条目中的 ``#N`` 是**可操作窗口**（可见且未最小化）的 Z 序序号，与
    ``#N`` 选择器一致；隐藏 / 最小化窗口显示 ``#-``（不会被 ``#N`` 选中）。
    """
    items = sort_by_z(windows)
    if not items:
        return "（无）"
    indices = selectable_index(items)
    shown = "; ".join(
        item.summary(indices.get(item.handle, NO_SELECTABLE_INDEX))
        for item in items[:limit]
    )
    if len(items) > limit:
        shown += f"; …（共 {len(items)} 个窗口）"
    return shown


def indexed_summary(windows: Sequence[WindowInfo],
                    target: WindowInfo) -> str:
    """按 ``#N`` 选择器语义生成某个窗口的一行摘要（截图结果回填用）。

    窗口摘要的 ``#N`` 必须与 ``#N`` 选择器语义一致，否则模型照抄摘要里的
    编号会选不中窗口：

      - ``WindowInfo.summary()`` 在拿不到上下文时按**枚举序**编号
        （``order + 1``，即清单里的 ``index``）；
      - ``#N`` 选择器按**可操作窗口**（可见且未最小化）的 Z 序编号
        （即清单里的 ``z_index``）。

    两者在存在隐藏 / 最小化窗口时并不相同（例如枚举序 ``#33`` 的可操作序号
    其实是 ``#1``）。本函数是「截图结果回填 ``window_summary``」的统一入口，
    保证摘要编号与选择器一致；同时供 ``op=windows`` 之外的调用方复用。
    """
    indices = selectable_index(windows)
    return target.summary(indices.get(target.handle, NO_SELECTABLE_INDEX))


def window_geometry(windows: Sequence[WindowInfo],
                    target: WindowInfo) -> dict:
    """描述某个窗口的几何（截图坐标系换算用）。

    返回字段：

      - ``z_index``：目标窗口在可操作窗口中的 Z 序序号（``#N`` 选择器语义，
        不可选时为 ``None``）；
      - ``handle`` / ``handle_hex``：窗口句柄；
      - ``rect``：窗口外框的屏幕矩形 ``{x, y, width, height}``（与
        ``op=windows`` 条目里的 ``x`` / ``y`` / ``width`` / ``height`` 同源，
        可直接喂给 ``window_action`` 的 ``move`` / ``fit``）；
      - ``selectable``：该窗口当前能否被 ``#N`` / ``popup`` / ``dialog`` 选中。

    各平台截图后端据此回填截图结果里的窗口几何，使模型能把截图坐标与
    屏幕坐标对齐（``screen = rect.x + 截图像素``）。
    """
    indices = selectable_index(windows)
    return {
        "z_index": indices.get(target.handle) or None,
        "handle": target.handle,
        "handle_hex": target.handle_hex,
        "selectable": is_selectable(target),
        "rect": {
            "x": target.left,
            "y": target.top,
            "width": target.width,
            "height": target.height,
        },
    }


def describe_windows(windows: Sequence[WindowInfo],
                    limit: int = DEFAULT_LIST_LIMIT) -> list[dict]:
    """把窗口清单转为可序列化列表（按 Z 序，``limit`` 截断）。

    每个条目的 ``z_index`` 是它在可操作窗口中的 Z 序序号（``#N`` 选择器用的
    就是这个序号），``selectable`` 标记该窗口当前能否被选中 / 注入。
    """
    items = sort_by_z(windows)
    indices = selectable_index(items)
    return [item.to_dict(indices.get(item.handle, NO_SELECTABLE_INDEX))
            for item in items[:max(int(limit), 0)]]


# ── 窗口控制（激活 / 最大化 / 移动 / 缩放 / 关闭） ───────

#: 窗口控制动作：改变窗口状态或几何，便于把坐标与布局固定下来后再操作
WINDOW_CONTROL_ACTIONS: tuple[str, ...] = (
    "activate", "maximize", "minimize", "restore", "close", "move", "resize", "fit",
    "always_on_top", "not_on_top",
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
    # 置顶 / 取消置顶：让被操作窗口保持在其他窗口之上（不被遮挡）
    "always_on_top": "always_on_top", "ontop": "always_on_top",
    "on_top": "always_on_top", "topmost": "always_on_top",
    "pin": "always_on_top", "keep_on_top": "always_on_top",
    "not_on_top": "not_on_top", "no_topmost": "not_on_top",
    "untop": "not_on_top", "unpin": "not_on_top",
    "normal_level": "not_on_top", "remove_topmost": "not_on_top",
    # 工具层扩展动作（几何记忆；见 WINDOW_MEMORY_ACTIONS）
    "get_geometry": "get_geometry", "geometry_info": "get_geometry",
    "current_geometry": "get_geometry",
    "save_geometry": "save_geometry", "remember_geometry": "save_geometry",
    "store_geometry": "save_geometry", "save_bounds": "save_geometry",
    "restore_geometry": "restore_geometry", "reset_geometry": "restore_geometry",
    "restore_bounds": "restore_geometry",
}

#: 需要 ``x`` / ``y`` 的动作
_NEED_POSITION: frozenset[str] = frozenset({"move", "fit"})
#: 需要 ``width`` / ``height`` 的动作
_NEED_SIZE: frozenset[str] = frozenset({"resize", "fit"})

#: 工具层扩展的窗口动作（不属平台后端契约，由 ``bash_opt`` 工具层用
#: 「读取几何 + fit」组合实现）：读取当前几何 / 记住几何 / 恢复几何。
#: 抽成独立常量便于工具层与测试共用，新增同类动作只需在此登记。
WINDOW_MEMORY_ACTIONS: tuple[str, ...] = (
    "get_geometry", "save_geometry", "restore_geometry",
)


def normalize_control_action(action: Any) -> str | None:
    """把窗口动作文本规范化为标准动作名（未知返回 ``None``）。

    大小写不敏感，接受别名（``topmost`` → ``always_on_top``、``pin`` →
    ``always_on_top``、``remember_geometry`` → ``save_geometry`` 等）。
    """
    text = str(action or "").strip().lower().replace("-", "_")
    if not text:
        return None
    return _CONTROL_ALIASES.get(text)


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
    "FUZZY_THRESHOLD",
    "NO_SELECTABLE_INDEX",
    "SELECTOR_KINDS",
    "WINDOW_CONTROL_ACTIONS",
    "WINDOW_MEMORY_ACTIONS",
    "SelectorError",
    "WindowControlRequest",
    "WindowInfo",
    "WindowSelector",
    "describe_windows",
    "filter_windows",
    "indexed_summary",
    "is_selectable",
    "main_rank",
    "main_window",
    "mark_main",
    "normalize_control_action",
    "parse_control_request",
    "parse_selector",
    "pick_window",
    "require_capturable",
    "selectable_index",
    "selectable_windows",
    "sort_by_z",
    "window_geometry",
    "window_hint",
]
