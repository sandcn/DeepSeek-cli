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
import re
import time as _time
from weakref import WeakKeyDictionary

from src.tui._format import format_duration, format_tokens
from src.tui._input_layout import _wrap_by_width
from src.tui.app.trace import (
    block_detail_lines,
    build_subagent_trace_records,
    build_trace_records,
)
from src.tui.app.trace_image import thumbnail_rows as _thumbnail_rows
from src.tui.core.style import Style
from src.tui.ink import (
    TEXT, Column, Row, StyledRun, h, use_effect, use_input, use_memo, use_ref,
)
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.widgets.listview import ListView

from ._inspector_pane import PaneState, handle_nav, resolve, scroll_for_cursor
from ._modal_view import is_modal_close_key, use_modal_scope

# ── 样式（共享定义位于 trace_styles，此处 re-import） ──────
from .trace_styles import (  # noqa: E402
    _S_DIM,
    _S_HINT,
    _S_INDEX,
    _S_INSP_BG,
    _S_SEARCH_BG,
    _S_SEARCH_CUR_BG,
    _S_SEARCH_PROMPT,
    _SEARCH_QUERY_MAX,
    _S_SECTION,
    _S_SEL_BG,
    _S_SEL_MARK,
    _S_SEP_ROW,
    _S_STATUS,
    _S_TEXT,
    _S_TIME,
    _S_TITLE,
    _S_TREE_KEY,
    _S_TREE_VAL,
)

# ── 统计 / 帮助 / 导出（2026-10-07：轨迹 Trace 显示信息 / 操作 / 更多功能） ──
from .trace_export import record_to_text, write_export  # noqa: E402
from .trace_help import help_panel_rows  # noqa: E402
from .trace_stats import (  # noqa: E402
    collect_trace_stats,
    format_summary,
    stats_panel_rows,
)

# ── 树渲染（实现位于 trace_tree，此处 re-import） ──────────
from .trace_tree import (  # noqa: E402
    _TREE_CLOSED,
    _TREE_INDENT,
    _TREE_LEAF,
    _TREE_MAX_DEPTH,
    _TREE_OPEN,
    _args_to_tree,
    _parse_tree_text,
    _tree_node_rows,
    _tree_row_wrap,
    _value_to_tree,
)

#: 参数/返回值小节标题前缀
_SECTION_PREFIX = "\u25b8 "  # ▸

#: 台账行预计算索引缓存（性能：O(N²) 优化——分隔行编号/记录↔行映射/轮次
#:   数一次 O(N) 预计算，跨帧 O(1) 查表；rows 来自 use_memo（内容不变引用
#:   稳定）→ 命中零重建；records 重建 → 新 rows 引用 → 一次性 O(N) 重建，
#:   远低于修复前每帧对每个可见分隔行的累计扫描）。有界防无限增长（超限
#:   清空重建——miss 仅多一次索引构建，无正确性影响）。
_ROWS_INDEX_CACHE_MAX = 4
_rows_index_cache: dict = {}  # id(rows) → (rows_ref, (sep_nums, rec_to_row, row_to_rec))

# ── 台账渲染（实现位于 trace_ledger，此处 re-import） ──────
from .trace_ledger import (  # noqa: E402
    _LEDGER_RUNS_CACHE,
    _LEDGER_RUNS_CACHE_MAX,
    _SEP_RUNS_CACHE,
    _kind_fg,
    _kind_name,
    _ledger_row_runs,
    _rec_time_seconds,
    _record_search_text,
    _row_search_text,
    _row_turn_map,
    _sep_row_runs,
    _status_fg,
    _status_icon,
    _trace_search_matches,
    _viewport_rows,
)

#: 检查器内容行预算下限（标题 + 元信息 + 省略提示占用后至少保留的行数）
_INSPECTOR_MIN_CONTENT = 4
#: 检查器内容行全量生成上限（2026-08-19 用户需求：轨迹 Trace 移动到右边
#:   滚动查看——内容行**全量生成**后按滚动窗口切片；超大内容（如大文件
#:   工具返回）防御性截断，超限追加「内容过长」提示行，滚动到底部可见）
_INSPECTOR_MAX_ROWS = 2000


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


#: 详情行缓存（★ P3 review）：键为 TraceRecord（弱引用，不延长记录生命周期）。
#: 修复前详情行经 ``rec._detail_lines = detail_lines`` 直接写共享记录对象
#: （渲染期副作用 + 跨视图/跨帧串扰）。弱引用字典把该派生缓存与记录对象
#: 绑定但不持有强引用；记录消失即自动清理。
_DETAIL_LINES_CACHE: "WeakKeyDictionary" = WeakKeyDictionary()


def _set_cached_detail_lines(rec, lines) -> None:
    """写入记录详情行缓存（不可弱引用对象退化为实例属性，兜底不抛）。"""
    try:
        _DETAIL_LINES_CACHE[rec] = lines
    except TypeError:
        try:
            rec._detail_lines = lines
        except Exception:
            pass


def _get_cached_detail_lines(rec):
    """读取记录详情行缓存（不存在返回 None）。"""
    try:
        return _DETAIL_LINES_CACHE.get(rec)
    except TypeError:
        return getattr(rec, "_detail_lines", None)


