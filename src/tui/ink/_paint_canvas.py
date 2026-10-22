"""画布行操作 — Line ↔ 列键字典转换 / 合并 / 裁剪（CJK 安全）。

模块边界（2026-08-05 架构优化）：从 ``ink/components.py`` 拆分——画布行
转换/合并/切片为纯数据操作（dict 列键 → (char, style)），供边框绘制
（``_paint_border``）/主绘制（``_paint_impl``）/帧构建（``render_frame``）
共享。依赖 ``output.Line``/``core.style``/``_width``，不反向依赖
components 主模块。
"""

from __future__ import annotations

from src._text_width import grapheme_clusters
from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from .output import Line


def _is_single_width_run(text: str, width: int | None = None) -> bool:
    """文本是否为「每个字符显示宽度恰为 1」（可逐字符写列键）。

    ASCII 可打印走 C 级 ``isascii()`` + ``isprintable()`` 快路径；非 ASCII
    （如边框用的 ``─│┌`` 等 width-1 符号）先比 ``width == len(text)``——
    含宽字符（CJK/emoji，宽 2）或零宽字符（组合标记/ZWJ/VS，宽 0）时两者
    必然不等（唯一例外是宽字符与零宽容量的数量恰好抵消，此时逐字符复核），
    因此长 CJK/emoji 文本无需逐字符扫描即可判否。仅当 ``width == len`` 时
    逐字符查 ``wcswidth_simple``（单字符走 ``char_width`` 有界缓存，约
    50ns/字符）。

    ★ 2026-10-23 性能：修复前只有 ASCII 走批量快路径，边框字符（U+2500
    区段）每个字符都要进 ``grapheme_clusters`` 生成器 + ``_put_char``
    （80 列边框行实测 ~127µs/行）；本判定后单宽字符直接逐字符写键。

    Args:
        text: run 文本。
        width: 该 run 的已缓存显示宽度（``StyledRun.width``）；None 时现算。
    """
    if text.isascii():
        return text.isprintable()
    if not text or not text.isprintable():
        return False
    if width is None:
        width = wcswidth_simple(text)
    if width != len(text):
        # 宽字符/零宽字符（或二者混合且数量不等）→ 不能按每字符 1 列写入。
        return False
    for ch in text:
        if wcswidth_simple(ch) != 1:
            return False
    return True


def _is_single_width_line(line: Line) -> bool:
    """行内所有 run 均为「单宽字符」（见 ``_is_single_width_run``）。

    ``_merge_line`` 的 run 级拼接快路径要求目标行/新行不含宽字符与零宽
    字符——只有单宽字符时，按 run 拼接与按列键合并的逐字结果完全一致。
    """
    for run in line.runs:
        if not _is_single_width_run(run.text, run.width):
            return False
    return True


