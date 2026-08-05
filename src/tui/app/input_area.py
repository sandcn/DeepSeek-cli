"""InputArea — 输入区 React Ink 标准组件（补全弹窗 + 分隔线 + 输入行）。

★ 标准 React Ink 组件化（2026-08-05，无例外收尾）：input-area 自定义 host
（直接画布绘制）迁移为**标准函数组件** ``InputArea``——返回 Column（组件
树）：``CompletionPopup``（补全弹窗 Column + TEXT）+ 上分隔线 TEXT + 反向
历史搜索 TEXT + 输入行 TEXT + 时间戳分隔线 TEXT。生产代码经 App 组件树
``h(InputArea, props)`` 渲染；旧 host "input-area" 与遗留 host 绘制函数
（``_measure``/``_paint``/``_build_separator_line``/``_merge``/
``_compute_input_rows``/``_wrap_input_text``）及 ``register()`` 空入口已
全部删除（无例外）——`_build_lines`/``_build_popup_lines`` 为组件内部渲染
辅助（快照缓存），保留。

复用 _input.py 的 ``_expand_tabs`` / ``_wrap_by_width`` /
``_compute_cursor_visual_pos`` / ``_compute_input_layout`` /
``_cursor_visual_from_layout``（唯一真源），保证换行/CJK/光标计算与旧实现
一致。

方向5（光标算法单一真源）：``_compute_input_layout`` /
``_cursor_visual_from_layout`` 已迁移至 ``_input.py``（本文件从 _input 导入，
删除本地副本——input_area 与 session 共享同一实现，不再双实现）。

性能：``_build_lines`` 快照缓存语义保留（Line 跨帧引用稳定）——InputArea
经 ``use_memo`` 缓存 Element 列表（deps = 快照键），命中时 children 引用
稳定 → reconciler/layout/paint 短路（零重建）。光标定位由 session 经
``dataInputArea`` 容器 + ``_compute_cursor_visual_pos`` 计算；换行布局缓存
（``fiber._input_layout_cache``）由 ``_build_lines`` 单点写入（原遗留
``_measure`` 写入职责收拢，session._position_cursor 复用）。
"""

from __future__ import annotations

import time
from functools import lru_cache

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
from src.tui.ink import h, TEXT, Column, Line, use_memo, use_ref
from src.tui.app import _fx
from src.tui.app._theme import sep_line as _theme_sep_line, time_glow, _S_ACCENT, _S_DIM, _S_SEP, _S_TEXT, _S_TIME

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


