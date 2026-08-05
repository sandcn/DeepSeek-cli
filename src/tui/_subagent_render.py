"""SubAgent 面板帧渲染（Layer 0 约束：仅依赖 _const/_config/_tool_icons/_format/app.* 叶模块与同级状态模块）。

方向C 步骤7：从 ``_subagent_panel.SubAgentPanelController`` 上帝类拆出的
帧渲染域——``render_frame`` / ``build_agent_lines`` / ``format_tool_record``
与动效辅助（``_fade_type_style`` / ``_get_tool_color``）。

★ 标准 React Ink 组件化（2026-08-05）：渲染输出从「ANSI 字符串行
（List[str]）」迁移为「ink Line 对象（List[Line]，StyledRun 行）」——彻底
移除 ANSI 中间层：subagent_panel 不再 ``ansi_to_runs`` 解析字符串，直接
``Line.runs`` 转 TEXT 标准组件（``h(TEXT, {"styled": ...})``）。样式统一用
``Style``（fg 色号），与其余 React Ink 组件（StatusBar/ToolCard/UserSelect）
同源。

对齐 Claude Code：子代理活动渲染为**逐 agent 卡片**（标题行 ``● ⚡ map 地图扫描``
+ 内容行 + 状态行 ``✔ 完成``，**无边框**——2026-08-06 用户需求：所有 tool
card 去掉边框，子代理活动卡一并去边框），不再输出汇总行/树形分支/
方括号类型标签。卡片内容宽度自适应（Line.width 测量）。

设计模式: 模板方法（Template Method）— 帧渲染骨架由渲染模块统一提供，
控制器（外观）委托本模块渲染，状态建模在 ``_subagent_state``。

输入约定：
  - ``render_frame(store, max_history)`` 以 ``StateStore`` 为输入，
    内部获取/释放 ``store._state_lock``（RLock 可重入）；
  - 渲染函数不修改状态（只读快照），全部输出为 ink Line 行列表，
    作为「控制器→模型→组件」互换契约（模型 ``subagent_lines`` 存 Line 行）。

依赖约束（P3-11 更新允许清单 + 2026-08-05 公共工具归位）：仅依赖
_const/_config/_tool_icons/events/_format/core(_fx/_theme)/_screen/
ink(Line/StyledRun/Style/truncate_runs) 与**同级状态模块 _subagent_state**
与标准库（无父包依赖、无事件订阅、无 app 域依赖——公共动效/样式工具
``core._fx`` / ``core._theme`` 替代原 ``app._fx`` / ``app._theme``）；
``_tool_icons`` / ``src.tools.registry`` 保持函数内惰性导入（避免模块加载环）。
"""

from __future__ import annotations

import time
from typing import List

from src.tui.core.style import Style
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.helpers import truncate_runs
from src.tui._config import TuiConfig
# ★ 标准 React Ink 组件化（2026-08-05）：配色映射从 ANSI 色串迁移为 Style
# 对象——TOOL_CATEGORY_STYLES / AGENT_TYPE_STYLES（直接取 Style.fg 色号，
# 不再经 _ansi_color_code 解析）。旧 ANSI 映射保留在 _tool_icons（兼容），
# 本模块不再消费。
from src.tui._tool_icons import (
    AGENT_TYPE_STYLES,
    TOOL_CATEGORY_MAP,
    TOOL_CATEGORY_STYLES,
)
from src.tui.core import _fx
from src.tui._format import format_duration, format_tokens, format_speed, single_line

from src.tui._subagent_state import _AgentSlot, _ToolRecord

#: 语义色（与 _const._C_* 值一致：RUNNING=214/DONE=40/FAIL=196/ANSWERING=75/
#: PARSING=178/BATCH=140/DIMMER=240/DIMMEST=238/SUMMARY_DIM=245）
_S_RUNNING = Style(fg=214)          # 琥珀 — 运行中
_S_DONE = Style(fg=40)              # 亮绿 — 完成
_S_FAIL = Style(fg=196)             # 亮红 — 失败
_S_ANSWERING = Style(fg=75)         # 浅蓝 — 回答中
_S_PARSING = Style(fg=178)          # 金色 — 解析
_S_BATCH = Style(fg=140)            # 淡紫 — 批量
_S_DIMMER = Style(fg=240)           # 暗灰 — 辅助
_S_DIMMEST = Style(fg=238)          # 深灰 — 分隔线
_S_SUMMARY_DIM = Style(fg=245)      # 中灰 — 摘要次要

