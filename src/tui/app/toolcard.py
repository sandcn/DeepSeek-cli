"""toolcard — ToolCard 工具调用卡片控件（React Ink 组件化）。

工具执行结果卡片（对齐 Claude Code）：标题行（状态图标 + ▎引导线 + 类别色
工具图标 + 加粗类别色名称 + detail）+ 内容行（``│ `` 竖线引导）+ 状态行
（``✔ 完成 · N 行 · Xs`` / ``✖ 失败 · N 行 · Xs``）。**无边框**——卡片以
「裸行」呈现（2026-08-06 用户需求：所有 tool card 去掉边框），不再绘制
``┌─…┐`` / ``│…│`` / ``└─…┘`` 边框字符；``│`` 为**内容竖线引导线**
（Claude Code 风格视觉归组，非边框）。

完整美化（BEAUTY-35，2026-08-06）：工具类别配色 + 标题强化 + 内容竖线 +
状态元信息：
  - 类别配色：标题 ▎引导线 / 工具图标 / 显示名按工具类别着色（唯一真源
    ``_tool_icons.TOOL_CATEGORY_STYLES``：shell 绿 / file_read 浅蓝 /
    file_write 粉 / search 金 / agent 蓝 / interact 青 / delete 红），运行中
    在类别色邻域 12s 脉动呼吸（与 detail 呼吸同步），关闭后静态类别色；
  - 标题强化：显示名加粗 + ▎引导线（状态图标恒为 runs[0]——close_tool_box
    原位翻转图标与 ``startswith(●/✔/✖)`` 测试不变式依赖）；
  - 内容竖线：每内容行前置 ``│ ``（深灰 238），窄屏截断保证总宽 <= width；
  - 状态元信息：状态行追加 ``· N 行 · Xs``（dim 灰；行数/耗时由
    ``_tool_output_mixin`` 记录）。

React Ink 组件化（2026-08-05，深度组件化）：原 ``AppModel._tool_card_styled_lines``
（模型层纯函数生成 StyledRun 行）迁移为独立组件模块，模型层不再持有行生成
逻辑：

  - ``tool_card_lines``：纯行生成函数（保留 PERF 缓存语义——开放工具卡按
    块对象缓存 wrap 结果，大工具卡每帧零重建）。供模型层 committed 路径
    （``_block_to_ink_lines``）/ ``close_tool_box`` 标题行更新等消费；
  - ``ToolCard``：React Ink 函数组件——ChatView live 路径渲染工具块
    （``h(ToolCard, {"block": ..., "width": ..., "start": ...})``），内部
    Column + TEXT 行（行宽由 ``tool_card_lines`` 保证 <= width）。

渲染期变换：不改动 ``block.lines`` 原文（model 测试不变式
``block.lines[0].plain.startswith("  · ")`` / ``strip()=="✔"`` 依赖此）。
标题行仅 ``start==0``（块首次提交）；状态行仅块关闭且为最终块
（``stop is None`` 或 ``stop >= len(block.lines)``）。关闭状态行
``  ✔``（模型层保留）**不渲染为内容行**——状态移入状态行。

依赖约束：鸭子类型访问 ``block``（ChatBlock 字段：lines/closed/extra），
不 import 模型层（避免循环依赖）；样式来自 app._theme 调色板/呼吸色。
"""

from __future__ import annotations

# core.style 为 Layer 0 底层（无 app 依赖），模块级 import 无循环风险；
# 用于模块级样式常量（_GUIDE_STYLE / _CATEGORY_DEFAULT_STYLE）。
from src.tui.core.style import Style

__all__ = ["ToolCard", "tool_card_lines", "_tool_icon_runs", "_tool_status_index"]