def _args_dep(args, limit: int = 200) -> str:
    """工具参数采样指纹（防超大参数每帧全量 repr——dict/list 只采样前
    ``limit`` 字符量，标量 repr 截断）。

    ★ 2026-08-20（review P2）：修复前 ``repr(args)[:200]`` 对超大 dict
    参数（1MB+）每帧 O(全量) repr——deps/缓存键每帧调用，大参数拖慢帧
    渲染。本函数对 dict/list 按前若干键值/元素采样（累计不超过 limit
    字符，超限截断标记 ``..Nkeys/..Nitems``），标量 repr 超长截断；内容
    变化落在采样区 → 指纹变化触发重建（与全量 repr 同判定语义），采样区
    外变化不触发（接受——缓存键/指纹只需区分主要内容）。纯函数，无状态。
    """
    if args is None:
        return ""
    if isinstance(args, dict):
        out: list = []
        size = 0
        for k, v in args.items():
            seg = f"{k!r}:{_args_dep(v, limit)};"
            if size + len(seg) > limit:
                out.append(f"..{len(args)}keys")
                break
            out.append(seg)
            size += len(seg)
        return "{" + "".join(out)
    if isinstance(args, (list, tuple)):
        out = []
        size = 0
        for v in args:
            seg = f"{_args_dep(v, limit)};"
            if size + len(seg) > limit:
                out.append(f"..{len(args)}items")
                break
            out.append(seg)
            size += len(seg)
        return "[" + "".join(out)
    s = repr(args)
    return s[:limit] if len(s) > limit else s


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

    ★ 2026-08-20（review P2）：``repr(args)[:200]`` 改为 ``_args_dep(args)``
    （采样指纹——超大 dict 参数免每帧全量 repr，见 ``_args_dep`` 注释）。
    """
    if rec is None:
        return (None,)
    if getattr(rec, "kind", "") == "tool":
        args = getattr(rec, "tool_args", None)
        result = str(getattr(rec, "tool_result", "") or "")
        # ★ 多模态工具（read_image 等）：图片指纹并入 deps——若工具返回了
        #   图片，但元信息文本（尺寸/格式/占用）恰好相同，use_memo 也能感知
        #   图片变化而重建缩略图（修复前 deps 不含 images → 缩略图恒不刷新）。
        #   展平为 ";".join（str 原子值——use_memo deps 逐项按值比较，嵌套
        #   tuple 按 is 恒 miss，见 docstring「展平原子值」契约）。
        images = getattr(rec, "images", None) or []
        img_fp = ";".join((img.get("sha", "") or "") for img in images if isinstance(img, dict))
        return ("tool-tree", _args_dep(args), result[:200], len(result), img_fp)
    block = getattr(rec, "source_block", None)
    if block is not None:
        return (id(getattr(block, "lines", None)), len(getattr(block, "lines", None) or []))
    lines = getattr(rec, "lines", None) or []
    images = getattr(rec, "images", None) or []
    # ★ 2026-08-22（review P2-3）：展平为 str 原子值——use_memo deps 逐项按值
    #   比较，嵌套 tuple 按 is 恒 miss（与 trace.py `_messages_fingerprint` 等
    #   契约一致）；修复前非 tool 分支返回嵌套 tuple（每帧重建新对象），带图
    #   user/assistant 记录内容行缓存恒失效、每帧全量重建（抵消近期优化）。
    img_fp = ";".join((img.get("sha", "") or "") for img in images if isinstance(img, dict))
    if lines:
        return (id(lines), getattr(rec, "index", 0), img_fp)
    return (None, img_fp)


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
    # ★ 多模态工具（read_image 等）：图片指纹并入缓存键——图片变化但元信息
    #   文本不变时（_TOOL_TREE_CACHE 键否则命中旧缩略图）仍能重建。
    images = getattr(rec, "images", None) or []
    img_fp = tuple((img.get("sha", "") or "") for img in images if isinstance(img, dict))
    # ★ 2026-08-20（review P2）：缓存键展平原子值——``repr(args)[:200]``
    #   改为 ``_args_dep(args)``（采样指纹，超大参数免每帧全量 repr）；
    #   ``tuple(sorted(collapsed or ()))`` 嵌套 tuple 按 is 引用比较恒 miss
    #   （折叠状态变化/每帧新对象）→ 改为 ``";".join`` 单一 str 原子值。
    key = (_args_dep(args), result[:200], len(result), img_fp, right_w,
           ";".join(sorted(collapsed or ())))
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
    # ── 3. 图片小节（多模态工具返回图片；缩略图作为返回视觉主体优先展示） ──
    #   read_image 等工具的返回值本质上是一张图，元信息文本仅是对图片的说明——
    #   把缩略图独立成「▸ 图片」小节、置于「▸ 返回值」文本之前，避免图片被
    #   埋在一堆元信息文本之后（修复前缩略图被追加到树外、无标题、信息割裂）。
    if images:
        rows.append([StyledRun(f"{_SECTION_PREFIX}图片", _S_SECTION)])
        keys.append(None)
        for img in images:
            for r in _thumbnail_rows(img, right_w):
                rows.append(r)
                keys.append(None)
    # ── 4. 返回值小节（树控件显示返回值） ──
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
    lines = _get_cached_detail_lines(rec)
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
        # ★ P1（review 2026-08-22）：合并 subagent 的 tool 记录
        #   （``subagent_label`` 非空）——``_tool_tree_rows`` 只渲染参数树/
        #   返回值树，忽略 ``rec.lines`` 中 ``_merge_subagent_into_tool_record``
        #   写入的 subagent 详情（结果/错误/工具历史），docstring 声明的
        #   「检查器完整表达两次动作」未兑现（仅留台账行摘要）。此处追加
        #   markdown 渲染 ``rec.lines``，保证检查器完整表达。
        if getattr(rec, "subagent_label", ""):
            sub_rows = list(_md_detail_rows(rec, right_w, "content"))
            if sub_rows:
                rows.extend(sub_rows)
                keys.extend([None] * len(sub_rows))
    elif kind in ("reasoning", "content", "system"):
        # markdown 渲染行（块记录直接复用渲染输出 / 内联原始文本重渲染）
        rows = list(_md_detail_rows(rec, right_w, kind))
        keys = [None] * len(rows)
    else:
        lines = _get_cached_detail_lines(rec)
        if lines is None:
            # 直接调用（测试/外部使用）未挂载惰性详情时回退记录内联 lines
            lines = getattr(rec, "lines", None) or []
        for line in lines:
            if not isinstance(line, str):
                line = str(line)
            rows.extend(_wrap_by_width(line, right_w))
        keys = [None] * len(rows)
    # ── 多模态图片缩略图（非工具树分支追加渲染；右栏宽驱动尺寸） ──
    #   tool 树分支（use_tool_tree=True）已在 _tool_tree_rows 内联「▸ 图片」
    #   小节渲染缩略图；此处只覆盖纯文本/markdown 分支（user/assistant 消息
    #   带图、tool 无参数/无返回但带图等），避免重复追加。
    if not use_tool_tree:
        images = getattr(rec, "images", None) or []
        if images:
            for img in images:
                for r in _thumbnail_rows(img, right_w):
                    rows.append(r)
                    keys.append(None)
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

    ★ 2026-08-20（review P1 修复）：折叠集合由 ``tuple(sorted(...))``
    嵌套 tuple 改为 ``";".join(sorted(...))`` 单一 str 原子值——``_object_is``
    对 tuple 仅按 is 引用比较（int/float/str 才按值），嵌套 tuple 每帧新建
    对象 → use_memo 恒 miss → 每帧全量重建检查器内容行（纯文本记录每帧
    全量换行、md 行每帧 hash、树行每帧 repr）。str 不可变按值比较，跨帧
    同折叠状态命中缓存（与 trace.py ``_messages_fingerprint`` 等「展平
    原子值」契约一致）。
    """
    if rec is None:
        return (None, right_w, "")
    return tuple(_detail_deps(rec)) + (right_w, ";".join(sorted(collapsed or ())))


def _truncate_text(text: str, width: int) -> str:
    """按显示宽度截断文本（超宽尾部追加 ``…``；异常回退原样）。

    用于检查器 meta 行（TEXT 单行，必须自行截断保证行宽不变量）。
    """
    if width <= 0:
        return text
    try:
        from src.tui._screen import wcswidth_simple

        if wcswidth_simple(text) <= width:
            return text
        out: list = []
        used = 0
        budget = max(0, width - 1)
        for ch in text:
            w = wcswidth_simple(ch)
            if used + w > budget:
                break
            out.append(ch)
            used += w
        return "".join(out) + "\u2026"
    except Exception:
        return text


def _line_count(text: str) -> int:
    """文本行数（非空时 ``\\n`` 数 + 1；空串 0）。"""
    if not text:
        return 0
    return text.count("\n") + 1


