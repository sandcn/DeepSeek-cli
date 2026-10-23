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
    选项列表末尾追加「其他（自定义输入…）」伪选项——Enter 进入输入编辑
    模式，Enter 提交自由输入、Esc 返回（多行编辑见 2026-10-23 说明）
    选项。自由输入结果并入 ``result``，并经 ``custom`` 字段返回（见工具层
    JSON）。
  - ★ 2026-10-22（用户需求：删除 ``timeout`` 参数，改成「不操作界面一分钟
    后才算超时」）：弹窗每收到一次按键/滚动，就把各未提交问题的倒计时重置
    为 1 分钟（``_touch_idle_states`` → ``UserSelectState.touch_activity``；
    共享计时：任一 tab 上的操作刷新全部问题）。因此持续操作永不超时，
    界面空闲 1 分钟才由工具协程判定超时并回退 ``default_options``。外部
    编辑器（Ctrl+G vim）独占终端期间 ``us.typing=True`` 暂停计时（返回后
    补回），避免长文编辑被超时打断。
  - ★ 2026-10-21（用户需求：Ctrl+G 用 vim 输入）：输入模式内按 Ctrl+G
    调用外部编辑器（``EDITOR``，默认 vim）编辑当前答案；返回后回填输入行
    （Enter 确认）。多行内容在输入行以 ``⏎`` 折叠显示，提交/返回 JSON
    保留原始换行。
  - ★ 2026-10-21（用户需求：美化 TUI + 优化操作体验）：
    * 视觉分区：标签栏（多问题）/ 标题区 + 选项列表 + 主/次两行提示；
      标题右侧显示位置 ``(n/total)``、多选 ``已选 k/total``、超时倒计时
      ``⏱ Ns``（≤10s 变黄）；选中行整行背景高亮。
    * 选项带 ``1.`` 序号，数字键 ``1-9`` 快选：单选立即确认（多问题自动
      推进），多选切换勾选（Enter 再确认）；自定义输入模式数字放行给
      TextInput。
    * 选项说明：说明写在选项对象 ``description`` 里，缩进显示在该选项
      下方（选中亮色/其余 dim）；★ 2026-10-23（用户需求：说明支持多行
      显示）：**选中项**按原文分行 + 超宽自动换行展开（受弹窗高度预算
      限制，超出以 ``…`` 截断），其余选项折叠为单行。
    * 导航增强：Home/End 首末、PgUp/PgDn 翻页（共享控件仅在模态弹窗
      ``consumeAll=True`` 时启用，命令补全等调用零回归）。
    * 多选未勾选时按 Enter 仍提交空列表（返回协议不变），提示行明确
      显示「未勾选」；Submit 页未回答项显示默认返回值。
  - ★ 2026-10-21（继续美化 + 优化操作体验）：
    * Submit 页分页：大量并发问题时按可视行数分页，``↑↓/PgUp/PgDn/Home/End``
      滚动，数字键 ``1-9`` 跳回对应问题修改。
    * 长列表指示：列表上方显示 ``▲ 上方 N 项 · ▼ 下方 M 项``（仅隐藏时）。
    * tab 摘要：已回答 tab 显示 ``1 ✓ Python``；取消 ``✗ 取消``、超时
      ``⏱ 超时``；未回答显示 ``1 标题``。
    * 多选批量键：``a`` 全选/全不选、``c`` 清空。
    * 输入模式快捷键：``Ctrl+U`` 清空、``Ctrl+W`` 删词。
  - ★ 2026-10-22（用户需求：user_select TUI 与 Claude 对齐，实测 Claude
    Code v2.1.308 AskUserQuestion）：
    * tab 栏：``← [x] 标题  [ ] 标题  √ 提交 →``——问题 tab 带 ``[x]/[ ]``
      勾选标记（答案摘要不在 tab 显示，Submit 页可看）、当前 tab 反色块、
      左右箭头提示可切换；全部已回答时 Submit 显示 ``√``。
    * 标题区：问题页只渲染一行问题文本（加粗白字），无 header chip / 无
      整行 ``─`` 分割线；``header`` 仅用于 tab 标签。
    * 选项行：``> 1. 标签``（选中指针 + 序号），选中整行背景高亮；
      说明缩进显示在选项下方（Claude 选项 label+description 布局）；
      ★ 2026-10-23：说明与选项同参数（``options[].description``），
      选中项多行展开、其余折叠单行。
    * 多选标记：``[√]``/``[ ]``（替代 ●/○）；多选列表末尾追加 ``提交`` 项
      （Claude 多选 Submit 行；Enter 直接提交仍保留兼容）。
    * 自定义输入项：``4. 其他（自定义输入…）``（普通编号项，无说明行）。
    * 提示行：主提示 ``Enter 选择 · Tab/←→ 切换 · Esc 取消``（dim 单行，
      多选含 ``Space 勾选``），次要快捷键更暗的第二行。
    * Submit 页：``检查你的答案`` + 每题 ``• 问题`` / ``  → 答案``（绿色）+
      ``准备好提交答案了吗？`` + ``> 1. 提交答案`` / ``  2. 取消`` 两项
      （↑↓ 切换、Enter 执行、PgUp/PgDn 滚动、1-9 跳题）。
    * 保留全部既有增强（数字键、Submit 页、自动推进、空闲计时、a/c、
      Home/End/PgUp/PgDn、Ctrl+G vim、Ctrl+U/Ctrl+W）。
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
渲染经 ``renderItem``（单选 ``> `` 指针/整行背景高亮、多选 ``[√]/[ ]`` 勾选、
选项下方说明行）完全保留既有视觉；``consumeAll=True`` 弹窗模式（其他按键
消费阻断输入框，Ctrl+C 放行可中断工具执行）。

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
from src.tui.app.input_area import _truncate_width
from src.tui.app._theme import _S_DIM
from src.tui.ink import TEXT, h, Column, StyledRun
from src.tui.ink.helpers import truncate_runs_ellipsis
from src.tui.ink.hooks import use_effect, use_ref, use_state, use_input, useMouseInput
from src.tui.ink.widgets.interactive import SelectInput, MultiSelect
from src.tui._external_editor import run_external_editor as _run_external_editor

from ._modal_view import empty_modal_frame, use_modal_scope

_logger = logging.getLogger(__name__)

__all__ = ["UserSelectPopup"]

#: 问题正文（Claude 风格：问题页只渲染一行问题文本，无 chip / 分割线）
_S_QUESTION = Style(fg=252, bold=True)
#: 说明列 / 提示行静态色（浅蓝 110——弹窗不呼吸）
_S_DESC = Style(fg=110)
#: 未选中项说明（dim 灰）
_S_DESC_DIM = Style(fg=244)
#: 标题右侧元信息
_S_META = Style(fg=244)
#: 倒计时剩余 ≤10s 的警示色
_S_META_WARN = Style(fg=214, bold=True)
#: 主提示行（Claude 风格：底部单行 dim 提示）
_S_HINT = Style(fg=244)
#: 次提示行（更暗——次要快捷键）
_S_HINT2 = Style(fg=240)
#: 选中行序号（亮青 + 背景）
_S_SEL_ACCENT = Style(fg=45, bg=237, bold=True)
#: 选中行正文（亮色 + 背景）
_S_SEL_TEXT = Style(fg=252, bg=237)
#: 选中行补白（背景铺满）
_S_SEL_PAD = Style(bg=237)
#: 未选中序号（暗灰）
_S_NUM = Style(fg=240)
#: 未选中选项正文（Claude 风格：亮白正文，序号 dim）
_S_ITEM_IDLE = Style(fg=252)
#: 多选已勾选标记（绿色加粗）
_S_MARK_CHECKED = Style(fg=40, bold=True)
#: 多选未勾选标记（dim 灰）
_S_MARK_UNCHECKED = Style(fg=244)
#: 轻度警示（多选未勾选等）
_S_WARN = Style(fg=214)
#: 选中指针（Claude 风格 ``> ``）
_CURSOR = "> "
_CURSOR_PAD = "  "
#: 多选勾选标记（Claude 风格方框 + √，固定 4 列宽——安全对齐）
_CHECKED = "[\u221a] "
_UNCHECKED = "[ ] "
#: 说明行缩进步数（对齐 label 文本起点：指针 2 + 序号列 + 标记列）
_DESC_INDENT = 2

#: 自定义输入伪选项（Claude "Type something." 风格：列表末尾一项，无图标）
_CUSTOM_LABEL = "其他（自定义输入…）"
#: 自定义输入伪选项的内部值（NUL 前缀不会与用户选项文本冲突；只用于
#: 控件 Enter/空格判定，不进入提交结果/返回 JSON）
_CUSTOM_VALUE = "\x00__user_select_custom__"
#: 多选末尾「提交」项（Claude 多选列表的 Submit 项；Enter 提交勾选集合）
_SUBMIT_LABEL = "提交"
#: 多选提交项的内部值（同 _CUSTOM_VALUE：不进入提交结果/返回 JSON）
_SUBMIT_VALUE = "\x00__user_select_submit__"
#: 自定义输入编辑行占位提示（空文本时显示在光标块之后）
_CUSTOM_PLACEHOLDER = "输入自定义答案 · Enter 提交 · Shift+Enter/Ctrl+O 换行"

#: 并发 tab 栏样式（Claude 风格）：当前焦点问题（反色块：亮底暗字）
_S_TAB_ACTIVE = Style(fg=235, bg=252, bold=True)
#: 未完成非焦点问题（dim 灰）
_S_TAB_INACTIVE = Style(fg=244)
#: 已回答（可重答）非焦点问题（[x]，亮灰——区别于已提交终态）
_S_TAB_ANSWERED = Style(fg=250)
#: 已提交（done 终态锁定）非焦点问题（暗灰）
_S_TAB_DONE = Style(fg=240)
#: 全部完成后的 Submit 提示（绿色加粗）
_S_SUBMIT = Style(fg=40, bold=True)
#: 已完成 tab 只读结果行（绿色）
_S_DONE_RESULT = Style(fg=40)
#: 自定义输入光标块（青色背景；空文本/行尾同样可见）
_S_CURSOR = Style(bg=6)


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
    """Submit 页可见问题行数（标题 1 行 + 两条提示固定占 3 行）。

    ★ 2026-10-22（TUI 与 Claude 对齐）：Submit 页不再渲染分割线，固定行数
    由 5 降为 3。
    """
    return max(1, _popup_item_rows() - 3)


def _normalize_multiline(text) -> str:
    """归一化自定义输入原始文本（CRLF/CR → LF；None → 空串）。"""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _wrap_segment(text: str, width: int) -> list:
    """把一条硬换行行按显示宽度切成视觉行。

    Returns:
        ``[(start, end, segment), ...]``——``start/end`` 为该视觉行在
        ``text`` 中的字符索引（不含硬换行符），``segment`` 为显示文本。
    """
    width = max(1, int(width))
    if not text:
        return [(0, 0, "")]
    out: list = []
    start = 0
    used = 0
    for idx, ch in enumerate(text):
        cw = wcswidth_simple(ch)
        if cw < 0:
            cw = 1
        if used + cw > width and idx > start:
            out.append((start, idx, text[start:idx]))
            start = idx
            used = 0
        used += cw
    out.append((start, len(text), text[start:]))
    return out


