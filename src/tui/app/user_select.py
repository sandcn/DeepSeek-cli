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
  - ★ 2026-10-21（用户需求：支持用户输入不在选项中的内容并返回）：
    选项列表末尾追加「✎ 其他（自定义输入…）」伪选项——Enter 进入输入编辑
    模式（TextInput 接管字符/退格/方向键），Enter 提交自由输入、Esc 返回
    选项。自由输入结果并入 ``result``，并经 ``custom`` 字段返回（见工具层
    JSON）。
  - ★ 2026-10-22（用户需求：删除 ``timeout`` 参数，改成「不操作界面三分钟
    后才算超时」）：弹窗每收到一次按键/滚动，就把各未提交问题的倒计时重置
    为 3 分钟（``_touch_idle_states`` → ``UserSelectState.touch_activity``；
    共享计时：任一 tab 上的操作刷新全部问题）。因此持续操作永不超时，
    界面空闲 3 分钟才由工具协程判定超时并回退 ``default_options``。外部
    编辑器（Ctrl+G vim）独占终端期间 ``us.typing=True`` 暂停计时（返回后
    补回），避免长文编辑被超时打断。
  - ★ 2026-10-21（用户需求：Ctrl+G 用 vim 输入）：输入模式内按 Ctrl+G
    调用外部编辑器（``EDITOR``，默认 vim）编辑当前答案；返回后回填输入行
    （Enter 确认）。多行内容在输入行以 ``⏎`` 折叠显示，提交/返回 JSON
    保留原始换行。
  - ★ 2026-10-21（用户需求：美化 TUI + 优化操作体验）：
    * 视觉分区：标签栏（多问题）/ 标题 + 细分割线 + 选项列表 + 主/次两行
      提示；标题右侧显示位置 ``(n/total)``、多选 ``已选 k/total``、超时
      倒计时 ``⏱ Ns``（≤10s 变黄）；选中行整行背景高亮。
    * 选项带 ``1.`` 序号，数字键 ``1-9`` 快选：单选立即确认（多问题自动
      推进），多选切换勾选（Enter 再确认）；自定义输入模式数字放行给
      TextInput。
    * 分栏说明：右栏显示每个选项自己的说明（选中亮色/其余 dim），说明
      超出右栏宽度时在列表下方完整显示（最多 3 行）。
    * 导航增强：Home/End 首末、PgUp/PgDn 翻页（共享控件仅在模态弹窗
      ``consumeAll=True`` 时启用，命令补全等调用零回归）。
    * 多选未勾选时按 Enter 仍提交空列表（返回协议不变），提示行明确
      显示「未勾选」；Submit 页未回答项显示默认返回值。
  - ★ 2026-10-21（继续美化 + 优化操作体验）：
    * Submit 页分页：大量并发问题时按可视行数分页，``↑↓/PgUp/PgDn/Home/End``
      滚动，数字键 ``1-9`` 跳回对应问题修改。
    * 长列表指示：列表上方显示 ``▲ 上方 N 项 · ▼ 下方 M 项``（仅隐藏时）。
    * tab 摘要：已回答 tab 显示 ``1 ✓ Python``；取消 ``✗ 取消``、超时
      ``⏱ 超时``；未回答仍 ``○ 标题``。
    * 多选批量键：``a`` 全选/全不选、``c`` 清空。
    * 输入模式快捷键：``Ctrl+U`` 清空、``Ctrl+W`` 删词。
    * 分栏模式新增 dim 表头 ``选项 │ 说明``。
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
import math
import time

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from src.tui._input_layout import _wrap_by_width
from src.tui.app.input_area import _desc_column_width, _truncate_width
from src.tui.app._theme import _S_DIM, _S_SEP
from src.tui.ink import TEXT, h, Column, Row, StyledRun
from src.tui.ink.helpers import truncate_runs_ellipsis
from src.tui.ink.hooks import use_effect, use_ref, use_state, use_input, useMouseInput
from src.tui.ink.widgets.interactive import SelectInput, MultiSelect, TextInput
from src.tui._external_editor import run_external_editor as _run_external_editor

from ._modal_view import empty_modal_frame, use_modal_scope

_logger = logging.getLogger(__name__)

__all__ = ["UserSelectPopup"]

#: 弹窗标题色（亮青加粗，对齐 _S_ACCENT_BOLD）
_S_TITLE = Style(fg=45, bold=True)
#: 说明列 / 提示行静态色（浅蓝 110——弹窗不呼吸）
_S_DESC = Style(fg=110)
#: 未选中项说明（dim 灰）
_S_DESC_DIM = Style(fg=244)
#: 标题右侧元信息
_S_META = Style(fg=244)
#: 倒计时剩余 ≤10s 的警示色
_S_META_WARN = Style(fg=214, bold=True)
#: 分区细分隔线
_S_SEP_LINE = Style(fg=238)
#: 主提示行（亮色）
_S_HINT = Style(fg=252)
#: 选中行序号（亮青 + 背景）
_S_SEL_ACCENT = Style(fg=45, bg=237, bold=True)
#: 选中行正文（亮色 + 背景）
_S_SEL_TEXT = Style(fg=252, bg=237)
#: 选中行补白（背景铺满）
_S_SEL_PAD = Style(bg=237)
#: 未选中序号（暗灰）
_S_NUM = Style(fg=240)
#: 多选已勾选标记（绿色加粗）
_S_MARK_CHECKED = Style(fg=40, bold=True)
#: 多选未勾选标记（dim 灰）
_S_MARK_UNCHECKED = Style(fg=244)
#: 自定义输入伪选项提示色
_S_CUSTOM = Style(fg=110)
#: 轻度警示（多选未勾选等）
_S_WARN = Style(fg=214)
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


def _submit_rows_limit() -> int:
    """Submit 页可见问题行数（标题 1 + 两条分割线 + 两条提示固定占 5 行）。"""
    return max(1, _popup_item_rows() - 5)


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


def _num_width(total: int) -> int:
    """选项序号列宽（按最大序号位数；至少 1）。"""
    return max(1, len(str(max(1, int(total)))))


def _checked_indices(us, total: int) -> list:
    """当前多选勾选索引（过滤越界/非整数，升序去重）。"""
    out: set = set()
    for raw in list(getattr(us, "checked", []) or []):
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < total:
            out.add(idx)
    return sorted(out)


def _option_prefix(
    i: int, total: int, multi: bool, checked: list, cur: int,
) -> tuple[str, str]:
    """选项行前缀：``序号. `` + ``标记 ``（标记：单选 ▶／空格，多选 ●/○）。"""
    num = f"{i + 1:>{_num_width(total)}}. "
    if multi:
        mark = _CHECKED if i in checked else _UNCHECKED
    else:
        mark = "\u25b6 " if i == cur else "  "
    return num, mark


