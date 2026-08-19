"""user_select — React Ink 用户选择弹窗组件（UserSelectPopup）。

React Ink 化（2026-08-05）：user_select 工具的终端交互界面从「命令补全弹窗
（CompletionState + show_completions）+ 手动 raw I/O（select/read_byte）」
迁移为独立的 React Ink 函数组件。

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

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from src.tui._input_layout import _wrap_by_width
from src.tui.app.input_area import _desc_column_width, _truncate_width
from src.tui.app._theme import _S_DIM, _S_SEP
from src.tui.ink import TEXT, h, Column, Row
from src.tui.ink.hooks import use_modal, use_ref, use_state
from src.tui.ink.widgets.interactive import SelectInput, MultiSelect

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
        return max(6, h - 3)
    except Exception:
        return 12


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
        us: UserSelectState。
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
        us: UserSelectState。
        total: 选项总数。

    Returns:
        可见 item 数上限（>= 1）。
    """
    return max(1, min(total, _popup_item_rows()))


def _build_split_row(
    us, options: list, multi: bool, cur: int, checked: list,
    opt_w: int, desc_w: int, width: int, row_i: int, total: int,
) -> object:
    """分栏说明模式单行构建（左栏选项 + │ + 右栏当前选中项说明）。

    与旧 UserSelectPopup 分栏视觉一致：左栏选项（前缀 ▶/勾选 + 文本 +
    补宽对齐），分隔线 ``│`` 紧跟最长选项之后，右栏说明自动变宽；选项超长
    受上限约束（右栏至少保留 ``_desc_column_width`` 基准宽）。

    Args:
        us: UserSelectState。
        options: 选项字符串列表。
        multi: 是否多选。
        cur: 当前高亮索引（已钳制）。
        checked: 多选勾选索引列表。
        opt_w: 左栏宽。
        desc_w: 右栏宽。
        width: 终端宽度。
        row_i: 弹窗行号（0-based；超选项数时左栏留白）。
        total: 选项总数。

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
    desc_lines = _wrap_by_width((descs[desc_sel] if descs else "") or "", desc_w)
    desc_txt = _truncate_width(
        desc_lines[row_i] if row_i < len(desc_lines) else "", desc_w,
    )
    return h(Row, {"height": 1}, [
        left,
        h(TEXT, {"children": "\u2502", "style": _S_SEP, "height": 1}),
        h(TEXT, {"children": desc_txt, "style": _S_DESC, "height": 1}),
    ])


def UserSelectPopup(props) -> object:
    """React Ink 用户选择弹窗组件（SelectInput/MultiSelect 控件化）。

    Props:
        model: AppModel 实例（读 ``model.user_select``）。
        width: 终端宽度（分栏说明模式需要）。

    Returns:
        Column（弹窗行）或空 TEXT（不可见时零高度）。
    """
    model = props["model"]
    width = props.get("width", 80)
    us = getattr(model, "user_select", None)
    # ★ P2-6（review 修复）：options 为空（外部注入/异常状态）时弹窗无可交互
    #   选项——静默不可见（visible=False）会让工具协程（无超时 deadline=0）
    #   永远轮询 ``us.done`` → 交互卡死。修复：自动以 default_options 回退
    #   （置 done=True；first-write-wins——done 已由工具超时置位则跳过）。
    if (
        us is not None and us.visible and not us.done
        and not getattr(us, "options", None)
    ):
        # 原子终态写入（first-write-wins）：done 已由工具超时置位则放弃
        us.try_set_final("confirmed", list(getattr(us, "default_options", None) or []))
    visible = bool(
        us is not None and us.visible and not us.done
        and getattr(us, "options", None)
    )

    # ── hooks（无条件调用，保持 fiber hook 顺序稳定） ──
    # 初始值从 model.user_select 读取（App 以 key=seq 强制重挂载，
    # seq 变化 → fiber 重建 → use_state 重新初始化）。
    selected, set_selected = use_state(us.selected if us is not None else 0)
    # ★ 2026-08-18（连续弹出显示错乱修复 · 组件级双保险）：us 实例变化
    #   （清理后重新打开产生新 UserSelectState 对象）时本帧即以新
    #   us.selected 计算高亮，并排队 set_selected 让下一帧 state 收敛——
    #   即使调和器因 key 复用（seq 修复后理论不会发生）保留旧 fiber，
    #   也不残留旧选中/旧勾选（标题 (n/N) 与高亮行显示正确）。
    us_ref = use_ref(None)
    fresh_us = us_ref.current is not us
    if fresh_us:
        us_ref.current = us
        if us is not None and selected != us.selected:
            set_selected(us.selected)
    # ★ 模态底部视图声明（2026-08-17 通用机制）：visible 时独占键盘输入——
    #   未消费按键被 input router 吞掉（不落入输入缓冲；输入区已不渲染，
    #   字符落入输入缓冲会「看不见地」改变用户输入）。visible=False 时 hook
    #   不参与路由（组件不渲染/已关闭，零影响）。与 use_fullscreen 同节点
    #   类型（模态输入接管语义，见 _hooks_input.use_modal docstring）。
    use_modal(visible)

    if not visible:
        return h(TEXT, {"children": ""})

    options = list(us.options)
    total = len(options)
    multi = bool(us.multi_select)

    # ── 渲染 ──
    # 本帧高亮源：us 实例变化（fresh_us——组件防御，防 fiber 复用残留旧
    # 选中）时用新 us.selected；否则用 use_state 值（控件导航权威）。
    if fresh_us and us is not None:
        try:
            cur = max(0, min(int(us.selected or 0), total - 1))
        except (TypeError, ValueError):
            cur = max(0, min(selected, total - 1))
    else:
        cur = max(0, min(selected, total - 1))
    descs = us.option_descriptions or []
    # 分栏说明模式：option_descriptions 非空时左栏选项 + 右栏说明。
    # （★ 2026-08-18：option_lines 已随 /editmsg 拆分移除——本组件选项
    #   恒为单行纯文本，分栏判断不再需要 option_lines 排除条件。）
    split = bool(descs) and width and width > 0
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
        "key": "us-title",
    }))

    # ── 协议回调（first-write-wins） ──
    def _commit(result, action: str) -> None:
        """提交终态（first-write-wins：done 已置位则放弃覆盖）。

        原子终态写入经 ``us.try_set_final``（锁内检查+写入）——消除与
        工具侧超时分支的竞态覆盖（2026-08-17 修复：修复前此处
        ``if us.done: return`` + 顺序写三字段存在 TOCTOU 窗口，组件确认
        可能被工具超时分支覆盖）。
        """
        us.try_set_final(action, result)

    def _on_select(item) -> None:
        # 单选 Enter：经 try_set_final 原子终态写入（工具超时已置位则
        # 放弃覆盖——first-write-wins）
        result = [item["value"]] if 0 <= cur < total else list(us.default_options or [])
        _commit(result, "confirmed")

    def _on_submit(sel: list) -> None:
        # 多选 Enter：返回勾选结果（空勾选返回空列表）——与单选的
        # confirm 行为一致（修复前空勾选误回退 default_options）。
        _commit(list(sel), "confirmed")

    def _on_cancel(*_args) -> None:
        _commit(list(us.default_options or []), "cancel")

    def _on_highlight(idx: int) -> None:
        # 导航变化：同步 us.selected（组件内部 state 由控件维护）
        us.selected = int(idx)
        set_selected(int(idx))

    # ── 选项控件（SelectInput/MultiSelect 标准控件） ──
    items = [{"label": opt, "value": opt} for opt in options]
    if split:
        # ── 分栏说明模式：左栏选项 + │ + 右栏当前选中项说明 ──
        max_opt_w = max((wcswidth_simple(o) for o in options), default=0)
        base_desc_w = _desc_column_width(width)
        auto_opt_w = max_opt_w + 4
        opt_w = max(1, min(max(1, width - base_desc_w - 1), auto_opt_w))
        desc_w = max(1, width - opt_w - 1)
        # 超屏防护：选项 + 说明行数限制（与补全弹窗 _completion_item_rows
        # 同源——超长说明 / 大量选项时弹窗不超终端高度）。每选项一行，
        # limit 即可见选项数上限（控件窗口滚动交互仍可导航到隐藏项）。
        limit = min(max(total, 1), _popup_item_rows())

        def _split_renderer(item, idx, is_sel, is_checked=None):
            # 分栏说明模式单行：控件对每个 item 调用一次 renderItem——
            # 只构建 item 索引对应的那一行（修复前循环渲染整个选项列表，
            # 每个 item 重复 total 次 → 弹窗选项重复多份）。
            checked_now = list(getattr(us, "checked", []) or [])
            if multi and is_checked is not None:
                # 多选勾选态以控件内部 selected 为权威（is_checked 当前
                # item 勾选与否）；_build_split_row 仅判断本行 row_i==idx，
                # 直接以 is_checked 归一化（修复前只读 us.checked——空格
                # 切换后勾选标记不即时更新）。
                checked_now = [idx] if is_checked else []
            return _build_split_row(
                us, options, multi, cur, checked_now,
                opt_w, desc_w, width, idx, total,
            )
    else:
        opt_w = max(1, width - 4) if width and width > 0 else 40
        limit = _regular_item_limit(us, total)

        def _regular_renderer(item, idx, is_sel, is_checked=None):
            # 普通模式单 item 行：控件对每个 item 调用一次 renderItem——
            # 只构建 item 索引对应的那一项（修复前返回整个选项列表，
            # 每个 item 重复 total 次 → 弹窗选项重复多份）。
            checked_now = list(getattr(us, "checked", []) or [])
            if multi and is_checked is not None:
                # ★ P2（review）：``[i for i, _v in enumerate(options) if
                #   is_checked]`` 的 if 判断的是循环外常量——结果为「全选
                #   索引」或空列表。改为与 _split_renderer 一致的单点写法
                #   （当前 item 勾选与否），防未来消费方读取完整勾选集时
                #   产生「全部勾选」错误。
                checked_now = [idx] if is_checked else []
            return _build_regular_row(
                us, options, multi, idx, cur, checked_now, opt_w,
            )

    # 多选勾选态：控件内部维护；Enter 提交经 onSubmit 返回勾选 values。
    if multi:
        # initialValues：us.checked 中索引 → 选项 value（控件按 value 匹配）
        initial_vals = []
        checked_idx = list(getattr(us, "checked", []) or [])
        for i in checked_idx:
            if 0 <= i < total:
                initial_vals.append(options[i])
        control = h(MultiSelect, {
            "key": f"us-multiselect-{getattr(us, 'seq', 0)}",
            "items": items,
            "initialIndex": cur,
            "initialValues": initial_vals,
            "limit": limit,
            "onSubmit": _on_submit,
            "onCancel": _on_cancel,
            "onHighlight": _on_highlight,
            "renderItem": _regular_renderer if not split else _split_renderer,
            "consumeAll": True,
            "focus": True,
        })
    else:
        control = h(SelectInput, {
            "key": f"us-select-{getattr(us, 'seq', 0)}",
            "items": items,
            "initialIndex": cur,
            "limit": limit,
            "onSelect": _on_select,
            "onCancel": _on_cancel,
            "onHighlight": _on_highlight,
            "renderItem": _regular_renderer if not split else _split_renderer,
            "consumeAll": True,
            "focus": True,
        })
    rows.append(control)

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
        "key": "us-hint",
    }))

    return h(Column, None, rows)


__all__ = ["UserSelectPopup"]