#: spinner 帧序列唯一真源（方向4 收敛至 core._fx.SPINNER_FRAMES；原内联列表
#: 形态保留为 list——兼容既有测试 ``_SPINNER_FRAMES[i]`` 下标访问与 patch 路径）。
from src.tui.core._fx import SPINNER_FRAMES as _SPINNER_FRAMES_SRC
_SPINNER_FRAMES = list(_SPINNER_FRAMES_SRC)
_INDENT = "  "


def _fade_type_style(agent_type: str, elapsed: float) -> Style:
    """agent 类型名 FadeIn 渐显（BEAUTY-1，返回 Style）。

    时间基：elapsed>=duration 时返回原色（动画结束不触发重绘）；
    elapsed 期间从 ``_FADE_START_COLOR`` 渐变到原色号。

    ★ 标准 React Ink 组件化：直接查 ``AGENT_TYPE_STYLES``（Style 映射）取
    色号——不再经 ANSI 色串 + ``_ansi_color_code`` 解析（中间层已随死代码
    清理移除，2026-08-05）。
    参数更名 ``agent_type_ansi`` → ``agent_type``（原传 ANSI 色串，现传
    类型名）；未知类型回退 ``_S_DIMMER``（与原 ``code is None`` 分支一致）。
    """
    style = AGENT_TYPE_STYLES.get(agent_type)
    if style is None or getattr(style, "fg", None) is None:
        return _S_DIMMER
    code = style.fg
    faded = _fx.fade_color(elapsed, _FADE_DURATION, _FADE_START_COLOR, code)
    return Style(fg=faded)


def _get_tool_color(tool_name: str) -> Style:
    """查询工具类别配色（共享单一真源映射，_tool_icons.TOOL_CATEGORY_MAP/STYLES）。

    ★ 标准 React Ink 组件化：直接返回 **Style**（fg 色号）——查询
    ``TOOL_CATEGORY_STYLES`` 映射，不再经 ANSI 色串解析。未知工具回退
    ``Style(fg=245)``（与原 ANSI 默认色号一致）。线程安全只读。
    """
    cat = TOOL_CATEGORY_MAP.get(tool_name, "")
    style = TOOL_CATEGORY_STYLES.get(cat)
    if style is not None:
        return style
    return Style(fg=245)


# ═══════════════════════════════════════════════════════════
# 帧渲染（单卡合并，对齐 Claude Code 子代理 Task 卡 + 终端行数保护）
# ═══════════════════════════════════════════════════════════

# ── 动效时间基配置 ──
_CFG = TuiConfig.defaults()
_FADE_DURATION: float = _CFG.fade_duration_sec       # FadeIn 渐显总时长（0.6s）
_FADE_START_COLOR: int = _CFG.fade_start_color       # FadeIn 起始暗色（238）
_SPINNER_HZ: float = _CFG.spinner_tick_hz            # spinner 时间基推进频率（10Hz）