def _put_char(d: dict, col: int, ch: str, st, link: str | None = None) -> int:
    """将字符写入列键字典，返回下一个列键（零宽字符合并到前一键）。

    ★ P1-1 修复（review 方向）：零宽字符（组合标记 U+0300-036F、ZWJ
    U+200D、变体选择符 U+FE00 等，宽度 0）写入时若存在**最近的前一列键**
    则与该键合并（追加到前键文本，样式保留基字符）——修复前
    ``d[col]=(ch, st); col += wcswidth_simple(ch)`` 对零宽字符 col 不递增，
    下一个字符以相同键覆盖写入，组合标记静默丢失（如 ``"e\\u0301x"`` 渲染
    成 ``"ex"``）。合并目标为**最近的前键**而非 ``col-1``：宽字符（占 2 列）
    后跟零宽字符时前键是 ``col-2``（键 ``col-1`` 不存在，如 ``"中\\u0301文"``
    的 ``\\u0301`` 依附键 0 的 ``中``）。tab（宽度 0）同根因一并处理（合并到
    前键，不覆盖后续字符）。行首零宽字符（col==0 无前键可依附）按原语义写入
    当前键（无基字符可依附，视觉上不渲染——保留既有行为）。CJK 宽字符
    （宽度 2）走常规路径，行为不变。

    ★ 超链接（OSC 8）：条目为 3 元组 ``(ch, style, link)``——link 随字符
    传播到帧输出（``_canvas_row_to_line`` 还原为 ``Line.append(link=)``）。

    Args:
        d: 列键字典（``{col: (ch, style, link)}``）。
        col: 当前列键。
        ch: 待写入字符。
        st: 字符样式。
        link: 字符超链接 URL（None 表示无）。

    Returns:
        下一个列键（零宽字符合并后不递增）。
    """
    w = wcswidth_simple(ch)
    if w == 0:
        # 零宽字符：合并到最近的既有前键（画布行宽有界 ≤ 终端列数，向前
        # 扫描可接受；正常文本零宽字符紧跟基字符，扫描至多 1-2 次）。
        prev_col = col - 1
        while prev_col >= 0 and prev_col not in d:
            prev_col -= 1
        if prev_col >= 0:
            prev_ch, prev_st, prev_link = _entry3(d[prev_col])
            # 样式合并：保留基字符样式；基字符无样式时用零宽字符自身样式
            d[prev_col] = (
                prev_ch + ch,
                prev_st if prev_st is not None else st,
                prev_link if prev_link is not None else link,
            )
            return col
        # ★ P3（review）：行首零宽字符（无前键可依附）——修复前直接
        #   ``d[col] = (ch, st)`` 且 col 不递增，下一字符以同键覆盖 → 行首
        #   组合标记/ZWJ 丢失。现暂存于当前键，由下一非零宽字符合并（见下）。
        ex = d.get(col)
        if ex is not None and wcswidth_simple(ex[0]) == 0:
            d[col] = (ex[0] + ch, ex[1] if ex[1] is not None else st, link)
        else:
            d[col] = (ch, st, link)
        return col
    # ★ P3（review）：当前键已有「零宽累积」（行首零宽暂存）时与基字符合并
    #   ——零宽字符不占列，与基字符同键（宽度以基字符计）。
    ex = d.get(col)
    if ex is not None and ex[0] and wcswidth_simple(ex[0]) == 0:
        d[col] = (ex[0] + ch, st, link)
        return col + w
    d[col] = (ch, st, link)
    return col + w


def _entry3(entry) -> tuple:
    """把画布条目归一化为 ``(ch, style, link)`` 三元组。

    历史/第三方 host 可能写入 2 元组 ``(ch, style)``（``registry.paint_fn``
    契约文档为 ``{col: (char, style)}``）——统一在此补齐 ``link=None``，
    避免各处 unpack 分叉。
    """
    if len(entry) >= 3:
        return entry[0], entry[1], entry[2]
    return entry[0], entry[1], None


def _fill_single_width_run(
    d: dict, row, col: int, text: str, st, link: str | None,
) -> int:
    """把「单宽字符 run」写入列键字典（含行首零宽暂存合并），返回下一列键。

    ★ 修复（2026-10-23 画布内容丢失）：零宽字符（ZWSP / VS16 / 组合标记 /
    ZWJ）位于**独立 run** 且处于行首时，``_put_char`` 把它暂存在当前列键且
    **不推进列号**；下一个 run 走单宽快路径时直接 ``d[col] = ...`` 会覆盖该
    键 → 零宽字符静默丢失（如 ``Line(["\u200b"], ["bb"])`` 渲染成 ``bb``）。
    本函数在写首字符前把暂存零宽并入（样式/链接以基字符为准，与
    ``_put_char`` 同口径），修复后逐字往返与逐列合并结果一致。

    Args:
        d: 目标列键字典（切片或整行）。
        row: 目标画布行（dict/None；仅 ``_merge_line_dict`` 传入，用于识别
            既有行在起始列的零宽暂存；``_line_as_dict`` 传 None）。
        col: 当前列键。
        text: run 文本（调用方保证每个字符显示宽度恰为 1）。
        st: 字符样式。
        link: 字符超链接 URL。

    Returns:
        下一列键。
    """
    if not text:
        return col
    ent = d.get(col)
    if ent is None and row is not None:
        ent = row.get(col)
    if ent is not None and wcswidth_simple(ent[0]) == 0:
        d[col] = (ent[0] + text[0], st, link)
        text = text[1:]
        col += 1
    for ch in text:
        d[col] = (ch, st, link)
        col += 1
    return col