def _option_runs(
    num_text: str, mark_text: str, label: str, *,
    num_style: Style, mark_style: Style,
    text_style: Style | None, target_w: int, pad_style: Style | None,
) -> list:
    """构建选项行 styled runs（序号 + 标记 + 文本 + 可选补白）。

    每个 run 的样式分开表达（序号/标记/正文可以有不同颜色）；``target_w``
    为整行显示宽度预算，超长文本按显示宽度截断（不拆 CJK），选中行可用
    ``pad_style`` 把背景铺满整行（整行高亮）。
    """
    target_w = max(0, int(target_w))
    prefix_w = wcswidth_simple(num_text) + wcswidth_simple(mark_text)
    if prefix_w > target_w:
        # 极端窄终端：前缀自身已超预算——合并截断为单个 run（不溢出）。
        prefix = _truncate_width(num_text + mark_text, target_w)
        return [StyledRun(prefix, num_style)] if prefix else []
    runs: list = [
        StyledRun(num_text, num_style),
        StyledRun(mark_text, mark_style),
    ]
    used = prefix_w
    if label:
        budget = max(0, target_w - used)
        if wcswidth_simple(label) > budget:
            label = _truncate_width(label, budget)
        if label:
            runs.append(StyledRun(label, text_style))
            used += wcswidth_simple(label)
    if pad_style is not None and target_w > used:
        runs.append(StyledRun(" " * (target_w - used), pad_style))
    return runs


def _build_custom_row(is_sel: bool, opt_w: int, total: int) -> object:
    """自定义输入伪选项行（普通模式，无序号、无勾选标记）。

    Args:
        is_sel: 是否当前高亮。
        opt_w: 选项行宽度预算。
        total: 选项总数（用于序号列宽对齐）。
    """
    num_w = _num_width(total)
    num = " " * (num_w + 2)
    mark = "  "
    runs = _option_runs(
        num, mark, _CUSTOM_LABEL,
        num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        mark_style=_S_CUSTOM,
        text_style=_S_SEL_TEXT if is_sel else _S_CUSTOM,
        target_w=max(1, opt_w),
        pad_style=_S_SEL_PAD if is_sel else None,
    )
    return h(TEXT, {
        "styled": runs, "height": 1, "key": "row-custom",
        "textWrap": "truncate-end",
    })


def _build_custom_split_row(
    is_sel: bool, opt_w: int, desc_w: int, total: int,
) -> object:
    """自定义输入伪选项行（分栏说明模式：左栏选项 + │ + 右栏用途说明）。"""
    num_w = _num_width(total)
    num = " " * (num_w + 2)
    mark = "  "
    left_runs = _option_runs(
        num, mark, _CUSTOM_LABEL,
        num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        mark_style=_S_CUSTOM,
        text_style=_S_SEL_TEXT if is_sel else _S_CUSTOM,
        target_w=max(1, opt_w),
        pad_style=_S_SEL_PAD if is_sel else _S_DIM,
    )
    desc_txt = _truncate_width("输入不在选项中的答案", desc_w)
    return h(Row, {"height": 1}, [
        h(TEXT, {"styled": left_runs, "height": 1}),
        h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
        h(TEXT, {
            "children": desc_txt,
            "style": _S_DESC if is_sel else _S_DESC_DIM,
            "height": 1,
        }),
    ])


def _build_custom_input_row(
    text: str, on_change, on_submit, rev: int = 0,
) -> object:
    """自定义输入编辑行（TextInput 受控；输入模式下渲染在选项列表之后）。

    ``rev`` 为外部重置版本号（Ctrl+U/Ctrl+W/外部编辑器回填时递增）——用作
    TextInput 的 key 强制重挂载，使半受控控件的内部缓冲同步为新的 ``value``
    （否则清除后继续输入会拼到旧文本上）。
    """
    return h(Row, {"height": 1}, [
        h(TEXT, {
            "children": " \u270e ", "style": _S_SEL_ACCENT, "height": 1,
        }),
        h(TextInput, {
            "key": f"us-custom-input-{rev}",
            "value": text,
            "onChange": on_change,
            "onSubmit": on_submit,
            "focus": True,
            "placeholder": "输入自定义答案后 Enter 提交",
        }),
    ])


def _build_header(
    us, cur: int, total: int, multi: bool, checked_count: int,
    done: bool, width: int,
) -> object:
    """标题行：``▍ 模式图标 标题`` + 右侧元信息（位置/已选/倒计时）。

    窄屏时优先保留标题：先按剩余宽度截断标题，仍放不下则丢弃右侧元信息；
    极窄终端（宽度不足以容纳前缀）只渲染截断后的前缀——保证行宽 ≤ width。
    """
    title = getattr(us, "title", "") or "选择"
    mode_icon = "\u2611" if multi else "\u25b6"
    prefix = f" \u258d {mode_icon} "
    now = time.monotonic()
    warn = False
    if done:
        meta = "\u2713 已确认"
        meta_style = _S_DONE_RESULT
    else:
        pos = f"({cur + 1}/{total})" if cur < total else "自定义"
        pieces = [pos]
        if multi:
            pieces.append(f"已选 {checked_count}/{total}")
        deadline = float(getattr(us, "deadline", 0.0) or 0.0)
        if deadline > 0 and not getattr(us, "typing", False):
            remain = max(0, int(math.ceil(deadline - now)))
            pieces.append(f"\u23f1 {remain}s")
            warn = remain <= 10
        meta = " · ".join(pieces)
        meta_style = _S_META_WARN if warn else _S_META
    width = int(width) if width and width > 0 else 80
    prefix_w = wcswidth_simple(prefix)
    if width <= prefix_w:
        prefix = _truncate_width(prefix, width)
        return h(TEXT, {
            "children": prefix, "style": _S_TITLE, "height": 1,
            "key": "us-title", "textWrap": "truncate-end",
        })
    meta_w = wcswidth_simple(meta)
    title_budget = width - prefix_w - meta_w - 1
    if meta and title_budget < 4:
        meta = ""
        meta_w = 0
        title_budget = width - prefix_w
    title_show = _truncate_width(title, max(0, title_budget))
    left_w = prefix_w + wcswidth_simple(title_show)
    runs = [StyledRun(prefix, _S_TITLE), StyledRun(title_show, _S_TITLE)]
    if meta:
        gap = width - left_w - meta_w
        if gap > 0:
            runs.append(StyledRun(" " * gap, _S_DIM))
            runs.append(StyledRun(meta, meta_style))
    else:
        # 无元信息时标题可以有更多空间：重新按整行宽度截断
        title_show = _truncate_width(title, max(0, width - prefix_w))
        runs = [StyledRun(prefix, _S_TITLE), StyledRun(title_show, _S_TITLE)]
    return h(TEXT, {
        "styled": truncate_runs_ellipsis(runs, width),
        "height": 1,
        "key": "us-title",
        "textWrap": "truncate-end",
    })


def _build_separator(width: int, key: str = "us-sep") -> object:
    """分区细分隔线（``─`` 铺满可用宽度，极窄/无宽度时零宽）。"""
    w = max(0, int(width) if width else 0)
    if w <= 0:
        return h(TEXT, {"children": "", "height": 1, "key": key})
    return h(TEXT, {
        "children": "\u2500" * w,
        "style": _S_SEP_LINE,
        "height": 1,
        "key": key,
        "textWrap": "truncate-end",
    })