def _visual_lines(text: str, width: int) -> list:
    """把多行文本切成视觉行：``[(start, end, segment, hard_break), ...]``。

    ``hard_break`` 表示该视觉行以硬换行（``\\n``）结束——光标落在硬换行
    索引时显示在该行末尾；软换行边界则显示在下一行开头（避免光标重复）。
    """
    raw = _normalize_multiline(text)
    if not raw:
        return [(0, 0, "", False)]
    out: list = []
    i = 0
    n = len(raw)
    while i <= n:
        j = raw.find("\n", i)
        if j < 0:
            content = raw[i:]
            hard = False
        else:
            content = raw[i:j]
            hard = True
        segs = _wrap_segment(content, width)
        last = len(segs) - 1
        for k, (s, e, seg) in enumerate(segs):
            out.append((i + s, i + e, seg, bool(hard and k == last)))
        if j < 0:
            break
        i = j + 1
        if i >= n:
            # 末尾硬换行：补一条空行，光标可落在新行行首。
            out.append((n, n, "", False))
            break
    return out


def _line_bounds(text: str, pos: int) -> "tuple[int, int]":
    """返回光标所在硬换行行的 ``(start, end)``（``end`` 不含换行符）。"""
    raw = _normalize_multiline(text)
    pos = max(0, min(int(pos), len(raw)))
    start = raw.rfind("\n", 0, pos) + 1
    end = raw.find("\n", pos)
    if end < 0:
        end = len(raw)
    return start, end


def _editor_marker(text: str, width: int, key: str) -> object:
    """多行编辑器的隐藏行提示（dim 单行）。"""
    return h(TEXT, {
        "children": _truncate_width(text, max(1, int(width))),
        "style": _S_DESC_DIM,
        "height": 1,
        "key": key,
        "textWrap": "truncate-end",
    })


def _build_editor_line(
    raw: str, cursor: int, seg_start: int, seg_end: int, seg: str,
    *, first: bool, width: int, key: str,
) -> object:
    """构造一条自定义输入视觉行（含光标块）。

    ★ 2026-10-23（用户需求：自定义输入没有光标 + 多行显示）：不再依赖单行
    ``TextInput``；空文本、行尾、行中均显式渲染光标块，且硬换行/软换行
    都按完整内容逐行输出。
    """
    width_i = max(1, int(width))
    prefix = _CURSOR if first else _CURSOR_PAD
    runs: list = [StyledRun(
        prefix, _S_SEL_ACCENT if first else _S_NUM,
    )]
    text_w = max(1, width_i - wcswidth_simple(prefix))
    cur_off = None
    if seg_start <= cursor < seg_end:
        cur_off = cursor - seg_start
    elif cursor == seg_end and (
        seg_end == len(raw)
        or (0 <= seg_end < len(raw) and raw[seg_end] == "\n")
    ):
        # 光标在硬换行处（行尾）或全文末尾。
        cur_off = len(seg)
    elif seg_start == seg_end == cursor:
        # 空行 / 空文本。
        cur_off = 0
    if not raw:
        runs.append(StyledRun(" ", _S_CURSOR))
        runs.append(StyledRun(_CUSTOM_PLACEHOLDER, _S_DESC_DIM))
        return h(TEXT, {
            "styled": runs, "height": 1, "key": key,
            "textWrap": "truncate-end",
        })
    if cur_off is None:
        body = _truncate_width(seg, text_w)
        if body:
            runs.append(StyledRun(body, _S_ITEM_IDLE))
    else:
        off = max(0, min(int(cur_off), len(seg)))
        before = seg[:off]
        if off < len(seg):
            ch = seg[off]
            after = seg[off + 1:]
            if before:
                runs.append(StyledRun(before, _S_ITEM_IDLE))
            runs.append(StyledRun(ch, _S_CURSOR))
            if after:
                runs.append(StyledRun(after, _S_ITEM_IDLE))
        else:
            if before:
                runs.append(StyledRun(before, _S_ITEM_IDLE))
            used = wcswidth_simple(before)
            if used + 1 <= text_w:
                runs.append(StyledRun(" ", _S_CURSOR))
            elif before:
                # 行已满：光标覆盖最后一个字符（仍保持行宽不溢出）。
                runs[-1] = StyledRun(before[:-1], _S_ITEM_IDLE)
                runs.append(StyledRun(before[-1], _S_CURSOR))
            else:
                runs.append(StyledRun(" ", _S_CURSOR))
    return h(TEXT, {
        "styled": runs, "height": 1, "key": key,
        "textWrap": "truncate-end",
    })