def _line_as_dict(line: Line) -> dict:
    """将 Line 转为列键字典（``{display_col: (ch, style)}``，CJK 安全）。

    列键为**显示宽度**（``wcswidth_simple``），与画布行键语义一致——
    CJK 宽字符占 2 列则键递增 2（修复前逐字符 ``col += 1`` 导致宽字符
    后续内容错位重叠）。零宽字符（宽度 0）经 ``_put_char`` 合并到前一键
    （P1-1：不覆盖后续字符）。

    ★ 性能（2026-08-05）：纯可打印 ASCII run 走批量快路径——宽度 == 字符数
    （``isascii()`` + ``isprintable()`` C 实现单趟扫描），免逐字符
    ``wcswidth_simple`` 调用（渲染热路径画布转换以 ASCII 文本为主）。
    ★ 性能（2026-10-23）：单宽非 ASCII（边框 ``─│┌`` 等）同样走批量快路径
    （``_is_single_width_run``）——修复前边框逐字符进 ``grapheme_clusters``
    + ``_put_char``，80 列边框行实测 ~127µs。
    """
    d: dict = {}
    col = 0
    for run in line.runs:
        t = run.text
        st = run.style
        lk = run.link
        if t.isascii() and t.isprintable():
            col = _fill_single_width_run(d, None, col, t, st, lk)
        elif _is_single_width_run(t, run.width):
            col = _fill_single_width_run(d, None, col, t, st, lk)
        else:
            for cluster in grapheme_clusters(t):
                # ★ P1-1 修复：零宽字符合并到前键（见 _put_char）；
                #   字形簇整体写入（skin tone/keycap/ZWJ 不拆成多格）。
                col = _put_char(d, col, cluster, st, lk)
    return d


def _ensure_row_dict(row) -> dict:
    """将画布行归一化为 dict（Line/None → dict，dict 原样返回）。

    画布行可能为三种形态：None（惰性空行）、Line（box.x==0 快路径写入的
    Line 对象）、dict（增量合并）。后续 dict 操作（``row[col]=...`` /
    ``row.update(...)``）前必须先归一化——修复前对 Line 直接做 dict 操作
    抛 AttributeError/TypeError，被 _paint 隔离吞掉导致内容丢失。
    """
    if isinstance(row, Line):
        return _line_as_dict(row)
    if row is None:
        return {}
    return row


def _overlaps_wide_second_col(row: dict, boundaries) -> bool:
    """检测待合并片段是否存在「新字符落在既有宽字符第二列」的冲突（E2）。

    条件：任一 ``c in boundaries`` 满足——``c > 0`` 且 ``(c-1) in row`` 且
    ``row[c-1]`` 为宽字符（显示宽度 2）。

    ``boundaries`` 为**切片中「左侧相邻键不在切片内」的键**（调用方在
    构造切片时收集）——只有这些键才可能与底行既有宽字符的第二列冲突；
    切片内部的键其左列已被本片段覆盖，无需检查。修复前遍历整个切片并
    对每个键做 ``wcswidth_simple``（120 列文本 ≈ 120 次），本函数按边界
    键检查后典型 ASCII 文本只有 1 个候选，开销 O(1)。

    Args:
        row: 目标画布行（dict 形态，col → (ch, style)）。
        boundaries: 切片中的左边界键列表（``c-1 not in slice_``）。

    Returns:
        True — 存在宽字符第二列冲突，须走逐键覆盖分支。
    """
    for c in boundaries:
        if c <= 0:
            continue
        left = row.get(c - 1)
        if left is not None and wcswidth_simple(left[0]) == 2:
            return True
    return False


def _overwrites_wide_second_col(row: dict, wide_starts, slice_: dict) -> bool:
    """检测 slice_ 的新宽字符是否与既有行的「第二列键」冲突（P2）。

    条件：任一 ``c in wide_starts`` 满足——``(c+1) in row`` 且
    ``(c+1) not in slice_``。``wide_starts`` 为切片中**新宽字符首列键**
    （调用方构造切片时收集）；修复前遍历所有键并对每个键测宽，现在只对
    宽字符（典型文本为 0 个）做 O(1) 检查。

    Args:
        row: 目标画布行（dict 形态）。
        wide_starts: 切片中新宽字符的首列键列表。
        slice_: 待合并片段（dict 形态）。

    Returns:
        True — 存在冲突，须走逐键覆盖分支。
    """
    for c in wide_starts:
        if (c + 1) in row and (c + 1) not in slice_:
            return True
    return False


