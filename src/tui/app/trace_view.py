"""trace_view — TraceView 轨迹视图组件（DSH 风格左台账 + 右检查器，2026-08-19）。

Ctrl+H（0x08）打开/关闭：App 在 ``model.trace_open`` 时**整屏只渲染本组件**
（消息区/顶部标题栏/状态栏/输入区全部不显示——「打开时其他 TUI 不显示，
只显示这个界面」），台账/检查器占满整个终端高度；Esc/Ctrl+H 关闭后恢复
完整聊天界面。

布局（React Ink 左右布局）：
  - 左栏「台账」：轮次分隔行 + 记录行（#N · 种类图标 · 摘要 · 右对齐耗时），
    选中行整行高亮（▶ 标记 + 背景色）；仅渲染可见窗口（虚拟窗口，行数 =
    终端高度自适应）；
  - 右栏「检查器」：选中记录详情（#N 种类 · 状态图标/耗时/token · 内容行，
    按栏宽换行 + 视口行数截断；**思考/回答经流式 markdown 渲染**——标题/
    粗体/行内码/代码高亮/表格等格式化，与聊天区内容渲染同管线，内容增长
    自动重渲染）。

记录数据：``build_trace_records``（agent 消息列表为主数据源；use_memo 指纹
缓存——消息/块内容变化才重建；详情行仅对选中记录惰性提取）。

键盘（use_input 路由，trace_open 期间激活）：
  - ↑↓ 选择 · PgUp/PgDn 翻页 · Home/End、g/G 首末 · Esc/Ctrl+H 关闭；
  - Enter 选中 subagent 记录 → **进入 subagent 轨迹**（嵌套 TraceView——
    显示内容与 mainagent 同构：system/user/思考/回答/工具，Esc/Ctrl+H 返回
    主轨迹）；其余记录 Enter/其余按键**放行**（无输入区显示；Enter 仍可
    提交消息——轨迹界面持续显示会话最新记录）。
"""

from __future__ import annotations

import json

from src.tui._format import format_duration, format_tokens
from src.tui._input_layout import _wrap_by_width
from src.tui.app.trace import (
    block_detail_lines,
    build_subagent_trace_records,
    build_trace_records,
)
from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input, use_memo
from src.tui.ink.helpers import truncate_runs
from src.tui.ink.widgets.listview import ListView

# ── 样式 ─────────────────────────────────────────────
_S_TITLE = Style(fg=45, bold=True)        # 视图标题前缀（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/分隔弱化（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 轮次分隔行（深灰）
_S_INDEX = Style(fg=242)                   # #N 记录号（暗灰）
_S_TIME = Style(fg=110)                    # 耗时（浅蓝）
_S_TEXT = Style(fg=252)                    # 摘要/内容文本（亮白）
_S_DIM = Style(fg=242)                     # 推理摘要/元信息（暗灰）
_S_SEL_BG = Style(bg=237)                  # 选中行背景（静态 237，不呼吸）
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）
_S_SECTION = Style(fg=110, bold=True)      # 检查器小节标题（参数/返回值，浅蓝加粗）
_S_TREE_KEY = Style(fg=75)                 # 树节点键（浅紫蓝）
_S_TREE_VAL = Style(fg=252)                # 树节点标量值（亮白）

#: 树节点指示符/缩进（对齐 ink Tree 控件渲染语义——检查器参数/返回值
#:   以树形结构展示：层级缩进 + 展开指示符）
_TREE_OPEN = "\u25be "    # ▾ 展开
_TREE_LEAF = "  "         # 叶子占位（对齐 Tree._TREE_LEAF）
_TREE_INDENT = 2          # 每层缩进空格数（对齐 Tree._TREE_INDENT）
#: 参数/返回值小节标题前缀
_SECTION_PREFIX = "\u25b8 "  # ▸

#: 种类图标（台账行）与名称（检查器标题）——对齐既有角色头 emoji 语义
_KIND_ICON = {
    "tools": "\U0001F9F0", "system": "\u2699", "user": "\U0001F464",
    "reasoning": "\U0001F4AD", "content": "\U0001F4AC", "tool": "\u26A1",
    "subagent": "\U0001F916", "context": "\U0001F4C4",
}
_KIND_NAME = {
    "tools": "工具列表", "system": "系统", "user": "用户", "reasoning": "思考",
    "content": "回答", "tool": "工具", "subagent": "子代理", "context": "上下文",
}
#: 种类图标色（摘要文本 reasoning 用暗灰，其余亮白）
_KIND_FG = {
    "tools": 214, "system": 110, "user": 39, "reasoning": 242, "content": 45,
    "tool": 214, "subagent": 75, "context": 110,
}
#: 状态图标与色（tool/subagent：● 运行中 / ✔ 完成 / ✖ 失败）
_STATUS_ICON = {"running": "\u25cf", "done": "\u2714", "fail": "\u2716", "error": "\u2716"}
_STATUS_FG = {"running": 208, "done": 41, "fail": 196, "error": 196}

#: 台账可见行数 = 终端高度 - 保留行（**全屏模式**：仅轨迹头 1 行——trace_open
#:   时消息区/顶部标题栏/状态栏/输入区全部不渲染，「打开时其他 TUI 不显示，
#:   只显示轨迹界面」，台账/检查器占满整个终端）
_VIEWPORT_RESERVED = 1
#: 检查器内容行预算下限（标题 + 元信息 + 省略提示占用后至少保留的行数）
_INSPECTOR_MIN_CONTENT = 4