def _build_custom_editor_rows(
    text: str, cursor: int, width: int, max_rows: "int | None" = None,
) -> list:
    """构造自定义输入的多行编辑区（完整内容 + 光标块）。

    Args:
        text: 原始文本（可含换行）。
        cursor: 光标字符索引（0..len(text)）。
        width: 弹窗宽度。
        max_rows: 最大渲染行数（None = 全部显示）；超出时以光标为中心
            滚动，并在上方/下方显示隐藏行提示。

    Returns:
        TEXT 行列表（每行高度 1）。
    """
    raw = _normalize_multiline(text)
    width_i = max(1, int(width))
    text_w = max(1, width_i - wcswidth_simple(_CURSOR))
    cursor = max(0, min(int(cursor), len(raw)))
    visual = _visual_lines(raw, text_w)
    total = len(visual)
    start, end = 0, total
    above = below = 0
    if max_rows is not None:
        try:
            cap = max(1, int(max_rows))
        except (TypeError, ValueError, OverflowError):
            cap = 1
        if total > cap:
            # 光标所在视觉行（硬换行末尾 / 全文末尾也算当前行）。
            ci = total - 1
            for i, (s, e, _seg, hard) in enumerate(visual):
                if s <= cursor < e or (cursor == e and (hard or i == total - 1)):
                    ci = i
                    break
            visible = min(cap, total)
            while True:
                visible = max(1, min(visible, total))
                start = max(0, min(ci - visible // 2, total - visible))
                end = min(total, start + visible)
                above = start
                below = total - end
                need = (1 if above else 0) + (1 if below else 0)
                if visible + need <= cap:
                    break
                if visible <= 1:
                    # 高度只够一行：只显示光标行，不放隐藏行提示。
                    start = ci
                    end = min(total, ci + 1)
                    above = below = 0
                    break
                visible = max(1, cap - need)
    rows: list = []
    if above:
        rows.append(_editor_marker(
            f"\u25b2 上方 {above} 行", width_i, "us-custom-editor-above",
        ))
    for i in range(start, end):
        s, e, seg, _hard = visual[i]
        rows.append(_build_editor_line(
            raw, cursor, s, e, seg, first=(i == 0), width=width_i,
            key=f"us-custom-editor-{i}",
        ))
    if below:
        rows.append(_editor_marker(
            f"\u25bc 下方 {below} 行", width_i, "us-custom-editor-below",
        ))
    return rows


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


def _option_runs(
    cursor_text: str, num_text: str, mark_text: str, label: str, *,
    cursor_style: Style, num_style: Style, mark_style: Style,
    text_style: Style | None, target_w: int, pad_style: Style | None,
) -> list:
    """构建选项行 styled runs（指针 + 序号 + 标记 + 文本 + 可选补白）。

    每个 run 的样式分开表达（指针/序号/标记/正文可以不同颜色）；
    ``target_w`` 为整行显示宽度预算，超长文本按显示宽度截断（不拆 CJK），
    选中行可用 ``pad_style`` 把背景铺满整行（Claude 风格整行高亮）。
    """
    target_w = max(0, int(target_w))
    prefix_w = (
        wcswidth_simple(cursor_text)
        + wcswidth_simple(num_text)
        + wcswidth_simple(mark_text)
    )
    if prefix_w > target_w:
        # 极端窄终端：前缀自身已超预算——合并截断为单个 run（不溢出）。
        prefix = _truncate_width(cursor_text + num_text + mark_text, target_w)
        return [StyledRun(prefix, num_style)] if prefix else []
    runs: list = [
        StyledRun(cursor_text, cursor_style),
        StyledRun(num_text, num_style),
    ]
    if mark_text:
        runs.append(StyledRun(mark_text, mark_style))
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


def _one_line(text: str) -> str:
    """把任意文本折叠为单行（换行/回车 → 空格；防行数预算被破坏）。"""
    return str(text or "").replace("\r", " ").replace("\n", " ")


def _option_description(option) -> str:
    """读取选项说明文本（``UserSelectOption.description``；空串 = 无说明）。"""
    return str(getattr(option, "description", "") or "")


def _option_label(option) -> str:
    """读取选项标签文本（``UserSelectOption.label``）。"""
    return str(getattr(option, "label", "") or "")


def _wrap_description(text: str, width: int) -> list:
    """说明文本按显示宽度换行（``\\n`` 强制换行；空文本返回空列表）。

    ★ 2026-10-23（用户需求：说明支持多行显示）：说明原文含换行时逐行
    保留，每行再按可用列宽自动换行（CJK 安全，不产生超宽行）。
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return []
    return _wrap_by_width(raw, max(1, int(width))) or [""]


def _option_desc_rows(
    text: str, *, indent: int, target_w: int, style: Style,
    key: str, max_rows: "int | None" = None,
) -> list:
    """选中项说明行：按原文多行展开（超宽自动换行，超出预算以 ``…`` 截断）。

    Args:
        text: 说明原文（可含换行）。
        indent: 说明行缩进（对齐选项 label 起点）。
        target_w: 行宽预算（含缩进）。
        style: 说明文字样式。
        key: 元素 key 前缀（多行时追加行号）。
        max_rows: 最大渲染行数（None = 不限制；用于弹窗高度预算）。
    """
    target_w = max(1, int(target_w))
    indent = max(0, min(int(indent), target_w - 1))
    budget = max(1, target_w - indent)
    lines = _wrap_description(text, budget)
    truncated = max_rows is not None and len(lines) > max(1, int(max_rows))
    if truncated:
        lines = lines[: max(1, int(max_rows))]
    rows: list = []
    for i, line in enumerate(lines):
        body = line
        if truncated and i == len(lines) - 1:
            # 末行加省略号（先留 1 列给 ``…``，保持行宽不超预算）。
            body = _truncate_width(body, max(1, budget - 1)).rstrip() + "\u2026"
        rows.append(h(TEXT, {
            "children": " " * indent + body,
            "style": style,
            "height": 1,
            "key": f"{key}-{i}",
            "textWrap": "truncate-end",
        }))
    return rows


def _option_folded_desc_row(
    text: str, *, indent: int, target_w: int, style: Style, key: str,
) -> object:
    """非选中项说明行（多行说明折叠为单行，超宽截断）。"""
    target_w = max(1, int(target_w))
    indent = max(0, min(int(indent), target_w - 1))
    budget = max(1, target_w - indent)
    body = _truncate_width(_one_line(text), budget)
    return h(TEXT, {
        "children": " " * indent + body,
        "style": style,
        "height": 1,
        "key": key,
        "textWrap": "truncate-end",
    })


def _option_indent(num_w: int, multi: bool) -> int:
    """说明行缩进（对齐 label 文本起点：指针 2 + ``序号. `` 列 + 多选标记列）。"""
    return _DESC_INDENT + int(num_w) + 2 + (len(_UNCHECKED) if multi else 0)


def _build_custom_row(
    is_sel: bool, opt_w: int, total: int, multi: bool = False,
    idx: int | None = None, num_total: int | None = None,
    draft: str = "", max_desc_rows: "int | None" = None,
) -> object:
    """自定义输入伪选项（Claude 风格：普通编号项 + 已输入草稿说明）。

    实测 Claude AskUserQuestion 的 ``4. Type something.`` 是一项普通选项
    （带序号、多选带 ``[ ]`` 勾选框），没有额外的说明行。★ 2026-10-23
    （用户需求：没输入时也要显示已输入内容，像选项说明一样）：当该 tab
    存在未提交草稿且**不在编辑模式**时，在自定义行下方按选项说明样式显示
    草稿——选中项多行展开，未选中折叠单行；进入编辑模式后由编辑区接管，
    不再重复显示说明。

    Args:
        is_sel: 是否当前高亮。
        opt_w: 选项行宽度预算。
        total: 选项总数（自定义项序号 = total + 1）。
        multi: 是否多选（渲染 ``[ ]`` 勾选框）。
        idx: 自定义项在控件 items 中的下标（缺省 ``total``）。
        num_total: 序号列宽参考的总项数（含伪选项；缺省 ``total + 1``）。
        draft: 未提交的自定义草稿（空串 = 不显示说明行）。
        max_desc_rows: 选中项草稿说明最大行数（None = 不限制）。
    """
    n = int(num_total if num_total is not None else total + 1)
    i = int(total if idx is None else idx)
    num_w = _num_width(n)
    num = f"{i + 1:>{num_w}}. "
    mark = _UNCHECKED if multi else ""
    runs = _option_runs(
        _CURSOR if is_sel else _CURSOR_PAD, num, mark, _CUSTOM_LABEL,
        cursor_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        mark_style=_S_SEL_ACCENT if is_sel else _S_MARK_UNCHECKED,
        text_style=_S_SEL_TEXT if is_sel else _S_ITEM_IDLE,
        target_w=max(1, opt_w),
        pad_style=_S_SEL_PAD if is_sel else None,
    )
    row = h(TEXT, {
        "styled": runs, "height": 1, "key": "row-custom",
        "textWrap": "truncate-end",
    })
    draft_text = str(draft or "")
    if not draft_text.strip():
        return row
    indent = _option_indent(num_w, multi)
    if is_sel:
        desc_rows = _option_desc_rows(
            draft_text, indent=indent, target_w=max(1, opt_w),
            style=_S_DESC, key="row-custom-desc", max_rows=max_desc_rows,
        )
    else:
        desc_rows = [_option_folded_desc_row(
            draft_text, indent=indent, target_w=max(1, opt_w),
            style=_S_DESC_DIM, key="row-custom-desc",
        )]
    return h(Column, None, [row] + desc_rows)


def _build_submit_row(
    is_sel: bool, opt_w: int, total: int, multi: bool = False,
    idx: int | None = None, num_total: int | None = None,
) -> object:
    """多选末尾「提交」项（Claude Submit 行：无序号，缩进到 label 列）。

    实测 Claude 多选列表在选项之后单独显示 ``Submit``（不带序号，上面一条
    分隔线）——本函数对齐该视觉：指针 + 序号列留白 + ``提交``。

    Args:
        is_sel: 是否当前高亮。
        opt_w: 选项行宽度预算。
        total: 选项总数（序号列宽参考）。
        multi: 是否多选（保留勾选标记列占位，使 label 对齐）。
        idx: 控件 items 下标（缺省最后一项）。
        num_total: 序号列宽参考的总项数（含伪选项）。
    """
    n = int(num_total if num_total is not None else total + 2)
    num_w = _num_width(n)
    # 序号列留白（保持 label 起点与普通选项一致）+ 多选标记列留白。
    lead = " " * (num_w + 2 + (len(_UNCHECKED) if multi else 0))
    idx_i = int(idx if idx is not None else total + 1)
    label_w = max(0, int(opt_w) - wcswidth_simple(lead))
    text = _truncate_width(_SUBMIT_LABEL, label_w)
    text_style = _S_SEL_TEXT if is_sel else _S_QUESTION
    runs: list = []
    if text:
        runs.append(StyledRun(lead, text_style))
        runs.append(StyledRun(text, text_style))
    if is_sel:
        used = wcswidth_simple(lead + text)
        if int(opt_w) > used:
            runs.append(StyledRun(" " * (int(opt_w) - used), _S_SEL_PAD))
    return h(TEXT, {
        "styled": runs, "height": 1, "key": f"row-submit-{idx_i}",
        "textWrap": "truncate-end",
    })


def _header_chip_label(us) -> str:
    """tab 标签文本（``header``，≤12 显示宽；空串时返回空）。

    ★ 2026-10-22（用户需求：TUI 与 Claude 对齐）：Claude AskUserQuestion
    的 ``header`` 是极短标签（≤12 字符），只用于顶部 tab 标签；问题页
    正文只显示 ``title``。
    """
    raw = str(getattr(us, "header", "") or "")
    raw = raw.replace("\r", " ").replace("\n", " ").strip()
    return _truncate_width(raw, 12) if raw else ""


def _build_header(
    us, cur: int, total: int, multi: bool, checked_count: int,
    done: bool, width: int, q_pos: "tuple[int, int] | None" = None,
    pos_label: "str | None" = None,
) -> list:
    """Claude 风格问题标题区（单行问题文本 + 右侧元信息）。

    ★ 2026-10-22（用户需求：TUI 与 Claude 对齐，实机 Claude Code v2.1.308）：
    Claude 的问题页**没有** header chip 行、也没有整行分割线——``header``
    仅出现在顶部 tab 栏；正文只有一行问题文本（加粗白字）。本函数据此
    只渲染一行：问题文本 + 右对齐元信息（位置 / 多选已选数 / 倒计时；
    这些是项目保留的增强，Claude 原版没有）。

    窄屏时优先保留问题文本：先截断正文，仍放不下则丢弃右侧元信息；
    极窄终端只渲染截断后的单行——保证每行宽度 ≤ width。

    Args:
        us: UserSelectState（当前 tab）。
        cur: 当前选项高亮索引。
        total: 选项总数。
        multi: 是否多选。
        checked_count: 多选已勾选数。
        done: 是否已提交终态。
        width: 终端宽度。
        q_pos: 可选 ``(当前问题, 问题总数)``——多问题时显示 ``(1/2)``；
            缺省回退 ``(cur+1/total)``（选项位置，保持单问题旧语义）。

    Returns:
        单元素列表（调用方 extend 到 rows）。
    """
    title = _one_line(getattr(us, "title", "") or "选择")
    now = time.monotonic()
    warn = False
    if done:
        meta = "\u2713 已确认"
        meta_style = _S_DONE_RESULT
    else:
        if q_pos is not None:
            cur_q, total_q = q_pos
            pos = f"({cur_q}/{total_q})"
        elif pos_label:
            pos = pos_label
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
    meta_w = wcswidth_simple(meta)
    # 右侧元信息与正文之间至少留 1 列间距。
    body_budget = width - meta_w - (1 if meta else 0)
    if meta and body_budget < 4:
        meta = ""
        meta_w = 0
        body_budget = width
    body_show = _truncate_width(title, max(0, body_budget))
    runs = [StyledRun(body_show, _S_QUESTION)]
    if meta:
        gap = width - wcswidth_simple(body_show) - meta_w
        if gap > 0:
            runs.append(StyledRun(" " * gap, _S_DIM))
            runs.append(StyledRun(meta, meta_style))
    return [h(TEXT, {
        "styled": truncate_runs_ellipsis(runs, width),
        "height": 1, "key": "us-title", "textWrap": "truncate-end",
    })]


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


def _tab_label(state) -> str:
    """tab 标签：``header``（≤12 显示宽）或标题单行截断。"""
    return _header_chip_label(state) or _one_line(
        getattr(state, "title", "") or "选择"
    )


def _build_tab_bar(states: list, active: int, width: int) -> object:
    """并发 tab 栏（对齐 Claude AskUserQuestion 实机布局）。

    实测 Claude Code v2.1.308 顶部一行（左右箭头 + 问题 tab + Submit）::

        ← [x] 测试颜色  [ ] 多选测试  √ Submit →

    - ``[ ]`` 未回答 / ``[x]`` 已回答（内容摘要不在 tab 显示，Submit 页可看）；
    - 当前焦点 tab 反色块（亮底暗字，含两侧空格）；
    - 左右箭头 ``←`` / ``→`` 提示可用左右键切换；
    - 全部已回答时 Submit 显示 ``√ 提交``（项目保留中文文案）。

    Args:
        states: 弹窗 state 列表（并发队列）。
        active: 当前焦点索引（已钳制；``== len(states)`` 表示 Submit tab）。
        width: 终端宽度（tab 标签截断预算）。

    Returns:
        TEXT 元素（高度 1，单行）。
    """
    n = len(states)
    if n <= 0:
        return h(TEXT, {"children": "", "height": 1})
    avail_w = max(1, int(width) if width and width > 0 else 80)
    all_answered = all(
        getattr(s, "answered", False) or getattr(s, "done", False)
        for s in states
    )
    submit_text = "\u221a 提交" if all_answered else "提交"
    arrow_l = "\u2190"
    arrow_r = "\u2192"
    submit_block = f" {submit_text} "
    fixed = (
        wcswidth_simple(arrow_l) + wcswidth_simple(arrow_r)
        + wcswidth_simple(submit_block) + 2  # 当前 tab 反色块两侧空格
    )
    # 每个 tab 固定占「前导空格 + [x] 4 列」+ 标签；标签预算均分剩余宽度。
    gap = 1
    budget = (avail_w - fixed - (len(_UNCHECKED) + 1 + gap) * n) // n
    if budget < 2:
        # 窄屏 / 大量并发问题：紧凑显示「当前位置/总数 + 提交」。
        pos = (
            f"{min(active + 1, n)}/{n}" if active < n
            else f"\u221a {n}/{n}"
        )
        compact = _truncate_width(
            f"\u2190 {pos}  {submit_text} \u2192", avail_w,
        )
        style = _S_TAB_ACTIVE if active < n else _S_TAB_INACTIVE
        return h(TEXT, {
            "children": compact, "style": style, "height": 1,
            "key": "tab-compact", "textWrap": "truncate-end",
        })
    runs: list = [StyledRun(arrow_l, _S_META)]
    for i, s in enumerate(states):
        marked = bool(
            getattr(s, "answered", False) or getattr(s, "done", False)
        )
        mark = "[x] " if marked else "[ ] "
        label = _truncate_width(_tab_label(s), max(1, budget))
        if i == active:
            # 当前 tab：反色块（两侧空格让底色包住标签）。
            runs.append(StyledRun(f" {mark}{label} ", _S_TAB_ACTIVE))
        else:
            if getattr(s, "done", False):
                style = _S_TAB_DONE
            elif marked:
                style = _S_TAB_ANSWERED
            else:
                style = _S_TAB_INACTIVE
            runs.append(StyledRun(f" {mark}{label}", style))
        # 与下一个 tab 的间距：当前 tab 自带尾随空格 → 不再补；其余 1 列。
        if i < n - 1 and i != active:
            runs.append(StyledRun(" ", _S_DIM))
    if active == n:
        submit_style = _S_TAB_ACTIVE
    elif all_answered:
        submit_style = _S_SUBMIT
    else:
        submit_style = _S_TAB_INACTIVE
    runs.append(StyledRun(submit_block, submit_style))
    runs.append(StyledRun(arrow_r, _S_META))
    return h(TEXT, {
        "styled": truncate_runs_ellipsis(runs, avail_w),
        "height": 1, "key": "us-tabs", "textWrap": "truncate-end",
    })


def _build_regular_row(
    options: list, multi: bool, i: int, cur: int, checked: list,
    opt_w: int, num_total: "int | None" = None,
    max_desc_rows: "int | None" = None,
) -> object:
    """单个选项行（Claude 风格：``> 1. 标签`` + 缩进说明行）。

    序号（``1.``）与数字键快选呼应；选中行整行背景高亮（含补白），
    指针 ``>`` 唯一标识当前光标；多选在序号前显示 ``[√]``/``[ ]``。
    选项/说明超长均按显示宽度截断（CJK 安全，防超宽行破坏行级 diff
    宽度不变量）。

    ★ 2026-10-23（用户需求：说明支持多行显示）：说明已并入选项对象
    （``options[i].description``）——**仅选中项**按原文多行展开（超宽自动
    换行，``max_desc_rows`` 控制高度预算），其余选项折叠为单行。

    Args:
        options: 选项对象列表（``UserSelectOption``）。
        multi: 是否多选。
        i: 当前选项索引（控件 item 索引）。
        cur: 当前高亮索引（已钳制）。
        checked: 多选勾选索引列表。
        opt_w: 选项行宽度预算。
        num_total: 序号列宽参考总项数（含自定义 / 提交伪选项；缺省选项数）。
        max_desc_rows: 选中项说明最大渲染行数（None = 不限制）。

    Returns:
        Column（选项行 + 说明行；选中项说明可多行）。
    """
    is_sel = i == cur
    # 序号列宽按控件总项数计算（含末尾伪选项），保证 9→10 项时仍对齐。
    total_num = int(num_total if num_total is not None else len(options))
    num_w = _num_width(total_num)
    num = f"{i + 1:>{num_w}}. "
    mark = (
        (_CHECKED if i in checked else _UNCHECKED) if multi else ""
    )
    if multi:
        mark_style = _S_SEL_ACCENT if is_sel else (
            _S_MARK_CHECKED if i in checked else _S_MARK_UNCHECKED
        )
    else:
        mark_style = _S_NUM
    runs = _option_runs(
        _CURSOR if is_sel else _CURSOR_PAD, num, mark, _option_label(options[i]),
        cursor_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
        mark_style=mark_style,
        text_style=_S_SEL_TEXT if is_sel else _S_ITEM_IDLE,
        target_w=max(1, opt_w),
        pad_style=_S_SEL_PAD if is_sel else None,
    )
    rows: list = [h(TEXT, {
        "styled": runs, "height": 1, "key": f"row-{i}",
        "textWrap": "truncate-end",
    })]
    desc = _option_description(options[i])
    if desc:
        indent = _option_indent(num_w, multi)
        if is_sel:
            # 选中项：按原文多行展开（超宽自动换行；受高度预算限制）。
            rows.extend(_option_desc_rows(
                desc,
                indent=indent,
                target_w=opt_w,
                style=_S_DESC,
                key=f"row-{i}-desc",
                max_rows=max_desc_rows,
            ))
        else:
            # 其余选项：折叠为单行（换行 → 空格，超宽截断）。
            rows.append(_option_folded_desc_row(
                desc,
                indent=indent,
                target_w=opt_w,
                style=_S_DESC_DIM,
                key=f"row-{i}-desc",
            ))
    return h(Column, None, rows)


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
    """空闲计时：界面每发生一次操作，重置各问题的「1 分钟无操作」倒计时。

    ★ 2026-10-22（用户需求：删除 user_select 的 ``timeout`` 参数，改成
    「不操作界面一分钟后才算超时」）：工具路径构造的 state 带
    ``idle_timeout``（``IDLE_TIMEOUT_SECONDS`` = 60s）——本函数对**每个**
    未提交（未 done）的此类 state 调用 ``touch_activity``，把 deadline
    顺延为 ``now + idle_timeout``。并发多问题时采用**共享计时**（用户确认
    的方案）：任一 tab 上的一次操作都会重置全部未提交问题——界面整体
    空闲 1 分钟才算超时。

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
    input_mode: bool = False, exit_custom=None, suspend_custom=None,
    submit_scroll_ref=None, set_submit_scroll=None, submit_rows_limit=None,
    submit_choice_ref=None, set_submit_choice=None,
) -> bool:
    """顶层 tab 路由：Submit 页选项/跳题/滚动 + Tab/←/→ 切换（模块级）。

    父组件 use_input 先于子控件（SelectInput/MultiSelect/TextInput）被
    router 调用（前序收集）——tab/←/→ 被本 handler 消费，↑↓/jk 等放行给
    控件。

    ★ 2026-10-22（Claude TUI 对齐）：Submit 页底部改为 Claude 风格
    ``> 1. 提交答案`` / ``  2. 取消`` 两项——↑↓ 切换选项（``submit_choice_ref``
    0=提交 / 1=取消），Enter 执行当前项；大量问题的滚动改由
    PgUp/PgDn/Home/End 负责（不再占用 ↑↓）。

    Args:
        input_mode: 当前是否处于自定义输入编辑模式。输入模式下 ←/→ 必须
            放行给编辑区移动光标（不得切换问题 tab）；Tab 仍切换问题——
            切换前调用 ``suspend_custom`` 挂起输入并**保留本题草稿**，切回
            该 tab 时自动恢复编辑区与草稿。
        exit_custom: 退出自定义输入模式的回调（无参；Esc 语义，保留草稿
            但不自动恢复）。
        suspend_custom: 切 tab 时挂起自定义输入的回调（无参；保留草稿且
            标记 ``custom_active=True``，切回自动恢复）。
        submit_scroll_ref: Submit 页滚动偏移 ref。
        set_submit_scroll: Submit 页滚动偏移 state setter。
        submit_rows_limit: Submit 页当前可见问题行数（用于滚动步长/边界）。
        submit_choice_ref: Submit 页当前选项 ref（0=提交答案，1=取消）。
        set_submit_choice: Submit 页当前选项 state setter。
    """
    if not visible:
        return False
    # Submit 页：↑↓ 选「提交答案 / 取消」；Enter 执行；Esc 返回问题；
    # 数字跳题；PgUp/PgDn/Home/End 滚动问题列表。
    if is_submit:
        cur_choice = 0
        if submit_choice_ref is not None:
            try:
                cur_choice = 1 if int(submit_choice_ref.current or 0) else 0
            except (TypeError, ValueError, OverflowError):
                cur_choice = 0
        if event.kind in ("arrow_up", "arrow_down"):
            new_choice = (
                0 if event.kind == "arrow_up" else 1
            )
            if new_choice != cur_choice:
                if submit_choice_ref is not None:
                    submit_choice_ref.current = new_choice
                if set_submit_choice is not None:
                    set_submit_choice(new_choice)
            return True
        if event.kind == "enter":
            if cur_choice == 1:
                _back_to_questions(model, active_ref, set_active)
            else:
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
        # ★ 2026-10-21（继续优化）：PgUp/PgDn/Home/End 滚动 Submit 页。
        if (
            event.kind in ("page_up", "page_down", "home", "end")
            and submit_scroll_ref is not None
        ):
            try:
                rows = max(
                    1, int(submit_rows_limit) if submit_rows_limit else
                    _submit_rows_limit(),
                )
            except (TypeError, ValueError, OverflowError):
                rows = _submit_rows_limit()
            # ★ 2026-10-23（多行答案）：按实际答案行数计算最大滚动题号
            #   （固定行 5 + 每题至少 1 行问题 + 动态答案行）。
            max_scroll = _submit_max_scroll(states, rows)
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
        # ★ 2026-10-23（用户需求：并发 tab 切换保留自定义草稿）：切 tab
        #   前**挂起**输入（保留草稿 + 光标 + active 标记），切回该 tab 时
        #   自动恢复编辑区；不再清空未提交文本。
        if input_mode:
            if suspend_custom is not None:
                suspend_custom()
            elif exit_custom is not None:
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
    insert_text=None, backspace=None, delete_forward=None,
    move_left=None, move_right=None, move_home=None, move_end=None,
    move_up=None, move_down=None, insert_newline=None, submit=None,
) -> bool:
    """自定义输入编辑模式的顶层按键（模块级）。

    ★ 2026-10-23（用户需求：光标 + 多行输入）：不再使用单行 ``TextInput``
    ——字符/粘贴/退格/Delete/方向键/Home/End/上下行全部由本 handler 直接
    编辑 ``custom_text`` + ``custom_cursor``，支持多行显示与行内光标。

    接管：
      - ``Shift+Enter``（``kind="enter", modifier=2``）：插入换行；
      - ``Enter``（含 CSI u modifier=1/3/5）：提交当前答案；
      - 可打印字符 / 多字符粘贴：插入光标位置；
      - ``Backspace`` / ``Delete``：删除光标前/后字符；
      - ``←/→``：移动光标；``↑/↓``：上下行移动；``Home/End``：行首/行尾；
      - ``Ctrl+G``（``\\x07``）：外部编辑器（``EDITOR``，默认 vim）；
      - ``Ctrl+U``（``\\x15`` / ``delete`` modifier=2）：清空；
      - ``Ctrl+W``（``\\x17`` / ``delete`` modifier=1）：删除光标前一个词；
      - ``Esc``：退出输入、返回选项（**不取消问题**——再次 Esc 才由
        SelectInput/MultiSelect 的 onCancel 取消）。

    Args:
        edit_custom: Ctrl+G 回调（无参）；None 时消费但不动作（防御）。
        clear_custom: Ctrl+U 回调（无参）。
        delete_word: Ctrl+W 回调（无参）。
        insert_text: ``(text: str) -> None``——在光标处插入文本。
        backspace: 删除光标前一个字符。
        delete_forward: 删除光标处字符。
        move_left/move_right/move_home/move_end/move_up/move_down: 光标移动。
        insert_newline: 插入 ``\\n``。
        submit: 提交当前答案。
    """
    if not visible or not input_mode:
        return False
    kind = getattr(event, "kind", "")
    mod = int(getattr(event, "modifier", 0) or 0)
    # ★ 真实终端解析：Ctrl+W → ``delete`` modifier=1；Ctrl+U →
    #   ``delete`` modifier=2（见 InputParser._decode_control_char）。
    if kind == "delete":
        if mod == 1:
            if delete_word is not None:
                delete_word()
            return True
        if mod == 2:
            if clear_custom is not None:
                clear_custom()
            return True
        if mod == 3:
            # Alt+Delete / kill-line 等组合键交给上层（不误删单字符）。
            return False
        if delete_forward is not None:
            delete_forward()
            return True
        return False
    ch = getattr(event, "char", "") or ""
    if ch == "\x07":
        if edit_custom is not None:
            edit_custom()
        return True
    # ★ 2026-10-23（多行输入兼容）：传统终端（mintty/Cygwin）不区分
    #   Shift+Enter，且 Enter/Ctrl+J 都可能同发 LF——改用 Ctrl+O（0x0F）
    #   作为可靠换行键（弹窗内被本 handler 消费，不会触发 /editmsg）。
    if ch == "\x0f":
        if insert_newline is not None:
            insert_newline()
        return True
    if ch == "\x15":
        if clear_custom is not None:
            clear_custom()
        return True
    if ch == "\x17":
        if delete_word is not None:
            delete_word()
        return True
    if kind == "escape":
        if exit_custom is not None:
            exit_custom()
        return True
    if kind == "enter":
        if mod == 2:
            # Shift+Enter：插入换行（多行输入）。
            if insert_newline is not None:
                insert_newline()
            return True
        if submit is not None:
            submit()
        return True
    if kind == "paste":
        if ch and insert_text is not None:
            insert_text(ch)
            return True
        return False
    if kind == "char":
        if not ch:
            return False
        # 控制字符（Ctrl+字母等）不插入；换行/制表符按文本插入。
        if any(ord(c) < 32 and c not in ("\n", "\t") for c in ch):
            return False
        if insert_text is not None:
            insert_text(ch)
            return True
        return False
    if kind == "backspace":
        if mod not in (0, 1):
            return False
        if backspace is not None:
            backspace()
            return True
        return False
    if kind == "arrow_left":
        if mod not in (0, 1):
            return False
        if move_left is not None:
            move_left()
            return True
        return False
    if kind == "arrow_right":
        if mod not in (0, 1):
            return False
        if move_right is not None:
            move_right()
            return True
        return False
    if kind == "home":
        if mod not in (0, 1):
            return False
        if move_home is not None:
            move_home()
            return True
        return False
    if kind == "end":
        if mod not in (0, 1):
            return False
        if move_end is not None:
            move_end()
            return True
        return False
    if kind == "arrow_up":
        if mod not in (0, 1):
            return False
        if move_up is not None:
            move_up()
            return True
        return False
    if kind == "arrow_down":
        if mod not in (0, 1):
            return False
        if move_down is not None:
            move_down()
            return True
        return False
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
        # ★ 2026-10-21（用户需求）：列表末尾的「其他（自定义输入…）」
        #   伪选项不提交——进入输入编辑模式（其文本经 TextInput 提交）。
        value = item["value"] if item is not None else None
        if (
            allow_custom and on_custom_enter is not None
            and value == _CUSTOM_VALUE
        ):
            on_custom_enter()
            return
        # ★ 2026-10-22（Claude TUI 对齐）：多选列表末尾的「提交」项——
        #   Enter 提交当前勾选集合（与直接 Enter 提交兼容并存）。
        if value == _SUBMIT_VALUE:
            result = (
                list(checked_values_getter())
                if checked_values_getter is not None else []
            )
            if multi_mode:
                _commit_answer(result, "confirmed")
            else:
                _commit(result, "confirmed")
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
    # ★ 2026-10-23：选项对象以 label 作为结果值（说明不进入返回值）。
    label = _option_label(options[idx])
    on_confirm({"label": label, "value": label})
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


