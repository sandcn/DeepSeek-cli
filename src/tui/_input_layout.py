"""输入区布局计算 — 光标视觉位置 / 制表符展开 / 按宽换行（纯函数）。

模块边界（2026-08-05 架构优化）：从 ``_input.py`` 拆分——输入区换行与光标
视觉位置计算为纯函数（零 I/O 依赖），供 ``Input``（组合）、``input_area``
（输入区渲染）、``ink._cursor``（光标定位）、``_input_metrics``（补全弹窗
说明换行）共享。依赖仅 ``_width``（字符宽度，Layer 0）。

唯一真源说明（方向5）：``_compute_input_layout`` / ``_cursor_visual_from_layout``
原自 ``app/input_area.py`` 迁移至 ``_input.py``，再随本拆分独立成模块——
input_area / session / _cursor 均从本模块复用同一实现，不再双实现。

★ 循环依赖消除（2026-08-05 重构）：``_wrap_by_width`` 原定义于 ``_input.py``
（测试 ``patch("src.tui._input._wrap_by_width")`` 契约锁定），``_compute_input_layout``
经**函数内延迟 import** 访问——形成 ``_input → _input_layout → _input`` 隐性环。
本模块将 ``_wrap_by_width`` 归位（纯布局函数，仅依赖 ``_width``），
``_input.py`` re-export 保持旧导入路径兼容（``from src.tui._input import
_wrap_by_width`` 仍可用）；``_input_metrics`` / ``ink._cursor`` 改从本模块
导入。循环消除后依赖方向统一为 ``_input → _input_layout → _width``。
"""

from __future__ import annotations

from src.tui._width import wcswidth_simple

_TAB_WIDTH = 4  # 制表符宽度（列数）—— 唯一真源


def _wrap_by_width(s: str, max_width: int) -> list[str]:
    """按终端列宽拆分文本为多行，每行不超过 max_width 列。

    优先按 \\n 拆分（强制换行），再对每段按列宽拆行。
    调用方应先通过 _expand_tabs 展开制表符。

    ★ 单一真源（2026-08-05 重构）：定义归位于本模块（纯布局函数层）——
    ``_input.py`` re-export 保持旧导入路径兼容；``_input_metrics`` /
    ``app/input_area`` 从本模块复用同一实现（防双实现漂移）。测试
    ``patch("src.tui._input_layout._wrap_by_width")`` 拦截计数（原
    ``patch("src.tui._input._wrap_by_width")`` 路径已随归位迁移）。
    """
    if max_width <= 0:
        # P3-2：max_width<=0 无有效列宽——显式返回 []（不拆行、不产生超宽
        # 单行）；调用方 _compute_input_layout 以 ``or [""]`` 兜底为空段，
        # 避免无限循环（原返回 [s] 对超长行不拆行产生超宽单行）。
        return []
    if not s:
        return [""]
    # ★ P3（review 2026-08-22）：调用方（_input_metrics._completion_height /
    #   _popup_builder / user_select / trace_view）直接以原始描述文本调用，未先
    #   _expand_tabs；\t 经 wcswidth_simple 计宽 0（控制字符分支）——含制表符
    #   的描述按下标不连续断行/宽度虚低。此处内部统一展开制表符（对已展开
    #   调用方幂等），消除前置条件依赖。
    s = _expand_tabs(s)
    lines: list[str] = []
    for segment in s.split('\n'):
        remaining = segment
        # P2-10（review）：本逻辑段是否存在可显示内容（至少一个字符宽 <=
        # max_width）——决定跳过超宽字符时是否保留零宽占位（仅当段内确有
        # 可显示内容时占位才有意义；全部字符均超宽的段保持 L1 空段语义）。
        has_fittable = any(
            wcswidth_simple(ch) <= max_width for ch in segment
        )
        while remaining:
            w = 0
            idx = 0
            for i, ch in enumerate(remaining):
                cw = wcswidth_simple(ch)
                if w + cw > max_width:
                    break
                w += cw
                idx = i + 1
            if idx == 0:
                # ★ L1（2026-08-15）：首字符超宽分支——max_width=1 且首字符
                #   CJK（宽 2）时原 ``idx=1`` 强制拆出宽 2 行 > max_width，
                #   破坏行宽不变量。宁可窄不可宽：最小 1 列预算仍放不下该
                #   字符（``wcswidth_simple(remaining[0]) > max_width``）时
                #   跳过该字符（每轮至少推进 1 字符，无死循环；不产生超宽
                #   行）；否则保持 ``idx=1``。调用方 ``_compute_input_layout``
                #   以 ``or [""]`` 兜底空段。
                # P2-10（review）：跳过超宽字符时保留零宽占位（U+200B，宽 0）
                #   ——修复前直接 ``remaining = remaining[1:]`` 丢弃字符，混合
                #   内容（如 "a가b" / "a가", max_width=1）中超宽字符从输出消失，
                #   换行结果拼接不回原文本 → ``_cursor_visual_from_layout`` 按
                #   字符数映射光标位置错乱（丢字）。零宽占位宽度 0（不破坏
                #   行宽不变量）、字符数 1（光标映射不丢位）。**仅当本逻辑段
                #   存在可显示内容**时保留占位——全部字符均超宽（极端窄终端 +
                #   全宽文本，如 "가나", max_width=1）时保持 L1 空段语义（调用
                #   方 ``or [""]`` 兜底），不破坏既有回归断言。
                if wcswidth_simple(remaining[0]) > max_width:
                    if has_fittable:
                        lines.append("\u200b")
                    remaining = remaining[1:]
                    continue
                idx = 1
            lines.append(remaining[:idx])
            remaining = remaining[idx:]
        if not segment:
            lines.append("")
    return lines if lines else [""]


