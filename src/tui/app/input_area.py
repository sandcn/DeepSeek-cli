"""InputArea — 输入区 React Ink 标准组件（补全弹窗 + 分隔线 + 输入行）。

★ 标准 React Ink 组件化（2026-08-05，无例外收尾）：input-area 自定义 host
（直接画布绘制）迁移为**标准函数组件** ``InputArea``——返回 Column（组件
树）：``CompletionPopup``（补全弹窗 Column + TEXT）+ 上分隔线 TEXT + 反向
历史搜索 TEXT + 输入行 TEXT + 时间戳分隔线 TEXT。生产代码经 App 组件树
``h(InputArea, props)`` 渲染；旧 host "input-area" 与遗留 host 绘制函数
（``_measure``/``_paint``/``_build_separator_line``/``_merge``/
``_compute_input_rows``/``_wrap_input_text``）及 ``register()`` 空入口已
全部删除（无例外）——`_build_lines` 为组件内部渲染辅助（快照缓存），保留。

复用 _input.py 的 ``_expand_tabs`` / ``_wrap_by_width`` /
``_compute_cursor_visual_pos`` / ``_compute_input_layout`` /
``_cursor_visual_from_layout``（唯一真源），保证换行/CJK/光标计算与旧实现
一致。

方向5（光标算法单一真源）：``_compute_input_layout`` /
``_cursor_visual_from_layout`` 已迁移至 ``_input.py``（本文件从 _input 导入，
删除本地副本——input_area 与 session 共享同一实现，不再双实现）。

模块边界（2026-08-05 架构优化）：
  - ``_popup_builder.py`` — 补全弹窗行构建（标题/候选项/提示 Line + 样式
    辅助：``_build_popup_lines``/``_styled_completion``/``_truncate_width``/
    ``_append_truncated``/``_glow_color``/``_placeholder_fade_color``/
    ``_vwidth``）——本文件 re-export 保持旧导入路径兼容。
  - ``src/tui/_input_metrics.py`` — 输入区布局度量（``_completion_height``/
    ``_is_search_active``/``_desc_column_width``/``_completion_item_rows``/
    ``_LOCKED_PAD_LIMIT``，ink 依赖净化）——本文件 re-export 保持旧导入
    路径兼容（tests/test_tui/ink/test_completion_flash_fix.py 等）。

性能：``_build_lines`` 快照缓存语义保留（Line 跨帧引用稳定）——InputArea
经 ``use_memo`` 缓存 Element 列表（deps = 快照键），命中时 children 引用
稳定 → reconciler/layout/paint 短路（零重建）。★ P2-6：组件内部经**持久
fiber 引用**（use_ref 持有）使 ``_build_lines`` 的快照/换行布局缓存跨帧命中
（修复前每帧新建临时 fiber，缓存写回当帧即弃恒 miss——死缓存）。光标定位
由 session 经 ``dataInputArea`` 容器 + ``_compute_cursor_visual_pos`` 计算；
换行布局缓存（``fiber._input_layout_cache``）由 ``_build_lines`` 单点写入
（原遗留 ``_measure`` 写入职责收拢，session._position_cursor 复用）。

模式行（2026-08-14）：时间戳分隔线（下分隔线）下方新增一行——行最右侧
显示主 Agent 运行模式（Ctrl+B 切换，``src/prompt_builder.builder`` 的
``is_empty_mode()``）：「空模式」（金色 178 强调——特殊状态醒目）/
「标准模式」（暗灰 dim——常规状态零打扰）。左侧无分隔线填充（用户反馈
要求仅最右侧显示）。模式状态同时作为 ``_build_lines`` snap_key 与
InputArea ``_input_snap_key``（use_memo deps）字段——Ctrl+B 切换后即时
重建模式行（无需额外同步链路）。
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
# ★ 输入区布局度量（模块边界优化，2026-08-05）：_completion_height /
#   _is_search_active / _desc_column_width / _completion_item_rows /
#   _LOCKED_PAD_LIMIT 迁至 src/tui/_input_metrics.py（ink 依赖净化——
#   ink/session 光标定位不再反向依赖 app 层）；本模块 re-export 保持旧
#   导入路径兼容（tests/test_tui/ink/test_completion_flash_fix.py 等）。
from src.tui._input_metrics import (
    _LOCKED_PAD_LIMIT,
    _desc_column_width,
    _completion_item_rows,
    _completion_height,
    _is_search_active,
)
# ★ 补全弹窗行构建（模块边界优化，2026-08-05）：弹窗标题/候选项/提示行
#   Line 生成 + 样式辅助迁至 _popup_builder.py（弹窗构建独立职责）；本模块
#   re-export 保持旧导入路径兼容（test_completion_flash_fix.py 等）。
from src.tui.app._popup_builder import (
    _glow_color,
    _placeholder_fade_color,
    _build_popup_lines,
    _vwidth,
    _styled_completion_cached,
    _styled_completion,
    _truncate_width,
    _append_truncated,
)
from src.tui.core.style import Style
from src.tui.ink import h, TEXT, Column, Line, use_memo, use_ref
from src.tui.ink.widgets.interactive import SelectInput
from src.tui.app import _fx
from src.tui.app._theme import sep_line as _theme_sep_line, time_glow, _S_ACCENT, _S_DIM, _S_TEXT, _S_TIME

# 占位符
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"
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

# ── 主 Agent 运行模式行（时间戳分隔线下方，2026-08-14） ──────────
#: 空模式显示文本（Ctrl+B 切换；系统提词替换为 prompts_export_main_empty.md）
_MODE_EMPTY_TEXT = "空模式"
#: 标准模式显示文本（完整规则集）
_MODE_STANDARD_TEXT = "标准模式"
#: 空模式文本强调色（金色 178——特殊状态醒目，与解析阶段标签同色系）
_S_MODE_EMPTY = Style(fg=178)


def _build_mode_line(width: int, empty_mode: bool) -> Line:
    """构建主 Agent 运行模式行（时间戳分隔线下方，最右侧显示当前运行模式）。

    Ctrl+B 切换空模式（``src/prompt_builder.builder.is_empty_mode()``）：
      - 空模式（True）：显示「空模式」（金色 178 强调——特殊状态醒目）；
      - 标准模式（False）：显示「标准模式」（暗灰 dim——常规状态零打扰）。
    ★ 左侧无分隔线填充（2026-08-14 用户反馈：模式行左边不要分割线）——
    左侧空白 + 最右侧模式文本。行宽恒 = width（行级 diff 行宽不变量）；
    窄屏时内容按预算截断不溢出（与 CPU/MEM/时间戳分隔线截断语义一致）。

    Args:
        width: 行总宽（终端列宽）。
        empty_mode: 是否处于主 Agent 空模式（True=空模式）。

    Returns:
        模式行（Line，行宽 = width；左侧空白 + 右侧模式文本）。
    """
    text = _MODE_EMPTY_TEXT if empty_mode else _MODE_STANDARD_TEXT
    style = _S_MODE_EMPTY if empty_mode else _S_DIM
    line = Line()
    # 预算按显示宽度计（CJK 字符显示宽 2 列——len() 为字符数不准确）
    mode_w = wcswidth_simple(f" {text}")
    pad = max(0, width - mode_w)
    if pad > 0:
        line.append(" " * pad, None)
    _append_truncated(line, f" {text}", style, max(0, width))
    # 窄屏（width < mode_w）内容截断后可能不足 width——补空格保持行宽
    # 不变量（行级 diff 行宽恒 = width，与分隔线行语义一致）。
    if width > 0 and line.width < width:
        line.append(" " * (width - line.width), None)
    return line


def _build_lines(fiber, include_popup: bool = True) -> list[Line]:
    """构建输入区行列表（弹窗/分隔线/搜索/输入行/时间戳/模式行）。

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
        # ★ P2-9（review 修复）：fade_duration 惰性读取 TuiConfig
        #   （``_default_fx_params()[0]``）——修复前用模块导入时固化的
        #   ``_DEFAULT_FADE_DURATION``（0.6s 快照），运行期修改 TuiConfig
        #   不生效（与 ``fade_color`` 惰性读取不一致）。
        fade_duration = _fx._default_fx_params()[0]
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
            # ★ 修复（P3）：completion.items 可能为 None（外部注入）——
            #   len(None) 抛 TypeError；`or []` 防御（id() 不变——id(None)
            #   安全且稳定；换 `or []` 会让空列表每次新建对象 id 变化 →
            #   快照缓存恒 miss）。
            len(completion.items or []),
            completion.selected,
            # ★ P2-2（review 修复）：texts/descriptions 同样可能为 None
            #   （外部注入）——``len(None)`` 抛 TypeError；``or []`` 防御
            #   （与 items 对齐；id(None) 稳定，不破坏缓存命中）。
            id(completion.texts),
            len(completion.texts or []),
            id(completion.descriptions),
            len(completion.descriptions or []),
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
            # ★ P3（review 2026-08-19）：matches 可能为 None（外部注入）——
            #   与 _input_snap_key 同防御（``or []``），len(None) 抛
            #   TypeError 中断输入区渲染。
            len(search.matches or []),
            search.index,
        )
    else:
        search_snap = (False, "", 0, 0, -1)
    # ★ 主 Agent 运行模式（Ctrl+B 切换，2026-08-14）：模式状态进快照缓存
    #   键——切换后 snap_key 变化 → 模式行重建（无需额外同步链路）。读取
    #   失败回退标准模式（False，不崩溃）；开销为单次模块布尔读取。
    try:
        from src.prompt_builder.builder import is_empty_mode
        empty_mode = is_empty_mode()
    except Exception:
        empty_mode = False
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
        empty_mode,  # ★ 主 Agent 运行模式（Ctrl+B 切换即时刷新模式行）
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
    # ★ P2-7：``max(1, width - max(0, width - cpu_mem_w))`` 等价
    #   ``max(1, min(width, cpu_mem_w))``——简化表达式（保留 max(1, ...)
    #   语义：width<=0 或内容超宽时预算至少 1 列）。
    content_budget = max(1, min(width, cpu_mem_w))
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
        # ★ P2-8：search.query 可能为 None（外部注入/异常状态）——
        #   ``Line.append(None)`` 行为未定义，回退空串（防御性，正常路径
        #   query 恒为 str）。
        q = search.query or ""
        match = ""
        if search.matches and 0 <= search.index < len(search.matches):
            match = search.matches[search.index]
        sline = Line.of("(reverse-i-search)`", _S_ACCENT)
        # ★ 静态 query 色（修复同弹窗：搜索行不呼吸，避免每帧重绘——修复前
        #   query 呼吸色 221↔232 使搜索激活无输入时仍 10Hz 渲染重绘）
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
    # ★ BEAUTY-36（2026-08-19 美化）：时间戳加 ◷ 时钟盘面图标前缀
    #   （单宽符号，wcswidth_simple 计 1）——时间信息一眼可辨。
    ts_disp = f"\u25f7 {ts}"
    time_w = len(ts_disp) + 2
    # ★ BEAUTY-13（动效）：下分隔线（时间戳行）呼吸——活跃/流式期间与
    #   上分隔线/状态栏分隔线同周期青色呼吸（32-45，8s），三条分隔线视觉
    #   联动；空闲保持静态深灰（_S_SEP，零额外渲染成本）。★ 方向5：统一
    #   经 _theme.sep_style。
    # 方向1 步骤4（窄屏防溢出）：sep_len 下限 0 + 时间戳内容独立行截断
    # （width < 22 时不超宽；正常宽度时间戳完整保留）
    # ★ P2-7（同 CPU/MEM 分隔线）：简化 ``max(1, width - max(0, width - time_w))``
    #   为等价 ``max(1, min(width, time_w))``。
    content_budget = max(1, min(width, time_w))
    content = Line()
    _append_truncated(content, f" {ts_disp}", _S_TIME, content_budget)
    lines.append(_theme_sep_line(width, content, status_active))

    # ── 主 Agent 运行模式行（时间戳下方，最右侧显示空模式/标准模式） ──
    # ★ 2026-08-14：时间戳分隔线下方新增一行——最右侧显示当前运行模式
    #   （Ctrl+B 切换：空模式金色强调 / 标准模式暗灰 dim）。左侧无分隔线
    #   （用户反馈：模式行左边不要分割线）；模式状态已进 snap_key，
    #   Ctrl+B 切换后本函数重建（外层 InputArea use_memo deps 亦含模式）。
    lines.append(_build_mode_line(width, empty_mode))

    # ★ 快照缓存写回（方向4）：未命中重建后更新缓存（同快照下次命中）
    fiber._lines_cache = (snap_key, lines)
    return lines