def _result_rows(
    result, width: int, key_prefix: str, style: Style, *,
    first_prefix: str = "  \u2192 ",
    other_prefix: str = "  \u00b7 ",
    continuation: str = "    ",
    first_suffix: str = "",
) -> list:
    """把选择结果按原文逐行渲染（多行自定义输入完整显示）。

    ★ 2026-10-23（用户需求：输入完了要显示所有的输入（多行））：不再用
    ``、`` 拼接并折叠换行——每个结果项的第一行加前缀，后续行缩进对齐；
    自定义多行答案逐行输出，不截断、不折叠。
    """
    width_i = max(1, int(width))
    items = [str(x) for x in (result or [])]
    if not items:
        items = ["（无）"]
    rows: list = []
    first_row = True
    for i, item in enumerate(items):
        for j, line in enumerate(_normalize_multiline(item).split("\n")):
            if j == 0:
                prefix = first_prefix if i == 0 else other_prefix
            else:
                prefix = continuation
            text = prefix + line
            if first_row and first_suffix:
                text += first_suffix
            rows.append(h(TEXT, {
                "children": _truncate_width(text, width_i),
                "style": style,
                "height": 1,
                "key": f"{key_prefix}-{i}-{j}",
                "textWrap": "truncate-end",
            }))
            first_row = False
    return rows


