"""trace_tools_view — 工具列表详情视图（轨迹 Trace 工具列表 Enter 进入）。

用户需求（2026-08-17）：轨迹 Trace 中选中 **#0 工具列表** 记录按 Enter →
进入**新界面**（模态全屏视图 id ``"trace_tools"``）——左右布局：
  - 左栏「工具列表」：全部工具名（原名，注册顺序），↑↓/PgUp/PgDn/Home/End/
    g/G 上下选择（ListView 标准控件——受控光标 + 虚拟滚动 + 选中整行高亮）；
  - 右栏「参数检查器」：选中工具的参数 schema **树控件**展示
    （``build_tools_params_tree``——``参数 (N 项)`` 容器 + 每个参数的
    类型/描述/必需/默认值/枚举/约束叶子），树行渲染复用
    ``trace_view._tree_node_rows``（缩进 + 展开指示符，与轨迹工具调用
    参数树同语义）。

键盘：
  - ↑↓/PgUp/PgDn/Home/End/g/G：上下选择工具（ListView 消费，导航经
    ``onNavigate`` 写回 ``model.trace_tools_selected``——返回主轨迹再进入
    保持上次选择）；
  - Esc/Ctrl+H：返回主轨迹（``model.fullscreen = "trace"``——TraceView
    恢复；主轨迹再次 Esc/Ctrl+H 关闭整个轨迹视图）；
  - 其余按键（Enter/字符等）不消费——经 ``use_fullscreen`` 模态被 input
    router 吞掉（不落入输入缓冲，杜绝看不见的输入）。

数据源：``trace._tools_schema_list``（与 ``_tools_record`` 同源：
ToolRegistry 注册顺序，TTL 缓存；注册表异常/为空 → 空列表——左栏空态 +
右栏「无可用工具」占位，静默降级零成本）。依赖约束：仅依赖 app 同层
（trace/trace_view——均不依赖本模块，无循环依赖）与 ink 框架（Layer 0/1）。
"""

from __future__ import annotations

from src.tui._input_layout import _wrap_by_width
from src.tui.app.trace import _tools_schema_list, build_tools_params_tree
from src.tui.app.trace_view import _tree_node_rows, _viewport_rows
from src.tui.core.style import Style
from src.tui.ink import (
    TEXT, Column, Row, StyledRun, h, use_fullscreen, use_input, use_memo,
)
from src.tui.ink.helpers import truncate_runs
from src.tui.ink.widgets.listview import ListView

# ── 样式（对齐 trace_view 轨迹视图视觉：亮青标题/暗灰提示/浅蓝小节） ──
_S_TITLE = Style(fg=45, bold=True)        # 视图标题/检查器标题（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/占位（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 分隔线（深灰）
_S_TEXT = Style(fg=252)                    # 工具名/树叶子文本（亮白）
_S_DIM = Style(fg=242)                     # 元信息/描述（暗灰）
_S_SEL_BG = Style(bg=237)                  # 选中行背景（静态 237，不呼吸）
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）
_S_SECTION = Style(fg=110, bold=True)      # 参数小节标题（浅蓝加粗）

#: 右栏内容行预算下限（标题 + 元信息 + 省略提示占用后至少保留的行数）
_INSPECTOR_MIN_CONTENT = 4


def _tool_row_runs(name: str, sel: bool, left_w: int) -> list:
    """工具名行 runs（选中行整行背景高亮 + ▶ 标记；宽截断）。

    Args:
        name: 工具名（原名）。
        sel: 是否选中。
        left_w: 左栏宽（>0 时截断；<=0 不截断防御）。
    """
    runs: list = []
    if sel:
        runs.append(StyledRun("\u25b6 ", _S_SEL_MARK))
    else:
        runs.append(StyledRun("  ", None))
    runs.append(StyledRun(name, _S_TEXT))
    if sel:
        runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
    return truncate_runs(runs, left_w) if left_w > 0 else runs


def _tree_rows(nodes: list, right_w: int) -> list:
    """树节点列表 → StyledRun 行列表（前序；缩进 + 展开指示符）。

    复用 ``trace_view._tree_node_rows``（轨迹工具调用参数/返回值树同渲染
    管线：层级缩进 + ▾ 展开指示符 + 超宽截断）——右栏参数树与轨迹检查器
    树视觉一致。
    """
    out: list = []
    _tree_node_rows(nodes, max(1, right_w), out)
    return out


