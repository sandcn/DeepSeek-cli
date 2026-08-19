"""trace_view — TraceView 轨迹视图组件（DSH 风格左台账 + 右检查器，2026-08-19）。

Ctrl+H（0x08）打开/关闭：App 在 ``model.fullscreen == "trace"``（兼容别名
``model.trace_open``）时经全屏视图注册表**整屏只渲染本组件**（消息区/顶部
标题栏/状态栏/输入区全部不显示——「打开时其他 TUI 不显示，只显示这个
界面」），台账/检查器占满整个终端高度；Esc/Ctrl+H 关闭后恢复完整聊天界面。

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

键盘（use_input 路由 + 模态全屏声明，trace_open 期间激活）：
  - ↑↓ 选择 · PgUp/PgDn 翻页 · Home/End、g/G 首末 · Esc/Ctrl+H 关闭；
  - Enter 选中 subagent 记录 → **进入 subagent 轨迹**（嵌套 TraceView——
    显示内容与 mainagent 同构：system/user/思考/回答/工具，Esc/Ctrl+H 返回
    主轨迹）；其余记录 Enter/其余按键**不消费**——经 ``use_fullscreen``
    （2026-08-17 模态全屏视图通用机制）被 input router 吞掉：字符/Enter 不
    落入输入缓冲（杜绝看不见的输入），关闭视图后恢复输入区正常输入。
"""

from __future__ import annotations

import json
import time as _time

from src.tui._format import format_duration, format_tokens
from src.tui._input_layout import _wrap_by_width
from src.tui.app.trace import (
    block_detail_lines,
    build_subagent_trace_records,
    build_trace_records,
)
from src.tui.core.style import Style
from src.tui.ink import (
    TEXT, Column, Row, StyledRun, h, use_fullscreen, use_input, use_memo, use_ref,
)
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
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
_S_TREE_KEY = Style(fg=75)                 # 树节点键（浅紫蓝——BEAUTY-36 键值分色）
_S_TREE_VAL = Style(fg=252)                # 树节点标量值（亮白——BEAUTY-36 键值分色）
#: 检查器光标行背景（2026-08-19 用户需求：右边高亮当前行背景色）——vim
#:   cursorline 语义：检查器焦点时 j/k 移动光标，光标所在行整行背景高亮
#:   （与台账选中行 _S_SEL_BG 同色 237，两栏视觉一致）
_S_INSP_BG = Style(bg=237)
#: 搜索匹配行背景（2026-08-19 用户需求：轨迹 Trace vim 风格搜索——所有
#:   匹配行高亮，vim hlsearch 风格；暗蓝灰 236，低调不抢选中焦点）
_S_SEARCH_BG = Style(bg=236)
#: 当前匹配行背景（匹配 + 选中/光标叠加——n/N 定位到的当前匹配行用亮蓝
#:   25 区分，与 _S_SEL_BG 237 形成视觉层级：所有匹配 < 当前匹配）
_S_SEARCH_CUR_BG = Style(bg=25)
#: 搜索输入行提示样式（底部 ``/${query}``——vim 风格，亮青加粗）
_S_SEARCH_PROMPT = Style(fg=45, bold=True)

#: 树节点指示符/缩进（对齐 ink Tree 控件渲染语义——检查器参数/返回值
#:   以树形结构展示：层级缩进 + 展开指示符）
_TREE_OPEN = "\u25be "    # ▾ 展开
_TREE_CLOSED = "\u25b8 "  # ▸ 折叠（2026-08-19 用户需求：树控件空格展开/收缩）
_TREE_LEAF = "  "         # 叶子占位（对齐 Tree._TREE_LEAF）
_TREE_INDENT = 2          # 每层缩进空格数（对齐 Tree._TREE_INDENT）
#: 参数/返回值小节标题前缀
_SECTION_PREFIX = "\u25b8 "  # ▸

#: 台账行 runs 模块级缓存（性能：ListView 每帧对可见行调 renderItem——
#:   同内容记录跨 rec 命中返回同一 runs 引用，零重建 + TEXT wrap 引用级
#:   命中零重写；运行中耗时按整数秒入指纹，每秒刷新一次）。有界防无限
#:   增长（超限清空重建——miss 仅多一次 runs 构建，无正确性影响）。
_LEDGER_RUNS_CACHE: dict = {}
_LEDGER_RUNS_CACHE_MAX = 256
#: 台账行预计算索引缓存（性能：O(N²) 优化——分隔行编号/记录↔行映射/轮次
#:   数一次 O(N) 预计算，跨帧 O(1) 查表；rows 来自 use_memo（内容不变引用
#:   稳定）→ 命中零重建；records 重建 → 新 rows 引用 → 一次性 O(N) 重建，
#:   远低于修复前每帧对每个可见分隔行的累计扫描）。有界防无限增长（超限
#:   清空重建——miss 仅多一次索引构建，无正确性影响）。
_ROWS_INDEX_CACHE_MAX = 4
_rows_index_cache: dict = {}  # id(rows) → (rows_ref, (sep_nums, rec_to_row, row_to_rec))
#: 轮次分隔行 runs 缓存（与 _ledger_row_runs 同缓存上限——纯函数输出复用）
_SEP_RUNS_CACHE: dict = {}

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
#: 检查器内容行全量生成上限（2026-08-19 用户需求：轨迹 Trace 移动到右边
#:   滚动查看——内容行**全量生成**后按滚动窗口切片；超大内容（如大文件
#:   工具返回）防御性截断，超限追加「内容过长」提示行，滚动到底部可见）
_INSPECTOR_MAX_ROWS = 2000


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


def _rec_time_seconds(rec) -> float | None:
    """记录实时耗时（运行中记录按起始时间戳实时计算；其余用快照）。

    ★ 2026-08-19（用户需求：轨迹 Trace 正运行的工具耗时没有刷新）：运行中
    工具/subagent 耗时随时间增长，但 records 仅在内容变化时重建
    （``_records_deps``/``_subagent_trace_deps`` 时间基元素不入指纹——工具
    无输出/状态不变期间 use_memo 命中）——rec.time_seconds 为构建时**快照**
    会冻结。渲染层（台账行每帧读取 / 检查器 use_memo deps）改经本函数取
    实时值：running 记录按 ``time_started``（构建时保留的起始时间戳）实时
    计算，并按**整数秒**入指纹（每秒刷新一次，避免每帧重建）。

    时间基准（``time_started_monotonic``）：True=单调时钟（主轨迹工具 box
    ``_tool_started_at``=time.monotonic）；False=墙上时钟（subagent 槽位
    ``start_time``=time.time）。异常/缺失起始时间戳回退快照（防御）。
    """
    if getattr(rec, "status", "") != "running":
        return getattr(rec, "time_seconds", None)
    started = getattr(rec, "time_started", None)
    if started is None:
        return getattr(rec, "time_seconds", None)
    try:
        started_f = float(started)
    except (TypeError, ValueError):
        return getattr(rec, "time_seconds", None)
    if getattr(rec, "time_started_monotonic", True):
        return max(0.0, _time.monotonic() - started_f)
    return max(0.0, _time.time() - started_f)


def _record_search_text(rec) -> str:
    """记录全文（台账搜索匹配文本源——summary + 详情行 + 返回 + 参数/返回值）。

    搜索目标 = 记录的可视内容全集：摘要（台账行）、详情行（lines）、返回
    预览（result）、工具参数/返回值（tool_args/tool_result）、subagent
    label。块记录详情（source_block）不展开（惰性提取，搜索覆盖摘要/返回
    已足够——记录正文经 lines/result 表达）。字段缺失/异常防御拼接。
    """
    parts: list = []
    for attr in ("summary", "result", "subagent_label"):
        v = getattr(rec, attr, None)
        if v:
            parts.append(str(v))
    lines = getattr(rec, "lines", None) or []
    for ln in lines:
        if isinstance(ln, str):
            parts.append(ln)
        else:
            plain = getattr(ln, "plain", None)
            parts.append(plain if plain is not None else str(ln))
    for attr in ("tool_args", "tool_result"):
        v = getattr(rec, attr, None)
        if v is not None:
            parts.append(str(v))
    return "\n".join(p for p in parts if p)


def _row_search_text(row) -> str:
    """内容行文本（检查器搜索匹配文本源——StyledRun 行/纯文本行归一化）。"""
    if isinstance(row, str):
        return row
    if isinstance(row, (list, tuple)):
        return "".join(getattr(r, "text", "") or "" for r in row)
    return str(row)


def _trace_search_matches(pattern: str, side: str, records: list,
                          content_rows: list | None = None) -> list:
    """正则搜索 → 匹配索引列表（当前焦点面板；非法正则 → 空列表）。

    Args:
        pattern: 正则表达式（re.search 语义——子串匹配，非全匹配）。
        side: "ledger"=搜索台账记录（索引 = 记录索引）/ "inspector"=搜索
            检查器内容行（索引 = 内容行索引）。
        records: 台账记录列表。
        content_rows: 检查器全量内容行（side=="inspector" 时需要；None 时
            视为无内容）。

    Returns:
        list[int]——匹配索引（首次出现顺序）。
    """
    if not pattern or side not in ("ledger", "inspector"):
        return []
    try:
        rx = __import__("re").compile(pattern)
    except Exception:
        return []  # 非法正则 → 无匹配（不崩溃，vim 中非法正则报错后无结果）
    matches: list = []
    if side == "ledger":
        for i, rec in enumerate(records):
            if rec is None:
                continue
            try:
                if rx.search(_record_search_text(rec)):
                    matches.append(i)
            except Exception:
                continue
    else:
        for i, row in enumerate(content_rows or []):
            try:
                if rx.search(_row_search_text(row)):
                    matches.append(i)
            except Exception:
                continue
    return matches


