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

  结果写入协议（P1-2，first-write-wins）：``done`` 一旦置位即终态，后续
  写入方不覆盖——组件 Enter/Escape 写入前先读 ``us.done``（若已由工具超时
  置位则放弃覆盖，保留 timeout 结果）；工具侧轮询 ``while not us.done``
  退出语义天然符合 first-write-wins（读到 done 即终止循环，不再写 timeout）。
  注意：工具侧超时分支（tools 层只读）存在「已进入超时分支、组件同时确认」
  的极端窗口仍可能覆盖组件结果，属工具侧协议限制（本组件侧防御 + 注释
  收敛该语义）。

  结果写入协议（P1-2，first-write-wins）：``done`` 一旦置位即终态，后续
  写入方不覆盖——组件 Enter/Escape 写入前先读 ``us.done``（若已由工具超时
  置位则放弃覆盖，保留 timeout 结果）；工具侧轮询 ``while not us.done``
  退出语义天然符合 first-write-wins（读到 done 即终止循环，不再写 timeout）。
  注意：工具侧超时分支（tools 层只读）存在「已进入超时分支、组件同时确认」
  的极端窗口仍可能覆盖组件结果，属工具侧协议限制（本组件侧防御 + 注释
  收敛该语义）。

依赖约束：仅依赖 app 同层（model/_theme/input_area）与 ink 框架（Layer 0/1），
无 tools 层反向依赖。
"""

from __future__ import annotations

from src.renderer.ansi.helpers import AnsiLine, truncate_line
from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from src.tui._input import _wrap_by_width
from src.tui.app.input_area import _desc_column_width, _truncate_width
from src.tui.app._theme import _S_DIM, _S_SEP
from src.tui.ink import TEXT, h, Column, Row, StyledRun
from src.tui.ink.hooks import use_state, use_input, use_ref

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


def _popup_item_rows() -> int:
    """弹窗选项/说明行数上限（超屏防护）。

    与补全弹窗 ``_completion_item_rows`` 同源（预留顶部标题 1 + 弹窗标题 1 +
    弹窗提示行 1 + 状态栏/输入区约 8 行 ≈ 11 行，2026-08-14 新增模式行
    后 +1）：选项 + 说明行数限制在 ``max(6, h - 11)``。修复前分栏说明行数
    与普通模式选项数均无上限——长说明 / 大量选项时弹窗超高，挤压甚至遮挡
    状态栏与输入区。

    Returns:
        选项（含说明）最大渲染行数。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, h - 11)
    except Exception:
        return 12


#: 每条选项最多显示行数（/editmsg 多行历史消息——防单条超长消息撑爆弹窗；
#: 超过部分以省略行提示）。单行选项（user_select 工具协议）不受影响。
_MAX_OPTION_LINES = 4


def _option_rows_of(us) -> list[list[AnsiLine]]:
    """提取每条选项的渲染行（AnsiLine 列表）。

    优先使用 ``us.option_lines``（/editmsg 用 TUI 消息渲染方式生成——
    ``build_user_line`` 每行 ``> 内容``，user_icon/user_text 色）；
    缺省（user_select 工具协议，options 为单行纯文本）时回退
    ``options[i]`` 单行。

    Returns:
        list[list[AnsiLine]] — 每条选项的 AnsiLine 行列表。
    """
    options = list(getattr(us, "options", None) or [])
    raw = getattr(us, "option_lines", None) or []
    out: list[list[AnsiLine]] = []
    for i, opt in enumerate(options):
        if i < len(raw) and raw[i]:
            out.append(list(raw[i]))
        else:
            out.append([AnsiLine.of(opt)])
    return out


