"""帧差异区间纯函数 — 差异区间收集 / 尾部位移判定 / 锚点查找。

模块边界（2026-08-05 架构优化）：从 ``ink/renderer.py`` 拆分——行级 diff
的纯逻辑（仅依赖 prev/frame 帧数据，不依赖 InkRenderer 实例状态）独立成
模块，供 InkRenderer（差异区间重写 / 平移快路径）与测试直接复用。

比较语义（与 ``diff.first_diff_line`` 一致）：身份短路（同 Line 对象恒相等）
+ runs 值相等。
"""

from __future__ import annotations

from .output import Frame, Line


def _lines_equal(a: Line, b: Line) -> bool:
    """两行内容是否相等（行级 diff 的唯一比较语义）。

    与 ``Line.__eq__`` 同语义，但直接在函数体内做 O(1) 快速拒绝（同对象 /
    同 runs 列表 / run 数 / 已缓存宽度），不额外走一次 Python 方法调用——
    行级 diff 每帧对 live 区所有非同一对象行调用本函数，方法调用开销在
    大文档下可感知。

    ★ 性能（2026-10-23）：修复前各 diff 函数直接 ``p.runs != f.runs``——
      大文档每帧对未变化行执行完整 run 比较；现在宽度（已缓存时）/run 数
      不同即拒绝。语义不变：宽度是 runs 的派生值，宽度不同必然内容不同。
    """
    if a is b:
        return True
    ra = a.runs
    rb = b.runs
    if ra is rb:
        return True
    if len(ra) != len(rb):
        return False
    wa = a._w
    if wa is not None:
        wb = b._w
        if wb is not None and wa != wb:
            return False
    # ★ 性能（2026-10-23 比较器）：手写 run 逐项比较——先在字段级做身份
    #   短路（画布/布局缓存在样式对象上大量复用），再退化为值比较。修复前
    #   ``ra == rb`` 对每个 run 调用 ``StyledRun.__eq__``（frozen dataclass，
    #   每次构造三元组并逐字段比较）；等值行（1500 行 × 4 run）同进程交替
    #   实测快约 10%~15%。字段级比较与 dataclass 相等一致：``runs`` 只含
    #   ``StyledRun``（``Line.append`` / ``append_run`` 是唯一写入路径，
    #   两类对象字段完全相同）——逐 run ``type()`` 校验会让本函数比原生
    #   list 比较更慢，故不引入。
    for x, y in zip(ra, rb):
        if x is y:
            continue
        if x.text is not y.text and x.text != y.text:
            return False
        if x.style is not y.style and x.style != y.style:
            return False
        if x.link is not y.link and x.link != y.link:
            return False
    return True


def _skip_interval(prev: Frame, frame: Frame) -> tuple[int, int]:
    """稳定前缀跳过区间 ``[lo, hi)``（跨帧同一前缀对象时）；无则 ``(0, 0)``。

    ``render_frame`` 的 committed 前缀复用命中时，``_stable_prefix`` 为**同一
    列表对象**且 offset/len 一致——区间内元素是同一 ``Line`` 对象（跨帧同
    对象）→ 必然无差异，可整段跳过（与 ``diff.first_diff_line`` 同契约）。
    """
    sp = prev._stable_prefix
    if (
        sp is not None
        and sp is frame._stable_prefix
        and prev._stable_prefix_offset == frame._stable_prefix_offset
        and prev._stable_prefix_len == frame._stable_prefix_len
    ):
        lo = prev._stable_prefix_offset
        return lo, lo + prev._stable_prefix_len
    return 0, 0