def _build_scroll_indicator(above: int, below: int, width: int) -> object | None:
    """长列表滚动指示行（``▲ 上方 N 项 · ▼ 下方 M 项``，仅隐藏时显示）。"""
    parts: list = []
    if above > 0:
        parts.append(f"\u25b2 上方 {above} 项")
    if below > 0:
        parts.append(f"\u25bc 下方 {below} 项")
    if not parts:
        return None
    return h(TEXT, {
        "children": _truncate_width(
            " " + " · ".join(parts), max(1, int(width)),
        ),
        "style": _S_DESC_DIM,
        "height": 1,
        "key": "us-scroll-indicator",
        "textWrap": "truncate-end",
    })


def _build_split_header(opt_w: int, desc_w: int) -> object:
    """分栏模式表头（dim）：``选项 │ 说明``（左栏补齐到 ``opt_w``）。"""
    left_text = _truncate_width(" 选项", max(1, int(opt_w)))
    pad = max(0, int(opt_w) - wcswidth_simple(left_text))
    return h(Row, {"height": 1}, [
        h(TEXT, {
            "children": left_text + " " * pad,
            "style": _S_DESC_DIM,
            "height": 1,
        }),
        h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
        h(TEXT, {
            "children": _truncate_width(" 说明", max(1, int(desc_w))),
            "style": _S_DESC_DIM,
            "height": 1,
        }),
    ])


def _build_tab_bar(states: list, active: int, width: int) -> object:
    """并发 tab 栏（Claude AskUserQuestion 风格，含 Submit tab）。

    问题 tab：``1 ○ 标题``（未回答）／``1 ✓ 标题``（已回答/已提交）；
    当前焦点 tab 整段亮青加粗 + 背景高亮。行尾 **Submit tab**：
    ``[✓ 提交]``（全部已回答）／``[提交]``（有未回答）。

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
    submit_label = "[\u2713 提交]" if all_answered else "[提交]"
    submit_w = wcswidth_simple(submit_label) + 2
    avail_w = max(1, int(width) if width and width > 0 else 80)
    # 预算恒等式：Σlabel + gap*n + submit_w <= width（每个 label 经
    # ``_truncate_width`` 限制在 budget 内）——窄终端/多问题不溢出。
    # 间距按 2 → 1 → 0 递减；仍放不下可读标签时退化为「当前位置/总数 +
    # 提交」紧凑模式（避免大量并发问题时 tab 栏超宽换行）。
    budget = 0
    gap = 2
    for candidate in (2, 1, 0):
        budget = (avail_w - submit_w - candidate * n) // n
        if budget >= 3:
            gap = candidate
            break
    if budget < 3:
        pos = f"{min(active + 1, n)}/{n}" if active < n else f"\u2713 {n}/{n}"
        compact = _truncate_width(f"{pos}  {submit_label}", avail_w)
        style = _S_TAB_ACTIVE if active <= n else _S_TAB_INACTIVE
        return h(Row, {"height": 1}, [
            h(TEXT, {
                "children": compact, "style": style, "height": 1,
                "key": "tab-compact",
            }),
        ])
    gap_w = gap * n  # 问题间（含 Submit 前）间距
    children: list = []
    for i, s in enumerate(states):
        marked = getattr(s, "answered", False) or getattr(s, "done", False)
        if marked:
            action = str(getattr(s, "action", "") or "")
            if action == "cancel":
                mark, inner = "\u2717", "取消"
            elif action == "timeout":
                mark, inner = "\u23f1", "超时"
            else:
                mark = "\u2713"
                inner = "、".join(
                    str(x).replace("\r\n", "\n").replace("\r", "\n").replace(
                        "\n", _NEWLINE_MARK,
                    )
                    for x in (getattr(s, "result", None) or [])
                ) or "已答"
                if getattr(s, "custom", ""):
                    inner += " \u270e"
        else:
            mark = "\u25cb"
            inner = getattr(s, "title", "") or "选择"
        label = _truncate_width(f"{i + 1} {mark} {inner}", budget)
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
        if gap:
            children.append(h(TEXT, {"children": " " * gap, "height": 1}))
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
    """普通模式单个选项行构建（序号 + 单选高亮/多选勾选，单行）。

    序号（``1.``）与数字键快选呼应；选中行整行背景高亮（含补白）。选项
    超长截断到 ``opt_w``（CJK 安全，防超宽行破坏行级 diff 宽度不变量）。

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
    is_sel = i == cur
    num, mark = _option_prefix(i, len(options), multi, checked, cur)
    if multi:
        mark_style = _S_SEL_ACCENT if is_sel else (
            _S_MARK_CHECKED if i in checked else _S_MARK_UNCHECKED
        )
    else:
        mark_style = _S_SEL_ACCENT if is_sel else _S_NUM
    runs = _option_runs(
        num, mark, str(options[i]),
        num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        mark_style=mark_style,
        text_style=_S_SEL_TEXT if is_sel else None,
        target_w=max(1, opt_w),
        pad_style=_S_SEL_PAD if is_sel else None,
    )
    return h(TEXT, {
        "styled": runs, "height": 1, "key": f"row-{i}",
        "textWrap": "truncate-end",
    })


def _build_split_row(
    us, options: list, multi: bool, cur: int, checked: list,
    opt_w: int, desc_w: int, row_i: int, total: int,
) -> object:
    """分栏说明模式单行构建（左栏选项 + │ + 右栏该选项自己的说明）。

    与旧版「右栏只显示当前选中项说明」不同：每一行右栏显示**该选项
    自己的说明**（单行截断，选中行亮色、其余 dim），列表下方再完整显示
    选中项说明（由调用方构建，见 ``_selected_desc_lines``）。选项超长
    受左栏宽度约束（右栏至少保留 ``_desc_column_width`` 基准宽）。

    Args:
        us: UserSelectState（当前 tab）。
        options: 选项字符串列表。
        multi: 是否多选。
        cur: 当前高亮索引（已钳制）。
        checked: 多选勾选索引列表。
        opt_w: 左栏宽。
        desc_w: 右栏宽。
        row_i: 选项行号（0-based）。
        total: 选项总数。

    Returns:
        Row 元素（左栏 + │ + 右栏说明）。
    """
    descs = us.option_descriptions or []
    is_sel = row_i == cur
    num, mark = _option_prefix(row_i, total, multi, checked, cur)
    if multi:
        mark_style = _S_SEL_ACCENT if is_sel else (
            _S_MARK_CHECKED if row_i in checked else _S_MARK_UNCHECKED
        )
    else:
        mark_style = _S_SEL_ACCENT if is_sel else _S_NUM
    left_runs = _option_runs(
        num, mark, str(options[row_i]),
        num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        mark_style=mark_style,
        text_style=_S_SEL_TEXT if is_sel else None,
        target_w=max(1, opt_w),
        pad_style=_S_SEL_PAD if is_sel else _S_DIM,
    )
    desc = descs[row_i] if row_i < len(descs) else ""
    desc_txt = _truncate_width(str(desc or ""), max(1, desc_w))
    return h(Row, {"height": 1}, [
        h(TEXT, {"styled": left_runs, "height": 1}),
        h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
        h(TEXT, {
            "children": desc_txt,
            "style": _S_DESC if is_sel else _S_DESC_DIM,
            "height": 1,
            "textWrap": "truncate-end",
        }),
    ])