def _merge_line(row, x: int, line: Line):
    """将 Line 合并到画布行（从第 x 列开始），返回合并后的行。

    ★ 2026-10-23 画布性能优化：按目标行形态分派——
      - **Line 且全段覆盖**（``x == 0`` 且新行宽度 >= 原行宽度）：新内容覆盖
        全部显示列，直接返回新行（零 dict 构建、零逐字符重绘）；
      - **Line 且无重叠**（``x >= 原行宽度``）：按 run 级拼接（原有 runs +
        必要时补空格 + 新行 runs）——免整行 ``_line_as_dict`` + 逐列合并 +
        ``_canvas_row_to_line`` 往返，是「一行多段文本」的主热路径；
      - **None**：x==0 直接返回新行；x>0 生成「前导空格 + 新行」的 Line；
      - 其余（Line 部分重叠 / dict / 带空行）：回退 ``_merge_line_dict``
        （原算法逐字保持不变，作为快路径的参考实现与兜底）。

    ★ 快路径仅用于**单宽字符行**（见 ``_is_single_width_line``：每个字符
    显示宽度恰为 1，如 ASCII 与边框 ``─│┌`` 等）——宽字符/零宽字符在 dict
    合并中有「第二列覆盖 / 合并到前键」等精确语义，run 级拼接无法逐字
    复现，强行启用会改变 CJK/emoji 的渲染结果。含宽/零宽字符的行一律回退
    dict 路径（行为与优化前完全一致）。

    Args:
        row: 目标画布行（None / Line / dict）。
        x: 新行起始显示列。
        line: 待合并的 Line。

    Returns:
        合并后的画布行（Line 或 dict）。
    """
    # 空行合并：目标行原样返回（None 归一化为空 dict，保持既有返回契约）。
    if not line.runs:
        return row if row is not None else {}
    if not _is_single_width_line(line):
        return _merge_line_dict(row, x, line)
    if row is None:
        if x == 0:
            return line
        out = Line()
        out.append(" " * x)
        for run in line.runs:
            out.append(run.text, run.style, run.link)
        return out
    if isinstance(row, Line) and _is_single_width_line(row):
        if x == 0:
            # 新内容覆盖 [0, line.width)——原行全部显示列都被覆盖。
            if line.width >= row.width:
                return line
        elif x >= row.width:
            # 新内容从原行末尾（或更右）开始：纯拼接，无任何重叠。
            out = row.clone()
            if x > row.width:
                out.append(" " * (x - row.width))
            for run in line.runs:
                out.append(run.text, run.style, run.link)
            return out
    return _merge_line_dict(row, x, line)