def _ledger_row_runs(rec, sel: bool, left_w: int,
                     matched: bool = False, cur_match: bool = False) -> list:
    """台账行 runs（选中行整行背景高亮 + ▶ 标记；耗时右对齐；宽截断）。

    Args:
        rec: TraceRecord。
        sel: 是否选中。
        left_w: 左栏宽（>0 时截断；<=0 不截断防御）。
        matched: 是否搜索匹配行（vim hlsearch 风格——匹配行背景 _S_SEARCH_BG）。
        cur_match: 是否当前匹配行（n/N 定位到的匹配——背景 _S_SEARCH_CUR_BG，
            比普通匹配更醒目；与 sel 叠加）。

    ★ 性能（2026-08-19 用户需求：轨迹 Trace 优化性能）：**内容指纹缓存**
    （``_LEDGER_RUNS_CACHE``）——ListView 每帧对可见行调用 renderItem →
    本函数每帧重建 StyledRun（含 truncate 宽计算）；同内容记录（records
    流式重建新对象但字段值相同）→ 指纹命中 → 返回同一 runs 列表引用（零
    重建；TEXT ``_wrap_cache`` 按 styled 引用命中 → 渲染层也零重写）。运行
    中耗时（``_rec_time_seconds`` 实时值）按**整数秒**入指纹（每秒刷新一次，
    避免每帧重建——与检查器 meta 同语义）。有界防无限增长（超限清空重建）。
    """
    t_raw = _rec_time_seconds(rec)
    t_key = int(t_raw) if t_raw is not None else None
    key = (
        getattr(rec, "index", 0),
        getattr(rec, "kind", ""),
        getattr(rec, "summary", "") or "",
        getattr(rec, "status", "") or "",
        getattr(rec, "result", "") or "",
        t_key,
        bool(sel),
        left_w,
        bool(matched),
        bool(cur_match),
    )
    cached = _LEDGER_RUNS_CACHE.get(key)
    if cached is not None:
        return cached
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
    if t_raw is not None:
        t = format_duration(t_raw)
    if t and left_w > 0:
        used = sum(getattr(r, "width", 1) for r in runs)
        pad = left_w - used - len(t) - 1
        if pad > 0:
            runs.append(StyledRun(" " * pad, None))
        runs.append(StyledRun(t, _S_TIME))
    # ★ 2026-08-19（vim 搜索匹配高亮）：背景优先级——
    #   当前匹配（_S_SEARCH_CUR_BG）> 匹配行（_S_SEARCH_BG）> 选中（_S_SEL_BG）
    #   > 无。当前匹配行与选中同时成立时用亮蓝（区分普通匹配的暗蓝灰）。
    if cur_match:
        bg = _S_SEARCH_CUR_BG
    elif matched:
        bg = _S_SEARCH_BG
    elif sel:
        bg = _S_SEL_BG
    else:
        bg = None
    if bg is not None:
        runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
    runs = truncate_runs(runs, left_w) if left_w > 0 else runs
    _LEDGER_RUNS_CACHE[key] = runs
    if len(_LEDGER_RUNS_CACHE) > _LEDGER_RUNS_CACHE_MAX:
        _LEDGER_RUNS_CACHE.clear()
    return runs


def _sep_row_runs(n: int, left_w: int) -> list:
    """轮次分隔行 runs（``── 轮次 N ──``，深灰）。

    ★ 性能（2026-08-19 用户需求：轨迹 Trace 优化性能）：内容纯函数
    （同 n/left_w 输出恒同）——模块级缓存返回同一 runs 引用（零重建 +
    TEXT wrap 引用级命中）；与 ``_ledger_row_runs`` 同缓存上限。
    """
    key = (n, left_w)
    cached = _SEP_RUNS_CACHE.get(key)
    if cached is not None:
        return cached
    runs = [StyledRun(f"\u2500\u2500 轮次 {n} \u2500\u2500", _S_SEP_ROW)]
    runs = truncate_runs(runs, left_w) if left_w > 0 else runs
    _SEP_RUNS_CACHE[key] = runs
    if len(_SEP_RUNS_CACHE) > _LEDGER_RUNS_CACHE_MAX:
        _SEP_RUNS_CACHE.clear()
    return runs


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


def _tree_node_rows(nodes: list, right_w: int, out: list, depth: int = 0,
                    collapsed: set | None = None, path: str = "",
                    keys: list | None = None) -> None:
    """树节点列表 → 可见行（前序；缩进 + 展开指示符；对齐 Tree 控件渲染）。

    只读展示（检查器不参与树交互——台账 ListView 独占导航焦点），label 含
    ``\\n`` 归一化单行（防行级 diff 宽度不变量破坏）。

    ★ 2026-08-19（用户需求：轨迹 Trace 的工具的实参要显示完整）：超宽行
    由截断（``truncate_runs``——超宽部分直接丢弃，长实参在检查器不可见）
    改为**换行显示完整**（``_tree_row_wrap`` → ``wrap_runs_by_width`` hard
    字符级硬拆，与检查器纯文本 ``_wrap_by_width`` 同语义）——续行带
    hanging indent（缩进到首行内容起始列，值与层级视觉连贯）；极窄栏
    （缩进 >= 栏宽）续行不缩进（预算不足防御，内容仍完整）。换行增多的
    行数由检查器滚动窗口（vim j/k/g/G）浏览，受 ``_INSPECTOR_MAX_ROWS``
    上限保护。

    ★ 2026-08-19（用户需求：树控件按空格可以展开和收缩，默认展开所有）：
    ``collapsed`` 为**折叠节点路径 key 集合**（空集合/None = 全部展开——
    默认）——折叠节点不递归 children（其子级行不进入可见列表，与 ink Tree
    控件 ``_collect_visible`` 同语义），指示符切换为 ``▸``（_TREE_CLOSED）。
    节点路径 key（``path`` 递归拼接：``f"{path}/{i}"``，根为 ``"i"``——
    同数据同 key 稳定，可区分同 label 兄弟）写入 ``keys``（与 out 行对齐：
    可折叠节点首行 = 节点 key，叶子/续行为 None）——检查器空格切换展开/
    收缩经此把光标行映射到节点。参数树/返回值树经 ``path="args"/"res"``
    前缀隔离（两树路径 key 不冲突）。

    ★ BEAUTY-36（2026-08-19 美化）：键/值分色——``key: value`` 形态的叶子
    行键（含缩进 + 展开指示符）浅紫蓝（_S_TREE_KEY 75）、值亮白
    （_S_TREE_VAL 252），树形参数/返回值的层级更易扫读；无 ``": "``
    分隔（纯值/纯文本行）整行 _S_TEXT（零回归）。
    """
    if depth > _TREE_MAX_DEPTH:
        return
    for i, node in enumerate(nodes):
        node_path = f"{path}/{i}" if path else str(i)
        children = node.get("children") or []
        folded = bool(collapsed) and node_path in collapsed
        if children:
            indicator = _TREE_CLOSED if folded else _TREE_OPEN
        else:
            indicator = _TREE_LEAF
        prefix = " " * (depth * _TREE_INDENT)
        label = node.get("label", "")
        if "\n" in label:
            label = label.replace("\n", " ")
        sep_idx = label.find(": ")
        if sep_idx >= 0:
            runs = [
                StyledRun(f"{prefix}{indicator}{label[:sep_idx]}: ", _S_TREE_KEY),
                StyledRun(label[sep_idx + 2:], _S_TREE_VAL),
            ]
        else:
            runs = [StyledRun(f"{prefix}{indicator}{label}", _S_TEXT)]
        _tree_row_wrap(
            runs, len(prefix) + len(indicator), max(1, right_w), out,
            node_key=node_path if children else None, keys=keys,
        )
        if children and not folded:
            _tree_node_rows(
                children, right_w, out, depth + 1, collapsed, node_path, keys,
            )


def _tree_row_wrap(runs: list, hang: int, right_w: int, out: list,
                   node_key: str | None = None, keys: list | None = None) -> None:
    """树行换行：首行预算 right_w；续行 hanging indent=hang（内容完整）。

    ★ 用户需求（2026-08-19：轨迹 Trace 的工具的实参要显示完整）：修复前
    ``truncate_runs(runs, right_w)`` 直接丢弃超宽部分——长实参（bash
    command / update_file old_string 全文等）在轨迹检查器不可见。换行后
    每行宽度 <= right_w（行级 diff 宽度不变量保持）；续行缩进 hang 列
    （对齐首行内容起始列——值与层级视觉连贯）；hang >= right_w（极窄栏）
    时续行不缩进（预算不足防御，内容仍完整）。宽度依据
    ``wrap_runs_by_width``（hard 字符级硬拆，与检查器纯文本换行同语义——
    CJK 安全、不拆宽字符）。

    ★ 2026-08-19（树控件空格展开/收缩）：``keys`` 与 out 行对齐——首行写
    ``node_key``（可折叠节点路径 key；叶子 None）、续行写 None（换行行
    不可折叠）。检查器空格经 ``row_keys[cursor]`` 定位光标所在节点。
    """
    if keys is not None:
        keys.append(node_key)
    if right_w <= 0:
        out.append(list(runs))
        return
    if not runs:
        out.append([])
        return
    lines = wrap_runs_by_width(runs, right_w, hard=True)
    out.append(list(lines[0].runs))
    if len(lines) <= 1:
        return
    rest: list = []
    for ln in lines[1:]:
        rest.extend(ln.runs)
    indent = hang if hang < right_w else 0
    cont_w = max(1, right_w - indent)
    for ln in wrap_runs_by_width(rest, cont_w, hard=True):
        if keys is not None:
            keys.append(None)
        row = [StyledRun(" " * indent, None)] if indent else []
        row.extend(ln.runs)
        out.append(row)


