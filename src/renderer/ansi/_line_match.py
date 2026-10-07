"""_line_match — 行列表的「前缀 / 头部滑窗」重叠检测（共享工具）。

预览有界化（解析器 ``_preview_tail`` 把超长块截为最近 ``_PREVIEW_MAX_LINES``
行）会让源行列表**头部滑窗**：每帧丢弃最旧行、追加最新行。此时行级增量缓存
（``LinePreviewCache``）与段落行边界定界符跟踪（``ParagraphBoundaryScanner``）
若只按「最长公共前缀」复用，会因首行变化而整块重算。

本模块提供统一的重叠检测：最长公共前缀 + 头部滑窗偏移，供上述两处复用，
避免重复实现导致行为不一致。
"""

from __future__ import annotations

#: 滑窗候选搜索上限（丢弃行数通常很小；上限避免最坏 O(行数²) 扫描）
SLIDING_SEARCH_MAX = 64


def common_prefix_len(a: list, b: list) -> int:
    """返回两列表的最长公共前缀长度。"""
    n = len(a) if len(a) < len(b) else len(b)
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def sliding_drop(a: list, b: list, ignore_tail: bool = False) -> int:
    """检测「头部滑窗」，返回 ``a`` 头部被丢弃的行数 ``d``（无重叠返回 0）。

    滑窗语义：``a[d:]`` 与 ``b`` 的前 ``len(a)-d`` 行逐行相等（``d >= 1``）。
    用 ``b[0]`` 在 ``a`` 中的位置作候选（从 1 起），命中即校验重叠区间；
    候选上限 ``SLIDING_SEARCH_MAX``。``b[0]`` 在 ``a`` 中重复出现时返回首个
    通过校验的偏移（重叠区间内容相同，复用等价）。

    ``ignore_tail=True`` 时两侧各忽略最后一行再匹配——流式预览的最后一行是
    活动行（每帧变化），不应参与重叠判定。
    """
    m = len(a)
    n = len(b)
    if ignore_tail:
        m -= 1
        n -= 1
    if m < 2 or n == 0:
        return 0
    first = b[0]
    idx = 1
    end = m if m < 1 + SLIDING_SEARCH_MAX else 1 + SLIDING_SEARCH_MAX
    while idx < end:
        if a[idx] == first:
            k = m - idx
            if k > n:
                k = n
            ok = True
            for j in range(1, k):
                if a[idx + j] != b[j]:
                    ok = False
                    break
            if ok:
                return idx
        idx += 1
    return 0


__all__ = ["common_prefix_len", "sliding_drop", "SLIDING_SEARCH_MAX"]