def _format_clock(ts) -> str:
    """epoch 时间戳 → ``HH:MM:SS``（本地时区；非法值回退空串）。"""
    try:
        return _time.strftime("%H:%M:%S", _time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _meta_parts(rec, content_total: int = 0) -> list:
    """检查器 meta 行分段（信息存在才输出；单行由调用方 join + 截断）。

    ★ 2026-10-07（轨迹 Trace 元信息增强）：在原「耗时 · 输入 · 输出」基础上
    补充——状态、工具名、调用 ID、起始时间（epoch 基准才显示）、token 明细
    （缓存/实时输出）、参数与返回行数、内容行数。``content_total`` 仅在已有
    信息时作为附加项（不作为 meta 存在的唯一条件——保持 ``_inspector_fixed_rows``
    预算口径与渲染一致）。
    """
    parts: list = []
    if rec is None:
        return parts
    ts = _rec_time_seconds(rec)
    if ts is not None:
        parts.append(f"耗时 {format_duration(ts)}")
    tokens = getattr(rec, "tokens", None) or {}
    if isinstance(tokens, dict) and tokens:
        seg = (
            f"输入 {format_tokens(_safe_int(tokens.get('input', 0) or 0))} "
            f"输出 {format_tokens(_safe_int(tokens.get('output', 0) or 0))}"
        )
        cache = _safe_int(tokens.get("cache", 0) or 0)
        if cache:
            seg += f" 缓存 {format_tokens(cache)}"
        live_out = _safe_int(tokens.get("live_output", 0) or 0)
        if live_out:
            seg += f" 实时\u2193{format_tokens(live_out)}"
        parts.append(seg)
    name = (getattr(rec, "tool_name", "") or "").strip()
    if name:
        parts.append(f"工具 {name}")
    cid = (getattr(rec, "tool_call_id", "") or "").strip()
    if cid:
        parts.append(f"ID {cid[:20]}")
    started = getattr(rec, "time_started", None)
    if started is not None and not getattr(rec, "time_started_monotonic", True):
        stamp = _format_clock(started)
        if stamp:
            parts.append(f"开始 {stamp}")
    status = (getattr(rec, "status", "") or "").strip()
    if status:
        parts.append(f"状态 {status}")
    if (getattr(rec, "kind", "") or "") == "tool":
        args = getattr(rec, "tool_args", None)
        if args is not None and str(args) != "":
            parts.append(f"参数 {_line_count(str(args))} 行")
        result = getattr(rec, "tool_result", "") or ""
        if result:
            parts.append(f"返回 {_line_count(result)} 行")
    if parts and content_total:
        parts.append(f"内容 {content_total} 行")
    return parts


def _inspector_fixed_rows(rec) -> int:
    """检查器非内容区固定占用行数（★ P1 review：单一真源）。

    标题 2 行（标题 + 分隔/提示占位）+ meta 行（``_meta_parts`` 非空时 1 行）
    + subagent 提示行（``subagent_label`` 非空时 1 行）。

    ``_inspector_children``（窗口预算）与 TraceView（滚动协调）共用本函数，
    消除此前「精确 ``content_vh = vh - fixed``」与「近似
    ``approx_content_vh = vh - 3``」两套预算不一致（fixed=4 时光标可落到
    窗口外，光标行高亮消失、j/k 视口跟随在边界失效）。

    ★ 2026-10-07（元信息增强）：meta 触发条件与 ``_meta_parts(rec, 0)``
    单一真源（工具名/调用 ID 等新字段同样计入）——预算与渲染恒一致。
    """
    fixed = 2
    if rec is not None:
        if _meta_parts(rec, 0):
            fixed += 1
        if getattr(rec, "subagent_label", ""):
            fixed += 1
    return fixed


def _inspector_viewport_rows(rec, vh: int) -> int:
    """检查器内容区可用行数（单一真源，见 ``_inspector_fixed_rows``）。"""
    return max(_INSPECTOR_MIN_CONTENT, vh - _inspector_fixed_rows(rec))


def _pane_window_children(
    pane_rows, right_w: int, vh: int, scroll: int = 0, cursor: int = -1,
    key_prefix: str = "tpane", empty_text: str = "(无内容)",
) -> list:
    """通用滚动面板内容元素（帮助 / 统计面板；与检查器同一滚动语义）。

    ★ 2026-10-07（轨迹 Trace 显示信息 / 更多功能）：帮助面板与统计面板
    全量内容行经本函数切窗口渲染——scroll 偏移、光标行背景高亮
    （``_S_INSP_BG``，vim cursorline 语义）、顶/底省略提示、空态提示。
    每行 TEXT 带唯一 key（``{key_prefix}-*``）防 fiber 共享环。

    Args:
        pane_rows: 全量内容行（``list[list[StyledRun]]``）。
        right_w: 右栏宽（截断预算；<=0 防御）。
        vh: 视口行数预算。
        scroll: 滚动偏移（越界钳制）。
        cursor: 光标行（-1 = 不高亮）。
        key_prefix: 元素 key 前缀（帮助/统计各一，防跨面板 key 冲突）。
        empty_text: 空内容提示文本。
    """
    rows = list(pane_rows or [])
    total = len(rows)
    # 内容区预算（顶部/底部提示占位 2 行——与检查器同口径）
    content_vh = max(_INSPECTOR_MIN_CONTENT, int(vh) - 2)
    try:
        scroll = int(scroll or 0)
    except (TypeError, ValueError, OverflowError):
        scroll = 0
    try:
        cursor = int(cursor) if cursor is not None else -1
    except (TypeError, ValueError, OverflowError):
        cursor = -1
    if total > 0:
        cursor = max(0, min(cursor, total - 1))
    else:
        cursor = -1
    if total > content_vh:
        scroll = max(0, min(scroll, total - content_vh))
    else:
        scroll = 0
    children: list = []
    if scroll > 0:
        children.append(h(TEXT, {
            "children": f"\u2026 前 {scroll} 行省略", "style": _S_HINT,
            "height": 1, "key": f"{key_prefix}-top",
        }))
    window = rows[scroll:scroll + content_vh]
    if scroll + len(window) < total:
        if cursor >= 0 and cursor == scroll + len(window) - 1:
            bottom_omitted = total - scroll - len(window)
        else:
            window = window[:max(0, len(window) - 1)]
            bottom_omitted = total - scroll - len(window)
    else:
        bottom_omitted = 0
    for i, seg in enumerate(window):
        abs_idx = scroll + i
        runs = seg if isinstance(seg, list) else [StyledRun(str(seg), _S_TEXT)]
        if cursor >= 0 and abs_idx == cursor:
            runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG))
                for r in runs
            ]
        children.append(h(TEXT, {
            "children": ("".join(r.text for r in runs) if runs else "") or " ",
            "styled": runs if runs else [StyledRun(" ", None)],
            "height": 1,
            "key": f"{key_prefix}-{len(children)}",
        }))
    if bottom_omitted:
        children.append(h(TEXT, {
            "children": f"\u2026 后 {bottom_omitted} 行省略", "style": _S_HINT,
            "height": 1, "key": f"{key_prefix}-omitted",
        }))
    if total == 0:
        children.append(h(TEXT, {
            "children": empty_text, "style": _S_HINT, "height": 1,
            "key": f"{key_prefix}-empty",
        }))
    return children


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
    title = f"#{getattr(rec, 'index', 0)} {_kind_name(kind)}"
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _status_icon(status)
        title = f"{title} {sicon} {status}"
    children.append(h(TEXT, {
        "children": title, "style": _S_TITLE, "height": 1, "key": "tinsp-title",
    }))
    # ── 内容行（全量生成 → 滚动窗口切片；光标行高亮；省略提示两侧） ──
    # ★ 2026-10-07（元信息增强）：内容行提前解析——meta 行需要内容行数
    #   （「内容 N 行」）；解析结果供下方滚动窗口复用（无重复生成）。
    if content_rows is None:
        content_rows, row_keys = _inspector_content_rows(rec, right_w, collapsed)
    total = len(content_rows)
    # 元信息（耗时 / 状态 / 工具 / 调用 ID / 起始时间 / token 明细 /
    #   参数与返回行数 / 内容行数）
    # ★ 2026-08-19（用户需求：轨迹 Trace 正运行的工具耗时没有刷新）：耗时
    #   经 ``_rec_time_seconds`` 实时计算（运行中记录按起始时间戳走动——
    #   工具无输出/records 不重建期间 meta 行每秒刷新）。
    # ★ 2026-10-07（轨迹 Trace 元信息增强）：单行信息密度提升（工具名/调用
    #   ID/起始时间/token 明细/参数与返回行数/内容行数），按栏宽截断。
    meta = _meta_parts(rec, total)
    if meta:
        meta_text = _truncate_text(" \u00b7 ".join(meta), right_w)
        children.append(h(TEXT, {
            "children": meta_text, "style": _S_DIM, "height": 1,
            "key": "tinsp-meta",
        }))
    try:
        scroll = int(scroll) or 0
    except (TypeError, ValueError, OverflowError):
        scroll = 0
    try:
        cursor = int(cursor) if cursor is not None else -1
    except (TypeError, ValueError, OverflowError):
        cursor = -1
    # 内容区行数预算（标题/meta/省略提示/subagent 提示占位后）
    # ★ P1（review）：预算经 ``_inspector_viewport_rows`` 与 TraceView 滚动
    #   协调共用（单一真源），修复前此处为 ``vh - fixed`` 而调用方用
    #   ``vh - 3`` 近似，二者不一致时光标行落到窗口外。
    content_vh = _inspector_viewport_rows(rec, vh)
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
    # ★ P1（review）：窗口收缩时**优先保留光标行**——修复前无条件
    #   ``window[:-1]`` 为底部省略提示让位，光标恰在窗口末行时该行被删除
    #   （光标行背景高亮消失、j/k 视口跟随边界失效）。光标在末行时保留
    #   光标行、改为不显示底部省略提示（信息性提示让位于光标可见性）。
    if scroll + len(window) < total:
        if cursor >= 0 and cursor == scroll + len(window) - 1:
            bottom_omitted = total - scroll - len(window)
        else:
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
    messages = getattr(slot, "messages", None)
    if messages is None:
        messages = ()
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
        msg_fp = (len(messages), id(messages))
    else:
        # ★ P2（review 2026-08-22）：messages 为空/None 时 getattr(...) or []
        #   每次新建 [] 导致 id 每帧变化 → use_memo 恒 miss → subagent 轨迹
        #   每帧重建。空态指纹恒定 (0, 0)；非空时以 (len, id) 捕捉内容变化。
        msg_fp = (0, 0)
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
    # ★ 2026-10-07（台账行增强）：记录行 → 轮次号映射（O(1) 查表显示 tN）。
    turn_map = _row_turn_map(rows)

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
                turn_map.get(idx, 0),
            ),
            "height": 1,
        })
    return render_item