def _build_popup_lines(completion, width: int, now: float) -> list:
    """构建补全弹窗行（标题 + 候选项 + 提示）；弹窗不可见返回 []。

    ★ 性能（PERF-7）：弹窗部分独立缓存——打字（input_text 变化）时
    ``_build_lines`` 全量快照 miss，但弹窗 items/selected/时间桶未变时直接
    复用本部分行（免每帧重建 20+ 候选项）。缓存键覆盖全部动态因素：
    title/items 引用+长度/selected/texts/descriptions/descriptions/split_desc/
    match_prefix/types/width/呼吸时间桶。``selected`` 变化（导航高亮）与
    items 变化自动重建；``time_glow`` 呼吸 0.1s 桶变化自动重建。
    弹窗行引用跨帧稳定（调用方只读，diff 身份短路受益）。
    """
    if completion is None or not completion.visible or not completion.items:
        return []
    items = completion.items
    selected = completion.selected
    match_prefix = completion.match_prefix or ""
    # ★ 缓存键稳定性（PERF-7 同族，BUG-73）：types 为空列表时用模块级空元组
    #   （恒同对象）——``completion.types or [""] * len(items)`` 每次创建新
    #   列表，`id(types)` 每帧变化 → 弹窗缓存永远 miss（每帧重建 20+ 候选项）。
    #   与 descs 的 ``descriptions or ()`` 修复一致。types 非空（show_completions
    #   传入）时保持列表引用稳定（不可变契约）。
    types = completion.types or ()
    # ★ 绘制用 types 列表：types 为空时生成 ``[""] * len(items)`` 供 ``types[i]``
    #   索引（绘制阶段才展开，不进缓存键——键用稳定空元组 id）。
    types_disp = list(types) if types else [""] * len(items)
    title = completion.title
    total = len(completion.texts) if completion.texts else len(items)
    # ★ 缓存键稳定性（PERF-7）：descriptions 为空时用模块级空元组（恒同对象）
    #   ——``[] or []`` 每次调用创建新空列表，`id(descs)` 每帧变化 → 弹窗缓存
    #   永远 miss（每帧重建）。
    descs = completion.descriptions if completion.descriptions else ()
    split = bool(getattr(completion, "split_desc", False)) and bool(descs)
    desc_w = _desc_column_width(width) if split else 0
    opt_w = max(1, width - desc_w - 1) if split else 0
    # 弹窗呼吸色依赖时间桶（time_glow 内部 int(t/0.1) 为 0.1s 粒度）——
    # 与 _build_lines 的 time_bucket 不同（后者随 status_active 用 0.1/0.25s）。
    # ★ 性能（PERF-11）：弹窗缓存键用 **0.25s 桶**（4Hz）——修复前用
    #   ``int(now/0.1)``（10Hz）：打字（input_text 变化触发外层快照 miss →
    #   调 _build_popup_lines）间隔 >0.1s 时每次按键跨桶 → 弹窗缓存几乎每次
    #   miss → 每键重建 20+ 候选项。0.25s 桶与 _build_lines 空闲桶一致：
    #   打字跨桶概率降 60%，呼吸色仍 4Hz 平滑推进（标题 12s/提示 12s/高亮
    #   10s 周期，4Hz 步进视觉无感知差异）；弹窗可见时 _needs_animation 持续
    #   10Hz 渲染，动画不冻结。
    popup_bucket = int(now / 0.25)
    popup_snap = (
        title,
        id(items), len(items), selected,
        id(completion.texts), len(completion.texts) if completion.texts else 0,
        id(descs), len(descs),
        split,
        match_prefix,
        id(types), len(types),
        width,
        popup_bucket,
    )
    cached = getattr(completion, "_popup_lines_cache", None)
    if cached is not None and cached[0] == popup_snap:
        return cached[1]

    lines: list = []
    # 分栏说明模式（user_select）：左侧选项列表、右侧当前选中项说明
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
                for run in _styled_completion(items[i], types_disp[i], match_prefix, cell_w).runs:
                    line.append_run(run)
                # 补齐左栏剩余宽度（选项不足 opt_w 时留白，分隔线对齐）
                pad = opt_w - line.width
                if pad > 0:
                    line.append(" " * pad, _S_DIM)
            else:
                line.append(" " * opt_w, _S_DIM)
            line.append("\u2502", _S_SEP)
            # 右栏：当前选中项说明（分栏换行）
            # ★ BEAUTY-21（体验动效）：说明列呼吸色——浅蓝 110→120 脉动
            #   （12s 周期，与弹窗标题/提示呼吸协调）。弹窗可见时渲染循环
            #   持续推进（_needs_animation 覆盖），呼吸平滑。
            if row < len(desc_lines):
                line.append(
                    _truncate_width(desc_lines[row], desc_w),
                    Style(fg=time_glow(110, 120, 12.0)),
                )
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
            for run in _styled_completion(item, types_disp[i], match_prefix, cell_w).runs:
                line.append_run(run)
            # Claude TUI parity 步骤 3.7：斜杠命令描述灰显（command 且描述非空）
            if types_disp[i] == "command" and i < len(descs) and descs[i]:
                line.append("  ", _S_DIM)
                # ★ BEAUTY-21（体验动效）：命令描述呼吸色——浅蓝 110→120 脉动
                #   （12s 周期，与分栏说明列呼吸一致）。弹窗可见时渲染循环
                #   持续推进，呼吸平滑；与原 _S_DIM 相比增加细微动态。
                desc_budget = max(1, width - line.width)
                line.append(
                    _truncate_width(descs[i], desc_budget),
                    Style(fg=time_glow(110, 120, 12.0)),
                )
            # ★ 方向8（窄屏防溢出）：选项行超宽时截断至 width（不拆
            #   CJK）——修复前 `` ▶ /help 显示帮助`` 在窄屏撑爆行宽。
            if width > 0 and line.width > width:
                from src.tui.ink.helpers import truncate_line
                line = truncate_line(line, width)
            lines.append(line)
    # 底部提示（方向3 动效：提示文本呼吸色——补全弹窗出现时更生动）
    hint_color = _glow_color(110, 16)  # 浅蓝 110 → 126 脉动（_S_TIME 邻域）
    hint = Line.of(" ", Style(fg=hint_color))
    hint.append("Tab \u2191\u2193 PgUp/PgDn Esc", Style(fg=hint_color))
    # ★ 方向8（窄屏防溢出）：提示行超宽时截断至 width。
    if width > 0 and hint.width > width:
        from src.tui.ink.helpers import truncate_line
        hint = truncate_line(hint, width)
    lines.append(hint)
    completion._popup_lines_cache = (popup_snap, lines)
    return lines