# ── 工具类别配色（BEAUTY-35，2026-08-06 美化） ─────────────────────
# 标题 ▎引导线 / 工具图标 / 显示名按工具类别着色；运行中在类别色邻域
# 脉动呼吸（12s 周期，与 detail 呼吸同步——视觉联动），关闭后静态类别色。
# 呼吸区间下限 = 静态类别色号（_tool_icons.TOOL_CATEGORY_STYLES 同值），
# 上限为更亮色号（呼吸峰值）。未知名工具兜底暗灰→亮白（与旧图标呼吸一致）。
_CATEGORY_BREATH: dict[str, tuple[int, int]] = {
    "shell":      (41, 49),    # 绿 → 亮绿
    "file_read":  (81, 87),    # 浅蓝 → 亮蓝
    "file_write": (213, 219),  # 粉 → 亮粉
    "search":     (221, 229),  # 金 → 亮金
    "agent":      (75, 81),    # 蓝 → 亮蓝
    "interact":   (51, 87),    # 青 → 亮青
    "delete":     (203, 210),  # 红 → 亮红
}
_CATEGORY_DEFAULT_STYLE = Style(fg=242)     # 未知名工具兜底（dim）
_CATEGORY_DEFAULT_BREATH = (242, 252)       # 未知名工具呼吸（暗灰→亮白）
_GUIDE_STYLE = Style(fg=238)                # 内容竖线引导色（深灰，低调）


def _category_style(tool_name: str) -> Style:
    """工具类别静态 Style（唯一真源 ``_tool_icons.TOOL_CATEGORY_STYLES``）。

    Args:
        tool_name: 工具名（TOOL_CATEGORY_MAP 键；未知名返回 dim 兜底）。

    Returns:
        类别 Style（frozen 对象，可复用）。
    """
    from src.tui._tool_icons import TOOL_CATEGORY_MAP, TOOL_CATEGORY_STYLES
    cat = TOOL_CATEGORY_MAP.get(tool_name, "")
    return TOOL_CATEGORY_STYLES.get(cat, _CATEGORY_DEFAULT_STYLE)


def _category_breath_fg(tool_name: str) -> int:
    """工具类别呼吸色号（运行中：类别色邻域 12s 脉动）。

    Args:
        tool_name: 工具名（TOOL_CATEGORY_MAP 键；未知名回退暗灰→亮白）。

    Returns:
        [lo, hi] 区间内的 256 色号（与 detail 呼吸同周期，视觉联动）。
    """
    from src.tui.app._theme import time_glow
    from src.tui._tool_icons import TOOL_CATEGORY_MAP
    cat = TOOL_CATEGORY_MAP.get(tool_name, "")
    lo, hi = _CATEGORY_BREATH.get(cat, _CATEGORY_DEFAULT_BREATH)
    return time_glow(lo, hi, 12.0)


def _tool_icon_runs(block) -> list:
    """工具块标题前置状态图标 runs（渲染装饰）。

    不改动 ``block.lines`` 原文（模型层保持原始标题行，测试断言
    ``block.lines[0].plain.startswith("  · ")`` 依赖此不变式）。
    样式取 ``StyleSheet.resolve`` 语义色（success/error/warn），
    兜底硬编码确保任何加载顺序下都有默认值。

    Args:
        block: 工具块（ChatBlock.kind == "tool"）。

    Returns:
        StyledRun 列表（图标 + 空格），running ● / done ✔ / fail ✖。
    """
    from src.tui.ink import StyledRun
    from src.tui.core.style import Style, StyleSheet
    status = block.extra.get("tool_status", "running")
    if status == "done":
        return [StyledRun("\u2714 ", StyleSheet.resolve("success", Style(fg=41)))]
    if status == "fail":
        return [StyledRun("\u2716 ", StyleSheet.resolve("error", Style(fg=196, bold=True)))]
    # 方向3（动效）：running ● 用橙色邻域呼吸色（208-220 脉动，6s 周期）——
    # 正在执行的工具图标持续呼吸，视觉提示活跃状态（替代静态 fg=214）。
    from src.tui.app._theme import time_glow
    c = time_glow(208, 220, 6.0)
    return [StyledRun("\u25cf ", Style(fg=c))]


