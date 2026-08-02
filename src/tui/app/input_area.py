"""InputArea — 输入区 host 组件（提示符 + 换行输入 + 光标 + 呼吸发光）。

注册 ``input-area`` host 标签到 ink 注册表：
  - measure_fn：补全弹窗 + 上分隔线 + 输入行 + 下分隔线高度。
  - paint_fn：绘制到画布。

复用 _input.py 的 ``_expand_tabs`` / ``_wrap_by_width`` /
``_compute_cursor_visual_pos`` / ``_compute_input_layout`` /
``_cursor_visual_from_layout``（唯一真源），保证换行/CJK/光标计算与旧实现
一致。

方向5（光标算法单一真源）：``_compute_input_layout`` /
``_cursor_visual_from_layout`` 已迁移至 ``_input.py``（本文件从 _input 导入，
删除本地副本——input_area 与 session 共享同一实现，不再双实现）。
"""

from __future__ import annotations

import time

from src.tui._screen import (
    _COLOR_ACCENT,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _COLOR_SPEED,
    _COLOR_TIME,
    wcswidth_simple,
)
from src.tui._input import (
    _expand_tabs,
    _wrap_by_width,
    _tab_pos_to_expanded,
    _compute_cursor_visual_pos,
    # ★ 方向5（光标算法单一真源）：_compute_input_layout /
    #   _cursor_visual_from_layout 自本文件迁移至 _input.py——这里从 _input
    #   导入（删除本地副本，避免双实现）。
    _compute_input_layout,
    _cursor_visual_from_layout,
)
from src.tui.core.style import Style
from src.tui.ink import register_host, Line
from src.tui.app import _fx
from src.tui.app._theme import time_glow, _S_ACCENT, _S_DIM, _S_SEP, _S_TEXT, _S_TIME

# 占位符
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"
_PLACEHOLDER_STREAMING = "AI 生成中..."

_PROMPT = "> "

# 方向C 步骤4：_S_TEXT 被多处使用 → 迁入 app/_theme.py 共享池；以下单处使用
# 常量保留模块私有（享元收敛原则：仅多处使用才共享）。
# P2-10：_S_PROMPT/_S_PLACEHOLDER 为死常量（定义后全项目无引用——提示符已用
# 呼吸色 _glow_color、占位符已用渐显色 _placeholder_fade_color）→ 删除。
_S_CONT = Style(fg=242)
_S_CPU = Style(fg=214)
_S_MEM = Style(fg=214)


def _glow_color(base: int, amp: int) -> int:
    return time_glow(base, base + amp, 12.0)


def _placeholder_fade_color(fiber, ph: str, end_color: int) -> int:
    """占位提示 FadeIn 渐显色号（BEAUTY-1，时间基）。

    fiber 上记录 ``(ph, start_monotonic)``；占位符出现/切换时重置起始时间，
    同占位符持续显示时 elapsed 单调递增；elapsed>=duration 后返回 end_color
    （动画结束返回终色，不再触发重绘——BEAUTY-5）。duration/start 使用
    ``_fx.fade_color`` 默认参数（对齐 TuiConfig.fade_duration_sec/fade_start_color）。
    """
    key = getattr(fiber, "_placeholder_fade_key", None)
    if key is None or key[0] != ph:
        # ★ 方向6（复用一次 time.monotonic）：修复前两次调用——第一次存储值
        #   未用于计算，start 取第二次调用值，两次调用间时钟推进产生轻微
        #   起始抖动窗口；统一为单次调用（now 既存储又作为 start）。
        now = time.monotonic()
        fiber._placeholder_fade_key = (ph, now)
        start = now
    else:
        start = key[1]
    elapsed = time.monotonic() - start
    return _fx.fade_color(elapsed, None, 238, end_color)


def _compute_input_rows(text: str, max_input: int) -> int:
    """输入文本换行行数（至少 1）。"""
    rows, _ = _compute_input_layout(text, max_input)
    return rows


def _wrap_input_text(text: str, max_input: int) -> list[str]:
    """输入文本拆行段列表（扁平，兼容旧调用面）。"""
    _, wrapped_by_logical = _compute_input_layout(text, max_input)
    return [seg for segs in wrapped_by_logical for seg in segs]