def _build_lines(fiber, include_popup: bool = True) -> list[Line]:
    """构建输入区行列表（弹窗/分隔线/搜索/输入行/时间戳）。

    Args:
        fiber: 输入区 host fiber（读取 props + layout_box）。
        include_popup: 是否包含补全弹窗行。False 供 InputArea 标准组件——
            弹窗由独立 ``CompletionPopup`` 组件渲染（避免重复）。
    """
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
        include_popup,  # ★ 标准组件化：弹窗行存在性须进缓存键（InputArea
        #   用 include_popup=False 时不命中全量缓存；置于首部保持 time_bucket
        #   仍在末尾——测试 ``_snap_key_of`` 读 ``[-1]`` 兼容）
        text,
        max_input,
        width,  # ★ BUG-71（review 方向，缓存键完整性）：snap_key 补 width——
        #   修复前缺 width：极窄屏（width 变化但 max_input 可能不变，如
        #   width 3→2 时 max_input 1→1）下命中旧快照，分隔线/弹窗按旧宽渲染
        #   （测量与绘制错位）。
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

    # ★ PERF-1：复用换行布局缓存（同 text/max_input 命中复用，未命中计算后
    #   写回——InputArea 组件经 use_memo 快照缓存控制调用频率，本缓存补充
    #   _input_elements 之外调用方（session._position_cursor 读 dataInputArea
    #   容器 fiber 缓存）的复用语义。原遗留 host ``_measure`` 承担写入职责
    #   （已移除，见模块 docstring）——职责收拢到 ``_build_lines`` 单点。
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        _, wrapped_by_logical = cached[1]
    else:
        rows, wrapped_by_logical = _compute_input_layout(text, max_input)
        fiber._input_layout_cache = ((text, max_input), (rows, wrapped_by_logical))
    wrapped = [seg for segs in wrapped_by_logical for seg in segs]

    lines: list[Line] = []

    # ── 补全弹窗（独立缓存，PERF-7） ──
    # ★ 性能（PERF-7）：弹窗部分提取为 ``_build_popup_lines`` 独立缓存——
    #   打字（input_text 变化）导致全量快照 miss 时，弹窗 items/selected/时间
    #   桶未变则直接复用弹窗行（免每帧重建 20+ 候选项 + 行宽判断）。
    # ★ 标准组件化：include_popup=False 时弹窗行跳过（由独立 CompletionPopup
    #   组件渲染）。
    if include_popup:
        lines.extend(_build_popup_lines(completion, width, now))

    # ── 上分隔线（CPU/MEM） ──
    cpu = int(props.get("cpu", 0))
    mem = int(props.get("mem", 0))
    cpu_mem = f"CPU:{cpu}% \u00b7 MEM:{mem}%"
    cpu_mem_w = len(cpu_mem) + 2
    # 方向3（动效）：活跃期间上分隔线用青色呼吸（32-45，8s 周期），与状态栏
    # 分隔线呼吸同步周期；空闲保持静态深灰。★ 方向5：统一经 _theme.sep_style
    # （input_area 上下分隔线 + status_bar 分隔线共用同一周期/色域）。
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限改为 0（修复前 ``max(1, ...)``
    # 在 width < cpu_mem_w 时内容超宽溢出）；CPU/MEM 内容独立行逐段截断至
    # 剩余宽度（不拆 CJK；width < 22 时不再超宽）。
    content_budget = max(1, width - max(0, width - cpu_mem_w))
    content = Line()
    # ★ BEAUTY-22（体验动效）：CPU/MEM 值活跃期呼吸——CPU 亮青 45→55、MEM
    #   橙黄 214→224（12s 周期，与状态栏 token/速度呼吸同步）。空闲静态
    #   _S_CPU/_S_MEM（零额外渲染成本）。系统监控 2s 刷新一次，呼吸提供
    #   平滑视觉过渡。
    _active_cpu_style = Style(fg=time_glow(45, 55, 12.0)) if status_active else _S_CPU
    _active_mem_style = Style(fg=time_glow(214, 224, 12.0)) if status_active else _S_MEM
    _append_truncated(content, " CPU:", _S_ACCENT, content_budget)
    _append_truncated(content, f"{cpu}%", _active_cpu_style, content_budget)
    _append_truncated(content, " \u00b7 MEM:", _S_ACCENT, content_budget)
    _append_truncated(content, f"{mem}%", _active_mem_style, content_budget)
    lines.append(_theme_sep_line(width, content, status_active))

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
    #   联动；空闲保持静态深灰（_S_SEP，零额外渲染成本）。★ 方向5：统一
    #   经 _theme.sep_style。
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限 0 + 时间戳内容独立行截断
    # （width < 22 时不超宽；正常宽度时间戳完整保留）
    content_budget = max(1, width - max(0, width - time_w))
    content = Line()
    _append_truncated(content, f" {ts}", _S_TIME, content_budget)
    lines.append(_theme_sep_line(width, content, status_active))

    # ★ 快照缓存写回（方向4）：未命中重建后更新缓存（同快照下次命中）
    fiber._lines_cache = (snap_key, lines)
    return lines