def _tool_status_index(block):
    """工具卡状态行下标（close 追加的 `  ✔`/`  ✖` 行）；无则返回 None。

    关闭工具块时 ``close_tool_box`` 追加状态行到 block.lines 末尾（模型层
    不变式 ``block.lines[-1].plain.strip()=="✔"``）。卡片渲染把状态移到状态行
    （``✔ 完成``），渲染内容行时跳过该行。
    ``_status_line_index`` 由 close_tool_box 记录（歧义安全）；回退按末行
    plain 匹配（覆盖 reflow/旧块等未记录场景）。
    """
    idx = block.extra.get("_status_line_index")
    if idx is not None:
        return idx
    if block.closed and block.lines:
        last = block.lines[-1]
        if getattr(last, "plain", "").strip() in ("\u2714", "\u2716"):
            return len(block.lines) - 1
    return None


def _omitted_line(text: str, width: int) -> list:
    """省略提示行（``│ … 前/后 N 行省略``，无边框——BEAUTY-35 带竖线引导）。

    窄屏防溢出：提示文本超宽时截断至 width（与标题/内容行一致——
    不截断时窄终端错乱；竖线引导占用 2 列，内容截断至 width-2）。
    函数内惰性 import（与 tool_card_lines 同模式）。
    """
    from src.tui.ink import StyledRun
    from src.tui.ink.helpers import truncate_runs
    guide = [StyledRun("│ ", _GUIDE_STYLE)]
    if width <= 0:
        # 无宽度防御：不截断（与旧行为一致）
        return guide + [StyledRun(text, Style(fg=242))]
    if width == 1:
        # 极端窄屏：仅竖线（1 列）
        return [StyledRun("│", _GUIDE_STYLE)]
    return guide + truncate_runs([StyledRun(text, Style(fg=242))], width - 2)


