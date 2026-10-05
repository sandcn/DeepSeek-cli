"""toolcard — ToolCard 工具调用卡片控件（React Ink 组件化）。

工具执行结果卡片（完整对齐 Claude Code，2026-08-06 用户需求）：标题行
（状态图标 + 类别色工具名 + 参数）+ 内容行（``│ `` 竖线引导）。**无边框、
极简**——卡片以「裸行」呈现，不再绘制 ``┌─…┐`` / ``│…│`` / ``└─…┘``
边框字符；``│`` 为**内容竖线引导线**（Claude Code 风格视觉归组，非边框）。

对齐 Claude Code 的极简样式（方案 A，2026-08-06）：
  - 标题行：``状态图标 + 工具名 + 参数``（如 ``● Bash ls -la``）——去掉
    ▎ 引导线、去掉 emoji 工具图标、detail 用空格分隔（Claude Code
    ``ReadFile src/main.py`` 语义，非 ``·``）；状态图标恒为 runs[0]
    （close_tool_box 原位翻转图标与 ``startswith(●/✔/✖)`` 测试不变式依赖）；
  - 工具名类别配色：唯一真源 ``_tool_icons.TOOL_CATEGORY_STYLES``（shell 绿 /
    file_read 浅蓝 / file_write 粉 / search 金 / agent 蓝 / interact 青 /
    delete 红），运行中在类别色邻域 12s 脉动呼吸（与 detail 呼吸同步），
    关闭后静态类别色；工具名加粗（标题强化）；
  - 参数（detail）：运行中暗灰 242→252 呼吸（12s），关闭后静态 pal.dim；
  - 内容行：每内容行前置 ``│ ``（深灰 238），窄屏截断保证总宽 <= width；
    所有行（标题/内容/省略/空行）经 ``_apply_line_bg`` 统一钳制——总宽超过
    width 时截断到 width 并追加省略号 ``…``（「显示一行超过终端宽度就截断到
    最大宽度 + 增加…」，防终端自动换行错位并提示内容被截断）；
  - **无独立状态行**（Claude Code 无 ``✔ 完成 · N 行 · Xs``）——状态由
    标题行状态图标表达（● 运行中 / ✔ 完成 / ✖ 失败）；模型层 close_tool_box
    追加的 ``  ✔``/``  ✖`` 数据行渲染时跳过（``_tool_status_index``）。

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
标题行仅 ``start==0``（块首次提交）。关闭状态行 ``  ✔``（模型层保留）
**不渲染为内容行**——状态移入标题行图标（``_tool_status_index`` 跳过）。

依赖约束：鸭子类型访问 ``block``（ChatBlock 字段：lines/closed/extra），
不 import 模型层（避免循环依赖）；样式来自 app._theme 调色板/呼吸色。
"""

from __future__ import annotations

# core.style 为 Layer 0 底层（无 app 依赖），模块级 import 无循环风险；
# 用于模块级样式常量（_GUIDE_STYLE / _CATEGORY_DEFAULT_STYLE）。
from src.tui.core.style import Style

__all__ = ["ToolCard", "tool_card_lines", "_tool_icon_runs", "_tool_status_index"]

# ── 工具类别配色（BEAUTY-35，2026-08-06 美化） ─────────────────────
# 标题工具名按工具类别着色（Claude Code 极简样式后不再有 ▎引导线/emoji
# 工具图标）；运行中在类别色邻域脉动呼吸（12s 周期，与 detail 呼吸同步——
# 视觉联动），关闭后静态类别色。
# 呼吸区间下限 = 静态类别色号（_tool_icons.TOOL_CATEGORY_STYLES 同值），
# 上限为更亮色号（呼吸峰值）。未知名工具兜底暗灰→亮白。
_CATEGORY_BREATH: dict[str, tuple[int, int]] = {
    "shell":      (41, 49),    # 绿 → 亮绿
    "file_read":  (81, 87),    # 浅蓝 → 亮蓝
    "file_write": (213, 219),  # 粉 → 亮粉
    "search":     (221, 229),  # 金 → 亮金
    "agent":      (75, 81),    # 蓝 → 亮蓝
    "interact":   (51, 87),    # 青 → 亮青
    "delete":     (203, 210),  # 红 → 亮红
}
#: 未知名工具兜底样式 / 呼吸区间兜底快照。
#: 「一切皆插件」：默认值来自表现层数据注册表（``ui_defaults`` 表 →
#: ``tool_fallback_fg`` / ``tool_fallback_breath``），可按 Patch/Overlay 覆盖
#: 或禁用；此处为兜底字面量（Style 对象按色号缓存，避免每帧重建）。
_CATEGORY_DEFAULT_FG = 242
_CATEGORY_DEFAULT_BREATH = (242, 252)
_GUIDE_STYLE = Style(fg=238)                # 内容竖线引导色（深灰，低调）
#: 工具卡满宽背景色（256 色号；默认深灰 236）。
#: 「工具卡整行占满终端宽度」（2026-10-05 用户需求）：标题行/内容行/省略行
#: 右侧以背景色空格填充至终端宽度，视觉上撑满终端（对齐 Claude Code 工具卡
#: 满宽背景）——即使内容较短也延伸到终端右边缘，终端宽度变化时随新宽度重排。
#: 「一切皆插件」：默认值来自表现层数据注册表（``ui_defaults`` 表 →
#: ``tool_card_bg``），可按 Patch/Overlay 覆盖或禁用（禁用后回退本字面量）。
_CARD_BG_DEFAULT = 236

