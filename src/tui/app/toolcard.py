"""toolcard — ToolCard 工具调用卡片控件（React Ink 组件化）。

工具执行结果卡片（对齐 Claude Code）：顶边框（状态图标 + 工具图标 + 名称 +
detail）+ 主体行（``│ `` 前缀）+ 底边框（``└─ ✔ 完成 ─┘`` / ``└─ ✖ 失败 ─┘``）。

React Ink 组件化（2026-08-05，深度组件化）：原 ``AppModel._tool_card_styled_lines``
（模型层纯函数生成 StyledRun 行）迁移为独立组件模块，模型层不再持有行生成
逻辑：

  - ``tool_card_lines``：纯行生成函数（保留 PERF 缓存语义——开放工具卡按
    块对象缓存 wrap 结果，大工具卡每帧零重建）。供模型层 committed 路径
    （``_block_to_ink_lines``）/ ``close_tool_box`` 顶边框更新等消费；
  - ``ToolCard``：React Ink 函数组件——ChatView live 路径渲染工具块
    （``h(ToolCard, {"block": ..., "width": ..., "start": ...})``），内部
    Column + TEXT 行（行宽由 ``tool_card_lines`` 保证 <= width）。

渲染期变换：不改动 ``block.lines`` 原文（model 测试不变式
``block.lines[0].plain.startswith("  · ")`` / ``strip()=="✔"`` / 无边框
字符依赖此）。顶边框仅 ``start==0``（块首次提交）；底边框仅块关闭且为
最终块（``stop is None`` 或 ``stop >= len(block.lines)``）。关闭状态行
``  ✔``（模型层保留）**不渲染为主体行**——状态移入底边框。

依赖约束：鸭子类型访问 ``block``（ChatBlock 字段：lines/closed/extra），
不 import 模型层（避免循环依赖）；样式来自 app._theme 调色板/呼吸色。
"""

from __future__ import annotations

__all__ = ["ToolCard", "tool_card_lines", "_tool_icon_runs", "_tool_status_index"]


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
    不变式 ``block.lines[-1].plain.strip()=="✔"``）。卡片渲染把状态移到底边框
    （``└─ ✔ 完成 ─┘``，对齐 Claude Code），渲染主体行时跳过该行。
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