def _tool_tree_rows(rec, right_w: int, collapsed: set | None = None) -> tuple:
    """tool 记录检查器树内容行：**参数树 → 分割线 → 返回值树**。

    ★ 2026-08-17（用户需求：轨迹 Trace 工具调用修改——参数用树控件显示，
    然后分割线，返回值用树控件显示）：选中 tool 记录时检查器内容 =
      1. ``▸ 参数`` 小节标题 + 参数树（``tool_args`` JSON 树形展开）；
      2. 分割线（``──`` 深灰满宽——分隔参数与返回值）；
      3. ``▸ 返回值`` 小节标题 + 返回值树（``tool_result`` JSON 树形展开，
         非 JSON 文本每行一个叶子）。
    参数/返回值缺失（None/空）→ 对应小节占位提示（(无参数)/(无返回)）。

    ★ 2026-08-19（用户需求：树控件按空格可以展开和收缩，默认展开所有）：
    ``collapsed`` 为折叠节点路径 key 集合（空/None = 全部展开）——参数树
    路径前缀 ``"args"``、返回值树 ``"res"``（两树路径 key 不冲突）；返回
    ``(rows, keys)``——keys 与 rows 逐行对齐（可折叠节点行 = 节点路径 key，
    小节标题/分割线/占位/叶子/换行续行为 None），检查器空格切换据此定位
    光标所在节点。

    Args:
        rec: tool TraceRecord（读 ``tool_args``/``tool_result``）。
        right_w: 右栏宽（行超宽截断；<=0 外部调用防御）。
        collapsed: 折叠节点路径 key 集合（None/空 = 全部展开）。

    Returns:
        (rows, keys)：
        - rows: list[list[StyledRun]]——树内容行（head-first 顺序；预算/
          截断/省略提示由 ``_inspector_children`` 统一处理）；
        - keys: list[str | None]——与 rows 对齐的节点路径 key 列表。
    """
    right_w = max(1, right_w)
    args = getattr(rec, "tool_args", None)
    result = str(getattr(rec, "tool_result", "") or "")
    key = (repr(args)[:200], result[:200], len(result), right_w,
           tuple(sorted(collapsed or ())))
    cached = _TOOL_TREE_CACHE.get(key)
    if cached is not None:
        return cached
    rows: list = []
    keys: list = []
    # ── 1. 参数小节（树控件显示参数） ──
    rows.append([StyledRun(f"{_SECTION_PREFIX}参数", _S_SECTION)])
    keys.append(None)
    arg_nodes = _args_to_tree(args)
    if arg_nodes:
        _tree_node_rows(arg_nodes, right_w, rows, collapsed=collapsed,
                        path="args", keys=keys)
    else:
        rows.append([StyledRun("(无参数)", _S_HINT)])
        keys.append(None)
    # ── 2. 分割线（参数 / 返回值 之间的分隔） ──
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP_ROW)])
    keys.append(None)
    # ── 3. 返回值小节（树控件显示返回值） ──
    rows.append([StyledRun(f"{_SECTION_PREFIX}返回值", _S_SECTION)])
    keys.append(None)
    result_nodes = _parse_tree_text(result)
    if result_nodes:
        _tree_node_rows(result_nodes, right_w, rows, collapsed=collapsed,
                        path="res", keys=keys)
    else:
        rows.append([StyledRun("(无返回)", _S_HINT)])
        keys.append(None)
    cached = (rows, keys)
    _TOOL_TREE_CACHE[key] = cached
    if len(_TOOL_TREE_CACHE) > _TOOL_TREE_CACHE_MAX:
        _TOOL_TREE_CACHE.clear()
    return cached


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


def _inspector_content_rows(rec, right_w: int, collapsed: set | None = None) -> tuple:
    """检查器**全量内容行**（正序；上限防御）——滚动查看数据源。

    ★ 2026-08-19（用户需求：轨迹 Trace 移动到右边查看东西 + vim 风格）：
    检查器由「视口截断」改为「**全量生成 + 滚动窗口切片**」——焦点移到
    右栏后 j/k/↑↓/PgUp/PgDn/g/G 滚动浏览全部内容（含被省略部分）。内容
    行按 kind 分支生成（与旧 ``_inspector_children`` 截断逻辑同源）：
      - tool 且携带树数据 → ``_tool_tree_rows``（参数树 + 分割线 + 返回值树）；
      - reasoning/content/system → ``_md_detail_rows``（markdown 渲染行）；
      - 其余 → 纯文本按栏宽换行（``_wrap_by_width``）。
    返回元素为 ``list[StyledRun]``（markdown/树样式行）或 ``str``（纯文本
    行）——窗口切片后由 ``_inspector_children`` 统一转 TEXT 元素。

    ★ 2026-08-19（用户需求：树控件按空格可以展开和收缩，默认展开所有）：
    返回 ``(rows, keys)``——keys 与 rows 逐行对齐（工具树行 = 节点路径
    key；其余行 None），检查器空格经 ``keys[cursor]`` 定位光标所在节点并
    切换折叠。``collapsed`` 为折叠节点路径 key 集合（None/空 = 全部展开
    ——默认）。

    ★ 上限防御：超大内容（大文件工具返回/长回答）全量生成有界
    （``_INSPECTOR_MAX_ROWS``）——超限截断并追加「内容过长」提示行
    （滚动到底部可见，不静默丢内容；与 less 分页提示同语义）。

    Args:
        rec: 选中 TraceRecord。
        right_w: 右栏宽（换行/截断宽度；<=0 外部调用防御）。
        collapsed: 工具树折叠节点路径 key 集合（None/空 = 全部展开）。

    Returns:
        (rows, keys)：rows 为内容行列表；keys 为与 rows 对齐的节点路径
        key 列表（str=可折叠节点行；None=叶子/非树行）。
    """
    right_w = max(1, right_w)
    kind = getattr(rec, "kind", "context")
    rows: list = []
    keys: list = []
    # ★ 2026-08-17（工具调用参数/返回值用树控件）：tool 记录且携带树数据 →
    #   参数树 + 分割线 + 返回值树（全量——滚动可查看全部层级）
    use_tool_tree = kind == "tool" and (
        (getattr(rec, "tool_args", None) is not None
         and str(getattr(rec, "tool_args", "")) != "")
        or (getattr(rec, "tool_result", "") or "")
    )
    if use_tool_tree:
        rows, keys = _tool_tree_rows(rec, right_w, collapsed)
    elif kind in ("reasoning", "content", "system"):
        # markdown 渲染行（块记录直接复用渲染输出 / 内联原始文本重渲染）
        rows = list(_md_detail_rows(rec, right_w, kind))
        keys = [None] * len(rows)
    else:
        lines = getattr(rec, "_detail_lines", None)
        if lines is None:
            # 直接调用（测试/外部使用）未挂载惰性详情时回退记录内联 lines
            lines = getattr(rec, "lines", None) or []
        for line in lines:
            if not isinstance(line, str):
                line = str(line)
            rows.extend(_wrap_by_width(line, right_w))
        keys = [None] * len(rows)
    if len(rows) > _INSPECTOR_MAX_ROWS:
        rows = rows[:_INSPECTOR_MAX_ROWS]
        keys = keys[:_INSPECTOR_MAX_ROWS]
        rows.append([StyledRun(
            f"\u2026 内容过长，仅显示前 {_INSPECTOR_MAX_ROWS} 行", _S_HINT,
        )])
        keys.append(None)
    return rows, keys


def _inspector_content_deps(rec, right_w: int, collapsed: set | None = None) -> tuple:
    """检查器内容行 use_memo 依赖（TraceView 内 ``_inspector_content_rows``
    包装）。

    与 ``_detail_deps`` 同源（块行数/树内容/lines 身份，展平原子值）+
    栏宽 + **折叠集合**（空格展开/收缩触发重建）——内容变化（流式增长/
    树输出变化）、栏宽变化或折叠状态变化才重建全量内容行；时间基元素
    （耗时）不入指纹（检查器 meta 经 ``_inspector_deps`` 每秒刷新）。
    """
    if rec is None:
        return (None, right_w, ())
    return tuple(_detail_deps(rec)) + (right_w,) + (tuple(sorted(collapsed or ())),)


