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
    wcswidth_simple,
)
from src.tui._input import (
    _wrap_by_width,
    # ★ 方向5（光标算法单一真源）：_compute_input_layout /
    #   _cursor_visual_from_layout 自本文件迁移至 _input.py——这里从 _input
    #   导入（删除本地副本，避免双实现）。
    # ★ _compute_cursor_visual_pos 经本模块 re-export（test_input_area.py
    #   TestCursorAlgorithmSingleSource 锁定同一对象契约），保留导入。
    _compute_cursor_visual_pos,
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
#: 流式占位符完整显示（保留既有值/外部引用）
_PLACEHOLDER_STREAMING = "AI 生成中..."
#: 流式占位符动画基文本（无尾点；BEAUTY-8 动态追加 0-3 个点循环）
_PLACEHOLDER_STREAMING_BASE = "AI 生成中"

_PROMPT = "> "

# 方向C 步骤4：_S_TEXT 被多处使用 → 迁入 app/_theme.py 共享池；以下单处使用
# 常量保留模块私有（享元收敛原则：仅多处使用才共享）。
# P2-10：_S_PROMPT/_S_PLACEHOLDER 为死常量（定义后全项目无引用——提示符已用
# 呼吸色 _glow_color、占位符已用渐显色 _placeholder_fade_color）→ 删除。
_S_CONT = Style(fg=242)
# ★ BEAUTY-14（美化）：CPU/MEM 着色区分——CPU 亮青（45）、MEM 橙黄（214），
#   上分隔线信息更易扫读（原两者同灰）。
_S_CPU = Style(fg=45)
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
    极窄分支：宽度下限钳制到可用宽度（≤ width-1，左栏至少 1 列）——
    修复前 ``max(8, ...)`` 在 width<20 时右栏恒 8 超过终端总宽，
    分栏行总宽溢出终端。
    """
    if int(width) < 20:
        return max(1, min(int(width) - 1, int(width) // 2))
    return max(8, min(int(width) // 3, 40, int(width) - 12))


#: 补全弹窗高度锁定的最大允许补白行数——items 减少时弹窗高度保持（防闪烁），
#: 但补白超过此值（items 大幅减少）时允许缩小（避免弹窗底部大片空白）。
_LOCKED_PAD_LIMIT = 3


def _completion_item_rows() -> int:
    """补全弹窗候选项最大行数（终端高度约束，防超屏）。

    预留顶部标题 1 + 弹窗标题 1 + 弹窗提示行 1 + 状态栏 1 + 输入区分隔线 1
    + 输入行 1 + 输入下分隔线 1 + 时间戳 1 ≈ 8 行；候选项 + 说明行数限制在
    ``max(6, h - 10)``。正常补全（≤20 项）不受影响；极长说明 / user_select
    大量选项时弹窗不超屏。

    ★ 性能（方向4）：终端高度经 ``TerminalWidthCache`` 读取——修复前每次
    调用直接 ``_get_terminal_size()``（fcntl.ioctl），补全弹窗可见时
    ``_completion_height`` 在 ``_measure`` 与 ``_position_cursor`` 每帧各
    调一次 → 每帧 2 次 ioctl。TTL 缓存避免重复系统调用。

    Returns:
        候选项（含说明）最大渲染行数。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, h - 10)
    except Exception:
        return 12


