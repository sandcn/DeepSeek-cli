"""TextInput — 单行文本输入控件（受控，React Ink ink-text-input 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——文本
输入独立成模块（公共辅助经 ``_interactive_common`` 共享）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_effect, use_ref
from ..widgets.layout import Row
from ._interactive_common import (
    _call,
    _color,
)


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
    # ★ P3（review 2026-08-22）语义说明：外部 value 变化时保持光标（钳制到
    #   新长度）而非跳尾——用户输入路径依赖此行为（父组件经 onChange 回传
    #   value 时，value 变化触发此处但光标须保留编辑位置）；若用于「预填」场景
    #   需外部注入时显式置光标，属设计取舍（框架未区分来源）。
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
            # ★ P3（review）：光标在 0 时无操作——返回 False（不消费，放行
            #   父级；与 ListView 边界修复对齐）。
            if cur_cursor <= 0:
                return False
            new_text = cur_text[:cur_cursor - 1] + cur_text[cur_cursor:]
            text_ref.current = new_text
            cursor_ref.current = cur_cursor - 1
            set_text(new_text)
            set_cursor(cursor_ref.current)
            _call(onChange, new_text)
            return True
        if event.kind == "delete":
            # ★ P3（review）：光标在末尾时无操作——返回 False（不消费）。
            # ★ P3（review）：带修饰键的 delete（modifier>=2，如 Shift/Alt/
            #   Ctrl+Delete）不按单字符删除处理——放行（组合键由上层消费）。
            if event.modifier >= 2:
                return False
            if cur_cursor >= len(cur_text):
                return False
            new_text = cur_text[:cur_cursor] + cur_text[cur_cursor + 1:]
            text_ref.current = new_text
            set_text(new_text)
            _call(onChange, new_text)
            return True
        if event.kind == "arrow_left":
            # ★ P3（review）：带修饰键的方向键（modifier>=2，如 Alt+← 词跳转
            #   由 _dispatch_key_event 消费）不按单格移动处理——放行。
            if event.modifier >= 2:
                return False
            cursor_ref.current = max(0, cur_cursor - 1)
            set_cursor(cursor_ref.current)
            return True
        if event.kind == "arrow_right":
            if event.modifier >= 2:
                return False
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
    # ★ P2-5（review）：mask 长度 >1 时 eff 是**原文**索引，display 长度 =
    #   len(mask) * len(text)——直接用 eff 切片会落在错误字符边界（如 mask="ab"
    #   text="xy" cursor=1 → display[:1]="a" 而非 "ab"）。按 ``eff * len(mask)``
    #   换算显示索引后再切片。
    if mask:
        disp_eff = eff * len(mask)
    else:
        disp_eff = eff
    before = display[:disp_eff]
    in_cursor = disp_eff < len(display)
    after = display[disp_eff + 1:] if in_cursor else display[disp_eff:]
    if not show_cursor:
        return h(TEXT, {"children": display})
    cursor_ch = " " if not in_cursor else display[disp_eff]
    cursor_style = Style(bg=cursor_color)
    # ★ P3（review E10）：光标列对齐由 row 布局按显示宽度累加保证——``before``
    #   含 CJK/emoji（宽字符）时其**显示宽度** ≠ 字符数，row 布局子节点按
    #   显示宽度（wcswidth）推进，光标块自动落在正确列（无需显式 spacer）。
    #   光标字符本身占宽字符全宽（2 列），视觉与 React Ink 光标覆盖单字符
    #   语义一致。
    # ★ 阶段2（标准布局容器重构）：row BOX → Row（语义化门面，输出等价）。
    return h(Row, {"height": 1}, [
        h(TEXT, {"children": before}),
        h(TEXT, {"children": cursor_ch, "style": cursor_style}),
        h(TEXT, {"children": after}),
    ])


__all__ = ["TextInput"]
