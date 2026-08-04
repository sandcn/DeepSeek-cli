"""user_select — React Ink 用户选择弹窗组件（UserSelectPopup）。

React Ink 化（2026-08-05）：user_select 工具的终端交互界面从「命令补全弹窗
（CompletionState + show_completions）+ 手动 raw I/O（select/read_byte）」
迁移为独立的 React Ink 函数组件：

  - 组件在 App 组件树底部区渲染（StatusBar 上方，``visible`` 时占行、
    不可见时零高度不占行）；
  - 交互（↑↓/Enter/Esc/空格）经 ``use_input`` 钩子消费（render 线程驱动
    InputDispatcher 路由），不再手动读取 stdin；
  - 渲染经 ``use_state`` 驱动（高亮/勾选即时重绘），不再直接操作补全弹窗
    私有字段（``bb._completion_idx`` 等）；
  - 结果（done/action/result）写入 ``model.user_select``，工具协程轮询读取。

组件与工具协程通信协议（跨线程安全，GIL 原子字段）：
  - 工具：设置 ``model.user_select``（visible=True, seq+1）→ request_bottom_redraw；
    App 组件以 ``key=seq`` 渲染本组件（seq 变化强制重挂载，重置内部 state）。
  - 组件：use_input handler 更新内部 state（渲染）+ 写 ``us.selected/checked``，
    提交/取消时写 ``us.done/us.action/us.result``。
  - 工具：轮询 ``us.done``（带 deadline 超时），读 result 后清理
    ``model.user_select = UserSelectState()`` 并 request_bottom_redraw。

依赖约束：仅依赖 app 同层（model/_theme/input_area）与 ink 框架（Layer 0/1），
无 tools 层反向依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._screen import wcswidth_simple
from src.tui._input import _wrap_by_width
from src.tui.app.input_area import _desc_column_width, _truncate_width
from src.tui.app._theme import _S_DIM, _S_SEP, _S_TIME
from src.tui.ink import TEXT, h, Column, Row
from src.tui.ink.hooks import use_state, use_input, use_ref

__all__ = ["UserSelectPopup"]

#: 弹窗标题色（亮青加粗，对齐 _S_ACCENT_BOLD）
_S_TITLE = Style(fg=45, bold=True)
#: 高亮行背景色（与补全弹窗 sel_bg 呼吸下限一致）
_S_SEL_BG = Style(bg=237)
#: 多选勾选标记（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25cf "
_UNCHECKED = "\u25cb "


def UserSelectPopup(props) -> object:
    """React Ink 用户选择弹窗组件。

    Props:
        model: AppModel 实例（读 ``model.user_select``）。
        width: 终端宽度（分栏说明模式需要）。

    Returns:
        Column（弹窗行）或空 TEXT（不可见时零高度）。
    """
    model = props["model"]
    width = props.get("width", 80)
    us = getattr(model, "user_select", None)
    visible = bool(
        us is not None and us.visible and not us.done
        and getattr(us, "options", None)
    )

    # ── hooks（无条件调用，保持 fiber hook 顺序稳定） ──
    # 初始值从 model.user_select 读取（App 以 key=seq 强制重挂载，
    # seq 变化 → fiber 重建 → use_state 重新初始化）。
    selected, set_selected = use_state(us.selected if us is not None else 0)
    checked, set_checked = use_state(list(us.checked) if us is not None else [])
    selected_ref = use_ref(selected)
    checked_ref = use_ref(checked)
    selected_ref.current = selected
    checked_ref.current = checked

    # ★ 输入钩子必须无条件注册（hook 顺序稳定）：is_active=visible——
    # 弹窗关闭（visible=False）时 router 不收集本 hook，按键放行旧路径
    # （修复前 use_input 仅在可见分支调用：visible=False 时 InputHook 残留
    # active 在 fiber.hooks，router 仍收集 → 弹窗关闭后所有输入被吞）。
    def _handle(event) -> bool:
        """弹窗按键处理：↑↓ 导航 / Enter 确认 / Esc 取消 / 空格切换（多选）。

        弹窗激活期间消费所有按键（阻断输入框/旧路径副作用）。
        """
        # 弹窗已关闭（工具清理）→ 不再处理
        if us is None or not us.visible or us.done:
            return True
        options_now = getattr(us, "options", None) or []
        total_now = len(options_now)
        multi_now = bool(getattr(us, "multi_select", False))
        cur = selected_ref.current
        if not (0 <= cur < total_now):
            cur = 0
        if event.kind == "arrow_up":
            if cur > 0:
                cur -= 1
                selected_ref.current = cur
                set_selected(cur)
                us.selected = cur
            return True
        if event.kind == "arrow_down":
            if cur < total_now - 1:
                cur += 1
                selected_ref.current = cur
                set_selected(cur)
                us.selected = cur
            return True
        if event.kind == "space" or (event.kind == "char" and event.char == " "):
            if multi_now:
                new_checked = list(checked_ref.current)
                if cur in new_checked:
                    new_checked.remove(cur)
                else:
                    new_checked.append(cur)
                checked_ref.current = new_checked
                set_checked(new_checked)
                us.checked = new_checked
            return True
        if event.kind == "enter":
            if multi_now:
                sel = sorted(checked_ref.current)
                result = [options_now[i] for i in sel] if sel else list(us.default_options or [])
            else:
                result = [options_now[cur]] if 0 <= cur < total_now else list(us.default_options or [])
            us.done = True
            us.action = "confirmed"
            us.result = result
            return True
        if event.kind == "escape":
            us.done = True
            us.action = "cancel"
            us.result = list(us.default_options or [])
            return True
        # 弹窗期间其他按键一律消费（不输入到输入框）
        return True

    use_input(_handle, visible)

    if not visible:
        return h(TEXT, {"children": ""})

    options = list(us.options)
    total = len(options)
    multi = bool(us.multi_select)

    # ── 渲染 ──
    cur = max(0, min(selected, total - 1))
    descs = us.option_descriptions or []
    split = bool(descs) and width and width > 0
    title = us.title or "选择"
    rows: list = []

    # 标题行（对齐补全弹窗：▍ + 标题 + (n/total)）
    rows.append(h(TEXT, {
        "children": f" \u258d {title} ({cur + 1}/{total})",
        "style": _S_TITLE,
    }))

    if split:
        # ── 分栏说明模式：左栏选项 + │ + 右栏当前选中项说明 ──
        desc_w = _desc_column_width(width)
        opt_w = max(1, width - desc_w - 1)
        desc_sel = max(0, min(cur, len(descs) - 1)) if descs else 0
        desc_text = descs[desc_sel] if descs else ""
        desc_lines = _wrap_by_width(desc_text or "", desc_w)
        n_rows = max(total, len(desc_lines))
        for row_i in range(n_rows):
            if row_i < total:
                opt = _truncate_width(options[row_i], opt_w - 3)
                if row_i == cur:
                    left = h(TEXT, {
                        "children": f" \u25b6 {opt}",
                        "style": _S_SEL_BG,
                        "height": 1,
                    })
                else:
                    left = h(TEXT, {
                        "children": f"   {opt}",
                        "height": 1,
                    })
                # 左栏补宽（分隔线对齐）
                pad = max(0, opt_w - 1 - wcswidth_simple(f" {opt}"))
                if pad > 0:
                    left = h(Row, {"height": 1}, [
                        left,
                        h(TEXT, {"children": " " * pad, "style": _S_DIM, "height": 1}),
                    ])
            else:
                left = h(TEXT, {"children": " " * opt_w, "style": _S_DIM, "height": 1})
            desc_txt = _truncate_width(
                desc_lines[row_i] if row_i < len(desc_lines) else "", desc_w,
            )
            rows.append(h(Row, {"height": 1}, [
                left,
                h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
                h(TEXT, {"children": desc_txt, "style": _S_DIM, "height": 1}),
            ]))
    else:
        # ── 普通模式：选项列表（单选高亮 / 多选勾选 + 高亮） ──
        opt_w = max(1, width - 4) if width and width > 0 else 40
        for i, opt in enumerate(options):
            opt = _truncate_width(opt, opt_w)
            if multi:
                mark = _CHECKED if i in checked else _UNCHECKED
                prefix = f" {mark}"
            else:
                prefix = " \u25b6 " if i == cur else "   "
            style = _S_SEL_BG if i == cur else None
            rows.append(h(TEXT, {
                "children": f"{prefix} {opt}",
                "style": style,
                "height": 1,
            }))

    # 提示行
    if multi:
        hint = " \u2423 切换选中 · Enter 确认 · Esc 取消"
    else:
        hint = " \u2191\u2193 选择 · Enter 确认 · Esc 取消"
    rows.append(h(TEXT, {"children": hint, "style": _S_TIME}))

    return h(Column, None, rows)