def _completion_height(completion, width=None) -> int:
    """补全弹窗高度（标题 + 候选项 + 提示行）。

    分栏说明模式（split_desc 且存在说明）下，高度取选项数与当前选中项说明
    换行行数的较大值——说明可多行，弹窗随说明行数增高。

    方向4（超屏防护）：候选项行数经 ``_completion_item_rows`` 限制——大量
    选项 / 超长说明时弹窗不超终端高度（渲染截断与高度一致，光标定位正确）。

    ★ 高度锁定（补全弹窗闪烁修复 + 补白上限）：弹窗打开期间优先返回
    ``locked_height``（items 小幅减少时**只增不减**）——打字时 items 数量变化
    （5→2→1）若高度随之下调，input_area 高度变化触发文档缩短重排（物理缓冲
    无 delete-line → 漂移 → 全量重写 → 视觉闪烁）；锁定后 items 小幅减少时
    高度保持（底部短暂留白，≤ ``_LOCKED_PAD_LIMIT`` 行），doc 高度不变 →
    等高 diff 只重写弹窗行（不闪）；items 增加时高度跟随（增高，增长滚动
    自然）。
    但补白超过 ``_LOCKED_PAD_LIMIT``（items **大幅**减少，如 20→1 项）时允许
    缩小到当前 need——避免弹窗底部渲染十余行空白（视觉异常；一次 diff 重写
    换取无空白更优）。弹窗关闭（hide_completions）重置 locked_height=0。

    Args:
        completion: CompletionState 或 None。
        width: 终端宽度（分栏说明模式需要）。

    Returns:
        弹窗高度（行数）；弹窗不可见/无 items 时 0。
    """
    if completion is None or not completion.visible or not completion.items:
        return 0
    n = len(completion.items)
    descs = completion.descriptions or []
    if not (getattr(completion, "split_desc", False) and descs) or width is None:
        need = min(n, _completion_item_rows()) + 2
    else:
        desc_w = _desc_column_width(width)
        sel = max(0, min(completion.selected, len(descs) - 1))
        desc_lines = _wrap_by_width(descs[sel] or "", desc_w)
        need = min(max(n, len(desc_lines)), _completion_item_rows()) + 2
    # 高度锁定（补全弹窗闪烁修复 + 补白上限）：
    #   - items 增加 → 高度跟随（增长滚动自然）。
    #   - items 小幅减少（need 与 locked_height 差距 <= _LOCKED_PAD_LIMIT）
    #     → 高度保持（底部补白 ≤ 上限），doc 高度不变 → 等高 diff 只重写
    #     弹窗行（消除打字时 items 数量变化引发的全量重写闪烁）。
    #   - items 大幅减少（差距 > _LOCKED_PAD_LIMIT，如 20→1 项）→ 允许缩小
    #     到 need——避免弹窗底部渲染十余行空白（视觉异常；一次 diff 重写
    #     换取无空白更优）。
    locked = getattr(completion, "locked_height", 0)
    if need > locked:
        completion.locked_height = need
    elif locked - need > _LOCKED_PAD_LIMIT:
        completion.locked_height = need
    return completion.locked_height


def _is_search_active(search) -> bool:
    """反向历史搜索是否激活（history_search 非 None 且 active，方向D 步骤14）。"""
    return search is not None and bool(getattr(search, "active", False))