def _selected_desc_lines(
    us, cur: int, total: int, width: int, desc_w: int,
) -> list:
    """选中项完整说明的换行行（最多 3 行；无需额外显示时返回空列表）。

    右栏只显示单行摘要；说明超过右栏宽度（或本身含换行）时，在列表
    下方完整显示（避免右栏截断丢失信息）。
    """
    if cur < 0 or cur >= total:
        return []
    descs = us.option_descriptions or []
    if cur >= len(descs):
        return []
    desc = str(descs[cur] or "")
    if not desc:
        return []
    if "\n" not in desc and wcswidth_simple(desc) <= max(1, desc_w):
        return []
    lines = _wrap_by_width(desc, max(1, int(width) - 2))
    return lines[:3]


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


def _touch_idle_states(states: list, now: "float | None" = None) -> None:
    """空闲计时：界面每发生一次操作，重置各问题的「3 分钟无操作」倒计时。

    ★ 2026-10-22（用户需求：删除 user_select 的 ``timeout`` 参数，改成
    「不操作界面三分钟后才算超时」）：工具路径构造的 state 带
    ``idle_timeout``（``IDLE_TIMEOUT_SECONDS`` = 180s）——本函数对**每个**
    未提交（未 done）的此类 state 调用 ``touch_activity``，把 deadline
    顺延为 ``now + idle_timeout``。并发多问题时采用**共享计时**（用户确认
    的方案）：任一 tab 上的一次操作都会重置全部未提交问题——界面整体
    空闲 3 分钟才算超时。

    ``idle_timeout`` 为 0 的固定 deadline 调用方（命令系统选择
    ``CommandUiAdapter`` 的 60s 计时）不受影响——``touch_activity`` 返回
    False，deadline 保持不变（零回归）。

    Args:
        states: 当前弹窗问题 state 列表（并发队列；None 元素跳过）。
        now: 当前 ``time.monotonic()``（测试可注入；None 取实时值）。
    """
    for state in list(states or []):
        if state is None:
            continue
        touch = getattr(state, "touch_activity", None)
        if touch is None:
            continue
        try:
            touch(now)
        except Exception:
            _logger.debug("user_select: 空闲计时刷新失败", exc_info=True)


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
    submit_scroll_ref=None, set_submit_scroll=None, submit_rows_limit=None,
) -> bool:
    """顶层 tab 路由：Submit 页 Enter/Esc/跳题/滚动 + Tab/←/→ 切换（模块级）。

    父组件 use_input 先于子控件（SelectInput/MultiSelect/TextInput）被
    router 调用（前序收集）——tab/←/→ 被本 handler 消费，↑↓/jk 等放行给
    控件。

    Args:
        input_mode: 当前是否处于自定义输入编辑模式。输入模式下 ←/→ 必须
            放行给 TextInput 移动光标（不得切换问题 tab）；Tab 仍切换问题，
            但切换前调用 ``exit_custom`` 退出输入并清空本题缓冲（避免输入
            内容串到下一题）。
        exit_custom: 退出自定义输入模式的回调（无参）。
        submit_scroll_ref: Submit 页滚动偏移 ref。
        set_submit_scroll: Submit 页滚动偏移 state setter。
        submit_rows_limit: Submit 页当前可见问题行数（用于滚动步长/边界）。
    """
    if not visible:
        return False
    # Submit 页：Enter 提交全部 / Esc 返回问题 / 数字跳题 / 上下翻页滚动
    if is_submit:
        if event.kind == "enter":
            _submit_all(states)
            return True
        if event.kind == "escape":
            _back_to_questions(model, active_ref, set_active)
            return True
        # ★ 2026-10-21（继续优化）：数字键 1-9 跳到对应问题修改。
        if (
            event.kind == "char"
            and not getattr(event, "modifier", 0)
            and submit_scroll_ref is not None
        ):
            ch = getattr(event, "char", "") or ""
            if len(ch) == 1 and ch in "123456789":
                idx = int(ch) - 1
                if 0 <= idx < min(9, len(states)):
                    active_ref.current = idx
                    set_active(idx)
                    return True
        # ★ 2026-10-21（继续优化）：↑↓/PgUp/PgDn/Home/End 滚动 Submit 页。
        if (
            event.kind in (
                "arrow_up", "arrow_down", "page_up", "page_down", "home", "end",
            )
            and submit_scroll_ref is not None
        ):
            try:
                rows = max(
                    1, int(submit_rows_limit) if submit_rows_limit else
                    _submit_rows_limit(),
                )
            except (TypeError, ValueError, OverflowError):
                rows = _submit_rows_limit()
            max_scroll = max(0, len(states) - rows)
            try:
                cur_scroll = max(
                    0, min(int(submit_scroll_ref.current or 0), max_scroll),
                )
            except (TypeError, ValueError, OverflowError):
                cur_scroll = 0
            if event.kind == "home":
                new_scroll = 0
            elif event.kind == "end":
                new_scroll = max_scroll
            elif event.kind == "arrow_up":
                new_scroll = cur_scroll - 1
            elif event.kind == "arrow_down":
                new_scroll = cur_scroll + 1
            elif event.kind == "page_up":
                new_scroll = cur_scroll - rows
            else:
                new_scroll = cur_scroll + rows
            new_scroll = max(0, min(new_scroll, max_scroll))
            if new_scroll != cur_scroll:
                submit_scroll_ref.current = new_scroll
                if set_submit_scroll is not None:
                    set_submit_scroll(new_scroll)
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
    edit_custom=None, clear_custom=None, delete_word=None,
) -> bool:
    """自定义输入编辑模式的顶层按键（模块级）。

    接管：
      - ``Ctrl+G``（``\\x07``）：调用外部编辑器（``EDITOR``，默认 vim）
        编辑当前自定义答案；回填后仍停留在输入模式（Enter 确认）。
      - ``Ctrl+U``（``\\x15``）：清空当前输入。
      - ``Ctrl+W``（``\\x17``）：删除光标前一个词。
      - ``Esc``：退出输入、返回选项（**不取消问题**——再次 Esc 才由
        SelectInput/MultiSelect 的 onCancel 取消）。

    字符/退格/方向键/Enter 全部放行给 TextInput（输入行）处理。

    Args:
        edit_custom: Ctrl+G 回调（无参）；None 时消费但不动作（防御）。
        clear_custom: Ctrl+U 回调（无参）。
        delete_word: Ctrl+W 回调（无参）。
    """
    if not visible or not input_mode:
        return False
    # ★ 真实终端解析：Ctrl+W → ``delete`` modifier=1；Ctrl+U →
    #   ``delete`` modifier=2（见 InputParser._decode_control_char）。
    if getattr(event, "kind", "") == "delete":
        mod = int(getattr(event, "modifier", 0) or 0)
        if mod == 1:
            if delete_word is not None:
                delete_word()
            return True
        if mod == 2:
            if clear_custom is not None:
                clear_custom()
            return True
        return False
    ch = getattr(event, "char", "") or ""
    if ch == "\x07":
        if edit_custom is not None:
            edit_custom()
        return True
    if ch == "\x15":
        if clear_custom is not None:
            clear_custom()
        return True
    if ch == "\x17":
        if delete_word is not None:
            delete_word()
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