def _answer_rows_for_state(
    s, width: int, key_prefix: str = "us-submit-a",
    max_rows: "int | None" = None,
) -> list:
    """Submit 页单个问题的答案行（按实际行数，多行答案逐行显示）。"""
    width_i = max(1, int(width))
    answered = bool(getattr(s, "answered", False))
    done = bool(getattr(s, "done", False))
    action = str(getattr(s, "action", "") or "")
    if answered or done:
        if action == "cancel":
            rows = [h(TEXT, {
                "children": _truncate_width("  \u2192 \u2717 取消", width_i),
                "style": _S_TAB_ANSWERED,
                "height": 1,
                "key": f"{key_prefix}-cancel",
                "textWrap": "truncate-end",
            })]
        elif action == "timeout":
            rows = [h(TEXT, {
                "children": _truncate_width("  \u2192 \u23f1 超时", width_i),
                "style": _S_TAB_ANSWERED,
                "height": 1,
                "key": f"{key_prefix}-timeout",
                "textWrap": "truncate-end",
            })]
        else:
            result = list(getattr(s, "result", None) or [])
            custom = str(getattr(s, "custom", "") or "")
            suffix = "（自定义）" if custom and len(result) == 1 else ""
            rows = _result_rows(
                result, width_i, key_prefix, _S_DONE_RESULT,
                first_suffix=suffix,
            )
    else:
        default_txt = "、".join(
            _one_line(x) for x in (getattr(s, "default_options", None) or [])
        )
        status = (
            f"未回答（默认：{default_txt}）" if default_txt
            else "未回答（将返回空）"
        )
        rows = [h(TEXT, {
            "children": _truncate_width(f"  \u2192 {status}", width_i),
            "style": _S_WARN if default_txt else _S_DESC_DIM,
            "height": 1,
            "key": f"{key_prefix}-empty",
            "textWrap": "truncate-end",
        })]
    if max_rows is not None and len(rows) > max(1, int(max_rows)):
        keep = max(1, int(max_rows))
        hidden = len(rows) - (keep - 1)
        rows = rows[:keep - 1] + [h(TEXT, {
            "children": _truncate_width(
                f"    \u2026（还有 {hidden} 行）", width_i,
            ),
            "style": _S_DESC_DIM,
            "height": 1,
            "key": f"{key_prefix}-more",
            "textWrap": "truncate-end",
        })]
    return rows


def _submit_max_scroll(states: list, max_rows: "int | None") -> int:
    """Submit 页最大滚动题号（按实际答案行数计算，保证能翻到最后一题）。"""
    if max_rows is None:
        return 0
    try:
        budget = max(1, int(max_rows) - 5)
    except (TypeError, ValueError, OverflowError):
        budget = max(1, _submit_rows_limit() - 5)
    n = len(states)
    if n <= 0:
        return 0

    def _page_end(start: int) -> int:
        used = 0
        i = start
        while i < n:
            arows = _answer_rows_for_state(states[i], 80, "sim")
            need = 1 + len(arows)
            remaining = budget - used
            if remaining <= 0:
                return i - 1
            if remaining <= 1 or need > remaining:
                return i
            used += need
            i += 1
        return n - 1

    max_scroll = 0
    for s in range(n):
        if _page_end(s) >= n - 1:
            max_scroll = s
    return max_scroll


def _build_submit_page(
    states: list, width: int, scroll: int = 0, max_rows: int | None = None,
    choice: int = 0,
) -> list:
    """Submit 页行构建（Claude ``Review your answers`` 风格）。

    ★ 2026-08-19（用户需求：增加一个 tab 页面给玩家是否提交）。
    ★ 2026-10-22（用户需求：TUI 与 Claude 对齐，实机 v2.1.308）：布局改为
      ``检查你的答案`` 标题 + 每题 ``• 问题`` / ``  → 答案``（答案绿色）+
      ``准备好提交答案了吗？`` + 两项选择 ``> 1. 提交答案`` / ``  2. 取消``。
      每题占 2 行；``PgUp/PgDn/Home/End`` 滚动（大量并发问题时），
      数字键 1-9 跳题（由 ``_handle_tab_event`` 处理）。

    Args:
        states: 问题 state 列表。
        width: 终端宽度。
        scroll: 滚动偏移（按问题计）。
        max_rows: 可用行预算（None = 全部显示）。
        choice: 底部当前选项（0=提交答案，1=取消）。
    """
    try:
        width_i = max(1, int(width))
    except (TypeError, ValueError, OverflowError):
        width_i = 80
    n = len(states)
    if max_rows is None:
        budget = None
    else:
        try:
            budget = max(1, int(max_rows))
        except (TypeError, ValueError, OverflowError):
            budget = _submit_rows_limit()
    # ★ 2026-10-23（用户需求：多行答案完整显示）：分页按**实际答案行数**
    #   计算（固定行 = 标题 1 + 准备提示 1 + 两个选项 2 + 次提示 1 = 5）。
    max_scroll = _submit_max_scroll(states, max_rows)
    try:
        start = int(scroll or 0)
    except (TypeError, ValueError, OverflowError):
        start = 0
    start = max(0, min(start, max_scroll))
    end = start
    answered_count = sum(
        1 for s in states
        if getattr(s, "answered", False) or getattr(s, "done", False)
    )
    rows: list = []
    # ── 标题（右侧已答计数）──
    title_txt = "检查你的答案"
    meta = f"已答 {answered_count}/{n}"
    gap = max(1, width_i - wcswidth_simple(title_txt) - wcswidth_simple(meta))
    rows.append(h(TEXT, {
        "styled": truncate_runs_ellipsis([
            StyledRun(title_txt, _S_QUESTION),
            StyledRun(" " * gap, _S_DIM),
            StyledRun(meta, _S_META),
        ], width_i),
        "height": 1,
        "key": "us-submit-title",
        "textWrap": "truncate-end",
    }))
    # ── 每题：``• 问题`` + 答案行（多行答案逐行显示，行数动态参与分页）──
    used = 0
    for i in range(start, n):
        remaining = None if budget is None else budget - 5 - used
        if remaining is not None and remaining <= 0:
            break
        s = states[i]
        title_q = _one_line(getattr(s, "title", "") or "选择")
        q_row = h(TEXT, {
            "children": _truncate_width(f"\u2022 {title_q}", width_i),
            "style": _S_ITEM_IDLE,
            "height": 1,
            "key": f"us-submit-q-{i}",
            "textWrap": "truncate-end",
        })
        if remaining is not None and remaining <= 1:
            rows.append(q_row)
            used += 1
            end = i + 1
            break
        arows = _answer_rows_for_state(
            s, width_i, f"us-submit-a-{i}",
            max_rows=None if remaining is None else remaining - 1,
        )
        rows.append(q_row)
        rows.extend(arows)
        used += 1 + len(arows)
        end = i + 1
        if budget is not None and used >= max(1, budget - 5):
            break
    if end == start and n:
        # 预算极矮（< 6 行）：至少显示第一题的问题行，保证页面非空。
        i = start
        rows.append(h(TEXT, {
            "children": _truncate_width(
                f"\u2022 {_one_line(getattr(states[i], 'title', '') or '选择')}",
                width_i,
            ),
            "style": _S_ITEM_IDLE,
            "height": 1,
            "key": f"us-submit-q-{i}",
            "textWrap": "truncate-end",
        }))
        end = i + 1
    # ── 准备提交提示 ──
    rows.append(h(TEXT, {
        "children": _truncate_width(
            "准备好提交答案了吗？", width_i,
        ),
        "style": _S_DESC_DIM,
        "height": 1,
        "key": "us-submit-ready",
        "textWrap": "truncate-end",
    }))
    # ── 底部两项（Claude：> 1. Submit answers / 2. Cancel）──
    for opt_idx, label in enumerate(("提交答案", "取消")):
        is_sel = int(choice) == opt_idx
        runs = _option_runs(
            _CURSOR if is_sel else _CURSOR_PAD,
            f"{opt_idx + 1}. ", "", label,
            cursor_style=_S_SEL_ACCENT if is_sel else _S_NUM,
            num_style=_S_SEL_ACCENT if is_sel else _S_NUM,
            mark_style=_S_NUM,
            text_style=_S_SEL_TEXT if is_sel else _S_ITEM_IDLE,
            target_w=width_i,
            pad_style=_S_SEL_PAD if is_sel else None,
        )
        rows.append(h(TEXT, {
            "styled": runs, "height": 1,
            "key": f"us-submit-opt-{opt_idx}",
            "textWrap": "truncate-end",
        }))
    # ── 次提示（滚动 / 跳题 / 已答进度）──
    if max_scroll > 0:
        secondary = (
            f"已答 {answered_count}/{n} · 显示 {start + 1}-{end}/{n}"
            " · PgUp/PgDn 滚动 · 1-9 跳题"
        )
    else:
        secondary = (
            f"已答 {answered_count}/{n} · 未回答项将使用默认选项（若有）"
            " · 1-9 跳题"
        )
    rows.append(h(TEXT, {
        "children": _truncate_width(secondary, width_i),
        "style": _S_HINT2,
        "height": 1,
        "key": "us-submit-hint2",
        "textWrap": "truncate-end",
    }))
    return rows


