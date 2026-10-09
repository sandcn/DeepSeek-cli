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
    """返回两列表的最长公共前缀长度。

    ★ 性能（流式追加热路径）：先做一次 **C 级整段比较** ``a[:n] == b[:n]``
    ——流式预览中 ``a`` 绝大多数场景是新行列表的前缀（只追加），此时一次
    C 级列表比较即得结果（免逐元素 Python 循环）。

    ★ 性能（分歧定位二分，2026-10）：整段比较失败时，改用**二分查找**定位
    首个分歧位置（每次比较取 ``a[lo:mid] == b[lo:mid]``，均为 C 级列表比较），
    Python 层迭代次数由 O(分歧位置) 降为 O(log n)。流式预览中「已确定行
    前缀 + 仅活动行变化」是常见形态（前缀长度接近全表），修复前回退逐元素
    ``while`` 循环逐个比较到分歧点（200 行预览实测 21µs/次、长段落流式每帧
    调用）；二分后为常数级 C 级比较（约 8 次），单次降至数微秒。
    """
    la = len(a)
    lb = len(b)
    n = la if la < lb else lb
    if n == 0:
        return 0
    # ★ 首元素短路：头部滑窗（预览截断丢弃最旧行）是新旧行列表最常见的
    #   不一致形态，此时首元素必不同——直接返回 0，省去整段/二分比较。
    if a[0] != b[0]:
        return 0
    if a[:n] == b[:n]:
        return n
    # 已知 a[:lo] == b[:lo]（lo 起始 0 恒真）、a[:n] != b[:n]（hi 起始 n）
    lo = 0
    hi = n
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[lo:mid] == b[lo:mid]:
            lo = mid
        else:
            hi = mid
    return lo


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
    # ``n <= 0`` 而非 ``n == 0``：``ignore_tail`` 会让空 ``b`` 的 ``n`` 变为
    # ``-1``——修复前绕过判空后 ``b[0]`` 抛 IndexError（空活动行序列触发）。
    if m < 2 or n <= 0:
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
