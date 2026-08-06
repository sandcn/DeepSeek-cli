"""StyledRun 换行/截断工具 — wrap_runs_by_width + truncate 系列。

模块边界（2026-08-05 架构优化）：从 ``ink/helpers.py`` 拆分——StyledRun
序列的宽度换行与截断为独立职责（纯函数，依赖 ``_width`` 与 ``output``
的 StyledRun/Line），供 ``_layout_measure``（TEXT 测量换行）/``_style_utils``
（无）/``_border_box``（标题截断）/``helpers`` 门面共享。
"""

from __future__ import annotations

from src.tui._width import wcswidth_simple
from src.tui.core.style import Style
from .output import StyledRun, Line
from ._ansi_utils import _is_plain_ascii_fast


def wrap_runs_by_width(runs: list[StyledRun], max_width: int, hard: bool = False) -> list[Line]:
    """将 StyledRun 序列按显示宽度换行为多行。

    ``\\n`` 作为强制换行符：直接结束当前行（react-ink Text 语义——文本
    按 ``\\n`` 拆分为多个逻辑行，再对各逻辑行按宽度 wrap）。修复前 ``\\n``
    被当作零宽字符留在行内（宽度测量正确但渲染出单行含字面换行符，
    终端按物理行拆分破坏行级 diff 宽度不变量）。

    词边界换行（方向8）：超宽且当前行内含空格断点时**优先在空格处断行**
    （react-ink ``textWrap="wrap"`` 语义——保留词完整性）。断点取行内最后
    一个空格（空格不保留在行尾；下一行从空格后开始）；行首空格不成为断点。
    无空格断点（长单词/长 CJK）时回退字符级硬拆（与既有行为一致，
    ``test_wrap_by_width``/``test_wrap_cjk`` 锁定）。样式跨行保持
    （相邻同 style 字符经 ``Line.append`` 自动合并）。

    ``hard=True``（react-ink ``textWrap="hard"`` 语义，方向 G）：忽略空格
    断点，始终字符级硬拆——每行填满 max_width（必要时拆词）。与默认
    wrap 模式的差异仅在「行内存在空格断点时」——hard 不保留词完整性。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 每行最大显示宽度；<=0 表示不换行。
        hard: True 时字符级硬拆（忽略空格断点）；默认 False（词边界换行）。

    Returns:
        换行后的 Line 列表。
    """
    if max_width <= 0:
        # ★ BUG-34（review 方向）：width<=0 早返回也按 ``\n`` 拆行——修复前
        #   含换行文本被原样拼进单行（Line 内嵌字面换行符，终端按物理行拆分，
        #   破坏行级 diff 宽度不变量；与正宽路径的 ``\n`` 强制换行语义不一致）。
        # ★ BUG-70（review 方向，空行语义）：``a\n\nb`` 在零宽分支丢中间空行
        #   （产出 ``["a","b"]``）——修复前 ``if cur.runs`` 只在行非空时结束，
        #   空行（``\n`` 紧邻）被静默丢弃；正宽分支（行首 ``\n`` 产生空行）与
        #   FrameBuilder 均保留空行。修复：每个 ``\n`` 无条件结束当前行
        #   （空行也 append），与正宽/FrameBuilder 语义一致。
        lines_flat: list[Line] = []
        cur = Line()
        for run in runs:
            segs = run.text.split("\n")
            for si, seg in enumerate(segs):
                if si > 0:
                    lines_flat.append(cur)
                    cur = Line()
                if seg:
                    cur.append(seg, run.style)
        if cur.runs:
            lines_flat.append(cur)
        return lines_flat
    # ★ 性能（纯 ASCII 批量快路径，PERF-22）：单 run 且文本为可打印 ASCII
    #   （0x21-0x7E，无空格/换行/控制字符）时——每字符宽度恒 1、无词边界
    #   断点（无空格）、无强制换行（无 ``\n``）→ 按 max_width 直接字符串
    #   切片分段（C 级切片，免 100k 字符逐字符展开 tuple + wcswidth_simple
    #   调用 + 逐字符 append）。与通用路径语义等价（字符级硬拆、每行
    #   max_width 字符、保持样式）。100k 字符 wrap 从 ~0.42s → ~0.01s。
    if len(runs) == 1:
        _run = runs[0]
        _text = _run.text
        if _text and _is_plain_ascii_fast(_text):
            style = _run.style
            mw = max_width
            return [
                Line([StyledRun(_text[i:i + mw], style)])
                for i in range(0, len(_text), mw)
            ]
    # 展开为 (ch, style) 序列——词边界断行需跨 run 追踪行内空格位置
    items: list[tuple[str, Style | None]] = []
    for run in runs:
        for ch in run.text:
            items.append((ch, run.style))
    n = len(items)
    if n == 0:
        return []
    lines: list[Line] = []
    i = 0
    while i < n:
        # 贪心填充一行（不超 max_width）
        j = i
        width = 0
        last_space = -1  # 本行内最后一个空格的索引（绝对）
        while j < n:
            ch, _ = items[j]
            if ch == "\n":
                break  # 强制换行
            # ★ 先记录空格断点再判超宽：超宽字符本身是空格时（行恰好填满
            #   后在空格前断行），该空格须作为断点（end=空格、下一行从空格
            #   后开始）——修复前 break 在 last_space 记录之前，行内最后一个
            #   空格未被记录，断点回退到字符级 → 下一行以空格开头（如
            #   "brown" 被拆成 " brow"/"n"）。
            if ch == " ":
                last_space = j
            cw = wcswidth_simple(ch)
            if width + cw > max_width and j > i:
                break
            width += cw
            j += 1
        if j == i:
            # 行首字符即超宽（无法放下）或行首为强制换行
            if items[i][0] == "\n":
                # 行首强制换行：产生空行（Newline 组件渲染语义）
                lines.append(Line())
                i += 1
                continue
            # 行首字符即超宽：硬塞一个字符（CJK 宽字符仍可能单字符超宽——
            # 无法避免，与既有行为一致）。
            end = i + 1
            next_i = i + 1
        elif j < n and items[j][0] == "\n":
            # 强制换行：本行到 \n 前，下一行从 \n 后开始
            end = j
            next_i = j + 1
        elif j < n and last_space > i and not hard:
            # 词边界断行：本行到空格前（不含空格），下一行从空格后开始
            end = last_space
            next_i = last_space + 1
        else:
            # 无空格断点：字符级断开（本行到 j）
            end = j
            next_i = j
        line = Line()
        # ★ 方向8（性能）：段级 join 追加（同 style 字符累积到 list，一次
        #   join 后 append）——修复前逐字符 ``line.append(ch, st)`` 在
        #   大单行（100k 字符）下 ``last.text + text`` 反复复制累积串导致
        #   O(n²)（100k 字符 wrap 耗 1.5s+）。行宽有界，同 style 段 join
        #   成本 O(行宽)；样式切换处段级拆分（跨 style 不合并）。
        chars: list[str] = []
        seg_style = items[i][1] if i < end else None
        for k in range(i, end):
            ch, st = items[k]
            if st != seg_style:
                if chars:
                    line.append("".join(chars), seg_style)
                    chars = []
                seg_style = st
            chars.append(ch)
        if chars:
            line.append("".join(chars), seg_style)
        if line.runs:
            lines.append(line)
        i = next_i
    return lines