def _measure(fiber, avail_w) -> tuple[int, int]:
    props = fiber.props
    explicit = props.get("width")
    # ★ 健壮性（方向4）：width 畸形兜底——与其他 host/内置布局一致
    #   （try/except TypeError/ValueError，修复前 ``int(explicit)`` 对畸形值
    #   抛异常 → 经 layout_tree 传播中断整帧渲染）。App 正常路径无显式 width，
    #   本分支为防御。
    try:
        width = max(0, int(explicit)) if explicit is not None else avail_w
    except (TypeError, ValueError):
        width = avail_w
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
    #   BEAUTY-8：status_active 期间恒用 0.1s 桶——流式占位符动画点
    #   （``AI 生成中.`` 推进）以 10Hz 平滑刷新（流式期间帧率本就 10Hz，
    #   零额外渲染成本）；空闲回 1s 桶（静态显示，CPU 保持低占用）。
    now = time.monotonic()
    fade_key = getattr(fiber, "_placeholder_fade_key", None)
    fading = False
    if fade_key is not None:
        fade_elapsed = now - fade_key[1]
        fade_duration = _fx._DEFAULT_FADE_DURATION
        fading = fade_duration > 0 and fade_elapsed < fade_duration
    if status_active or fading:
        time_bucket = int(now / 0.1)
    else:
        # 方向3（呼吸平滑）：空闲占位符呼吸色用 0.25s 桶（4Hz）——1s 桶下
        # 呼吸色 1Hz 步进明显可感知；4Hz 平滑且仍低频（CPU 开销可忽略）。
        time_bucket = int(now / 0.25)
    # ★ BUG-23（review 方向，性能）：补全快照用**轻量指纹**（id/len/selected）
    #   替代 tuple(全部项)——修复前缓存命中检查**之前**无条件 tuple 化
    #   items/texts/types/descriptions 全部元素（user_select 大量选项/长命令
    #   列表时每帧 O(n) 分配，即使缓存命中）。指纹语义：id(items) 变化
    #   （show_completions 每次新建列表）→ 重建；selected 变化（导航高亮）→
    #   重建；原地修改同列表（罕见）→ 不重建（可接受的权衡，补全项通常
    #   不可变）。
    if completion is not None:
        completion_snap = (
            completion.visible,
            id(completion.items),
            len(completion.items),
            completion.selected,
            id(completion.texts),
            len(completion.texts),
            id(completion.descriptions),
            len(completion.descriptions),
            getattr(completion, "split_desc", False),
        )
    else:
        completion_snap = (False, 0, 0, 0, 0, 0, 0, 0, False)
    search = props.get("history_search")
    if search is not None:
        search_snap = (
            bool(search.active),
            search.query,
            id(search.matches),
            len(search.matches),
            search.index,
        )
    else:
        search_snap = (False, "", 0, 0, -1)
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
        # 标题行（方向3：标题呼吸色——补全弹窗出现时增加动态感；
        # 方向8：▍ 装饰条前缀——与错误/通知标记同语义，补全弹窗更醒目）
        title_color = _glow_color(38, 55)
        head = Line.of(" \u258d", Style(fg=title_color, bold=True))
        head.append(" ", Style(fg=title_color, bold=True))
        head.append(title, Style(fg=title_color, bold=True))
        # ★ BEAUTY-17（体验）：导航位置提示 ``(2/10)``——选中项位置/总数，
        #   补全弹窗导航时用户可感知当前位置（替代仅总数）。总数取
        #   ``len(completion.texts)``（与项数一致；缺省回退 len(items)）。
        if total > 0:
            head.append(f" ({selected + 1}/{total})", Style(fg=title_color))
        if split:
            # 左栏标题占位（标题与选项栏对齐；右栏说明位置留白）
            head.append(" " * max(0, opt_w - head.width), _S_DIM)
        # ★ 方向8（窄屏防溢出）：标题行超宽时截断至 width（不拆 CJK）——
        #   修复前 `` 补全 (4项)`` 在 width<11 时撑爆行宽。
        if head.width > width:
            from src.tui.ink.helpers import truncate_line
            head = truncate_line(head, width)
        lines.append(head)
        # 候选项
        # ★ 方向3（动效）：选中项高亮呼吸——背景色 236→239 脉动（10s 周期），
        #   高亮项有微弱呼吸感（候选列表导航更生动）。
        sel_bg = time_glow(236, 239, 10.0)
        if split:
            # 左栏选项内容宽度（前缀 ▶ + 文本；右栏说明独立换行）
            cell_w = max(
                1, min(max((_vwidth(i) for i in items), default=10) + 4, opt_w - 2) - 3,
            )
            # ★ BUG-27（review 方向）：selected 越界钳制与 ``_completion_height``
            #   一致——修复前高度按 ``min(selected, len(descs)-1)`` 的说明行数
            #   计算、绘制却 ``descs[selected] if 0 <= selected < len(descs)``
            #   （越界时空说明）→ 弹窗底部多出空白行，测量高度与绘制不一致。
            desc_sel = max(0, min(selected, len(descs) - 1)) if descs else 0
            desc_text = descs[desc_sel] if descs else ""
            desc_lines = _wrap_by_width(desc_text or "", desc_w)
            # 方向4（超屏防护）：候选项 + 说明行数限制（与 _completion_height
            # 一致——超长说明 / 大量选项时弹窗不超终端高度）。
            # ★ 高度锁定（补全弹窗闪烁修复）：渲染行数取 ``_completion_height-2``
            #   （锁定高度）而非当前内容需求——items 减少时弹窗高度保持（底部
            #   补白），doc 高度不变 → 等高 diff 只重写弹窗行（不闪）。
            n_rows = max(0, _completion_height(completion, width) - 2)
            for row in range(n_rows):
                line = Line()
                # 左栏：选项
                if row < len(items):
                    i = row
                    if i == selected:
                        line.append(" \u25b6 ", Style(fg=15, bg=sel_bg))
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
                # ★ 方向8（窄屏防溢出）：分栏行超宽时截断至 width（不拆
                #   CJK）——修复前窄屏下左栏前缀 + 文本 + 分隔线 + 说明撑爆行宽。
                if width > 0 and line.width > width:
                    from src.tui.ink.helpers import truncate_line
                    line = truncate_line(line, width)
                lines.append(line)
        else:
            cell_w = max(1, min(max((_vwidth(i) for i in items), default=10) + 4, width - 2) - 3)
            # 方向4（超屏防护）：大量选项时截断渲染行数（与 _completion_height
            # 一致——超出终端的选项不渲染，弹窗不超屏）。
            # ★ 高度锁定（补全弹窗闪烁修复）：渲染行数取 ``_completion_height-2``
            #   （锁定高度）而非当前 items 数量——items 减少时弹窗高度保持
            #   （底部补白空行），doc 高度不变 → 等高 diff 只重写弹窗行（不闪）。
            n_rows = max(0, _completion_height(completion, width) - 2)
            for i in range(n_rows):
                if i >= len(items):
                    # 高度锁定补白：items 减少后弹窗底部留白（空行）
                    lines.append(Line())
                    continue
                item = items[i]
                line = Line()
                if i == selected:
                    line.append(" \u25b6 ", Style(fg=15, bg=sel_bg))
                else:
                    # 与选中行 ` ▶ `（3 列）等宽——修复前 `"  "`（2 列）使
                    # 选项文本上下移动时左右跳动（选中/非选中相差 1 列）。
                    line.append("   ")
                for run in _styled_completion(item, types[i], match_prefix, cell_w).runs:
                    line.append_run(run)
                # Claude TUI parity 步骤 3.7：斜杠命令描述灰显（command 且描述非空）
                if types[i] == "command" and i < len(descs) and descs[i]:
                    line.append("  ", _S_DIM)
                    # 方向1 步骤4（窄屏防溢出）：描述截断至剩余行宽（复用
                    # _truncate_width，截断点不拆 CJK）——超长描述不再撑爆行宽。
                    desc_budget = max(1, width - line.width)
                    line.append(_truncate_width(descs[i], desc_budget), _S_DIM)
                # ★ 方向8（窄屏防溢出）：选项行超宽时截断至 width（不拆
                #   CJK）——修复前 `` ▶ /help 显示帮助`` 在窄屏撑爆行宽。
                if width > 0 and line.width > width:
                    from src.tui.ink.helpers import truncate_line
                    line = truncate_line(line, width)
                lines.append(line)
        # 底部提示（方向3 动效：提示文本呼吸色——补全弹窗出现时更生动）
        hint_color = _glow_color(110, 16)  # 浅蓝 110 → 126 脉动（_S_TIME 邻域）
        hint = Line.of(" ", Style(fg=hint_color))
        hint.append("Tab \u2191\u2193 Esc", Style(fg=hint_color))
        # ★ 方向8（窄屏防溢出）：提示行超宽时截断至 width。
        if width > 0 and hint.width > width:
            from src.tui.ink.helpers import truncate_line
            hint = truncate_line(hint, width)
        lines.append(hint)

    # ── 上分隔线（CPU/MEM） ──
    cpu = int(props.get("cpu", 0))
    mem = int(props.get("mem", 0))
    cpu_mem = f"CPU:{cpu}% \u00b7 MEM:{mem}%"
    cpu_mem_w = len(cpu_mem) + 2
    # 方向3（动效）：活跃期间上分隔线用青色呼吸（32-45，8s 周期），与状态栏
    # 分隔线呼吸同步周期；空闲保持静态深灰。
    top_sep_style = Style(fg=time_glow(32, 45, 8.0)) if status_active else _S_SEP
    top = Line.of("", top_sep_style)
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限改为 0（修复前 ``max(1, ...)``
    # 在 width < cpu_mem_w 时内容超宽溢出）；CPU/MEM 内容独立行逐段截断至
    # 剩余宽度（不拆 CJK；width < 22 时不再超宽）。
    sep_len = max(0, width - cpu_mem_w)
    top.append("\u2501" * sep_len, top_sep_style)
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
        # ★ 方向8（动效）：搜索 query 呼吸色（221→232，8s 周期）——搜索
        #   激活时 query 微呼吸，视觉提示「匹配进行中」（0.25s 桶，4Hz 刷新）。
        from src.tui.app._theme import time_glow as _tg
        sline.append(q, Style(fg=_tg(221, 232, 8.0)))
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
            # ★ 方向4（体验）：补全弹窗打开时提示符提亮——``_glow_color(base,
            #   amp)`` 语义为 time_glow(base, base+amp)：空闲 32-81（青色呼吸），
            #   补全导航 45-100（整体上移更亮）——弹窗可见、键盘导航时提示符
            #   更醒目。
            if completion is not None and completion.visible:
                color = _glow_color(45, 55)
            else:
                color = _glow_color(32, 49)
            line.append(_PROMPT, Style(fg=color, bold=True))
            if text:
                line.append(segment, _S_TEXT)
            else:
                if status_active:
                    # BEAUTY-8：流式占位符动画点（0.25s 帧推进；
                    # 渐显键用稳定基文本——动画点变化不重置 FadeIn）
                    base_ph = _PLACEHOLDER_STREAMING_BASE
                    n_dots = int(now * 4) % 4
                    ph = base_ph + "." * n_dots
                else:
                    base_ph = _PLACEHOLDER_COMPACT if (completion is not None and completion.visible) else _PLACEHOLDER_TEXT
                    ph = base_ph
                # 方向1 步骤4（窄屏防溢出）：占位符截断至剩余输入区宽度
                # （提示符后；_truncate_width 不拆 CJK）——width < 占位符长度
                # 时不再撑爆行宽。截断后的 base_ph 作为渐显键（同占位符持续
                # 显示语义一致）。
                ph_budget = max(1, width - len(_PROMPT))
                if wcswidth_simple(ph) > ph_budget:
                    ph = _truncate_width(ph, ph_budget)
                fade_key_ph = base_ph
                if wcswidth_simple(fade_key_ph) > ph_budget:
                    fade_key_ph = _truncate_width(fade_key_ph, ph_budget)
                # BEAUTY-1：占位提示 FadeIn 渐显（时间基；_glow_color 呼吸色为终色）
                line.append(ph, Style(fg=_placeholder_fade_color(fiber, fade_key_ph, _glow_color(242, 10))))
        else:
            line.append("\u00b7 ", _S_CONT)
            line.append(segment, _S_TEXT)
        # ★ 方向8（极窄屏防溢出）：``> ``（2 列）/``· ``（2 列）前缀 +
        #   输入段可能超 width（width<4 时 CJK 段宽 2）——截断至 width 保持
        #   行级 diff 宽度不变量（与补全弹窗/搜索行截断语义一致）。
        if width > 0 and line.width > width:
            from src.tui.ink.helpers import truncate_line
            line = truncate_line(line, width)
        lines.append(line)

    # ── 下分隔线（时间戳） ──
    now_local = time.localtime()
    ts = f"{now_local.tm_year}-{now_local.tm_mon:02d}-{now_local.tm_mday:02d} {now_local.tm_hour:02d}:{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
    time_w = len(ts) + 2
    # ★ BEAUTY-13（动效）：下分隔线（时间戳行）呼吸——活跃/流式期间与
    #   上分隔线/状态栏分隔线同周期青色呼吸（32-45，8s），三条分隔线视觉
    #   联动；空闲保持静态深灰（_S_SEP，零额外渲染成本）。
    bottom_style = Style(fg=time_glow(32, 45, 8.0)) if status_active else _S_SEP
    bottom = Line.of("", bottom_style)
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限 0 + 时间戳内容独立行截断
    # （width < 22 时不超宽；正常宽度时间戳完整保留）
    sep_len = max(0, width - time_w)
    bottom.append("\u2501" * sep_len, bottom_style)
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
            #   自定义 host paint 与内置 TEXT 共用惰性语义。行可能为 Line
            #   （x==0 快路径写入的兄弟节点）→ 归一并合并（修复前对 Line
            #   直接 ``row[col]=...`` 抛 TypeError，内容被 _paint 隔离吞掉）。
            target = canvas[row]
            # ★ 整行 Line 快路径（方向4）：box.x==0 且行未命中时直接存 Line
            #   对象——与内置 TEXT 快路径一致：免逐字符 ``_merge`` dict 合并
            #   （输入区每帧重建热路径，大文档渲染耗时关键）+ diff 阶段身份
            #   短路（``_build_lines`` 快照缓存命中的同 Line 引用跨帧零重建）。
            #   输入区位于文档底部、无后续兄弟覆盖同屏行，快路径安全。
            if target is None and box.x == 0:
                canvas[row] = line
                continue
            if isinstance(target, Line):
                from src.tui.ink.components import _line_as_dict
                target = _line_as_dict(target)
                canvas[row] = target
            elif target is None:
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