# ── 标准 React Ink 组件（2026-08-05） ─────────────────────
# ★ 标准 React Ink 组件化（无例外收尾）：input-area 自定义 host（直接画布
#   绘制）迁移为标准函数组件 InputArea（Column 组件树）+ CompletionPopup
#   （补全弹窗 Column + TEXT）。生产代码经 App 组件树 ``h(InputArea, props)``
#   渲染；旧 host "input-area" 已彻底移除——遗留 host 绘制函数（``_measure``/
#   ``_paint``/``_build_separator_line``/``_merge``/``_compute_input_rows``/
#   ``_wrap_input_text``）与 ``register()`` 空入口已全部删除（无例外）：
#   ``_build_lines``（快照缓存）与 ``_build_popup_lines``（弹窗缓存，已迁移
#   ``_popup_builder``）为 InputArea/CompletionPopup 组件内部渲染辅助，保留；
#   分隔线构建统一经 ``_theme.sep_line``（BUG-72 行宽修复唯一真源）。


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
    """React Ink 标准组件：命令补全弹窗（Column + SelectInput 控件 + TEXT）。

    ★ 全面控件化（方案B）：候选项列表经标准控件 ``SelectInput`` 表达——
    导航（↑↓/PgUp/PgDn）由控件消费并写回 ``completion.selected``
    （onHighlight）；Enter 放行（补全确认由 InputDispatcher 旧路径接管——
    无 onSelect 时控件不消费 enter）；Esc 放行（关闭弹窗由 InputDispatcher
    处理——无 onCancel）；``limit`` = 锁定高度可见行数（高度锁定防闪烁语义
    保持，底部补白）；``renderItem`` 复用候选项行视觉（▶ 高亮 + match 前缀
    高亮 + 描述灰显）。标题/提示行保持 TEXT（基础控件）。

    ★ 分栏说明模式（split_desc——历史 user_select 场景，生产已迁移
    UserSelectPopup 不再触发）回退 ``_build_popup_lines`` 旧路径（兼容
    既有调用/测试；分栏行需滚动偏移，SelectInput renderItem 无法表达）。

    Props:
        completion: CompletionState 或 None（不可见/无 items 时空 TEXT 零高度）。
        width: 弹窗宽度（终端列宽）。
        now: 当前 monotonic 时间（父组件传入，缓存键同源；缺省自取）。

    Returns:
        Column（标题 + SelectInput 候选项 + 提示行）；弹窗不可见返回空
        TEXT（h=0 不占行）。
    """
    completion = props.get("completion")
    width = props.get("width", 80)
    now = props.get("now")
    if now is None:
        now = time.monotonic()
    if completion is None or not completion.visible or not completion.items:
        return h(TEXT, {"children": "", "key": "popup-empty"})
    items = list(completion.items)
    # selected 钳制（_build_popup_lines 同语义——外部注入异常/越界归一化）
    try:
        sel = max(0, min(int(completion.selected), len(items) - 1))
    except (TypeError, ValueError):
        sel = 0
    completion.selected = sel
    match_prefix = completion.match_prefix or ""
    types = completion.types or ()
    types_disp = list(types) + [""] * (len(items) - len(types))
    title = completion.title or ""
    descs = completion.descriptions if completion.descriptions else ()
    split = bool(getattr(completion, "split_desc", False)) and bool(descs)
    # ★ 分栏说明模式回退旧路径（生产无触发；兼容既有调用/测试）
    if split:
        lines = _build_popup_lines(completion, width, now)
        return h(Column, {"key": "completion-popup"}, [
            h(TEXT, {"key": f"popup-{i}", "styled": ln.runs, "height": 1})
            for i, ln in enumerate(lines)
        ])
    # 锁定可见行数（高度锁定防闪烁：_completion_height-2，与 _build_popup_lines
    # 一致——items 减少时底部补白空行，doc 高度不变）
    n_rows = max(0, _completion_height(completion, width) - 2)
    total = len(items)
    sel_bg = 237

    # ── 标题行（静态色——弹窗不呼吸，避免每帧重绘） ──
    # ★ BEAUTY-36（2026-08-19 美化）：标题色 38 → 45——与 _build_popup_lines /
    #   user_select / editmsg 三处弹窗标题统一亮青加粗。
    title_color = 45
    head = Line.of(" \u258d", Style(fg=title_color, bold=True))
    head.append(" ", Style(fg=title_color, bold=True))
    head.append(title, Style(fg=title_color, bold=True))
    if total > 0:
        head.append(f" ({sel + 1}/{total})", Style(fg=title_color))
    if head.width > width:
        from src.tui.ink.helpers import truncate_line
        head = truncate_line(head, width)

    # ── 候选项（SelectInput 标准控件） ──
    cell_w = max(
        1, min(max((_vwidth(i) for i in items), default=10) + 4, width - 2) - 3,
    )

    def _render_item(item, idx, is_sel):
        """候选项行渲染（▶ 高亮 + match 前缀高亮 + 描述灰显）。

        SelectInput 调用 renderItem 时 item 为规范化 dict
        （``{"label", "value"}``）——取 label 渲染；选中态经第三参
        ``is_sel``（控件内部 state——导航后自动更新，非闭包 selected）。
        """
        label = item["label"] if isinstance(item, dict) else str(item)
        line = Line()
        if is_sel:
            line.append(" \u25b6 ", Style(fg=15, bg=sel_bg))
        else:
            line.append("   ")
        for run in _styled_completion(label, types_disp[idx], match_prefix, cell_w).runs:
            line.append_run(run)
        # 斜杠命令描述灰显（command 且描述非空）
        if types_disp[idx] == "command" and idx < len(descs) and descs[idx]:
            line.append("  ", _S_DIM)
            desc_budget = max(1, width - line.width)
            line.append(_truncate_width(descs[idx], desc_budget), Style(fg=110))
        if width > 0 and line.width > width:
            from src.tui.ink.helpers import truncate_line
            line = truncate_line(line, width)
        return h(TEXT, {"styled": line.runs, "height": 1})

    select_items = [{"label": item, "value": item} for item in items]
    control = h(SelectInput, {
        "key": "popup-items",
        "items": select_items,
        "initialIndex": sel,
        # ★ P1（review 2026-08-19）：受控 index——InputDispatcher 旧路径
        #   （PgUp/PgDn/Shift+Tab/边界回绕经 cycle_completion 写回
        #   completion.selected）外部改写后，SelectInput 内部高亮渲染期
        #   同步跟随（可见高亮 == 实际选中，Tab 补全项一致）。
        "index": sel,
        "limit": n_rows if n_rows > 0 else None,
        "onHighlight": lambda idx: setattr(completion, "selected", idx),
        "renderItem": _render_item,
        "focus": True,
        "consumeAll": False,
    })

    # 高度锁定补白：items 减少时弹窗底部补空行（doc 高度不变——防闪烁）
    pad_rows = max(0, n_rows - min(total, n_rows))

    # ── 提示行（静态色） ──
    hint = Line.of(" ", Style(fg=110))
    hint.append("Tab \u2191\u2193 PgUp/PgDn Esc", Style(fg=110))
    if width > 0 and hint.width > width:
        from src.tui.ink.helpers import truncate_line
        hint = truncate_line(hint, width)

    children = [
        h(TEXT, {"key": "popup-head", "styled": head.runs, "height": 1}),
        control,
    ]
    if pad_rows > 0:
        children.append(h(TEXT, {"key": "popup-pad", "children": "", "height": pad_rows}))
    children.append(h(TEXT, {"key": "popup-hint", "styled": hint.runs, "height": 1}))
    return h(Column, {"key": "completion-popup"}, children)