# ── 测量 ───────────────────────────────────────────


def _desc_column_width(width: int) -> int:
    """分栏说明模式右栏宽度（user_select：说明在选项右侧显示）。

    取终端宽度 1/3，钳制到 [8, 40]，且给左栏选项至少预留 12 列——
    极窄终端（width<20）下右栏同步缩小，避免左栏被挤压溢出。
    """
    max_w = max(8, int(width) - 12)
    return max(8, min(int(width) // 3, 40, max_w))


def _completion_height(completion, width=None) -> int:
    """补全弹窗高度（标题 + 候选项 + 提示行）。

    分栏说明模式（split_desc 且存在说明）下，高度取选项数与当前选中项说明
    换行行数的较大值——说明可多行，弹窗随说明行数增高。
    """
    if completion is None or not completion.visible or not completion.items:
        return 0
    n = len(completion.items)
    descs = completion.descriptions or []
    if not (getattr(completion, "split_desc", False) and descs) or width is None:
        return n + 2
    desc_w = _desc_column_width(width)
    sel = max(0, min(completion.selected, len(descs) - 1))
    desc_lines = _wrap_by_width(descs[sel] or "", desc_w)
    return max(n, len(desc_lines)) + 2


def _is_search_active(search) -> bool:
    """反向历史搜索是否激活（history_search 非 None 且 active，方向D 步骤14）。"""
    return search is not None and bool(getattr(search, "active", False))


def _measure(fiber, avail_w) -> tuple[int, int]:
    props = fiber.props
    explicit = props.get("width")
    width = max(0, int(explicit)) if explicit is not None else avail_w
    completion = props.get("completion")
    popup_height = _completion_height(completion, width)
    max_input = max(1, width - len(_PROMPT))
    text = str(props.get("text", ""))
    # ★ 方向D 步骤14：反向历史搜索覆盖行（追加一行）
    search_active = _is_search_active(props.get("history_search"))
    # ★ PERF-1：缓存命中（同 text/max_input）时复用换行布局（每帧至多 1 次换行）
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        rows, _ = cached[1]
    else:
        rows, wrapped_by_logical = _compute_input_layout(text, max_input)
        fiber._input_layout_cache = ((text, max_input), (rows, wrapped_by_logical))
    height = popup_height + 2 + rows + (1 if search_active else 0)
    return (width, height)


# ── 绘制 ───────────────────────────────────────────


def _build_lines(fiber) -> list[Line]:
    props = fiber.props
    box = fiber.layout_box
    width = box.w
    text = str(props.get("text", ""))
    completion = props.get("completion")
    popup_height = _completion_height(completion, width)
    status_active = bool(props.get("status_active", False))
    max_input = max(1, width - len(_PROMPT))

    # ★ 快照缓存（方向4）：同快照（text/max_input/completion 全字段/cpu/mem/
    #   status_active/history_search/时间桶）命中直接返回缓存的 Line 列表——
    #   免每帧重建全部行（补全弹窗/分隔线/时间戳/输入行）。时间戳降级 1s 桶
    #   （``int(time.monotonic() / 1.0)``）——当前每帧 ``time.localtime()``
    #   秒级时间戳导致每帧重建；1s 桶内时间显示最多滞后 1s（可接受，与状态栏
    #   1s 桶一致）。补全弹窗高亮移动（selected 变化）与状态变化（cpu/mem 每
    #   2s）必须进 key——均已包含。
    #   方向1 步骤4（呼吸动画渐显 0.1s 桶）：占位符渐显期（_placeholder_fade_key
    #   起始后 elapsed < fade_duration）用 0.1s 桶平滑渐显（避免 1s 桶内渐显
    #   冻结）；结束后回 1s 桶（性能保持，与 status_bar 语义对齐）；
    #   fade_duration<=0（配置异常）回退纯 1s 桶。
    now = time.monotonic()
    fade_key = getattr(fiber, "_placeholder_fade_key", None)
    fading = False
    if fade_key is not None:
        fade_elapsed = now - fade_key[1]
        fade_duration = _fx._DEFAULT_FADE_DURATION
        fading = fade_duration > 0 and fade_elapsed < fade_duration
    time_bucket = int(now / 0.1) if fading else int(now / 1.0)
    if completion is not None:
        completion_snap = (
            completion.visible,
            tuple(completion.items),
            completion.selected,
            completion.title,
            tuple(completion.texts),
            completion.match_prefix,
            tuple(completion.types),
            tuple(completion.descriptions),
            getattr(completion, "split_desc", False),
        )
    else:
        completion_snap = (False, (), 0, "", (), "", (), (), False)
    search = props.get("history_search")
    if search is not None:
        search_snap = (
            bool(search.active),
            search.query,
            tuple(search.matches),
            search.index,
        )
    else:
        search_snap = (False, "", (), -1)
    snap_key = (
        text,
        max_input,
        completion_snap,
        int(props.get("cpu", 0)),
        int(props.get("mem", 0)),
        status_active,
        search_snap,
        time_bucket,
    )
    cached = getattr(fiber, "_lines_cache", None)
    if cached is not None and cached[0] == snap_key:
        return cached[1]

    # ★ PERF-1：复用 measure 阶段缓存的换行布局（未命中时回退单次计算）
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        _, wrapped_by_logical = cached[1]
    else:
        _, wrapped_by_logical = _compute_input_layout(text, max_input)
    wrapped = [seg for segs in wrapped_by_logical for seg in segs]

    lines: list[Line] = []

    # ── 补全弹窗 ──
    if completion is not None and completion.visible and completion.items:
        items = completion.items
        selected = completion.selected
        match_prefix = completion.match_prefix or ""
        types = completion.types or [""] * len(items)
        title = completion.title
        total = len(completion.texts) if completion.texts else len(items)
        descs = completion.descriptions or []
        # 分栏说明模式（user_select）：左侧选项列表、右侧当前选中项说明
        split = bool(getattr(completion, "split_desc", False)) and bool(descs)
        desc_w = _desc_column_width(width) if split else 0
        # 左栏选项宽度 = 总宽 - 右栏说明 - 分隔线
        opt_w = max(1, width - desc_w - 1) if split else 0
        # 标题行
        head = Line.of(" ", Style(fg=45, bold=True))
        head.append(title, Style(fg=45, bold=True))
        head.append(f" ({total}项)", _S_TIME)
        if split:
            # 左栏标题占位（标题与选项栏对齐；右栏说明位置留白）
            head.append(" " * max(0, opt_w - head.width), _S_DIM)
        lines.append(head)
        # 候选项
        if split:
            # 左栏选项内容宽度（前缀 ▶ + 文本；右栏说明独立换行）
            cell_w = max(
                1, min(max((_vwidth(i) for i in items), default=10) + 4, opt_w - 2) - 3,
            )
            desc_text = descs[selected] if 0 <= selected < len(descs) else ""
            desc_lines = _wrap_by_width(desc_text or "", desc_w)
            n_rows = max(len(items), len(desc_lines))
            for row in range(n_rows):
                line = Line()
                # 左栏：选项
                if row < len(items):
                    i = row
                    if i == selected:
                        line.append(" \u25b6 ", Style(fg=15, bg=236))
                    else:
                        line.append("   ")
                    for run in _styled_completion(items[i], types[i], match_prefix, cell_w).runs:
                        line.append_run(run)
                    # 补齐左栏剩余宽度（选项不足 opt_w 时留白，分隔线对齐）
                    pad = opt_w - line.width
                    if pad > 0:
                        line.append(" " * pad, _S_DIM)
                else:
                    line.append(" " * opt_w, _S_DIM)
                line.append("\u2502", _S_SEP)
                # 右栏：当前选中项说明（分栏换行）
                if row < len(desc_lines):
                    line.append(_truncate_width(desc_lines[row], desc_w), _S_DIM)
                lines.append(line)
        else:
            cell_w = max(1, min(max((_vwidth(i) for i in items), default=10) + 4, width - 2) - 3)
            for i, item in enumerate(items):
                line = Line()
                if i == selected:
                    line.append(" \u25b6 ", Style(fg=15, bg=236))
                else:
                    line.append("  ")
                for run in _styled_completion(item, types[i], match_prefix, cell_w).runs:
                    line.append_run(run)
                # Claude TUI parity 步骤 3.7：斜杠命令描述灰显（command 且描述非空）
                if types[i] == "command" and i < len(descs) and descs[i]:
                    line.append("  ", _S_DIM)
                    # 方向1 步骤4（窄屏防溢出）：描述截断至剩余行宽（复用
                    # _truncate_width，截断点不拆 CJK）——超长描述不再撑爆行宽。
                    desc_budget = max(1, width - line.width)
                    line.append(_truncate_width(descs[i], desc_budget), _S_DIM)
                lines.append(line)
        # 底部提示
        hint = Line.of(" ", _S_TIME)
        hint.append("Tab \u2191\u2193 Esc", _S_TIME)
        lines.append(hint)

    # ── 上分隔线（CPU/MEM） ──
    cpu = int(props.get("cpu", 0))
    mem = int(props.get("mem", 0))
    cpu_mem = f"CPU:{cpu}% \u00b7 MEM:{mem}%"
    cpu_mem_w = len(cpu_mem) + 2
    top = Line.of("", _S_SEP)
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限改为 0（修复前 ``max(1, ...)``
    # 在 width < cpu_mem_w 时内容超宽溢出）；CPU/MEM 内容独立行逐段截断至
    # 剩余宽度（不拆 CJK；width < 22 时不再超宽）。
    sep_len = max(0, width - cpu_mem_w)
    top.append("\u2501" * sep_len, _S_SEP)
    content_budget = max(1, width - sep_len)
    content = Line()
    _append_truncated(content, " CPU:", _S_ACCENT, content_budget)
    _append_truncated(content, f"{cpu}%", _S_CPU, content_budget)
    _append_truncated(content, " \u00b7 MEM:", _S_ACCENT, content_budget)
    _append_truncated(content, f"{mem}%", _S_MEM, content_budget)
    for run in content.runs:
        top.append_run(run)
    lines.append(top)

    # ── 反向历史搜索覆盖行（方向D 步骤14，Ctrl+R 配置门控） ──
    # 搜索激活时在上分隔线之后、输入文本行之前追加一行（measure 已增行）：
    # (reverse-i-search)`query`: match
    search = props.get("history_search")
    if _is_search_active(search):
        q = search.query
        match = ""
        if search.matches and 0 <= search.index < len(search.matches):
            match = search.matches[search.index]
        sline = Line.of("(reverse-i-search)`", _S_ACCENT)
        sline.append(q, Style(fg=221))
        sline.append("`: ", _S_ACCENT)
        # 方向1 步骤4（窄屏防溢出）：match 截断至剩余行宽（不拆 CJK）
        match_budget = max(1, width - sline.width)
        sline.append(_truncate_width(match, match_budget), _S_TEXT)
        # 极窄屏（前缀 + query 已超宽）→ 整行截断至 width（复用 truncate_line）
        if sline.width > width:
            from src.tui.ink.helpers import truncate_line
            sline = truncate_line(sline, width)
        lines.append(sline)

    # ── 输入文本行 ──
    # ★ PERF-1：wrapped 已在函数开头从缓存/单次计算得到（见上），此处直接使用
    for i, segment in enumerate(wrapped):
        line = Line()
        if i == 0:
            color = _glow_color(32, 49)
            line.append(_PROMPT, Style(fg=color, bold=True))
            if text:
                line.append(segment, _S_TEXT)
            else:
                if status_active:
                    ph = _PLACEHOLDER_STREAMING
                else:
                    ph = _PLACEHOLDER_COMPACT if (completion is not None and completion.visible) else _PLACEHOLDER_TEXT
                # 方向1 步骤4（窄屏防溢出）：占位符截断至剩余输入区宽度
                # （提示符后；_truncate_width 不拆 CJK）——width < 占位符长度
                # 时不再撑爆行宽。截断后的 ph 作为渐显键（同占位符持续显示
                # 语义一致）。
                ph_budget = max(1, width - len(_PROMPT))
                if wcswidth_simple(ph) > ph_budget:
                    ph = _truncate_width(ph, ph_budget)
                # BEAUTY-1：占位提示 FadeIn 渐显（时间基；_glow_color 呼吸色为终色）
                line.append(ph, Style(fg=_placeholder_fade_color(fiber, ph, _glow_color(242, 10))))
        else:
            line.append("\u00b7 ", _S_CONT)
            line.append(segment, _S_TEXT)
        lines.append(line)

    # ── 下分隔线（时间戳） ──
    now_local = time.localtime()
    ts = f"{now_local.tm_year}-{now_local.tm_mon:02d}-{now_local.tm_mday:02d} {now_local.tm_hour:02d}:{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
    time_w = len(ts) + 2
    bottom = Line.of("", _S_SEP)
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限 0 + 时间戳内容独立行截断
    # （width < 22 时不超宽；正常宽度时间戳完整保留）
    sep_len = max(0, width - time_w)
    bottom.append("\u2501" * sep_len, _S_SEP)
    content_budget = max(1, width - sep_len)
    content = Line()
    _append_truncated(content, f" {ts}", _S_TIME, content_budget)
    for run in content.runs:
        bottom.append_run(run)
    lines.append(bottom)

    # ★ 快照缓存写回（方向4）：未命中重建后更新缓存（同快照下次命中）
    fiber._lines_cache = (snap_key, lines)
    return lines


def _vwidth(s: str) -> int:
    return wcswidth_simple(s)


def _styled_completion(text: str, item_type: str, match_prefix: str, cell_w: int) -> Line:
    """构建候选项行（命令/目录/匹配高亮）。"""
    out = Line()
    truncated = _truncate_width(text, cell_w)
    if item_type == "command" and truncated.startswith("/"):
        out.append("/", Style(fg=45, bold=True))
        rest = truncated[1:]
        if match_prefix and len(match_prefix) > 1 and rest.startswith(match_prefix[1:]):
            inner = match_prefix[1:]
            out.append(rest[:len(inner)], Style(fg=221))
            out.append(rest[len(inner):])
        else:
            out.append(rest)
    elif item_type == "dir" and truncated.endswith("/"):
        out.append(truncated, Style(fg=110))
    else:
        if match_prefix and truncated.startswith(match_prefix):
            out.append(truncated[:len(match_prefix)], Style(fg=221))
            out.append(truncated[len(match_prefix):])
        else:
            out.append(truncated)
    return out


def _truncate_width(s: str, max_w: int) -> str:
    w = 0
    out = []
    for ch in s:
        cw = wcswidth_simple(ch)
        if w + cw > max_w:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def _append_truncated(line: Line, text: str, style, budget: int) -> None:
    """向内容行追加文本，超宽时按剩余预算截断（不拆 CJK）。

    方向1 步骤4：窄屏防溢出辅助——内容行（从 0 列计宽，独立于分隔线）
    逐段截断至预算，保证分隔线行总宽不超 width。
    """
    remaining = max(0, budget - line.width)
    if remaining <= 0:
        return
    line.append(_truncate_width(text, remaining), style)


def _paint(fiber, canvas) -> None:
    box = fiber.layout_box
    if box is None:
        return
    lines = _build_lines(fiber)
    for i, line in enumerate(lines):
        row = box.y + i
        if 0 <= row < len(canvas):
            # ★ 画布惰性行（方向4）：canvas 初始 None——仅未命中行创建 dict；
            #   自定义 host paint 与内置 TEXT 共用惰性语义。
            target = canvas[row]
            if target is None:
                target = {}
                canvas[row] = target
            _merge(target, box.x, line)


def _merge(row: dict, x: int, line: Line) -> None:
    # 方向1 步骤4（CJK 列推进）：列偏移按显示宽度推进（``wcswidth_simple``）
    # ——修复前 ``col += 1`` 按字符计数，CJK 宽字符占 2 列却只推进 1，
    # 后续字符错位。
    col = x
    for run in line.runs:
        for ch in run.text:
            row[col] = (ch, run.style)
            col += wcswidth_simple(ch)


# ── 注册 ───────────────────────────────────────────


def register() -> None:
    """注册 input-area host 组件。"""
    register_host("input-area", _measure, _paint)


__all__ = [
    "register",
    "_measure",
    "_paint",
    "_compute_input_rows",
    "_compute_input_layout",
    "_wrap_input_text",
    "_cursor_visual_from_layout",
]