def _expand_tabs(text: str, start_col: int = 0, tab_width: int | None = None) -> str:
    """将制表符按制表位展开为空格。

    每个 \\t 跳到下一个制表位列（tab_width 的整数倍），
    用空格填充至该列。

    Args:
        text: 含制表符的文本。
        start_col: 起始列（0-based）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后的纯空格文本。
    """
    if tab_width is None:
        tab_width = _TAB_WIDTH
    if '\t' not in text:
        return text
    result = []
    col = start_col
    for ch in text:
        if ch == '\n':
            result.append(ch)
            col = 0
        elif ch == '\t':
            spaces = tab_width - (col % tab_width)
            result.append(' ' * spaces)
            col += spaces
        else:
            cw = wcswidth_simple(ch)
            result.append(ch)
            col += cw
    return ''.join(result)


def _tab_pos_to_expanded(text: str, pos: int,
                         tab_width: int | None = None) -> int:
    """将含制表符文本中的字符位置映射到展开后的位置。

    Args:
        text: 含制表符的原始文本。
        pos: 原始文本中的字符索引（<0 返回 -1）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后文本中对应的字符索引。
    """
    if pos < 0:
        return -1
    if tab_width is None:
        tab_width = _TAB_WIDTH
    expanded_pos = 0
    col = 0
    for i, ch in enumerate(text):
        if i >= pos:
            break
        if ch == '\t':
            spaces = tab_width - (col % tab_width)
            expanded_pos += spaces
            col += spaces
        elif ch == '\n':
            expanded_pos += 1
            col = 0
        else:
            cw = wcswidth_simple(ch)
            expanded_pos += 1
            col += cw
    return expanded_pos


def _compute_input_layout(text: str, max_input: int) -> tuple[int, list[list[str]]]:
    """单次换行计算：返回 (总行数, 每逻辑行拆行后的段列表)。

    方向5（光标算法单一真源）：本函数自 ``app/input_area.py`` 迁移——与
    ``_cursor_visual_from_layout``/``_compute_cursor_visual_pos`` 一起构成
    单一实现（input_area / session / _cursor 复用，每帧至多 1 次换行计算）。

    ``wrapped_by_logical[i]`` 为第 i 个逻辑行（按 ``\\n`` 拆分）拆行后的段
    列表；空逻辑行对应 ``[""]``。

    ★ 循环依赖消除（2026-08-05 重构）：``_wrap_by_width`` 定义已归位本模块
    （原定义保留在 ``_input.py`` 时本函数经延迟 import 访问造成隐性环）——
    现直接调用本模块函数（同命名空间，测试 ``patch("src.tui._input_layout.
    _wrap_by_width")`` 可拦截）。每帧至多调用 1 次（InputArea 快照缓存），
    开销可忽略。
    """
    if not text:
        return 1, [[""]]
    expanded = _expand_tabs(text)
    wrapped_by_logical: list[list[str]] = []
    total_rows = 0
    for segment in expanded.split('\n'):
        seg_wrapped = _wrap_by_width(segment, max_input) or [""]
        wrapped_by_logical.append(seg_wrapped)
        total_rows += len(seg_wrapped)
    return max(1, total_rows), wrapped_by_logical