def _input_elements(props: dict, width: int, now: float, fade_state: dict) -> list:
    """构建输入区 Element 列表（弹窗 + 上分隔线 + 搜索行 + 输入行 + 时间戳）。

    复用 ``_build_lines`` 快照缓存语义（非弹窗行 include_popup=False）：
    每行 TEXT 带索引 key，styled 引用稳定（Line 跨帧复用）→ 零重建。

    ★ P2-6（死缓存修复）：fiber 为**持久对象**（存于组件级 fade_state，
    use_ref 持有跨渲染持久）——修复前每帧新建 ``SimpleNamespace`` fiber，
    ``_build_lines`` 内部快照缓存（``fiber._lines_cache``）与换行布局缓存
    （``fiber._input_layout_cache``）写回临时对象当帧即弃 → 生产路径恒 miss
    （死缓存：每帧全量重建行 + 换行计算）。持久引用后同快照/同
    text/max_input 帧缓存命中（外层 use_memo 兜底之外再省一层重建）。
    props/layout_box 每帧更新（fiber 本体跨帧复用）。

    Args:
        props: 输入区 props。
        width: 布局宽度。
        now: 当前 monotonic 时间。
        fade_state: 组件级缓存（use_ref 持有，跨 use_memo 重算持久）——
            ① 占位符渐显缓存（修复组件化后占位符渐显每 0.1s 桶重置的 bug：
            fiber 为临时对象时渐显 key 丢失 → 渐显永远停在起点色）；
            ② P2-6：持久 fiber 对象（``fade_state["_fiber"]``）。
    """
    from types import SimpleNamespace
    fiber = fade_state.get("_fiber")
    if fiber is None:
        # 首次：创建持久 fiber 对象（含缓存字段，SimpleNamespace 动态可写）
        fiber = SimpleNamespace(
            props=props,
            layout_box=SimpleNamespace(w=width, x=0, y=0),
            _placeholder_fade_key=fade_state.get("_placeholder_fade_key"),
        )
        fade_state["_fiber"] = fiber
    else:
        # 跨帧复用：仅更新可变字段（props/布局宽度）
        fiber.props = props
        fiber.layout_box.w = width
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
    # ★ P2（review 2026-08-18）：外层 deps 时间桶对齐 _build_lines 的 fading
    #   判定——渐显期（fade_key 起始后 elapsed < fade_duration）同样用 0.1s
    #   桶驱动 use_memo 重算。修复前仅 status_active 用 0.1s 桶，空闲渐显期
    #   外层 0.25s 桶粒度重算 → 内层 0.1s 桶形同虚设，占位符渐显以 ~3 个
    #   粗糙步进呈现（注释声称对齐与实现不符）。fade_duration 惰性读取
    #   TuiConfig（与 _build_lines/_placeholder_fade_color 一致）。
    fade_key = fade_ref.current.get("_placeholder_fade_key")
    fading = False
    if fade_key is not None:
        try:
            fade_duration = _fx._default_fx_params()[0]
            fading = fade_duration > 0 and (now - fade_key[1]) < fade_duration
        except (TypeError, IndexError, ValueError):
            fading = False
    # ★ deps 直接传原子值元组（不可再包一层——use_memo 内部 list(deps) 后
    #   逐项 _object_is：嵌套 tuple 按 is 引用比较恒 miss → 缓存永远失效）。
    children = use_memo(
        lambda: _input_elements(props, width, now, fade_ref.current),
        _input_snap_key(props, width, now, fading),
    )
    # ★ key 保留传入值（缺省 "input-area"）——多实例/测试 fiber 替换检测。
    key = props.get("key", "input-area")
    return h(Column, {**props, "dataInputArea": True, "key": key}, children)


