"""interactive — React Ink 风格交互控件（SelectInput / TextInput / MultiSelect / ConfirmInput）。

基于 ``use_input`` + ``use_state`` 实现，按键事件结构对齐框架 KeyEvent：
  - kind: "char" | "enter" | "backspace" | "delete" | "arrow_up" | "arrow_down"
          | "arrow_left" | "arrow_right" | "home" | "end" | "escape" | ...
  - char: kind="char" 时的可打印字符（含粘贴整段文本）

与 React Ink 生态控件（ink-select-input / ink-text-input / ink-multi-select /
ink-confirm-input）API 对齐：
  - 控件为函数组件（props 传入 + 内部 state）；
  - 父组件渲染时传入回调（onSelect/onChange/onSubmit/onConfirm），控件经
    ``use_input`` 消费按键并触发回调；
  - ``focus=False`` 时控件不参与输入路由（零行为变化，事件放行旧路径）。

依赖约束：仅依赖 element / output / core.style / _screen / hooks（Layer 0/1），
无父包依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from src.tui._screen import wcswidth_simple
from ..element import BOX, TEXT, Element, h
from ..helpers import _parse_color
from ..hooks import use_state, use_input, use_effect, use_ref

_logger = logging.getLogger(__name__)

__all__ = ["SelectInput", "TextInput", "MultiSelect", "ConfirmInput"]


# ═══════════════════════════════════════════════════════════
# 公共辅助
# ═══════════════════════════════════════════════════════════


def _call(fn, *args) -> None:
    """安全调用可选回调（异常仅记录日志，不阻断输入分发）。"""
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        _logger.debug("控件回调异常", exc_info=True)


def _color(value, default: int = 6) -> int | None:
    """解析颜色 shorthand（颜色名/int）为 256 色号；解析失败回退 default。"""
    if value is None:
        return default
    parsed = _parse_color(value)
    return parsed if parsed is not None else default


def _normalize_items(items) -> list[dict]:
    """将 items 规范化为 ``{"label": str, "value": Any}`` 列表。

    支持两种输入形态：
      - list of str（label == value）；
      - list of dict（含 "label" 键；缺省回退 "value"）。
    """
    out: list[dict] = []
    for item in items or []:
        if isinstance(item, dict):
            label = str(item.get("label", item.get("value", "")))
            out.append({"label": label, "value": item.get("value", label)})
        else:
            out.append({"label": str(item), "value": item})
    return out


def _visible_window(selected: int, total: int, limit: int | None) -> tuple[int, int]:
    """计算可见窗口 ``(offset, count)``（limit 无/超界时返回全量）。"""
    if limit is None or total <= limit:
        return 0, total
    offset = max(0, min(selected, total - limit))
    return offset, limit


# ═══════════════════════════════════════════════════════════
# SelectInput — 单选列表
# ═══════════════════════════════════════════════════════════


def SelectInput(props: dict) -> Element:
    """React Ink ``<SelectInput>`` 等价物：单选列表选择控件。

    Props:
        items: list[str] 或 list[{"label": str, "value": Any}]。
        onSelect: ``(item) -> None``——Enter 确认时回调（item 为
            ``{"label", "value"}`` dict）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始选中下标（默认 0）。
        limit: 可见 item 数（超出滚动窗口；默认 None 全部显示）。
        highlightStyle: 选中行样式（默认 ``Style(fg=6)`` cyan）。
        prefix: 选中行前缀（默认 ``"> "``）；未选中行用同宽空格对齐。

    Returns:
        BOX 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    on_select = props.get("onSelect")
    focus = bool(props.get("focus", True))
    limit = props.get("limit")
    if limit is not None:
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = None
    initial_index = 0
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError):
        initial_index = 0
    if items:
        initial_index = min(initial_index, len(items) - 1)
    highlight_style = props.get("highlightStyle") or Style(fg=6)
    prefix = str(props.get("prefix", "> "))

    selected, set_selected = use_state(initial_index)
    # ★ ref 镜像（同批连续按键修复）：handler 闭包捕获渲染期 state——同一渲染
    #   批次内多个事件（如按住 ↑/↓）之间无重渲染，闭包 state 陈旧。用 ref
    #   保存最新值（渲染期同步 + 事件期即时更新），handler 一律读 ref。
    selected_ref = use_ref(selected)
    selected_ref.current = selected

    def _handle(event) -> bool:
        if not focus or not items:
            return False
        cur = selected_ref.current
        if event.kind == "arrow_up":
            if cur > 0:
                new = cur - 1
                selected_ref.current = new
                set_selected(new)
            return True
        if event.kind == "arrow_down":
            if cur < len(items) - 1:
                new = cur + 1
                selected_ref.current = new
                set_selected(new)
            return True
        if event.kind == "enter":
            _call(on_select, items[selected_ref.current])
            return True
        return False

    use_input(_handle, focus)

    offset, count = _visible_window(selected, len(items), limit)
    rows = []
    pad = " " * (wcswidth_simple(prefix) if prefix else 2)
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_sel = idx == selected
        line_prefix = prefix if is_sel else pad
        style = highlight_style if is_sel else None
        rows.append(
            h(TEXT, {"children": line_prefix + item["label"], "style": style, "key": f"item-{idx}"})
        )
    return h(BOX, {"flexDirection": "column"}, rows)