def _inspector_children(
    rec, right_w: int, vh: int, scroll: int = 0, content_rows: list | None = None,
    cursor: int = -1, row_keys: list | None = None, collapsed: set | None = None,
    search_matches: list | None = None, search_cur: int = -1,
) -> list:
    """检查器子元素（标题 + 元信息 + 内容行滚动窗口 + 光标行高亮 + 省略提示）。

    每行 TEXT 带**唯一 key**（``tinsp-*``）——修复 fiber 共享环（2026-08-19）：
    同层多个无 key TEXT 被调和器按派生 key（``host:text``）匹配到同一 fiber →
    同一 fiber 挂到多个位置（sibling 链环）→ ``find_input_fiber`` 全树 DFS
    无限循环（渲染线程卡死）。key 唯一后调和按 key 1:1 复用。

    ★ 2026-08-19（用户需求：轨迹 Trace 移动到右边查看东西 + vim 风格）：
    **滚动窗口渲染**——标题/meta 固定顶部，内容行按 ``scroll`` 偏移取
    视口窗口（``content_rows`` 全量行切片）；scroll>0 时置顶「… 前 N 行
    省略」，窗口未到尾部时后置「… 后 N 行省略」。scroll 越界钳制到合法
    范围（内容不足一屏 → 0）。reasoning/content 不再特判尾部优先——滚动
    能力取代（vim/less 语义：scroll=0=顶部，G 跳尾部看最新内容）。

    ★ 2026-08-19（用户需求：右边高亮当前行背景色）：``cursor`` 为内容
    光标行（绝对行索引，0-based；-1 = 不高亮——台账焦点/直接调用默认）。
    cursor 落在窗口内的行**整行背景高亮**（``_S_INSP_BG``——vim
    cursorline 语义，与台账选中行同色；markdown/树 StyledRun 行逐 run
    合并背景，纯文本行样式合并背景）。

    ★ 2026-08-19（用户需求：树控件按空格可以展开和收缩，默认展开所有）：
    ``row_keys`` 为与 content_rows 对齐的节点路径 key 列表（None=惰性
    生成时同步生成）——TraceView 空格切换经 ``row_keys[cursor]`` 定位
    光标所在可折叠节点；``collapsed`` 为折叠集合（惰性生成时传入）。

    ★ 2026-08-19（vim 搜索匹配高亮）：``search_matches`` 为搜索匹配内容
    行索引列表、``search_cur`` 为当前匹配行索引（-1=无）——匹配行背景
    ``_S_SEARCH_BG``、当前匹配行 ``_S_SEARCH_CUR_BG``（vim hlsearch 风格，
    所有匹配行高亮；当前匹配行与光标行叠加时用亮蓝区分）。None = 无搜索
    （零成本快路径）。

    Args:
        rec: 选中 TraceRecord（None = 空台账）。
        right_w: 右栏宽。
        vh: 视口行数预算（内容行数上限）。
        scroll: 内容滚动偏移（0=顶部；越界钳制）。
        content_rows: 预生成的全量内容行（TraceView 组件经 use_memo 传入，
            避免双份生成）；None 时内部惰性生成（直接调用/测试兼容）。
        cursor: 内容光标行绝对索引（-1 = 不高亮）。
        row_keys: 与 content_rows 对齐的节点路径 key 列表（None=惰性生成）。
        collapsed: 工具树折叠节点路径 key 集合（惰性生成时传入）。
        search_matches: 搜索匹配内容行索引列表（None = 无搜索高亮）。
        search_cur: 当前匹配内容行索引（-1 = 无当前匹配）。
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
    # ★ 2026-08-19（用户需求：轨迹 Trace 正运行的工具耗时没有刷新）：耗时
    #   经 ``_rec_time_seconds`` 实时计算（运行中记录按起始时间戳走动——
    #   工具无输出/records 不重建期间 meta 行每秒刷新）。
    meta: list = []
    ts = _rec_time_seconds(rec)
    if ts is not None:
        meta.append(f"耗时 {format_duration(ts)}")
    tokens = getattr(rec, "tokens", None) or {}
    if tokens:
        meta.append(f"输入 {format_tokens(_safe_int(tokens.get('input', 0) or 0))}")
        meta.append(f"输出 {format_tokens(_safe_int(tokens.get('output', 0) or 0))}")
    if meta:
        children.append(h(TEXT, {
            "children": " · ".join(meta), "style": _S_DIM, "height": 1,
            "key": "tinsp-meta",
        }))
    # ── 内容行（全量生成 → 滚动窗口切片；光标行高亮；省略提示两侧） ──
    if content_rows is None:
        content_rows, row_keys = _inspector_content_rows(rec, right_w, collapsed)
    total = len(content_rows)
    try:
        scroll = int(scroll) or 0
    except (TypeError, ValueError, OverflowError):
        scroll = 0
    try:
        cursor = int(cursor) if cursor is not None else -1
    except (TypeError, ValueError, OverflowError):
        cursor = -1
    # 内容区行数预算（标题/meta/省略提示/subagent 提示占位后）
    fixed = 2 + (1 if meta else 0) + (1 if getattr(rec, "subagent_label", "") else 0)
    content_vh = max(_INSPECTOR_MIN_CONTENT, vh - fixed)
    if total > content_vh:
        scroll = max(0, min(scroll, total - content_vh))
    else:
        scroll = 0
    # 顶部省略提示（scroll>0：省略的是前部内容）
    if scroll > 0:
        children.append(h(TEXT, {
            "children": f"\u2026 前 {scroll} 行省略",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted-top",
        }))
    window = content_rows[scroll:scroll + content_vh]
    # 底部省略提示预留 1 行（内容未到尾部时窗口收缩，提示后置可见）
    if scroll + len(window) < total:
        window = window[:max(0, len(window) - 1)]
        bottom_omitted = total - scroll - len(window)
    else:
        bottom_omitted = 0
    for i, seg in enumerate(window):
        abs_idx = scroll + i
        is_cursor = cursor >= 0 and abs_idx == cursor
        # ★ 2026-08-19（vim 搜索匹配高亮）：背景优先级——
        #   当前匹配（_S_SEARCH_CUR_BG）> 匹配行（_S_SEARCH_BG）> 光标行
        #   （_S_INSP_BG）> 无。
        is_match = search_matches is not None and abs_idx in search_matches
        is_cur_match = is_match and abs_idx == search_cur
        if is_cur_match:
            bg = _S_SEARCH_CUR_BG
        elif is_match:
            bg = _S_SEARCH_BG
        elif is_cursor:
            bg = _S_INSP_BG
        else:
            bg = None
        if bg is not None and isinstance(seg, list):
            # markdown/树渲染行（StyledRun 列表）——逐 run 合并背景色
            seg = [
                StyledRun(r.text, (r.style or Style()).merge(bg))
                for r in seg
            ]
        if isinstance(seg, list):
            # markdown/树渲染行（StyledRun 列表——children 纯文本仅供测试/
            # 调试可见，渲染走 styled 优先分支）
            children.append(h(TEXT, {
                "children": "".join(r.text for r in seg) if seg else " ",
                "styled": seg,
                "height": 1,
                "key": f"tinsp-{len(children)}",
            }))
        else:
            # 纯文本行——光标/匹配行样式合并背景色（vim cursorline/hlsearch）
            style = _S_DIM if kind == "reasoning" else _S_TEXT
            if bg is not None:
                style = style.merge(bg)
            children.append(h(TEXT, {
                "children": seg if seg else " ",
                "style": style,
                "height": 1,
                "key": f"tinsp-{len(children)}",
            }))
    if bottom_omitted:
        children.append(h(TEXT, {
            "children": f"\u2026 后 {bottom_omitted} 行省略",
            "style": _S_HINT, "height": 1, "key": "tinsp-omitted",
        }))
    if total == 0:
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


def _safe_int(v, default=0) -> int:
    """int 归一化（P3 review 防御）：str/None/NaN/inf 等异常注入值回退默认。

    ``int(nan)`` ValueError / ``int(inf)`` OverflowError——异常冒泡会中断
    TraceView 渲染（与 ``_clamp_color`` 的全面防御风格对齐）。
    """
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def _inspector_deps(
    rec, right_w: int, vh: int, scroll: int = 0, cursor: int = -1,
) -> tuple:
    """检查器 use_memo 依赖（TraceView 内 ``_inspector_children`` 包装）。

    ★ 2026-08-19（用户需求：轨迹 Trace 优化性能）：检查器元素树（标题 +
    元信息 + 内容行 TEXT）在 TraceView 组件体内每帧直接构建（h() 调用）——
    选中记录内容不变时**元素树每帧重建**（仅内容行缓存命中）。use_memo
    包装后：deps = ``_detail_deps``（内容行数据源——块行数/树内容/lines 身份，
    展平原子值）+ 标题/元信息字段（index/kind/status/tokens/time）+ 栏宽/
    视口 + **滚动偏移**（scroll 变化触发重建——vim 滚动）+ **光标行**
    （cursor 变化触发重建——高亮行移动）。内容不变 → deps 稳定 → 元素树
    引用稳定 → reconciler 短路零重建。运行中耗时（``_rec_time_seconds``
    实时值）按**整数秒**入指纹（meta 行每秒刷新一次，避免每帧重建）。
    ★ P3（review 2026-08-19）：time/tokens 经 ``_safe_int`` 归一化——
    异常注入值（str/NaN/inf）不再中断渲染。
    ★ 2026-08-19（vim 面板浏览）：末尾追加 scroll（滚动窗口位置）与
    cursor（光标行，-1=不高亮）——滚动/光标键触发重建；越界残留经
    ``_safe_int`` 归一化防御。
    """
    if rec is None:
        return (None, right_w, vh, 0, -1)
    tok = getattr(rec, "tokens", None) or {}
    t_raw = _rec_time_seconds(rec)
    return tuple(_detail_deps(rec)) + (
        getattr(rec, "index", 0),
        getattr(rec, "kind", "") or "",
        getattr(rec, "status", "") or "",
        _safe_int(t_raw) if t_raw is not None else None,
        _safe_int(tok.get("input", 0) or 0),
        _safe_int(tok.get("output", 0) or 0),
        right_w,
        vh,
        _safe_int(scroll, 0),
        _safe_int(cursor, -1),
    )


def _block_fingerprint(model) -> tuple:
    """块指纹（use_memo deps）：块种类/行数/关闭态/工具状态——行数变化
    （流式追加）或状态变化才重建记录列表；时间基元素不入指纹（台账静态
    色，不随动画重建）。

    ★ 2026-08-19（用户需求：轨迹 Trace 优化性能）：**返回展平原子值**
    （无嵌套 tuple——use_memo deps 逐项按值比较，嵌套 tuple 按 is 恒 miss
    导致缓存永久失效，见 trace._messages_fingerprint 说明）。
    """
    fp: list = []
    for b in getattr(model, "blocks", None) or []:
        extra = getattr(b, "extra", None) or {}
        fp.extend((
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
            fp: list = []
            for label in labels:
                slot = agents.get(label) or archive.get(label)
                fp.extend((
                    label,
                    getattr(slot, "status", "") or "",
                    len(getattr(slot, "tool_history", None) or []),
                ))
        # ★ 2026-08-19（用户需求：轨迹 Trace 优化性能）：返回展平原子值
        #   （无嵌套 tuple——use_memo deps 逐项按值比较；嵌套 tuple 按 is
        #   恒 miss 导致缓存永久失效，见 trace._messages_fingerprint 说明）。
        return tuple(fp)
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

    ★ 2026-08-19（用户需求：轨迹 Trace 优化性能）：**返回展平原子值**
    （``itertools.chain`` 拼接各指纹——各指纹已展平为原子值；use_memo deps
    逐项 ``_object_is`` 按值比较，嵌套 tuple 按 is 恒 miss 导致缓存永久
    失效 → 每帧全量重建 records + ListView 全重渲染。修复后内容不变 →
    deps 稳定 → use_memo 命中零重建）。
    """
    from itertools import chain
    if getattr(model, "message_source", None) is not None:
        from src.tui.app.trace import _live_fingerprint, _messages_fingerprint
        return tuple(chain(
            _messages_fingerprint(model),
            _live_fingerprint(model),
            _subagent_fingerprint(),
        ))
    return tuple(chain(
        _block_fingerprint(model),
        _subagent_fingerprint(),
    ))