def _cursor_visual_from_layout(
    text: str,
    cursor_pos: int,
    wrapped_by_logical: list[list[str]],
) -> tuple[int, int]:
    """基于已缓存的换行布局计算光标视觉位置（复用缓存，避免重复换行计算）。

    方向5（光标算法单一真源）：与 ``_compute_cursor_visual_pos`` 语义一致：
    返回 (visual_line_idx, visual_col)。仅对光标所在逻辑行做 O(行) 定位，
    不重新整段换行。
    """
    if not text:
        return (0, 0)
    abs_cursor = len(text) if cursor_pos < 0 else cursor_pos

    lines = text.split('\n')
    cum = 0
    for logical_idx, logical_line in enumerate(lines):
        line_len = len(logical_line)
        if abs_cursor <= cum + line_len:
            # 光标在此逻辑行中（或在行末的 \n 上）
            pos_in_line = abs_cursor - cum
            segs = (
                wrapped_by_logical[logical_idx]
                if logical_idx < len(wrapped_by_logical)
                else [""]
            )
            expanded_in_line = _tab_pos_to_expanded(logical_line, pos_in_line)
            if expanded_in_line < 0:
                last_seg = segs[-1] if segs else ""
                col_in_line = wcswidth_simple(last_seg)
                visual_line_in_logical = len(segs) - 1 if segs else 0
            else:
                cum2 = 0
                visual_line_in_logical = 0
                for i, seg in enumerate(segs):
                    if expanded_in_line <= cum2 + len(seg):
                        visual_line_in_logical = i
                        prefix = seg[:expanded_in_line - cum2]
                        col_in_line = wcswidth_simple(prefix)
                        break
                    cum2 += len(seg)
                else:
                    visual_line_in_logical = len(segs) - 1 if segs else 0
                    col_in_line = wcswidth_simple(segs[-1]) if segs else 0
            total_before = sum(len(s) for s in wrapped_by_logical[:logical_idx])
            return (total_before + visual_line_in_logical, col_in_line)
        cum += line_len + 1

    # 超出范围 → 末尾（防御分支，正常路径不可达——光标位置落在文本内时
    # 上方循环已 return；仅当 abs_cursor 超出全部逻辑行（如 cursor_pos 超
    # 长）时到达。保留防御成本低、正确性安全）
    last_segs = wrapped_by_logical[-1] if wrapped_by_logical else [""]
    last_seg = last_segs[-1] if last_segs else ""
    col = wcswidth_simple(last_seg)
    total_before = sum(len(s) for s in wrapped_by_logical[:-1])
    visual_row = total_before + (len(last_segs) - 1 if last_segs else 0)
    return (visual_row, col)


def _compute_cursor_visual_pos(
    text: str, cursor_pos: int, max_width: int,
) -> tuple[int, int]:
    """计算光标在带 \\n 的文本中的视觉位置（行号, 列号）。

    方向5（光标算法单一真源）：内部复用 ``_compute_input_layout`` +
    ``_cursor_visual_from_layout``（与 input_area 缓存布局语义一致，行为
    不变——input_area 的 ``_compute_input_layout``/``_cursor_visual_from_layout``
    已迁移到本模块，不再双实现）。

    Args:
        text: 原始输入文本（含 \\n）。
        cursor_pos: 光标在原始文本中的字符偏移（-1=末尾）。
        max_width: 每行最大列宽。

    Returns:
        (visual_line_idx, visual_col) —— 均为 0-based。
    """
    _, wrapped_by_logical = _compute_input_layout(text, max_width)
    return _cursor_visual_from_layout(text, cursor_pos, wrapped_by_logical)


__all__ = [
    "_TAB_WIDTH",
    "_wrap_by_width",
    "_expand_tabs",
    "_tab_pos_to_expanded",
    "_compute_input_layout",
    "_cursor_visual_from_layout",
    "_compute_cursor_visual_pos",
]
