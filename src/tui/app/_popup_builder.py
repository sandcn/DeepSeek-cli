"""补全弹窗行构建 — 弹窗标题/候选项/提示行 Line 生成 + 样式辅助。

模块边界（2026-08-05 架构优化）：从 ``app/input_area.py`` 拆分——弹窗行
构建为独立职责（纯渲染辅助，不涉及输入区行编排/组件树），供
``CompletionPopup`` 组件与 ``_build_lines``（include_popup=True）共享。

边界说明：
  - ``_build_lines``（输入区全量行快照缓存）保留在 ``input_area.py``
    （与组件/快照缓存耦合）。
  - 本模块仅依赖 ``_input``（_wrap_by_width）/``_input_metrics``（高度/
    分栏宽度）/``_theme``（分隔线样式辅助）/``output``（Line）/``core.style``。
"""

from __future__ import annotations

import time
from functools import lru_cache

from src.tui._screen import (
    wcswidth_simple,
)
from src.tui._input import (
    _wrap_by_width,
)
from src.tui._input_metrics import (
    _desc_column_width,
    _completion_height,
)
from src.tui.core.style import Style
from src.tui.ink import Line
from src.tui.app import _fx
from src.tui.app._theme import time_glow, _S_DIM, _S_SEP

#: 候选行样式说明（2026-08-15 L5）：选中高亮背景静态色 237 由
#: ``_build_popup_lines`` 函数内局部定义（弹窗不呼吸，避免每帧重绘）。
#: 模块级同名定义已删除（死代码——被局部遮蔽且无外部引用，见 L5）。


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
    # ★ review 修复：selected 越界钳制（绘制统一使用 sel）——selected 可能为
    #   负/超出 items 范围（外部状态注入异常 / items 变化后残留旧索引），越界
    #   时无高亮且标题位置 (n/total) 显示错乱；钳制到 [0, len(items)-1]。
    #   （缓存键 popup_snap 仍用原始 selected——值变化自然触发重建。）
    # ★ 2026-08-06：selected 非 int（None/str 等外部注入）时 `min(selected, n)`
    #   抛 TypeError——int() 归一化失败回退 0。
    try:
        sel = max(0, min(int(selected), len(items) - 1))
    except (TypeError, ValueError):
        sel = 0
    # ★ 修复（P2）：归一化后回写 completion.selected——``_completion_height``
    #   （_input_metrics）直接读原始 ``completion.selected``（split_desc 模式
    #   下 selected 被外部注入 None/str 时 min(selected, ...) 抛 TypeError）；
    #   回写后高度计算与绘制统一用归一化 int（同帧后续 _completion_height
    #   调用及外部 _cursor 定位均读到安全值）。
    completion.selected = sel
    match_prefix = completion.match_prefix or ""
    # ★ 缓存键稳定性（PERF-7 同族，BUG-73）：types 为空列表时用模块级空元组
    #   （恒同对象）——``completion.types or [""] * len(items)`` 每次创建新
    #   列表，`id(types)` 每帧变化 → 弹窗缓存永远 miss（每帧重建 20+ 候选项）。
    #   与 descs 的 ``descriptions or ()`` 修复一致。types 非空（show_completions
    #   传入）时保持列表引用稳定（不可变契约）。
    types = completion.types or ()
    # ★ 绘制用 types 列表：types 为空或长度不足 items 时补齐空串，保证
    #   ``types_disp[i]`` 对任意 i < len(items) 不越界（修复前 types 非空但
    #   长度 < len(items) 时越界 IndexError）。不进缓存键（键用稳定空元组 id）。
    types_disp = list(types) + [""] * (len(items) - len(types))
    title = completion.title or ""
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
    # 标题行（方向8：▍ 装饰条前缀——与错误/通知标记同语义，补全弹窗更醒目）
    # ★ 静态色（2026-08-05 修复，与 user_select 弹窗同）：补全弹窗不再呼吸
    #   ——弹窗是交互界面，呼吸色使弹窗行每帧随 time_glow 变化 → 渲染器每帧
    #   重写弹窗行（Termux 等终端闪烁/错乱）；静态色弹窗内容不变时 diff 零输出
    #   （仅打字 items 变化 / 导航 selected 变化时重绘）。
    title_color = 38
    head = Line.of(" \u258d", Style(fg=title_color, bold=True))
    head.append(" ", Style(fg=title_color, bold=True))
    head.append(title, Style(fg=title_color, bold=True))
    # ★ BEAUTY-17（体验）：导航位置提示 ``(2/10)``——选中项位置/总数，
    #   补全弹窗导航时用户可感知当前位置（替代仅总数）。总数取
    #   ``len(completion.texts)``（与项数一致；缺省回退 len(items)）。
    if total > 0:
        head.append(f" ({sel + 1}/{total})", Style(fg=title_color))
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
    # ★ 静态高亮背景（修复同标题：弹窗不呼吸，避免每帧重绘）
    # ★ L5（2026-08-15）：函数内局部定义 sel_bg（静态高亮背景色）——模块级
    #   同名定义已删除（死代码，被本局部遮蔽且无外部引用）；本局部供下方
    #   Style(fg=15, bg=sel_bg) 选中行高亮使用（L272/L313）。
    sel_bg = 237
    if split:
        # 左栏选项内容宽度（前缀 ▶ + 文本；右栏说明独立换行）
        cell_w = max(
            1, min(max((_vwidth(i) for i in items), default=10) + 4, opt_w - 2) - 3,
        )
        # ★ BUG-27（review 方向）：selected 越界钳制与 ``_completion_height``
        #   一致——修复前高度按 ``min(selected, len(descs)-1)`` 的说明行数
        #   计算、绘制却 ``descs[selected] if 0 <= selected < len(descs)``
        #   （越界时空说明）→ 弹窗底部多出空白行，测量高度与绘制不一致。
        desc_sel = max(0, min(sel, len(descs) - 1)) if descs else 0
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
                if i == sel:
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
            # ★ 静态说明列色（修复同标题：弹窗不呼吸，避免每帧重绘）
            if row < len(desc_lines):
                line.append(
                    _truncate_width(desc_lines[row], desc_w),
                    Style(fg=110),
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
            if i == sel:
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
                # ★ 静态描述色（修复同标题：弹窗不呼吸，避免每帧重绘）
                desc_budget = max(1, width - line.width)
                line.append(
                    _truncate_width(descs[i], desc_budget),
                    Style(fg=110),
                )
            # ★ 方向8（窄屏防溢出）：选项行超宽时截断至 width（不拆
            #   CJK）——修复前 `` ▶ /help 显示帮助`` 在窄屏撑爆行宽。
            if width > 0 and line.width > width:
                from src.tui.ink.helpers import truncate_line
                line = truncate_line(line, width)
            lines.append(line)
    # 底部提示（★ 静态提示色——修复同标题：弹窗不呼吸，避免每帧重绘）
    hint_color = 110  # 浅蓝（静态，原呼吸 110→126 的基色）
    hint = Line.of(" ", Style(fg=hint_color))
    hint.append("Tab \u2191\u2193 PgUp/PgDn Esc", Style(fg=hint_color))
    # ★ 方向8（窄屏防溢出）：提示行超宽时截断至 width。
    if width > 0 and hint.width > width:
        from src.tui.ink.helpers import truncate_line
        hint = truncate_line(hint, width)
    lines.append(hint)
    completion._popup_lines_cache = (popup_snap, lines)
    return lines


__all__ = [
    "_glow_color",
    "_placeholder_fade_color",
    "_build_popup_lines",
    "_vwidth",
    "_styled_completion_cached",
    "_styled_completion",
    "_truncate_width",
    "_append_truncated",
]