def _subagent_trace_deps(label: str) -> tuple:
    """subagent 轨迹 use_memo 依赖（嵌套视图数据源指纹）。

    消息列表身份 + 长度 + 末条消息（内容增长/追加触发重建）+ 槽位状态 +
    工具历史长度 + **动态元素**（模型阶段/解析摘要/运行中工具 phase——
    SubAgent 模型调用为非流式，运行中内容以占位记录动态显示；阶段/工具
    状态变化触发重建）——subagent 消息逐轮追加 + 运行中状态推进时轨迹台账
    实时更新；时间基元素（耗时）不入指纹（台账静态色）。

    ★ 2026-08-19（用户需求：轨迹 Trace 优化性能）：**返回展平原子值**
    （无嵌套 tuple——use_memo deps 逐项按值比较；嵌套 tuple 按 is 恒 miss
    导致缓存永久失效，见 trace._messages_fingerprint 说明）。工具 phase
    序列（``tool_live``）逐对展平为 (name, phase, name, phase, ...)。
    """
    from itertools import chain
    from src.tui.app.trace import _subagent_slot
    slot = _subagent_slot(label)
    if slot is None:
        return ("missing", label)
    messages = getattr(slot, "messages", None) or []
    tail_fp: tuple = ()
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
        msg_fp = (id(messages), len(messages))
    else:
        msg_fp = (id(messages), len(messages))
    # 动态元素（subagent 动态部分——与 mainagent _live_fingerprint 同语义：
    # 运行中工具/阶段/流式内容长度变化触发台账重建）
    tool_live = tuple(chain.from_iterable(
        (getattr(r, "tool_name", ""), getattr(r, "phase", ""))
        for r in getattr(slot, "tool_history", None) or []
    ))
    live_fp = (
        getattr(slot, "status", "") or "",
        getattr(slot, "model_phase", "") or "",
        getattr(slot, "parse_info", "") or "",
        len(getattr(slot, "live_reasoning", "") or ""),
        len(getattr(slot, "live_content", "") or ""),
    )
    return (label, *msg_fp, *tail_fp, *live_fp, *tool_live)


def _rows_index(rows: list) -> tuple:
    """台账行预计算索引：(sep_nums, rec_to_row, row_to_rec)。

    - ``sep_nums``: {row_idx: 轮次数}——分隔行编号 O(1) 查表（修复前
      ``_ledger_renderer`` 的 ``sum(1 for r in rows[:idx] if r is None)``
      对每个可见分隔行每帧 O(idx) 扫描 + O(idx) 切片分配，大台账累计
      O(N×视口) ≈ O(N²)）；
    - ``rec_to_row``: {id(record): row_idx}——``_row_of_record`` O(1) 查表
      （修复前每帧 O(N) 线性扫描）；
    - ``row_to_rec``: list（row_idx → records 索引；分隔行为 -1）——
      ``_records_index_of_row`` O(1) 查表（修复前 O(row_idx)）。

    缓存 keyed by ``id(rows)`` + 引用校验（rows 来自 use_memo：内容不变
    引用稳定 → 跨帧命中零重建）。``_rows_index`` 只遍历 rows（不依赖
    records——rows 中非 None 项顺序与 records 索引一一对应）。
    """
    key = id(rows)
    entry = _rows_index_cache.get(key)
    if entry is not None and entry[0] is rows:
        return entry[1]
    sep_nums: dict = {}
    rec_to_row: dict = {}
    row_to_rec: list = []
    sep = 0
    rec_idx = 0
    for i, r in enumerate(rows):
        if r is None:
            sep += 1
            sep_nums[i] = sep
            row_to_rec.append(-1)
        else:
            rec_to_row[id(r)] = i
            row_to_rec.append(rec_idx)
            rec_idx += 1
    idx = (sep_nums, rec_to_row, row_to_rec)
    if len(_rows_index_cache) >= _ROWS_INDEX_CACHE_MAX:
        _rows_index_cache.clear()
    _rows_index_cache[key] = (rows, idx)
    return idx


def _row_of_record(rows: list, sel: int, records: list) -> int:
    """记录 sel 在台账行（rows）中的下标（分隔行不计入选择）。

    ★ 性能（O(N²) 优化）：预计算 ``rec_to_row`` 映射 O(1) 查表——修复前
    ``for i, row in enumerate(rows): if row is target`` 每帧 O(N) 线性扫描
    （大台账下随渲染帧数累积）。
    """
    if not (0 <= sel < len(records)):
        return 0
    target = records[sel]
    _, rec_to_row, _ = _rows_index(rows)
    return rec_to_row.get(id(target), 0)


def _records_index_of_row(rows: list, row_idx: int) -> int:
    """台账行下标 → 记录索引（跳过 None 分隔行；row 为 None/越界返回 -1）。

    ★ 性能（O(N²) 优化）：预计算 ``row_to_rec`` 映射 O(1) 查表——修复前
    ``for i in range(row_idx + 1)`` O(row_idx)（导航回调高频触发时随台账
    行数累积）。
    """
    if not (0 <= row_idx < len(rows)):
        return -1
    _, _, row_to_rec = _rows_index(rows)
    return row_to_rec[row_idx]


def _ledger_renderer(rows: list, left_w: int,
                     matched_ids: set | None = None,
                     cur_rec_id: int | None = None):
    """台账行渲染函数（ListView renderItem 三参签名）。

    ★ P3（review 2026-08-18）：删除未使用的 ``records``/``model`` 死参数
      ——渲染仅消费 rows/left_w（分隔行编号经 ``_rows_index`` 查表），
      死参数误导后续维护（调用点同步收紧签名）。

    items 为 ``rows``（TraceRecord 或 None 分隔行）：
      - 分隔行（None）→ 轮次分隔行 TEXT（``── 轮次 N ──``）；
      - 记录行 → ``_ledger_row_runs``（选中整行背景高亮 + ▶ 标记），
        isSelected 由 ListView 注入（受控 cursor 行）。

    ★ 2026-08-19（vim 搜索匹配高亮）：``matched_ids`` 为搜索匹配记录
    ``id(rec)`` 集合、``cur_rec_id`` 为当前匹配记录 id——匹配行背景
    ``_S_SEARCH_BG``、当前匹配行 ``_S_SEARCH_CUR_BG``（vim hlsearch 风格，
    所有匹配行高亮）。None = 无搜索（零成本快路径）。

    ★ 性能（O(N²) 优化）：分隔行编号经 ``_rows_index`` 预计算 O(1) 查表
    （``sep_nums``）——修复前 ``sum(1 for r in rows[:idx] if r is None)``
    对每个可见分隔行每帧 O(idx) 扫描 + ``rows[:idx]`` O(idx) 切片分配，
    大台账（多轮次）下每帧 O(N×视口) ≈ O(N²)。
    """
    sep_nums, _, _ = _rows_index(rows)

    def render_item(item, idx, is_sel):
        if item is None:
            n = sep_nums.get(idx, 1)  # 第 n 个分隔 = 轮次 n（O(1) 查表）
            return h(TEXT, {
                "key": f"tsep-{idx}",
                "styled": _sep_row_runs(n, left_w),
                "height": 1,
            })
        matched = matched_ids is not None and id(item) in matched_ids
        cur_match = cur_rec_id is not None and id(item) == cur_rec_id
        return h(TEXT, {
            "key": f"trow-{idx}",
            "styled": _ledger_row_runs(
                item, bool(is_sel), left_w, matched, cur_match,
            ),
            "height": 1,
        })
    return render_item