def _merge_line_dict(row, x: int, line: Line) -> dict:
    """``_merge_line`` 的 dict 路径（原实现，语义参考与兜底）。

    构造 ``{col: (ch, style)}`` 片段，与目标行键集无交时批量
    ``row.update(slice_)``；重叠时回退逐字符覆盖（语义一致）。目标行可能
    为 Line/None/dict 任意形态——先 ``_ensure_row_dict`` 归一化再合并。

    ★ E2（宽字符第二列覆盖）：快路径在普通键无交（disjoint）之外还须检查
    宽字符第二列冲突（``_overlaps_wide_second_col``）——新字符落在既有宽字符
    第二列时，批量 update 后 ``_canvas_row_to_line`` 的 ``col < prev`` 跳过该
    键（新字符静默丢失，如 row={0:('中'),2:('a')} + 覆盖键 1 → 渲染 "中a"、
    "X" 丢失）。冲突时走逐键覆盖：**新字符获胜、旧宽字符整体消失**（视觉
    语义：宽字符被覆盖为新字符，不再静默丢失；被替换字符为空格时同样整体
    替换——新写入内容优先）。

    ★ 2026-10-23 性能：宽字符冲突检查只扫描**切片边界键**（``left_boundaries``）
    与新宽字符首列键（``wide_starts``），不再对切片每个键调用两次
    ``wcswidth_simple``（120 列文本的 merge 从 ~250 次测宽降为典型 1~2 次）。

    Returns:
        合并后的 dict 行（调用方写回 canvas[row]）。
    """
    slice_: dict[int, tuple[str, Style | None, str | None]] = {}
    # 宽字符冲突候选（见 _overlaps_wide_second_col / _overwrites_wide_second_col）
    left_boundaries: list[int] = []
    wide_starts: list[int] = []
    col = x
    # ★ 2026-10-23：目标行**前移**归一化——单宽快路径需要读取既有行在起始列
    #   的「行首零宽暂存」键（见 ``_fill_single_width_run``）；修复前在切片
    #   构建完成后才归一化，无法访问。
    row = _ensure_row_dict(row)
    # ★ 性能（2026-08-05）：纯可打印 ASCII run 走批量快路径（宽度 == 字符数），
    #   免逐字符 ``wcswidth_simple`` 调用——画布合并热路径（TEXT 行合并、
    #   StaticLines 非 x==0 路径）以 ASCII 文本为主。
    for run in line.runs:
        t = run.text
        st = run.style
        lk = run.link
        if t.isascii() and t.isprintable():
            # 整个 ASCII run 键连续（每字符 +1 列）：只需判断 run 首字符是否
            # 有左邻键（run 内其余字符必然有）。
            if (col - 1) not in slice_:
                left_boundaries.append(col)
            col = _fill_single_width_run(slice_, row, col, t, st, lk)
        elif _is_single_width_run(t, run.width):
            # 单宽非 ASCII（边框字符等）：同样键连续、不产生宽字符冲突。
            if (col - 1) not in slice_:
                left_boundaries.append(col)
            col = _fill_single_width_run(slice_, row, col, t, st, lk)
        else:
            for cluster in grapheme_clusters(t):
                # ★ P1-1 修复：零宽字符合并到前键（见 _put_char）；
                #   字形簇整体写入（skin tone/keycap/ZWJ 不拆成多格）。
                prev_col = col
                if (col - 1) not in slice_:
                    left_boundaries.append(col)
                col = _put_char(slice_, col, cluster, st, lk)
                if col - prev_col > 1:
                    # 宽字符（占 2 列，键只写首列）；零宽字符不推进 col。
                    wide_starts.append(prev_col)
    # ★ P2（review）：空行（row={}）场景跳过宽字符扫描（常见合并热路径
    #   零额外开销）；非空行还需检测「新宽字符覆盖既有第二列键」
    #   （_overwrites_wide_second_col：修复前 disjoint 快路径批量 update 后
    #   行同含宽字符首列与旧第二列残字 → _canvas_row_to_line 宽度推导
    #   把宽字符按 1 列处理并拼接残字，后续内容错位）。
    if slice_.keys().isdisjoint(row) and (
        not row
        or (
            not _overlaps_wide_second_col(row, left_boundaries)
            and not _overwrites_wide_second_col(row, wide_starts, slice_)
        )
    ):
        row.update(slice_)
    else:
        # ★ 2026-10-23 性能：部分重叠路径改为「一次批量 update + 只对候选键
        #   修正」——修复前对 slice_ 每个键做字典判断 + 两次测宽（120 列文本
        #   ≈ 120 次循环 + 250 次 wcswidth）。候选键只需两类：
        #     - E2（新字符落在旧宽字符第二列）：``left_boundaries`` 中 ``c-1``
        #       在旧行且为宽字符的键；
        #     - BUG-61（覆盖旧宽字符首列/新宽字符覆盖旧第二列）：仅
        #       ``c+1 not in slice_`` 的键（连续切片通常只有最后一个键）。
        #   其余键的写入语义与批量 update 完全一致。
        left_conflicts: dict[int, int] = {}
        for c in left_boundaries:
            if c > 0 and (c - 1) in row and (c - 1) not in slice_:
                if wcswidth_simple(row[c - 1][0]) == 2:
                    left_conflicts[c] = c - 1
        cleanup_wide = set()
        # ★ 2026-10-23 性能：只遍历**目标行键**（典型边框行 2 个）——修复前
        #   遍历整个切片（120 列文本 ≈ 120 次字典查找 + 测宽），语义等价
        #   （c 必须同时在 row 与 slice_ 中，且 c+1 不在切片内）。
        for c in row:
            if c in slice_ and (c + 1) not in slice_:
                if wcswidth_simple(row[c][0]) == 2:
                    cleanup_wide.add(c)
        new_wide = set(wide_starts)
        row.update(slice_)
        for c, v in slice_.items():
            # ★ E2（宽字符第二列覆盖）：新字符落在既有宽字符第二列——替换
            #   宽字符整体（新字符不再静默丢失）。语义：宽字符被新字符覆盖
            #   （如 ``中`` 第二列被 ``X`` 覆盖 → 渲染 ``X``，不残留 ``中``）。
            conflict = left_conflicts.get(c)
            if conflict is not None:
                row[conflict] = v
                row.pop(c, None)
                continue
            # ★ BUG-61（review 方向）：宽字符残留清理——被覆盖位置为宽字符
            #   首列（旧占 c+1 列，仅首列键）时同步清除 c+1 键（残留第二列
            #   字形）；新写入字符为宽字符（占 c+1 列）时清除 c+1 旧内容
            #   （slice_ 未覆盖该键——宽字符只写首列键）。修复前覆盖宽字符
            #   首列后行含孤立第二列字形（渲染出 ``a``+残留字形）。
            if c in cleanup_wide:
                row.pop(c + 1, None)
            if c in new_wide and (c + 1) not in slice_:
                row.pop(c + 1, None)
    return row