_fallback_style_cache: dict[int, Style] = {}
_bg_style_cache: dict[int, Style] = {}


def _ui_int(key: str, default: int) -> int:
    from src.presentation_data import ui_default

    value = ui_default(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _category_default_style() -> Style:
    """未知名工具兜底 Style（数据注册表优先；按色号缓存复用对象）。"""
    fg = _ui_int("tool_fallback_fg", _CATEGORY_DEFAULT_FG)
    style = _fallback_style_cache.get(fg)
    if style is None:
        style = Style(fg=fg)
        _fallback_style_cache[fg] = style
    return style


def _category_default_breath() -> tuple[int, int]:
    """未知名工具呼吸区间（数据注册表优先，非法时回退兜底快照）。"""
    from src.presentation_data import ui_default

    value = ui_default("tool_fallback_breath", _CATEGORY_DEFAULT_BREATH)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            pass
    return _CATEGORY_DEFAULT_BREATH


def _card_bg_style() -> Style:
    """工具卡满宽背景 Style（数据注册表优先；按色号缓存复用对象）。"""
    bg = _ui_int("tool_card_bg", _CARD_BG_DEFAULT)
    style = _bg_style_cache.get(bg)
    if style is None:
        style = Style(bg=bg)
        _bg_style_cache[bg] = style
    return style


def _apply_line_bg(runs: list, width: int, bg_style: Style) -> list:
    """给整行应用工具卡背景并填充至 width（「整行占满终端宽度」）。

    2026-10-05 用户需求：工具卡每行（标题/内容/省略行）整行带背景色，内容
    较短时行尾以背景色空格填充到终端宽度——整行（含背景）延伸到终端右边缘，
    即使内容较短；已有背景的 run 保持自身背景不被覆盖。

    ★ 行宽硬上限（2026-10-05 用户需求「显示一行超过终端宽度就截断到最大
    宽度」+「增加…」）：``runs`` 总宽**超过 width** 时先
    ``truncate_runs_ellipsis`` 截断到 width 并在末尾追加省略号 ``…``
    （截断点不拆宽字符），再应用背景/填充——工具卡任何「显示一行」都
    不超过终端宽度，即使上游漏截断（含制表符等「计算宽度 < 实际渲染宽度」
    的历史问题）也不会触发终端自动换行、后续行错位；末尾 ``…`` 明确提示
    「此处内容被截断」。本函数是工具卡所有行（标题/内容/省略/空行）输出的
    **最后一道行宽钳制**。

    ``width <= 0``（无宽度上下文）时原样返回（不新建列表，零额外分配）。
    """
    if width <= 0:
        return runs
    from src.tui.ink import StyledRun
    # ★ 行宽硬上限：总宽超 width → 先截断并追加省略号（上游已按 width 预算
    #   截断/换行的行不会触发；仅兜底漏截断路径）。
    used = 0
    for r in runs:
        used += r.width
    if used > width:
        from src.tui.ink.helpers import truncate_runs_ellipsis
        runs = truncate_runs_ellipsis(runs, width)
    out: list = []
    used = 0
    for r in runs:
        st = r.style
        if st is None:
            st = bg_style
        elif st.bg is None:
            st = st.merge(bg_style)
        out.append(r if st is r.style else StyledRun(r.text, st))
        used += r.width
    if used < width:
        out.append(StyledRun(" " * (width - used), bg_style))
    return out


def _category_style(tool_name: str) -> Style:
    """工具类别静态 Style（唯一真源 ``_tool_icons.TOOL_CATEGORY_STYLES``）。

    Args:
        tool_name: 工具名（TOOL_CATEGORY_MAP 键；未知名返回 dim 兜底）。

    Returns:
        类别 Style（frozen 对象，可复用）。
    """
    from src.tui._tool_styles import tool_style
    return tool_style(tool_name) or _category_default_style()


def _category_breath_fg(tool_name: str) -> int:
    """工具类别呼吸色号（运行中：类别色邻域 12s 脉动）。

    Args:
        tool_name: 工具名（TOOL_CATEGORY_MAP 键；未知名回退暗灰→亮白）。

    Returns:
        [lo, hi] 区间内的 256 色号（与 detail 呼吸同周期，视觉联动）。
    """
    from src.tui.app._theme import time_glow
    from src.tui._tool_styles import tool_category
    cat = tool_category(tool_name)
    lo, hi = _CATEGORY_BREATH.get(cat, _category_default_breath())
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
    不变式 ``block.lines[-1].plain.strip()=="✔"``）。Claude Code 极简样式下
    状态由标题行状态图标表达（●/✔/✖），渲染内容行时跳过该数据行。
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


def _omitted_line(text: str, width: int, bg_style: Style) -> list:
    """省略提示行（``│ … 前/后 N 行省略``，无边框——BEAUTY-35 带竖线引导）。

    窄屏防溢出：提示文本超宽时截断至 width 并追加省略号 ``…``（与标题/内容行
    一致——不截断时窄终端错乱；竖线引导占用 2 列，内容截断至 width-2）。
    行尾以背景色空格填充至 width（「整行占满终端宽度」，2026-10-05 用户需求）。
    函数内惰性 import（与 tool_card_lines 同模式）。
    """
    from src.tui.ink import StyledRun
    from src.tui.ink.helpers import truncate_runs_ellipsis
    guide = [StyledRun("│ ", _GUIDE_STYLE)]
    if width <= 0:
        # ★ P3（review）：无宽度上下文（width<=0）时仅返回竖线引导——修复前
        #   返回 ``guide + 全文``（行宽 2+len(text)），与函数自身声明的
        #   「提示文本超宽时截断至 width」冲突（破坏行宽不变量，靠帧级守卫
        #   兜底）。空/极窄宽度下不渲染文本。
        return []
    if width == 1:
        # 极端窄屏：仅竖线（1 列）
        return _apply_line_bg([StyledRun("│", _GUIDE_STYLE)], width, bg_style)
    return _apply_line_bg(
        guide + truncate_runs_ellipsis([StyledRun(text, Style(fg=242))], width - 2),
        width, bg_style,
    )


def tool_card_lines(block, width, start=0, stop=None):
    """工具卡片渲染期行（标题行 + 内容行，**无边框、无独立状态行**）。

    纯行生成函数（React Ink 组件化迁移自 ``_tool_card_styled_lines``）：
    渲染期变换，不改动 ``block.lines`` 原文（model 测试不变式
    ``block.lines[0].plain.startswith("  · ")`` / ``strip()=="✔"`` 依赖此）。
    标题行仅 ``start==0``（块首次提交）。关闭状态行 ``  ✔``（模型层保留）
    **不渲染为内容行**——状态移入标题行状态图标（``_tool_status_index``
    跳过该数据行）。

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
    from src.tui.ink.helpers import truncate_runs_ellipsis
    from src.tools.registry import get_tool_display_name
    from src.renderer.ansi.helpers import wrap_line
    pal = get_active_palette()
    width = width if isinstance(width, int) and width > 0 else 0
    bg_style = _card_bg_style()
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
        len(block.extra.get("_chat_hidden_lines") or ()),
        width, bg_style.bg,
    )
    _frame_cache = getattr(block, "_tool_card_frame_cache", None)
    if _frame_cache is not None and _frame_cache[0] == _frame_key:
        return _frame_cache[1]
    out: list[list[StyledRun]] = []
    # 标题行（仅 start==0）：状态图标 + 工具名 + 参数（Claude Code 极简）。
    # 状态图标恒为 title_runs[0] → 标题行 runs[0]，供 close_tool_box 原位
    # 翻转图标（无边框前缀，runs[0] 即状态图标）。
    if start == 0:
        tool_name = block.extra.get("tool_name") or "工具"
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        detail = block.extra.get("tool_detail", "")
        title_runs = list(_tool_icon_runs(block))
        running = _status == "running" and not block.closed
        # ★ Claude Code 极简样式（2026-08-06 用户需求）：标题行 = 状态图标 +
        #   工具名（类别色，加粗）+ 参数（空格分隔，dim）——去掉 ▎ 引导线、
        #   emoji 工具图标（工具注册名语义，如 ``ReadFile src/main.py``）。工具名
        #   按类别着色——运行中在类别色邻域呼吸（12s 周期，与 detail 呼吸
        #   同步；同 _cat_fg 值，整体同色脉动），关闭/提交后静态类别色
        #   （frozen 缓存不再重算，零额外渲染成本）。runs[0] 保持状态图标
        #   （close_tool_box 原位翻转 + 测试 startswith(●/✔/✖) 不变式）。
        if running:
            cat_fg = _cat_fg
        else:
            cat_style = _category_style(tool_name)
            cat_fg = cat_style.fg if cat_style.fg is not None else 242
        title_runs.append(StyledRun(display, Style(fg=cat_fg, bold=True)))
        if detail:
            # ★ BEAUTY-24（体验动效）：工具 detail 运行中呼吸——暗灰 242→252
            #   脉动（12s 周期，与状态栏 token/速度呼吸同步）。运行中的工具
            #   detail 更生动；关闭/提交后保持静态 pal.dim（frozen 缓存不再
            #   重算，零额外渲染成本）。空格分隔（Claude Code ``Bash ls -la``
            #   语义——非 ``·``）。
            if running:
                from src.tui.app._theme import time_glow
                title_runs.append(StyledRun(
                    f" {detail}", Style(fg=time_glow(242, 252, 12.0)),
                ))
            else:
                title_runs.append(StyledRun(f" {detail}", pal.dim))
        # ★ 标题行超宽截断 + 末尾省略号（2026-10-05 用户需求「截断到最大
        #   宽度 + 增加…」）：标题行（图标 + 工具名 + 参数）超过 width 时
        #   截断到 width 并以 ``…`` 收尾（提示参数被截断）。
        out.append(_apply_line_bg(
            truncate_runs_ellipsis(title_runs, width) if width > 0 else title_runs,
            width, bg_style,
        ))
    # 内容行：block.lines[start:stop]，start==0 时跳过标题行（名字已在标题行）；
    # 关闭状态行数据行（_tool_status_index）跳过——状态由标题行状态图标表达
    body_end = len(block.lines) if stop is None else min(stop, len(block.lines))
    body_start = start if start > 0 else 1
    # ★ 用户需求（read_file 聊天卡只显示标题行）：聊天卡隐藏行集合
    #   （``block.extra["_chat_hidden_lines"]`` 行对象引用，由
    #   ``append_tool_output(chat_hidden=True)`` 登记）——按 id() 判定跳过；
    #   数据仍保留在 block.lines（轨迹 Trace / 详情视图可见）。
    _hidden_rows = block.extra.get("_chat_hidden_lines")
    _hidden_ids = {id(l) for l in _hidden_rows} if _hidden_rows else None
    # 全隐藏判定（read_file 成功读取：body 全部为隐藏行）——跳过省略提示行
    # （否则聊天卡残留「… 后 N 行省略」，破坏「只显示标题行」语义）。
    _hidden_body_count = 0
    _body_count = 0
    if _hidden_ids is not None:
        for _i in range(body_start, body_end):
            if status_idx is not None and _i == status_idx:
                continue
            _body_count += 1
            if id(block.lines[_i]) in _hidden_ids:
                _hidden_body_count += 1
    _body_all_hidden = _body_count > 0 and _hidden_body_count == _body_count
    # ★ PERF-6b：内容行整体缓存——跨帧/跨桶复用列表对象，TEXT
    #   ``_wrap_cache`` 按 styled 引用命中（大工具卡跨桶渲染不再每帧全量
    #   wrap；frame_cache 同桶快速路径之外的兜底）。key 仅依赖块内容/宽度/
    #   省略计数/隐藏集合（不含呼吸色）——变化时自动重建。
    _body_key = (
        start, len(block.lines), body_start, body_end, width, status_idx,
        block.extra.get("_bash_omitted_lines", 0),
        block.extra.get("_head_omitted_lines", 0),
        len(_hidden_rows) if _hidden_rows else 0,
        bg_style.bg,
    )
    body_lines_cache = getattr(block, "_tool_card_body_lines_cache", None)
    if body_lines_cache is not None and body_lines_cache[0] == _body_key:
        body_lines = body_lines_cache[1]
    else:
        body_lines: list[list[StyledRun]] = []
        # bash 尾显示：前置省略提示行「… 前 N 行省略」（仅首次提交 start==0）；
        # 全隐藏（read_file 成功内容）时一并跳过（保持「只显示标题行」）
        omitted = block.extra.get("_bash_omitted_lines", 0)
        if omitted > 0 and not _body_all_hidden:
            body_lines.append(_omitted_line(f"\u2026 前 {omitted} 行省略", width, bg_style))
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
            # ★ 用户需求（read_file 聊天卡只显示标题行）：隐藏行不渲染——
            #   数据仍在 block.lines（Trace 可见），仅聊天卡跳过。
            if _hidden_ids is not None and id(ansi_line) in _hidden_ids:
                continue
            key = (ansi_line, width, bg_style.bg)
            cached = body_cache.get(key)
            if cached is None:
                # ★ H2（BUG 修复，2026-08-15）：内容行 wrap 预算与显示预算
                #   对齐——内容行每段前置 ``│ ``（竖线引导占 guide_w 列），
                #   修复前按总宽 ``wrap_line(ansi_line, width)`` 换行，但每段
                #   显示预算仅 ``width-guide_w``（下方 truncate_runs_ellipsis
                #   丢弃段末 2 列）→ 长行跨段时每段末尾 2 列内容丢失。修复：wrap
                #   宽度改用 ``content_w = width - guide_w``——每段 + 竖线后
                #   恰为 width，不再丢内容。width<=1 走既有「仅竖线」分支
                #   （content_w<=0 无内容）；width<=0 无宽度防御保持裸行。
                guide_w = 2 if width >= 2 else 1
                content_w = max(0, width - guide_w)
                wrapped = (
                    wrap_line(ansi_line, content_w)
                    if width >= 2
                    else wrap_line(ansi_line, width)
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
                            _apply_line_bg(
                                [StyledRun(guide_text, _GUIDE_STYLE)]
                                + truncate_runs_ellipsis(
                                    seg_runs, max(0, width - guide_w),
                                ),
                                width, bg_style,
                            ),
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
                        body_lines.append(_apply_line_bg(
                            [StyledRun(guide_text, _GUIDE_STYLE)], width, bg_style,
                        ))
                    else:
                        body_lines.append([StyledRun("", None)])
                    continue
                if kind == "bare":
                    body_lines.append(item[1])
                    continue
                body_lines.append(item[1])
        # read_file 头显示：后置省略提示行「… 后 N 行省略」
        # （head 省略的行在末尾——提示置于内容行之后，对齐终端 head 语义）
        omitted_head = block.extra.get("_head_omitted_lines", 0)
        if omitted_head > 0 and not _body_all_hidden:
            body_lines.append(_omitted_line(f"\u2026 后 {omitted_head} 行省略", width, bg_style))
        block._tool_card_body_lines_cache = (_body_key, body_lines)
    out.extend(body_lines)
    # ★ Claude Code 极简样式（2026-08-06 用户需求）：**无独立状态行**——
    #   Claude Code 完成/失败时状态由标题行状态图标（✔/✖）原位表达，不追加
    #   ``✔ 完成 · N 行 · Xs`` 状态行。模型层 close_tool_box 追加的
    #   ``  ✔``/``  ✖`` 数据行经 ``_tool_status_index`` 跳过（不渲染为内容行）。
    block._tool_card_frame_cache = (_frame_key, out)
    return out


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
        Column 元素（标题行 + 内容行，每行一个 TEXT）。
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
    from src.tui.ink.widgets._panel import Panel
    block = props["block"]
    width = props.get("width", 0)
    start = props.get("start", 0)
    stop = props.get("stop")
    runs_list = tool_card_lines(block, width, start, stop)
    children = [
        h(TEXT, {"key": f"tool-{i}", "styled": runs})
        for i, runs in enumerate(runs_list)
    ]
    # ★ 全面控件化（方案B）：工具卡经标准控件 ``Panel``（border=0 无边框
    #   模式）表达——「无边框裸行 + │ 引导线」Claude Code 极简视觉保持
    #   （2026-08-06 用户需求：无边框），Panel 无边框模式直接渲染内部
    #   Column（与旧 h(Column, ...) 等价，控件化表达）。
    return h(Panel, {
        "border": 0, "width": max(0, width),
        "padding": 0, "paddingLeft": 0, "paddingRight": 0,
    }, children)
