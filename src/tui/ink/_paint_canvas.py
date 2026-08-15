"""画布行操作 — Line ↔ 列键字典转换 / 合并 / 裁剪（CJK 安全）。

模块边界（2026-08-05 架构优化）：从 ``ink/components.py`` 拆分——画布行
转换/合并/切片为纯数据操作（dict 列键 → (char, style)），供边框绘制
（``_paint_border``）/主绘制（``_paint_impl``）/帧构建（``render_frame``）
共享。依赖 ``output.Line``/``core.style``/``_width``，不反向依赖
components 主模块。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from .output import Line


def _put_char(d: dict, col: int, ch: str, st) -> int:
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

    Args:
        d: 列键字典（``{col: (ch, style)}``）。
        col: 当前列键。
        ch: 待写入字符。
        st: 字符样式。

    Returns:
        下一个列键（零宽字符合并后不递增）。
    """
    w = wcswidth_simple(ch)
    if w == 0 and col > 0:
        # 零宽字符合并到最近的既有前键（画布行宽有界 ≤ 终端列数，向前
        # 扫描可接受；正常文本零宽字符紧跟基字符，扫描至多 1-2 次）。
        prev_col = col - 1
        while prev_col >= 0 and prev_col not in d:
            prev_col -= 1
        if prev_col >= 0:
            prev_ch, prev_st = d[prev_col]
            # 样式合并：保留基字符样式；基字符无样式时用零宽字符自身样式
            d[prev_col] = (prev_ch + ch, prev_st if prev_st is not None else st)
            return col
    d[col] = (ch, st)
    return col + w


def _line_as_dict(line: Line) -> dict:
    """将 Line 转为列键字典（``{display_col: (ch, style)}``，CJK 安全）。

    列键为**显示宽度**（``wcswidth_simple``），与画布行键语义一致——
    CJK 宽字符占 2 列则键递增 2（修复前逐字符 ``col += 1`` 导致宽字符
    后续内容错位重叠）。零宽字符（宽度 0）经 ``_put_char`` 合并到前一键
    （P1-1：不覆盖后续字符）。

    ★ 性能（2026-08-05）：纯可打印 ASCII run 走批量快路径——宽度 == 字符数
    （``isascii()`` + ``isprintable()`` C 实现单趟扫描），免逐字符
    ``wcswidth_simple`` 调用（渲染热路径画布转换以 ASCII 文本为主）。
    """
    d: dict = {}
    col = 0
    for run in line.runs:
        t = run.text
        if t.isascii() and t.isprintable():
            st = run.style
            for ch in t:
                d[col] = (ch, st)
                col += 1
        else:
            st = run.style
            for ch in t:
                # ★ P1-1 修复：零宽字符合并到前键（见 _put_char）
                col = _put_char(d, col, ch, st)
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


def _overlaps_wide_second_col(row: dict, slice_: dict) -> bool:
    """检测 slice_ 是否存在「新字符落在既有宽字符第二列」的冲突（E2）。

    条件：任一 ``c in slice_`` 满足——``c > 0`` 且 ``(c-1) in row`` 且
    ``row[c-1]`` 为宽字符（显示宽度 2）且 ``(c-1) not in slice_``（slice_ 自身
    同时含首列+第二列时视为正常覆盖，不冲突——宽字符整体替换走逐键覆盖）。

    供 ``_merge_line`` 快路径判定：disjoint 命中（无普通键冲突）时仍可能
    存在「新字符落在宽字符第二列」——批量 update 会让新字符被
    ``_canvas_row_to_line`` 的 ``col < prev`` 跳过（静默丢失，E2）。

    Args:
        row: 目标画布行（dict 形态，col → (ch, style)）。
        slice_: 待合并片段（col → (ch, style)）。

    Returns:
        True — 存在宽字符第二列冲突，须走逐键覆盖分支。
    """
    for c in slice_:
        if c <= 0:
            continue
        left = row.get(c - 1)
        if left is not None and wcswidth_simple(left[0]) == 2 and (c - 1) not in slice_:
            return True
    return False