def _slice_run_text_with_offset(text: str, start_w: int, end_w: int) -> tuple[str, int]:
    """同 ``_slice_run_text``，另返回首个保留字符相对 ``start_w`` 的起始偏移。

    宽字符横跨左边界时整体保留（``_slice_run_text`` 语义）→ 该字符实际起始列
    ``< start_w``（偏移为负，最多 -1）；正常情况偏移 0。供 ``_slice_line_with_offset``
    把绘制列回退到真实位置。

    Returns:
        ``(text, offset)``：切片文本 + 首个保留字符实际起始列与 ``start_w`` 之差
        （``<= 0``；无保留字符时为 0）。
    """
    if start_w <= 0 and end_w >= 10**9:
        return text, 0
    chars: list[str] = []
    col = 0
    first_start = 0
    got = False
    for cluster in grapheme_clusters(text):
        w = wcswidth_simple(cluster)
        if col < end_w and col + w > start_w:
            if not got:
                got = True
                first_start = col
            chars.append(cluster)
        elif w == 0 and chars and col <= end_w:
            # 零宽字形簇（组合标记）依附已保留的基字符（不因恰好落在
            # 右边界而丢失重音/变体选择符）。
            chars.append(cluster)
        col += w
        if col >= end_w and chars and col - w >= end_w:
            break
    if not got:
        return "", 0
    return "".join(chars), first_start - start_w


def _slice_run_text(text: str, start_w: int, end_w: int) -> str:
    """按显示宽度切片文本（``[start_w, end_w)``，CJK 安全）。

    逐字符累积显示宽度；字符区间与目标区间有交时保留。宽字符横跨区间
    边界时整体保留（避免半个字符，可能略超边界——视觉正确优先）。

    Args:
        text: 原文本。
        start_w: 起始显示列（含）。
        end_w: 结束显示列（不含）。

    Returns:
        切片后的文本。
    """
    return _slice_run_text_with_offset(text, start_w, end_w)[0]


def _slice_run_text_strict(text: str, start_w: int, end_w: int) -> tuple[str, int]:
    """按显示宽度**严格**切片文本，返回 ``(text, offset)``（CJK 安全）。

    与 ``_slice_run_text`` 的区别：只保留**完整落在** ``[start_w, end_w)``
    内的字形簇——宽字符横跨左右边界时**整字丢弃**。用于把一行切成
    「左段 + 窗口中段 + 右段」再无缝拼接（保留半个宽字符会占用相邻片段的
    首列，导致其后内容整体错位）；被丢弃的列由调用方补空格留白。

    零宽字形簇（组合标记 / 变体选择符）不占列：落在区间内（含左右边界，
    边界处即紧邻区间内的字符）即保留，由调用方决定其依附关系。

    Returns:
        ``(text, offset)``：切片文本 + 首个保留字形簇的实际起始列相对
        ``start_w`` 的偏移（``>= 0``；无保留字符时为 0）。
    """
    if not text or end_w <= start_w:
        return "", 0
    # ★ 性能：纯可打印 ASCII run 宽度 == 字符数（C 实现单趟扫描），直接切片
    if text.isascii() and text.isprintable():
        s = start_w if start_w > 0 else 0
        if s >= len(text):
            return "", 0
        e = min(end_w, len(text))
        if e <= s:
            return "", 0
        return text[s:e], 0
    chars: list[str] = []
    col = 0
    first = 0
    for cluster in grapheme_clusters(text):
        w = wcswidth_simple(cluster)
        if w == 0:
            # 零宽簇不占列：落在区间内即保留（含 run 首的组合标记——它依附
            # **前一个 run** 的基字符，与 ``_line_as_dict`` 的合并口径一致，
            # 丢弃会让重音/变体选择符整段消失）；右边界处同样保留（紧邻
            # 最后一个保留字符）。
            if start_w <= col <= end_w:
                chars.append(cluster)
            continue
        if col >= end_w:
            break
        if col >= start_w and col + w <= end_w:
            if not chars:
                first = col - start_w
            chars.append(cluster)
        col += w
    return "".join(chars), first