def _input_snap_key(props: dict, width: int, now: float, fading: bool = False):
    """InputArea use_memo 依赖（纯原子值，逐项 Object.is 值比较）。

    use_memo deps 逐项 ``_object_is``（React Object.is：int/bool/str 按值
    比较——BUG-44 修复后 str 按值比较；其余按 is 引用比较）。text 直接放
    ``text_str`` 值（修复前 ``hash()+len()`` 指纹：哈希碰撞可致**错误命中**
    ——不同文本 hash 相同且 len 相同时代入旧缓存；str 已按值比较，无需指纹）。
    ★ P3（review）：query 同样直传字符串值——修复前保留 ``hash()+len()``
    指纹（两个不同 query hash 与 len 均相同时错误命中缓存，搜索覆盖行显示
    陈旧查询）；``_object_is`` 对 str 按值比较已支持，指纹无必要。嵌套
    tuple 每帧新建会 is miss，已展开为原子值。时间桶与 _build_lines 对齐
    （status_active **或 fading 渐显期** 0.1s 桶 / 空闲 0.25s 桶——P2 review
    2026-08-18：fading 由 InputArea 从持久 fade_state 读取传入，对齐内层
    ``status_active or fading`` 判定）。

    ★ 性能（PERF-24）：props.get 去重——history_search 经局部变量一次提取
    （修复前逐字段 ``props.get("history_search")`` 调用 8 次；空值快路径
    直接返回常量元组，免重复 dict 查找 + 字段求值）。text 同样一次提取
    （修复前 ``props.get("text")`` 调用 3 次）。

    Args:
        fading: 占位符渐显期标志（InputArea 从 fade_ref 读取 fade_key 判定）；
            缺省 False 保持既有调用方兼容。
    """
    text = props.get("text")
    text_str = "" if text is None else str(text)
    completion = props.get("completion")
    status_active = bool(props.get("status_active", False))
    max_input = max(1, width - len(_PROMPT))
    # history_search 一次提取（多处字段共享）
    search = props.get("history_search")
    # ★ 主 Agent 运行模式（Ctrl+B 切换，2026-08-14）：进 use_memo deps——
    #   模式切换后 InputArea 重建（_build_lines snap_key 已含模式，双保险
    #   即时刷新）。单次模块布尔读取，开销可忽略。
    try:
        from src.prompt_builder.builder import is_empty_mode
        empty_mode_flag = is_empty_mode()
    except Exception:
        empty_mode_flag = False
    # ★ P2-3（review 修复）：text 直接放 ``text_str`` 值——``_object_is`` 对
    #   str 按值比较（BUG-44 修复后），无需 hash()+len() 指纹（哈希碰撞可致
    #   错误命中：不同文本 hash+len 相同时代入旧缓存）。
    # ★ P3（review）：query 同样直传字符串值（同上指纹去除）。
    return (
        text_str,
        max_input,
        width,
        # completion 指纹
        bool(completion is not None and completion.visible),
        id(completion.items) if completion is not None else -1,
        len(completion.items or []) if completion is not None else 0,
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
        empty_mode_flag,  # ★ 主 Agent 运行模式（Ctrl+B 切换即时刷新）
        # history_search 指纹（局部变量提取——一次 props.get）
        bool(search is not None and bool(getattr(search, "active", False))),
        str(getattr(search, "query", "") or "") if search is not None else "",
        id(getattr(search, "matches", None)) if search is not None else -1,
        len(getattr(search, "matches", None) or []) if search is not None else 0,
        getattr(search, "index", -1) if search is not None else -1,
        # 时间桶（★ P2 review 2026-08-18：fading 渐显期同样 0.1s 桶——
        #   对齐 _build_lines 内层 ``status_active or fading`` 判定）
        int(now / 0.1) if (status_active or fading) else int(now / 0.25),
    )


__all__ = [
    "InputArea",
    "CompletionPopup",
    "_build_lines",
    "_build_mode_line",
    "_build_popup_lines",
    "_completion_height",
    "_is_search_active",
    "_compute_input_layout",
    "_cursor_visual_from_layout",
    "_MODE_EMPTY_TEXT",
    "_MODE_STANDARD_TEXT",
    "_S_MODE_EMPTY",
]
