"""editmsg — 消息选择弹窗组件（EditMsgSelectPopup，独立于 UserSelectPopup）。

★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
/editmsg 消息选择从 user_select 协议（``model.user_select`` +
``UserSelectPopup`` + ``bottom_view="user_select"``）拆分为**独立实现**：

  - 独立状态：``model.editmsg_select``（``EditMsgSelectState``，
    src/tui/app/_state_types.py）；
  - 独立底部视图：``bottom_view="editmsg"``（``app.BOTTOM_VIEWS`` 注册）；
  - 独立组件：``EditMsgSelectPopup``（本文件）；
  - **每条消息只显示一行**：``options`` 为单行摘要（message_editor
    ``_user_msg_summary`` 生成），组件按单行渲染并截断防超宽。

组件与编辑器轮询线程通信协议（跨线程安全，GIL 原子字段；与 user_select
工具协议同构但独立实现，不共用代码）：
  - 编辑器：设置 ``model.editmsg_select``（visible=True, seq+1）→
    request_bottom_redraw；App 组件以 ``key=seq`` 渲染本组件（seq 变化
    强制重挂载，重置内部 use_state）。
  - 组件：控件回调更新内部 state + 写 ``es.selected``；提交/取消经
    ``es.try_set_final`` 原子终态写入（first-write-wins，锁内检查 done）。
  - 编辑器：轮询 ``es.done``（带 deadline 超时），读 selected 后清理
    ``model.editmsg_select = EditMsgSelectState()`` 并 request_bottom_redraw。

依赖约束：仅依赖 app 同层（model/input_area）与 ink 框架（Layer 0/1），
无 tools 层反向依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from src.tui.app.input_area import _truncate_width
from src.tui.ink import TEXT, h, Column
from src.tui.ink.hooks import use_modal, use_ref, use_state
from src.tui.ink.widgets.interactive import SelectInput

__all__ = ["EditMsgSelectPopup"]

#: 弹窗标题色（亮青加粗，与 UserSelectPopup 标题视觉同源但独立定义）
_S_TITLE = Style(fg=45, bold=True)
#: 高亮行背景色（静态 237——弹窗不呼吸）
_S_SEL_BG = Style(bg=237)
#: 提示行静态色（浅蓝 110——弹窗不呼吸）
_S_DESC = Style(fg=110)


def _editmsg_item_rows() -> int:
    """消息选择弹窗行数上限（超屏防护）。

    ★ 模态底部视图：EditMsgSelectPopup 独立为底部视图——弹窗打开时状态栏/
    输入区**不渲染**，可用高度 = 终端高 - 顶部标题栏 1 - 弹窗标题 1 - 弹窗
    提示行 1 ≈ ``h - 3``（与 UserSelectPopup 的高度预算语义相同，独立实现
    ——editmsg 与 user_select 不共用代码）。

    Returns:
        选项最大渲染行数。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, h - 3)
    except Exception:
        return 12


