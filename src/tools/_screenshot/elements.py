"""窗口内控件（子窗口）枚举与匹配（``bash_opt`` 的 ``op=elements`` 实现层）。

作用：把 GUI 窗口里的**控件**（按钮、编辑框、列表、标签……）连同其名称与
几何一起列出来，让模型按「控件名」而不是盲点像素去操作——先用
``op=elements`` 拿到控件中心坐标，再把坐标交给 ``op=click`` / ``op=type``，
或者直接给输入 op 传 ``element='确定'`` 由工具内部换算坐标。

职责划分：

  - 本模块：控件描述（:class:`ElementInfo`）+ 与平台无关的**匹配 / 分类 /
    序列化**规则，以及公共入口 :func:`list_process_elements`；
  - 平台后端（``_screenshot/win.py`` 的 ``list_elements``）：负责产出一致的
    :class:`ElementInfo` 列表（Windows 走 ``EnumChildWindows``）。

坐标系：:class:`ElementInfo` 给出的是**屏幕坐标**矩形；工具层再用输入后端的
窗口 frame（与 ``op=screenshot`` 产物同一坐标系）换算为「窗口内坐标」，
因此截图、输入、控件三种坐标始终一致。

平台能力差异：经典 Win32 控件（对话框、记事本、老式工具）能被完整枚举；
Chrome / Electron / Qt / 游戏等**自绘界面**内部没有标准子窗口，枚举结果会
很少或为空——此时仍应回到「截图 + 像素坐标」的方式操作（如实提示，不假装
识别到了控件）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from .result import ScreenshotError

logger = logging.getLogger(__name__)

#: 单次枚举返回的控件条数上限（防止复杂界面输出过长）
DEFAULT_ELEMENT_LIMIT = 200

#: 控件类名特征 → 可读类型（按顺序匹配，先命中者为准）
_CONTROL_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("edit", "edit"),
    ("richedit", "edit"),
    ("combobox", "combobox"),
    ("comboboxex", "combobox"),
    ("listbox", "list"),
    ("syslistview", "list"),
    ("systreeview", "tree"),
    ("treeview", "tree"),
    ("button", "button"),
    ("checkbox", "checkbox"),
    ("radiobutton", "radio"),
    ("static", "text"),
    ("toolbar", "toolbar"),
    ("toolbarwindow", "toolbar"),
    ("msctls_statusbar", "statusbar"),
    ("msctls_trackbar", "slider"),
    ("msctls_updown", "spinner"),
    ("msctls_progress", "progress"),
    ("tab", "tab"),
    ("systabcontrol", "tab"),
    ("scrollbar", "scrollbar"),
    ("menu", "menu"),
    ("#32770", "dialog"),
    ("internet explorer_server", "webview"),
    ("chrome_widgetwin", "webview"),
    ("chrome_renderwidgethost", "webview"),
)

#: 无文本控件的默认展示名（按类型）
_TYPE_LABELS: dict[str, str] = {
    "edit": "编辑框",
    "combobox": "下拉框",
    "list": "列表",
    "tree": "树",
    "button": "按钮",
    "checkbox": "复选框",
    "radio": "单选框",
    "text": "文本",
    "toolbar": "工具栏",
    "statusbar": "状态栏",
    "slider": "滑块",
    "spinner": "微调",
    "progress": "进度条",
    "tab": "标签页",
    "scrollbar": "滚动条",
    "menu": "菜单",
    "dialog": "对话框",
    "webview": "网页视图",
    "window": "窗口",
}


@dataclass(frozen=True)
class ElementInfo:
    """一个窗口内控件（跨平台统一描述，坐标为屏幕像素）。"""

    handle: int
    pid: int
    class_name: str
    text: str
    left: int
    top: int
    width: int
    height: int
    enabled: bool = True
    visible: bool = True
    depth: int = 0

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center_x(self) -> int:
        """屏幕坐标下的控件中心 X。"""
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        """屏幕坐标下的控件中心 Y。"""
        return self.top + self.height // 2

    @property
    def control_type(self) -> str:
        """由类名推断的可读控件类型（无法识别时为 ``window``）。"""
        return classify_control(self.class_name)

    @property
    def label(self) -> str:
        """控件展示名（文本优先，无文本时用类型名）。"""
        text = self.text.strip()
        if text:
            return text
        return _TYPE_LABELS.get(self.control_type, "控件")

    @property
    def handle_hex(self) -> str:
        return f"0x{self.handle:X}" if self.handle else "0x0"

    def summary(self) -> str:
        return (f"「{self.label}」class={self.class_name} "
                f"{self.width}x{self.height}@({self.left},{self.top})"
                + ("" if self.enabled else " [disabled]")
                + ("" if self.visible else " [hidden]"))


class ElementError(ScreenshotError):
    """控件枚举 / 匹配失败（无窗口、平台不支持、无匹配控件）。"""


def classify_control(class_name: str) -> str:
    """由窗口类名推断可读控件类型（识别不出时返回 ``window``）。"""
    lowered = str(class_name or "").strip().lower()
    if not lowered:
        return "window"
    for hint, kind in _CONTROL_TYPE_HINTS:
        if hint in lowered:
            return kind
    return "window"


def list_process_elements(pid: int, window: str | None = None) -> list[ElementInfo]:
    """返回目标窗口内的控件列表（平台不支持时返回空列表）。

    Args:
        pid: 目标进程 PID（含子进程一起参与窗口匹配）。
        window: 窗口选择器（见 ``windows`` 模块）；``None`` = 主窗口。

    Raises:
        SelectorError: 选择器非法或没有匹配窗口（由平台后端抛出）。
        ElementError: 枚举过程失败。
    """
    from . import resolve_backend
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return []
    backend = resolve_backend()
    enumerate_fn = getattr(backend, "list_elements", None)
    if enumerate_fn is None:
        return []
    try:
        return list(enumerate_fn(pid, window))
    except ScreenshotError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:  # pragma: no cover - 依赖系统调用
        raise ElementError(f"枚举窗口控件失败（进程 {pid}）: {exc}") from exc


def filter_elements(elements: Sequence[ElementInfo],
                    needle: str | None = None) -> list[ElementInfo]:
    """按控件名 / 类名 / 类型过滤（``needle`` 为空时原样返回）。

    匹配顺序：控件文本 → 类名 → 控件类型（中英文皆可，如 ``edit`` / ``编辑框``、
    ``button`` / ``按钮``）——与窗口选择器 ``title:`` / ``class:`` 的兜底策略
    一致，模型可以直接用清单里看到的 ``label`` / ``type`` 去筛选。
    """
    items = list(elements)
    text = str(needle or "").strip()
    if not text:
        return items
    lowered = text.lower()
    matched = [item for item in items if lowered in item.text.lower()]
    if matched:
        return matched
    matched = [item for item in items if lowered in item.class_name.lower()]
    if matched:
        return matched
    return _by_type(items, text)


def _by_type(elements: Sequence[ElementInfo], needle: str) -> list[ElementInfo]:
    """按控件类型匹配（接受类型名 ``edit`` 与中文标签 ``编辑框``）。"""
    lowered = needle.strip().lower()
    matched: list[ElementInfo] = []
    for item in elements:
        kind = item.control_type
        label = _TYPE_LABELS.get(kind, "")
        if lowered in kind or (label and lowered in label):
            matched.append(item)
    return matched


def match_element(elements: Sequence[ElementInfo], query: str) -> ElementInfo:
    """按查询文本定位一个控件（供输入 op 的 ``element`` 参数使用）。

    支持形式：

      - ``#N``：第 N 个控件（按 ``op=elements`` 的清单顺序，从 1 开始）；
      - ``text:子串`` / ``name:子串``：按控件文本匹配；
      - ``class:子串`` / ``type:子串``：按控件类名或控件类型匹配；
      - 裸字符串：依次按控件文本 → 类名 → 类型匹配（类型支持 ``edit`` /
        ``编辑框``、``button`` / ``按钮`` 这类中英文写法）。

    多个命中时取**面积最大**者（通常是真正可交互的控件，而不是包装它的
    容器），并优先取可用（enabled）且可见的控件。

    Raises:
        ElementError: 查询为空、序号越界，或没有匹配控件。
    """
    items = [item for item in elements]
    text = str(query or "").strip()
    if not text:
        raise ElementError("element 参数不能为空（支持 '#N' / 'text:子串' / 'class:子串' / 裸子串）")
    if text.startswith("#"):
        index = _parse_index(text[1:])
        ordered = list(items)  # 清单顺序（与 op=elements 输出一致）
        if not 1 <= index <= len(ordered):
            raise ElementError(
                f"控件序号 {text} 越界：当前窗口共 {len(ordered)} 个控件"
            )
        return ordered[index - 1]
    prefix, _, rest = text.partition(":")
    if prefix.strip().lower() in ("text", "name", "label") and rest.strip():
        candidates = _by_text(items, rest.strip())
    elif prefix.strip().lower() in ("class", "type") and rest.strip():
        candidates = (_by_class(items, rest.strip())
                      or _by_type(items, rest.strip()))
    else:
        candidates = (_by_text(items, text) or _by_class(items, text)
                      or _by_type(items, text))
    if not candidates:
        return _raise_no_match(items, text)
    return max(candidates, key=_match_rank)


def _by_text(elements: Sequence[ElementInfo], needle: str) -> list[ElementInfo]:
    lowered = needle.lower()
    return [item for item in elements if lowered in item.text.lower()]


def _by_class(elements: Sequence[ElementInfo], needle: str) -> list[ElementInfo]:
    lowered = needle.lower()
    return [item for item in elements if lowered in item.class_name.lower()]


def _match_rank(item: ElementInfo) -> tuple:
    """匹配排序键（越大越优先）：可见 > 可用 > 有文本 > 面积大。"""
    return (
        bool(item.visible),
        bool(item.enabled),
        bool(item.text.strip()),
        item.area,
    )


def _raise_no_match(elements: Sequence[ElementInfo], query: str) -> ElementInfo:
    names = ", ".join(
        f"「{item.label}」({item.class_name})" for item in elements[:8]
    ) or "（无控件）"
    raise ElementError(
        f"没有匹配 {query!r} 的控件。当前窗口控件: {names}"
        + ("；…" if len(elements) > 8 else "")
    )


def _parse_index(text: str) -> int:
    try:
        return int(str(text).strip(), 10)
    except ValueError:
        raise ElementError(f"控件序号必须是整数: {text!r}（如 '#1'）") from None


def describe_elements(elements: Iterable[ElementInfo],
                      limit: int = DEFAULT_ELEMENT_LIMIT) -> list[dict]:
    """把控件列表转为可序列化描述（``limit`` 截断，超限在调用方提示）。"""
    return [item_to_dict(item) for item in list(elements)[:max(int(limit), 0)]]


def item_to_dict(item: ElementInfo) -> dict:
    """单个控件的可序列化描述（屏幕坐标；工具层再附窗口内坐标）。"""
    return {
        "handle": item.handle,
        "handle_hex": item.handle_hex,
        "pid": item.pid,
        "class": item.class_name,
        "text": item.text,
        "label": item.label,
        "type": item.control_type,
        "x": item.left,
        "y": item.top,
        "width": item.width,
        "height": item.height,
        "center_x": item.center_x,
        "center_y": item.center_y,
        "enabled": item.enabled,
        "visible": item.visible,
        "depth": item.depth,
        "summary": item.summary(),
    }


__all__ = [
    "DEFAULT_ELEMENT_LIMIT",
    "ElementError",
    "ElementInfo",
    "classify_control",
    "describe_elements",
    "filter_elements",
    "item_to_dict",
    "list_process_elements",
    "match_element",
]