def render_frame(store, max_history: int = 3,
                 agents: dict | None = None,
                 order: list | None = None,
                 max_lines: int | None = None) -> List[Line]:
    """渲染面板帧（所有 Agent 合并为一个卡片，含终端行数保护）。

    Args:
        store: ``StateStore`` 状态存储（内部取锁读取快照）。
        max_history: 工具历史展示条数上限（来自控制器构造参数）。
        agents: 可选状态字典覆盖（兼容控制器测试直接替换
            ``ctrl._agents`` 引用的场景；None 时用 ``store._agents``）。
        order: 可选顺序列表覆盖（同上；None 时用 ``store._order``）。
        max_lines: 卡片最大总行数（终端行数保护）；None 时按终端高度推算。

    Returns:
        单卡片 Line 行列表；无 agent 时返回空列表。
    """
    # 控制器（外观）可整体替换 _agents/_order 引用（既有测试模式）——
    # 渲染以调用方传入的当前引用为准，锁仍取自 store（RLock 可重入）。
    agents = store._agents if agents is None else agents
    order = store._order if order is None else order
    with store._state_lock:
        if not agents:
            return []
        now = time.time()
        rows: list[tuple[str, str, List[Line]]] = []
        for label in order:
            slot = agents.get(label)
            if slot is None:
                continue
            lines = build_agent_lines(slot, now, is_last=True, max_history=max_history)
            if not lines:
                continue
            rows.append((slot.status, lines[0], lines[1:]))
        if not rows:
            return []
        return _build_group_card(rows, now, max_lines)


def _terminal_max_lines() -> int:
    """按终端高度计算卡片最大行数（预留顶部标题栏 + 状态栏 + 输入区 + 边距）。

    ★ 性能（方向4）：终端高度经 ``TerminalWidthCache`` 读取（TTL 缓存）——
    修复前每次渲染直接 ``_get_terminal_size()``（fcntl.ioctl），subagent
    面板 10Hz 刷新时每帧 2 次系统调用。终端尺寸查询失败回退 12（行数保护
    兜底）。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, h - 6)
    except Exception:
        return 12


def _terminal_max_width() -> int:
    """当前终端宽度（卡片宽度 clamp 上限，方向3：防卡片比终端宽致边框截断）。

    ★ 性能（方向4）：终端宽度经 ``TerminalWidthCache`` 读取（TTL 缓存）——
    修复前每次渲染直接 ``_get_terminal_size()``（fcntl.ioctl），subagent
    面板 10Hz 刷新时每帧系统调用。终端尺寸查询失败回退 80。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        w = TerminalWidthCache.get_default().get_width()
        return max(20, w)
    except Exception:
        return 80


def _build_group_card(rows: list[tuple[str, str, List[Line]]],
                      now: float,
                      max_lines: int | None = None) -> List[Line]:
    """构建子代理组卡片（所有 Agent 合并为一个卡，内容宽度自适应，**无边框**）。

    对齐 Claude Code：标题行（``●/✔ ⚡ 子代理 · N``）+ 各 agent 行（running
    优先并展开阶段/工具子行，done/fail 为单行）+ 状态行（``✔ 完成``，全部
    结束）。**行数保护**：卡片总行数 ≤ max_lines（终端高度推算），超限截断
    并追加 ``… +K 行省略`` 提示——防卡片撑爆终端可视区。
    """
    if max_lines is None:
        max_lines = _terminal_max_lines()
    n = len(rows)
    any_running = any(st == "running" for st, _, _ in rows)
    # 标题：●/✔ ⚡ 子代理 · N（⚡ 为 subagent 图标，对齐 Claude Code Task 卡）。
    # 运行中 ● 状态图标呼吸（琥珀 208-220 脉动，BEAUTY-11 语义——2026-08-06
    # 去边框后呼吸由状态图标承接，与工具卡状态图标呼吸一致）；全部完成（closed）
    # 保持静态 _S_DONE（零额外渲染成本）。
    status_icon = "\u25cf" if any_running else "\u2714"
    if any_running:
        icon_style = _running_pulse_style()
    else:
        icon_style = _S_DONE
    title: List[StyledRun] = [
        StyledRun(status_icon, icon_style),
        StyledRun(" ", None),
        StyledRun("\u26a1", _S_RUNNING),
        StyledRun(f" 子代理 \u00b7 {n}", None),
    ]
    # 主体行：running 优先（标题 + 子行），done/fail 单行（后置）。
    # 无边框：子行直接裸行输出（2026-08-06 去边框，不再加 `│ ` 前缀）。
    body: List[Line] = []
    for status, t, sublines in rows:
        if status == "running":
            body.append(t)
            for s in sublines:
                body.append(s)
    for status, t, sublines in rows:
        if status != "running":
            body.append(t)
    # 行数保护：卡片总行数（标题 + 主体 + 状态）≤ max_lines
    closed = not any_running
    budget = max_lines - (2 if closed else 1)
    if len(body) > budget:
        kept = max(1, budget - 1)  # 预留省略提示行
        dropped = len(body) - kept
        # ★ BEAUTY-34（2026-08-05 体验动效）：省略提示呼吸——运行中组卡
        #   浅蓝 110→120 脉动（12s 周期，与状态栏耗时呼吸同步）；空闲静态
        #   _S_DIMMER（零额外渲染成本）。
        if any_running:
            from src.tui.core._theme import time_glow
            omit_style = Style(fg=time_glow(110, 120, 12.0))
        else:
            omit_style = _S_DIMMER
        body = body[:kept] + [Line([
            StyledRun("\u2026", omit_style),
            StyledRun(f" +{dropped} 行省略", omit_style),
        ])]
    # 组装卡片（无边框裸行；宽度 clamp 到终端宽度——超长内容截断防撑爆）
    widths = [sum(r.width for r in title)] + [ln.width for ln in body]
    if closed:
        status_text = [StyledRun("\u2714 完成", _S_DONE)]
        widths.append(sum(r.width for r in status_text))
    card_w = min(max(widths) if widths else 0, _terminal_max_width())
    out: List[Line] = []
    out.append(Line(truncate_runs(title, card_w)))
    for ln in body:
        out.append(Line(truncate_runs(ln.runs, card_w)))
    if closed:
        out.append(Line(truncate_runs(status_text, card_w)))
    return out