def _merge_line(row, x: int, line: Line) -> dict:
    """将 Line 合并到画布行（从第 x 列开始），返回合并后的行。

    性能快路径：构造 ``{col: (ch, style)}`` 片段，与目标行键集无交时批量
    ``row.update(slice_)``；重叠时回退逐字符覆盖（语义一致）。目标行可能
    为 Line/None/dict 任意形态——先 ``_ensure_row_dict`` 归一化再合并。

    ★ E2（宽字符第二列覆盖）：快路径在普通键无交（disjoint）之外还须检查
    宽字符第二列冲突（``_overlaps_wide_second_col``）——新字符落在既有宽字符
    第二列时，批量 update 后 ``_canvas_row_to_line`` 的 ``col < prev`` 跳过该
    键（新字符静默丢失，如 row={0:('中'),2:('a')} + 覆盖键 1 → 渲染 "中a"、
    "X" 丢失）。冲突时走逐键覆盖：**新字符获胜、旧宽字符整体消失**（视觉
    语义：宽字符被覆盖为新字符，不再静默丢失；被替换字符为空格时同样整体
    替换——新写入内容优先）。

    返回合并后的 dict 行（调用方写回 canvas[row]）——修复前返回 None，
    Line→dict 转换结果无法写回画布，目标行保持 Line 引用导致后续兄弟节点
    继续对 Line 做 dict 操作失败（row-of-texts 仅首项绘制）。
    """
    if not line.runs:
        return _ensure_row_dict(row)
    slice_: dict[int, tuple[str, Style | None]] = {}
    col = x
    # ★ 性能（2026-08-05）：纯可打印 ASCII run 走批量快路径（宽度 == 字符数），
    #   免逐字符 ``wcswidth_simple`` 调用——画布合并热路径（TEXT 行合并、
    #   StaticLines 非 x==0 路径）以 ASCII 文本为主。
    for run in line.runs:
        t = run.text
        st = run.style
        if t.isascii() and t.isprintable():
            for ch in t:
                slice_[col] = (ch, st)
                col += 1
        else:
            for ch in t:
                # ★ P1-1 修复：零宽字符合并到前键（见 _put_char）
                col = _put_char(slice_, col, ch, st)
    row = _ensure_row_dict(row)
    # ★ P2（review）：空行（row={}）场景跳过宽字符第二列扫描（常见合并热路径
    #   零额外开销）——``not row`` 短路后不调用 ``_overlaps_wide_second_col``。
    if slice_.keys().isdisjoint(row) and (not row or not _overlaps_wide_second_col(row, slice_)):
        row.update(slice_)
    else:
        for c, v in slice_.items():
            # ★ E2（宽字符第二列覆盖）：新字符落在既有宽字符第二列——替换
            #   宽字符整体（新字符不再静默丢失）。语义：宽字符被新字符覆盖
            #   （如 ``中`` 第二列被 ``X`` 覆盖 → 渲染 ``X``，不残留 ``中``）。
            if (
                c > 0
                and (c - 1) in row
                and (c - 1) not in slice_
                and wcswidth_simple(row[c - 1][0]) == 2
            ):
                row[c - 1] = v
                row.pop(c, None)
                continue
            # ★ BUG-61（review 方向）：宽字符残留清理——被覆盖位置为宽字符
            #   首列（旧占 c+1 列，仅首列键）时同步清除 c+1 键（残留第二列
            #   字形）；新写入字符为宽字符（占 c+1 列）时清除 c+1 旧内容
            #   （slice_ 未覆盖该键——宽字符只写首列键）。修复前覆盖宽字符
            #   首列后行含孤立第二列字形（渲染出 ``a``+残留字形）。
            old = row.get(c)
            if old is not None and wcswidth_simple(old[0]) == 2 and (c + 1) not in slice_:
                row.pop(c + 1, None)
            row[c] = v
            if wcswidth_simple(v[0]) == 2 and (c + 1) not in slice_:
                row.pop(c + 1, None)
    return row


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
    if start_w <= 0 and end_w >= 10**9:
        return text
    chars: list[str] = []
    col = 0
    for ch in text:
        w = wcswidth_simple(ch)
        if col < end_w and col + w > start_w:
            chars.append(ch)
        col += w
        if col >= end_w and chars and col - w >= end_w:
            break
    return "".join(chars)


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
    out = Line()
    col = 0
    for run in line.runs:
        run_end = col + getattr(run, "width", 0)
        s = max(col, start_col)
        e = min(run_end, end_col)
        if s < e:
            text = _slice_run_text(run.text, s - col, e - col)
            if text:
                out.append(text, run.style)
        col = run_end
        if col >= end_col:
            break
    return out


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

    方向4（性能）：一次排序 + 段级累积——连续同 style 字符先累积到
    ``run_text``（行宽有界 ≤80 列，str += 段长可接受），最后一次性构造
    Line（免逐字符 ``Line.append`` 的段合并检查）。列间隙以空格段补齐。
    """
    if isinstance(row, Line):
        return row
    if row is None:
        return Line()
    line = Line()
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
        ch, style = row[col]
        if col > prev:
            line.append(" " * (col - prev))
            prev = col  # 空格段宽 = 空格数
        # ★ 批量 append（方向4 性能）：累积同 style 连续字符段，段级一次性
        #   Line.append（免逐字符 append 的段合并检查 + StyledRun 重建——
        #   基准 ~2x 提速）。段长受行宽约束（≤终端列数），str += 拼接可接受。
        # ★ 性能（2026-08-05）：连续段内非末尾字符用**相邻键差**推导宽度——
        #   画布写入不变量 ``col += wcswidth_simple(ch)`` 保证连续键差 = 前序
        #   字符宽度；差 1 必然 ASCII 宽 1（宽字符差 2、gap 差 >2 均不可能是 1）
        #   → 免 ``wcswidth_simple`` 调用（聊天文本以 ASCII 为主，内层热路径
        #   大量连续 ASCII 字符）。差 >=2（宽字符/gap）或段末尾（无下一键可
        #   推导）回退 ``wcswidth_simple``（单字符路径：ASCII O(1) / 缓存 O(1)）。
        j = i
        buf = ""
        while j < n:
            c2 = keys[j]
            if c2 < prev:
                # 键已被前序宽字符覆盖（宽字符的第二列）→ 跳过该键
                j += 1
                continue
            if c2 != prev:
                break
            ch2, st2 = row[c2]
            if st2 != style:
                break
            buf += ch2
            if j + 1 < n and keys[j + 1] == c2 + 1:
                cw = 1  # 连续 ASCII 快路径（相邻键差 1）
            else:
                cw = wcswidth_simple(ch2)
            prev = c2 + cw
            j += 1
        if buf:
            line.append(buf, style)
        i = j
    return line


__all__ = [
    "_line_as_dict",
    "_ensure_row_dict",
    "_overlaps_wide_second_col",
    "_merge_line",
    "_slice_run_text",
    "_slice_line",
    "_canvas_row_to_line",
]
