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
from src.tui.app._theme import _S_DIM, _S_SEP
from src.tui.ink import TEXT, h, Column, Row
from src.tui.ink.hooks import use_state, use_input, use_ref

__all__ = ["UserSelectPopup"]

#: 弹窗标题色（亮青加粗，对齐 _S_ACCENT_BOLD）
# ★ BEAUTY-18（体验动效）：生产路径改用标题呼吸色（time_glow 时间基）；
#   本常量保留为静态兼容回退/测试断言。
_S_TITLE = Style(fg=45, bold=True)
#: 高亮行背景色（与补全弹窗 sel_bg 呼吸下限一致）
# ★ BEAUTY-18：生产路径改用选中高亮呼吸背景（time_glow 时间基）；
#   本常量保留为静态兼容回退/测试断言。
_S_SEL_BG = Style(bg=237)
#: 多选勾选标记（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25cf "
_UNCHECKED = "\u25cb "

#: 弹窗标题呼吸色域（亮青 38→93 脉动，12s 周期——与补全弹窗 title_color 对齐）
_S_TITLE_LO = 38
_S_TITLE_HI = 93
_S_TITLE_PERIOD = 12.0
#: 选中高亮背景呼吸色域（236→239 脉动，10s 周期——与补全弹窗 sel_bg 对齐）
_S_SEL_BG_LO = 236
_S_SEL_BG_HI = 239
_S_SEL_BG_PERIOD = 10.0


