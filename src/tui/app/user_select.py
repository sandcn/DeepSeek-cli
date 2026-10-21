"""user_select — React Ink 用户选择弹窗组件（UserSelectPopup）。

React Ink 化（2026-08-05）：user_select 工具的终端交互界面从「命令补全弹窗
（CompletionState + show_completions）+ 手动 raw I/O（select/read_byte）」
迁移为独立的 React Ink 函数组件。

★ 2026-08-19（用户需求：user_select 并发 + tab 切换，参考 Claude Code
AskUserQuestion）：弹窗从「单问题」升级为**多问题 tab 界面**——多个并发
user_select 工具调用各自 append 一个 ``UserSelectState`` 到
``model.user_selects`` 并发队列，本组件以 tab 形式**全部一起显示**：:

    [×] 测试1:语言  [ ] 测试2:优先级  [ ] 测试3:工作流  [ ] 测试4:UI  √ Submit
    测试2:这个项目接下来最想优先做什么？

    > 1. 修 Bug
         先处理现有的问题
     ...

交互：
  - Tab / Shift+Tab / ← / → 切换焦点问题（顶部 tab 栏，含 Submit tab）；
  - ↑↓/jk/g/G 在当前问题内导航选项（SelectInput/MultiSelect 控件）；
  - Enter 确认当前问题（tab 标记 [×]，**提交前可重新回答**——切回该 tab
    重新导航选择后 Enter 覆盖旧答案）；
  - Esc 取消当前问题（用 default_options 回答，同样可重答）；
  - 最后一个 tab = **Submit 页**（2026-08-19 用户需求：增加一个 tab 页面
    给玩家是否提交）——汇总显示全部问题的答案，Enter 确认提交（统一置
    done → 各工具协程返回结果；未回答的问题取 default_options），Esc 返回
    修改。
  - ★ 2026-08-19（用户需求：回车自动切换）：Enter 确认（或 Esc 取消）
    当前问题后，焦点**自动跳到下一个未选择（未回答）的问题**；全部回答
    后自动切到 Submit 页。
  - ★ 2026-10-21（用户需求：支持用户输入不在选项中的内容并返回，输入时
    停止自动超时）：选项列表末尾追加「✎ 其他（自定义输入…）」伪选项——
    Enter 进入输入编辑模式（TextInput 接管字符/退格/方向键），Enter 提交
    自由输入、Esc 返回选项；输入期间把 ``us.typing`` 置 True（工具轮询
    据此暂停自动超时），退出/提交后恢复。自由输入结果并入 ``result``，
    并经 ``custom`` 字段返回（见工具层 JSON）。
  - ★ 2026-10-21（用户需求：Ctrl+G 用 vim 输入）：输入模式内按 Ctrl+G
    调用外部编辑器（``EDITOR``，默认 vim）编辑当前答案；返回后回填输入行
    （Enter 确认）。多行内容在输入行以 ``⏎`` 折叠显示，提交/返回 JSON
    保留原始换行。
  单问题（无并发）时行为与旧版完全一致（不渲染 tab 栏/Submit 页，Enter
  直接提交，零额外行）。

数据协议（跨线程安全，GIL 原子字段）：
  - 工具：构造 ``UserSelectState`` → append ``model.user_selects``（真源）
    → 同步 ``model.user_select``（兼容字段）→ ``bottom_view="user_select"``；
  - 组件：读取 ``model.user_selects``（空时回退单例 ``model.user_select``
    ——兼容旧调用/测试）；问题 Enter/Esc 经 ``mark_answered`` 写
    action/result/answered（提交前可重答覆盖）；Submit 页 Enter 统一经
    ``try_set_final`` 原子终态写入（first-write-wins，置 done）；
  - 工具：轮询自己的 state.done（Submit 提交或超时置位），读 result 后
    返回；finally 中若**全部**问题已 done（最后一个完成的协程）则清空
    列表 + 关闭 bottom_view。

★ 全面控件化（2026-08-16 方案B）：弹窗选项列表经标准控件
``SelectInput``（单选）/``MultiSelect``（多选）表达——导航（↑↓/j/k/g/G）、
Enter 确认、Esc 取消、空格勾选由控件消费，协议（first-write-wins、us 状态
写回）经控件回调（onSelect/onSubmit/onCancel/onHighlight）承载；自定义行
渲染经 ``renderItem``（单选 ▶/整行背景高亮、多选 ●/○ 勾选、分栏说明）
完全保留既有视觉；``consumeAll=True`` 弹窗模式（其他按键消费阻断输入框，
Ctrl+C 放行可中断工具执行）。

★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
/editmsg 消息选择已拆分为**独立协议**（EditMsgSelectState +
EditMsgSelectPopup + bottom_view="editmsg"，见 editmsg_select.py）——
本组件仅服务 ``user_select`` 工具与 ``CommandUiAdapter``，options 为单行
纯文本，不再承载 /editmsg 多行 option_lines 渲染。

组件与工具协程通信协议（跨线程安全，GIL 原子字段）：
  - 工具：设置 ``model.user_select``（visible=True, seq+1）→ request_bottom_redraw；
    App 组件以 ``key=seq`` 渲染本组件（seq 变化强制重挂载，重置内部 state）。
  - 组件：控件回调更新内部 state（渲染）+ 写 ``us.selected/checked``，
    提交/取消时写 ``us.done/us.action/us.result``。
  - 工具：轮询 ``us.done``（带 deadline 超时），读 result 后清理
    ``model.user_select = UserSelectState()`` 并 request_bottom_redraw。

  结果写入协议（P1-2，first-write-wins）：``done`` 一旦置位即终态，后续
  写入方不覆盖——组件 Enter/Escape 与工具超时分支统一经
  ``UserSelectState.try_set_final`` 原子终态写入（锁内检查 done + 写
  action/result/done，跨线程安全）：任一写入方先置位则另一方放弃覆盖
  （组件确认 vs 工具超时竞态全覆盖，2026-08-17 修复——修复前工具侧超时
  分支无条件覆盖 action/result，用户已确认却可能返回 timeout；组件侧
  _commit 已走 try_set_final 前存在 TOCTOU 窗口）。

依赖约束：仅依赖 app 同层（model/_theme/input_area）与 ink 框架（Layer 0/1），
无 tools 层反向依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from src.tui._input_layout import _wrap_by_width
from src.tui.app.input_area import _desc_column_width, _truncate_width
from src.tui.app._theme import _S_DIM, _S_SEP
from src.tui.ink import TEXT, h, Column, Row
from src.tui.ink.hooks import use_effect, use_ref, use_state, use_input
from src.tui.ink.widgets.interactive import SelectInput, MultiSelect, TextInput
from src.tui._external_editor import run_external_editor as _run_external_editor

from ._modal_view import empty_modal_frame, use_modal_scope

_logger = logging.getLogger(__name__)

__all__ = ["UserSelectPopup"]

#: 弹窗标题色（亮青加粗，对齐 _S_ACCENT_BOLD）
_S_TITLE = Style(fg=45, bold=True)
#: 高亮行背景色（静态 237——弹窗不呼吸）
_S_SEL_BG = Style(bg=237)
#: 说明列 / 提示行静态色（浅蓝 110——弹窗不呼吸）
_S_DESC = Style(fg=110)
#: 多选勾选标记（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25cf "
_UNCHECKED = "\u25cb "

#: 自定义输入伪选项（2026-10-21 用户需求：支持输入选项外内容并返回）
_CUSTOM_LABEL = "\u270e 其他（自定义输入…）"
#: 自定义输入伪选项的内部值（NUL 前缀不会与用户选项文本冲突；只用于
#: 控件 Enter/空格判定，不进入提交结果/返回 JSON）
_CUSTOM_VALUE = "\x00__user_select_custom__"
#: 自定义输入文本的显示宽度上限（防止单行输入超出弹窗宽度；按显示宽度截断）
_CUSTOM_INPUT_MAX_WIDTH = 200
#: 自定义输入多行内容的单行显示标记（提交/返回保留原始换行）
_NEWLINE_MARK = "\u23ce"

#: 并发 tab 栏样式（2026-08-19）：当前焦点问题（亮青加粗）
_S_TAB_ACTIVE = Style(fg=45, bold=True)
#: 未完成非焦点问题（dim 灰）
_S_TAB_INACTIVE = Style(fg=244)
#: 已回答（可重答）非焦点问题（[×]，中灰——区别于已提交终态）
_S_TAB_ANSWERED = Style(fg=246)
#: 已提交（done 终态锁定）非焦点问题（暗灰）
_S_TAB_DONE = Style(fg=240)
#: 全部完成后的 Submit 提示（绿色加粗）
_S_SUBMIT = Style(fg=40, bold=True)
#: 已完成 tab 只读结果行（绿色）
_S_DONE_RESULT = Style(fg=40)


def _popup_item_rows() -> int:
    """弹窗选项/说明行数上限（超屏防护）。

    ★ 模态底部视图（2026-08-17）：UserSelectPopup 独立为底部视图——弹窗
    打开时状态栏/输入区**不渲染**，可用高度 = 终端高 - 顶部标题栏 1 -
    弹窗标题 1 - 弹窗提示行 1 ≈ ``h - 3``（修复前按普通弹窗预留状态栏/输入区
    约 8 行 ``h - 11``，底部视图模式下留白过多——弹窗独立界面应利用原
    底部框全部空间）。与补全弹窗 ``_completion_item_rows`` 仅**超屏防护模式
    同源**（选项 + 说明行数限制，防止弹窗超高挤压消息区）；数值语义不同：
    ``_completion_item_rows`` 仍为 ``max(6, h - 11)``（补全弹窗须预留状态栏/
    输入区空间），本函数为底部视图模式独立放宽——勿同步两值。

    Returns:
        选项（含说明）最大渲染行数。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        # ★ P0（review）：下限由 6 改为 1——修复前 ``max(6, h - 3)`` 在矮终端
        #   （h < 9）强制 6 行，弹窗溢出屏幕底部（无滚动）；与
        #   ``editmsg_select._editmsg_item_rows`` 的 ``max(1, h - 3)`` 同根因，
        #   属同类缺陷跨模块未同步修复。二者均为模态底部视图，预算语义一致。
        return max(1, h - 3)
    except Exception:
        return 12


