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

    Args:
        prev: 上一帧。
        new: 新帧。

    Returns:
        首差异行索引；无差异返回 -1。
    """
    n = min(len(prev.lines), len(new.lines))
    for i in range(n):
        # ★ 身份短路：缓存行（committed-chat）为同一对象 → O(1) 跳过比较
        if prev.lines[i] is not new.lines[i] and prev.lines[i].runs != new.lines[i].runs:
            return i
    if len(prev.lines) != len(new.lines):
        return n
    return -1


def height_delta(prev: Frame, new: Frame) -> int:
    """高度差（new_height - prev_height）。"""
    return len(new.lines) - len(prev.lines)


__all__ = ["first_diff_line", "height_delta"]