def _viewport_rows() -> int:
    """台账可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, int(h) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _kind_fg(kind: str) -> int:
    return _KIND_FG.get(kind, 242)


def _status_fg(status: str) -> int:
    return _STATUS_FG.get(status, 242)


def _ledger_row_runs(rec, sel: bool, left_w: int) -> list:
    """台账行 runs（选中行整行背景高亮 + ▶ 标记；耗时右对齐；宽截断）。

    Args:
        rec: TraceRecord。
        sel: 是否选中。
        left_w: 左栏宽（>0 时截断；<=0 不截断防御）。
    """
    runs: list = []
    # 选择标记（2 列）
    if sel:
        runs.append(StyledRun("\u25b6 ", _S_SEL_MARK))
    else:
        runs.append(StyledRun("  ", None))
    runs.append(StyledRun(f"#{rec.index:>2} ", _S_INDEX))
    kind = getattr(rec, "kind", "context")
    icon = _KIND_ICON.get(kind, "\u00b7")
    runs.append(StyledRun(f"{icon} ", Style(fg=_kind_fg(kind))))
    # 状态图标（tool/subagent 才携带；running 呼吸色由 time_glow 负担过重，
    # 静态色——台账行不每帧重建）
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _STATUS_ICON.get(status, "\u00b7")
        runs.append(StyledRun(f"{sicon} ", Style(fg=_status_fg(status))))
    summary = getattr(rec, "summary", "") or "(空)"
    runs.append(StyledRun(summary, _S_DIM if kind == "reasoning" else _S_TEXT))
    # ★ 2026-08-19（工具调用+返回合并一条）：tool 记录在台账行追加返回首行
    #   预览（``· 返回…``，暗灰）——调用与返回同一条记录可见
    result = getattr(rec, "result", "") or ""
    if result and left_w > 0:
        budget = max(8, left_w // 3)
        prev_runs = truncate_runs([StyledRun(result, _S_DIM)], budget)
        if prev_runs:
            runs.append(StyledRun(" \u00b7 ", _S_HINT))
            runs.extend(prev_runs)
    # 耗时右对齐（尾列）
    t = ""
    ts = getattr(rec, "time_seconds", None)
    if ts is not None:
        t = format_duration(ts)
    if t and left_w > 0:
        used = sum(getattr(r, "width", 1) for r in runs)
        pad = left_w - used - len(t) - 1
        if pad > 0:
            runs.append(StyledRun(" " * pad, None))
        runs.append(StyledRun(t, _S_TIME))
    if sel:
        runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
    return truncate_runs(runs, left_w) if left_w > 0 else runs


def _sep_row_runs(n: int, left_w: int) -> list:
    """轮次分隔行 runs（``── 轮次 N ──``，深灰）。"""
    runs = [StyledRun(f"\u2500\u2500 轮次 {n} \u2500\u2500", _S_SEP_ROW)]
    return truncate_runs(runs, left_w) if left_w > 0 else runs


def _detail_lines_of(rec) -> list:
    """选中记录详情行（惰性提取：subagent 记录自带 lines；块记录经
    ``block_detail_lines`` 按需提取）。

    ★ review 修复（P3-1）：**块记录（source_block 非空）优先块路径**——
    与 ``_detail_deps`` 的块优先键一致（live 记录同时携带 lines 快照与
    source_block：修复前 lines 快照优先，快照随 records 重建漂移而
    ``_detail_deps`` 用块引用 → 同长原地替换场景 deps 不变但快照陈旧）。
    块路径实时反映 block.lines 内容（流式增长触发 deps 行数变化重建）。
    """
    if rec is None:
        return []
    block = getattr(rec, "source_block", None)
    if block is not None:
        return block_detail_lines(block)
    lines = getattr(rec, "lines", None) or []
    if lines:
        return lines
    return []


def _detail_deps(rec) -> tuple:
    """详情行 use_memo 依赖（块记录：行列表身份 + 行数；subagent：lines 身份）。

    ★ 2026-08-17（review 修复）：**块记录（source_block 非空）优先块路径**
    ——live 记录同时携带 lines 快照（随 records 重建漂移）与 source_block
    （实时引用），修复前 ``_detail_deps`` 优先 lines 快照分支：流式期间
    records 每帧重建 → lines 新 id → use_memo 恒 miss → 每帧重新提取快照
    （无用功）。块路径用 block.lines 稳定引用 + 行数（流式增长触发重建），
    与 ``_md_detail_rows`` 的块缓存键一致（单一数据源语义）。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具调用参数/返回值用树控件显示）：
    tool 记录检查器树显示数据源 = ``tool_args``/``tool_result``——运行中
    工具输出流式增长（``tool_result`` 变长）须触发重建；时间基元素
    （``time_seconds``）不入指纹（台账静态色，不随动画重建）。
    """
    if rec is None:
        return (None,)
    if getattr(rec, "kind", "") == "tool":
        args = getattr(rec, "tool_args", None)
        result = str(getattr(rec, "tool_result", "") or "")
        return ("tool-tree", repr(args)[:200], result[:200], len(result))
    block = getattr(rec, "source_block", None)
    if block is not None:
        return (id(getattr(block, "lines", None)), len(getattr(block, "lines", None) or []))
    lines = getattr(rec, "lines", None) or []
    if lines:
        return (id(lines), getattr(rec, "index", 0))
    return (None,)


def _clamp_color(v):
    """renderer 色号钳制（review 修复：bool / 越界 int / 非 int 非 tuple
    色号会让 tui Style 构造抛 ValueError 或生成非法 ANSI——一律回退 None，
    丢色不崩溃）。

    注意 bool 是 int 子类且 True∈[0,255]——但 ``Style.__post_init__`` 对
    bool 显式抛 ValueError（bool 色号语义错误），故 bool 必须回退 None
    （修复前原样放行 → Style 构造异常传播中断检查器渲染）。
    """
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if 0 <= v <= 255 else None


def _to_tui_style(rs, kind: str):
    """renderer.ansi.Style → tui.core.Style（检查器 markdown 行用）。

    RGB 三元组 fg/bg → ``TrueColor``（tui 样式类型）；reasoning 叠加暗灰
    弱化样式（对齐 chat_view 推理块弱化语义：基础样式 fg 覆盖渲染色、
    布尔属性 OR 保留——与 ``_block_styled_lines`` 的
    ``(r.style or Style()).merge(_S_REASONING)`` 同语义，_S_REASONING 与
    _S_DIM 同为 fg=242 暗灰）。非法色号（越界 int / 越界 RGB）钳制回退
    None（防御：异常数据不中断检查器渲染）。
    """
    if rs is None:
        st = None
    else:
        fg = rs.fg
        if isinstance(fg, tuple):
            from src.tui.core.color import TrueColor
            try:
                fg = TrueColor(*fg)
            except Exception:
                fg = None
        else:
            fg = _clamp_color(fg)
        bg = rs.bg
        if isinstance(bg, tuple):
            from src.tui.core.color import TrueColor
            try:
                bg = TrueColor(*bg)
            except Exception:
                bg = None
        else:
            bg = _clamp_color(bg)
        st = Style(fg=fg, bg=bg, bold=rs.bold, italic=rs.italic,
                   dim=rs.dim, underline=rs.underline)
    if kind == "reasoning":
        st = _S_DIM if st is None else st.merge(_S_DIM)
    return st


def _convert_ansi_row(aline, right_w: int, kind: str) -> list:
    """单条 AnsiLine → StyledRun 行列表（超宽按 right_w 样式安全换行）。

    空行（无 runs / 纯文本为空——段落/结构分隔）→ 单个空格占位行
    ``[StyledRun(" ", 样式)]``（review 修复：TEXT styled=[] 渲染 0 行 h=0
    不绘制——空行必须有占位才能保留段落/表格结构；占位行带 kind 基础
    样式——reasoning 空行与其他行同暗灰，视觉一致）。

    防御（review 修复 P3）：单 run 样式转换异常（非法色号等）→ 该 run
    回退默认样式（不中断整行/检查器渲染）。
    """
    if not getattr(aline, "runs", None) or not getattr(aline, "plain", ""):
        return [[StyledRun(" ", _to_tui_style(None, kind))]]
    from src.renderer.ansi.helpers import wrap_line
    out: list = []
    for wl in wrap_line(aline, right_w):
        runs: list = []
        for r in wl.runs:
            if not r.text:
                continue
            try:
                st = _to_tui_style(r.style, kind)
            except Exception:
                st = None
            runs.append(StyledRun(r.text, st))
        out.append(runs if runs else [StyledRun(" ", _to_tui_style(None, kind))])
    return out


def _block_styled_rows(block, right_w: int, kind: str) -> list:
    """块渲染输出行 → StyledRun 行列表（缓存；块/live 记录检查器内容）。

    ★ 2026-08-17（用户需求：回答/思考/system 用 markdown 显示在右边）：
    块（model.blocks）内 AnsiLine 是**流式 markdown 渲染管线的输出**（标题
    青色粗体/代码 pygments 高亮/列表符号等已带样式）——直接复用（AnsiLine
    runs → StyledRun），**不二次 markdown 解析**（二次解析会把渲染后的代码
    块标题行 `` ```python [python]`` 误判为「语言 + 首行内容」）。

    **流式（review 修复 P2：增量缓存）**：block.lines 为 append-only（渲染
    管线只追加不修改）——缓存记录已转换行数，流式增长仅转换**新增行**
    （既有行引用复用），避免每帧全量重建 O(n²)。行数倒退 / right_w / kind
    变化（非 append-only 异常）→ 全量重建。缓存挂载到 block
    （``_insp_md_cache = (key, rows, converted_count)``）；key = (lines
    引用 id, right_w, kind)。**内容不可变契约**：block.lines 内 AnsiLine
    只追加不原地修改（与 ``_wrap_cache`` BUG-71 强制约定同语义）。
    """
    blines = getattr(block, "lines", None) or []
    cache = getattr(block, "_insp_md_cache", None)
    if cache is not None:
        ckey, crows, cconv = cache
        if (ckey == (id(blines), right_w, kind) and len(blines) >= cconv):
            # 增量路径：仅转换新增行（流式 append-only）
            for aline in blines[cconv:]:
                crows.extend(_convert_ansi_row(aline, right_w, kind))
            block._insp_md_cache = (ckey, crows, len(blines))
            return crows
    # 全量重建（缓存 miss / 参数变化 / 行数倒退）
    rows: list = []
    for aline in blines:
        rows.extend(_convert_ansi_row(aline, right_w, kind))
    block._insp_md_cache = ((id(blines), right_w, kind), rows, len(blines))
    return rows


def _lines_fp(lines) -> int:
    """lines 内容指纹（缓存键：records 流式重建时静态记录内容未变 → 命中
    → 零重渲染；内容变化 → 指纹变化 → 重建）。

    ``hash(tuple(lines))`` 每次 O(内容)（远低于全量 markdown 渲染成本）；
    hash 碰撞仅导致缓存陈旧/重建（不崩溃，可接受）。lines 元素不可 hash
    （异常数据）回退引用 id（保底）。
    """
    try:
        return hash(tuple(lines))
    except Exception:
        return id(lines)


#: 工具调用树显示模块级缓存（对齐 ``_MD_RENDER_CACHE`` 语义）：键 =
#: (args 前 200 字符, result 前 200 字符, result 长度, right_w) → 行列表。
#: 流式期间 records 每帧重建（新 TraceRecord）——树内容不变时跨 rec 命中
#: 零重建；运行中工具输出增长（result 变化）键变 → miss 重建（流式动态
#: 更新）。渲染结果纯函数（同输入同输出），跨 rec 共享安全。有界防无限
#: 增长（超限清空重建——miss 仅多一次渲染，无正确性影响）。
_TOOL_TREE_CACHE: dict = {}
_TOOL_TREE_CACHE_MAX = 64


#: 树递归深度上限（防御：超过则停止展开 children——与 ink Tree 控件
#: ``_TREE_MAX_DEPTH`` 同语义，避免异常深层 JSON 触发 RecursionError）。
_TREE_MAX_DEPTH = 200


def _value_to_tree(value, key: str = "", depth: int = 0) -> list:
    """任意值 → 树控件 data 格式节点列表（{label, children}）。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具调用参数/返回值用树控件显示）：
    JSON 值 → 树形节点（对齐 ink Tree 控件 data 形态——label/children）：
      - dict → 键值对子节点；key 非空时包装为 ``key (N 项)`` 节点（根 key
        为空直接列出子节点——省略无名根，紧凑展示）；
      - list → 下标子节点（``[i]``）；key 非空时包装为 ``key (N 项)``；
      - 标量 → 叶子 ``key: value``（key 为空则纯 value）；
      - 空 dict/list → 叶子 ``key: {}``/``key: []``。
    """
    if depth > _TREE_MAX_DEPTH:
        return []
    if isinstance(value, dict):
        if not value:
            return [{"label": (f"{key}: {{}}" if key else "{}"), "children": []}]
        children: list = []
        for k, v in value.items():
            children.extend(_value_to_tree(v, str(k), depth + 1))
        if key:
            return [{"label": f"{key} ({len(value)} 项)", "children": children}]
        return children
    if isinstance(value, (list, tuple)):
        if not value:
            return [{"label": (f"{key}: []" if key else "[]"), "children": []}]
        children = []
        for i, v in enumerate(value):
            children.extend(_value_to_tree(v, f"[{i}]", depth + 1))
        if key:
            return [{"label": f"{key} ({len(value)} 项)", "children": children}]
        return children
    # 标量：JSON 字面量语义（null/true/false——对齐 JSON 原文，而非 Python
    # 的 None/True/False 字符串化）
    if value is None:
        display = "null"
    elif isinstance(value, bool):
        display = "true" if value else "false"
    else:
        display = str(value)
    return [{"label": (f"{key}: {display}" if key else display), "children": []}]


def _args_to_tree(args) -> list:
    """工具调用参数 → 树节点列表（str JSON / dict；None/空 → []）。

    str 形态尝试 JSON 解析（消息模型 arguments 原始 JSON 串）；解析失败
    （块路径 tool_detail 关键参数摘要等非 JSON 文本）→ 单叶子节点。
    """
    if args is None:
        return []
    if isinstance(args, dict):
        return _value_to_tree(args)
    text = str(args).strip()
    if not text:
        return []
    try:
        return _value_to_tree(json.loads(text))
    except (ValueError, TypeError):
        return [{"label": text, "children": []}]


def _parse_tree_text(text) -> list:
    """工具返回文本 → 树节点列表（JSON 解析成功 → 树；失败 → 每行一个叶子）。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具调用返回值用树控件显示）：
    bash/read_file 等工具返回值通常为**非 JSON 纯文本**（命令回显/文件
    内容）——以文本行叶子树形展示（对齐 Tree 控件叶子行语义）。
    """
    if text is None:
        return []
    s = str(text).strip()
    if not s:
        return []
    try:
        return _value_to_tree(json.loads(s))
    except (ValueError, TypeError):
        lines = s.splitlines() or [s]
        return [{"label": ln, "children": []} for ln in lines]


def _tree_node_rows(nodes: list, right_w: int, out: list, depth: int = 0) -> None:
    """树节点列表 → 可见行（前序；缩进 + 展开指示符；对齐 Tree 控件渲染）。

    只读展示（检查器不参与树交互——台账 ListView 独占导航焦点），默认展开
    全部层级；label 含 ``\\n`` 归一化单行（防行级 diff 宽度不变量破坏）；
    行超宽截断到 right_w（行级 diff 宽度不变量）。
    """
    if depth > _TREE_MAX_DEPTH:
        return
    for node in nodes:
        children = node.get("children") or []
        indicator = _TREE_OPEN if children else _TREE_LEAF
        prefix = " " * (depth * _TREE_INDENT)
        label = node.get("label", "")
        if "\n" in label:
            label = label.replace("\n", " ")
        text = f"{prefix}{indicator}{label}"
        out.append(truncate_runs([StyledRun(text, _S_TEXT)], max(1, right_w)))
        if children:
            _tree_node_rows(children, right_w, out, depth + 1)


def _tool_tree_rows(rec, right_w: int) -> list:
    """tool 记录检查器树内容行：**参数树 → 分割线 → 返回值树**。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具调用修改——参数用树控件显示，
    然后分割线，返回值用树控件显示）：选中 tool 记录时检查器内容 =
      1. ``▸ 参数`` 小节标题 + 参数树（``tool_args`` JSON 树形展开）；
      2. 分割线（``──`` 深灰满宽——分隔参数与返回值）；
      3. ``▸ 返回值`` 小节标题 + 返回值树（``tool_result`` JSON 树形展开，
         非 JSON 文本每行一个叶子）。
    参数/返回值缺失（None/空）→ 对应小节占位提示（(无参数)/(无返回)）。

    Args:
        rec: tool TraceRecord（读 ``tool_args``/``tool_result``）。
        right_w: 右栏宽（行超宽截断；<=0 外部调用防御）。

    Returns:
        list[list[StyledRun]]——树内容行（head-first 顺序；预算/截断/省略
        提示由 ``_inspector_children`` 统一处理）。
    """
    right_w = max(1, right_w)
    args = getattr(rec, "tool_args", None)
    result = str(getattr(rec, "tool_result", "") or "")
    key = (repr(args)[:200], result[:200], len(result), right_w)
    cached = _TOOL_TREE_CACHE.get(key)
    if cached is not None:
        return cached
    rows: list = []
    # ── 1. 参数小节（树控件显示参数） ──
    rows.append([StyledRun(f"{_SECTION_PREFIX}参数", _S_SECTION)])
    arg_nodes = _args_to_tree(args)
    if arg_nodes:
        _tree_node_rows(arg_nodes, right_w, rows)
    else:
        rows.append([StyledRun("(无参数)", _S_HINT)])
    # ── 2. 分割线（参数 / 返回值 之间的分隔） ──
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP_ROW)])
    # ── 3. 返回值小节（树控件显示返回值） ──
    rows.append([StyledRun(f"{_SECTION_PREFIX}返回值", _S_SECTION)])
    result_nodes = _parse_tree_text(result)
    if result_nodes:
        _tree_node_rows(result_nodes, right_w, rows)
    else:
        rows.append([StyledRun("(无返回)", _S_HINT)])
    _TOOL_TREE_CACHE[key] = rows
    if len(_TOOL_TREE_CACHE) > _TOOL_TREE_CACHE_MAX:
        _TOOL_TREE_CACHE.clear()
    return rows


#: 内联 markdown 渲染模块级缓存（review 修复 P1-3）：键 = (内容指纹, 行数,
#: right_w, kind) → rows。流式期间 records 每帧重建（新 TraceRecord）——
#: rec 级缓存恒冷（新 rec 无 _md_detail_cache），模块级缓存**跨 rec 命中**
#: （静态内联记录内容未变 → 指纹相同 → 零重渲染）；内容变化（live 记录
#: 增长）指纹变化 → miss → 重建。渲染结果纯函数（同输入同输出），跨 rec
#: 共享安全。有界防无限增长（超限清空重建——miss 仅多一次渲染，无正确性
#: 影响）。
_MD_RENDER_CACHE: dict = {}
_MD_RENDER_CACHE_MAX = 256


def _md_detail_rows(rec, right_w: int, kind: str) -> list:
    """reasoning/content/system 记录详情 → markdown 渲染 StyledRun 行（缓存）。

    ★ 2026-08-17（用户需求：回答/思考/system 用流式 markdown 显示在右边）：
    **数据源分支**：
      - ``rec.source_block`` 非空（块/live 记录——块内 AnsiLine 已是流式
        markdown 渲染管线的输出，带标题/代码高亮等样式）→ 直接复用
        ``_block_styled_rows``（不二次解析，渲染输出二次解析会把代码块
        标题行 `` ```python [python]`` 误判为「语言 + 首行内容」）；
      - 内联 lines（消息源模式 = reasoning_content/content/system 提示词
        的**原始 markdown 文本**）→ 经 ``apply._render_markdown_lines``
        （与聊天区流式内容同一渲染管线）重新渲染。

    渲染行按 right_w 样式安全换行（``wrap_line``）→ StyledRun 行。**流式
    （review 修复 P1-3）**：缓存用**模块级内容指纹**（``_MD_RENDER_CACHE``
    + ``_lines_fp``）——流式期间 ``_live_fingerprint`` 变化驱动 records
    整体重建，静态内联记录（system 提示词/历史回答）每次重建产生新
    TraceRecord + 新 lines 引用，但内容未变 → 指纹命中 → 零重渲染（修复前
    每帧全量 markdown 渲染 + TOC；rec 级缓存因新 rec 恒冷无效）。live 记录
    （内容增长）指纹变化 → miss → 重建（流式动态更新）。无内联源码且无块
    → 空列表（纯文本回退）。渲染异常（``_render_markdown_lines`` 抛错）→
    回退空列表（不中断检查器）。

    key = (内容指纹, 行数, right_w, kind)。
    """
    right_w = max(1, right_w)  # review 修复：right_w<=0 外部调用防御
    block = getattr(rec, "source_block", None)
    if block is not None:
        return _block_styled_rows(block, right_w, kind)
    lines = getattr(rec, "_detail_lines", None)
    if lines is None:
        lines = getattr(rec, "lines", None) or []
    if not lines:
        return []
    key = (_lines_fp(lines), len(lines), right_w, kind)
    cached = _MD_RENDER_CACHE.get(key)
    if cached is not None:
        return cached
    from src.tui.app.apply import _render_markdown_lines
    text = "\n".join(str(ln) for ln in lines)
    try:
        ansi_lines = _render_markdown_lines(text, max(right_w, 20))
    except Exception:
        ansi_lines = []
    rows: list = []
    for aline in ansi_lines:
        rows.extend(_convert_ansi_row(aline, right_w, kind))
    _MD_RENDER_CACHE[key] = rows
    if len(_MD_RENDER_CACHE) > _MD_RENDER_CACHE_MAX:
        _MD_RENDER_CACHE.clear()  # 有界：超限清空重建（miss 仅多一次渲染）
    return rows


def _inspector_children(rec, right_w: int, vh: int) -> list:
    """检查器子元素（标题 + 元信息 + 内容行；内容按栏宽换行 + 视口截断）。

    每行 TEXT 带**唯一 key**（``tinsp-*``）——修复 fiber 共享环（2026-08-19）：
    同层多个无 key TEXT 被调和器按派生 key（``host:text``）匹配到同一 fiber →
    同一 fiber 挂到多个位置（sibling 链环）→ ``find_input_fiber`` 全树 DFS
    无限循环（渲染线程卡死）。key 唯一后调和按 key 1:1 复用。

    Args:
        rec: 选中 TraceRecord（None = 空台账）。
        right_w: 右栏宽。
        vh: 视口行数预算（内容行数上限）。
    """
    if rec is None:
        return [h(TEXT, {
            "children": "无轨迹记录", "style": _S_HINT, "height": 1,
            "key": "tinsp-empty",
        })]
    children: list = []
    kind = getattr(rec, "kind", "context")
    title = f"#{getattr(rec, 'index', 0)} {_KIND_NAME.get(kind, kind)}"
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _STATUS_ICON.get(status, "\u00b7")
        title = f"{title} {sicon} {status}"
    children.append(h(TEXT, {
        "children": title, "style": _S_TITLE, "height": 1, "key": "tinsp-title",
    }))
    # 元信息（耗时 / token）
    meta: list = []
    ts = getattr(rec, "time_seconds", None)
    if ts is not None:
        meta.append(f"耗时 {format_duration(ts)}")
    tokens = getattr(rec, "tokens", None) or {}
    if tokens:
        meta.append(f"输入 {format_tokens(int(tokens.get('input', 0) or 0))}")
        meta.append(f"输出 {format_tokens(int(tokens.get('output', 0) or 0))}")
    if meta:
        children.append(h(TEXT, {
            "children": " · ".join(meta), "style": _S_DIM, "height": 1,
            "key": "tinsp-meta",
        }))
    # 内容行（按栏宽换行；视口行数截断 + 省略提示）
    budget = max(_INSPECTOR_MIN_CONTENT, vh - 2 - (1 if meta else 0))
    shown = 0
    truncated = False
    lines = getattr(rec, "_detail_lines", None)
    if lines is None:
        # 直接调用（测试/外部使用）未挂载惰性详情时回退记录内联 lines
        lines = getattr(rec, "lines", None) or []
    if right_w <= 0:
        right_w = 40  # 无栏宽上下文防御（_wrap_by_width max_width<=0 返回空）
    # ★ 2026-08-16（用户需求：思考/回答省略提示改「… 前 N 行省略」）：
    #   思考（reasoning）/回答（content）为流式生成内容——检查器优先显示
    #   **最新内容（尾部）**（与轨迹尾部跟随语义一致：用户关注正在生成的
    #   最新行），被截断时置顶提示「… 前 N 行省略」（省略的是前部旧内容）；
    #   其余种类（system/user/tool/subagent/context）保持从头部显示（省略
    #   尾部，「… 后 N 行省略」）。
    # ★ 2026-08-17（用户需求：回答/思考/system 用流式 markdown 显示在右边）：
    #   reasoning/content/system 详情经 markdown 渲染管线（``_md_detail_rows``
    #   ——消息源模式原始文本重新渲染 / 块记录直接复用渲染输出）显示带样式
    #   StyledRun 行，内容增长（流式）自动重渲染（rec/block 缓存）；其余种类
    #   保持纯文本按宽换行（零回归）。
    tail_first = kind in ("reasoning", "content")
    segs: list = []
    md_rows: list = []
    use_md = kind in ("reasoning", "content", "system")
    if use_md:
        md_rows = _md_detail_rows(rec, right_w, kind)
    # ★ 2026-08-17（用户需求：轨迹 Trace 工具调用修改——参数用树控件显示，
    #   然后分割线，返回值用树控件显示）：tool 记录且携带参数/返回值树数据
    #   （``tool_args``/``tool_result``）→ 检查器内容 = 参数树 + 分割线 +
    #   返回值树（``_tool_tree_rows``——head-first 截断 + 「… 后 N 行省略」
    #   后置，与既有 tool 纯文本分支语义一致）；无树数据（手动构造/异常
    #   路径）回退纯文本 lines（零回归——既有 test_inspector_tool_* 直接
    #   构造无树数据的记录）。
    use_tool_tree = kind == "tool" and (
        (getattr(rec, "tool_args", None) is not None
         and str(getattr(rec, "tool_args", "")) != "")
        or (getattr(rec, "tool_result", "") or "")
    )
    if use_tool_tree:
        tree_rows = _tool_tree_rows(rec, right_w)
        total_lines = len(tree_rows)
        for runs in tree_rows:
            if shown >= budget:
                truncated = True
                break
            segs.append(runs)
            shown += 1
    elif use_md and md_rows:
        total_lines = len(md_rows)
        src_rows = reversed(md_rows) if tail_first else md_rows
        for runs in src_rows:
            if shown >= budget:
                truncated = True
                break
            segs.append(runs)
            shown += 1
    else:
        # ★ review 修复（P1-2）：纯文本分支统计**换行后总行数**（单行拆
        #   多行/截断提前 break 均计入）——省略提示数值 = 总行数 - 已显示
        #   行数，修复前 ``len(lines) - shown + 1`` 用原始行数减换行后行数
        #   可显示负数（如 1 行拆 11 行显示 8 行 → 1-8+1 = -2）。
        total_lines = 0
        for li, line in enumerate(lines):
            if not isinstance(line, str):
                line = str(line)
            wrapped = _wrap_by_width(line, right_w)
            total_lines += len(wrapped)
            for seg in wrapped:
                if shown >= budget:
                    truncated = True
                    break
                segs.append(seg)
                shown += 1
            if truncated:
                # 补算剩余行的换行数（省略提示数值准确）
                for rest in lines[li + 1:]:
                    r = str(rest) if not isinstance(rest, str) else rest
                    total_lines += len(_wrap_by_width(r, right_w))
                break
    if tail_first:
        segs.reverse()  # reversed 遍历恢复正序（省略提示在前、最新内容在后）
    omitted = max(1, total_lines - shown) if truncated else 0
    if truncated and tail_first:
        # 思考/回答（tail_first）：省略的是前部旧内容 → 「… 前 N 行省略」
        # 置顶（在内容行之前——与 toolcard bash 尾显示「… 前 N 行省略」
        # 前置语义一致，提示紧跟被省略内容一侧）
        children.append(h(TEXT, {
            "children": f"\u2026 前 {omitted} 行省略",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted",
        }))
    for seg in segs:
        if isinstance(seg, list):
            # markdown 渲染行（StyledRun 列表——children 纯文本仅供测试/
            # 调试可见，渲染走 styled 优先分支）
            children.append(h(TEXT, {
                "children": "".join(r.text for r in seg) if seg else " ",
                "styled": seg,
                "height": 1,
                "key": f"tinsp-{len(children)}",
            }))
        else:
            children.append(h(TEXT, {
                "children": seg if seg else " ",
                "style": _S_DIM if kind == "reasoning" else _S_TEXT,
                "height": 1,
                "key": f"tinsp-{len(children)}",
            }))
    if truncated and not tail_first:
        # 其余种类（system/user/tool/subagent/context，head-first）：省略的
        # 是尾部内容 → 「… 后 N 行省略」**后置在内容行之后**（最后一行——
        # 与 toolcard 头显示「head 省略的行在末尾——提示置于内容行之后，
        # 对齐终端 head 语义」一致；修复前统一前置在内容上方，与语义不符）
        children.append(h(TEXT, {
            "children": f"\u2026 后 {omitted} 行省略",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted",
        }))
    if not lines and not md_rows and not use_tool_tree:
        children.append(h(TEXT, {
            "children": "(无内容)", "style": _S_HINT, "height": 1,
            "key": "tinsp-none",
        }))
    # ★ 2026-08-16（轨迹 Trace 嵌套）：subagent 记录检查器追加操作提示——
    #   「Enter 查看该子代理的轨迹」（引导用户下钻到 subagent 轨迹视图）。
    #   ★ 2026-08-17（用户需求：agent 内容合并到 subagent）：合并后
    #   的 subagent 工具记录同样携带 subagent_label——提示条件从
    #   kind=="subagent" 放宽为 subagent_label 非空（独立 subagent 记录与
    #   合并 tool 记录均可下钻）。
    if getattr(rec, "subagent_label", ""):
        children.append(h(TEXT, {
            "children": "\u23ce Enter 查看该子代理的轨迹",
            "style": _S_HINT, "height": 1, "key": "tinsp-subagent-hint",
        }))
    return children


def _block_fingerprint(model) -> tuple:
    """块指纹（use_memo deps）：块种类/行数/关闭态/工具状态——行数变化
    （流式追加）或状态变化才重建记录列表；时间基元素不入指纹（台账静态
    色，不随动画重建）。"""
    fp = []
    for b in getattr(model, "blocks", None) or []:
        extra = getattr(b, "extra", None) or {}
        fp.append((
            getattr(b, "kind", ""),
            len(getattr(b, "lines", None) or []),
            bool(getattr(b, "closed", False)),
            extra.get("tool_status", ""),
        ))
    return tuple(fp)


def _subagent_fingerprint() -> tuple:
    """subagent 槽位指纹（use_memo deps）：顺序 + 状态 + 工具历史长度。

    控制器不存在/未装配时返回空元组（零成本——无 subagent 记录）。

    ★ 2026-08-17（用户需求：已完成 subagent 仍可查看轨迹）：数据源与
    ``trace._subagent_records`` 一致 = 面板 store（未注册槽位）+ **轨迹存档**
    （``_trace_archive``——``stop()`` 清空 store 后存档保留 → 指纹稳定，
    主轨迹持续显示已完成 subagent 记录；新任务注册覆盖存档 → 指纹变化
    触发重建）。遍历顺序复用 ``trace._subagent_label_order``（单一实现，
    review 方向：避免与记录构建逻辑漂移）。
    """
    try:
        from src.tui.app.trace import _subagent_label_order
        from src.tui.subagent import SubAgentPanelController
        controller = SubAgentPanelController.get_default()
        store = getattr(controller, "_store", None)
        if store is None:
            return ()
        with store._state_lock:
            order = list(getattr(store, "_order", None) or [])
            agents = getattr(store, "_agents", None) or {}
            archive = getattr(controller, "_trace_archive", None) or {}
            labels = _subagent_label_order(order, archive)
            fp = tuple(
                (label,
                 getattr(agents.get(label) or archive.get(label), "status", ""),
                 len(getattr(agents.get(label) or archive.get(label),
                             "tool_history", None) or []))
                for label in labels
            )
        return fp
    except Exception:
        return ()


def _records_deps(model) -> tuple:
    """记录构建 use_memo 依赖（数据源自适应指纹）。

    消息源模式（装配注入 agent.messages）：``_messages_fingerprint`` +
    ``_live_fingerprint`` + ``_subagent_fingerprint``——消息内容变化（流式
    完成后追加/编辑）、**实时生成内容**（开放块行数/内容长度、运行中工具
    输出）与 subagent 槽位状态（新增/状态变更/工具历史增长——消息源模式
    同样追加 subagent 记录）任一变化均触发重建：流式生成期间 agent.messages
    不变，靠实时指纹驱动台账动态显示正在生成的内容（用户需求 2026-08-19）。
    块模式：块指纹 + subagent 指纹（内容变化才重建）。时间基元素不入指纹
    （台账静态色，不随动画重建）。
    """
    if getattr(model, "message_source", None) is not None:
        from src.tui.app.trace import _live_fingerprint, _messages_fingerprint
        return (_messages_fingerprint(model), _live_fingerprint(model),
                _subagent_fingerprint())
    return (_block_fingerprint(model), _subagent_fingerprint())


def _subagent_trace_deps(label: str) -> tuple:
    """subagent 轨迹 use_memo 依赖（嵌套视图数据源指纹）。

    消息列表身份 + 长度 + 末条消息（内容增长/追加触发重建）+ 槽位状态 +
    工具历史长度 + **动态元素**（模型阶段/解析摘要/运行中工具 phase——
    SubAgent 模型调用为非流式，运行中内容以占位记录动态显示；阶段/工具
    状态变化触发重建）——subagent 消息逐轮追加 + 运行中状态推进时轨迹台账
    实时更新；时间基元素（耗时）不入指纹（台账静态色）。
    """
    from src.tui.app.trace import _subagent_slot
    slot = _subagent_slot(label)
    if slot is None:
        return ("missing", label)
    messages = getattr(slot, "messages", None) or []
    if isinstance(messages, list) and messages:
        tail = messages[-1]
        if isinstance(tail, dict):
            tail_fp = (
                id(tail), tail.get("role", ""),
                len(str(tail.get("content", ""))),
                len(tail.get("tool_calls") or ()),
            )
        else:
            tail_fp = (id(tail), str(tail)[:40])
        msg_fp = (id(messages), len(messages), tail_fp)
    else:
        msg_fp = (id(messages), len(messages))
    # 动态元素（subagent 动态部分——与 mainagent _live_fingerprint 同语义：
    # 运行中工具/阶段/流式内容长度变化触发台账重建）
    tool_live = tuple(
        (getattr(r, "tool_name", ""), getattr(r, "phase", ""))
        for r in getattr(slot, "tool_history", None) or []
    )
    live_fp = (
        getattr(slot, "status", ""),
        getattr(slot, "model_phase", "") or "",
        getattr(slot, "parse_info", "") or "",
        len(getattr(slot, "live_reasoning", "") or ""),
        len(getattr(slot, "live_content", "") or ""),
        tool_live,
    )
    return (label, msg_fp, live_fp)


def _row_of_record(rows: list, sel: int, records: list) -> int:
    """记录 sel 在台账行（rows）中的下标（分隔行不计入选择）。"""
    if not (0 <= sel < len(records)):
        return 0
    target = records[sel]
    for i, row in enumerate(rows):
        if row is target:
            return i
    return 0


def _records_index_of_row(rows: list, row_idx: int) -> int:
    """台账行下标 → 记录索引（跳过 None 分隔行；row 为 None/越界返回 -1）。"""
    if not (0 <= row_idx < len(rows)):
        return -1
    count = 0
    for i in range(row_idx + 1):
        if rows[i] is None:
            continue
        if i == row_idx:
            return count
        count += 1
    return -1


def _ledger_renderer(rows: list, left_w: int, records: list, model):
    """台账行渲染函数（ListView renderItem 三参签名）。

    items 为 ``rows``（TraceRecord 或 None 分隔行）：
      - 分隔行（None）→ 轮次分隔行 TEXT（``── 轮次 N ──``）；
      - 记录行 → ``_ledger_row_runs``（选中整行背景高亮 + ▶ 标记），
        isSelected 由 ListView 注入（受控 cursor 行）。
    """
    def render_item(item, idx, is_sel):
        if item is None:
            n = sum(1 for r in rows[:idx] if r is None) + 1  # 第 n 个分隔 = 轮次 n
            return h(TEXT, {
                "key": f"tsep-{idx}",
                "styled": _sep_row_runs(n, left_w),
                "height": 1,
            })
        return h(TEXT, {
            "key": f"trow-{idx}",
            "styled": _ledger_row_runs(item, bool(is_sel), left_w),
            "height": 1,
        })
    return render_item


def TraceView(props) -> object:
    """轨迹视图组件（App 消息区替换渲染；Ctrl+H 开关）。

    Props:
        model: AppModel 实例（blocks/subagent_lines/trace_open/trace_selected）。
        width: 终端宽度（左右栏宽分配）。

    ★ 全面控件化（方案B）：台账左栏经标准控件 ``ListView`` 表达——
    受控光标（``cursor``= 选中记录在 rows 中的下标）、虚拟滚动
    （``height``= 台账可见行数，内部自动滚动）、导航
    （↑↓/PgUp/PgDn/Home/End/g/G，None 分隔行自动跳过）、选中态注入
    （``renderItem`` 三参 isSelected）；导航结果经 ``onNavigate`` 写回
    ``model.trace_selected``（退出尾部跟随）。本组件 use_input 仅处理
    关闭类按键（Esc/Ctrl+H）——其余导航/选择键放行 ListView 消费，
    Enter 放行（非模态：提交消息）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    # ★ 2026-08-16（轨迹 Trace 嵌套）：trace_subagent_label 非 None = 主轨迹
    #   中按 Enter 选中 subagent 记录后进入其轨迹（嵌套 TraceView——显示
    #   subagent 轨迹，内容与 mainagent 同构：system/user/思考/回答/工具）。
    sub_label = getattr(model, "trace_subagent_label", None) or None

    # ── 数据（use_memo 指纹缓存：消息源/块/subagent 内容变化才重建） ──
    if sub_label:
        records, rows = use_memo(
            lambda: build_subagent_trace_records(sub_label, model),
            _subagent_trace_deps(sub_label),
        )
    else:
        records, rows = use_memo(
            lambda: build_trace_records(model),
            _records_deps(model),
        )
    total = len(records)

    # ── 选中解析（-1 = 跟随尾部：渲染期解析为最新记录，流式追加自动跟进） ──
    sel = getattr(model, "trace_selected", -1)
    if total == 0:
        sel = 0
    elif sel == -1 or sel >= total:
        sel = total - 1

    # ── 视口 / 栏宽 ──
    vh = _viewport_rows()
    if width > 0:
        left_w = max(24, int(width * 0.45))
        if width - left_w - 1 < 20:
            left_w = max(20, width - 21)
        right_w = max(1, width - left_w - 1)
    else:
        left_w, right_w = 40, 40

    # ── 选中记录详情（惰性提取 + use_memo：行列表身份/行数变化才重建） ──
    rec = records[sel] if 0 <= sel < total else None
    detail_lines = use_memo(
        lambda: _detail_lines_of(rec),
        _detail_deps(rec),
    )
    if rec is not None:
        rec._detail_lines = detail_lines

    # ── 台账可见窗口（选中记录在 rows 中的下标——ListView 受控光标） ──
    row_count = len(rows)
    sel_row = _row_of_record(rows, sel, records) if row_count else 0

    # ── 输入（trace_open 期间激活；关闭类按键本组件消费，导航放行 ListView） ──
    def _handle(event) -> bool:
        if not getattr(model, "trace_open", False):
            return False
        # 关闭类按键（Esc / Ctrl+H）——subagent 轨迹优先返回主轨迹
        #   （trace_subagent_label 置 None），主轨迹才关闭整个视图
        if event.kind == "escape":
            if getattr(model, "trace_subagent_label", None):
                model.trace_subagent_label = None
                model.trace_selected = -1  # 返回主轨迹：回到尾部跟随
            else:
                model.trace_open = False
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            if getattr(model, "trace_subagent_label", None):
                model.trace_subagent_label = None
                model.trace_selected = -1
            else:
                model.trace_open = False
            return True
        # Enter：主轨迹中选中 subagent 记录 → 进入 subagent 轨迹（嵌套
        #   TraceView——显示内容与 mainagent 同构）。subagent 轨迹内 Enter
        #   放行（提交消息）；sub-subagent 下钻不阻断（覆盖 label）。
        # ★ 2026-08-17（用户需求：agent 内容合并到 subagent）：合并
        #   后的 subagent 工具记录携带 subagent_label（kind 仍为 tool）
        #   ——下钻条件从 kind=="subagent" 放宽为 subagent_label 非空（独立
        #   subagent 记录与合并 tool 记录均可 Enter 进入 subagent 轨迹）。
        if event.kind == "enter" and not getattr(model, "trace_subagent_label", None):
            rec = records[sel] if 0 <= sel < total else None
            if rec is not None:
                sub = getattr(rec, "subagent_label", "") or ""
                if sub:
                    model.trace_subagent_label = sub
                    model.trace_selected = -1  # subagent 轨迹：尾部跟随
                    return True
        # 其余按键（↑↓/PgUp/PgDn/Home/End/g/G/Enter/字符）放行——导航由
        # ListView 消费，Enter/字符放行（非模态：提交消息/打字）
        return False

    use_input(_handle, bool(getattr(model, "trace_open", False)))

    def _on_navigate(row_idx: int) -> None:
        """台账导航回调（ListView 导航后）：写回 model.trace_selected（退出跟随）。"""
        rec_idx = _records_index_of_row(rows, row_idx)
        if rec_idx >= 0:
            model.trace_selected = rec_idx

    # ── 渲染 ──
    # 头部（静态色——轨迹视图为浏览界面，不呼吸，diff 零输出）
    turn_count = sum(1 for r in rows if r is None)
    if sub_label:
        header_title = f"\u258d子代理轨迹 {sub_label}"
        header_hint = "  \u2191\u2193 选择 · PgUp/PgDn 翻页 · g/G 首末 · Esc/Ctrl+H 返回"
    else:
        header_title = "\u258d轨迹 Trace"
        header_hint = ("  \u2191\u2193 选择 · PgUp/PgDn 翻页 · g/G 首末 · "
                       "Enter 进入子代理 · Esc/Ctrl+H 关闭")
    header_runs = [
        StyledRun(header_title, _S_TITLE),
        StyledRun(f" · {total} 条 · {turn_count} 轮", _S_HINT),
        StyledRun(header_hint, _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)

    # 左栏（台账——ListView 标准控件：受控光标 + 虚拟滚动 + 分隔行跳过）
    ledger = h(ListView, {
        "items": rows,
        "height": vh,
        "width": left_w,
        "cursor": sel_row if row_count else 0,
        "renderItem": _ledger_renderer(rows, left_w, records, model),
        "onNavigate": _on_navigate,
        "focus": bool(getattr(model, "trace_open", False)),
    })
    # 右栏（检查器）
    right_rows = _inspector_children(rec, right_w, vh)

    return h(Column, None, [
        h(TEXT, {"styled": header_runs, "height": 1}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_rows),
        ]),
    ])


__all__ = [
    "TraceView",
    "_ledger_row_runs",
    "_inspector_children",
    "_viewport_rows",
    "_subagent_trace_deps",
    "_md_detail_rows",
    "_block_styled_rows",
    "_convert_ansi_row",
    "_lines_fp",
    "_to_tui_style",
    "_value_to_tree",
    "_args_to_tree",
    "_parse_tree_text",
    "_tool_tree_rows",
    "_tree_node_rows",
]