def _handle_number_event(
    event, *, visible: bool, is_submit: bool, input_mode: bool, multi: bool,
    total: int, options: list, us, on_confirm, on_toggle,
    set_selected=None,
) -> bool:
    """数字键快选（1-9）：单选立即确认，多选切换勾选（模块级）。

    ★ 2026-10-21（用户需求：美化 TUI + 优化操作体验）：选项行显示 ``1.``
    序号后，数字键成为最直接的选择方式：
      - 单选：按下数字即选中该项并确认（多问题模式自动推进下一题）；
      - 多选：按下数字切换该项勾选状态（需再按 Enter 确认）；
      - 自定义输入模式：**放行**（数字作为文本输入，不触发快选）；
      - Submit 页 / 越界序号（> 选项数或 > 9）：不消费（返回 False）。

    父组件 ``use_input`` 先于子控件（SelectInput/MultiSelect/TextInput）
    被 router 调用——数字键在此消费后不会落入控件/输入缓冲。
    """
    if not visible or is_submit or input_mode or us is None:
        return False
    # 已提交终态（done tab 保留显示）：数字键不再改变任何状态。
    if getattr(us, "done", False):
        return False
    if getattr(event, "kind", "") != "char":
        return False
    if getattr(event, "modifier", 0):
        return False
    ch = getattr(event, "char", "") or ""
    if len(ch) != 1 or ch not in "123456789":
        return False
    idx = int(ch) - 1
    if idx < 0 or idx >= total or idx >= len(options):
        return False
    if multi:
        if on_toggle is None:
            return False
        on_toggle(idx)
        return True
    if on_confirm is None:
        return False
    us.selected = idx
    if set_selected is not None:
        set_selected(idx)
    on_confirm({"label": str(options[idx]), "value": options[idx]})
    return True


def _handle_batch_event(
    event, *, visible: bool, is_submit: bool, input_mode: bool, multi: bool,
    total: int, us, on_toggle_all=None, on_clear=None,
) -> bool:
    """多选批量键：``a`` 全选/全不选，``c`` 清空（模块级）。

    仅单选问题页、非输入模式、多选状态生效；输入模式下放行给 TextInput
    （用户可输入字母 a/c）。
    """
    if not visible or is_submit or input_mode or not multi or us is None:
        return False
    if getattr(us, "done", False) or total <= 0:
        return False
    if getattr(event, "kind", "") != "char":
        return False
    if getattr(event, "modifier", 0):
        return False
    ch = getattr(event, "char", "") or ""
    if ch in ("a", "A"):
        if on_toggle_all is None:
            return False
        on_toggle_all()
        return True
    if ch in ("c", "C"):
        if on_clear is None:
            return False
        on_clear()
        return True
    return False


def _build_submit_page(
    states: list, width: int, scroll: int = 0, max_rows: int | None = None,
) -> list:
    """Submit 页行构建（标题 + 每题状态 + 提示；支持滚动窗口）。

    ★ 2026-08-19（用户需求：增加一个 tab 页面给玩家是否提交）。
    ★ 2026-10-21（美化）：每题显示序号与 ``✓/○`` 标记；未回答项显示
    「未回答（默认：…）」，让用户在提交前明确知道会返回什么。
    ★ 2026-10-21（继续优化）：大量并发问题时按 ``max_rows`` 分页显示，
    ``↑↓/PgUp/PgDn`` 滚动；数字键 1-9 跳题（由 ``_handle_tab_event`` 处理）。
    """
    try:
        width_i = max(1, int(width))
    except (TypeError, ValueError, OverflowError):
        width_i = 80
    n = len(states)
    visible = n if max_rows is None else max(1, int(max_rows))
    visible = max(1, min(visible, n)) if n else 1
    max_scroll = max(0, n - visible)
    try:
        start = int(scroll or 0)
    except (TypeError, ValueError, OverflowError):
        start = 0
    start = max(0, min(start, max_scroll))
    end = min(n, start + visible)
    answered_count = sum(
        1 for s in states
        if getattr(s, "answered", False) or getattr(s, "done", False)
    )
    rows: list = []
    rows.append(h(TEXT, {
        "children": " \u258d \u2713 提交全部答案",
        "style": _S_TITLE,
        "textWrap": "truncate-end",
        "key": "us-submit-title",
    }))
    rows.append(_build_separator(width_i, key="us-submit-sep"))
    for i in range(start, end):
        s = states[i]
        answered = getattr(s, "answered", False)
        done = getattr(s, "done", False)
        title_txt = getattr(s, "title", "") or "选择"
        action = str(getattr(s, "action", "") or "")
        if answered or done:
            if action == "cancel":
                mark, status, style = "\u2717", "\u2717 取消", _S_TAB_ANSWERED
            elif action == "timeout":
                mark, status, style = "\u23f1", "\u23f1 超时", _S_TAB_ANSWERED
            else:
                result_txt = "、".join(
                    str(x).replace("\r\n", "\n").replace("\r", "\n").replace(
                        "\n", _NEWLINE_MARK,
                    )
                    for x in (getattr(s, "result", None) or [])
                ) or "（无）"
                # ★ 2026-10-21（用户需求）：自由输入标记（提交页汇总可见）。
                custom_txt = str(getattr(s, "custom", "") or "")
                status = f"\u2713 {result_txt}" + (
                    "（自定义）" if custom_txt else ""
                )
                mark, style = "\u2713", _S_DONE_RESULT
        else:
            default_txt = "、".join(
                str(x).replace("\r\n", "\n").replace("\r", "\n").replace(
                    "\n", _NEWLINE_MARK,
                )
                for x in (getattr(s, "default_options", None) or [])
            )
            status = (
                f"未回答（默认：{default_txt}）" if default_txt
                else "未回答（将返回空）"
            )
            mark = "\u25cb"
            style = _S_WARN if default_txt else _S_DESC
        line = f" {i + 1}. [{mark}] {title_txt} \u2192 {status}"
        rows.append(h(TEXT, {
            "children": _truncate_width(line, width_i),
            "style": style,
            "height": 1,
            "key": f"us-submit-row-{i}",
        }))
    rows.append(_build_separator(width_i, key="us-submit-sep2"))
    rows.append(h(TEXT, {
        "children": _truncate_width(
            " Enter 提交全部 · Esc 返回修改 · 1-9 跳题 · Tab/←→ 切换",
            width_i,
        ),
        "style": _S_HINT,
        "height": 1,
        "key": "us-submit-hint",
        "textWrap": "truncate-end",
    }))
    if max_scroll > 0:
        secondary = (
            f" 已答 {answered_count}/{n} · 显示 {start + 1}-{end}/{n}"
            " · ↑↓/PgUp/PgDn 滚动"
        )
    else:
        secondary = (
            f" 已答 {answered_count}/{n} · 未回答项将使用默认选项（若有）"
        )
    rows.append(h(TEXT, {
        "children": _truncate_width(secondary, width_i),
        "style": _S_DESC,
        "height": 1,
        "key": "us-submit-hint2",
        "textWrap": "truncate-end",
    }))
    return rows