def _inspector_children(
    name: str, props_map: dict, required: list, description: str,
    right_w: int, vh: int,
) -> list:
    """右栏参数检查器子元素（标题 + 元信息 + 描述 + 参数树）。

    内容顺序：
      1. 标题（工具名，亮青加粗）；
      2. 元信息（``N 个参数 · M 个必需``，暗灰）；
      3. 描述（按栏宽换行；空描述跳过）；
      4. 分割线（描述存在时，参数树之前）；
      5. ``▸ 参数`` 小节标题 + 参数树行（``build_tools_params_tree`` 树形
         展开——每个参数的类型/描述/必需/默认值/枚举/约束叶子）。

    行数按 ``vh`` 预算截断（head-first，省略尾部，「… 后 N 行省略」后置）。
    工具名为空（空数据源防御）→ 返回「无可用工具」占位。

    Args:
        name: 工具名（空串 = 空态）。
        props_map: 参数名 → 参数定义 dict（schema parameters.properties）。
        required: 必需参数名列表。
        description: 工具描述（可为空串）。
        right_w: 右栏宽（换行/截断宽度；<=0 外部调用防御）。
        vh: 视口行数预算（内容行数上限）。

    Returns:
        list——TEXT 元素列表（检查器子元素）。
    """
    if not name:
        return [h(TEXT, {
            "children": "无可用工具", "style": _S_HINT, "height": 1,
            "key": "tinsp-empty",
        })]
    right_w = max(1, right_w)
    children: list = []
    children.append(h(TEXT, {
        "children": name, "style": _S_TITLE, "height": 1, "key": "tinsp-title",
    }))
    n_req = len([p for p in (required or []) if p in (props_map or {})])
    children.append(h(TEXT, {
        "children": f"{len(props_map or {})} 个参数 · {n_req} 个必需",
        "style": _S_DIM, "height": 1, "key": "tinsp-meta",
    }))
    # 内容行（描述 + 分割线 + 小节标题 + 树行；按预算截断）
    desc_lines = _wrap_by_width(description, right_w) if description else []
    tree_rows = _tree_rows(build_tools_params_tree(props_map or {}, required or []), right_w)
    total = len(desc_lines) + (1 if desc_lines else 0) + 1 + len(tree_rows)
    budget = max(_INSPECTOR_MIN_CONTENT, vh - 2)
    shown = 0
    truncated = False
    if desc_lines:
        for line in desc_lines:
            if shown >= budget:
                truncated = True
                break
            children.append(h(TEXT, {
                "children": line if line else " ",
                "style": _S_DIM, "height": 1,
                "key": f"tinsp-desc-{len(children)}",
            }))
            shown += 1
        # ★ P3（review 2026-08-19）：分割线/小节标题追加前检查预算——desc 行
        #   恰好填满 budget 时自然结束（truncated=False），无条件追加会使
        #   右栏内容超视口预算 2 行。
        if not truncated and shown < budget:
            children.append(h(TEXT, {
                "children": "\u2500" * max(1, right_w - 1),
                "style": _S_SEP_ROW, "height": 1,
                "key": f"tinsp-sep-{len(children)}",
            }))
            shown += 1
    if not truncated and shown < budget:
        children.append(h(TEXT, {
            "children": "\u25b8 \u53c2\u6570", "style": _S_SECTION, "height": 1,
            "key": f"tinsp-section-{len(children)}",
        }))
        shown += 1
    for runs in tree_rows:
        if shown >= budget:
            truncated = True
            break
        children.append(h(TEXT, {
            "children": "".join(r.text for r in runs) if runs else " ",
            "styled": runs,
            "height": 1,
            "key": f"tinsp-{len(children)}",
        }))
        shown += 1
    if truncated:
        omitted = max(1, total - shown)
        children.append(h(TEXT, {
            "children": f"\u2026 \u540e {omitted} \u884c\u7701\u7565",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted",
        }))
    return children