def _vwidth(s: str) -> int:
    return wcswidth_simple(s)


@lru_cache(maxsize=512)
def _styled_completion_cached(text: str, item_type: str, match_prefix: str, cell_w: int) -> Line:
    """候选项行构建（内部缓存实现，见 ``_styled_completion``）。"""
    out = Line()
    # 方向F·步骤15（渲染错误防御）：候选项文本可能含换行符（/load 会话标题
    # 来自多行用户消息；Unix 文件名也允许 \n）——Line 内嵌字面换行会把一
    # "行"拆成多行，破坏帧行号/diff/光标定位。渲染前统一归一化为空格。
    text = text.replace("\n", " ")
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


def _styled_completion(text: str, item_type: str, match_prefix: str, cell_w: int) -> Line:
    """构建候选项行（命令/目录/匹配高亮）。

    ★ 性能（PERF-7）：frozen 输入（text/item_type/match_prefix/cell_w 均为
    str/int，可 hash）确定性输出 → lru_cache 缓存。补全弹窗 20+ 候选项每帧
    重建时，相同候选项行跨帧复用（调用方只读 ``.runs`` 不修改，缓存安全）；
    maxsize=512 有界（候选文本数量有限）。返回缓存的 Line 对象，跨帧引用
    稳定（diff 身份短路受益）。
    """
    return _styled_completion_cached(text, item_type, match_prefix, cell_w)


def _truncate_width(s: str, max_w: int) -> str:
    """按显示宽度截断字符串（不拆 CJK），返回截断后文本。

    ★ 性能（PERF-7）：纯 ASCII 可打印字符串宽度 == 字符数——C 实现的
    ``isascii()`` + ``isprintable()`` 单趟扫描后直接切片（逐字符
    ``wcswidth_simple`` 的 Python 循环仅用于含 CJK/emoji/控制字符的文本）。
    命令名/工具名等 ASCII 输入截断热路径受益。
    """
    if max_w <= 0:
        return ""
    if s.isascii() and s.isprintable():
        return s if len(s) <= max_w else s[:max_w]
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


# ── 标准 React Ink 组件（2026-08-05） ─────────────────────
# ★ 标准 React Ink 组件化（无例外收尾）：input-area 自定义 host（直接画布
#   绘制）迁移为标准函数组件 InputArea（Column 组件树）+ CompletionPopup
#   （补全弹窗 Column + TEXT）。生产代码经 App 组件树 ``h(InputArea, props)``
#   渲染；旧 host "input-area" 已彻底移除——遗留 host 绘制函数（``_measure``/
#   ``_paint``/``_build_separator_line``/``_merge``/``_compute_input_rows``/
#   ``_wrap_input_text``）与 ``register()`` 空入口已全部删除（无例外）：
#   ``_build_lines``（快照缓存）+ ``_build_popup_lines``（弹窗缓存）为
#   InputArea/CompletionPopup 组件内部渲染辅助，保留；分隔线构建统一经
#   ``_theme.sep_line``（BUG-72 行宽修复唯一真源）。