def build_agent_lines(slot: _AgentSlot, now: float, is_last: bool,
                      max_history: int = 3) -> List[Line]:
    """构建单个 Agent 的内容行（标题 + 阶段指示 + 工具历史，无树形分支）。

    首行为卡片标题行（状态图标 + 类型名 + 描述 + 统计，无边框）；其余为
    卡片内容行（阶段指示 + 工具记录）。``is_last`` 保留兼容参数（无分支后
    不再使用）。
    """
    lines: List[Line] = []
    elapsed = (slot.end_time or now) - slot.start_time
    elapsed_str = format_duration(elapsed)
    disp_out = slot.output_tokens + slot.live_output_tokens
    output_str = format_tokens(disp_out)
    speed_str = format_speed(slot.last_speed) if slot.status == "running" else ""
    # ★ BEAUTY-23（体验动效）：running 统计呼吸色统一惰性导入（time_glow
    #   0.1s 桶缓存——函数级导入避免模块加载环；仅 running 分支消费）。
    from src.tui.core._theme import time_glow as _tg

    # ── 类型名（BEAUTY-1：FadeIn 渐显，时间基；无 `[xx]` 方括号标签） ──
    # ★ 标准 React Ink 组件化：AGENT_TYPE_STYLES 导入为模块级（顶部）
    #   ——fade 查 Style 映射取色号（不再经 ANSI 色串解析）。
    type_name = slot.agent_type or "??"
    fade_elapsed = time.monotonic() - slot.appear_time
    type_style = _fade_type_style(slot.agent_type, fade_elapsed)

    # ── 状态图标 + 标题行 ──
    # P3-?：description 经 _single_line 转义（可能含 \n → 强制单行显示）
    description = _single_line(slot.description)
    if slot.status == "done":
        icon = StyledRun("\u2714", _S_DONE)
        suffix: List[StyledRun] = [
            StyledRun("  ", None),
            StyledRun(output_str, _S_DIMMER),
            StyledRun("  ", None),
            StyledRun(elapsed_str, _S_DIMMER),
        ]
        title = [icon, StyledRun(" ", None), StyledRun(type_name, type_style),
                 StyledRun(f" {description}", None)] + suffix
    elif slot.status == "fail":
        icon = StyledRun("\u2716", _S_FAIL)
        suffix = [StyledRun("  ", None), StyledRun(elapsed_str, _S_DIMMER)]
        title = [icon, StyledRun(" ", None), StyledRun(type_name, type_style),
                 StyledRun(f" {description}", None)] + suffix
    else:
        # BEAUTY-3：spinner 时间基推进（非帧计数；_frame 字段保留兼容）
        # ★ 方向4：帧字符唯一真源 _fx.spinner_char（_SPINNER_FRAMES 别名
        #   保留兼容测试 patch 路径；值同 _fx.SPINNER_FRAMES）。
        spinner = _fx.spinner_char(_SPINNER_HZ)
        dot = StyledRun(spinner, _S_RUNNING)
        # ★ BEAUTY-23（体验动效）：running 期间输出/speed/耗时统计呼吸——
        #   输出量浅蓝 240→250、速度亮青 45→55、耗时暗灰 240→250（12s 周期，
        #   与状态栏 token/速度呼吸同步）。active 子代理面板 10Hz 刷新，
        #   time_glow 0.1s 桶缓存平滑推进；done/fail 折叠为单行保持静态。
        suffix = [
            StyledRun("  ", None),
            StyledRun(output_str, Style(fg=_tg(240, 250, 12.0))),
            StyledRun("  ", None),
            StyledRun(speed_str, Style(fg=_tg(45, 55, 12.0))),
            StyledRun("  ", None),
            StyledRun(elapsed_str, Style(fg=_tg(240, 250, 12.0))),
        ]
        title = [dot, StyledRun(" ", None), StyledRun(type_name, type_style),
                 StyledRun(f" {description}", None)] + suffix
    lines.append(Line(title))

    # ── 阶段指示 ──
    if slot.status == "running" and slot.model_phase:
        phase_elapsed = now - slot.model_phase_start if slot.model_phase_start else 0
        phase_time = f"{phase_elapsed:.1f}s"
        if slot.model_phase == "thinking":
            lines.append(Line([
                StyledRun("\u2026thinking", _S_DIMMER),
                StyledRun("  ", None),
                StyledRun(phase_time, None),
            ]))
        elif slot.model_phase == "answering":
            lines.append(Line([
                StyledRun("\u2026answering", _S_ANSWERING),
                StyledRun("  ", None),
                StyledRun(phase_time, _S_DIMMER),
            ]))
        # ★ BUG-T5：parsing 阶段不再追加独立 ``…parsing`` 行——由 parsing 工具
        #   记录行（``○`` 前缀）表达解析状态；解析进度摘要（parse_info）经
        #   ``format_tool_record`` 并入该记录行。修复前独立阶段行使工具开始瞬间
        #   面板高度 +2（阶段行 + 记录行）→ ``start_tool`` 清除 model_phase 后
        #   -1（阶段行消失），文档高于屏幕时 InkRenderer 对缩短做**全量
        #   clear + 重建**——每次 subagent 调用 search 等工具 TUI 全量刷新闪烁。
        elif slot.model_phase == "batch":
            lines.append(Line([
                StyledRun("\u2026batch", _S_BATCH),
                StyledRun("  ", None),
                StyledRun(_single_line(slot.model_info), _S_DIMMER),
                StyledRun("  ", None),
                StyledRun(phase_time, _S_DIMMER),
            ]))

    # ── 工具历史（仅 running 时展开；done/fail 折叠为单行） ──
    if slot.status not in ("done", "fail"):
        history = slot.tool_history[-max_history:]
        for rec in reversed(history):
            parse_info = slot.parse_info if rec.phase == "parsing" else ""
            lines.append(format_tool_record(rec, now, "", parse_info=parse_info))
    return lines