def _build_hints(
    us, *, multi: bool, allow_custom: bool, multi_mode: bool,
    custom_editing: bool, editor_error: str, done: bool, checked_count: int,
) -> tuple[str, str]:
    """主/次两行提示（主操作亮色，次要快捷键 dim）。

    ★ 2026-10-21（美化）：提示分层——第一行放「确认/取消/提交」等主操作，
    第二行放导航/快选等次要快捷键；窄屏时第二行优先被截断（主操作始终
    可见）。既有文案 ``✎ 其他=自定义``、``Ctrl+G vim 编辑`` 原样保留
    （测试与用户肌肉记忆依赖）。
    """
    if custom_editing and not done and allow_custom:
        primary = " \u270e 输入自定义答案 · Enter 提交 · Esc 返回选项 · Ctrl+G vim 编辑"
        if editor_error:
            primary += f" · ! {editor_error}"
        secondary = " Ctrl+U 清空 · Ctrl+W 删词"
        if multi_mode:
            secondary += " · Tab 切换问题"
        return primary, secondary
    if done:
        return " \u2713 已提交（锁定）", ""
    if multi:
        primary = " Space 勾选 · Enter 确认 · Esc 取消"
        if checked_count <= 0:
            primary = " Space 勾选（未勾选） · Enter 提交空 · Esc 取消"
        if allow_custom:
            primary += " · \u270e 其他=自定义"
        secondary = (
            " \u2191\u2193/jk 移动 · 1-9 勾选 · a 全选 · c 清空"
            " · g/G/Home/End · PgUp/PgDn"
        )
        if multi_mode:
            secondary = " Tab/\u2190\u2192 切换 ·" + secondary
        return primary, secondary
    primary = " Enter 确认 · Esc 取消"
    if allow_custom:
        primary += " · \u270e 其他=自定义"
    secondary = " \u2191\u2193/jk 移动 · g/G/Home/End 首末 · PgUp/PgDn 翻页 · 1-9 快选"
    if multi_mode:
        secondary = " Tab/\u2190\u2192 切换 ·" + secondary
    return primary, secondary