def _lines_to_text_elements(lines: list, prefix: str = "ia") -> list:
    """Line 行列表 → TEXT 元素列表（每行带索引 key）。

    styled 引用 = Line.runs（跨帧稳定——_build_lines 快照缓存命中时同一
    Line 对象）→ TEXT wrap 缓存命中（零重建）。
    """
    return [
        h(TEXT, {"key": f"{prefix}-{i}", "styled": ln.runs, "height": 1})
        for i, ln in enumerate(lines)
    ]


def CompletionPopup(props: dict) -> object:
    """React Ink 标准组件：命令补全弹窗（Column + TEXT 行）。

    Props:
        completion: CompletionState 或 None（不可见/无 items 时空 TEXT 零高度）。
        width: 弹窗宽度（终端列宽）。
        now: 当前 monotonic 时间（父组件传入，缓存键同源；缺省自取）。

    Returns:
        Column（标题 + 候选项 + 提示行）；弹窗不可见返回空 TEXT（h=0 不占行）。
    """
    completion = props.get("completion")
    width = props.get("width", 80)
    now = props.get("now")
    if now is None:
        now = time.monotonic()
    lines = _build_popup_lines(completion, width, now)
    if not lines:
        return h(TEXT, {"children": "", "key": "popup-empty"})
    return h(Column, {"key": "completion-popup"}, [
        h(TEXT, {"key": f"popup-{i}", "styled": ln.runs, "height": 1})
        for i, ln in enumerate(lines)
    ])


def _input_elements(props: dict, width: int, now: float, fade_state: dict) -> list:
    """构建输入区 Element 列表（弹窗 + 上分隔线 + 搜索行 + 输入行 + 时间戳）。

    复用 ``_build_lines`` 快照缓存语义（非弹窗行 include_popup=False）：
    每行 TEXT 带索引 key，styled 引用稳定（Line 跨帧复用）→ 零重建。

    Args:
        props: 输入区 props。
        width: 布局宽度。
        now: 当前 monotonic 时间。
        fade_state: 组件级渐显缓存（use_ref 持有，跨 use_memo 重算持久——
            修复组件化后占位符渐显每 0.1s 桶重置的 bug：fiber 为临时对象，
            渐显 key 丢失 → 渐显永远停在起点色）。
    """
    from types import SimpleNamespace
    fiber = SimpleNamespace(
        props=props,
        layout_box=SimpleNamespace(w=width, x=0, y=0),
        _placeholder_fade_key=fade_state.get("_placeholder_fade_key"),
    )
    # 补全弹窗：独立标准组件（Column + TEXT）
    children = [h(CompletionPopup, {
        "completion": props.get("completion"),
        "width": width,
        "now": now,
        "key": "ia-popup",
    })]
    # 其余行（上分隔线/搜索/输入行/下分隔线）
    rest = _build_lines(fiber, include_popup=False)
    # 渐显状态写回组件级缓存（_build_lines 内部经 _placeholder_fade_color
    # 更新 fiber._placeholder_fade_key——SimpleNamespace 可写，读回持久）
    fade_state["_placeholder_fade_key"] = getattr(fiber, "_placeholder_fade_key", None)
    children.extend(_lines_to_text_elements(rest, "ia"))
    return children


