"""帧行级 diff — 新旧 Frame 首个差异行 + 高度差处理。

InkRenderer 用它决定从哪一行开始重写：静态内容在首差异行之前永不重写。
"""

from __future__ import annotations

from .output import Frame


def first_diff_line(prev: Frame, new: Frame) -> int:
    """计算首差异行索引（0-based）。

    规则：
      - 逐行比较 runs（StyledRun 冻结 dataclass 值相等）；首个不等行返回其索引。
        直接比较 runs 而非 render()（ANSI 串），避免为未变化行构建 ANSI。
      - 前缀完全一致但高度不同：返回较短的帧高度（从该行开始补写/清行）。
      - 完全一致（含高度）：返回 -1。

    ★ 稳定前缀跳过（PERF-7）：prev/new 的 ``_stable_prefix`` 为**同一列表
    对象**（``render_frame`` 的 committed 前缀复用命中）且 offset/len 一致时，
    覆盖区间 ``[offset, offset+len)`` 的元素是**同一 Line 对象**（跨帧同对象）
    → 必然无差异 → 从区间末尾开始扫描，免大文档每帧全量逐行 is 比较
    （第一差异行位于尾部 live 区时收益明显）。区间外的行（TopHeader 等
    每帧新建 Line）仍逐行比较。前缀对象变化（committed 更新）→ 不跳过 →
    正常逐行扫描（正确性保持）。

    Args:
        prev: 上一帧。
        new: 新帧。

    Returns:
        首差异行索引；无差异返回 -1。
    """
    p_lines = prev.lines
    n_lines = new.lines
    start = 0
    sp = prev._stable_prefix
    if (
        sp is not None
        and sp is new._stable_prefix
        and prev._stable_prefix_offset == new._stable_prefix_offset
        and prev._stable_prefix_len == new._stable_prefix_len
    ):
        start = prev._stable_prefix_offset + prev._stable_prefix_len
        if start > len(p_lines):
            start = len(p_lines)
        if start > len(n_lines):
            start = len(n_lines)
    n = min(len(p_lines), len(n_lines))
    for i in range(start, n):
        # ★ 身份短路：缓存行（committed-chat）为同一对象 → O(1) 跳过比较
        p = p_lines[i]
        f = n_lines[i]
        if p is not f and p.runs != f.runs:
            return i
    if len(p_lines) != len(n_lines):
        return n
    return -1


def height_delta(prev: Frame, new: Frame) -> int:
    """高度差（new_height - prev_height）。"""
    return len(new.lines) - len(prev.lines)


__all__ = ["first_diff_line", "height_delta"]