# ═══════════════════════════════════════════════════════════
# vim 搜索辅助 / 输入事件处理（模块级；P1-1 巨型组件拆分）
# ═══════════════════════════════════════════════════════════
# 说明：以下逻辑原为 ``TraceView`` 内部闭包（组件函数 ~640 行）。提取为模块级
# 函数后按显式参数传递上下文（model / records / content_rows / ...），主组件
# 只负责状态读取 + hooks 接线 + 渲染组装。


# ═══════════════════════════════════════════════════════════
# 过滤视图 / 记录定位 / 面板动作（2026-10-07 轨迹 Trace 增强）
# ═══════════════════════════════════════════════════════════


def _filter_view(records: list, matches: list) -> tuple:
    """过滤视图：台账只保留搜索匹配的记录（无轮次分隔行）。

    ★ 2026-10-07（轨迹 Trace 搜索增强——过滤模式 ``f``）：``matches`` 为
    台账搜索匹配的记录索引（原始列表）——构造子集 records/rows 与
    「原始索引 → 视图索引」映射（``view_map``），供选择定位/下钻/统计
    统一基于子集。

    Returns:
        (view_records, view_rows, view_map)。
    """
    keep = [i for i in matches if 0 <= i < len(records)]
    view_records = [records[i] for i in keep]
    view_map = {orig: pos for pos, orig in enumerate(keep)}
    return view_records, list(view_records), view_map


def _record_pos_by_number(records: list, number: int) -> int:
    """记录号 ``#N`` → 视图位置（找不到 -1）。"""
    for i, rec in enumerate(records or []):
        if rec is not None and getattr(rec, "index", -1) == number:
            return i
    return -1


def _find_record_pos(records: list, start: int, delta: int, predicate) -> int:
    """从 ``start`` 起按 ``delta`` 方向找首个满足 ``predicate`` 的记录位置。

    不环绕（到边界即失败返回 -1）——符合「下一个错误/下一个工具」的
    单向推进直觉（``e``/``]`` 到底后提示「无更多」而非跳回开头）。
    """
    n = len(records or [])
    i = int(start) + int(delta)
    while 0 <= i < n:
        rec = records[i]
        if rec is not None and predicate(rec):
            return i
        i += int(delta)
    return -1


def _is_error_record(rec) -> bool:
    """记录是否失败（status ∈ fail/error）。"""
    return (getattr(rec, "status", "") or "").lower() in ("fail", "error")


def _is_tool_record(rec) -> bool:
    """记录是否工具调用。"""
    return (getattr(rec, "kind", "") or "") == "tool"


def _set_status(model, message: str) -> None:
    """写轨迹视图底部状态提示（空串清除）。"""
    model.trace_status_message = message or ""


def _status_line_text(model, filtered: bool = False) -> str:
    """底部状态行文本（搜索匹配计数 + 操作反馈 + 面板提示）。

    ★ 2026-10-07（轨迹 Trace 搜索/操作增强）：有搜索时显示
    ``/pattern  n/m [Aa] [过滤]``（匹配计数——用户要求「底部显示匹配 n/m
    计数」）；操作反馈（导出/复制/无匹配等）经 ``trace_status_message``
    追加；帮助/统计面板打开时提示关闭方式。
    """
    parts: list = []
    if getattr(model, "trace_help_open", False):
        parts.append("帮助面板：? / q / Esc 关闭")
    elif getattr(model, "trace_stats_open", False):
        parts.append("统计面板：i 关闭")
    pattern = getattr(model, "trace_search_pattern", "") or ""
    if pattern:
        matches = getattr(model, "trace_search_matches", None) or []
        n = len(matches)
        idx = getattr(model, "trace_search_idx", -1)
        cur = (idx + 1) if 0 <= idx < n else 0
        seg = f"/{pattern}  {cur}/{n}"
        if getattr(model, "trace_search_case", False):
            seg += " Aa"
        if filtered:
            seg += " [过滤]"
        parts.append(seg)
    message = getattr(model, "trace_status_message", "") or ""
    if message:
        parts.append(message)
    return "  \u00b7  ".join(parts)


def _reset_browse_state(model) -> None:
    """切换选中记录后复位浏览态（检查器滚动/光标/树折叠）。"""
    model.trace_inspector_scroll = 0
    model.trace_inspector_cursor = 0
    model.trace_tree_collapsed = set()


def _select_record(model, pos: int) -> None:
    """选中视图位置 ``pos``（-1 = 尾部跟随）并复位浏览态。"""
    model.trace_selected = pos
    _reset_browse_state(model)


def _collapse_all(model, row_keys, fold: bool) -> None:
    """``zR``/``zM``：全部展开（fold=False）/ 全部折叠（fold=True）。

    折叠集合元素 = 当前内容行的可折叠节点路径 key（``row_keys`` 非 None 项
    ——树节点的真源）；检查器搜索在内容结构变化后失效（与空格折叠同语义）。
    """
    if fold:
        keys = {k for k in (row_keys or []) if k}
        model.trace_tree_collapsed = keys
        if not keys:
            _set_status(model, "当前记录无可折叠节点")
            return
    else:
        model.trace_tree_collapsed = set()
    if getattr(model, "trace_search_side", "") == "inspector":
        _clear_search(model)


def _do_copy(model, rec) -> None:
    """``y``：复制当前记录内容到剪贴板（OSC52）。"""
    if rec is None:
        _set_status(model, "无可复制的记录")
        return
    from src.tui._screen import set_clipboard

    text = record_to_text(rec)
    ok = set_clipboard(text)
    if ok:
        _set_status(
            model,
            f"已复制 #{getattr(rec, 'index', 0)} 记录（{len(text)} 字符）到剪贴板",
        )
    else:
        _set_status(model, "复制失败：无可用终端输出")


def _toggle_filter(model, records) -> None:
    """``f``：切换过滤模式（台账只显示搜索匹配记录）。

    仅在「台账搜索 + 有匹配」时开启（否则提示并保持关闭——避免空台账
    无从导航）；关闭时回到全量显示（尾部跟随）。
    """
    new_value = not bool(getattr(model, "trace_search_filter", False))
    side = getattr(model, "trace_search_side", "") or ""
    pattern = getattr(model, "trace_search_pattern", "") or ""
    matches = list(getattr(model, "trace_search_matches", None) or [])
    if new_value and not (side == "ledger" and pattern and matches):
        model.trace_search_filter = False
        _set_status(model, "过滤需先进行台账搜索且有匹配（/ 搜索）")
        return
    model.trace_search_filter = new_value
    if new_value:
        _select_record(model, 0)
        _set_status(model, f"过滤开启：仅显示 {len(matches)} 条匹配")
    else:
        _select_record(model, -1)
        _set_status(model, "过滤关闭：显示全部记录")