def tool_card_lines(block, width, start=0, stop=None):
    """工具卡片渲染期行（标题行 + 内容行 + 状态行，**无边框**）。

    纯行生成函数（React Ink 组件化迁移自 ``_tool_card_styled_lines``）：
    渲染期变换，不改动 ``block.lines`` 原文（model 测试不变式
    ``block.lines[0].plain.startswith("  · ")`` / ``strip()=="✔"`` 依赖此）。
    标题行仅 ``start==0``（块首次提交）；状态行仅块关闭且为最终块
    （``stop is None`` 或 ``stop >= len(block.lines)``）。关闭状态行
    ``  ✔``（模型层保留）**不渲染为内容行**——状态移入状态行
    （``✔ 完成`` / ``✖ 失败``）。

    Args:
        block: 工具块（ChatBlock.kind == "tool"）。
        width: 卡片总宽度（终端列宽）；<=0 时按无边框裸行防御渲染。
        start: 起始 AnsiLine 下标（块内行）。
        stop: 结束下标（不含）；None 表示到块末尾。

    Returns:
        list[list[StyledRun]] — 每行 StyledRun 列表（卡片行，无边框字符）。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.ink import StyledRun
    from src.tui.ink.helpers import truncate_runs
    from src.tools.registry import get_tool_display_name
    from src.tui._tool_icons import TOOL_ICONS
    from src.renderer.ansi.helpers import wrap_line
    pal = get_active_palette()
    width = width if isinstance(width, int) and width > 0 else 0
    status_idx = _tool_status_index(block)
    # ★ 帧级缓存：开放工具卡动态色（状态图标呼吸 208↔220）为时间基
    #   （time_glow 0.1s 桶）——同一桶内帧复用**完整输出列表对象**，TEXT
    #   组件 ``_wrap_cache`` 按 styled 引用命中 → 主体行零重建。key 覆盖
    #   全部动态因素（行数/状态/宽度/省略计数/呼吸色）；任何变化重建。
    _status = block.extra.get("tool_status", "running")
    if start == 0 and _status == "running" and not block.closed:
        from src.tui.app._theme import time_glow as _time_glow_icon
        _icon_fg = _time_glow_icon(208, 220, 6.0)
        # BEAUTY-35：类别呼吸色（▎/图标/名称 12s 脉动）——与 _icon_fg 同桶
        # 固定（跨桶变化触发重建）；仅运行中且 start==0 时计算，其余 -1。
        _cat_fg = _category_breath_fg(block.extra.get("tool_name", ""))
    else:
        _icon_fg = -1
        _cat_fg = -1
    # ★ BUG-71（review 方向，缓存键完整性）：_frame_key 补充标题字段
    #   （tool_name/tool_detail）——修复前缺标题：open_tool_box 复用 box 更新
    #   标题后，同帧帧缓存（同 start/stop/status/len/呼吸色桶）命中旧标题。
    _frame_key = (
        start, stop, block.closed, _status, len(block.lines),
        _icon_fg, _cat_fg,
        block.extra.get("tool_name", ""),
        block.extra.get("tool_detail", ""),
        block.extra.get("_bash_omitted_lines", 0),
        block.extra.get("_head_omitted_lines", 0),
        width,
    )
    _frame_cache = getattr(block, "_tool_card_frame_cache", None)
    if _frame_cache is not None and _frame_cache[0] == _frame_key:
        return _frame_cache[1]
    out: list[list[StyledRun]] = []
    # 标题行（仅 start==0）：状态图标 + 工具图标 + 显示名（+ detail）。
    # 状态图标恒为 title_runs[0] → 标题行 runs[0]，供 close_tool_box 原位
    # 翻转图标（无边框前缀，runs[0] 即状态图标）。
    if start == 0:
        tool_name = block.extra.get("tool_name") or "工具"
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        icon_char = TOOL_ICONS.get(tool_name, "\u2699")
        detail = block.extra.get("tool_detail", "")
        title_runs = list(_tool_icon_runs(block))
        running = _status == "running" and not block.closed
        # ★ BEAUTY-35（工具卡完整美化）：▎引导线 + 工具图标 + 显示名按工具
        #   类别着色——运行中在类别色邻域呼吸（12s 周期，与 detail 呼吸同步；
        #   同 _cat_fg 值，整体同色脉动），关闭/提交后静态类别色（frozen 缓存
        #   不再重算，零额外渲染成本）。显示名加粗（标题强化）。runs[0] 保持
        #   状态图标（close_tool_box 原位翻转 + 测试 startswith(●/✔/✖) 不变式）。
        if running:
            cat_fg = _cat_fg
        else:
            cat_style = _category_style(tool_name)
            cat_fg = cat_style.fg if cat_style.fg is not None else 242
        title_runs.append(StyledRun("\u258e", Style(fg=cat_fg)))  # ▎ 引导线
        title_runs.append(StyledRun(f"{icon_char} ", Style(fg=cat_fg)))
        title_runs.append(StyledRun(display, Style(fg=cat_fg, bold=True)))
        if detail:
            # ★ BEAUTY-24（体验动效）：工具 detail 运行中呼吸——暗灰 242→252
            #   脉动（12s 周期，与状态栏 token/速度呼吸同步）。运行中的工具
            #   detail 更生动（与图标呼吸联动）；关闭/提交后保持静态 pal.dim
            #   （frozen 缓存不再重算，零额外渲染成本）。
            if running:
                from src.tui.app._theme import time_glow
                title_runs.append(StyledRun(
                    f" \u00b7 {detail}", Style(fg=time_glow(242, 252, 12.0)),
                ))
            else:
                title_runs.append(StyledRun(f" \u00b7 {detail}", pal.dim))
        out.append(truncate_runs(title_runs, width) if width > 0 else title_runs)
    # 内容行：block.lines[start:stop]，start==0 时跳过标题行（名字已在标题行）；
    # 关闭状态行（_tool_status_index）跳过——状态已移入状态行
    body_end = len(block.lines) if stop is None else min(stop, len(block.lines))
    body_start = start if start > 0 else 1
    # ★ PERF-6b：内容行整体缓存——跨帧/跨桶复用列表对象，TEXT
    #   ``_wrap_cache`` 按 styled 引用命中（大工具卡跨桶渲染不再每帧全量
    #   wrap；frame_cache 同桶快速路径之外的兜底）。key 仅依赖块内容/宽度/
    #   省略计数（不含呼吸色）——行数/宽度/省略变化时自动重建。
    _body_key = (
        start, len(block.lines), body_start, body_end, width, status_idx,
        block.extra.get("_bash_omitted_lines", 0),
        block.extra.get("_head_omitted_lines", 0),
    )
    body_lines_cache = getattr(block, "_tool_card_body_lines_cache", None)
    if body_lines_cache is not None and body_lines_cache[0] == _body_key:
        body_lines = body_lines_cache[1]
    else:
        body_lines: list[list[StyledRun]] = []
        # bash 尾显示：前置省略提示行「… 前 N 行省略」（仅首次提交 start==0）
        omitted = block.extra.get("_bash_omitted_lines", 0)
        if omitted > 0:
            body_lines.append(_omitted_line(f"\u2026 前 {omitted} 行省略", width))
        # ★ PERF-6（性能）：开放工具卡内容行按 ``(行对象, width)`` 缓存
        #   wrap+截断后的内容 runs——修复前每帧对全部内容行重新 ``wrap_line``
        #   （长 bash 输出 300 行 → 单帧 ~190ms → 10Hz 下 CPU 100%）。行对象
        #   创建后不原地修改（``append_tool_output`` 每行新建 AnsiLine），
        #   width 变化时 key miss 自动重算。
        body_cache = getattr(block, "_tool_card_body_cache", None)
        if body_cache is None:
            body_cache = {}
            block._tool_card_body_cache = body_cache
        for abs_idx in range(body_start, body_end):
            if status_idx is not None and abs_idx == status_idx:
                continue
            ansi_line = block.lines[abs_idx]
            key = (ansi_line, width)
            cached = body_cache.get(key)
            if cached is None:
                wrapped = (
                    wrap_line(ansi_line, width)
                    if width > 0
                    else ([ansi_line] if ansi_line.runs else [])
                )
                if not wrapped:
                    # 空输出行 → 空行（保持行映射）
                    cached = [("empty",)]
                else:
                    items: list = []
                    for seg in wrapped:
                        seg_runs = [StyledRun(r.text, r.style) for r in seg.runs if r.text]
                        if width <= 0:
                            items.append(("bare", seg_runs))
                            continue
                        # ★ BEAUTY-35（内容竖线引导）：每行前置 ``│ ``（深灰
                        #   238），内容截断至 width-2——对齐 Claude Code 内容
                        #   缩进引导（视觉归组）；窄屏 width<=1 时仅 ``│``
                        #   （1 列）。无边框——竖线是引导线不是边框字符。
                        guide_text = "│ " if width >= 2 else "│"
                        guide_w = 2 if width >= 2 else 1
                        items.append((
                            "content",
                            [StyledRun(guide_text, _GUIDE_STYLE)]
                            + truncate_runs(seg_runs, max(0, width - guide_w)),
                        ))
                    cached = items
                body_cache[key] = cached
            for item in cached:
                kind = item[0]
                if kind == "empty":
                    # ★ BEAUTY-35：空输出行保留竖线引导（视觉连续）；width<=0
                    #   无宽度防御保持空行
                    if width >= 1:
                        guide_text = "│ " if width >= 2 else "│"
                        body_lines.append([StyledRun(guide_text, _GUIDE_STYLE)])
                    else:
                        body_lines.append([StyledRun("", None)])
                    continue
                if kind == "bare":
                    body_lines.append(item[1])
                    continue
                body_lines.append(item[1])
        # find/search/ls/read_file 头显示：后置省略提示行「… 后 N 行省略」
        # （head 省略的行在末尾——提示置于内容行之后，对齐终端 head 语义）
        omitted_head = block.extra.get("_head_omitted_lines", 0)
        if omitted_head > 0:
            body_lines.append(_omitted_line(f"\u2026 后 {omitted_head} 行省略", width))
        block._tool_card_body_lines_cache = (_body_key, body_lines)
    out.extend(body_lines)
    # 状态行：仅关闭块且为最终块；含状态文本（✔ 完成 / ✖ 失败，无边框）。
    # ★ BEAUTY-35（状态行元信息）：追加 ``· N 行 · Xs``（dim 灰）——行数/
    #   耗时由 _tool_output_mixin 记录（open 记 _tool_started_at、close 算
    #   _tool_duration）。窄屏防溢出：状态文本截断至 width。
    if block.closed and (stop is None or stop >= len(block.lines)):
        status = block.extra.get("tool_status", "running")
        if status == "done":
            status_runs = [StyledRun("\u2714 完成", Style(fg=41))]
        elif status == "fail":
            status_runs = [StyledRun("\u2716 失败", Style(fg=196))]
        else:
            status_runs = []
        if status_runs:
            meta = _status_meta(block)
            if meta:
                status_runs.append(StyledRun(f" \u00b7 {meta}", Style(fg=242)))
            out.append(truncate_runs(status_runs, width) if width > 0 else status_runs)
    block._tool_card_frame_cache = (_frame_key, out)
    return out


def _status_meta(block) -> str:
    """状态行元信息（``N 行 · Xs``，dim 灰；无内容/无耗时返回空串）。

    行数 = 内容行数（``_status_line_index - 1``，标题行下标 0；回退
    ``len(lines)-1``——未记录 _status_line_index 的旧块）；耗时来自
    close_tool_box 记录的 ``_tool_duration``。
    """
    parts: list[str] = []
    idx = block.extra.get("_status_line_index")
    if idx is not None:
        n = max(0, idx - 1)
    else:
        n = max(0, len(block.lines) - 1)
    if n > 0:
        parts.append(f"{n} 行")
    dur = block.extra.get("_tool_duration")
    if dur is not None and dur >= 0:
        parts.append(_format_duration(dur))
    return " \u00b7 ".join(parts)


def _format_duration(secs: float) -> str:
    """耗时格式化（<60s：``0.42s``；>=60s：``1m 05s``）。"""
    if secs < 60:
        return f"{secs:.2f}s"
    m = int(secs // 60)
    s = secs - m * 60
    return f"{m}m {s:.1f}s"


def ToolCard(props: dict):
    """React Ink 组件 — 工具调用卡片（工具块渲染为元素树）。

    Props:
        block: 工具块（ChatBlock.kind == "tool"；鸭子类型——读写
            ``block.lines`` / ``block.closed`` / ``block.extra`` 与
            ``_tool_card_*`` 缓存字段）。
        width: 卡片总宽度（终端列宽；<=0 时按无边框裸行防御渲染）。
        start: 起始 AnsiLine 下标（块内行；默认 0——live 增量提交后为
            ``committed_line_count``，仅渲染未提交尾）。
        stop: 结束下标（不含；None 表示到块末尾）。

    Returns:
        Column 元素（标题行 + 内容行 + 状态行，每行一个 TEXT）。
        行宽由 ``tool_card_lines`` 保证 <= width（行级 diff 宽度不变量）。

    组件化语义：ChatView live 路径对未提交工具块用本组件渲染（替代原
    逐行 ``h(TEXT, {"styled": runs})``）；committed 路径（已提交行列表）
    仍由模型层 ``tool_card_lines`` 生成 Line 列表直接发射。内部 TEXT 行带
    索引 key（``tool-{i}``）——开放工具卡追加输出时已渲染行 key 稳定，
    调和器复用 fiber（换行/样式缓存命中）。
    """
    # 惰性 import（保持与 model 层行生成一致的加载模式，避免 app → ink
    # 模块级提前加载影响启动顺序）
    from src.tui.ink import h, TEXT
    from src.tui.ink.widgets.layout import Column
    block = props["block"]
    width = props.get("width", 0)
    start = props.get("start", 0)
    stop = props.get("stop")
    runs_list = tool_card_lines(block, width, start, stop)
    children = [
        h(TEXT, {"key": f"tool-{i}", "styled": runs})
        for i, runs in enumerate(runs_list)
    ]
    return h(Column, None, children)