# ═══════════════════════════════════════════════════════════
# TextInput — 单行文本输入（受控）
# ═══════════════════════════════════════════════════════════


def TextInput(props: dict) -> Element:
    """React Ink ``<TextInput>`` 等价物：单行文本输入控件（受控）。

    Props:
        value: 当前文本（受控；父组件经 onChange 回调更新）。
        onChange: ``(value: str) -> None``——文本变化时回调。
        onSubmit: ``(value: str) -> None``——Enter 提交时回调。
        focus: 是否参与输入路由（默认 True）。
        placeholder: 空值占位文本（dim 显示）。
        mask: 掩码字符（如 ``"*"``）——密码模式隐藏真实文本。
        showCursor: 是否显示光标（默认 True）。
        cursorColor: 光标反显色（颜色名/int，默认 ``"cyan"``）。

    行为（与 ink-text-input 对齐）：
      - 可打印字符插入光标位置；backspace/delete 删除前后字符；
      - left/right 移动光标；home/end 跳到行首/行尾；
      - enter 触发 ``onSubmit(value)`` 并消费事件。

    Returns:
        BOX 元素（文本 + 光标，横向排列）。
    """
    value = str(props.get("value", ""))
    onChange = props.get("onChange")
    onSubmit = props.get("onSubmit")
    focus = bool(props.get("focus", True))
    placeholder = str(props.get("placeholder", ""))
    mask = props.get("mask")
    show_cursor = bool(props.get("showCursor", True))
    cursor_color = _color(props.get("cursorColor", "cyan"))

    # 内部文本缓冲（React Ink ink-text-input 半受控语义）：按键先更新内部
    # state（即使父组件未立即重渲染也能累积输入），外部受控 value 变化时
    # 再同步覆盖内部缓冲。
    text, set_text = use_state(value)
    cursor, set_cursor = use_state(len(value))
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 state——
    # 同一渲染批次内多个 char/backspace 事件之间无重渲染，闭包 text/cursor
    # 陈旧会导致逐字符输入只保留最后一个字符。
    text_ref = use_ref(text)
    cursor_ref = use_ref(cursor)
    text_ref.current = text
    cursor_ref.current = cursor

    # 外部受控值变化 → 同步内部缓冲（deps 仅 value——受控覆盖）
    def _sync_external():
        set_text(value)
        set_cursor(max(0, min(cursor_ref.current, len(value))))

    use_effect(_sync_external, (value,))

    def _handle(event) -> bool:
        if not focus:
            return False
        cur_text = text_ref.current
        cur_cursor = max(0, min(cursor_ref.current, len(cur_text)))
        if event.kind == "char":
            ch = event.char
            if not ch:
                return False
            if "\n" in ch or "\r" in ch:
                return False  # 换行放行（多行场景由宿主处理）
            new_text = cur_text[:cur_cursor] + ch + cur_text[cur_cursor:]
            text_ref.current = new_text
            cursor_ref.current = cur_cursor + len(ch)
            set_text(new_text)
            set_cursor(cursor_ref.current)
            _call(onChange, new_text)
            return True
        if event.kind == "backspace":
            if cur_cursor > 0:
                new_text = cur_text[:cur_cursor - 1] + cur_text[cur_cursor:]
                text_ref.current = new_text
                cursor_ref.current = cur_cursor - 1
                set_text(new_text)
                set_cursor(cursor_ref.current)
                _call(onChange, new_text)
            return True
        if event.kind == "delete":
            if cur_cursor < len(cur_text):
                new_text = cur_text[:cur_cursor] + cur_text[cur_cursor + 1:]
                text_ref.current = new_text
                set_text(new_text)
                _call(onChange, new_text)
            return True
        if event.kind == "arrow_left":
            cursor_ref.current = max(0, cur_cursor - 1)
            set_cursor(cursor_ref.current)
            return True
        if event.kind == "arrow_right":
            cursor_ref.current = min(len(cur_text), cur_cursor + 1)
            set_cursor(cursor_ref.current)
            return True
        if event.kind == "home":
            cursor_ref.current = 0
            set_cursor(0)
            return True
        if event.kind == "end":
            cursor_ref.current = len(cur_text)
            set_cursor(len(cur_text))
            return True
        if event.kind == "enter":
            _call(onSubmit, cur_text)
            return True
        return False

    use_input(_handle, focus)

    display = (mask * len(text)) if mask else text
    eff = max(0, min(cursor, len(text)))
    if not display:
        if placeholder:
            return h(TEXT, {"children": placeholder, "dim": True, "style": Style(fg=244)})
        return h(TEXT, {"children": " ", "height": 1})
    before = display[:eff]
    after = display[eff:]
    if not show_cursor:
        return h(TEXT, {"children": display})
    cursor_ch = " " if eff >= len(display) else display[eff]
    cursor_style = Style(bg=cursor_color)
    return h(BOX, {"flexDirection": "row", "height": 1}, [
        h(TEXT, {"children": before}),
        h(TEXT, {"children": cursor_ch, "style": cursor_style}),
        h(TEXT, {"children": after}),
    ])