def _toggle_search_case(model, records, content_rows, total_content: int,
                        approx_content_vh: int, view_map) -> None:
    """``v``：切换搜索大小写敏感并重跑当前搜索（无搜索时仅切换开关）。"""
    model.trace_search_case = not bool(getattr(model, "trace_search_case", False))
    pattern = getattr(model, "trace_search_pattern", "") or ""
    label = "敏感" if model.trace_search_case else "不敏感"
    if not pattern:
        _set_status(model, f"搜索大小写：{label}")
        return
    side = getattr(model, "trace_search_side", "") or "ledger"
    matches = _trace_search_matches(
        pattern, side, records, content_rows,
        case_sensitive=model.trace_search_case,
    )
    model.trace_search_matches = matches
    if matches:
        model.trace_search_idx = 0
        _search_locate(
            model, side, matches[0], total_content, approx_content_vh, view_map,
        )
    else:
        model.trace_search_idx = -1
    _set_status(model, f"搜索大小写{label}：{len(matches)} 处匹配")


def _do_export(model, records, fmt: str, source: str) -> None:
    """``w``/``W``：导出当前轨迹为 Markdown / JSON 文件。"""
    try:
        path = write_export(records, fmt, source)
    except Exception as exc:  # 写盘失败（权限/磁盘）→ 状态提示，不崩溃
        _set_status(model, f"导出失败：{exc}")
        return
    count = sum(1 for rec in (records or []) if rec is not None)
    label = "Markdown" if fmt == "md" else "JSON"
    _set_status(model, f"已导出 {count} 条记录（{label}）→ {path}")


def _clear_search(model) -> None:
    """清除搜索状态（无匹配高亮；Esc/关闭视图/切换记录失效时调用）。"""
    model.trace_search_pattern = ""
    model.trace_search_side = ""
    model.trace_search_matches = []
    model.trace_search_idx = -1


def _search_locate(model, side: str, target: int,
                   total_content: int, approx_content_vh: int,
                   view_map: dict | None = None) -> None:
    """定位到匹配：台账 → 选中记录（ListView 自动滚动）；检查器 → 光标行 +
    视口跟随（渲染期协调）。焦点切到匹配所在面板（vim 定位语义）。

    ★ 2026-10-07（过滤模式）：``view_map`` 为「原始记录索引 → 过滤视图索引」
    映射（未过滤时 None）——台账定位把匹配的原始索引转换为视图索引后写回
    ``trace_selected``（过滤视图下选择基于子集）。
    """
    if side == "ledger":
        model.trace_selected = (
            view_map.get(target, target) if view_map else target
        )
        model.trace_pane = "ledger"
        model.trace_inspector_scroll = 0
        model.trace_inspector_cursor = 0
        model.trace_tree_collapsed = set()
    else:
        model.trace_pane = "inspector"
        model.trace_inspector_cursor = target
        model.trace_inspector_scroll = scroll_for_cursor(
            target, getattr(model, "trace_inspector_scroll", 0) or 0,
            total_content, approx_content_vh,
        )


def _search_jump(model, delta: int,
                 total_content: int, approx_content_vh: int,
                 view_map: dict | None = None) -> None:
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
    _search_locate(
        model, side, matches[new_idx], total_content, approx_content_vh, view_map,
    )


def _exec_search(model, records, content_rows,
                 total_content: int, approx_content_vh: int,
                 view_map: dict | None = None) -> None:
    """回车执行搜索：当前焦点面板（台账搜记录 / 检查器搜内容行）、
    正则 re.search、所有匹配行高亮；定位到首个匹配。

    ★ 2026-08-20（review P3）：不再 ``strip()``——首尾空格是正则 pattern 的
    一部分（vim 语义：搜索含首尾空格的 pattern 合法，纯空格 pattern 即搜索
    空格）；清除搜索 = 空串直接回车（``if not pattern``）。
    """
    pattern = getattr(model, "trace_search_query", "") or ""
    model.trace_search_mode = False
    if not pattern:
        _clear_search(model)
        return
    side = getattr(model, "trace_pane", "ledger") or "ledger"
    matches = _trace_search_matches(
        pattern, side, records, content_rows,
        case_sensitive=bool(getattr(model, "trace_search_case", False)),
    )
    model.trace_search_pattern = pattern
    model.trace_search_side = side
    model.trace_search_matches = matches
    if matches:
        model.trace_search_idx = 0
        _search_locate(
            model, side, matches[0], total_content, approx_content_vh, view_map,
        )
    else:
        model.trace_search_idx = -1
        if pattern:
            _set_status(model, f"无匹配：{pattern}")