def _build_hints(
    us, *, multi: bool, allow_custom: bool, multi_mode: bool,
    custom_editing: bool, editor_error: str, done: bool, checked_count: int,
) -> tuple[str, str]:
    """主/次两行提示（Claude 风格：首行 dim 主操作，次行更暗快捷键）。

    ★ 2026-10-22（用户需求：TUI 与 Claude 对齐，实机 v2.1.308）：Claude
    底部只有一行 dim 提示（``Enter to select · Tab/Arrow keys to navigate ·
    Esc to cancel``）。项目保留第二行扩展快捷键（数字快选 / a/c / PgUp /
    Ctrl+G 等），但首行改为 Claude 结构的单行主提示。
    """
    if custom_editing and not done and allow_custom:
        if editor_error:
            # 错误提示前置，避免长提示在窄屏被截断后看不见。
            primary = (
                f"! {editor_error} · Enter 提交 · Shift+Enter/Ctrl+O 换行"
                " · Esc 返回选项 · Ctrl+G vim 编辑"
            )
        else:
            primary = (
                "Enter 提交 · Shift+Enter/Ctrl+O 换行"
                " · Esc 返回选项 · Ctrl+G vim 编辑"
            )
        secondary = "Ctrl+U 清空 · Ctrl+W 删词"
        if multi_mode:
            secondary += " · Tab 切换问题"
        secondary += " · ←→ 移动光标 · ↑↓ 切换行"
        return primary, secondary
    if done:
        return "\u2713 已提交（锁定）", ""
    if multi:
        primary = "Enter 选择 · Space 勾选 · Tab/\u2190\u2192 切换 · Esc 取消"
        if checked_count <= 0:
            primary = (
                "Enter 选择 · Space 勾选（未勾选） · Enter 提交空"
                " · Tab/\u2190\u2192 切换 · Esc 取消"
            )
        if allow_custom:
            primary += " · 其他=自定义"
        secondary = (
            "\u2191\u2193/jk 移动 · 1-9 勾选 · a 全选 · c 清空"
            " · g/G/Home/End 首末 · PgUp/PgDn 翻页"
        )
        return primary, secondary
    primary = "Enter 选择 · Tab/\u2190\u2192 切换 · Esc 取消"
    if allow_custom:
        primary += " · 其他=自定义"
    secondary = "\u2191\u2193/jk 移动 · g/G/Home/End 首末 · PgUp/PgDn 翻页 · 1-9 快选"
    return primary, secondary