def format_tool_record(rec: _ToolRecord, now: float, cont: str = "",
                       parse_info: str = "") -> Line:
    """构建工具历史单行（无树形分支；``cont`` 保留兼容参数不再使用）。

    Args:
        rec: 工具记录。
        now: 当前时间戳。
        cont: 保留兼容参数（不再使用）。
        parse_info: 解析进度摘要（如 ``"rf,rf 51t 0.74s"``）——仅 parsing
            记录附加到该行。修复前为独立 ``…parsing`` 阶段行（``build_agent_lines``
            追加），工具开始瞬间引起面板高度 +2 → -1 波动 → 缩短全量重建。
    """
    elapsed = (rec.end_time or now) - rec.start_time if rec.start_time else 0
    time_str = f"{elapsed:.1f}s"
    detail = _single_line(rec.detail)

    from ._tool_icons import TOOL_ICONS
    from src.tools.registry import get_tool_display_name
    tool_icon = TOOL_ICONS.get(rec.tool_name, "")
    display_name = get_tool_display_name(rec.tool_name)
    tool_style = _get_tool_color(rec.tool_name)
    tool_abbr: List[StyledRun] = []
    if tool_icon:
        tool_abbr.append(StyledRun(f"{tool_icon} ", None))
    tool_abbr.append(StyledRun(display_name, tool_style))

    runs: List[StyledRun] = []
    if rec.phase == "parsing":
        # ★ BUG-T5：parsing 记录行合并解析进度摘要（不产生独立阶段行）——
        #   修复前 build_agent_lines 额外追加 ``…parsing`` 独立行：工具开始
        #   瞬间面板 +2 行，start_tool 清除 model_phase 后 -1 行（缩短）。
        #   文档高于屏幕时 InkRenderer 对缩短做全量 clear + 重建 → 每次
        #   subagent 调用 search 等工具 TUI 全量刷新闪烁。
        extra_parts = [p for p in (detail, _single_line(parse_info)) if p]
        extra = "  ".join(extra_parts) if extra_parts else ""
        detail_disp = [StyledRun(" ", None), StyledRun(extra, _S_DIMMER)] if extra else []
        runs = [StyledRun("\u25cc", _S_PARSING), StyledRun(" ", None)] + tool_abbr + detail_disp
    else:
        detail_disp = [StyledRun(" ", None), StyledRun(detail, _S_DIMMER)] if detail else []
        if rec.phase == "running":
            # 方向4（动效）：running ● 呼吸色（琥珀 208-220 脉动）——替代静态
            # _S_RUNNING（214）。时间基呼吸（time_glow 0.1s 桶缓存）。
            pulse_style = _running_pulse_style()
            runs = [StyledRun("\u25cf", pulse_style), StyledRun(" ", None)] + tool_abbr + detail_disp + [
                StyledRun("  ", None), StyledRun(time_str, _S_DIMMER),
            ]
        elif rec.phase == "done":
            runs = [StyledRun("\u2714", _S_DONE), StyledRun(" ", None)] + tool_abbr + detail_disp + [
                StyledRun("  ", None), StyledRun(time_str, _S_DIMMER),
            ]
        else:  # fail
            runs = [StyledRun("\u2716", _S_FAIL), StyledRun(" ", None)] + tool_abbr + detail_disp + [
                StyledRun("  ", None), StyledRun(time_str, _S_DIMMER),
            ]
    return Line(runs)


def _running_pulse_style() -> Style:
    """running 状态 ● 呼吸色（方向4 动效：琥珀 208-220 脉动，6s 周期）。

    时间基（``time_glow`` 0.1s 桶缓存），subagent 面板 10Hz 刷新时平滑推进。
    """
    from src.tui.core._theme import time_glow
    return Style(fg=time_glow(208, 220, 6.0))


def _single_line(text: str) -> str:
    """确保单行显示：将换行/回车转义为字面量（subagent 行契约=单行）。

    每个 ``subagent_lines`` 条目应为一条终端行；来源字段（description /
    parse_info / model_info / tool detail）可能含 ``\n``/``\r``，直接插入
    会使终端按换行渲染成两行。与 ``format_tool_record`` 既有转义一致，
    转义为可见字面量 ``\\n``/``\\r``。★ 方向5：委托 ``_format.single_line``
    单一真源（三处单行契约收敛——model/_subagent_render/subagent_panel）。
    """
    return single_line(text)


__all__ = [
    "_SPINNER_FRAMES",
    "_get_tool_color",
    "render_frame",
    "build_agent_lines",
    "format_tool_record",
]