def _handle_trace_event(
    model, records, content_rows, row_keys, sel, *,
    total: int, total_content: int, approx_content_vh: int,
    pane_state, event,
    raw_records=None, view_map=None, source: str = "",
    pane_total: int = 0, pane_vh: int = 0,
) -> bool:
    """TraceView 输入事件处理（模块级；P1-1 拆分自组件内闭包 ``_handle``）。

    ★ 2026-10-07（轨迹 Trace 增强）新增参数：
      - ``raw_records``：未过滤的完整记录列表（搜索/导出用；None → 同
        ``records``）——过滤模式下 ``records`` 只是匹配子集，重新搜索仍
        基于全量；
      - ``view_map``：原始记录索引 → 过滤视图索引映射（未过滤 None）；
      - ``source``：轨迹来源标签（导出元信息；主轨迹 "" / 子代理 label）；
      - ``pane_total`` / ``pane_vh``：帮助 / 统计面板行数与视口（>0 表示
        右栏当前显示面板——滚动导航以面板行数为准，搜索输入被禁用）。
    """
    if not getattr(model, "trace_open", False):
        return False
    pane_now = getattr(model, "trace_pane", "ledger") or "ledger"
    search_records = raw_records if raw_records is not None else records
    # ★ 2026-10-07（轨迹 Trace 操作增强）：选中位置以 ``model.trace_selected``
    #   实时解析（handler 闭包捕获的 ``sel`` 是渲染期快照——同一帧内连续
    #   按键时陈旧，e/E、]/[、y、Enter 均须基于实时选中）。
    raw_sel = getattr(model, "trace_selected", -1)
    if total <= 0:
        sel_pos = -1
    elif raw_sel == -1 or raw_sel >= total:
        sel_pos = total - 1
    else:
        sel_pos = raw_sel
    # ★ 2026-10-07（帮助 / 统计面板）：右栏内容切换为面板时，滚动/光标
    #   以面板行数为准（面板与检查器行数不同——沿用检查器行数会误判越界）。
    if pane_total > 0:
        total_content = int(pane_total)
        if pane_vh > 0:
            approx_content_vh = int(pane_vh)
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
                # ★ 2026-08-20（review P3）：query 长度上限——超长输入
                #   截断丢弃（渲染行按栏宽截断，无上限累积只浪费内存）。
                q = getattr(model, "trace_search_query", "") or ""
                if len(q) < _SEARCH_QUERY_MAX:
                    model.trace_search_query = q + ch
                return True
            # 含换行 char（多行粘贴）不入 query——吞掉（vim 搜索输入
            #   模式不接受换行）。
            return True
        if event.kind == "backspace":
            q = getattr(model, "trace_search_query", "") or ""
            if q:
                model.trace_search_query = q[:-1]
            return True
        if event.kind == "enter":
            _exec_search(
                model, search_records, content_rows, total_content,
                approx_content_vh, view_map,
            )
            return True
        # ★ 2026-08-20（review P3）：搜索输入模式未识别事件返回 True
        #   （模态吞掉）——修复前 return False 放行：台账焦点时 ListView
        #   仍激活消费方向键/翻页等 → 搜索输入中按 ↑↓ 意外导航台账
        #   （vim 中搜索输入模式不导航）。
        return True
    # ★ 2026-10-07（视图内帮助面板）：打开期间模态——``?``/``q``/``Esc``
    #   关闭，其余按键吞掉（不落入台账导航/搜索，避免误操作）。
    if getattr(model, "trace_help_open", False):
        ch_help = getattr(event, "char", "") or ""
        if event.kind == "escape" or (
            event.kind == "char" and ch_help in ("?", "q")
        ):
            model.trace_help_open = False
            _set_status(model, "")
            return True
        # 帮助内容可超一屏：交给通用导航（j/k/↑↓/PgUp/PgDn/g/G）滚动；
        # 面板打开时禁用搜索输入（索引语义属于台账/检查器内容）。
        if event.kind == "char" and ch_help == "/":
            _set_status(model, "帮助面板打开时不支持搜索（? 关闭后可用）")
            return True
        if handle_nav(event, pane_state, total_content, approx_content_vh):
            return True
        return True
    # 关闭类按键（Esc / Ctrl+H，模态统一关闭键）——subagent 轨迹优先返回
    #   主轨迹（trace_subagent_label 置 None），主轨迹才关闭整个视图。
    #   ★ 2026-08-19（vim 面板浏览）：返回主轨迹同时复位焦点面板/滚动/
    #   光标（残留 pane/scroll/cursor 指向 subagent 轨迹的浏览状态）。
    if is_modal_close_key(event):
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
        # ★ 2026-10-07（轨迹 Trace 增强）：退出/关闭同时复位增强浏览态
        #   （帮助/统计面板、多键前缀、过滤模式、底部状态提示——与
        #   scroll/cursor 同语义，不跨视图残留）。
        model.trace_help_open = False
        model.trace_stats_open = False
        model.trace_count_buffer = ""
        model.trace_pending_prefix = ""
        model.trace_search_filter = False
        model.trace_status_message = ""
        # ★ 2026-08-19（vim 搜索）：退出嵌套/关闭视图同时清除搜索
        #   （搜索高亮/匹配不跨视图残留）。
        _clear_search(model)
        return True
    # ★ 2026-10-07（轨迹 Trace 操作增强）：``Ctrl+D`` / ``Ctrl+U`` 半屏翻页
    #   ——检查器焦点移动内容光标、台账焦点移动选中记录（步长 = 半屏）。
    #   轨迹视图内 router 优先消费（不被 InputDispatcher 的 Ctrl+D=EOF
    #   旧路径截走）。
    if event.kind == "ctrl_key":
        ctrl_ch = getattr(event, "char", "") or ""
        if ctrl_ch in ("\x04", "\x15"):
            half = max(1, int(approx_content_vh) // 2)
            delta = half if ctrl_ch == "\x04" else -half
            if pane_now == "inspector":
                pane_state.move_cursor(
                    pane_state.cursor() + delta, total_content, approx_content_vh,
                )
            elif total > 0:
                base = sel if 0 <= sel < total else 0
                _select_record(model, max(0, min(total - 1, base + delta)))
            _set_status(model, "")
            return True
    # ── 面板切换（vim h/l）与检查器光标（char 单字符） ──
    # ★ 2026-08-19（用户需求：轨迹 Trace 移动到右边查看东西 + vim 风格）：
    #   台账焦点：l → 右移检查器（光标浏览详情）、h 已在最左放行；
    #   检查器焦点：h → 返回台账、l 已在最右放行、j/k/↑↓ 移动光标
    #   （当前行背景高亮，视口跟随）、g/G 顶部/底部、PgUp/PgDn 翻页、
    #   Home/End 首末、← 返回台账。
    ch = getattr(event, "char", "") or ""
    if event.kind == "char" and len(ch) == 1:
        # ── vim 多键前缀（数字计数 Ngg/NG · zR/zM 树全展开/全折叠） ──
        #   ★ 2026-10-07（轨迹 Trace 操作增强）：数字键累积到
        #   ``trace_count_buffer``（最多 9 位），``g``/``G`` 消费为「跳到
        #   记录号 #N」；``z`` 为待定前缀，下一键 R/M 触发全展开/全折叠
        #   （非预期键则清除前缀并按普通键继续处理）。
        prefix = getattr(model, "trace_pending_prefix", "") or ""
        count_buf = getattr(model, "trace_count_buffer", "") or ""
        if ch.isdigit():
            model.trace_count_buffer = (count_buf + ch)[:9]
            _set_status(model, f"计数 {model.trace_count_buffer}（g/G 跳转记录号）")
            return True
        if prefix == "z":
            model.trace_pending_prefix = ""
            if ch == "R":
                _collapse_all(model, row_keys, False)
                _set_status(model, "已全部展开")
                return True
            if ch == "M":
                _collapse_all(model, row_keys, True)
                _set_status(model, "已全部折叠")
                return True
        elif ch == "z":
            model.trace_pending_prefix = "z"
            _set_status(model, "z…（R 全展开 / M 全折叠）")
            return True
        if count_buf:
            model.trace_count_buffer = ""
            if ch in ("g", "G"):
                try:
                    number = int(count_buf)
                except (TypeError, ValueError):
                    number = -1
                pos = _record_pos_by_number(records, number)
                if pos >= 0:
                    _select_record(model, pos)
                    _set_status(model, f"\u2192 记录 #{number}")
                else:
                    _set_status(model, f"无记录 #{number}")
                return True
        # ── 视图内面板 / 记录定位 / 剪贴板 / 导出（2026-10-07 增强） ──
        if ch == "?":
            model.trace_help_open = not bool(getattr(model, "trace_help_open", False))
            _set_status(model, "")
            return True
        if ch == "i":
            model.trace_stats_open = not bool(getattr(model, "trace_stats_open", False))
            _reset_browse_state(model)
            _set_status(model, "")
            return True
        if ch == "y":
            rec_now = records[sel_pos] if 0 <= sel_pos < len(records) else None
            _do_copy(model, rec_now)
            return True
        if ch in ("w", "W"):
            _do_export(
                model, search_records, "md" if ch == "w" else "json", source,
            )
            return True
        if ch == "f":
            _toggle_filter(model, records)
            return True
        if ch == "v":
            _toggle_search_case(
                model, search_records, content_rows, total_content,
                approx_content_vh, view_map,
            )
            return True
        if ch in ("e", "E"):
            pos = _find_record_pos(
                records, sel_pos, 1 if ch == "e" else -1, _is_error_record,
            )
            if pos >= 0:
                _select_record(model, pos)
                _set_status(
                    model, f"\u2192 #{getattr(records[pos], 'index', 0)} 失败记录",
                )
            else:
                _set_status(model, "无更多失败记录")
            return True
        if ch in ("]", "["):
            pos = _find_record_pos(
                records, sel_pos, 1 if ch == "]" else -1, _is_tool_record,
            )
            if pos >= 0:
                _select_record(model, pos)
                _set_status(
                    model, f"\u2192 #{getattr(records[pos], 'index', 0)} 工具调用",
                )
            else:
                _set_status(model, "无更多工具调用")
            return True
        # ★ 2026-08-19（vim 搜索）："/" 开始搜索（任何焦点）——预填上次
        #   pattern 可编辑（vim 语义）；n 下一个 / N、p 上一个（p 为用户
        #   原话 prev 兼容别名）切换匹配并定位。
        if ch == "/":
            # ★ 2026-10-07（统计面板）：搜索需查看检查器内容——自动关闭面板
            #   （下一帧右栏恢复检查器，滚动预算回归检查器行数）。
            model.trace_stats_open = False
            model.trace_search_mode = True
            model.trace_search_query = (
                getattr(model, "trace_search_pattern", "") or ""
            )
            return True
        if ch in ("n", "N", "p") and getattr(model, "trace_search_pattern", ""):
            # ★ 语义说明（P3 review）：n/N/p 在**任何焦点**（台账/检查器）
            #   下均消费——搜索结果导航是跨面板的全局操作（匹配集合来自
            #   台账 records，定位同时移动台账选中与检查器光标）；仅在
            #   已有搜索 pattern 时生效，未搜索时字符照常放行。
            _search_jump(
                model, 1 if ch == "n" else -1, total_content, approx_content_vh,
                view_map,
            )
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
                        _clear_search(model)
                    return True
            # ★ P0-1：通用 vim 导航（j/k/g/G）收敛到 ``_inspector_pane``
            #   （与 trace_tools_view / plugin_view 共享同一实现）。
            if handle_nav(event, pane_state, total_content, approx_content_vh):
                return True
    # ── 检查器焦点：方向键/翻页/首末（ListView focus=False 不消费） ──
    if pane_now == "inspector":
        if event.kind == "arrow_left":
            model.trace_pane = "ledger"
            return True
        # ★ P0-1：方向键/翻页/首末导航统一走 ``_inspector_pane``。
        if handle_nav(event, pane_state, total_content, approx_content_vh):
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
        rec = records[sel_pos] if 0 <= sel_pos < total else None
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
                _clear_search(model)
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
                _clear_search(model)
                return True
    # 其余按键不消费——台账：放行 ListView（j/k/↑↓/PgUp/PgDn/Home/End/
    # g/G 导航）；检查器：未消费按键被 use_fullscreen 模态吞掉（不落入
    # 输入缓冲，杜绝看不见的输入；2026-08-17 通用模态全屏视图机制）
    return False


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
    # ★ 渲染期无副作用（架构修复）：渲染期计算的模型写回（选中项跟随归一化 /
    #   光标与滚动钳制）先收集在本 dict，组件末尾经 ``use_effect`` 统一提交。
    #   修复前直接在组件函数体内写 ``model.xxx``——违反「渲染 = 纯函数」契约，
    #   与 memo 短路 / 未来并发渲染 / 双调用校验冲突，也让状态变更难以定位
    #   （渲染次数与顺序变化即改变模型）。
    _model_writes: dict = {}

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
    # ── 过滤视图（2026-10-07 搜索增强：``f`` 只显示匹配记录） ──
    #   hooks 无条件调用（顺序稳定）：关闭/无匹配时返回 (None, None, None)
    #   占位，实际列表保持原始；打开且有台账匹配 → 用匹配子集替换
    #   records/rows 并保留「原始索引 → 视图索引」映射（``view_map``）。
    raw_records = records
    view_map = None
    filter_on = bool(getattr(model, "trace_search_filter", False))
    filter_matches = list(getattr(model, "trace_search_matches", None) or [])
    filter_active = bool(
        filter_on
        and (getattr(model, "trace_search_side", "") or "") == "ledger"
        and (getattr(model, "trace_search_pattern", "") or "")
        and filter_matches
    )
    filtered = use_memo(
        lambda: (
            _filter_view(raw_records, filter_matches)
            if filter_active else (None, None, None)
        ),
        (
            id(raw_records),
            1 if filter_active else 0,
            ";".join(str(i) for i in filter_matches),
        ),
    )
    if filtered[0] is not None:
        records, rows, view_map = filtered

    # ── 面板开关与统计（2026-10-07：``?`` 帮助 / ``i`` 统计概览） ──
    help_open = bool(getattr(model, "trace_help_open", False))
    stats_open = bool(getattr(model, "trace_stats_open", False))
    # 头部统计（use_memo：记录集变化才重算——避免每帧全量遍历；统计口径
    # 始终基于未过滤全量记录，过滤层数另在头部标注）
    trace_stats = use_memo(
        lambda: collect_trace_stats(raw_records),
        (id(raw_records), len(raw_records)),
    )
    source_label = f"子代理 {sub_label}" if sub_label else ""

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
        _model_writes["trace_selected"] = -1
        sel = -1
    if total == 0:
        sel = 0
    elif sel == -1 or sel >= total:
        sel = total - 1

    # ── 视口 / 栏宽 ──
    # ★ 2026-08-19（vim 搜索）：搜索输入模式时底部显示 ``/${query}`` 行，
    #   台账/检查器可见高度减 1（输入行占一行）。
    # ★ 2026-10-07（底部状态行）：匹配计数/操作反馈占一行时同样从可见高度
    #   扣除（行级 diff 高度不变量——内容不超屏）。
    search_mode = bool(getattr(model, "trace_search_mode", False))
    status_text = _status_line_text(
        model, filtered=bool(filter_on and view_map is not None),
    )
    extra_rows = (1 if search_mode else 0) + (1 if status_text else 0)
    vh = max(4, _viewport_rows() - extra_rows)
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
        # ★ P3（review）：详情行写入弱引用缓存（不写记录对象属性）——
        #   修复前 ``rec._detail_lines = detail_lines`` 为渲染期写共享记录
        #   对象的副作用（跨视图/跨帧串扰）。
        _set_cached_detail_lines(rec, detail_lines)

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
    # ★ P1（review）：滚动协调用与 ``_inspector_children`` 相同的视口预算
    #   （单一真源）——修复前为固定 ``vh - 3`` 近似，与内容生成的
    #   ``vh - fixed``（fixed 随 meta/subagent 变化）不一致 → 光标越出可见
    #   窗口（高亮消失/视口跟随失效）。
    approx_content_vh = _inspector_viewport_rows(rec, vh)
    # ★ P0-1：光标/滚动归一化统一走 ``_inspector_pane.resolve``（越界钳制 +
    #   光标可见跟随）——取代本地复刻（与 trace_tools_view / plugin_view
    #   三份重复实现中的一份）；渲染期不写 model，写回经 ``_model_writes``
    #   在提交期统一落盘。
    if help_open or stats_open:
        # ★ 2026-10-07（帮助 / 统计面板）：面板内容行数与检查器不同——
        #   不复用检查器预算钳制（``_pane_window_children`` 按面板自身行数
        #   钳制光标/滚动，避免以检查器行数误判越界）。
        cursor, scroll = cursor_raw, scroll_raw
    else:
        cursor, scroll_new = resolve(
            cursor_raw, scroll_raw, total_content, approx_content_vh,
        )
        if cursor != cursor_raw:
            _model_writes["trace_inspector_cursor"] = cursor
        if scroll_new != (getattr(model, "trace_inspector_scroll", 0) or 0):
            _model_writes["trace_inspector_scroll"] = scroll_new
        scroll = scroll_new
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
        if view_map is not None:
            # ★ 2026-10-07（过滤模式）：视图内全部记录即匹配集合——全部
            #   标记为匹配；当前匹配经「原始索引 → 视图索引」映射定位。
            ledger_matched_ids = {id(r) for r in records}
            if 0 <= search_idx < len(search_matches):
                pos = view_map.get(search_matches[search_idx])
                if pos is not None and 0 <= pos < len(records):
                    ledger_cur_id = id(records[pos])
        else:
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
    # ★ 2026-10-07（帮助 / 统计面板）：与检查器同一滚动语义的通用面板内容
    #   （use_memo 包装——面板关闭时返回空列表；deps 含开关状态与内容指纹，
    #   打开/内容变化才重建）。
    help_rows = use_memo(
        lambda: (
            _pane_window_children(
                help_panel_rows(right_w), right_w, vh, scroll, cursor_arg,
                "thelp", "(无帮助内容)",
            )
            if help_open else []
        ),
        (1 if help_open else 0, right_w, vh, scroll, cursor_arg),
    )
    stats_rows = use_memo(
        lambda: (
            _pane_window_children(
                stats_panel_rows(records, right_w), right_w, vh, scroll,
                cursor_arg, "tstats", "(无统计数据)",
            )
            if stats_open else []
        ),
        (
            1 if stats_open else 0, id(records), len(records), right_w, vh,
            scroll, cursor_arg,
        ),
    )
    # 右栏内容三态：帮助面板 > 统计面板 > 检查器（面板互斥覆盖右栏）。
    # pane_total / pane_vh：面板行数与视口（供事件处理以面板行数滚动）。
    pane_total = 0
    pane_vh = 0
    if help_open:
        pane_rows = help_rows
        pane_total = len(help_panel_rows(right_w))
    elif stats_open:
        pane_rows = stats_rows
        pane_total = len(stats_panel_rows(records, right_w))
    else:
        pane_rows = right_rows
    if pane_total:
        pane_vh = max(_INSPECTOR_MIN_CONTENT, vh - 2)

    # ── 台账可见窗口（选中记录在 rows 中的下标——ListView 受控光标） ──
    row_count = len(rows)
    sel_row = _row_of_record(rows, sel, records) if row_count else 0

    # ── 输入（trace_open 期间激活；关闭类按键本组件消费，导航放行 ListView） ──
    # ★ P0-1：检查器面板状态规约——通用滚动/光标/导航逻辑经 getter/setter
    #   注入 ``_inspector_pane``（与 trace_tools_view / plugin_view 共享同一
    #   实现，取代此前的本地复刻 ``_scroll_for_cursor`` / ``_move_cursor``）。
    _pane_state = PaneState(
        lambda: getattr(model, "trace_inspector_cursor", 0) or 0,
        lambda v: setattr(model, "trace_inspector_cursor", v),
        lambda: getattr(model, "trace_inspector_scroll", 0) or 0,
        lambda v: setattr(model, "trace_inspector_scroll", v),
    )

    # ★ P1-1（巨型组件拆分）：搜索辅助（清除/定位/切换/执行）收敛到模块级
    #   ``_clear_search`` / ``_search_locate`` / ``_search_jump`` / ``_exec_search``。

    # ★ P1-1（巨型组件拆分）：输入事件处理收敛到模块级
    #   ``_handle_trace_event``（搜索输入 / 关闭键 / 面板切换 / 检查器导航 /
    #   Enter 下钻）；hooks 无条件注册，与拆分前一致。
    use_input(
        lambda ev: _handle_trace_event(
            model, records, content_rows, row_keys, sel,
            total=total, total_content=total_content,
            approx_content_vh=approx_content_vh,
            pane_state=_pane_state, event=ev,
            # ★ 2026-10-07（增强）：搜索/导出基于未过滤全量记录，选择定位
            #   经 view_map 映射（过滤模式）——source 供导出元信息标注来源。
            raw_records=raw_records, view_map=view_map, source=source_label,
            pane_total=pane_total, pane_vh=pane_vh,
        ),
        bool(getattr(model, "trace_open", False)),
    )
    # ★ 模态全屏视图声明（2026-08-17 通用机制）：trace_open 期间未消费按键
    #   被 input router 吞掉（不落入输入缓冲）——字符/Enter 不误编辑/误提交；
    #   关闭后（trace_open=False）hook 不激活零影响，输入区恢复正常输入。
    use_modal_scope(bool(getattr(model, "trace_open", False)))

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
        # ★ 2026-10-07（增强）：切换记录清除底部状态提示（陈旧反馈不残留）。
        model.trace_status_message = ""
        # ★ 2026-08-19（树控件空格展开/收缩）：切换记录同时复位树折叠
        #   集合（折叠状态是「当前选中记录」的临时浏览状态——不同记录树
        #   不同，从默认全展开开始）。
        model.trace_tree_collapsed = set()
        # ★ 2026-08-19（vim 搜索）：切换记录 → 检查器内容行变化——检查器
        #   搜索匹配索引失效，清除搜索（台账搜索匹配记录索引不受影响）。
        if getattr(model, "trace_search_side", "") == "inspector":
            _clear_search(model)

    # ── 渲染 ──
    # 头部（静态色——轨迹视图为浏览界面，不呼吸，diff 零输出）
    # ★ 2026-10-07（头部统计条）：统计摘要（条数/轮次/工具/失败/耗时/token/
    #   位置 n/total）经 ``format_summary`` 单一真源生成，随栏宽截断；
    #   过滤模式额外标注「过滤 x/y 条」，帮助/统计面板打开时标注面板名。
    # 提示精简（宽度预算）：完整键位速查见 ``?`` 帮助面板（trace_keymap 表）。
    if sub_label:
        header_title = f"\u258d子代理轨迹 {sub_label}"
        if pane == "inspector":
            header_hint = "  jk \u6eda\u52a8 \u00b7 / \u641c\u7d22 \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u8fd4\u56de"
        else:
            header_hint = "  \u2191\u2193 \u9009\u62e9 \u00b7 / \u641c\u7d22 \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u8fd4\u56de"
    else:
        header_title = "\u258d轨迹 Trace"
        if pane == "inspector":
            header_hint = "  jk \u6eda\u52a8 \u00b7 / \u641c\u7d22 \u00b7 h \u53f0\u8d26 \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u5173\u95ed"
        else:
            header_hint = "  \u2191\u2193 \u9009\u62e9 \u00b7 / \u641c\u7d22 \u00b7 Enter \u8be6\u60c5 \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u5173\u95ed"
    sel_pos = (sel + 1) if (0 <= sel < total) else 0
    stats_text = format_summary(trace_stats, sel_pos, total)
    if view_map is not None:
        stats_text = (
            f"过滤 {total}/{_safe_int(trace_stats.get('total'))} 条 \u00b7 "
            + stats_text
        )
    if help_open:
        stats_text = "帮助 \u00b7 " + stats_text
    elif stats_open:
        stats_text = "统计 \u00b7 " + stats_text
    header_runs = [
        StyledRun(header_title, _S_TITLE),
        StyledRun(f" \u00b7 {stats_text}", _S_HINT),
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
    # 右栏（帮助 / 统计 / 检查器三态——pane_rows 见上；均 use_memo 包装）

    # ── 底部状态行（搜索匹配计数 + 操作反馈；有内容才占一行） ──
    status_line = None
    if status_text:
        status_line = h(TEXT, {
            "children": (
                _truncate_text(status_text, width) if width > 0 else status_text
            ),
            "style": _S_STATUS,
            "height": 1,
            "key": "tstatus-line",
        })

    # ── 底部搜索输入行（vim 风格 ``/${query}`` + 光标；回车后不显示） ──
    # ★ 2026-08-19（用户需求：轨迹 Trace vim 搜索——"这里要显示光标回车后
    #   不显示"）：搜索输入模式时在视图底部渲染 ``/${query}`` + 光标块
    #   （▏），台账/检查器高度已减 1 为其让位；回车执行后
    #   ``trace_search_mode=False`` → 本行消失。
    search_line = None
    if search_mode:
        query_text = getattr(model, "trace_search_query", "") or ""
        # ★ 2026-08-20（review P3）：渲染截断——底部 ``/${query}▏`` 按栏宽
        #   预算截断（`/` 1 列 + query + `▏` 1 列 ≤ width），防超长 query 撑
        #   破行级 diff 宽度不变量（依赖 render_frame 超宽防线兜底改为显式
        #   截断；超长输入已在输入侧 _SEARCH_QUERY_MAX 限制，双保险）。
        query_text = query_text[: max(0, width - 2)]
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
            h(Column, {"width": right_w}, pane_rows),
        ]),
    ]
    if status_line is not None:
        children.append(status_line)
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
    # 2026-10-07（轨迹 Trace 增强）：统计/帮助/过滤/定位/复制/导出辅助
    "_meta_parts",
    "_pane_window_children",
    "_status_line_text",
    "_filter_view",
    "_record_pos_by_number",
    "_find_record_pos",
    "_is_error_record",
    "_is_tool_record",
    "_set_status",
    "_reset_browse_state",
    "_select_record",
    "_collapse_all",
    "_do_copy",
    "_do_export",
    "_toggle_filter",
    "_toggle_search_case",
]