def _diff_runs(
    prev: Frame,
    frame: Frame,
    n: int,
    start: int = 0,
) -> list[tuple[int, int]]:
    """收集两帧前 n 行的差异区间（[start, end) 行号，升序、不重叠）。

    与 ``first_diff_line`` 相同比较语义：身份短路（Line 对象相同 → 相等）
    + runs 值相等。连续差异行合并为一个区间（区间内逐行 ``\\r``+重写，
    免逐行光标移动）；区间间以光标移动衔接。仅覆盖两帧共有行
    （``min(prev.height, frame.height)``）；高度差（新增/删除行）由调用方
    delta 分支单独处理。

    ★ 稳定前缀跳过（性能，与 ``first_diff_line`` 一致）：``_stable_prefix``
    跨帧同一对象时其覆盖区间整体跳过（区间内行必然相同）——大文档下头部
    动画（标题栏呼吸色变化）触发 delta≠0 的 head_runs 扫描时，committed
    前缀区间零逐行比较（修复前本函数无该跳过，head_runs 每帧扫描整段前缀）。

    Args:
        prev: 上一帧。
        frame: 新帧。
        n: 参与比较的行数（``min(prev.height, frame.height)``）。
        start: 起始扫描行（0-based，含）；调用方保证 [0, start) 无差异
            （``first_diff_line`` 定义）——免扫描不变的 committed 前缀。

    Returns:
        差异区间列表（每个为 [start, end) 行号，至少含一行）。
    """
    runs: list[tuple[int, int]] = []
    in_run = False
    run_start = start
    p_lines = prev.lines
    f_lines = frame.lines
    skip_lo, skip_hi = _skip_interval(prev, frame)
    idx = start
    while idx < n:
        if skip_lo <= idx < skip_hi:
            # 跳过区间：区间内无差异 → 结束当前 run（若正在收集）
            if in_run:
                in_run = False
                runs.append((run_start, idx))
            idx = skip_hi
            continue
        p = p_lines[idx]
        f = f_lines[idx]
        # 身份短路内联 + 内容比较内联（不调用 helper）：本函数在 delta!=0
        # 的流式帧与 delta==0 的差异收集中每帧扫描共有行，热路径避免每行
        # 一次 Python 函数调用。比较语义与 ``_lines_equal`` 完全一致。
        if p is f:
            differs = False
        else:
            pr = p.runs
            fr = f.runs
            if pr is fr:
                differs = False
            elif len(pr) != len(fr):
                differs = True
            else:
                pw = p._w
                if pw is not None:
                    fw = f._w
                    if fw is not None and pw != fw:
                        differs = True
                    else:
                        differs = pr != fr
                else:
                    differs = pr != fr
        if differs and not in_run:
            in_run = True
            run_start = idx
        elif not differs and in_run:
            in_run = False
            runs.append((run_start, idx))
        idx += 1
    if in_run:
        runs.append((run_start, n))
    return runs


def _is_tail_shifted(self_prev: Frame, frame: Frame, i: int, delta: int) -> bool:
    """检测尾部内容是否只是整体下移（仅新增 delta 行）。

    规则：``prev.lines[i:prev_h]`` 与 ``frame.lines[i+delta:new_h]``
    逐行相同（身份短路 + runs 值相等）。

    Args:
        self_prev: 上一帧（``prev``——参数名保留调用点语义）。
        frame: 新帧。
        i: 首差异行。
        delta: 高度差（new_h - prev_h，>0 时检测有意义）。

    Returns:
        True — 尾部内容整体下移且相同（可跳过重写）。
    """
    p = self_prev.lines
    n = frame.lines
    # ★ 2026-10-23（比较器边界守卫）：i 越界（<0 / > prev.height）时
    #   ``count = prev.height - i`` 为负，``len(n) - start`` 同为负 → 两段
    #   范围都为空，原实现 ``count != len(n) - start`` 为假且比较循环不执行
    #   → **误返回 True**（声称尾部整体下移）。当前调用方只传 first_diff_line
    #   结果（0 <= i <= min(prev_h, new_h)），属防御缺口；显式拒绝越界参数。
    if i < 0 or i > self_prev.height:
        return False
    start = i + delta
    # ★ P3 修复（review 方向）：delta<0 时 start = i + delta 可为负——Python
    #   负索引回绕（``n[start]`` 从末尾取、``len(n)-start`` 虚增）导致错误
    #   判定/越界。防御：start < 0 直接返回 False（非纯下移尾部）。
    if start < 0 or start > len(n):
        return False
    # 索引循环比较（避免每帧创建两段切片——方向3 性能）
    count = self_prev.height - i
    if count != len(n) - start:
        return False
    for k in range(count):
        x = p[i + k]
        y = n[start + k]
        if x is not y and not _lines_equal(x, y):
            return False
    return True


def _find_tail_anchor(prev: Frame, frame: Frame, delta: int) -> int:
    """从文档末尾向前找尾部位移锚点（方向4 优化）。

    旧帧 ``[j, prev_h)`` 与新帧 ``[j+delta, new_h)`` 逐行相同（身份短路 +
    runs 值相等）时，j 为位移锚点——高度差发生在 j 之后，尾部内容整体
    位移 delta 行。头部差异（j 之前，如标题栏呼吸色变化）与尾部位移
    （j 之后）分开处理：仅重写头部差异区间 + 从锚点起的位移区，跳过
    锚点之前的未变化行（committed 历史可见区不再被头部动画引发全量
    重写——流式期间每帧重写范围从 O(可见区) 降为 O(头部差异 + 位移区)）。

    Args:
        prev: 上一帧。
        frame: 新帧。
        delta: 高度差（new_h - prev_h，可为负）。

    Returns:
        位移锚点 j（0-based，范围 [0, prev_h]）；无相同尾部时返回 0。
    """
    p = prev.lines
    n = frame.lines
    j = prev.height
    while j > 0:
        old_line = p[j - 1]
        new_idx = j - 1 + delta
        if 0 <= new_idx < len(n):
            new_line = n[new_idx]
            if old_line is not new_line and not _lines_equal(old_line, new_line):
                break
            j -= 1
            continue
        break
    return j


__all__ = [
    "_lines_equal",
    "_diff_runs",
    "_is_tail_shifted",
    "_find_tail_anchor",
    "_skip_interval",
]