def TraceView(props) -> object:
    """轨迹视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（blocks/subagent_lines/fullscreen/trace_selected）。
        width: 终端宽度（左右栏宽分配）。

    ★ 全面控件化（方案B）：台账左栏经标准控件 ``ListView`` 表达——
    受控光标（``cursor``= 选中记录在 rows 中的下标）、虚拟滚动
    （``height``= 台账可见行数，内部自动滚动）、导航
    （↑↓/PgUp/PgDn/Home/End/g/G，None 分隔行自动跳过）、选中态注入
    （``renderItem`` 三参 isSelected）；导航结果经 ``onNavigate`` 写回
    ``model.trace_selected``（退出尾部跟随）。本组件 use_input 仅处理
    关闭类按键（Esc/Ctrl+H）——其余导航/选择键放行 ListView 消费，
    Enter/字符等由 ``use_fullscreen``（模态全屏视图通用机制）吞掉——
    不落入输入缓冲（杜绝看不见的输入）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    # ★ 2026-08-16（轨迹 Trace 嵌套）：trace_subagent_label 非 None = 主轨迹
    #   中按 Enter 选中 subagent 记录后进入其轨迹（嵌套 TraceView——显示
    #   subagent 轨迹，内容与 mainagent 同构：system/user/思考/回答/工具）。
    sub_label = getattr(model, "trace_subagent_label", None) or None

    # ★ 2026-08-17（用户需求：选最后一行时新行自动选择最新行）：上次渲染
    #   记录总数（use_ref 跨帧持久）——渲染期据此检测「上次选中最后一条 +
    #   本次记录增长」→ 自动转为尾部跟随（选择最新行）。hooks 顺序稳定：
    #   首个 hook（渲染数据 use_memo 之前）。
    prev_total_ref = use_ref(0)
    prev_total = prev_total_ref.current

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
    prev_total_ref.current = total

    # ── 选中解析（-1 = 跟随尾部：渲染期解析为最新记录，流式追加自动跟进） ──
    sel = getattr(model, "trace_selected", -1)
    # ★ 2026-08-17（用户需求：选最后一行时新行自动选择最新行）：上次渲染
    #   选中**最后一条**（具体索引 == 上次 total-1）且本次记录增长 → 自动
    #   转为尾部跟随（trace_selected 置 -1，下方解析为最新记录）——覆盖
    #   非导航路径（历史遗留具体索引 == 末行）与「导航到末行后追加」的
    #   兜底（正常导航到末行经 ``_on_navigate`` 直接写 -1，见下）。
    if prev_total > 0 and sel == prev_total - 1 and total > prev_total:
        model.trace_selected = -1
        sel = -1
    if total == 0:
        sel = 0
    elif sel == -1 or sel >= total:
        sel = total - 1

    # ── 视口 / 栏宽 ──
    # ★ 2026-08-19（vim 搜索）：搜索输入模式时底部显示 ``/${query}`` 行，
    #   台账/检查器可见高度减 1（输入行占一行）。
    search_mode = bool(getattr(model, "trace_search_mode", False))
    vh = _viewport_rows() - (1 if search_mode else 0)
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

    # ── 面板焦点 / 检查器滚动与光标（2026-08-19：移动到右边查看东西 + vim） ──
    # trace_pane: "ledger"=左台账（ListView 焦点） / "inspector"=右检查器
    #   （内容光标浏览——当前行背景高亮）；l/h 切换，j/k 在台账移动选中 /
    #   在检查器移动光标（vim cursorline 语义）。
    pane = getattr(model, "trace_pane", "ledger") or "ledger"
    if pane not in ("ledger", "inspector"):
        pane = "ledger"
    scroll_raw = getattr(model, "trace_inspector_scroll", 0) or 0
    cursor_raw = getattr(model, "trace_inspector_cursor", 0) or 0

    # ── 右栏（检查器） ──
    # 全量内容行（use_memo：内容/栏宽/折叠变化才重建）——滚动窗口数据源
    #   （total_content 供 _handle 光标钳制与 scroll 渲染期协调）
    # ★ 2026-08-19（用户需求：树控件按空格可以展开和收缩，默认展开所有）：
    #   折叠集合（``model.trace_tree_collapsed``——空 = 全部展开，默认）
    #   传入内容行生成（折叠节点子级行不进入可见列表）；keys 与行对齐——
    #   空格经 keys[cursor] 定位光标所在节点。
    collapsed = set(getattr(model, "trace_tree_collapsed", None) or ())
    content = use_memo(
        lambda: _inspector_content_rows(rec, right_w, collapsed),
        _inspector_content_deps(rec, right_w, collapsed),
    )
    content_rows, row_keys = content
    total_content = len(content_rows)
    approx_content_vh = max(_INSPECTOR_MIN_CONTENT, vh - 3)
    # 光标渲染期钳制（写回 model——越界残留收敛；空内容 → 0）
    if total_content:
        cursor = max(0, min(cursor_raw, total_content - 1))
    else:
        cursor = 0
    if cursor != cursor_raw:
        model.trace_inspector_cursor = cursor
        cursor_raw = cursor
    # scroll 渲染期协调：钳制 + 跟随光标保持可见（vim 视口语义——光标在
    #   窗口内移动不滚动，到边界才滚动；与 _handle ``_scroll_for_cursor``
    #   同逻辑）
    if total_content > approx_content_vh:
        if scroll_raw < 0:
            scroll_raw = 0
        elif scroll_raw > total_content - approx_content_vh:
            scroll_raw = total_content - approx_content_vh
        if cursor < scroll_raw:
            scroll_raw = cursor
        elif cursor >= scroll_raw + approx_content_vh:
            scroll_raw = cursor - approx_content_vh + 1
    else:
        scroll_raw = 0
    if scroll_raw != (getattr(model, "trace_inspector_scroll", 0) or 0):
        model.trace_inspector_scroll = scroll_raw
    scroll = scroll_raw
    # 检查器光标参数：仅检查器焦点传入（高亮）；台账焦点 -1（不高亮）
    cursor_arg = cursor if pane == "inspector" else -1
    # ★ 2026-08-19（vim 搜索匹配高亮）：搜索状态展平读取——台账匹配记录
    #   id 集合（``_ledger_renderer`` 背景高亮）与检查器匹配内容行索引
    #   （``_inspector_children`` 背景高亮）；当前匹配行（n/N 定位）更强色。
    search_side = getattr(model, "trace_search_side", "") or ""
    search_pattern = getattr(model, "trace_search_pattern", "") or ""
    search_matches = list(getattr(model, "trace_search_matches", None) or [])
    search_idx = getattr(model, "trace_search_idx", -1)
    ledger_matched_ids: set | None = None
    ledger_cur_id: int | None = None
    if search_side == "ledger" and search_pattern and search_matches:
        ledger_matched_ids = {
            id(records[i]) for i in search_matches if 0 <= i < len(records)
        }
        if 0 <= search_idx < len(search_matches):
            mi = search_matches[search_idx]
            if 0 <= mi < len(records):
                ledger_cur_id = id(records[mi])
    insp_matches = (
        search_matches if (search_side == "inspector" and search_pattern) else None
    )
    insp_cur = -1
    if insp_matches and 0 <= search_idx < len(search_matches):
        insp_cur = search_matches[search_idx]
    # 搜索状态指纹（use_memo deps 展平原子值——嵌套 tuple 按 is 恒 miss）
    search_fp = (search_side, search_pattern, search_idx) + tuple(search_matches)
    # ★ 2026-08-19（用户需求：轨迹 Trace 优化性能）：修复前组件体内每帧
    #   直接调用 ``_inspector_children``（h(TEXT) 元素树每帧重建，仅内容行
    #   缓存命中）；use_memo 包装后 deps（``_inspector_deps`` = 内容行数据
    #   源 + 标题/元信息字段 + 栏宽/视口 + scroll + cursor）不变 → 元素树
    #   引用稳定 → reconciler 短路零重建。运行中耗时按整数秒入指纹（meta
    #   每秒刷新一次）。
    right_rows = use_memo(
        lambda: _inspector_children(
            rec, right_w, vh, scroll, content_rows, cursor_arg,
            row_keys, collapsed, insp_matches, insp_cur,
        ),
        _inspector_deps(rec, right_w, vh, scroll, cursor_arg)
        + (total_content,) + search_fp,
    )

    # ── 台账可见窗口（选中记录在 rows 中的下标——ListView 受控光标） ──
    row_count = len(rows)
    sel_row = _row_of_record(rows, sel, records) if row_count else 0

    # ── 输入（trace_open 期间激活；关闭类按键本组件消费，导航放行 ListView） ──
    def _scroll_for_cursor(cursor: int, scroll: int) -> int:
        """检查器视口滚动：钳制 + 跟随光标保持可见（vim 视口语义）。

        cursor 在窗口内（``[scroll, scroll+approx_content_vh)``）不滚动；
        光标越过上/下边界 → 滚动窗口使光标回到边缘可见。内容不足一屏 →
        0。``_inspector_children`` 内部对 scroll 做精确钳制（本函数为近似
        视口，差异 ≤ 省略提示行数，渲染兜底）。
        """
        if total_content <= approx_content_vh:
            return 0
        scroll = max(0, min(int(scroll), total_content - approx_content_vh))
        cursor = max(0, min(int(cursor), total_content - 1))
        if cursor < scroll:
            return cursor
        if cursor >= scroll + approx_content_vh:
            return cursor - approx_content_vh + 1
        return scroll

    def _move_cursor(new_cursor: int) -> None:
        """检查器光标移动：写回 cursor + scroll 跟随（保持光标可见）。"""
        if total_content:
            new_cursor = max(0, min(int(new_cursor), total_content - 1))
        else:
            new_cursor = 0
        model.trace_inspector_cursor = new_cursor
        model.trace_inspector_scroll = _scroll_for_cursor(
            new_cursor, getattr(model, "trace_inspector_scroll", 0) or 0,
        )

    # ── vim 风格搜索辅助（2026-08-19 用户需求：/ 搜索、n/N 切换、正则） ──
    def _clear_search() -> None:
        """清除搜索状态（无匹配高亮；Esc/关闭视图/切换记录失效时调用）。"""
        model.trace_search_pattern = ""
        model.trace_search_side = ""
        model.trace_search_matches = []
        model.trace_search_idx = -1

    def _search_locate(side: str, target: int) -> None:
        """定位到匹配：台账 → 选中记录（ListView 自动滚动）；检查器 → 光标
        行 + 视口跟随（渲染期协调）。焦点切到匹配所在面板（vim 定位语义）。"""
        if side == "ledger":
            model.trace_selected = target
            model.trace_pane = "ledger"
            model.trace_inspector_scroll = 0
            model.trace_inspector_cursor = 0
        else:
            model.trace_pane = "inspector"
            model.trace_inspector_cursor = target
            model.trace_inspector_scroll = _scroll_for_cursor(
                target, getattr(model, "trace_inspector_scroll", 0) or 0,
            )

    def _search_jump(delta: int) -> None:
        """n/N/p 切换当前匹配（环绕）：delta=1 下一个、-1 上一个。"""
        matches = getattr(model, "trace_search_matches", None) or []
        if not matches:
            return
        idx = getattr(model, "trace_search_idx", -1)
        n = len(matches)
        if idx < 0:
            new_idx = 0 if delta > 0 else n - 1
        else:
            new_idx = (idx + delta) % n
        model.trace_search_idx = new_idx
        side = getattr(model, "trace_search_side", "") or "ledger"
        _search_locate(side, matches[new_idx])

    def _exec_search() -> None:
        """回车执行搜索：当前焦点面板（台账搜记录 / 检查器搜内容行）、
        正则 re.search、所有匹配行高亮；定位到首个匹配。"""
        pattern = (getattr(model, "trace_search_query", "") or "").strip()
        model.trace_search_mode = False
        if not pattern:
            _clear_search()
            return
        side = getattr(model, "trace_pane", "ledger") or "ledger"
        matches = _trace_search_matches(pattern, side, records, content_rows)
        model.trace_search_pattern = pattern
        model.trace_search_side = side
        model.trace_search_matches = matches
        if matches:
            model.trace_search_idx = 0
            _search_locate(side, matches[0])
        else:
            model.trace_search_idx = -1

    def _handle(event) -> bool:
        if not getattr(model, "trace_open", False):
            return False
        pane_now = getattr(model, "trace_pane", "ledger") or "ledger"
        # ★ 2026-08-19（vim 搜索输入模式）："/" 后所有按键进入搜索输入——
        #   字符累积、退格删除、Esc 取消（退出输入模式，保留已执行搜索）、
        #   回车执行（底部输入行消失——「回车后不显示」）。导航/折叠等其余
        #   按键在输入模式不生效（vim 中输入搜索词时同样）。
        if getattr(model, "trace_search_mode", False):
            if event.kind == "escape":
                model.trace_search_mode = False
                return True
            if event.kind == "char":
                ch = getattr(event, "char", "") or ""
                if ch and "\n" not in ch and "\r" not in ch:
                    model.trace_search_query = (
                        getattr(model, "trace_search_query", "") or ""
                    ) + ch
                    return True
                return False
            if event.kind == "backspace":
                q = getattr(model, "trace_search_query", "") or ""
                if q:
                    model.trace_search_query = q[:-1]
                    return True
                return False
            if event.kind == "enter":
                _exec_search()
                return True
            return False
        # 关闭类按键（Esc / Ctrl+H）——subagent 轨迹优先返回主轨迹
        #   （trace_subagent_label 置 None），主轨迹才关闭整个视图。
        #   关闭统一经 trace_open setter（= fullscreen=""，2026-08-17 review
        #   方向：与 toggle 工厂/测试写法一致，避免 property 扩展遗漏联动）。
        #   ★ 2026-08-19（vim 面板浏览）：返回主轨迹同时复位焦点面板/滚动/
        #   光标（残留 pane/scroll/cursor 指向 subagent 轨迹的浏览状态）。
        if event.kind == "escape":
            if getattr(model, "trace_subagent_label", None):
                model.trace_subagent_label = None
                model.trace_selected = -1  # 返回主轨迹：回到尾部跟随
                model.trace_pane = "ledger"
                model.trace_inspector_scroll = 0
                model.trace_inspector_cursor = 0
            else:
                model.trace_open = False
            # ★ 2026-08-19（树控件空格展开/收缩）：退出嵌套/关闭视图同时
            #   复位树折叠集合（折叠状态是「当前选中记录」的临时浏览状态，
            #   与 scroll/cursor 同语义——不跨轨迹残留；默认展开所有）。
            model.trace_tree_collapsed = set()
            # ★ 2026-08-19（vim 搜索）：退出嵌套/关闭视图同时清除搜索
            #   （搜索高亮/匹配不跨视图残留）。
            _clear_search()
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            if getattr(model, "trace_subagent_label", None):
                model.trace_subagent_label = None
                model.trace_selected = -1
                model.trace_pane = "ledger"
                model.trace_inspector_scroll = 0
                model.trace_inspector_cursor = 0
            else:
                model.trace_open = False
            model.trace_tree_collapsed = set()
            _clear_search()
            return True
        # ── 面板切换（vim h/l）与检查器光标（char 单字符） ──
        # ★ 2026-08-19（用户需求：轨迹 Trace 移动到右边查看东西 + vim 风格）：
        #   台账焦点：l → 右移检查器（光标浏览详情）、h 已在最左放行；
        #   检查器焦点：h → 返回台账、l 已在最右放行、j/k/↑↓ 移动光标
        #   （当前行背景高亮，视口跟随）、g/G 顶部/底部、PgUp/PgDn 翻页、
        #   Home/End 首末、← 返回台账。
        ch = getattr(event, "char", "") or ""
        if event.kind == "char" and len(ch) == 1:
            # ★ 2026-08-19（vim 搜索）："/" 开始搜索（任何焦点）——预填上次
            #   pattern 可编辑（vim 语义）；n 下一个 / N、p 上一个（p 为用户
            #   原话 prev 兼容别名）切换匹配并定位。
            if ch == "/":
                model.trace_search_mode = True
                model.trace_search_query = (
                    getattr(model, "trace_search_pattern", "") or ""
                )
                return True
            if ch in ("n", "N", "p") and getattr(model, "trace_search_pattern", ""):
                _search_jump(1 if ch == "n" else -1)
                return True
            if pane_now == "ledger":
                if ch == "l":
                    model.trace_pane = "inspector"
                    return True
                # ch == "h"：已在最左 → 放行（模态吞掉，无副作用）
            else:
                if ch == "h":
                    model.trace_pane = "ledger"
                    return True
                # ch == "l"：已在最右 → 放行（模态吞掉）
                cur_cursor = getattr(model, "trace_inspector_cursor", 0) or 0
                # ★ 2026-08-19（用户需求：树控件按空格可以展开和收缩）：
                #   检查器焦点空格 → 切换光标所在节点的展开/收缩（row_keys
                #   [cursor] = 节点路径 key；叶子/非树行 None 不消费——放行
                #   被模态吞掉）。折叠集合写回 model → 下一帧 use_memo deps
                #   （``_inspector_content_deps`` 含折叠展平）变化 → 内容行
                #   重建（折叠节点子级行消失/恢复）。
                if ch == " ":
                    node_key = (
                        row_keys[cur_cursor]
                        if 0 <= cur_cursor < len(row_keys) else None
                    )
                    if node_key:
                        collapsed_now = set(
                            getattr(model, "trace_tree_collapsed", None) or ()
                        )
                        if node_key in collapsed_now:
                            collapsed_now.discard(node_key)
                        else:
                            collapsed_now.add(node_key)
                        model.trace_tree_collapsed = collapsed_now
                        # ★ 2026-08-19（vim 搜索）：折叠改变内容行结构——
                        #   检查器搜索匹配索引失效，清除搜索（台账搜索不受
                        #   影响）。
                        if getattr(model, "trace_search_side", "") == "inspector":
                            _clear_search()
                        return True
                if ch in ("j", "J"):
                    _move_cursor(cur_cursor + 1)
                    return True
                if ch in ("k", "K"):
                    _move_cursor(cur_cursor - 1)
                    return True
                if ch == "g":
                    _move_cursor(0)
                    return True
                if ch == "G":
                    _move_cursor(total_content)
                    return True
        # ── 检查器焦点：方向键/翻页/首末（ListView focus=False 不消费） ──
        if pane_now == "inspector":
            cur_cursor = getattr(model, "trace_inspector_cursor", 0) or 0
            if event.kind == "arrow_down":
                _move_cursor(cur_cursor + 1)
                return True
            if event.kind == "arrow_up":
                _move_cursor(cur_cursor - 1)
                return True
            if event.kind == "page_down":
                _move_cursor(cur_cursor + max(1, approx_content_vh))
                return True
            if event.kind == "page_up":
                _move_cursor(cur_cursor - max(1, approx_content_vh))
                return True
            if event.kind == "home":
                _move_cursor(0)
                return True
            if event.kind == "end":
                _move_cursor(total_content)
                return True
            if event.kind == "arrow_left":
                model.trace_pane = "ledger"
                return True
        # Enter：选中 subagent 记录 → 进入 subagent 轨迹（嵌套 TraceView——
        #   显示内容与 mainagent 同构）。subagent 轨迹内 Enter 放行（模态：
        #   由 use_fullscreen 吞掉，不落入输入缓冲）；sub-subagent 下钻不
        #   阻断（覆盖 label）。台账与检查器焦点一致（选中记录相同）。
        # ★ 2026-08-17（用户需求：agent 内容合并到 subagent）：合并
        #   后的 subagent 工具记录携带 subagent_label（kind 仍为 tool）
        #   ——下钻条件从 kind=="subagent" 放宽为 subagent_label 非空（独立
        #   subagent 记录与合并 tool 记录均可 Enter 进入 subagent 轨迹）。
        # ★ 2026-08-17（用户需求：轨迹 Trace 工具列表 Enter 进入新界面）：
        #   选中 #0 工具列表记录（kind=="tools"）→ 进入工具列表详情视图
        #   （模态全屏视图 id "trace_tools"——左右布局：左工具名列表上下
        #   选择 + 右树控件显示需要的参数）。主轨迹与 subagent 轨迹均显示
        #   工具列表记录——两处 Enter 均可进入；返回时经 fullscreen="trace"
        #   + trace_subagent_label 保留语义回到原轨迹（subagent 轨迹内进入
        #   后 Esc 仍回 subagent 轨迹，再 Esc 回主轨迹）。选中索引归零
        #   （从首个工具开始浏览），trace_selected 保留（返回时选中记录
        #   不变）。★ 2026-08-19（vim 面板浏览）：进入新轨迹/新视图同时
        #   复位焦点面板/滚动（从台账开始浏览）。
        if event.kind == "enter":
            rec = records[sel] if 0 <= sel < total else None
            if rec is not None:
                sub = getattr(rec, "subagent_label", "") or ""
                if sub:
                    model.trace_subagent_label = sub
                    model.trace_selected = -1  # subagent 轨迹：尾部跟随
                    model.trace_pane = "ledger"
                    model.trace_inspector_scroll = 0
                    model.trace_inspector_cursor = 0
                    # ★ 2026-08-19（树控件空格展开/收缩）：进入 subagent
                    #   轨迹复位树折叠集合（新轨迹树从默认全展开开始）。
                    model.trace_tree_collapsed = set()
                    # ★ 2026-08-19（vim 搜索）：进入 subagent 轨迹清除搜索
                    #   （搜索不跨轨迹残留）。
                    _clear_search()
                    return True
                if getattr(rec, "kind", "") == "tools":
                    model.fullscreen = "trace_tools"
                    model.trace_tools_selected = 0
                    model.trace_tools_pane = "ledger"
                    model.trace_tools_scroll = 0
                    model.trace_tools_cursor = 0
                    model.trace_pane = "ledger"  # 返回主轨迹保持台账
                    model.trace_inspector_scroll = 0
                    model.trace_inspector_cursor = 0
                    # ★ 2026-08-19（树控件空格展开/收缩）：进入工具列表
                    #   视图复位轨迹树折叠集合（浏览状态不跨视图残留）。
                    model.trace_tree_collapsed = set()
                    # ★ 2026-08-19（vim 搜索）：进入工具列表视图清除搜索。
                    _clear_search()
                    return True
        # 其余按键不消费——台账：放行 ListView（j/k/↑↓/PgUp/PgDn/Home/End/
        # g/G 导航）；检查器：未消费按键被 use_fullscreen 模态吞掉（不落入
        # 输入缓冲，杜绝看不见的输入；2026-08-17 通用模态全屏视图机制）
        return False

    use_input(_handle, bool(getattr(model, "trace_open", False)))
    # ★ 模态全屏视图声明（2026-08-17 通用机制）：trace_open 期间未消费按键
    #   被 input router 吞掉（不落入输入缓冲）——字符/Enter 不误编辑/误提交；
    #   关闭后（trace_open=False）hook 不激活零影响，输入区恢复正常输入。
    use_fullscreen(bool(getattr(model, "trace_open", False)))

    def _on_navigate(row_idx: int) -> None:
        """台账导航回调（ListView 导航后）：写回 model.trace_selected（退出跟随）。

        ★ 2026-08-17（用户需求：选最后一行时新行自动选择最新行）：导航到
        **最后一条记录** → 写 -1（尾部跟随语义——渲染期解析为最新记录，
        流式追加/新记录出现自动跟进最新行）；其余位置写具体索引（退出
        跟随、停留在选中记录）。
        ★ 2026-08-19（vim 面板浏览）：切换记录同时复位检查器滚动/光标
        （新记录详情从顶部查看——浏览位置不跨记录残留）。
        """
        rec_idx = _records_index_of_row(rows, row_idx)
        if rec_idx < 0:
            return
        if rec_idx == total - 1:
            model.trace_selected = -1
        else:
            model.trace_selected = rec_idx
        model.trace_inspector_scroll = 0
        model.trace_inspector_cursor = 0
        # ★ 2026-08-19（树控件空格展开/收缩）：切换记录同时复位树折叠
        #   集合（折叠状态是「当前选中记录」的临时浏览状态——不同记录树
        #   不同，从默认全展开开始）。
        model.trace_tree_collapsed = set()
        # ★ 2026-08-19（vim 搜索）：切换记录 → 检查器内容行变化——检查器
        #   搜索匹配索引失效，清除搜索（台账搜索匹配记录索引不受影响）。
        if getattr(model, "trace_search_side", "") == "inspector":
            _clear_search()

    # ── 渲染 ──
    # 头部（静态色——轨迹视图为浏览界面，不呼吸，diff 零输出）
    # ★ 性能（O(N²) 优化）：轮次数 = 分隔行编号表长度（``_rows_index``
    #   预计算 O(1)）——修复前 ``sum(1 for r in rows if r is None)`` 每帧
    #   O(N) 全量扫描（大台账下随渲染帧数累积）。
    sep_nums, _, _ = _rows_index(rows)
    turn_count = len(sep_nums)
    # ★ 2026-08-19（vim 搜索）：头部提示 "/ 搜索 · n/N 下个/上个"——
    #   g/G 为 vim 隐含语义精简掉，保留 Enter 子代理发现性提示。
    if sub_label:
        header_title = f"\u258d子代理轨迹 {sub_label}"
        if pane == "inspector":
            header_hint = ("  jk/\u2191\u2193 \u6eda\u52a8 \u00b7 / \u641c\u7d22 \u00b7 h \u53f0\u8d26 \u00b7 "
                           "Esc \u8fd4\u56de")
        else:
            header_hint = ("  \u2191\u2193/jk \u9009\u62e9 \u00b7 / \u641c\u7d22 \u00b7 l \u8be6\u60c5 \u00b7 "
                           "Esc \u8fd4\u56de")
    else:
        header_title = "\u258d轨迹 Trace"
        if pane == "inspector":
            header_hint = ("  jk/\u2191\u2193 \u6eda\u52a8 \u00b7 / \u641c\u7d22 \u00b7 h \u53f0\u8d26 \u00b7 "
                           "Enter \u5b50\u4ee3\u7406 \u00b7 Esc \u5173\u95ed")
        else:
            header_hint = ("  \u2191\u2193/jk \u9009\u62e9 \u00b7 / \u641c\u7d22 \u00b7 l \u8be6\u60c5 \u00b7 "
                           "Enter \u5b50\u4ee3\u7406 \u00b7 Esc \u5173\u95ed")
    header_runs = [
        StyledRun(header_title, _S_TITLE),
        StyledRun(f" · {total} 条 · {turn_count} 轮", _S_HINT),
        StyledRun(header_hint, _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
    # ★ BEAUTY-36（2026-08-19 美化）：头部行尾 ``─`` 分隔线填充至满宽——
    #   标题区与台账/检查器内容形成清晰视觉分层（对齐 status_bar 分隔线
    #   语义；填充用 _S_SEP_ROW 深灰，低调不抢焦点）。
    if width > 0:
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    # 左栏（台账——ListView 标准控件：受控光标 + 虚拟滚动 + 分隔行跳过）
    # ★ 2026-08-19（vim 面板浏览）：focus 仅在台账焦点时激活（检查器焦点
    #   放行 j/k/↑↓ 等给 TraceView 滚动处理——ListView focus=False 不注册
    #   use_input，事件直达 TraceView _handle）。
    # ★ 2026-08-19（vim 搜索匹配高亮）：renderItem 传搜索匹配记录 id 集合
    #   与当前匹配记录 id——匹配行背景 _S_SEARCH_BG、当前匹配行亮蓝。
    ledger = h(ListView, {
        "items": rows,
        "height": vh,
        "width": left_w,
        "cursor": sel_row if row_count else 0,
        "renderItem": _ledger_renderer(
            rows, left_w, ledger_matched_ids, ledger_cur_id,
        ),
        "onNavigate": _on_navigate,
        "focus": bool(getattr(model, "trace_open", False)) and pane == "ledger",
    })
    # 右栏（检查器——use_memo 包装见上文 `right_rows` 定义处）

    # ── 底部搜索输入行（vim 风格 ``/${query}`` + 光标；回车后不显示） ──
    # ★ 2026-08-19（用户需求：轨迹 Trace vim 搜索——"这里要显示光标回车后
    #   不显示"）：搜索输入模式时在视图底部渲染 ``/${query}`` + 光标块
    #   （▏），台账/检查器高度已减 1 为其让位；回车执行后
    #   ``trace_search_mode=False`` → 本行消失。
    search_line = None
    if search_mode:
        query_text = getattr(model, "trace_search_query", "") or ""
        search_line = h(TEXT, {
            "children": f"/{query_text}\u258f",
            "style": _S_SEARCH_PROMPT,
            "height": 1,
            "key": "tsearch-line",
        })

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_rows),
        ]),
    ]
    if search_line is not None:
        children.append(search_line)
    return h(Column, None, children)


__all__ = [
    "TraceView",
    "_ledger_row_runs",
    "_inspector_children",
    "_inspector_deps",
    "_inspector_content_rows",
    "_inspector_content_deps",
    "_rec_time_seconds",
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
    "_tree_row_wrap",
    "_TREE_CLOSED",
    "_trace_search_matches",
    "_record_search_text",
    "_row_search_text",
    "_S_SEARCH_BG",
    "_S_SEARCH_CUR_BG",
]