def _slice_line_with_offset(
    line: Line, start_col: int, end_col: int,
) -> tuple[Line, int]:
    """同 ``_slice_line``，另返回**绘制起点偏移**（首个保留内容相对 ``start_col``）。

    ★ P2（review 修复）：宽字符横跨左裁剪边界时 ``_slice_run_text`` 整体保留该
    字符，但调用方原先仍按 ``start_col`` 绘制 → 该字符及其后内容整体**右移 1 列**
    （尾部越界覆盖相邻单元格）。本函数返回偏移（``0`` 或 ``-1``），调用方以
    ``start_col + offset`` 为绘制列，宽字符落在真实列（整体保留、略出裁剪区，
    与「视觉正确优先」语义一致）。

    Returns:
        ``(line, offset)``：裁剪后的行 + 绘制起点偏移（``<= 0``）。
    """
    out = Line()
    col = 0
    offset = 0
    recorded = False
    for run in line.runs:
        run_end = col + getattr(run, "width", 0)
        s = max(col, start_col)
        e = min(run_end, end_col)
        if s < e:
            text, rel = _slice_run_text_with_offset(run.text, s - col, e - col)
            if text:
                if not recorded:
                    recorded = True
                    offset = rel
                out.append(text, run.style, run.link)
        col = run_end
        if col >= end_col:
            break
    return out, offset


def _slice_line(line: Line, start_col: int, end_col: int) -> Line:
    """返回保留 ``[start_col, end_col)`` 显示列的新 Line（列裁剪）。

    逐 run 求与目标区间的交集；交集为空/越界 run 跳过；宽字符横跨区间
    边界时整体保留（``_slice_run_text`` 语义）。用于 overflow 水平裁剪。

    Args:
        line: 原 Line。
        start_col: 起始显示列（含）。
        end_col: 结束显示列（不含）。

    Returns:
        裁剪后的新 Line（可能为空）。
    """
    return _slice_line_with_offset(line, start_col, end_col)[0]


def _span_at(line: Line, col: int) -> tuple[int, int] | None:
    """返回显示列 ``col`` 所在字符的 ``(起始列, 显示宽度)``；无字符返回 None。

    用于判断「边界列是否落在跨列宽字符内部」——浮窗合成按矩形列区间切分
    底层行时，宽字符不能切开（终端无法只覆盖宽字符的某一列）：调用方据
    ``span[0] < col < span[0] + span[1]`` 判定边界落在宽字符内部并整字处理。

    负数列 / 行尾之后返回 None（行内零宽字形簇不占列，不参与命中）。
    """
    if col < 0:
        return None
    pos = 0
    for run in line.runs:
        end = pos + run.width
        if col >= end:
            pos = end
            continue
        text = run.text
        if text.isascii() and text.isprintable():
            return col, 1
        offset = col - pos
        c = 0
        for cluster in grapheme_clusters(text):
            w = wcswidth_simple(cluster)
            if c <= offset < c + w:
                return pos + c, w
            c += w
        return None
    return None