def _glow(lo: int, hi: int, period: float) -> int:
    """时间基呼吸色号（0.1s 桶缓存，10Hz 渲染平滑推进）。"""
    from src.tui.app._theme import time_glow
    return time_glow(lo, hi, period)


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

        2026-08-05（增加操作）：vim 风格 ``j/k`` 导航（j=下、k=上，大小写
        等效）——与 ↑↓ 等价但无需方向键（终端/键盘布局友好）。弹窗激活期间
        消费所有按键（阻断输入框/旧路径副作用）。
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
        # vim 风格导航（j/k 大小写等效；g/G 跳首/末项——不与文本输入冲突，
        # 弹窗为纯选择界面）。2026-08-05（增加操作）。
        if event.kind == "char" and event.char in ("j", "J"):
            if cur < total_now - 1:
                cur += 1
                selected_ref.current = cur
                set_selected(cur)
                us.selected = cur
            return True
        if event.kind == "char" and event.char in ("k", "K"):
            if cur > 0:
                cur -= 1
                selected_ref.current = cur
                set_selected(cur)
                us.selected = cur
            return True
        if event.kind == "char" and event.char in ("g", "G"):
            target = 0 if event.char == "g" else total_now - 1
            if cur != target:
                cur = target
                selected_ref.current = cur
                set_selected(cur)
                us.selected = cur
            return True
        # ★ review 方向（死分支清理）：``event.kind == "space"`` 永假——
        #   KeyEvent.kind 无 "space" 值，空格统一走 ``kind == "char" and
        #   event.char == " "`` 分支（下方）。
        if event.kind == "char" and event.char == " ":
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
                # 多选：返回勾选结果（空勾选返回空列表）——与 Web 前端
                # confirm 行为一致；修复前空勾选时误回退 us.default_options
                # （用户取消所有勾选后回车仍返回默认项，违背交互意图）。
                sel = sorted(checked_ref.current)
                result = [options_now[i] for i in sel]
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

    # 标题行（对齐补全弹窗：▍ + 模式图标 + 标题 + (n/total)）
    # ★ BEAUTY-18（体验动效）：标题呼吸色——亮青 38→93 脉动（12s 周期，与
    #   补全弹窗 title_color 对齐）。弹窗激活时 session._needs_animation
    #   推进 10Hz 渲染，呼吸平滑；空闲静态 _S_TITLE（零额外渲染成本）。
    # ★ BEAUTY-29（2026-08-05 布局美化）：标题前置模式图标——单选 ▶ / 多选
    #   ☑（宽 1 列几何符号，与选中行 ▶/● 前缀语义呼应），一眼识别弹窗模式。
    title_style = Style(fg=_glow(_S_TITLE_LO, _S_TITLE_HI, _S_TITLE_PERIOD), bold=True)
    mode_icon = "\u2611" if multi else "\u25b6"
    rows.append(h(TEXT, {
        "children": f" \u258d {mode_icon} {title} ({cur + 1}/{total})",
        "style": title_style,
    }))
    # ★ BEAUTY-18：选中高亮背景呼吸——236→239 脉动（10s 周期，与补全弹窗
    #   sel_bg 对齐）。弹窗激活时渲染循环持续推进；空闲静态 _S_SEL_BG。
    sel_bg_style = Style(bg=_glow(_S_SEL_BG_LO, _S_SEL_BG_HI, _S_SEL_BG_PERIOD))

    if split:
        # ── 分栏说明模式：左栏选项 + │ + 右栏当前选中项说明 ──
        # 左栏按最大选项长度自适应分栏（2026-08-05 用户需求）：分隔线 │
        # 紧跟最长选项之后，右栏说明自动变宽——选项短时不再有大片留白；
        # 选项超长时受上限约束（右栏至少保留 _desc_column_width 基准宽），
        # 避免说明区被挤压。
        max_opt_w = max((wcswidth_simple(o) for o in options), default=0)
        base_desc_w = _desc_column_width(width)
        # 左栏 = 前缀 3 列（▶/空格）+ 最长选项宽 + 补白 1 列
        auto_opt_w = max_opt_w + 4
        # 上限：右栏至少保留 base_desc_w（说明可读）；左栏至少 1 列
        opt_w = max(1, min(max(1, width - base_desc_w - 1), auto_opt_w))
        desc_w = max(1, width - opt_w - 1)
        desc_sel = max(0, min(cur, len(descs) - 1)) if descs else 0
        desc_text = descs[desc_sel] if descs else ""
        desc_lines = _wrap_by_width(desc_text or "", desc_w)
        n_rows = max(total, len(desc_lines))
        for row_i in range(n_rows):
            if row_i < total:
                opt = _truncate_width(options[row_i], max(1, opt_w - 3))
                # ★ 多选勾选标记（回归 2026-08-05）：分栏分支原只渲染单选
                # ▶ 前缀，多选勾选态（●/○）完全不显示——多选 + 带说明时
                # 用户看不到选中项。前缀宽 3 列与单选 ▶ 一致（pad 补宽对齐）。
                if multi:
                    mark = _CHECKED if row_i in checked else _UNCHECKED
                    prefix = f" {mark}"
                else:
                    prefix = " \u25b6 " if row_i == cur else "   "
                left = h(TEXT, {
                    "children": f"{prefix}{opt}",
                    "style": sel_bg_style if row_i == cur else None,
                    "height": 1,
                })
                # 左栏补宽（分隔线对齐：左栏总宽 = opt_w，│ 在 opt_w 列）
                pad = max(0, opt_w - wcswidth_simple(f"{prefix}{opt}"))
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
            # ★ BEAUTY-19（体验动效）：说明列呼吸色——浅蓝 110→120 脉动
            #   （12s 周期，与提示行呼吸协调）。弹窗激活时渲染循环持续推进。
            rows.append(h(Row, {"height": 1}, [
                left,
                h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
                h(TEXT, {"children": desc_txt, "style": Style(fg=_glow(110, 120, 12.0)), "height": 1}),
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
            style = sel_bg_style if i == cur else None
            rows.append(h(TEXT, {
                "children": f"{prefix} {opt}",
                "style": style,
                "height": 1,
            }))

    # 提示行
    # 2026-08-05（增加操作）：提示加入 vim 风格 j/k/g/G 导航（与 ↑↓ 等价）
    # ★ BEAUTY-19（体验动效）：提示文本呼吸色——浅蓝 110→126 脉动（12s 周期，
    #   与补全弹窗 hint_color 对齐）。弹窗激活时渲染循环持续推进；空闲静态。
    if multi:
        hint = " \u2423 切换选中 · Enter 确认 · Esc 取消"
    else:
        hint = " \u2191\u2193/jk 选择 · g/G 首末 · Enter 确认 · Esc 取消"
    hint_style = Style(fg=_glow(110, 126, 12.0))
    rows.append(h(TEXT, {"children": hint, "style": hint_style}))

    return h(Column, None, rows)