def TraceToolsView(props) -> object:
    """工具列表详情视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（fullscreen/trace_tools_selected）。
        width: 终端宽度（左右栏宽分配）。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具列表 Enter 进入新界面）：主轨迹
    中选中 #0 工具列表记录按 Enter → ``model.fullscreen = "trace_tools"`` →
    App 整屏渲染本组件（左右布局：左工具名列表 + 右参数树）；Esc/Ctrl+H
    返回主轨迹（``model.fullscreen = "trace"``）。选中工具索引经
    ``model.trace_tools_selected`` 受控（ListView 受控光标 + onNavigate 写回
    ——返回主轨迹再进入保持上次选择）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    active = getattr(model, "fullscreen", "") == "trace_tools"

    # ── 数据（use_memo：schema 列表 TTL 缓存内稳定——工具注册装配期完成） ──
    schemas = use_memo(lambda: _tools_schema_list(), ("tools-schema",))
    sel = max(0, min(
        getattr(model, "trace_tools_selected", 0),
        max(0, len(schemas) - 1),
    )) if schemas else 0

    # ── 视口 / 栏宽（左栏窄于台账——工具名短；右栏参数树占主体） ──
    vh = _viewport_rows()
    if width > 0:
        left_w = max(18, int(width * 0.32))
        if width - left_w - 1 < 20:
            left_w = max(14, width - 21)
        right_w = max(1, width - left_w - 1)
    else:
        left_w, right_w = 28, 52

    # ── 输入（激活期间：Esc/Ctrl+H 返回主轨迹；导航放行 ListView 消费） ──
    def _handle(event) -> bool:
        if not active:
            return False
        # 返回主轨迹（TraceView 恢复；主轨迹再次 Esc/Ctrl+H 关闭整个视图）
        if event.kind == "escape":
            model.fullscreen = "trace"
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            model.fullscreen = "trace"
            return True
        return False

    use_input(_handle, active)
    # ★ 模态全屏视图声明：未消费按键被 input router 吞掉（字符/Enter 不落入
    #   输入缓冲）；关闭后（fullscreen 变化）hook 不激活零影响。
    use_fullscreen(active)

    def _on_navigate(idx: int) -> None:
        """工具列表导航回调（ListView 导航后）：写回选中索引（受控光标）。"""
        model.trace_tools_selected = int(idx)

    # ── 渲染 ──
    header_title = "\u258d\u5de5\u5177\u5217\u8868"
    # ★ BEAUTY-36（美化）：提示精简（80 宽终端下为行尾 ─ 分隔线留出空间）。
    header_hint = ("  \u2191\u2193 \u9009\u62e9 \u00b7 PgUp/PgDn \u00b7 Esc/Ctrl+H \u8fd4\u56de")
    header_runs = [
        StyledRun(header_title, _S_TITLE),
        StyledRun(f" \u00b7 {len(schemas)} \u4e2a\u5de5\u5177", _S_HINT),
        StyledRun(header_hint, _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
    # ★ BEAUTY-36（2026-08-19 美化）：头部行尾 ``─`` 分隔线填充至满宽——
    #   与 TraceView 头部同视觉分层（标题区 / 内容区）。
    if width > 0:
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    # 左栏（工具名列表——ListView 标准控件：受控光标 + 虚拟滚动）
    names = [s[0] for s in schemas]

    def _render_item(item, idx, is_sel):
        return h(TEXT, {
            "key": f"ttools-{idx}",
            "styled": _tool_row_runs(item, bool(is_sel), left_w),
            "height": 1,
        })

    ledger = h(ListView, {
        "items": names,
        "height": vh,
        "width": left_w,
        "cursor": sel,
        "renderItem": _render_item,
        "onNavigate": _on_navigate,
        "focus": active,
    })

    # 右栏（参数检查器——use_memo：选中工具/栏宽/视口变化才重建）
    name, props_map, required, description = (
        schemas[sel] if schemas else ("", {}, [], "")
    )
    # ★ P2（review 2026-08-18）：deps 展平原子值——修复前 ``tuple(required or [])``
    #   每帧新建嵌套元组，``_hooks_core._object_is`` 对 tuple 按 is 引用比较恒
    #   False → 右栏检查器每帧全量重建（与 trace.py 各指纹模块「展平原子值」
    #   契约相悖）。required 为参数名 str 列表 → join 单一 str（值比较稳定）。
    right_children = use_memo(
        lambda: _inspector_children(
            name, props_map, required, description, right_w, vh,
        ),
        (name, len(props_map or {}), ";".join(map(str, required or [])),
         description, right_w, vh),
    )

    return h(Column, None, [
        h(TEXT, {"styled": header_runs, "height": 1}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_children),
        ]),
    ])


__all__ = [
    "TraceToolsView",
    "_inspector_children",
    "_tool_row_runs",
    "_tree_rows",
]