# ═══════════════════════════════════════════════════════════
# MultiSelect — 多选列表
# ═══════════════════════════════════════════════════════════

#: 选中/未选中指示符（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25cf "   # ●
_UNCHECKED = "\u25cb "  # ○


def MultiSelect(props: dict) -> Element:
    """React Ink ``<MultiSelect>`` 等价物：多选列表控件。

    Props:
        items: list[str] 或 list[{"label": str, "value": Any}]。
        onSubmit: ``(selected: list) -> None``——Enter 确认时回调（选中的
            values 列表，保持 items 顺序）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始光标下标（默认 0）。
        initialValues: 初始选中的 value 列表（默认 []）。
        limit: 可见 item 数（超出滚动窗口；默认 None 全部显示）。
        highlightStyle: 光标行样式（默认 ``Style(fg=6)`` cyan）。
        checkedPrefix/uncheckedPrefix: 选中/未选中指示符
            （默认 ``"● "`` / ``"○ "``）。

    行为：
      - up/down 移动光标；space（或空格 char）切换当前项选中；
      - enter 触发 ``onSubmit(selected_values)`` 并消费事件。

    Returns:
        BOX 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    onSubmit = props.get("onSubmit")
    focus = bool(props.get("focus", True))
    limit = props.get("limit")
    if limit is not None:
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = None
    initial_index = 0
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError):
        initial_index = 0
    if items:
        initial_index = min(initial_index, len(items) - 1)
    highlight_style = props.get("highlightStyle") or Style(fg=6)
    checked_prefix = str(props.get("checkedPrefix", _CHECKED))
    unchecked_prefix = str(props.get("uncheckedPrefix", _UNCHECKED))

    cursor_idx, set_cursor_idx = use_state(initial_index)
    initial_values = props.get("initialValues", [])
    if not isinstance(initial_values, (list, tuple, set)):
        initial_values = []
    selected, set_selected = use_state(set(initial_values))
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 state。
    cursor_ref = use_ref(cursor_idx)
    selected_ref = use_ref(selected)
    cursor_ref.current = cursor_idx
    selected_ref.current = selected

    def _handle(event) -> bool:
        if not focus or not items:
            return False
        cur_cursor = cursor_ref.current
        cur_selected = selected_ref.current
        if event.kind == "arrow_up":
            if cur_cursor > 0:
                new = cur_cursor - 1
                cursor_ref.current = new
                set_cursor_idx(new)
            return True
        if event.kind == "arrow_down":
            if cur_cursor < len(items) - 1:
                new = cur_cursor + 1
                cursor_ref.current = new
                set_cursor_idx(new)
            return True
        if event.kind == "space" or (event.kind == "char" and event.char == " "):
            value = items[cur_cursor]["value"]
            new_selected = set(cur_selected)
            if value in new_selected:
                new_selected.discard(value)
            else:
                new_selected.add(value)
            selected_ref.current = new_selected
            set_selected(new_selected)
            return True
        if event.kind == "enter":
            ordered = [item["value"] for item in items if item["value"] in selected_ref.current]
            _call(onSubmit, ordered)
            return True
        return False

    use_input(_handle, focus)

    offset, count = _visible_window(cursor_idx, len(items), limit)
    rows = []
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_cursor = idx == cursor_idx
        is_checked = item["value"] in selected
        indicator = checked_prefix if is_checked else unchecked_prefix
        style = highlight_style if is_cursor else None
        rows.append(
            h(TEXT, {"children": indicator + item["label"], "style": style, "key": f"item-{idx}"})
        )
    return h(BOX, {"flexDirection": "column"}, rows)


# ═══════════════════════════════════════════════════════════
# ConfirmInput — y/n 确认输入
# ═══════════════════════════════════════════════════════════


def ConfirmInput(props: dict) -> Element:
    """React Ink ``<ConfirmInput>`` 等价物：y/n 确认输入控件。

    Props:
        onConfirm: ``(value: bool) -> None``——确认回调（True=y，False=n）。
        focus: 是否参与输入路由（默认 True）。
        yesKeys: 确认键集合（默认 ``("y", "Y")``）。
        noKeys: 否定键集合（默认 ``("n", "N")``）。
        label: 提示文本（默认 ``"(y/n)"``；可传自定义提示）。
        labelStyle: 提示样式（默认 None）。

    行为（与 ink-confirm-input 对齐）：
      - y/Y → ``onConfirm(True)``；n/N → ``onConfirm(False)``；
      - enter → ``onConfirm(True)``（默认确认）；escape → ``onConfirm(False)``。

    Returns:
        TEXT 元素（提示标签）。
    """
    onConfirm = props.get("onConfirm")
    focus = bool(props.get("focus", True))
    yes_keys = props.get("yesKeys", ("y", "Y"))
    no_keys = props.get("noKeys", ("n", "N"))
    label = str(props.get("label", "(y/n)"))
    label_style = props.get("labelStyle")

    def _handle(event) -> bool:
        if not focus:
            return False
        if event.kind == "char":
            ch = event.char
            if ch in yes_keys:
                _call(onConfirm, True)
                return True
            if ch in no_keys:
                _call(onConfirm, False)
                return True
            return False
        if event.kind == "enter":
            _call(onConfirm, True)
            return True
        if event.kind == "escape":
            _call(onConfirm, False)
            return True
        return False

    use_input(_handle, focus)

    if label_style is not None:
        return h(TEXT, {"children": label, "style": label_style})
    return h(TEXT, {"children": label})
