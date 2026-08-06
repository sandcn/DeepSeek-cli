"""帧差异区间纯函数 — 差异区间收集 / 尾部位移判定 / 锚点查找。

模块边界（2026-08-05 架构优化）：从 ``ink/renderer.py`` 拆分——行级 diff
的纯逻辑（仅依赖 prev/frame 帧数据，不依赖 InkRenderer 实例状态）独立成
模块，供 InkRenderer（差异区间重写 / 平移快路径）与测试直接复用。

比较语义（与 ``diff.first_diff_line`` 一致）：身份短路（同 Line 对象恒相等）
+ runs 值相等。
"""

from __future__ import annotations

from .output import Frame


def _diff_runs(
    prev: Frame,
    frame: Frame,
    n: int,
    start: int = 0,
) -> list[tuple[int, int]]:
    """收集两帧前 n 行的差异区间（[start, end) 行号，升序、不重叠）。

    与 ``first_diff_line`` 相同比较语义：身份短路（Line 对象相同 → 相等）
    + runs 值相等。连续差异行合并为一个区间（区间内逐行 ``\r``+重写，
    免逐行光标移动）；区间间以光标移动衔接。仅覆盖两帧共有行
    （``min(prev.height, frame.height)``）；高度差（新增/删除行）由调用方
    delta 分支单独处理。

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
    for idx in range(start, n):
        p = p_lines[idx]
        f = f_lines[idx]
        differs = p is not f and p.runs != f.runs
        if differs and not in_run:
            in_run = True
            run_start = idx
        elif not differs and in_run:
            in_run = False
            runs.append((run_start, idx))
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
        if x is not y and x.runs != y.runs:
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
            if old_line is new_line or old_line.runs == new_line.runs:
                j -= 1
                continue
        break
    return j


__all__ = ["_diff_runs", "_is_tail_shifted", "_find_tail_anchor"]