def _canvas_row_to_line(row) -> Line:
    """画布行转 Line。

    支持三种行：dict（列→(char,style)，增量合并）、已缓存的 Line
    （committed-chat 直接引用，免逐字符重绘 → 增量渲染核心）、None
    （画布惰性行——行级缓存优化，未绘制的空行）。

    列键为显示宽度（含 CJK 宽字符），转换为 Line 时**先补空格再写字符**
    ——修复前 ``sorted(row)`` 直接逐键拼接，跳过空列（如 justifyContent
    center/flex-end、alignItems 偏移、padding 留白、行内缩进），行首/
    行中间的空格全部丢失 → 水平定位失效。宽字符占位按**显示宽度**推进
    （``prev = col + wcswidth_simple(ch)``）——修复前 ``prev = col + 1``
    对 CJK 字符（占 2 列）推进不足，后续键 > prev 产生多余空格
    （``中文`` 被渲染为 ``中 文``）。

    ★ 2026-10-23 性能（画布热路径）：
      - 条目归一化内联（免每字符 ``_entry3`` 函数调用 + 重复取同一个键）：
        修复前每个字符被取两次（首键 + 内层循环）；
      - 同 style/link 连续段以 list 累积 + 一次 ``"".join`` 追加（免
        ``buf += ch`` 的 O(n²) 重复字符串分配）；
      - style/link 先做身份比较（``is``）再值比较——样式对象在画布内大量
        复用，身份命中可省 dataclass ``__eq__``。
    段级宽度推导（相邻键差 1 且字符 ASCII）与零宽/宽字符语义保持不变。
    """
    if isinstance(row, Line):
        return row
    if row is None:
        return Line()
    line = Line()
    # ★ 2026-10-23 性能：热路径局部化（每行多次测宽，属性查找最小化）。
    measure = wcswidth_simple
    prev = 0
    keys = sorted(row)
    n = len(keys)
    i = 0
    while i < n:
        col = keys[i]
        # ★ 方向8（宽字符重叠键死循环修复）：键列已被前序宽字符（CJK/emoji，
        #   宽 2 覆盖相邻列）占用时（``col < prev``）跳过该键——修复前
        #   ``col > prev`` 为 False 且内层 ``c2 != prev`` 立即 break → ``i = j``
        #   不变 → **无限循环**（画布行含宽字符 + 重叠键时整帧渲染挂起）。
        if col < prev:
            i += 1
            continue
        if col > prev:
            line.append(" " * (col - prev))
            prev = col  # 空格段宽 = 空格数
        entry = row[col]
        if len(entry) >= 3:
            ch, style, link = entry[0], entry[1], entry[2]
        else:
            ch, style = entry[0], entry[1]
            link = None
        parts = [ch]
        j = i + 1
        # ★ 段级宽度推导（方向4 性能）：连续段内非末尾字符用**相邻键差**推导
        #   宽度——画布写入不变量 ``col += wcswidth_simple(ch)`` 保证连续键差 =
        #   前序字符宽度；差 1 必然 ASCII 宽 1（宽字符差 2、gap 差 >2 均不可能
        #   是 1）→ 免 ``wcswidth_simple`` 调用。差 >=2（宽字符/gap）或段末尾
        #   （无下一键可推导）回退 ``wcswidth_simple``。
        if j < n and keys[j] == col + 1 and ch.isascii():
            cw = 1
        else:
            cw = measure(ch)
        prev = col + cw
        while j < n:
            c2 = keys[j]
            if c2 < prev:
                # 键已被前序宽字符覆盖（宽字符的第二列）→ 跳过该键
                j += 1
                continue
            if c2 != prev:
                break
            entry2 = row[c2]
            if len(entry2) >= 3:
                ch2, st2, lk2 = entry2[0], entry2[1], entry2[2]
            else:
                ch2, st2 = entry2[0], entry2[1]
                lk2 = None
            # 身份短路后再值比较（样式/link 对象在画布内大量复用）
            if (st2 is not style and st2 != style) or (
                lk2 is not link and lk2 != link
            ):
                break
            parts.append(ch2)
            # 相邻键差 1 仅在字符确为单列宽时可直接推导——非 ASCII 字符
            # （宽字符/CJK/emoji）即使被异常 host 以「每字符 +1 列」写入，
            # 也不能按 1 列推导（否则后续内容错位）。正常画布写入
            # （``_put_char``/``_line_as_dict``）的键差与宽度一致。
            if j + 1 < n and keys[j + 1] == c2 + 1 and ch2.isascii():
                cw = 1  # 连续 ASCII 快路径（相邻键差 1）
            else:
                cw = measure(ch2)
            prev = c2 + cw
            j += 1
        line.append("".join(parts), style, link)
        i = j
    return line


__all__ = [
    "_line_as_dict",
    "_ensure_row_dict",
    "_entry3",
    "_fill_single_width_run",
    "_overlaps_wide_second_col",
    "_overwrites_wide_second_col",
    "_merge_line",
    "_merge_line_dict",
    "_slice_run_text",
    "_slice_run_text_strict",
    "_slice_run_text_with_offset",
    "_slice_line",
    "_slice_line_with_offset",
    "_span_at",
    "_canvas_row_to_line",
]
