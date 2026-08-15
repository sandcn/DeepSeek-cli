"""ConfirmInput / Toggle — y/n 确认与开关控件（React Ink 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——确认
输入与开关控件独立成模块（公共辅助经 ``_interactive_common`` 共享）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_input, use_ref
from ..widgets.layout import Row
from ._interactive_common import (
    _call,
)


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
    # ★ P3（review）：yesKeys/noKeys 容器归一化 + 守卫——修复前直接
    #   ``ch in yes_keys``：传 str 时是**子串匹配**（"y" in "yn" 恒 True，
    #   误触发确认）；传 int 等不可迭代时抛 TypeError。统一 ``tuple()`` 化
    #   （str 按字符序列处理——``yesKeys="yn"`` → ``("y","n")``）；不可迭代
    #   输入回退默认值。
    yes_keys = props.get("yesKeys", ("y", "Y"))
    no_keys = props.get("noKeys", ("n", "N"))
    if not hasattr(yes_keys, "__iter__"):
        yes_keys = ("y", "Y")
    else:
        yes_keys = tuple(yes_keys)
    if not hasattr(no_keys, "__iter__"):
        no_keys = ("n", "N")
    else:
        no_keys = tuple(no_keys)
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


# ═══════════════════════════════════════════════════════════
# Toggle — 开关控件
# ═══════════════════════════════════════════════════════════

#: Toggle 默认指示符（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_TOGGLE_CHECKED = "\u25cf "
_TOGGLE_UNCHECKED = "\u25cb "


def Toggle(props: dict) -> Element:
    """React Ink ``<Toggle>`` 等价物（ink-toggle 风格）：开关控件。

    Props:
        value: 受控开关值（默认 False）。
        onChange: ``(value: bool) -> None``——切换时回调。
        focus: 是否参与输入路由（默认 True）。
        label: 标签文本（可选；无标签时仅渲染指示符）。
        checkedPrefix/uncheckedPrefix: 选中/未选中指示符
            （默认 ``"● "`` / ``"○ "``）。
        checkedStyle: 选中态样式（默认 ``Style(fg=6)`` cyan）。
        style: 基础样式（与 checkedStyle 合并——未选中态使用）。
        labelStyle: 标签样式（默认 None）。

    行为（与 ink-toggle 对齐）：
      - space（或空格 char）/ enter 切换开关值并触发 ``onChange``；
      - 其余按键放行（不消费）。

    Returns:
        BOX 元素（横向：指示符 + 标签）。
    """
    value = bool(props.get("value", False))
    onChange = props.get("onChange")
    focus = bool(props.get("focus", True))
    label = props.get("label")
    label = None if label is None else str(label)
    checked_prefix = str(props.get("checkedPrefix", _TOGGLE_CHECKED))
    unchecked_prefix = str(props.get("uncheckedPrefix", _TOGGLE_UNCHECKED))
    # ★ P3（review）：显式空 ``Style()`` 不被 ``or`` 覆盖——修复前
    #   ``props.get("checkedStyle") or Style(fg=6)`` 把显式 ``Style()``（空
    #   样式，可能为受控样式合并预留）当 falsy 替换为默认 cyan。改为
    #   ``is not None`` 判断：仅未提供时回退默认。
    checked_style = props.get("checkedStyle")
    if checked_style is None:
        checked_style = Style(fg=6)
    base_style = props.get("style")
    label_style = props.get("labelStyle")
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 value——同一渲染
    #   批次内连续 space 事件之间无重渲染，闭包 value 陈旧会反复提交旧值。
    value_ref = use_ref(value)
    value_ref.current = value

    def _handle(event) -> bool:
        if not focus:
            return False
        # ★ P3（review）：``event.kind == "space"`` 为死分支（InputParser 从不
        #   产生 kind=="space"，空格为 ``kind=="char", char==" "``）——删除。
        if (event.kind == "char" and event.char == " ") or event.kind == "enter":
            new_value = not value_ref.current
            value_ref.current = new_value
            _call(onChange, new_value)
            return True
        return False

    use_input(_handle, focus)

    indicator = checked_prefix if value else unchecked_prefix
    indicator_style = checked_style if value else base_style
    children = [h(TEXT, {"children": indicator, "style": indicator_style, "height": 1})]
    if label:
        children.append(h(TEXT, {"children": label, "style": label_style, "height": 1}))
    # ★ 阶段2（标准布局容器重构）：row BOX → Row（语义化门面，输出等价）。
    return h(Row, {"height": 1}, children)


__all__ = ["ConfirmInput", "Toggle"]