def _ansi_line_to_styled(
    line: AnsiLine, prefix: str, highlighted: bool,
) -> list[StyledRun]:
    """AnsiLine → StyledRun 列表（前缀 + 内容；高亮时整行 merge 背景色）。

    与 UserSelectPopup 普通模式单选高亮语义一致：选中行整行（含前缀）
    叠加 ``_S_SEL_BG`` 背景；未选中行仅保留 AnsiLine 自身样式。

    Args:
        line: 选项渲染行（可能为多 run 样式行，如 ``> 内容``）。
        prefix: 行首前缀（选中/勾选标记或续行对齐空白）。
        highlighted: 是否为当前高亮项。

    Returns:
        非空 StyledRun 列表（空行时兜底空格 run，保持高度）。
    """
    runs: list[StyledRun] = []
    if prefix:
        runs.append(StyledRun(prefix, _S_SEL_BG if highlighted else None))
    for r in getattr(line, "runs", None) or []:
        if not r.text:
            continue
        st = r.style
        if highlighted:
            st = (st or Style()).merge(_S_SEL_BG)
        runs.append(StyledRun(r.text, st))
    if not runs:
        runs.append(StyledRun(" ", _S_SEL_BG if highlighted else None))
    return runs


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
        消费选择类按键（阻断输入框/旧路径副作用）；**Ctrl+C 放行**（P3-1——
        应用级中断快捷键不被吞，可中断工具执行）。
        """
        # 弹窗已关闭（工具清理）→ 不再处理
        if us is None or not us.visible or us.done:
            return True
        # ★ P3-1（Ctrl+C 吞键）：``\x03`` 放行（return False 不消费）——修复
        #   前弹窗激活期间所有按键被消费（含 Ctrl+C），用户无法中断工具执行。
        if event.kind == "char" and event.char == "\x03":
            return False
        options_now = getattr(us, "options", None) or []
        total_now = len(options_now)
        multi_now = bool(getattr(us, "multi_select", False))
        cur = selected_ref.current
        if not (0 <= cur < total_now):
            # ★ P2-1（selected 越界一致性）：统一钳制并**回写** us.selected——
            #   修复前仅钳制局部 cur（selected_ref.current 保持越界），渲染
            #   高亮钳制到 ``max(0, min(selected, total-1))`` 但 us.selected
            #   保持越界 → Enter 单选 ``0 <= cur < total_now`` 为假走
            #   default_options 分支与高亮不一致。钳制到 total-1（与渲染
            #   钳制语义一致：越界钳制到末项）后同步 selected_ref/
            #   set_selected/us.selected——外部交互期缩窄 options 或预置越界
            #   selected 时导航与 Enter 均基于同一钳制值。
            cur = total_now - 1 if total_now > 0 else 0
            selected_ref.current = cur
            set_selected(cur)
            us.selected = cur
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
            # ★ P1-2（确认 vs 超时竞态，first-write-wins）：写入前先读
            #   us.done——工具超时轮询若已置位（timeout 回退）则不覆盖
            #   （保留 timeout 结果）。handler 开头已检查 us.done，此处为
            #   写终态前的显式防御（双保险，应对 handler 结构变化）。
            if us.done:
                return True
            if multi_now:
                # 多选：返回勾选结果（空勾选返回空列表）——与 Web 前端
                # confirm 行为一致；修复前空勾选时误回退 us.default_options
                # （用户取消所有勾选后回车仍返回默认项，违背交互意图）。
                # ★ 2026-08-06：对 sel 做 0 <= i < total_now 过滤——checked
                #   若被外部预置越界索引（或交互期选项被缩窄），options_now[i]
                #   抛 IndexError 使弹窗交互中断（无超时兜底）。
                sel = [i for i in sorted(checked_ref.current) if 0 <= i < total_now]
                result = [options_now[i] for i in sel]
            else:
                result = [options_now[cur]] if 0 <= cur < total_now else list(us.default_options or [])
            # ★ 发布屏障语义：result/action 先写、done 最后写——工具线程轮询
            #   done=True 时 result/action 必已就绪（修复前 done/action/result
            #   非原子写入，工具线程读到 done=True, action="" 误判超时）。
            us.action = "confirmed"
            us.result = result
            us.done = True
            return True
        if event.kind == "escape":
            # ★ P1-2（同 Enter 分支，first-write-wins）：写入前先读 us.done
            #   ——已置位则放弃覆盖（保留 timeout/confirmed 结果）。
            if us.done:
                return True
            # ★ 发布屏障语义（同 Enter 分支）：action/result 先写、done 最后写。
            us.action = "cancel"
            us.result = list(us.default_options or [])
            us.done = True
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
    # option_lines 非空（/editmsg 多行历史消息）时不走分栏说明模式——
    # 左栏已按 TUI 消息渲染多行，右栏说明与行结构冲突；editmsg 实际不传
    # option_descriptions，该分支仅防御性兜底。
    split = bool(descs) and width and width > 0 and not getattr(us, "option_lines", None)
    title = us.title or "选择"
    rows: list = []

    # 标题行（对齐补全弹窗：▍ + 模式图标 + 标题 + (n/total)）
    # ★ 静态色（2026-08-05 修复）：弹窗标题不再呼吸——弹窗是交互界面，
    #   呼吸色使弹窗行每帧随 time_glow 变化 → 渲染器每帧重写弹窗行（Termux
    #   等终端每帧闪烁/错乱）；静态色弹窗内容不变时 diff 零输出（只在交互
    #   按键时重绘）。
    # ★ BEAUTY-29（2026-08-05 布局美化）：标题前置模式图标——单选 ▶ / 多选
    #   ☑（宽 1 列几何符号，与选中行 ▶/● 前缀语义呼应），一眼识别弹窗模式。
    title_style = _S_TITLE
    mode_icon = "\u2611" if multi else "\u25b6"
    # ★ 窄屏防溢出：标题超宽时截断 title 文本（保留模式图标与位置指示
    #   (cur/total)）——修复前标题行自动换行，(1/3) 位置指示拆到下一行
    #   （视觉错乱）；textWrap="truncate-end" 兜底极端窄屏（截断后仍超宽）。
    title_disp = f" \u258d {mode_icon} {title} ({cur + 1}/{total})"
    if wcswidth_simple(title_disp) > width:
        prefix = f" \u258d {mode_icon} "
        suffix = f" ({cur + 1}/{total})"
        budget = max(1, width - wcswidth_simple(prefix) - wcswidth_simple(suffix))
        title_disp = prefix + _truncate_width(title, budget) + suffix
    rows.append(h(TEXT, {
        "children": title_disp,
        "style": title_style,
        "textWrap": "truncate-end",
    }))
    # ★ 静态高亮背景（修复同标题：弹窗不呼吸，避免每帧重绘）
    sel_bg_style = _S_SEL_BG

    if split:
        # ── 分栏说明模式：左栏选项 + │ + 右栏当前选中项说明 ──
        # /editmsg 多行历史消息（option_lines 非空）不经本分支——即使带了
        # option_descriptions 也走普通多行模式（editmsg 实际不传 descs）。
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
        # ★ 超屏防护：选项 + 说明行数限制（与补全弹窗 _completion_item_rows
        #   同源——超长说明 / 大量选项时弹窗不超终端高度；修复前无上限，
        #   长说明弹窗超高挤压状态栏/输入区）。
        n_rows = min(max(total, len(desc_lines)), _popup_item_rows())
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
            # ★ 静态色（修复同标题：说明列不呼吸，避免每帧重绘）
            rows.append(h(Row, {"height": 1}, [
                left,
                h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
                h(TEXT, {"children": desc_txt, "style": _S_DESC, "height": 1}),
            ]))
    else:
        # ── 普通模式：选项列表（单选高亮 / 多选勾选 + 高亮） ──
        # ★ /editmsg 多行历史消息（option_lines 非空）：每条选项按 TUI 消息
        #   渲染方式显示多行（``> 内容``，user_icon/user_text 色），首行带
        #   选中/勾选前缀、续行对齐；单条超长截断到 _MAX_OPTION_LINES 行并
        #   追加省略提示行；总行数受 _popup_item_rows 超屏防护（交互仍可
        #   导航到隐藏项，与补全弹窗行为一致）。无 option_lines（user_select
        #   工具协议）时行为不变：单行纯文本 + 宽度截断。
        opt_w = max(1, width - 4) if width and width > 0 else 40
        opt_rows = _option_rows_of(us)
        max_lines = _MAX_OPTION_LINES
        # 按总行数预算逐条累计可显示选项（保证至少显示 1 条）
        budget = _popup_item_rows()
        shown: list[int] = []
        used = 0
        for i in range(total):
            n = max(1, min(len(opt_rows[i]), max_lines))
            if shown and used + n > budget:
                break
            shown.append(i)
            used += n
        if not shown:
            shown = [0]
        for i in shown:
            lines_i = opt_rows[i][:max_lines]
            for li, ansi_line in enumerate(lines_i):
                # 单行截断（CJK 安全，防超宽行破坏行级 diff 宽度不变量）
                ansi_line = truncate_line(ansi_line, opt_w)
                if li == 0:
                    if multi:
                        mark = _CHECKED if i in checked else _UNCHECKED
                        prefix = f" {mark} "
                    else:
                        prefix = " \u25b6  " if i == cur else "    "
                else:
                    # 续行对齐首行前缀宽（4 列）
                    prefix = "    "
                runs = _ansi_line_to_styled(ansi_line, prefix, i == cur)
                rows.append(h(TEXT, {"styled": runs, "height": 1}))
            # 截断提示（超过 max_lines 的单条超长消息）
            if len(opt_rows[i]) > max_lines:
                rows.append(h(TEXT, {
                    "children": "    ...",
                    "style": _S_DESC,
                    "height": 1,
                }))

    # 提示行
    # 2026-08-05（增加操作）：提示加入 vim 风格 j/k/g/G 导航（与 ↑↓ 等价）
    # ★ 静态色（修复同标题：提示行不呼吸，避免每帧重绘）
    if multi:
        hint = " \u2423 切换选中 · Enter 确认 · Esc 取消"
    else:
        hint = " \u2191\u2193/jk 选择 · g/G 首末 · Enter 确认 · Esc 取消"
    hint_style = _S_DESC
    # ★ 窄屏防溢出：提示行单行截断（textWrap="truncate-end"，超宽省略号）——
    #   修复前提示行自动换行拆成两行（窄终端 `Esc 取消` 独立一行视觉错乱）。
    rows.append(h(TEXT, {
        "children": hint,
        "style": hint_style,
        "textWrap": "truncate-end",
    }))

    return h(Column, None, rows)