def UserSelectPopup(props) -> object:
    """React Ink 用户选择弹窗组件（多问题 tab，SelectInput/MultiSelect 控件化）。

    ★ 2026-10-21（用户需求）：支持用户输入列表外的自定义答案——状态
    ``us.allow_custom`` 为 True 时列表末尾渲染「✎ 其他（自定义输入…）」
    伪选项；Enter 进入输入编辑模式（选项控件失焦、TextInput 接管），
    Enter 提交 / Esc 返回；输入模式内 Ctrl+G 调用外部编辑器（``EDITOR``，
    默认 vim，多行以 ``⏎`` 折叠显示、返回保留换行）。
    ★ 2026-10-22：``us.typing`` 仅在外部编辑器运行期间为 True（暂停空闲
    计时）；弹窗内正常打字/导航由每次按键重置 3 分钟空闲倒计时。

    ★ 2026-10-21（继续优化）：Submit 页分页滚动 + 数字键 1-9 跳题；长列表
    显示上方/下方隐藏项数；tab 显示回答摘要；多选 `a` 全选/`c` 清空；
    输入模式 `Ctrl+U` 清空 / `Ctrl+W` 删词；分栏模式显示 `选项 │ 说明` 表头。

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
    # 外部重置版本号（Ctrl+U/Ctrl+W/编辑器回填）——TextInput key 强制重挂载。
    custom_input_rev, set_custom_input_rev = use_state(0)
    custom_editing_ref = use_ref(False)
    custom_editing_ref.current = custom_editing
    # ★ 2026-10-21（Ctrl+G vim 输入）：原始文本 ref（事件期权威源；可含换行，
    #   与单行显示分离）与编辑器错误提示 state。
    custom_text_ref = use_ref("")
    custom_editor_error, set_custom_editor_error = use_state("")
    # ★ 2026-10-21（继续优化）：Submit 页滚动偏移 + 当前列表可见窗口
    #   （offset, count）——Submit 页大量问题时滚动；列表上方显示隐藏项数。
    submit_scroll, set_submit_scroll = use_state(0)
    submit_scroll_ref = use_ref(0)
    prev_is_submit_ref = use_ref(False)
    list_window, set_list_window = use_state((0, 0))
    list_window_ref = use_ref((0, 0))
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
    # ★ 2026-10-21（继续优化）：进入 Submit 页时滚动回到顶部。
    if is_submit and not prev_is_submit_ref.current:
        submit_scroll_ref.current = 0
        if submit_scroll != 0:
            set_submit_scroll(0)
    prev_is_submit_ref.current = is_submit

    us = states[cur_active] if (states and not is_submit) else None
    prev_us = us_ref.current
    fresh_us = prev_us is not us
    if fresh_us:
        us_ref.current = us
        # ★ 2026-10-21（用户需求）：切换问题（手动 tab / 自动推进 / 并发
        #   新 tab 导致焦点变化）时退出自定义输入并清空缓冲——避免输入
        #   内容串到下一题；同时复位旧 state 的 typing（清除外部编辑器
        #   暂停标志，空闲计时恢复正常）。
        if custom_editing_ref.current:
            custom_editing_ref.current = False
            set_custom_editing(False)
            set_custom_text("")
        # ★ 2026-10-21：切题/自动推进时清空输入原文与编辑器错误提示。
        custom_text_ref.current = ""
        set_custom_editor_error("")
        # ★ 2026-10-21（继续优化）：切题时列表窗口回到顶部（新控件上报后覆盖）。
        if list_window_ref.current != (0, 0):
            list_window_ref.current = (0, 0)
            set_list_window((0, 0))
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

    # ── 空闲计时（2026-10-22 用户需求：删除 ``timeout`` 参数，改成「不操作
    #   界面三分钟后才算超时」）──
    # 弹窗每收到一次按键/鼠标操作，就把各未提交问题的倒计时重置为 3 分钟
    # （共享计时：任一 tab 上的操作都刷新全部，见 ``_touch_idle_states``）。
    # handler 恒返回 False（不消费事件），只刷新计时——父组件 use_input 先于
    # 子控件被 router 调用（前序收集），本次按键随后仍由后续 handler / 控件
    # 正常处理（导航/确认/取消/自定义输入）。
    def _on_any_input(_event) -> bool:
        _touch_idle_states(states)
        return False

    use_input(_on_any_input, visible)
    useMouseInput(_on_any_input, {"isActive": visible})

    # ══════════════ 自定义输入（2026-10-21 用户需求）══════════════
    # 列表末尾伪装项「✎ 其他（自定义输入…）」Enter 进入编辑模式：
    #   - 弹窗选项控件失焦（focus=False），TextInput 接管字符/退格/方向键；
    #   - 父 handler 只消费 Esc（退出输入）与 Tab（切换问题并退出输入）；
    #   - ★ 2026-10-22：输入期间每次按键都重置 3 分钟空闲倒计时（不再整段
    #     暂停超时）；只有外部编辑器（vim）运行期间 ``us.typing=True``
    #     暂停计时，返回后补回。
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

    def _toggle_checked(idx: int) -> None:
        """数字键切换多选勾选（同步 us.checked + 高亮，控件受控回显）。"""
        if us is None:
            return
        checked_now = _checked_indices(us, total)
        if idx in checked_now:
            checked_now.remove(idx)
        else:
            checked_now.append(idx)
            checked_now.sort()
        us.checked = checked_now
        us.selected = idx
        set_selected(idx)

    def _toggle_all_checked() -> None:
        """``a``：全选 / 全不选（当前问题；自定义伪选项不参与）。"""
        if us is None:
            return
        all_idx = set(range(total))
        checked_now = set(_checked_indices(us, total))
        us.checked = [] if checked_now == all_idx else sorted(all_idx)

    def _clear_checked() -> None:
        """``c``：清空当前问题勾选（自定义伪选项不参与）。"""
        if us is None:
            return
        us.checked = []

    def _on_window_change(offset, count, _total) -> None:
        """控件可见窗口变化：更新「上方/下方还有 N 项」指示。"""
        try:
            new = (int(offset), int(count))
        except (TypeError, ValueError):
            return
        if new != list_window_ref.current:
            list_window_ref.current = new
            set_list_window(new)

    def _can_toggle(idx, item) -> bool:
        """自定义输入伪选项行不可勾选（不是可提交的选项）。"""
        return not (allow_custom and idx >= total)

    def _exit_custom(clear: bool = True) -> None:
        """退出自定义输入编辑模式（恢复选项控件焦点；清空未提交缓冲）。

        ``us.typing`` 一并复位——外部编辑器异常退出等路径不会残留暂停标志
        （空闲计时恢复正常）。
        """
        custom_editing_ref.current = False
        set_custom_editing(False)
        if clear:
            set_custom_text("")
            custom_text_ref.current = ""
        set_custom_editor_error("")
        if us is not None:
            us.typing = False

    def _enter_custom() -> None:
        """进入自定义输入编辑模式（空缓冲；高亮伪选项行）。

        ★ 2026-10-22（用户需求：删除 timeout 参数，改成「不操作界面三分钟
        后才算超时」）：进入输入模式**不再**置 ``us.typing``——正常打字属于
        界面操作，每次按键经 ``_touch_idle_states`` 重置 3 分钟倒计时；
        ``typing`` 仅用于外部编辑器（Ctrl+G vim）独占终端期间暂停计时。
        """
        if us is None or not allow_custom:
            return
        custom_editing_ref.current = True
        set_custom_editing(True)
        set_custom_text("")
        custom_text_ref.current = ""
        set_custom_editor_error("")
        set_custom_input_rev(custom_input_rev + 1)
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

    def _clear_custom() -> None:
        """Ctrl+U：清空当前自定义答案输入。"""
        custom_text_ref.current = ""
        set_custom_text("")
        set_custom_editor_error("")
        set_custom_input_rev(custom_input_rev + 1)

    def _delete_custom_word() -> None:
        """Ctrl+W：删除光标前的词（按空白边界；CJK 无空格时删整段）。"""
        text = custom_text_ref.current or ""
        if not text:
            return
        i = len(text)
        while i > 0 and text[i - 1].isspace():
            i -= 1
        while i > 0 and not text[i - 1].isspace():
            i -= 1
        new_text = text[:i]
        custom_text_ref.current = new_text
        set_custom_text(new_text)
        set_custom_editor_error("")
        set_custom_input_rev(custom_input_rev + 1)

    def _open_custom_editor() -> None:
        """Ctrl+G：用外部编辑器（``EDITOR``，默认 vim）编辑自定义答案。

        编辑器返回文本后回填到输入行（仍保持输入模式，Enter 确认）；
        取消/失败（None）保持原文本并显示错误提示。

        ★ 2026-10-22（用户需求：删除 timeout 参数，改成「不操作界面三分钟
        后才算超时」）：外部编辑器独占终端，弹窗在此期间收不到任何按键——
        把 ``us.typing`` 置 True 让工具轮询暂停空闲计时，返回后（finally）
        复位并把编辑耗时补回倒计时，避免长时间编辑被 3 分钟空闲超时打断。
        """
        if us is None or not custom_editing_ref.current:
            return
        before = custom_text_ref.current or ""
        us.typing = True
        try:
            edited = _run_external_editor(before)
        finally:
            if us is not None:
                us.typing = False
        if edited is None:
            set_custom_editor_error("编辑器不可用，已保留原文本")
            return
        raw = str(edited).replace("\r\n", "\n").replace("\r", "\n")
        custom_text_ref.current = raw
        set_custom_text(raw)
        set_custom_editor_error("")
        set_custom_input_rev(custom_input_rev + 1)

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

    # ── 协议回调（first-write-wins；绑定当前 tab 的 us） ──
    # ★ P1-1（巨型组件拆分）：协议回调（提交/标记回答/自动推进/导航同步）
    #   收敛到模块级 ``_make_question_callbacks``。
    # ★ 2026-10-21：提前到 use_input 注册之前——数字键快选 handler 需要
    #   ``_on_select``（单选确认）与 ``_toggle_checked``（多选勾选）。
    _on_select, _on_submit, _on_cancel, _on_highlight = _make_question_callbacks(
        us, multi_mode=multi_mode, model=model, active_ref=active_ref,
        set_active=set_active, set_selected=set_selected,
        allow_custom=allow_custom, on_custom_enter=_enter_custom,
        checked_values_getter=_checked_values,
    )

    # ── 顶层输入路由（2026-10-21）：自定义输入 Esc 优先于 tab/控件 ──
    # 父组件 use_input 先于子控件（SelectInput/MultiSelect/TextInput）被
    # router 调用（前序收集）——输入模式下 Esc 由本 handler 消费（退出
    # 输入而不是取消问题）；字符/退格/方向键/Enter 放行给 TextInput。
    use_input(
        lambda ev: _handle_custom_input_event(
            ev, visible=visible, input_mode=custom_editing_ref.current,
            exit_custom=_exit_custom, edit_custom=_open_custom_editor,
            clear_custom=_clear_custom, delete_word=_delete_custom_word,
        ),
        visible,
    )

    # ── 顶层输入路由（2026-10-21）：数字键快选（1-9） ──
    # 父 handler 先于子控件消费：单选立即确认（多问题自动推进）、多选切换
    # 勾选；输入模式放行给 TextInput（数字作为文本输入）。
    use_input(
        lambda ev: _handle_number_event(
            ev, visible=visible, is_submit=is_submit,
            input_mode=custom_editing_ref.current, multi=multi, total=total,
            options=options, us=us, on_confirm=_on_select,
            on_toggle=_toggle_checked, set_selected=set_selected,
        ),
        visible,
    )

    # ── 顶层输入路由（2026-10-21）：多选批量键 a 全选 / c 清空 ──
    use_input(
        lambda ev: _handle_batch_event(
            ev, visible=visible, is_submit=is_submit,
            input_mode=custom_editing_ref.current, multi=multi, total=total,
            us=us, on_toggle_all=_toggle_all_checked,
            on_clear=_clear_checked,
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
            submit_scroll_ref=submit_scroll_ref,
            set_submit_scroll=set_submit_scroll,
            submit_rows_limit=_submit_rows_limit(),
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
        rows.extend(_build_submit_page(
            states, width, scroll=submit_scroll_ref.current,
            max_rows=_submit_rows_limit(),
        ))
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
    checked_idx = _checked_indices(us, total)
    checked_count = len(checked_idx)

    # ── 分栏宽度（先算好：说明块行数预算与控件渲染共用） ──
    if split:
        max_opt_w = max((wcswidth_simple(o) for o in options), default=0)
        if allow_custom:
            max_opt_w = max(max_opt_w, wcswidth_simple(_CUSTOM_LABEL))
        base_desc_w = _desc_column_width(width)
        # 左栏须容纳「序号 + 标记 + 最长选项（含自定义伪选项）」完整文本：
        # 前缀宽度 = 序号列（num_w + 2）+ 标记列（2）。
        auto_opt_w = max_opt_w + _num_width(total) + 5
        opt_w = max(1, min(max(1, width - base_desc_w - 1), auto_opt_w))
        desc_w = max(1, width - opt_w - 1)
    else:
        opt_w = max(1, width - 4) if width and width > 0 else 40
        desc_w = 0

    # 选中项完整说明块（分栏模式且说明超出右栏宽度时，列表下方完整显示）
    desc_block_lines = (
        _selected_desc_lines(us, cur, total, width, desc_w) if split else []
    )

    # 主/次两行提示（先算：决定可见项行预算）
    hint_primary, hint_secondary = _build_hints(
        us, multi=multi, allow_custom=allow_custom, multi_mode=multi_mode,
        custom_editing=custom_editing_ref.current,
        editor_error=custom_editor_error, done=done,
        checked_count=checked_count,
    )

    # ── 顶部：标题 + 分区线（tab 栏已在 Submit 页分支之前渲染） ──
    rows.append(_build_header(
        us, cur, total, multi, checked_count, done, width,
    ))
    rows.append(_build_separator(width))

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
            "children": f" \u2713 已选择: {result_txt}",
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
        # 行预算：头部固定行（标题 + 分割线 + tab 栏 + 分栏表头 + 说明块 +
        # 两行提示 + 输入行）先从可用高度扣除，剩余给选项列表（每项一行）。
        # 长列表需要滚动指示时再扣 1 行（矮终端下限 1 行，可滚动导航）。
        base_fixed_rows = 2 + (1 if multi_mode else 0) + len(desc_block_lines) + 1
        if split:
            base_fixed_rows += 1  # 分栏表头
        if hint_secondary:
            base_fixed_rows += 1
        if custom_editing_ref.current:
            base_fixed_rows += 1
        base_limit = max(1, _popup_item_rows() - base_fixed_rows)
        need_indicator = total_items > base_limit
        fixed_rows = base_fixed_rows + (1 if need_indicator else 0)
        limit_base = max(1, _popup_item_rows() - fixed_rows)
        control_focus = not custom_editing_ref.current
        limit = max(1, min(total_items, limit_base))
        # 可见窗口（控件 onWindowChange 上报；首帧用跟随光标估算兜底）。
        win_off, win_count = list_window
        if win_count <= 0:
            visible = limit
            if total_items <= visible:
                win_off, win_count = 0, total_items
            else:
                win_off = 0 if cur < visible else cur - visible + 1
                win_off = max(0, min(win_off, total_items - visible))
                win_count = visible
        above = max(0, int(win_off))
        below = max(0, total_items - int(win_off) - int(win_count))
        # ★ 2026-10-21（继续优化）：分栏表头 + 长列表隐藏项指示（均占 1 行，
        #   已在上方行预算中扣除）。
        if split:
            rows.append(_build_split_header(opt_w, desc_w))
        if need_indicator:
            indicator = _build_scroll_indicator(above, below, width)
            if indicator is not None:
                rows.append(indicator)
        if split:
            def _split_renderer(item, idx, is_sel, is_checked=None):
                # 分栏说明模式单行：控件对每个 item 调用一次 renderItem——
                # 只构建 item 索引对应的那一行（修复前循环渲染整个选项
                # 列表，每个 item 重复 total 次 → 弹窗选项重复多份）。
                # ★ 2026-10-21：右栏显示该选项自己的说明；列表末尾伪选项
                #   行单独渲染。
                if idx >= total:
                    return _build_custom_split_row(
                        is_sel, opt_w, desc_w, total,
                    )
                checked_now = _checked_indices(us, total)
                return _build_split_row(
                    us, options, multi, cur, checked_now,
                    opt_w, desc_w, idx, total,
                )
        else:
            def _regular_renderer(item, idx, is_sel, is_checked=None):
                # ★ 2026-10-21：列表末尾伪选项行单独渲染（无勾选标记）。
                if idx >= total:
                    return _build_custom_row(is_sel, opt_w, total)
                checked_now = _checked_indices(us, total)
                return _build_regular_row(
                    us, options, multi, idx, cur, checked_now, opt_w,
                )

        # 多选勾选态：控件内部与 ``us.checked`` 双向同步（控件受控 values
        # 由 us.checked 派生——数字键切换勾选后控件高亮即时回显）。
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
            checked_vals = [options[i] for i in checked_idx]
            control = h(MultiSelect, {
                "key": f"us-multiselect-{getattr(us, 'seq', 0)}{ctrl_key_suffix}",
                "items": items,
                "initialIndex": cur,
                "index": cur,
                "initialValues": checked_vals,
                "values": checked_vals,
                "limit": limit,
                "onSelect": _on_select,
                "onChange": _on_checked_change,
                "canToggle": _can_toggle,
                "onCancel": _on_cancel,
                "onHighlight": _on_highlight,
                "onWindowChange": _on_window_change,
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
                "onWindowChange": _on_window_change,
                "renderItem": _regular_renderer if not split else _split_renderer,
                "consumeAll": True,
                "focus": control_focus,
            })
        rows.append(control)

        # ── 选中项完整说明块（分栏模式） ──
        for i, line in enumerate(desc_block_lines):
            prefix = "  \u25b8 " if i == 0 else "    "
            rows.append(h(TEXT, {
                "children": _truncate_width(prefix + str(line), max(1, int(width))),
                "style": _S_DESC,
                "height": 1,
                "key": f"us-desc-block-{i}",
                "textWrap": "truncate-end",
            }))

        # ── 自定义输入编辑行（输入模式：选项列表之后、提示行之前） ──
        if custom_editing_ref.current:
            try:
                input_budget = min(_CUSTOM_INPUT_MAX_WIDTH, max(8, int(width) - 8))
            except (TypeError, ValueError):
                input_budget = _CUSTOM_INPUT_MAX_WIDTH
            rows.append(_build_custom_input_row(
                _single_line_custom(custom_text, input_budget),
                _on_custom_change, _submit_custom,
                rev=custom_input_rev,
            ))

    # ── 提示行（主操作亮色 + 次要快捷键 dim；窄屏按宽度截断） ──
    rows.append(h(TEXT, {
        "children": _truncate_width(hint_primary, max(1, int(width))),
        "style": _S_HINT,
        "height": 1,
        "textWrap": "truncate-end",
        "key": "us-hint",
    }))
    if hint_secondary:
        rows.append(h(TEXT, {
            "children": _truncate_width(hint_secondary, max(1, int(width))),
            "style": _S_DESC,
            "height": 1,
            "textWrap": "truncate-end",
            "key": "us-hint2",
        }))

    return h(Column, None, rows)


__all__ = ["UserSelectPopup"]