def _first_logical_line_runs(runs: list[StyledRun]) -> list[StyledRun]:
    """截取 runs 的第一个逻辑行（``\\n`` 前），保持样式。

    行级 diff 宽度不变量要求每个 ink Line 不内嵌字面换行符——truncate 系列
    （单行截断语义）对含 ``\\n`` 的 styled 输入须先归一化为首个逻辑行，避免
    截断结果保留字面 ``\\n``（渲染时终端按物理行拆分，破坏行级 diff）。

    Args:
        runs: StyledRun 列表（连续片段）。

    Returns:
        首个 ``\\n`` 前的 runs 子集（原样式）；无 ``\\n`` 时返回原列表引用
        （零拷贝，热路径快路径）。
    """
    for run in runs:
        if "\n" in run.text:
            break
    else:
        return runs  # 无换行：原引用返回（免拷贝）
    out: list[StyledRun] = []
    for run in runs:
        idx = run.text.find("\n")
        if idx < 0:
            out.append(run)
            continue
        if idx > 0:
            out.append(StyledRun(run.text[:idx], run.style))
        break  # 首个 \n 后的内容全部丢弃（单行截断语义）
    return out


def truncate_runs(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """将 StyledRun 序列截断至 max_width 显示宽度（保持样式）。

    超宽部分丢弃；截断点在字符边界，不拆分宽字符（CJK）。

    含 ``\\n`` 文本先归一化为首个逻辑行（单行截断语义，防字面换行破坏行宽）。
    """
    if max_width < 0:
        return []
    runs = _first_logical_line_runs(runs)
    out: list[StyledRun] = []
    width = 0
    for run in runs:
        if width >= max_width:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
    return out


def truncate_runs_ellipsis(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """将 StyledRun 序列截断至 max_width 显示宽度并追加省略号 ``…``（保持样式）。

    内容不超过 max_width 时原样返回（不追加省略号）；超过时截断内容至
    max_width-1 宽度（不拆分宽字符 CJK，宽度依据 ``wcswidth_simple``）并
    追加 ``…``（宽度 1）。省略号沿用截断点所在 run 的样式（与截断内容
    同 run，保持样式一致性）。

    含 ``\\n`` 文本先归一化为首个逻辑行（单行截断语义，防字面换行破坏行宽）。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 最大显示宽度；<=0 返回空列表。

    Returns:
        截断后的 StyledRun 列表（总宽度 <= max_width）。
    """
    # ★ P3-11（review 方向）：边界统一为 ``<= 0``——与 truncate_runs_start/
    #   truncate_runs_middle 一致（修复前 `< 0`：max_width=0 落入主逻辑，
    #   budget=-1 等边界分支依赖后续逻辑兜底；显式早返回语义更清晰）。
    if max_width <= 0:
        return []
    runs = _first_logical_line_runs(runs)
    total = 0
    for run in runs:
        total += run.width
    if total <= max_width:
        return list(runs)
    budget = max_width - 1
    out: list[StyledRun] = []
    ellipsis_style: Style | None = runs[0].style if runs else None
    width = 0
    for run in runs:
        if width >= budget:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > budget:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
            ellipsis_style = run.style
    if width < max_width:
        out.append(StyledRun("…", ellipsis_style))
    return out


def _runs_total_width(runs: list[StyledRun]) -> int:
    """StyledRun 序列总显示宽度。"""
    total = 0
    for run in runs:
        total += run.width
    return total


def _keep_head(runs: list[StyledRun], budget: int) -> list[StyledRun]:
    """保留 runs 开头最多 budget 宽度（保持 run 顺序与样式，不拆 CJK）。"""
    out: list[StyledRun] = []
    width = 0
    for run in runs:
        if width >= budget:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > budget:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
    return out


def _keep_tail(runs: list[StyledRun], budget: int) -> list[StyledRun]:
    """保留 runs 结尾最多 budget 宽度（结果保持原 run 顺序，不拆 CJK）。

    源 runs 已保证相邻同样式合并（Line.append），故保留尾部即为原序列的
    一个后缀——无需再次合并相邻同样式。
    """
    kept: list[StyledRun] = []  # 反向收集
    width = 0
    for run in reversed(runs):
        if width >= budget:
            break
        # ★ P-H7（性能）：逐字符前插 ``buf = ch + buf`` 为 O(n²)（超长行
        #   truncate-start/middle 处理 100k 字符日志时明显卡顿）——改为 list
        #   收集 + 末尾反转拼接 O(n)。
        chars: list[str] = []
        for ch in reversed(run.text):
            cw = wcswidth_simple(ch)
            if width + cw > budget:
                break
            chars.append(ch)  # 反向收集（后序字符在前）
            width += cw
        if chars:
            buf = "".join(reversed(chars))  # 反转恢复字符原序
            kept.append(StyledRun(buf, run.style))
    kept.reverse()
    return kept


def truncate_runs_start(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """truncate-start：省略号在开头，保留尾部内容（react-ink 语义）。

    内容不超过 max_width 时原样返回（不追加省略号）；超过时保留尾部
    ``max_width-1`` 宽度内容（不拆 CJK），开头追加 ``…``（宽度 1）。
    省略号采用尾部首个保留 run 的样式（与内容衔接一致）。

    含 ``\\n`` 文本先归一化为首个逻辑行（单行截断语义，防字面换行破坏行宽）。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 最大显示宽度；<=0 返回空列表。

    Returns:
        截断后的 StyledRun 列表（总宽度 <= max_width）。
    """
    if max_width <= 0:
        return []
    runs = _first_logical_line_runs(runs)
    if _runs_total_width(runs) <= max_width:
        return list(runs)
    tail = _keep_tail(runs, max_width - 1)
    ellipsis_style = tail[0].style if tail else (runs[-1].style if runs else None)
    return [StyledRun("…", ellipsis_style)] + tail


def truncate_runs_middle(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """truncate-middle：保留头尾，中间省略号（react-ink 语义）。

    内容不超过 max_width 时原样返回；超过时保留头部 ``(max_width-1)//2``
    宽度与尾部 ``max_width-1-(max_width-1)//2`` 宽度（不拆 CJK），中间
    追加 ``…``（宽度 1）。宽度 <=3 时头部预算不足（省略号+头尾各至少 1 格）
    → 回退 ``truncate-end`` 语义（末尾省略号）。

    含 ``\\n`` 文本先归一化为首个逻辑行（单行截断语义，防字面换行破坏行宽）。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 最大显示宽度；<=0 返回空列表。

    Returns:
        截断后的 StyledRun 列表（总宽度 <= max_width）。
    """
    # ★ P3-11（review 方向）：边界统一为 ``<= 0``——与 truncate_runs_start/
    #   truncate_runs_ellipsis 一致（修复前 `< 0`：max_width=0 落入主逻辑，
    #   经 `<= 3` 分支回退 ellipsis 后仍返回空，行为正确但语义不显式）。
    if max_width <= 0:
        return []
    runs = _first_logical_line_runs(runs)
    if _runs_total_width(runs) <= max_width:
        return list(runs)
    if max_width <= 3:
        return truncate_runs_ellipsis(runs, max_width)
    head_budget = (max_width - 1) // 2
    tail_budget = max_width - 1 - head_budget
    head = _keep_head(runs, head_budget)
    tail = _keep_tail(runs, tail_budget)
    ellipsis_style = head[-1].style if head else (tail[0].style if tail else None)
    return head + [StyledRun("…", ellipsis_style)] + tail


def truncate_line(line: Line, max_width: int) -> Line:
    """将行截断至 max_width 显示宽度（保持样式）。

    超宽部分丢弃；宽度不足时原样返回。截断点在字符边界，
    不拆分宽字符（CJK）。

    含 ``\\n`` 文本先归一化为首个逻辑行（单行截断语义，防字面换行破坏行宽）。
    """
    if max_width < 0:
        return Line()
    if line.width <= max_width:
        return line.clone()
    runs = _first_logical_line_runs(line.runs)
    out = Line()
    width = 0
    for run in runs:
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                return out
            out.append(ch, run.style)
            width += cw
    return out


def pad_line(line: Line, width: int) -> Line:
    """将行填充至指定宽度（不足补空格；已超宽则截断）。"""
    out = truncate_line(line, width)
    pad = width - out.width
    if pad > 0:
        out.append(" " * pad)
    return out


__all__ = [
    "wrap_runs_by_width",
    "_first_logical_line_runs",
    "truncate_runs",
    "truncate_runs_ellipsis",
    "truncate_runs_start",
    "truncate_runs_middle",
    "truncate_line",
    "pad_line",
    "_runs_total_width",
    "_keep_head",
    "_keep_tail",
]