def UserSelectPopup(props) -> object:
    """React Ink 用户选择弹窗组件（多问题 tab，SelectInput/MultiSelect 控件化）。

    ★ 2026-10-21（用户需求）：支持用户输入列表外的自定义答案——状态
    ``us.allow_custom`` 为 True 时列表末尾渲染「其他（自定义输入…）」
    伪选项；Enter 进入多行编辑模式，Enter 提交 / Esc 返回；输入模式内
    Ctrl+G 调用外部编辑器（``EDITOR``，默认 vim，返回保留换行）。
    ★ 2026-10-23（用户需求：光标 + 多行输入 + 输入中不计时）：空输入框
    也显示光标块；``Shift+Enter`` / ``Ctrl+O`` 换行，``Enter`` 提交，
    ``←→↑↓`` 移动光标；输入内容逐行显示；输入模式期间 ``us.typing=True``
    暂停空闲超时（标题区不显示/递减 ⏱），提交/退出后恢复。

    ★ 2026-10-21（继续优化）：Submit 页分页滚动 + 数字键 1-9 跳题；长列表
    显示上方/下方隐藏项数；tab 显示回答摘要；多选 `a` 全选/`c` 清空；
    输入模式 `Ctrl+U` 清空 / `Ctrl+W` 删词。
    ★ 2026-10-22（用户需求：TUI 与 Claude 对齐）：标题区 chip + 独立问题行，
    选项 ``> 序号. 标签`` + 说明缩进在选项下方，多选 ``[√]/[ ]``，tab 短标签
    chip，主提示 ``↑/↓ 选择 · Enter 确认 · Esc 取消``。

    Props:
        model: AppModel 实例（读 ``model.user_selects`` 并发队列；空时回退
            ``model.user_select`` 单例——兼容旧调用/测试）。
        width: 终端宽度（所有行按此截断，保证行宽不超终端）。

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
    #   - custom_text / custom_cursor 为受控原始文本与字符光标（多行编辑）；
    #   - state setter 仅负责触发重渲染（ref 即时生效）。
    custom_editing, set_custom_editing = use_state(False)
    custom_text, set_custom_text = use_state("")
    custom_cursor, set_custom_cursor = use_state(0)
    custom_editing_ref = use_ref(False)
    custom_editing_ref.current = custom_editing
    # ★ 2026-10-23（用户需求：光标 + 多行输入）：原始文本/光标 ref（事件期
    #   权威源）与编辑器错误提示 state。
    custom_text_ref = use_ref("")
    custom_cursor_ref = use_ref(0)
    custom_editor_error, set_custom_editor_error = use_state("")
    # ★ 2026-10-21（继续优化）：Submit 页滚动偏移 + 当前列表可见窗口
    #   （offset, count）——Submit 页大量问题时滚动；列表上方显示隐藏项数。
    submit_scroll, set_submit_scroll = use_state(0)
    submit_scroll_ref = use_ref(0)
    # ★ 2026-10-22（Claude TUI 对齐）：Submit 页底部两项当前选择
    #   （0=提交答案，1=取消）——↑↓ 切换，Enter 执行。
    submit_choice, set_submit_choice = use_state(0)
    submit_choice_ref = use_ref(0)
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
    # ★ 2026-10-22（Claude TUI 对齐）：同时把底部选项复位到「提交答案」。
    if is_submit and not prev_is_submit_ref.current:
        submit_scroll_ref.current = 0
        if submit_scroll != 0:
            set_submit_scroll(0)
        submit_choice_ref.current = 0
        if submit_choice != 0:
            set_submit_choice(0)
    prev_is_submit_ref.current = is_submit

    us = states[cur_active] if (states and not is_submit) else None
    prev_us = us_ref.current
    fresh_us = prev_us is not us
    if fresh_us:
        us_ref.current = us
        # ★ 2026-10-23（用户需求：并发 tab 切换保留自定义输入）：
        #   切走时把当前草稿/光标写回旧 state（并标记 active），切回时
        #   恢复该 tab 自己的草稿、光标与编辑区；不再清空输入内容。
        if prev_us is not None:
            if custom_editing_ref.current:
                prev_us.custom_draft = custom_text_ref.current or ""
                prev_us.custom_draft_cursor = custom_cursor_ref.current
                prev_us.custom_active = True
                # 有活动草稿的 tab 在后台也暂停计时（避免切走期间超时丢草稿）。
                prev_us.typing = True
            # 未处于输入模式时不动 prev_us.typing：可能由外部编辑器（Ctrl+G）
            # 或 _suspend_custom/_exit_custom 维护。
        draft = ""
        draft_cursor = 0
        resume_editing = False
        if us is not None:
            draft = str(getattr(us, "custom_draft", "") or "")
            try:
                draft_cursor = int(getattr(us, "custom_draft_cursor", 0) or 0)
            except (TypeError, ValueError):
                draft_cursor = len(draft)
            draft_cursor = max(0, min(draft_cursor, len(draft)))
            allow_custom_us = bool(getattr(us, "allow_custom", False))
            resume_editing = (
                bool(getattr(us, "custom_active", False)) and allow_custom_us
            )
        custom_editing_ref.current = resume_editing
        set_custom_editing(resume_editing)
        custom_text_ref.current = draft
        set_custom_text(draft)
        custom_cursor_ref.current = draft_cursor
        set_custom_cursor(draft_cursor)
        set_custom_editor_error("")
        # ★ 2026-10-21（继续优化）：切题时列表窗口回到顶部（新控件上报后覆盖）。
        if list_window_ref.current != (0, 0):
            list_window_ref.current = (0, 0)
            set_list_window((0, 0))
        if us is not None:
            if resume_editing:
                total_us = len(getattr(us, "options", None) or [])
                us.selected = total_us
                us.typing = True
                if selected != total_us:
                    set_selected(total_us)
            else:
                # 不覆盖 ``typing``：外部编辑器（Ctrl+G）运行期间它为 True；
                # 挂起/退出自定义输入时已由 _suspend_custom/_exit_custom 维护。
                try:
                    sel_us = int(getattr(us, "selected", 0) or 0)
                except (TypeError, ValueError):
                    sel_us = 0
                if selected != sel_us:
                    set_selected(sel_us)
    # ── 当前问题基础数据（提前计算：自定义输入回调/渲染共用） ──
    # ★ 2026-10-23（用户需求：说明并入选项对象）：options 为
    #   ``UserSelectOption(label, description)`` 列表，label 作为控件值与
    #   返回结果，description 仅供渲染。
    options = list(us.options) if us is not None else []
    total = len(options)
    multi = bool(getattr(us, "multi_select", False)) if us is not None else False
    # ★ 工具路径 allow_custom=True（列表末尾追加「其他（自定义输入…）」伪选项）；
    #   命令系统选择路径默认 False（index 契约不变）。
    allow_custom = bool(getattr(us, "allow_custom", False)) if us is not None else False
    # ★ 2026-10-22（Claude TUI 对齐）：多选列表末尾追加「提交」项
    #   （Claude 多选列表的 Submit 行）；Enter 直接提交仍保留兼容。
    show_submit_item = bool(multi)
    total_items = max(
        1, total + (1 if allow_custom else 0) + (1 if show_submit_item else 0)
    )
    # ★ 模态底部视图声明（2026-08-17 通用机制）：visible 时独占键盘输入——
    #   未消费按键被 input router 吞掉（不落入输入缓冲；输入区已不渲染，
    #   字符落入输入缓冲会「看不见地」改变用户输入）。visible=False 时 hook
    #   不参与路由（组件不渲染/已关闭，零影响）。与 use_fullscreen 同节点
    #   类型（模态输入接管语义，见 _hooks_input.use_modal docstring）。
    use_modal_scope(visible, fullscreen=False)

    # ── 空闲计时（2026-10-22 用户需求：删除 ``timeout`` 参数，改成「不操作
    #   界面一分钟后才算超时」）──
    # 弹窗每收到一次按键/鼠标操作，就把各未提交问题的倒计时重置为 1 分钟
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
    # 列表末尾伪选项「其他（自定义输入…）」Enter 进入编辑模式：
    #   - 弹窗选项控件失焦（focus=False），TextInput 接管字符/退格/方向键；
    #   - 父 handler 只消费 Esc（退出输入）与 Tab（切换问题并退出输入）；
    #   - ★ 2026-10-22：输入期间每次按键都重置 1 分钟空闲倒计时（不再整段
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
                out.append(_option_label(options[idx]))
        return out

    def _on_checked_change(values) -> None:
        """多选勾选集合变化：同步 us.checked（自定义提交 / 重答恢复用）。"""
        if us is None:
            return
        vals = [
            v for v in list(values or [])
            if v not in (_CUSTOM_VALUE, _SUBMIT_VALUE)
        ]
        us.checked = [
            i for i, opt in enumerate(options)
            if _option_label(opt) in vals
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
        """列表末尾伪选项行（自定义输入 / 提交）不可勾选。"""
        return idx < total

    def _save_custom_to_state(active: bool) -> None:
        """把当前草稿/光标写回本题 state（跨 tab 切换保留）。"""
        if us is None:
            return
        us.custom_draft = custom_text_ref.current or ""
        us.custom_draft_cursor = custom_cursor_ref.current
        us.custom_active = bool(active)

    def _set_custom_cursor_only(pos: int) -> None:
        """只移动光标（不改文本、不清错误提示）。"""
        text = custom_text_ref.current or ""
        cur = max(0, min(int(pos), len(text)))
        custom_cursor_ref.current = cur
        set_custom_cursor(cur)
        if us is not None:
            us.custom_draft = text
            us.custom_draft_cursor = cur
            us.custom_active = True

    def _sync_custom(text, cursor=None) -> None:
        """写入原始文本 + 光标（文本变化时清除错误提示）。"""
        raw = _normalize_multiline(text)
        custom_text_ref.current = raw
        set_custom_text(raw)
        cur = len(raw) if cursor is None else int(cursor)
        cur = max(0, min(cur, len(raw)))
        custom_cursor_ref.current = cur
        set_custom_cursor(cur)
        set_custom_editor_error("")
        if us is not None:
            us.custom_draft = raw
            us.custom_draft_cursor = cur
            us.custom_active = True

    def _suspend_custom() -> None:
        """切 tab：挂起自定义输入（保留草稿/光标；切回自动恢复编辑区）。"""
        _save_custom_to_state(True)
        custom_editing_ref.current = False
        set_custom_editing(False)
        # 保留本地 text/cursor（切回时由 fresh_us 从 state 恢复）。
        set_custom_editor_error("")
        if us is not None:
            # 草稿仍处于活动状态：后台也暂停该 tab 的计时，防止切走期间
            # 超时把草稿作废；切回后继续编辑，Esc/提交后恢复计时。
            us.typing = True

    def _exit_custom(clear: bool = False) -> None:
        """退出自定义输入编辑模式（Esc/提交；保留草稿，但不自动恢复）。

        ★ 2026-10-23（用户需求：输入中不要继续倒计时 + 切换 tab 保留内容）：
        输入模式期间 ``us.typing=True`` 暂停空闲超时；退出/提交后复位。
        默认保留草稿（``custom_active=False``），切回该 tab 后可再次进入
        编辑区继续编辑；``clear=True`` 才清空草稿。
        """
        if clear:
            custom_text_ref.current = ""
            set_custom_text("")
            custom_cursor_ref.current = 0
            set_custom_cursor(0)
            if us is not None:
                us.custom_draft = ""
                us.custom_draft_cursor = 0
                us.custom_active = False
        else:
            _save_custom_to_state(False)
        custom_editing_ref.current = False
        set_custom_editing(False)
        set_custom_editor_error("")
        if us is not None:
            us.typing = False

    def _enter_custom() -> None:
        """进入自定义输入编辑模式（恢复本题草稿；高亮伪选项行）。

        ★ 2026-10-23（用户需求：再次编辑不能清空原来的内容）：从
        ``us.custom_draft`` / ``custom_draft_cursor`` 恢复上次输入；
        没有草稿时从空文本开始。
        """
        if us is None or not allow_custom:
            return
        draft = str(getattr(us, "custom_draft", "") or "")
        try:
            cur = int(getattr(us, "custom_draft_cursor", 0) or 0)
        except (TypeError, ValueError):
            cur = len(draft)
        cur = max(0, min(cur, len(draft)))
        custom_editing_ref.current = True
        set_custom_editing(True)
        custom_text_ref.current = draft
        set_custom_text(draft)
        custom_cursor_ref.current = cur
        set_custom_cursor(cur)
        set_custom_editor_error("")
        # 输入模式期间暂停空闲超时（标题区不再显示/递减 ⏱ 倒计时）。
        us.typing = True
        us.custom_active = True
        # 视觉：高亮列表末尾的「其他（自定义输入…）」行（普通项索引 0..total-1）。
        us.selected = total
        set_selected(total)

    def _insert_custom_text(value) -> None:
        """在光标处插入文本（支持多字符粘贴与换行）。"""
        raw = _normalize_multiline(value)
        if not raw:
            return
        text = custom_text_ref.current or ""
        cur = max(0, min(custom_cursor_ref.current, len(text)))
        _sync_custom(text[:cur] + raw + text[cur:], cur + len(raw))

    def _backspace_custom() -> None:
        """删除光标前一个字符。"""
        text = custom_text_ref.current or ""
        cur = max(0, min(custom_cursor_ref.current, len(text)))
        if cur <= 0:
            return
        _sync_custom(text[:cur - 1] + text[cur:], cur - 1)

    def _delete_forward_custom() -> None:
        """删除光标处字符。"""
        text = custom_text_ref.current or ""
        cur = max(0, min(custom_cursor_ref.current, len(text)))
        if cur >= len(text):
            return
        _sync_custom(text[:cur] + text[cur + 1:], cur)

    def _move_custom_left() -> None:
        _set_custom_cursor_only(custom_cursor_ref.current - 1)

    def _move_custom_right() -> None:
        _set_custom_cursor_only(custom_cursor_ref.current + 1)

    def _move_custom_home() -> None:
        text = custom_text_ref.current or ""
        start, _end = _line_bounds(text, custom_cursor_ref.current)
        _set_custom_cursor_only(start)

    def _move_custom_end() -> None:
        text = custom_text_ref.current or ""
        _start, end = _line_bounds(text, custom_cursor_ref.current)
        _set_custom_cursor_only(end)

    def _move_custom_vertical(delta: int) -> None:
        """↑/↓：在当前硬换行行与相邻行之间移动（保持列号，越界钳制）。"""
        text = custom_text_ref.current or ""
        cur = max(0, min(custom_cursor_ref.current, len(text)))
        start, end = _line_bounds(text, cur)
        col = cur - start
        if delta < 0:
            if start <= 0:
                return
            prev_end = start - 1
            prev_start, _ = _line_bounds(text, prev_end)
            _set_custom_cursor_only(prev_start + min(col, prev_end - prev_start))
            return
        if end >= len(text):
            return
        next_start = end + 1
        _ns, next_end = _line_bounds(text, next_start)
        _set_custom_cursor_only(next_start + min(col, next_end - next_start))

    def _move_custom_up() -> None:
        _move_custom_vertical(-1)

    def _move_custom_down() -> None:
        _move_custom_vertical(1)

    def _clear_custom() -> None:
        """Ctrl+U：清空当前自定义答案输入。"""
        _sync_custom("", 0)

    def _delete_custom_word() -> None:
        """Ctrl+W：删除光标前的词（按空白边界；CJK 无空格时删整段）。"""
        text = custom_text_ref.current or ""
        cur = max(0, min(custom_cursor_ref.current, len(text)))
        if cur <= 0:
            return
        i = cur
        while i > 0 and text[i - 1].isspace():
            i -= 1
        while i > 0 and not text[i - 1].isspace():
            i -= 1
        _sync_custom(text[:i] + text[cur:], i)

    def _insert_custom_newline() -> None:
        """Shift+Enter：插入换行（多行输入）。"""
        _insert_custom_text("\n")

    def _open_custom_editor() -> None:
        """Ctrl+G：用外部编辑器（``EDITOR``，默认 vim）编辑自定义答案。

        编辑器返回文本后回填到输入区（仍保持输入模式，Enter 确认）；
        取消/失败（None）保持原文本并显示错误提示。

        ★ 2026-10-23：输入模式本身已置 ``us.typing=True`` 暂停超时；编辑器
        运行期间保持该标志，返回后仅在仍处于输入模式时维持暂停。
        """
        if us is None or not custom_editing_ref.current:
            return
        before = custom_text_ref.current or ""
        us.typing = True
        try:
            edited = _run_external_editor(before)
        finally:
            if us is not None:
                us.typing = bool(custom_editing_ref.current)
        if edited is None:
            set_custom_editor_error("编辑器不可用，已保留原文本")
            return
        raw = _normalize_multiline(edited)
        _sync_custom(raw, len(raw))

    def _submit_custom(*_args) -> None:
        """Enter 提交自由输入答案（空文本提示并保持输入模式）。

        ★ 2026-10-23：提交使用 ``custom_text_ref`` 的**原始多行文本**；
        空/纯空白输入不再静默无反应——设置错误提示（主提示行可见），
        并保持输入模式与暂停计时。
        """
        if us is None or not custom_editing_ref.current:
            return
        raw = _normalize_multiline(custom_text_ref.current or "")
        value = raw.strip()
        if not value:
            set_custom_editor_error("输入不能为空")
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
        # ★ 2026-10-23（用户需求：再次编辑不能清空原来的内容）：提交后把
        #   去空白后的答案保留为本 tab 草稿（active=False：切回不自动进入
        #   编辑区，但再次进入自定义输入时会预填该答案）。
        us.custom_draft = value
        us.custom_draft_cursor = len(value)
        us.custom_active = False

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
    # 父组件 use_input 先于子控件（SelectInput/MultiSelect）被 router 调用
    # （前序收集）——输入模式下所有编辑键由本 handler 消费；Shift+Enter
    # 插入换行，Enter 提交，Esc 退出输入（不取消问题）。
    use_input(
        lambda ev: _handle_custom_input_event(
            ev, visible=visible, input_mode=custom_editing_ref.current,
            exit_custom=_exit_custom, edit_custom=_open_custom_editor,
            clear_custom=_clear_custom, delete_word=_delete_custom_word,
            insert_text=_insert_custom_text, backspace=_backspace_custom,
            delete_forward=_delete_forward_custom,
            move_left=_move_custom_left, move_right=_move_custom_right,
            move_home=_move_custom_home, move_end=_move_custom_end,
            move_up=_move_custom_up, move_down=_move_custom_down,
            insert_newline=_insert_custom_newline, submit=_submit_custom,
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
    #   输入（**保留草稿** + 恢复自动超时）——切回该 tab 自动恢复编辑区。
    use_input(
        lambda ev: _handle_tab_event(
            ev, visible=visible, is_submit=is_submit, multi_mode=multi_mode,
            tab_count=tab_count, active_ref=active_ref, set_active=set_active,
            states=states, model=model,
            input_mode=custom_editing_ref.current, exit_custom=_exit_custom,
            suspend_custom=_suspend_custom,
            submit_scroll_ref=submit_scroll_ref,
            set_submit_scroll=set_submit_scroll,
            submit_rows_limit=_submit_rows_limit(),
            submit_choice_ref=submit_choice_ref,
            set_submit_choice=set_submit_choice,
        ),
        visible,
    )

    if not visible:
        return empty_modal_frame()

    rows: list = []

    # ── 并发 tab 栏（仅多问题显示；单问题零额外行——旧行为完全保留） ──
    if multi_mode:
        rows.append(_build_tab_bar(states, cur_active, width))
        # Claude 布局：tab 栏与正文之间空一行。
        rows.append(h(TEXT, {"children": "", "height": 1, "key": "us-tab-gap"}))

    # ══════════════ Submit 页（2026-08-19 用户需求：增加一个 tab
    # 页面给玩家是否提交）══════════════════
    if is_submit:
        rows.extend(_build_submit_page(
            states, width, scroll=submit_scroll_ref.current,
            max_rows=_submit_rows_limit(),
            choice=submit_choice_ref.current,
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
    checked_idx = _checked_indices(us, total)
    checked_count = len(checked_idx)

    # ── Claude 风格：选项 + 说明同列（说明缩进显示在选项下方） ──
    width_i = int(width) if width and width > 0 else 80
    opt_w = max(1, width_i)

    # ★ 2026-10-23（用户需求：说明支持多行显示）：仅选中项按原文展开多行，
    #   其余选项折叠单行——选中项说明的行数参与列表高度预算（见下）。
    sel_desc_full_rows = 0
    if 0 <= cur < total:
        sel_desc_rows_text = _wrap_description(
            _option_description(options[cur]),
            max(
                1,
                opt_w - _option_indent(_num_width(total_items), multi),
            ),
        )
        sel_desc_full_rows = len(sel_desc_rows_text)
    elif (
        not custom_editing_ref.current
        and us is not None
        and str(getattr(us, "custom_draft", "") or "").strip()
    ):
        # ★ 2026-10-23（用户需求：没输入时也显示已输入内容）：选中自定义行
        #   时，草稿说明按原文多行展开——其行数同样参与高度预算。
        sel_desc_full_rows = len(_wrap_description(
            str(getattr(us, "custom_draft", "") or ""),
            max(1, opt_w - _option_indent(_num_width(total_items), multi)),
        ))

    # 主/次两行提示（先算：决定可见项行预算）
    hint_primary, hint_secondary = _build_hints(
        us, multi=multi, allow_custom=allow_custom, multi_mode=multi_mode,
        custom_editing=custom_editing_ref.current,
        editor_error=custom_editor_error, done=done,
        checked_count=checked_count,
    )

    # ── 顶部：Claude 风格问题行（无 chip / 无分割线；元信息右对齐） ──
    q_pos = (cur_active + 1, len(states)) if multi_mode else None
    pos_label = None
    if cur >= total:
        if show_submit_item and cur == total_items - 1:
            pos_label = "提交"
        elif allow_custom:
            pos_label = "自定义"
    header_rows = _build_header(
        us, cur, total, multi, checked_count, done, width_i,
        q_pos=q_pos, pos_label=pos_label,
    )
    rows.extend(header_rows)

    # ── 选项控件（SelectInput/MultiSelect 标准控件） ──
    # done（已提交终态）：只读结果行；answered（可重答）与未回答：渲染控件。
    if done:
        # 已提交 tab：只读结果行（绿色，按原文多行显示），不渲染交互控件
        rows.extend(_result_rows(
            getattr(us, "result", None) or [], width_i,
            "us-done-result", _S_DONE_RESULT,
        ))
    else:
        # ★ 2026-10-23：选项对象 → 控件项（value 用 label，说明不参与选择）。
        items = [
            {"label": _option_label(opt), "value": _option_label(opt)}
            for opt in options
        ]
        # ★ 2026-10-21（用户需求）：列表末尾追加「其他（自定义输入…）」
        #   伪选项（仅工具路径 allow_custom=True；命令选择路径不追加）。
        if allow_custom:
            items.append({"label": _CUSTOM_LABEL, "value": _CUSTOM_VALUE})
        # ★ 2026-10-22（Claude TUI 对齐）：多选列表末尾追加「提交」项
        #   （Claude 的 Submit 行）；Enter 直接提交勾选集合仍保留兼容。
        if show_submit_item:
            items.append({"label": _SUBMIT_LABEL, "value": _SUBMIT_VALUE})
        # 行预算：标题区、tab 栏、提示行、输入行先从可用高度扣除，剩余给
        # 选项列表；长列表需要滚动指示时再扣 1 行（矮终端下限 1 行）。
        # ★ 2026-10-23（说明支持多行显示）：每项行数不再恒定——普通项
        #   ≤ 2 行（选项行 + 折叠说明行），选中项 = 1 + 说明展开行数。
        #   先按「选中项占满可用高度」求说明行上限，再据此反推可容纳的
        #   项数（limit），保证无论说明多长都不会撑破底部视图。
        fixed_chrome = len(header_rows) + (2 if multi_mode else 0) + 2
        if hint_secondary:
            fixed_chrome += 1
        # ★ 2026-10-23（用户需求：输入内容多行完整显示）：编辑区行数不固定
        #   ——先把「固定 chrome + 至少 1 行选项」之外的剩余高度给编辑区，
        #   再把实际行数计入固定行数，避免长输入撑破底部视图。
        editor_rows: list = []
        if custom_editing_ref.current:
            editor_cap = max(1, _popup_item_rows() - fixed_chrome - 1)
            editor_rows = _build_custom_editor_rows(
                custom_text, custom_cursor, width_i, max_rows=editor_cap,
            )
        base_fixed_rows = fixed_chrome + len(editor_rows)
        # ★ 2026-10-23（用户需求：没输入时也显示已输入内容）：非编辑模式下
        #   自定义行会多一条「草稿说明」——参与列表高度预算（按 2 行/项）。
        custom_draft_preview = (
            "" if custom_editing_ref.current
            else str(getattr(us, "custom_draft", "") or "")
        )
        has_desc = (
            any(_option_description(opt) for opt in options)
            or bool(custom_draft_preview.strip())
        )
        other_rows = 2 if has_desc else 1
        base_limit = max(1, _popup_item_rows() - base_fixed_rows)
        sel_desc_cap = max(1, base_limit - 1)
        sel_desc_rows = min(sel_desc_full_rows, sel_desc_cap)
        est_total_rows = (
            max(0, total_items - 1) * other_rows + 1 + sel_desc_rows
        )
        need_indicator = est_total_rows > base_limit
        fixed_rows = base_fixed_rows + (1 if need_indicator else 0)
        limit_base = max(1, _popup_item_rows() - fixed_rows)
        sel_desc_cap = max(1, limit_base - 1)
        sel_desc_rows = min(sel_desc_full_rows, sel_desc_cap)
        other_budget = max(0, limit_base - 1 - sel_desc_rows)
        control_focus = not custom_editing_ref.current
        limit = max(
            1,
            min(total_items, 1 + other_budget // max(1, other_rows)),
        )
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
        # ★ 2026-10-21（继续优化）：长列表隐藏项指示（占 1 行，已在预算扣除）。
        if need_indicator:
            indicator = _build_scroll_indicator(above, below, width_i)
            if indicator is not None:
                rows.append(indicator)

        def _regular_renderer(item, idx, is_sel, is_checked=None):
            # ★ 2026-10-21：列表末尾伪选项行单独渲染。
            # ★ 2026-10-22（Claude 对齐）：自定义项 = 普通编号项（无说明行）；
            #   多选末尾「提交」项 = 无序号、缩进到 label 列。
            if idx >= total:
                if show_submit_item and idx == total_items - 1:
                    return _build_submit_row(
                        is_sel, opt_w, total, multi,
                        idx=idx, num_total=total_items,
                    )
                return _build_custom_row(
                    is_sel, opt_w, total, multi,
                    idx=idx, num_total=total_items,
                    draft=custom_draft_preview,
                    max_desc_rows=sel_desc_cap,
                )
            checked_now = _checked_indices(us, total)
            return _build_regular_row(
                options, multi, idx, cur, checked_now, opt_w,
                num_total=total_items,
                max_desc_rows=sel_desc_cap,
            )

        # 多选勾选态：控件内部与 ``us.checked`` 双向同步（控件受控 values
        # 由 us.checked 派生——数字键切换勾选后控件高亮即时回显）。
        # Enter 经 onSelect 回调当前光标项（用于伪选项判断，
        # 普通项则提交当前勾选集合）。控件 key 含当前 tab 的 seq 与 active
        # ——tab 切换时控件重挂载（use_state 重置为当前 tab 的
        # initialIndex/initialValues，已回答重答时恢复 us.selected/us.checked）。
        # ★ 2026-10-21：自定义输入模式下控件失焦（focus=False）——字符/
        #   方向键交给输入行 TextInput，列表不可交互。
        ctrl_key_suffix = (
            f"-{cur_active}" if multi_mode else ""
        )
        if multi:
            checked_vals = [_option_label(options[i]) for i in checked_idx]
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
                "renderItem": _regular_renderer,
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
                "renderItem": _regular_renderer,
                "consumeAll": True,
                "focus": control_focus,
            })
        rows.append(control)
        # ★ 2026-10-23（用户需求：说明支持多行显示）：选中项说明已在
        #   选项行下方多行展开（`_build_regular_row`），不再额外渲染
        #   列表下方的 `▸` 展开块（避免同一说明重复显示两遍）。

        # ── 自定义输入编辑区（输入模式：选项列表之后、提示行之前） ──
        # ★ 2026-10-23（用户需求：输入中显示全部内容 + 光标）：多行编辑区
        #   由 `_build_custom_editor_rows` 生成，行数已计入上面的高度预算。
        if custom_editing_ref.current and editor_rows:
            rows.extend(editor_rows)

    # ── 提示行（Claude 风格：列表与提示之间空一行；首行 dim 主操作 +
    #    次行更暗快捷键；窄屏截断） ──
    rows.append(h(TEXT, {"children": "", "height": 1, "key": "us-hint-gap"}))
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
            "style": _S_HINT2,
            "height": 1,
            "textWrap": "truncate-end",
            "key": "us-hint2",
        }))

    return h(Column, None, rows)


__all__ = ["UserSelectPopup"]