def InputArea(props: dict) -> object:
    """React Ink 标准组件：输入区（补全弹窗 + 分隔线 + 输入行 + 时间戳）。

    Props:
        text/cursor_pos/prompt/completion/status_active/cpu/mem/
        history_search: 输入区状态（与旧 host 同字段）。
        width: 布局宽度（App 传入，截断/布局同源）。

    Returns:
        Column（``dataInputArea`` 标记容器 + 透传 props——session 定位输入区
        与光标计算读取）。补全弹窗经独立 ``CompletionPopup`` 组件渲染。
    """
    width = props.get("width", 80)
    try:
        width = max(0, int(width))
    except (TypeError, ValueError, OverflowError):
        width = 80
    now = time.monotonic()
    # ★ 渐显状态组件级缓存（use_ref 跨渲染持久）——占位符 FadeIn 渐显依赖
    #   起始时间（fiber._placeholder_fade_key）。组件化后 fiber 为临时对象，
    #   渐显 key 若不持久，use_memo 跨桶重算时渐显永远停在起点色。
    fade_ref = use_ref({})
    # ★ deps 直接传原子值元组（不可再包一层——use_memo 内部 list(deps) 后
    #   逐项 _object_is：嵌套 tuple 按 is 引用比较恒 miss → 缓存永远失效）。
    children = use_memo(
        lambda: _input_elements(props, width, now, fade_ref.current),
        _input_snap_key(props, width, now),
    )
    # ★ key 保留传入值（缺省 "input-area"）——多实例/测试 fiber 替换检测。
    key = props.get("key", "input-area")
    return h(Column, {**props, "dataInputArea": True, "key": key}, children)


def _input_snap_key(props: dict, width: int, now: float):
    """InputArea use_memo 依赖（纯原子值，逐项 Object.is 值比较）。

    use_memo deps 逐项 ``_object_is``（React Object.is：int/bool 按值比较、
    其余按 is 引用比较）——**str 不做值比较**（仅 is），故 text/query 用
    ``id()+len()`` 指纹（模型字段引用稳定时 id 稳定；内容变化时 id/len 变）。
    嵌套 tuple 每帧新建会 is miss，已展开为原子值。时间桶与 _build_lines
    对齐（status_active 0.1s 桶 / 空闲 0.25s 桶）。

    ★ 性能（PERF-24）：props.get 去重——history_search 经局部变量一次提取
    （修复前逐字段 ``props.get("history_search")`` 调用 8 次；空值快路径
    直接返回常量元组，免重复 dict 查找 + 字段求值）。text 同样一次提取
    （修复前 ``props.get("text")`` 调用 3 次）。
    """
    text = props.get("text")
    text_str = "" if text is None else str(text)
    completion = props.get("completion")
    status_active = bool(props.get("status_active", False))
    max_input = max(1, width - len(_PROMPT))
    # history_search 一次提取（多处字段共享）
    search = props.get("history_search")
    # ★ review 方向（BUG-T1 同族修复）：str 指纹从 ``id()`` 改为 ``hash()``——
    #   原 ``id(text)`` 在 echo 回调每次新建 str 时恒 miss（缓存退化）；且 str
    #   对象被 GC 后 id 复用可能错误命中（内容不同但 id+len 相同）。``hash(str)``
    #   同进程内稳定、内容变化必变（碰撞概率可忽略）；值比较语义正确。
    return (
        hash(text_str),
        len(text_str),
        max_input,
        width,
        # completion 指纹
        bool(completion is not None and completion.visible),
        id(completion.items) if completion is not None else -1,
        len(completion.items) if completion is not None else 0,
        completion.selected if completion is not None else 0,
        id(completion.texts) if completion is not None else -1,
        len(completion.texts) if completion is not None and completion.texts else 0,
        id(completion.descriptions) if completion is not None else -1,
        len(completion.descriptions) if completion is not None and completion.descriptions else 0,
        bool(completion is not None and getattr(completion, "split_desc", False)),
        # 状态
        int(props.get("cpu", 0)),
        int(props.get("mem", 0)),
        status_active,
        # history_search 指纹（局部变量提取——一次 props.get）
        bool(search is not None and bool(getattr(search, "active", False))),
        hash(getattr(search, "query", "") or "") if search is not None else 0,
        len(getattr(search, "query", "") or "") if search is not None else 0,
        id(getattr(search, "matches", None)) if search is not None else -1,
        len(getattr(search, "matches", None) or []) if search is not None else 0,
        getattr(search, "index", -1) if search is not None else -1,
        # 时间桶
        int(now / 0.1) if status_active else int(now / 0.25),
    )


__all__ = [
    "InputArea",
    "CompletionPopup",
    "_build_lines",
    "_build_popup_lines",
    "_completion_height",
    "_is_search_active",
    "_compute_input_layout",
    "_cursor_visual_from_layout",
]