def tool_card_lines(block, width, start=0, stop=None):
    """工具卡片渲染期边框行（顶/底边框 + `│ ` 主体行）。

    纯行生成函数（React Ink 组件化迁移自 ``_tool_card_styled_lines``）：
    渲染期变换，不改动 ``block.lines`` 原文（model 测试不变式
    ``block.lines[0].plain.startswith("  · ")`` / ``strip()=="✔"`` / 无边框
    字符依赖此）。顶边框仅 ``start==0``（块首次提交）；底边框仅块关闭且为
    最终块（``stop is None`` 或 ``stop >= len(block.lines)``）。关闭状态行
    ``  ✔``（模型层保留）**不渲染为主体行**——状态移入底边框
    （``└─ ✔ 完成 ─┘`` / ``└─ ✖ 失败 ─┘``，对齐 Claude Code）。

    Args:
        block: 工具块（ChatBlock.kind == "tool"）。
        width: 卡片总宽度（终端列宽）；<=0 时按无边框主体行防御渲染。
        start: 起始 AnsiLine 下标（块内行）。
        stop: 结束下标（不含）；None 表示到块末尾。

    Returns:
        list[list[StyledRun]] — 每行 StyledRun 列表（卡片边框行）。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.core.style import Style
    from src.tui.ink import StyledRun
    from src.tui.ink.helpers import truncate_runs
    from src.tools.registry import get_tool_display_name
    from src.tui._tool_icons import TOOL_ICONS
    from src.renderer.ansi.helpers import wrap_line
    pal = get_active_palette()
    # BEAUTY-10（方向4 动效）：运行中工具卡边框呼吸——开放工具卡顶边框
    #   （live 渲染每帧重建）从暗青 23 脉动到亮青 45（8s 周期），视觉提示
    #   「工具执行中」；已关闭/提交卡保持静态（frozen 缓存不再重算）。
    if block.extra.get("tool_status") == "running" and not block.closed:
        from src.tui.app._theme import time_glow
        border = Style(fg=time_glow(23, 45, 8.0))
    else:
        border = pal.border
    width = width if isinstance(width, int) and width > 0 else 0
    inner_w = max(1, width - 4) if width > 0 else 0
    status_idx = _tool_status_index(block)
    # ★ PERF-6（帧级缓存）：开放工具卡动态色（边框呼吸 23↔45 / 状态图标呼吸
    #   208↔220）为时间基（time_glow 0.1s 桶）——同一桶内帧复用**完整输出
    #   列表对象**（含边框），TEXT 组件 ``_wrap_cache`` 按 styled 引用命中 →
    #   主体行零重建（修复前每帧新建 StyledRun 列表 → TEXT 缓存 miss → 大
    #   工具卡每帧全量 wrap，CPU 100%）。key 覆盖全部动态因素（行数/状态/
    #   宽度/省略计数/呼吸色）；任何变化重建，视觉行为零变化。
    _status = block.extra.get("tool_status", "running")
    _border_fg = border.fg if getattr(border, "fg", None) is not None else -1
    if start == 0 and _status == "running" and not block.closed:
        from src.tui.app._theme import time_glow as _time_glow_icon
        _icon_fg = _time_glow_icon(208, 220, 6.0)
    else:
        _icon_fg = -1
    # ★ BUG-71（review 方向，缓存键完整性）：_frame_key 补充标题字段
    #   （tool_name/tool_detail）——修复前缺标题：open_tool_box 复用 box 更新
    #   标题后，同帧帧缓存（同 start/stop/status/len/呼吸色桶）命中旧标题。
    #   当前仅被运行中边框呼吸色（每 0.1s 桶变化）隐式失效掩盖——若边框改
    #   静态色（未来主题化）立即触发显示陈旧（A5 同族）。
    _frame_key = (
        start, stop, block.closed, _status, len(block.lines),
        _border_fg, _icon_fg,
        block.extra.get("tool_name", ""),
        block.extra.get("tool_detail", ""),
        block.extra.get("_bash_omitted_lines", 0),
        block.extra.get("_head_omitted_lines", 0),
        inner_w,
    )
    _frame_cache = getattr(block, "_tool_card_frame_cache", None)
    if _frame_cache is not None and _frame_cache[0] == _frame_key:
        return _frame_cache[1]
    out: list[list[StyledRun]] = []
    # ★ BUG-26（review 方向）：极端窄屏（width<5）边框降级——边框固定前缀
    #   ``┌─ ``（3 列）+ 右角（1 列）已占满宽度，标题无空间 → 行超宽溢出
    #   （宽度不变量破坏，渲染错位）。降级为无边框裸行（标题行 + 主体内容
    #   行），行宽 ≤ width。
    ultra_narrow = 0 < width < 5
    # 顶边框（仅 start==0）：┌─ ● ⚡ rf[ · detail] ─…─┐
    if start == 0:
        tool_name = block.extra.get("tool_name") or "工具"
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        icon_char = TOOL_ICONS.get(tool_name, "\u2699")
        detail = block.extra.get("tool_detail", "")
        # 标题 runs：状态图标（●/✔/✖）+ 工具图标 + 显示名（+ detail）。
        # 状态图标恒为 title_runs[0] → 顶边框 runs[1]（前缀 `┌─ ` 后），
        # 供 close_tool_box 原位翻转图标。
        title_runs = list(_tool_icon_runs(block))
        # ★ BEAUTY-26（体验动效）：工具图标运行中呼吸——亮白 232→252 脉动
        #   （12s 周期，与 detail 呼吸同步）。运行中的工具图标更生动（与边框
        #   呼吸/● 图标呼吸/detail 呼吸联动）；关闭/提交后保持亮白静态
        #   （frozen 缓存不再重算，零额外渲染成本）。
        if block.extra.get("tool_status") == "running" and not block.closed:
            from src.tui.app._theme import time_glow
            icon_style = Style(fg=time_glow(232, 252, 12.0))
        else:
            icon_style = Style(fg=252)
        title_runs.append(StyledRun(f"{icon_char} ", icon_style))
        title_runs.append(StyledRun(display, Style(fg=252)))
        if detail:
            # ★ BEAUTY-24（体验动效）：工具 detail 运行中呼吸——暗灰 242→252
            #   脉动（12s 周期，与状态栏 token/速度呼吸同步）。运行中的工具
            #   detail 更生动（与边框呼吸/图标呼吸联动）；关闭/提交后保持静态
            #   pal.dim（frozen 缓存不再重算，零额外渲染成本）。
            if block.extra.get("tool_status") == "running" and not block.closed:
                from src.tui.app._theme import time_glow
                title_runs.append(StyledRun(
                    f" \u00b7 {detail}", Style(fg=time_glow(242, 252, 12.0)),
                ))
            else:
                title_runs.append(StyledRun(f" \u00b7 {detail}", pal.dim))
        if ultra_narrow:
            # 降级：无边框裸标题行（截断至 width）
            out.append(truncate_runs(title_runs, width))
        else:
            head = [StyledRun("\u250c\u2500 ", border)]
            if width > 0:
                for run in truncate_runs(title_runs, max(1, width - 4)):
                    head.append(run)
                fill = max(0, width - 1 - sum(r.width for r in head))
                if fill > 0:
                    head.append(StyledRun("\u2500" * fill, border))
                head.append(StyledRun("\u2510", border))
            else:
                head.extend(title_runs)
            out.append(head)
    # 主体行：block.lines[start:stop]，start==0 时跳过标题行（名字已在顶边框）；
    # 关闭状态行（_tool_status_index）跳过——状态已移入底边框（对齐 Claude Code）
    body_end = len(block.lines) if stop is None else min(stop, len(block.lines))
    body_start = start if start > 0 else 1
    # ★ PERF-6b：主体行边框用**静态** pal.border（顶/底边框保持呼吸）——
    #   主体行列表跨帧/跨桶复用（TEXT ``_wrap_cache`` 按 styled 引用命中），
    #   大工具卡跨桶渲染不再每帧全量 wrap（frame_cache 同桶快速路径之外的
    #   兜底：跨桶时 frame_cache miss，但 body_lines 引用不变 → 零重建）。
    body_border = pal.border

    def _omitted_line(text: str) -> list:
        """省略提示边框行（`│ … 前/后 N 行省略`）。

        窄屏防溢出：提示文本（如「… 前 5000 行省略」）超内宽会撑破卡片边框，
        截断至内宽（与顶/底边框一致——不截断时窄终端错乱）。用静态
        body_border（主体行区域边框不呼吸，随 body_lines 跨帧复用）。
        """
        ind_runs = [StyledRun(text, Style(fg=242))]
        if width <= 0:
            return ind_runs
        ind_runs = truncate_runs(ind_runs, inner_w)
        body = [StyledRun("\u2502 ", body_border)] + ind_runs
        pad = inner_w - sum(r.width for r in ind_runs)
        if pad > 0:
            body.append(StyledRun(" " * pad, body_border))
        body.append(StyledRun(" \u2502", body_border))
        return body

    # ★ PERF-6b：主体行（bash omitted 提示 + 主体行循环 + head omitted 提示）
    #   整体缓存（含**静态边框** body_border）——跨帧/跨桶复用列表对象，
    #   TEXT ``_wrap_cache`` 按 styled 引用命中（大工具卡跨桶渲染不再每帧
    #   全量 wrap；frame_cache 同桶快速路径之外的兜底）。key 仅依赖块内容/
    #   宽度/省略计数（不含呼吸色）——行数/宽度/省略变化时自动重建。
    _body_key = (
        start, len(block.lines), body_start, body_end, inner_w, status_idx,
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
            if ultra_narrow:
                body_lines.append([StyledRun(f"\u2026 前 {omitted} 行省略", Style(fg=242))])
            else:
                body_lines.append(_omitted_line(f"\u2026 前 {omitted} 行省略"))
        # ★ PERF-6（性能）：开放工具卡主体行按 ``(行对象, inner_w)`` 缓存
        #   wrap+截断+pad 后的内容 runs——修复前每帧对全部主体行重新
        #   ``wrap_line``（长 bash 输出 300 行 → 单帧 ~190ms → 10Hz 下 CPU
        #   100%）。缓存仅存**内容与 pad**（不含边框色）；边框用静态
        #   body_border（随 body_lines 跨帧复用）。行对象创建后不原地修改
        #   （``append_tool_output`` 每行新建 AnsiLine），width 变化时 inner_w
        #   变 → key miss 自动重算。
        body_cache = getattr(block, "_tool_card_body_cache", None)
        if body_cache is None:
            body_cache = {}
            block._tool_card_body_cache = body_cache
        for abs_idx in range(body_start, body_end):
            if status_idx is not None and abs_idx == status_idx:
                continue
            ansi_line = block.lines[abs_idx]
            key = (ansi_line, inner_w)
            cached = body_cache.get(key)
            if cached is None:
                wrapped = (
                    wrap_line(ansi_line, inner_w)
                    if width > 0
                    else ([ansi_line] if ansi_line.runs else [])
                )
                if not wrapped:
                    # 空输出行 → 有边框空行 `│    │`（保持行映射）
                    cached = [("empty",)]
                else:
                    items: list = []
                    for seg in wrapped:
                        seg_runs = [StyledRun(r.text, r.style) for r in seg.runs if r.text]
                        if width <= 0:
                            items.append(("bare", seg_runs))
                            continue
                        if ultra_narrow:
                            # 降级：无边框裸行（截断至 width）
                            items.append(("narrow", truncate_runs(seg_runs, width)))
                            continue
                        # ★ BUG-29（review 方向）：极端窄屏主体行超宽——``wrap_line``
                        #   在宽度不足以容纳单个 CJK 字符（inner_w=1 < 2）时仍拆出
                        #   宽 2 的段，``│ ``（2）+ seg（2）+ `` │``（2）= 6 > width=5
                        #   → 破坏行级 diff 宽度不变量。修复：seg 宽度超出内宽时先
                        #   截断（``truncate_runs`` 不拆 CJK）再拼边框；pad 按截断后
                        #   宽度计算。
                        content = truncate_runs(seg_runs, inner_w)
                        pad = inner_w - sum(r.width for r in content)
                        items.append(("normal", content, max(0, pad)))
                    cached = items
                body_cache[key] = cached
            for item in cached:
                kind = item[0]
                if kind == "empty":
                    if width > 0:
                        if ultra_narrow:
                            body_lines.append([StyledRun("", None)])
                        else:
                            body_lines.append([StyledRun("\u2502 " + " " * inner_w + " \u2502", body_border)])
                    continue
                if kind == "bare":
                    body_lines.append(item[1])
                    continue
                if kind == "narrow":
                    body_lines.append(item[1])
                    continue
                # normal：内容 runs + pad（静态，已缓存）+ 静态边框拼接
                content, pad = item[1], item[2]
                body = [StyledRun("\u2502 ", body_border)] + content
                if pad > 0:
                    body.append(StyledRun(" " * pad, body_border))
                body.append(StyledRun(" \u2502", body_border))
                body_lines.append(body)
        # find/search/ls/read_file 头显示：后置省略提示行「… 后 N 行省略」
        # （head 省略的行在末尾——提示置于主体行之后，对齐终端 head 语义）
        omitted_head = block.extra.get("_head_omitted_lines", 0)
        if omitted_head > 0:
            if ultra_narrow:
                body_lines.append([StyledRun(f"\u2026 后 {omitted_head} 行省略", Style(fg=242))])
            else:
                body_lines.append(_omitted_line(f"\u2026 后 {omitted_head} 行省略"))
        block._tool_card_body_lines_cache = (_body_key, body_lines)
    out.extend(body_lines)
    # 底边框：仅关闭块且为最终块；含状态文本（对齐 Claude Code `└─ ✔ 完成 ─┘`）
    if block.closed and (stop is None or stop >= len(block.lines)):
        if ultra_narrow:
            # 降级：无边框状态行
            status = block.extra.get("tool_status", "running")
            if status == "done":
                out.append([StyledRun("\u2714", Style(fg=41))])
            elif status == "fail":
                out.append([StyledRun("\u2716", Style(fg=196))])
        elif width > 0:
            tail = [StyledRun("\u2514\u2500 ", border)]
            status = block.extra.get("tool_status", "running")
            if status == "done":
                status_runs = [StyledRun("\u2714 完成", Style(fg=41))]
            elif status == "fail":
                status_runs = [StyledRun("\u2716 失败", Style(fg=196))]
            else:
                status_runs = []
            # 窄屏防溢出：状态文本截断至剩余宽度（└─/┘ 占用 4 列 → 预算 width-4）
            for run in truncate_runs(status_runs, max(1, width - 4)):
                tail.append(run)
            fill = max(0, width - 1 - sum(r.width for r in tail))
            if fill > 0:
                tail.append(StyledRun("\u2500" * fill, border))
            tail.append(StyledRun("\u2518", border))
            out.append(tail)
        else:
            out.append([StyledRun("\u2514\u2518", border)])
    block._tool_card_frame_cache = (_frame_key, out)
    return out


def ToolCard(props: dict):
    """React Ink 组件 — 工具调用卡片（工具块渲染为卡片元素树）。

    Props:
        block: 工具块（ChatBlock.kind == "tool"；鸭子类型——读写
            ``block.lines`` / ``block.closed`` / ``block.extra`` 与
            ``_tool_card_*`` 缓存字段）。
        width: 卡片总宽度（终端列宽；<=0 时按无边框主体行防御渲染）。
        start: 起始 AnsiLine 下标（块内行；默认 0——live 增量提交后为
            ``committed_line_count``，仅渲染未提交尾）。
        stop: 结束下标（不含；None 表示到块末尾）。

    Returns:
        Column 元素（顶边框 + 主体行 + 底边框，每行一个 TEXT）。
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