def _truncate_display(text: str, max_w: int) -> str:
    """按显示宽度截断单行文本（CJK/emoji 安全；用于自定义输入缓冲上限）。"""
    if max_w <= 0:
        return ""
    if wcswidth_simple(text) <= max_w:
        return text
    out: list = []
    used = 0
    for ch in text:
        cw = wcswidth_simple(ch)
        if cw < 0:
            cw = 1
        if used + cw > max_w:
            break
        out.append(ch)
        used += cw
    return "".join(out)


def _single_line_custom(text: str, max_w: int) -> str:
    """把自定义输入原始文本转为单行显示（换行 → ``⏎``，超宽截断）。

    ★ 2026-10-21（用户需求：vim 输入保留多行）：TextInput 为单行控件，
    多行内容只在**显示层**折叠为 ``⏎``；``custom_text`` 原文保留换行，
    提交/返回 JSON 仍是完整多行文本。
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    view = raw.replace("\n", _NEWLINE_MARK)
    if max_w > 0 and wcswidth_simple(view) > max_w:
        view = _truncate_width(view, max_w)
    return view


def _build_custom_row(is_sel: bool, opt_w: int, multi: bool) -> object:
    """自定义输入伪选项行（普通模式）。

    Args:
        is_sel: 是否当前高亮。
        opt_w: 选项行宽度预算。
        multi: 是否多选（多选无 ●/○ 勾选标记，前缀补同样宽度保持对齐）。
    """
    prefix = "    " if multi else (" \u25b6  " if is_sel else "    ")
    opt = _truncate_width(_CUSTOM_LABEL, max(1, opt_w - wcswidth_simple(prefix)))
    return h(TEXT, {
        "children": f"{prefix}{opt}",
        "style": _S_SEL_BG if is_sel else None,
        "height": 1,
        "key": "row-custom",
    })


def _build_custom_split_row(
    is_sel: bool, opt_w: int, desc_w: int, multi: bool,
) -> object:
    """自定义输入伪选项行（分栏说明模式：左栏选项 + │ + 右栏用途说明）。"""
    prefix = "    " if multi else (" \u25b6  " if is_sel else "    ")
    opt = _truncate_width(_CUSTOM_LABEL, max(1, opt_w - wcswidth_simple(prefix)))
    left_text = f"{prefix}{opt}"
    pad = max(0, opt_w - wcswidth_simple(left_text))
    left_children = [
        h(TEXT, {
            "children": left_text,
            "style": _S_SEL_BG if is_sel else None,
            "height": 1,
        }),
    ]
    if pad > 0:
        left_children.append(
            h(TEXT, {"children": " " * pad, "style": _S_DIM, "height": 1})
        )
    desc_txt = _truncate_width("输入不在选项中的答案", desc_w)
    return h(Row, {"height": 1}, [
        h(Row, {"height": 1}, left_children),
        h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
        h(TEXT, {"children": desc_txt, "style": _S_DESC, "height": 1}),
    ])


def _build_custom_input_row(text: str, on_change, on_submit) -> object:
    """自定义输入编辑行（TextInput 受控；输入模式下渲染在选项列表之后）。"""
    return h(Row, {"height": 1}, [
        h(TEXT, {"children": " \u270e ", "style": _S_TITLE, "height": 1}),
        h(TextInput, {
            "value": text,
            "onChange": on_change,
            "onSubmit": on_submit,
            "focus": True,
            "placeholder": "输入自定义答案后 Enter 提交",
        }),
    ])


def _build_tab_bar(states: list, active: int, width: int) -> object:
    """并发 tab 栏（Claude AskUserQuestion 风格，含 Submit tab）。

    问题 tab：``[×] 标题``（已回答/已提交）／``[ ] 标题``（未回答）；
    行尾 **Submit tab**（2026-08-19 用户需求：增加一个 tab 页面给玩家
    是否提交）：``[✓ 提交]``（全部已回答）／``[提交]``（有未回答）。
    当前焦点高亮。

    Args:
        states: 弹窗 state 列表（并发队列）。
        active: 当前焦点索引（已钳制；``== len(states)`` 表示 Submit tab）。
        width: 终端宽度（tab 标签截断预算）。

    Returns:
        Row 元素（高度 1）。
    """
    n = len(states)
    if n <= 0:
        return h(TEXT, {"children": "", "height": 1})
    # 全部已回答（answered 或已提交 done）→ Submit tab 显示 ✓
    all_answered = all(
        getattr(s, "answered", False) or getattr(s, "done", False)
        for s in states
    )
    submit_label = "[✓ 提交]" if all_answered else "[提交]"
    submit_w = wcswidth_simple(submit_label) + 2
    gap_w = 2 * n  # 问题间（含 Submit 前）间距
    # ★ P2（review）：预算下限由 8 改为 1——修复前 ``max(8, ...)`` 使窄终端/
    #   多问题的总宽（n*budget + 间距 + Submit 宽）超过 width，标签被布局层
    #   flexShrink 硬裁（而非按预算省略）。改为按剩余宽度整除分配后，
    #   ``Σlabel + gap_w + submit_w <= width`` 恒成立（每个 label 经
    #   ``_truncate_width`` 限制在 budget 内）。
    avail_w = width if (width and width > 0) else 80
    budget = max(1, (avail_w - submit_w - gap_w) // n)
    children: list = []
    for i, s in enumerate(states):
        marked = getattr(s, "answered", False) or getattr(s, "done", False)
        mark = "\u00d7" if marked else " "
        label = _truncate_width(
            f"[{mark}] {getattr(s, 'title', '') or '选择'}", budget,
        )
        if i == active:
            style = _S_TAB_ACTIVE
        elif getattr(s, "done", False):
            style = _S_TAB_DONE
        elif marked:
            style = _S_TAB_ANSWERED
        else:
            style = _S_TAB_INACTIVE
        children.append(h(TEXT, {
            "children": label, "style": style, "height": 1, "key": f"tab-{i}",
        }))
        children.append(h(TEXT, {"children": "  ", "height": 1}))
    # Submit tab（索引 == n）
    if active == n:
        submit_style = _S_TAB_ACTIVE
    elif all_answered:
        submit_style = _S_SUBMIT
    else:
        submit_style = _S_TAB_INACTIVE
    children.append(h(TEXT, {
        "children": submit_label, "style": submit_style, "height": 1,
        "key": "tab-submit",
    }))
    return h(Row, {"height": 1}, children)


def _build_regular_row(
    us, options: list, multi: bool, i: int, cur: int, checked: list,
    opt_w: int,
) -> object:
    """普通模式单个选项行构建（单选高亮 / 多选勾选 + 高亮，单行）。

    与旧 UserSelectPopup 普通模式视觉一致：选中/勾选前缀 + 高亮背景
    （整行）；选项超长截断到 ``opt_w``（CJK 安全，防超宽行破坏行级 diff
    宽度不变量）。对应控件 ``renderItem`` 单 item 语义——每个选项只构建
    自身一行（行数预算由调用方以控件 ``limit`` 折算，交互仍可导航到
    隐藏项，与补全弹窗一致）。

    Args:
        us: UserSelectState（当前 tab）。
        options: 选项字符串列表。
        multi: 是否多选。
        i: 当前选项索引（控件 item 索引）。
        cur: 当前高亮索引（已钳制）。
        checked: 多选勾选索引列表。
        opt_w: 选项行宽度预算。

    Returns:
        TEXT（该选项的单行）。
    """
    if multi:
        mark = _CHECKED if i in checked else _UNCHECKED
        prefix = f" {mark} "
    else:
        prefix = " \u25b6  " if i == cur else "    "
    opt = _truncate_width(str(options[i]), max(1, opt_w - wcswidth_simple(prefix)))
    return h(TEXT, {
        "children": f"{prefix}{opt}",
        "style": _S_SEL_BG if i == cur else None,
        "height": 1,
        "key": f"row-{i}",
    })


def _regular_item_limit(us, total: int) -> int:
    """普通模式控件 limit：行数预算即可见项数（单行选项，一选项一行）。

    与旧版超屏防护同源——选项行数限制（防止弹窗超高挤压消息区）。控件
    窗口滚动交互仍可导航到隐藏项。

    Args:
        us: UserSelectState（当前 tab）。
        total: 选项总数。

    Returns:
        可见 item 数上限（>= 1）。
    """
    return max(1, min(total, _popup_item_rows()))


def _build_split_row(
    us, options: list, multi: bool, cur: int, checked: list,
    opt_w: int, desc_w: int, width: int, row_i: int, total: int,
    desc_lines: list | None = None,
) -> object:
    """分栏说明模式单行构建（左栏选项 + │ + 右栏当前选中项说明）。

    与旧 UserSelectPopup 分栏视觉一致：左栏选项（前缀 ▶/勾选 + 文本 +
    补宽对齐），分隔线 ``│`` 紧跟最长选项之后，右栏说明自动变宽；选项超长
    受上限约束（右栏至少保留 ``_desc_column_width`` 基准宽）。

    Args:
        us: UserSelectState（当前 tab）。
        options: 选项字符串列表。
        multi: 是否多选。
        cur: 当前高亮索引（已钳制）。
        checked: 多选勾选索引列表。
        opt_w: 左栏宽。
        desc_w: 右栏宽。
        width: 终端宽度。
        row_i: 弹窗行号（0-based；超选项数时左栏留白）。
        total: 选项总数。
        desc_lines: 当前选中项说明的换行结果（★ P2 review：调用方算一次
            传入——修复前每个可见行调用本函数都重算一次
            ``_wrap_by_width(说明)``，N 行即 N 次换行计算（大说明/多选项时
            每帧 O(N×说明长度)）；None 时兼容旧行为自行计算。

    Returns:
        Row 元素（左栏 + │ + 右栏说明）。
    """
    descs = us.option_descriptions or []
    if row_i < total:
        opt = _truncate_width(options[row_i], max(1, opt_w - 3))
        if multi:
            mark = _CHECKED if row_i in checked else _UNCHECKED
            prefix = f" {mark}"
        else:
            prefix = " \u25b6 " if row_i == cur else "   "
        left = h(TEXT, {
            "children": f"{prefix}{opt}",
            "style": _S_SEL_BG if row_i == cur else None,
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
    desc_sel = max(0, min(cur, len(descs) - 1)) if descs else 0
    if desc_lines is None:
        desc_lines = _wrap_by_width((descs[desc_sel] if descs else "") or "", desc_w)
    desc_txt = _truncate_width(
        desc_lines[row_i] if row_i < len(desc_lines) else "", desc_w,
    )
    return h(Row, {"height": 1}, [
        left,
        h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
        h(TEXT, {"children": desc_txt, "style": _S_DESC, "height": 1}),
    ])


# ═══════════════════════════════════════════════════════════
# 弹窗协议 / tab 路由 / Submit 页（模块级；P1-1 巨型组件拆分）
# ═══════════════════════════════════════════════════════════
# 说明：以下逻辑原为 ``UserSelectPopup`` 内部闭包（组件函数 ~510 行）。提取为
# 模块级函数后按显式参数传递上下文（states / us / model / active_ref /
# set_active / set_selected），主组件只负责状态读取 + hooks 接线 + 渲染组装。


def _fallback_empty_options(states: list) -> None:
    """options 为空（外部注入/异常状态）时以 default_options 回退置 done。

    静默不可见（visible=False）会让工具协程（无超时 deadline=0）永远轮询
    ``us.done`` → 交互卡死。修复：自动以 ``default_options`` 回退（置
    done=True；first-write-wins——done 已由工具超时置位则跳过）。
    """
    for s in states:
        if (
            s is not None and s.visible and not s.done
            and not getattr(s, "options", None)
            # ★ 2026-10-21：allow_custom 的 state 即使没有普通选项，也可由
            #   用户输入自定义答案——不得自动回退置 done（工具路径不会构造
            #   空 options state，此处为外部注入/未来调用方防御）。
            and not getattr(s, "allow_custom", False)
        ):
            s.try_set_final("confirmed", list(getattr(s, "default_options", None) or []))


def _submit_all(states: list) -> None:
    """Submit 页 Enter：统一提交全部问题。

    已回答（answered）的问题按其 action/result 写入终态；未回答的问题用
    default_options（未回答即取默认）——全部经 ``try_set_final``
    （first-write-wins）置 done → 各工具协程轮询到 done 后返回结果。
    """
    try:
        for s in states:
            if getattr(s, "done", False):
                continue
            if getattr(s, "answered", False):
                s.try_set_final(
                    getattr(s, "action", "") or "confirmed",
                    list(getattr(s, "result", None) or []),
                    # ★ 2026-10-21：保留已回答问题的自由输入标记。
                    custom=getattr(s, "custom", "") or "",
                )
            else:
                s.try_set_final(
                    "confirmed",
                    list(getattr(s, "default_options", None) or []),
                )
    except Exception:
        # ★ P3（review）：不静默吞异常——记 debug 日志（提交路径异常会导致
        #   部分问题未写入终态，须可观测）。
        _logger.debug("user_select Submit 页提交异常", exc_info=True)
        return


def _back_to_questions(model, active_ref, set_active) -> None:
    """Submit 页 Esc：返回问题 tab（最后一个未回答；没有则第一个）。"""
    try:
        lst = getattr(model, "user_selects", None) or []
        n = len(lst)
        if n <= 0:
            return
        for i in range(n - 1, -1, -1):
            if not getattr(lst[i], "answered", False) and not getattr(lst[i], "done", False):
                active_ref.current = i
                set_active(i)
                return
        active_ref.current = 0
        set_active(0)
    except Exception:
        _logger.debug("user_select 返回问题 tab 异常", exc_info=True)
        return


def _advance_to_next_pending(model, active_ref, set_active) -> None:
    """回答后自动切换焦点到**下一个未选择**的问题。

    当前问题经 Enter 确认（或 Esc 取消）后其 tab 标记 [×]；焦点自动跳到队列
    中下一个未回答（answered=False 且 done=False）的问题；全部已回答时切到
    Submit tab（索引 == len(states)）。单问题（无并发）零操作。

    经 ``model.user_selects`` 实时读取（渲染帧闭包捕获的 states 可能陈旧
    ——同批按键/新 tab 加入场景），active_ref/set_active 同步更新。
    """
    try:
        lst = getattr(model, "user_selects", None) or []
        if len(lst) <= 1:
            return
        cur = active_ref.current
        n = len(lst)
        for step in range(1, n + 1):
            nxt = (cur + step) % n
            if (
                not getattr(lst[nxt], "answered", False)
                and not getattr(lst[nxt], "done", False)
            ):
                active_ref.current = nxt
                set_active(nxt)
                return
        # 全部已回答 → Submit tab（玩家确认是否提交）
        active_ref.current = n
        set_active(n)
    except Exception:
        _logger.debug("user_select 推进下一问题异常", exc_info=True)
        return


def _handle_tab_event(
    event, *, visible: bool, is_submit: bool, multi_mode: bool, tab_count: int,
    active_ref, set_active, states: list, model,
    input_mode: bool = False, exit_custom=None,
) -> bool:
    """顶层 tab 路由：Submit 页 Enter/Esc + Tab/←/→ 切换（模块级）。

    父组件 use_input 先于子控件（SelectInput/MultiSelect/TextInput）被
    router 调用（前序收集）——tab/←/→ 被本 handler 消费，↑↓/jk 等放行给
    控件。

    Args:
        input_mode: 当前是否处于自定义输入编辑模式。输入模式下 ←/→ 必须
            放行给 TextInput 移动光标（不得切换问题 tab）；Tab 仍切换问题，
            但切换前调用 ``exit_custom`` 退出输入并清空本题缓冲（避免输入
            内容串到下一题）。
        exit_custom: 退出自定义输入模式的回调（无参）。
    """
    if not visible:
        return False
    # Submit 页：Enter 提交全部 / Esc 返回问题（无需控件）
    if is_submit:
        if event.kind == "enter":
            _submit_all(states)
            return True
        if event.kind == "escape":
            _back_to_questions(model, active_ref, set_active)
            return True
    if not multi_mode:
        return False
    n = tab_count
    cur = active_ref.current
    new = cur
    if event.kind == "tab":
        # ★ 2026-10-21（用户需求）：输入模式中 Tab 切换问题前先退出输入
        #   （清空未提交文本 + 恢复自动超时），避免输入内容串题。
        if input_mode and exit_custom is not None:
            exit_custom()
        # Tab → 下一个；Shift+Tab（CSI u modifier=2）→ 上一个
        new = (cur + 1) % n if getattr(event, "modifier", 0) != 2 else (cur - 1) % n
    elif event.kind == "arrow_right":
        if input_mode:
            # 输入模式：←/→ 用于移动文本光标（交给 TextInput）。
            return False
        new = (cur + 1) % n
    elif event.kind == "arrow_left":
        if input_mode:
            return False
        new = (cur - 1) % n
    else:
        return False
    if new != cur:
        active_ref.current = new
        set_active(new)
    return True


def _handle_custom_input_event(
    event, *, visible: bool, input_mode: bool, exit_custom=None,
    edit_custom=None,
) -> bool:
    """自定义输入编辑模式的顶层按键（模块级）。

    接管：
      - ``Ctrl+G``（``\\x07``）：调用外部编辑器（``EDITOR``，默认 vim）
        编辑当前自定义答案；回填后仍停留在输入模式（Enter 确认）。
      - ``Esc``：退出输入、返回选项（**不取消问题**——再次 Esc 才由
        SelectInput/MultiSelect 的 onCancel 取消）。

    字符/退格/方向键/Enter 全部放行给 TextInput（输入行）处理。

    Args:
        edit_custom: Ctrl+G 回调（无参）；None 时消费但不动作（防御）。
    """
    if not visible or not input_mode:
        return False
    if (getattr(event, "char", "") or "") == "\x07":
        if edit_custom is not None:
            edit_custom()
        return True
    if event.kind == "escape":
        if exit_custom is not None:
            exit_custom()
        return True
    return False


def _make_question_callbacks(
    us, *, multi_mode: bool, model, active_ref, set_active, set_selected,
    allow_custom: bool = False, on_custom_enter=None,
    checked_values_getter=None,
):
    """问题页协议回调（first-write-wins；绑定当前 tab 的 us）。

    Args:
        allow_custom: 当前问题是否允许自定义输入（列表末尾有伪选项）。
        on_custom_enter: 选中伪选项时进入输入模式的回调（``allow_custom``
            为 True 时传入）。
        checked_values_getter: 多选当前勾选值 getter（多选 Enter 提交 /
            自定义提交时使用；``None`` 时回退空列表）。

    Returns:
        ``(on_select, on_submit, on_cancel, on_highlight)``。
    """

    def _commit(result, action: str, custom: str = "") -> bool:
        """提交终态（单问题 Enter：直接提交，first-write-wins）。

        原子终态写入经 ``us.try_set_final``（锁内检查+写入）——消除与工具侧
        超时分支的竞态覆盖（修复前此处 ``if us.done: return`` + 顺序写三字段
        存在 TOCTOU 窗口，组件确认可能被工具超时分支覆盖）。

        Returns:
            True 本次写入生效；False 终态已由其他线程（工具超时）置位。
        """
        return us.try_set_final(action, result, custom=custom)

    def _commit_answer(result, action: str, custom: str = "") -> None:
        """标记回答（多问题 Enter/Esc；提交前可重答覆盖）→ 自动推进。

        ★ 2026-08-19（用户需求：已经回答的可以重新答）：经 ``us.mark_answered``
        写 action/result/answered（不置 done）——提交前切回该 tab 可反复重答
        覆盖；done 已置位（已提交/超时）则放弃。
        """
        if not us.mark_answered(action, result, custom=custom):
            return
        _advance_to_next_pending(model, active_ref, set_active)

    def _on_select(item) -> None:
        # Enter：单问题直接提交；多问题标记回答 + 自动推进。
        # ★ P2（review 2026-08-22，对齐 editmsg_select P2-4 修复）：result 直接
        #   取 item["value"]（权威选中值）——修复前用渲染帧闭包 ``cur`` 判定
        #   范围 ``0 <= cur < total``，同批多按键无重渲染时 cur 陈旧，可能与
        #   item 不一致（如列表收缩后误走 default_options）。
        # ★ 2026-10-21（用户需求）：列表末尾的「✎ 其他（自定义输入…）」
        #   伪选项不提交——进入输入编辑模式（其文本经 TextInput 提交）。
        value = item["value"] if item is not None else None
        if (
            allow_custom and on_custom_enter is not None
            and value == _CUSTOM_VALUE
        ):
            on_custom_enter()
            return
        if getattr(us, "multi_select", False):
            # 多选：Enter 提交当前勾选集合（onSelect 只用于拿到确定项，
            # 用于伪选项判断；勾选态由 onChange 同步）。
            result = (
                list(checked_values_getter())
                if checked_values_getter is not None else []
            )
        else:
            result = [value] if item is not None else []
        if multi_mode:
            _commit_answer(result, "confirmed")
        else:
            _commit(result, "confirmed")

    def _on_submit(sel: list) -> None:
        # 多选 Enter（未提供 onSelect 的旧调用路径）：返回勾选结果。
        if multi_mode:
            _commit_answer(list(sel), "confirmed")
        else:
            _commit(list(sel), "confirmed")

    def _on_cancel(*_args) -> None:
        # Esc 取消：单问题直接提交 cancel；多问题标记回答（默认值）+ 推进。
        if multi_mode:
            _commit_answer(list(us.default_options or []), "cancel")
        else:
            _commit(list(us.default_options or []), "cancel")

    def _on_highlight(idx: int) -> None:
        # 导航变化：同步 us.selected（组件内部 state 由控件维护）
        us.selected = int(idx)
        set_selected(int(idx))

    return _on_select, _on_submit, _on_cancel, _on_highlight


def _build_submit_page(states: list, width: int) -> list:
    """Submit 页行构建（标题 + 每题状态 + 提示）。

    ★ 2026-08-19（用户需求：增加一个 tab 页面给玩家是否提交）。
    """
    rows: list = []
    rows.append(h(TEXT, {
        "children": " \u258d \u2713 提交全部答案",
        "style": _S_TITLE,
        "textWrap": "truncate-end",
        "key": "us-submit-title",
    }))
    for i, s in enumerate(states):
        answered = getattr(s, "answered", False)
        done = getattr(s, "done", False)
        mark = "\u00d7" if (answered or done) else " "
        title_txt = getattr(s, "title", "") or "选择"
        if answered or done:
            result_txt = "、".join(
                str(x).replace("\r\n", "\n").replace("\r", "\n").replace(
                    "\n", _NEWLINE_MARK,
                )
                for x in (getattr(s, "result", None) or [])
            ) or "（无）"
            # ★ 2026-10-21（用户需求）：自由输入标记（提交页汇总可见）。
            custom_txt = str(getattr(s, "custom", "") or "")
            status = f"\u2713 {result_txt}" + ("（自定义）" if custom_txt else "")
            style = _S_DONE_RESULT
        else:
            status = "未回答"
            style = _S_DESC
        line = f"  [{mark}] {title_txt} \u2192 {status}"
        rows.append(h(TEXT, {
            "children": _truncate_width(line, width),
            "style": style,
            "height": 1,
            "key": f"us-submit-row-{i}",
        }))
    rows.append(h(TEXT, {
        "children": "  Enter 提交全部 · Esc 返回修改 · Tab/\u2190\u2192 切换",
        "style": _S_DESC,
        "textWrap": "truncate-end",
        "key": "us-submit-hint",
    }))
    return rows


def UserSelectPopup(props) -> object:
    """React Ink 用户选择弹窗组件（多问题 tab，SelectInput/MultiSelect 控件化）。

    ★ 2026-10-21（用户需求）：支持用户输入列表外的自定义答案——状态
    ``us.allow_custom`` 为 True 时列表末尾渲染「✎ 其他（自定义输入…）」
    伪选项；Enter 进入输入编辑模式（选项控件失焦、TextInput 接管），
    Enter 提交 / Esc 返回；输入模式内 Ctrl+G 调用外部编辑器（``EDITOR``，
    默认 vim，多行以 ``⏎`` 折叠显示、返回保留换行）。``us.typing`` 在
    输入期间为 True（工具轮询暂停自动超时）。

    Props:
        model: AppModel 实例（读 ``model.user_selects`` 并发队列；空时回退
            ``model.user_select`` 单例——兼容旧调用/测试）。
        width: 终端宽度（分栏说明模式需要）。

    Returns:
        Column（弹窗行）或空 TEXT（不可见时零高度）。
    """
    model = props["model"]
    width = props.get("width", 80)
    # ── 读取弹窗列表（2026-08-19 并发真源）+ 兼容单例回退 ──
    states_raw = getattr(model, "user_selects", None) or []
    us_single = getattr(model, "user_select", None)
    if states_raw:
        states = [
            s for s in states_raw
            if s is not None and getattr(s, "visible", True)
        ]
    else:
        # 兼容：旧路径（测试/直接赋值 model.user_select）构造单元素列表
        if us_single is not None and us_single.visible and not us_single.done:
            states = [us_single]
        else:
            states = []

    # ★ P2-6（review 修复）：options 为空（外部注入/异常状态）时弹窗无可交互
    #   选项——静默不可见（visible=False）会让工具协程（无超时 deadline=0）
    #   永远轮询 ``us.done`` → 交互卡死。修复：自动以 default_options 回退
    #   （置 done=True；first-write-wins——done 已由工具超时置位则跳过）。
    #   ★ P3（review）：副作用从渲染期移到 effect 提交期——渲染阶段不应修改
    #   跨线程共享终态（工具协程可能并发读取）；本函数每帧提交期执行，
    #   内部 done/visible 判断保证幂等（首写生效后不再触发）。
    use_effect(lambda: _fallback_empty_options(states), None)
    visible = bool(states)

    # ── hooks（无条件调用，保持 fiber hook 顺序稳定） ──
    # 当前焦点 tab 索引（并发多问题 + Submit tab；单问题时恒 0）。
    # tab 索引范围：0..len(states)-1 = 问题；len(states) = Submit 页。
    multi_mode = len(states) > 1
    tab_count = len(states) + 1 if multi_mode else 1
    active, set_active = use_state(0)
    active_ref = use_ref(0)
    # 当前 tab 高亮（从 us.selected 读取；控件导航经 set_selected 触发重渲染）
    initial_sel = 0
    if states and visible:
        try:
            initial_sel = int(getattr(states[0], "selected", 0) or 0)
        except (TypeError, ValueError):
            initial_sel = 0
    selected, set_selected = use_state(initial_sel)
    # ★ 2026-10-21（用户需求：user_select 支持自定义输入）：输入编辑状态。
    #   - custom_editing_ref 是事件期权威源（同批按键无重渲染时 state 陈旧）；
    #   - custom_text 受控传给输入行 TextInput；
    #   - state setter 仅负责触发重渲染（ref 即时生效）。
    custom_editing, set_custom_editing = use_state(False)
    custom_text, set_custom_text = use_state("")
    custom_editing_ref = use_ref(False)
    custom_editing_ref.current = custom_editing
    # ★ 2026-10-21（Ctrl+G vim 输入）：原始文本 ref（事件期权威源；可含换行，
    #   与单行显示分离）与编辑器错误提示 state。
    custom_text_ref = use_ref("")
    custom_editor_error, set_custom_editor_error = use_state("")
    # ★ 会话防御（2026-08-18 连续弹出 + 2026-08-19 并发 tab）：us 实例变化
    #   （清理后重开产生新 UserSelectState 对象 / tab 切换）时本帧即以新
    #   us.selected 计算高亮，并排队 set_selected 收敛——即使调和器因 key
    #   复用保留旧 fiber，也不残留旧选中/旧勾选。
    us_ref = use_ref(None)
    prev_states_ref = use_ref(None)
    prev_states = prev_states_ref.current
    if visible:
        # 并发会话检测（fiber 复用防御）：旧列表元素全部不在新列表中
        # （全部 done 清空后重开新会话）→ 焦点回到第一个 tab。
        if (
            prev_states is not None and prev_states
            and all(
                p is not None
                and all(p is not s for s in states)
                for p in prev_states
            )
        ):
            active = 0
            set_active(0)
        if active >= tab_count:
            active = max(0, tab_count - 1)
            set_active(active)
    prev_states_ref.current = list(states)
    cur_active = max(0, min(active, tab_count - 1)) if states else 0
    active_ref.current = cur_active
    # Submit 页：多问题时的最后一个 tab（索引 == len(states)）
    is_submit = multi_mode and cur_active == len(states)

    us = states[cur_active] if (states and not is_submit) else None
    prev_us = us_ref.current
    fresh_us = prev_us is not us
    if fresh_us:
        us_ref.current = us
        # ★ 2026-10-21（用户需求）：切换问题（手动 tab / 自动推进 / 并发
        #   新 tab 导致焦点变化）时退出自定义输入并清空缓冲——避免输入
        #   内容串到下一题；同时复位旧 state 的 typing（恢复其自动超时）。
        if custom_editing_ref.current:
            custom_editing_ref.current = False
            set_custom_editing(False)
            set_custom_text("")
        # ★ 2026-10-21：切题/自动推进时清空输入原文与编辑器错误提示。
        custom_text_ref.current = ""
        set_custom_editor_error("")
        if prev_us is not None:
            prev_us.typing = False
        if us is not None:
            try:
                sel_us = int(getattr(us, "selected", 0) or 0)
            except (TypeError, ValueError):
                sel_us = 0
            if selected != sel_us:
                set_selected(sel_us)
    # ── 当前问题基础数据（提前计算：自定义输入回调/渲染共用） ──
    options = list(us.options) if us is not None else []
    total = len(options)
    multi = bool(getattr(us, "multi_select", False)) if us is not None else False
    # ★ 工具路径 allow_custom=True（列表末尾追加「✎ 其他」伪选项）；
    #   命令系统选择路径默认 False（index 契约不变）。
    allow_custom = bool(getattr(us, "allow_custom", False)) if us is not None else False
    total_items = max(1, total + (1 if allow_custom else 0))
    # ★ 模态底部视图声明（2026-08-17 通用机制）：visible 时独占键盘输入——
    #   未消费按键被 input router 吞掉（不落入输入缓冲；输入区已不渲染，
    #   字符落入输入缓冲会「看不见地」改变用户输入）。visible=False 时 hook
    #   不参与路由（组件不渲染/已关闭，零影响）。与 use_fullscreen 同节点
    #   类型（模态输入接管语义，见 _hooks_input.use_modal docstring）。
    use_modal_scope(visible, fullscreen=False)

    # ══════════════ 自定义输入（2026-10-21 用户需求）══════════════
    # 列表末尾伪装项「✎ 其他（自定义输入…）」Enter 进入编辑模式：
    #   - 弹窗选项控件失焦（focus=False），TextInput 接管字符/退格/方向键；
    #   - 父 handler 只消费 Esc（退出输入）与 Tab（切换问题并退出输入）；
    #   - us.typing=True 期间工具轮询暂停自动超时（退出/提交后恢复）。
    def _checked_values() -> list:
        """当前多选勾选值（按 options 顺序；自定义伪选项不参与）。"""
        if us is None:
            return []
        out: list = []
        for raw in list(getattr(us, "checked", []) or []):
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < total:
                out.append(options[idx])
        return out

    def _on_checked_change(values) -> None:
        """多选勾选集合变化：同步 us.checked（自定义提交 / 重答恢复用）。"""
        if us is None:
            return
        vals = [v for v in list(values or []) if v != _CUSTOM_VALUE]
        us.checked = [
            i for i, opt in enumerate(options) if opt in vals
        ]

    def _can_toggle(idx, item) -> bool:
        """自定义输入伪选项行不可勾选（不是可提交的选项）。"""
        return not (allow_custom and idx >= total)

    def _exit_custom(clear: bool = True) -> None:
        """退出自定义输入编辑模式（恢复选项控件焦点 + 自动超时）。"""
        custom_editing_ref.current = False
        set_custom_editing(False)
        if clear:
            set_custom_text("")
            custom_text_ref.current = ""
        set_custom_editor_error("")
        if us is not None:
            us.typing = False

    def _enter_custom() -> None:
        """进入自定义输入编辑模式（空缓冲；高亮伪选项行）。"""
        if us is None or not allow_custom:
            return
        custom_editing_ref.current = True
        set_custom_editing(True)
        set_custom_text("")
        custom_text_ref.current = ""
        set_custom_editor_error("")
        # 跨线程标志：工具轮询检测到 True 后暂停自动超时。
        us.typing = True
        # 视觉：高亮列表末尾的「✎ 其他」行（普通项索引 0..total-1）。
        us.selected = total
        set_selected(total)

    def _on_custom_change(text: str) -> None:
        """TextInput 受控变化：保存原始文本（保留换行）。

        TextInput 显示的是单行折叠文本（换行 → ``_NEWLINE_MARK``）；此处
        还原换行后写入 ``custom_text_ref`` / state。显示宽度截断只在渲染
        输入行时做（``_single_line_custom``），不丢失原始多行内容。
        """
        raw = str(text or "").replace(_NEWLINE_MARK, "\n")
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        custom_text_ref.current = raw
        set_custom_text(raw)
        set_custom_editor_error("")

    def _open_custom_editor() -> None:
        """Ctrl+G：用外部编辑器（``EDITOR``，默认 vim）编辑自定义答案。

        编辑器返回文本后回填到输入行（仍保持输入模式，Enter 确认）；
        取消/失败（None）保持原文本并显示错误提示。编辑期间
        ``us.typing`` 保持 True（自动超时继续暂停）。
        """
        if us is None or not custom_editing_ref.current:
            return
        before = custom_text_ref.current or ""
        edited = _run_external_editor(before)
        if edited is None:
            set_custom_editor_error("编辑器不可用，已保留原文本")
            return
        raw = str(edited).replace("\r\n", "\n").replace("\r", "\n")
        custom_text_ref.current = raw
        set_custom_text(raw)
        set_custom_editor_error("")
        us.typing = True

    def _submit_custom(text: str) -> None:
        """Enter 提交自由输入答案（空文本不提交，保持输入模式）。

        ★ 2026-10-21：提交使用 ``custom_text_ref`` 的**原始文本**（保留
        vim 输入的多行换行）；TextInput 的 ``text`` 参数只是单行显示文本
        （回填后未触发 onChange 时作为兜底）。
        """
        if us is None or not custom_editing_ref.current:
            return
        raw = custom_text_ref.current or ""
        if not raw and text:
            raw = str(text).replace(_NEWLINE_MARK, "\n")
        value = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not value:
            return
        if multi:
            result = _checked_values()
            if value not in result:
                result.append(value)
        else:
            result = [value]
        if multi_mode:
            if us.mark_answered("confirmed", result, custom=value):
                _advance_to_next_pending(model, active_ref, set_active)
        else:
            us.try_set_final("confirmed", result, custom=value)
        _exit_custom()

    # ── 顶层输入路由（2026-10-21）：自定义输入 Esc 优先于 tab/控件 ──
    # 父组件 use_input 先于子控件（SelectInput/MultiSelect/TextInput）被
    # router 调用（前序收集）——输入模式下 Esc 由本 handler 消费（退出
    # 输入而不是取消问题）；字符/退格/方向键/Enter 放行给 TextInput。
    use_input(
        lambda ev: _handle_custom_input_event(
            ev, visible=visible, input_mode=custom_editing_ref.current,
            exit_custom=_exit_custom, edit_custom=_open_custom_editor,
        ),
        visible,
    )

    # ── 顶层输入路由（2026-08-19）：Submit 页 Enter/Esc + tab 切换 ──
    # 父组件 use_input 先于子控件（SelectInput/MultiSelect）被 router 调用
    # （前序收集）——tab/←/→ 被本 handler 消费，↑↓/jk 等放行给控件。
    # ★ P1-1（巨型组件拆分）：tab 路由（Submit 页 Enter/Esc + Tab/←/→ 切换）
    #   收敛到模块级 ``_handle_tab_event``（与 ``_submit_all`` /
    #   ``_back_to_questions``）。
    # ★ 2026-10-21：输入模式下 ←/→ 放行给 TextInput；Tab 切换问题前先退出
    #   输入（清空未提交文本 + 恢复自动超时）——避免输入内容串题。
    use_input(
        lambda ev: _handle_tab_event(
            ev, visible=visible, is_submit=is_submit, multi_mode=multi_mode,
            tab_count=tab_count, active_ref=active_ref, set_active=set_active,
            states=states, model=model,
            input_mode=custom_editing_ref.current, exit_custom=_exit_custom,
        ),
        visible,
    )

    if not visible:
        return empty_modal_frame()

    rows: list = []

    # ── 并发 tab 栏（仅多问题显示；单问题零额外行——旧行为完全保留） ──
    if multi_mode:
        rows.append(_build_tab_bar(states, cur_active, width))

    # ══════════════ Submit 页（2026-08-19 用户需求：增加一个 tab
    # 页面给玩家是否提交）══════════════════
    if is_submit:
        rows.extend(_build_submit_page(states, width))
        return h(Column, None, rows)

    # ══════════════ 问题页 ══════════════
    # ★ 收窄：``us`` 为 Optional（``states[cur_active] if ... else None``；
    #   提交页已在上面返回）——mypy 无法从 ``visible`` 反推非 None，显式判空。
    if us is None:
        return empty_modal_frame()
    done = bool(getattr(us, "done", False))

    # ── 渲染 ──
    # 本帧高亮源：us 实例变化（fresh_us——组件防御，防 fiber 复用残留旧
    # 选中）时用新 us.selected；否则用 use_state 值（控件导航权威）。
    # ★ 2026-10-21：索引范围含列表末尾的自定义输入伪选项（total_items-1）。
    if fresh_us and us is not None:
        try:
            cur = max(0, min(int(us.selected or 0), total_items - 1))
        except (TypeError, ValueError):
            cur = max(0, min(selected, total_items - 1))
    else:
        cur = max(0, min(selected, total_items - 1))
    descs = us.option_descriptions or []
    # 分栏说明模式：option_descriptions 非空时左栏选项 + 右栏说明。
    split = bool(descs) and width and width > 0
    title = us.title or "选择"

    # 标题行（对齐补全弹窗：▍ + 模式图标 + 标题 + (n/total)）
    # ★ 静态色（2026-08-05 修复）：弹窗标题不再呼吸——弹窗是交互界面，
    #   呼吸色使弹窗行每帧随 time_glow 变化 → 渲染器每帧重写弹窗行（Termux
    #   等终端每帧闪烁/错乱）；静态色弹窗内容不变时 diff 零输出（只在交互
    #   按键时重绘）。
    # ★ BEAUTY-29（2026-08-05 布局美化）：标题前置模式图标——单选 ▶ / 多选
    #   ☑（宽 1 列几何符号，与选中行 ▶/● 前缀语义呼应），一眼识别弹窗模式。
    title_style = _S_TITLE
    mode_icon = "\u2611" if multi else "\u25b6"
    if done:
        # 已提交终态（[×] 锁定）：只读显示已选结果（绿色 ✓ 行）
        # ★ 2026-10-21：多行自定义答案在单行标题中折叠显示（返回 JSON 保留原文）。
        result_txt = "、".join(
            str(x).replace("\r\n", "\n").replace("\r", "\n").replace("\n", _NEWLINE_MARK)
            for x in (getattr(us, "result", None) or [])
        )
        if result_txt:
            title_disp = f" \u258d {mode_icon} {title} \u2713 已选择: {result_txt}"
        else:
            title_disp = f" \u258d {mode_icon} {title} \u2713 已确认"
    else:
        # 未提交（含已回答可重答）：正常选项界面
        # ★ 2026-10-21：自定义输入伪选项行显示「(自定义)」而非越界序号。
        pos = f" ({cur + 1}/{total})" if cur < total else " (自定义)"
        title_disp = f" \u258d {mode_icon} {title}{pos}"
    # ★ 窄屏防溢出：标题超宽时截断 title 文本（保留模式图标与位置指示
    #   (cur/total)）——修复前标题行自动换行，(1/3) 位置指示拆到下一行
    #   （视觉错乱）；textWrap="truncate-end" 兜底极端窄屏（截断后仍超宽）。
    if wcswidth_simple(title_disp) > width:
        prefix = f" \u258d {mode_icon} "
        if done:
            title_disp = prefix + _truncate_width(title, max(1, width - wcswidth_simple(prefix)))
        else:
            suffix = pos
            budget = max(1, width - wcswidth_simple(prefix) - wcswidth_simple(suffix))
            title_disp = prefix + _truncate_width(title, budget) + suffix
    rows.append(h(TEXT, {
        "children": title_disp,
        "style": title_style,
        "textWrap": "truncate-end",
        "key": "us-title",
    }))

    # ── 协议回调（first-write-wins；绑定当前 tab 的 us） ──
    # ★ P1-1（巨型组件拆分）：协议回调（提交/标记回答/自动推进/导航同步）
    #   收敛到模块级 ``_make_question_callbacks``。
    _on_select, _on_submit, _on_cancel, _on_highlight = _make_question_callbacks(
        us, multi_mode=multi_mode, model=model, active_ref=active_ref,
        set_active=set_active, set_selected=set_selected,
        allow_custom=allow_custom, on_custom_enter=_enter_custom,
        checked_values_getter=_checked_values,
    )

    # ── 选项控件（SelectInput/MultiSelect 标准控件） ──
    # done（已提交终态）：只读结果行；answered（可重答）与未回答：渲染控件。
    if done:
        # 已提交 tab：只读结果行（绿色），不渲染交互控件
        result_txt = "、".join(
            str(x).replace("\r\n", "\n").replace("\r", "\n").replace("\n", _NEWLINE_MARK)
            for x in (getattr(us, "result", None) or [])
        )
        if not result_txt:
            result_txt = "（无）"
        rows.append(h(TEXT, {
            "children": f"  {result_txt}",
            "style": _S_DONE_RESULT,
            "textWrap": "truncate-end",
            "height": 1,
            "key": "us-done-result",
        }))
    else:
        items = [{"label": opt, "value": opt} for opt in options]
        # ★ 2026-10-21（用户需求）：列表末尾追加「✎ 其他（自定义输入…）」
        #   伪选项（仅工具路径 allow_custom=True；命令选择路径不追加）。
        if allow_custom:
            items.append({"label": _CUSTOM_LABEL, "value": _CUSTOM_VALUE})
        # 输入模式多占 1 行（输入编辑行）——可见项预算同步减 1，防超屏。
        limit_base = _popup_item_rows()
        if custom_editing_ref.current:
            limit_base = max(1, limit_base - 1)
        control_focus = not custom_editing_ref.current
        if split:
            # ── 分栏说明模式：左栏选项 + │ + 右栏当前选中项说明 ──
            # ★ 2026-10-21：左栏宽度须容纳「✎ 其他（自定义输入…）」伪选项
            #   文本（否则被截断成单个 ✎，用户看不出这是输入入口）。
            max_opt_w = max((wcswidth_simple(o) for o in options), default=0)
            if allow_custom:
                max_opt_w = max(max_opt_w, wcswidth_simple(_CUSTOM_LABEL))
            base_desc_w = _desc_column_width(width)
            auto_opt_w = max_opt_w + 4
            opt_w = max(1, min(max(1, width - base_desc_w - 1), auto_opt_w))
            desc_w = max(1, width - opt_w - 1)
            # 超屏防护：选项 + 说明行数限制（与补全弹窗 _completion_item_rows
            # 同源——超长说明 / 大量选项时弹窗不超终端高度）。每选项一行，
            # limit 即可见选项数上限（控件窗口滚动交互仍可导航到隐藏项）。
            limit = min(max(total_items, 1), limit_base)

            # ★ P2（review）：当前选中项说明换行只算一次——修复前每个可见行
            #   调用 renderItem 都重算一次 ``_wrap_by_width(说明)``（N 行 =
            #   N 次换行计算）。此处计算一次后闭包传入。
            _descs = us.option_descriptions or []
            _desc_sel = max(0, min(cur, len(_descs) - 1)) if _descs else 0
            desc_lines_cur = _wrap_by_width(
                (_descs[_desc_sel] if _descs else "") or "", desc_w,
            )

            def _split_renderer(item, idx, is_sel, is_checked=None):
                # 分栏说明模式单行：控件对每个 item 调用一次 renderItem——
                # 只构建 item 索引对应的那一行（修复前循环渲染整个选项列表，
                # 每个 item 重复 total 次 → 弹窗选项重复多份）。
                # ★ 2026-10-21：列表末尾伪选项行单独渲染。
                if idx >= total:
                    return _build_custom_split_row(is_sel, opt_w, desc_w, multi)
                checked_now = list(getattr(us, "checked", []) or [])
                if multi and is_checked is not None:
                    checked_now = [idx] if is_checked else []
                return _build_split_row(
                    us, options, multi, cur, checked_now,
                    opt_w, desc_w, width, idx, total,
                    desc_lines=desc_lines_cur,
                )
        else:
            opt_w = max(1, width - 4) if width and width > 0 else 40
            # ★ 2026-10-21：limit 用含伪选项的总数，并受输入模式行预算约束
            #   （limit_base 已在输入模式下减 1）。
            limit = max(1, min(total_items, limit_base))

            def _regular_renderer(item, idx, is_sel, is_checked=None):
                # ★ 2026-10-21：列表末尾伪选项行单独渲染（无勾选标记）。
                if idx >= total:
                    return _build_custom_row(is_sel, opt_w, multi)
                checked_now = list(getattr(us, "checked", []) or [])
                if multi and is_checked is not None:
                    checked_now = [idx] if is_checked else []
                return _build_regular_row(
                    us, options, multi, idx, cur, checked_now, opt_w,
                )

        # 多选勾选态：控件内部维护，并经 onChange 同步到 us.checked；
        # Enter 经 onSelect 回调当前光标项（用于「✎ 其他」伪选项判断，
        # 普通项则提交当前勾选集合）。控件 key 含当前 tab 的 seq 与 active
        # ——tab 切换时控件重挂载（use_state 重置为当前 tab 的
        # initialIndex/initialValues，已回答重答时恢复 us.selected/us.checked）。
        # ★ 2026-10-21：自定义输入模式下控件失焦（focus=False）——字符/
        #   方向键交给输入行 TextInput，列表不可交互。
        ctrl_key_suffix = (
            f"-{cur_active}" if multi_mode else ""
        )
        if multi:
            # initialValues：us.checked 中索引 → 选项 value（控件按 value 匹配）
            initial_vals = []
            checked_idx = list(getattr(us, "checked", []) or [])
            for i in checked_idx:
                try:
                    idx = int(i)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < total:
                    initial_vals.append(options[idx])
            control = h(MultiSelect, {
                "key": f"us-multiselect-{getattr(us, 'seq', 0)}{ctrl_key_suffix}",
                "items": items,
                "initialIndex": cur,
                "initialValues": initial_vals,
                "limit": limit,
                "onSelect": _on_select,
                "onChange": _on_checked_change,
                "canToggle": _can_toggle,
                "onCancel": _on_cancel,
                "onHighlight": _on_highlight,
                "renderItem": _regular_renderer if not split else _split_renderer,
                "consumeAll": True,
                "focus": control_focus,
            })
        else:
            control = h(SelectInput, {
                "key": f"us-select-{getattr(us, 'seq', 0)}{ctrl_key_suffix}",
                "items": items,
                "initialIndex": cur,
                "limit": limit,
                "onSelect": _on_select,
                "onCancel": _on_cancel,
                "onHighlight": _on_highlight,
                "renderItem": _regular_renderer if not split else _split_renderer,
                "consumeAll": True,
                "focus": control_focus,
            })
        rows.append(control)

        # ── 自定义输入编辑行（输入模式：选项列表之后、提示行之前） ──
        if custom_editing_ref.current:
            try:
                input_budget = min(_CUSTOM_INPUT_MAX_WIDTH, max(8, int(width) - 8))
            except (TypeError, ValueError):
                input_budget = _CUSTOM_INPUT_MAX_WIDTH
            rows.append(_build_custom_input_row(
                _single_line_custom(custom_text, input_budget),
                _on_custom_change, _submit_custom,
            ))

    # 提示行
    # 2026-08-05（增加操作）：提示加入 vim 风格 j/k/g/G 导航（与 ↑↓ 等价）
    # ★ 静态色（修复同标题：提示行不呼吸，避免每帧重绘）
    # ★ 2026-10-21（用户需求）：自定义输入模式专用提示；普通模式提示列表
    #   末尾的「✎ 其他」入口。
    if custom_editing_ref.current and not done and allow_custom:
        hint = " \u270e 输入自定义答案 · Enter 提交 · Esc 返回选项 · Ctrl+G vim 编辑"
        if multi_mode:
            hint += " · Tab 切换"
        if custom_editor_error:
            hint += f" · ! {custom_editor_error}"
    elif multi_mode:
        hint = " Tab/\u2190\u2192 切换"
        if done:
            hint += " · 已提交（锁定）"
        else:
            custom_hint = " · \u270e 其他=自定义" if allow_custom else ""
            if multi:
                hint += " · \u2423 切换选中 · Enter 确认" + custom_hint + " · Esc 取消"
            else:
                hint += " · \u2191\u2193/jk 选择 · g/G 首末 · Enter 确认" + custom_hint + " · Esc 取消"
    elif multi:
        custom_hint = " · \u270e 其他=自定义" if allow_custom else ""
        hint = " \u2423 切换选中 · Enter 确认" + custom_hint + " · Esc 取消"
    else:
        custom_hint = " · \u270e 其他=自定义" if allow_custom else ""
        hint = " \u2191\u2193/jk 选择 · g/G 首末 · Enter 确认" + custom_hint + " · Esc 取消"
    hint_style = _S_DESC
    # ★ 窄屏防溢出：提示行单行截断（textWrap="truncate-end"，超宽省略号）——
    #   修复前提示行自动换行拆成两行（窄终端 `Esc 取消` 独立一行视觉错乱）。
    rows.append(h(TEXT, {
        "children": hint,
        "style": hint_style,
        "textWrap": "truncate-end",
        "key": "us-hint",
    }))

    return h(Column, None, rows)


__all__ = ["UserSelectPopup"]