def EditMsgSelectPopup(props) -> object:
    """React Ink 消息选择弹窗组件（/editmsg 专用，独立于 UserSelectPopup）。

    Props:
        model: AppModel 实例（读 ``model.editmsg_select``）。
        width: 终端宽度。

    Returns:
        Column（弹窗行）或空 TEXT（不可见时零高度）。
    """
    model = props["model"]
    width = props.get("width", 80)
    es = getattr(model, "editmsg_select", None)
    # options 为空（异常状态）时弹窗无可交互选项——静默不可见
    # （编辑器轮询 deadline 超时兜底，不会永久卡死）。
    visible = bool(
        es is not None and es.visible and not es.done
        and getattr(es, "options", None)
    )

    # ── hooks（无条件调用，保持 fiber hook 顺序稳定） ──
    # 初始值从 model.editmsg_select 读取（App 以 key=seq 强制重挂载，
    # seq 变化 → fiber 重建 → use_state 重新初始化）。
    selected, set_selected = use_state(es.selected if es is not None else 0)
    # ★ 2026-08-18（连续弹出显示错乱修复 · 组件级双保险，与 UserSelectPopup
    #   同机制）：es 实例变化（清理后重新打开产生新 EditMsgSelectState 对象）
    #   时本帧即以新 es.selected 计算高亮，并排队 set_selected 让下一帧 state
    #   收敛——即使调和器因 key 复用（seq 修复后理论不会发生）保留旧 fiber，
    #   也不残留旧选中（标题 (n/N) 与高亮行显示正确）。
    es_ref = use_ref(None)
    fresh_es = es_ref.current is not es
    if fresh_es:
        es_ref.current = es
        if es is not None and selected != es.selected:
            set_selected(es.selected)
    # ★ 模态底部视图声明（与 UserSelectPopup 同机制）：visible 时独占键盘
    #   输入——未消费按键被 input router 吞掉（不落入输入缓冲；输入区已
    #   不渲染）。visible=False 时 hook 不参与路由（零影响）。
    use_modal(visible)

    if not visible:
        return h(TEXT, {"children": ""})

    options = list(es.options)
    total = len(options)
    # ── 渲染 ──
    # 本帧高亮源：es 实例变化（fresh_es——组件防御，防 fiber 复用残留旧
    # 选中）时用新 es.selected；否则用 use_state 值（控件导航权威）。
    if fresh_es and es is not None:
        try:
            cur = max(0, min(int(es.selected or 0), total - 1))
        except (TypeError, ValueError):
            cur = max(0, min(selected, total - 1))
    else:
        cur = max(0, min(selected, total - 1))
    title = es.title or "选择要编辑的消息"
    rows: list = []

    # 标题行（对齐补全弹窗：▍ + 模式图标 + 标题 + (n/total)；静态色不呼吸）
    title_style = _S_TITLE
    title_disp = f" \u258d \u25b6 {title} ({cur + 1}/{total})"
    if wcswidth_simple(title_disp) > width:
        prefix = " \u258d \u25b6 "
        suffix = f" ({cur + 1}/{total})"
        budget = max(1, width - wcswidth_simple(prefix) - wcswidth_simple(suffix))
        title_disp = prefix + _truncate_width(title, budget) + suffix
    rows.append(h(TEXT, {
        "children": title_disp,
        "style": title_style,
        "textWrap": "truncate-end",
        "key": "em-title",
    }))

    # ── 协议回调（first-write-wins，独立实现不共用 UserSelectState） ──
    def _commit(result, action: str) -> None:
        """提交终态（first-write-wins：done 已置位则放弃覆盖）。"""
        es.try_set_final(action, result)

    def _on_select(item) -> None:
        # 单选 Enter：经 try_set_final 原子终态写入（编辑器超时已置位则
        # 放弃覆盖——first-write-wins）
        result = [item["value"]] if 0 <= cur < total else []
        _commit(result, "confirmed")

    def _on_cancel(*_args) -> None:
        _commit([], "cancel")

    def _on_highlight(idx: int) -> None:
        # 导航变化：同步 es.selected（组件内部 state 由控件维护）
        es.selected = int(idx)
        set_selected(int(idx))

    # ── 选项控件（SelectInput 标准控件，单选） ──
    items = [{"label": opt, "value": opt} for opt in options]
    # 每条消息只显示一行：单行选项 → 行数预算即可见项数（交互仍可导航到
    # 隐藏项）。
    limit = max(1, min(total, _editmsg_item_rows()))

    def _render_item(item, idx, is_sel):
        """单行渲染：▶ 高亮前缀 + 消息单行摘要（超宽截断不拆 CJK）。"""
        prefix = " \u25b6  " if is_sel else "    "
        label = _truncate_width(
            str(item["label"]), max(1, (width - 4) if width and width > 0 else 40),
        )
        return h(TEXT, {
            "children": f"{prefix}{label}",
            "style": _S_SEL_BG if is_sel else None,
            "height": 1,
            "key": f"em-item-{idx}",
        })

    rows.append(h(SelectInput, {
        "key": f"em-select-{getattr(es, 'seq', 0)}",
        "items": items,
        "initialIndex": cur,
        "limit": limit,
        "onSelect": _on_select,
        "onCancel": _on_cancel,
        "onHighlight": _on_highlight,
        "renderItem": _render_item,
        "consumeAll": True,
        "focus": True,
    }))

    # 提示行（静态色不呼吸；窄屏单行截断）
    hint = " \u2191\u2193/jk 选择 · g/G 首末 · Enter 编辑 · Esc 取消"
    rows.append(h(TEXT, {
        "children": hint,
        "style": _S_DESC,
        "textWrap": "truncate-end",
        "key": "em-hint",
    }))

    return h(Column, None, rows)
