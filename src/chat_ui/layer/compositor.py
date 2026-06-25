"""TUI 层级渲染系统 — Compositor。

将多层 buffer 按层级顺序合并为最终帧（行列表）。
"""

from __future__ import annotations

from .types import Layer, LayerBuffer


class Compositor:
    """层级合并器。

    按层级顺序（从低到高）合并多层 buffer。
    高层非 None 行覆盖低层；None 表示透明，允许下层内容穿透。
    """

    def composite(self, buffers: dict[Layer, LayerBuffer]) -> list[str]:
        """合并多层 buffer 为最终帧。

        遍历路径：
        1. 确定输出行数（取所有层中最大行数）
        2. 对每一行，从低层到高层查找第一个非 None 值
        3. 若所有层该行均为 None，输出空字符串 ""

        Args:
            buffers: {Layer: LayerBuffer} 映射，LayerBuffer 为 list[str|None]

        Returns:
            合并后的行列表（纯 str，无 None）
        """
        if not buffers:
            return []

        # 按层级数值排序（低→高）
        sorted_layers = sorted(buffers.keys(), key=lambda l: l.value)

        # 确定最大行数
        max_rows = 0
        for buf in buffers.values():
            if buf:
                max_rows = max(max_rows, len(buf))

        if max_rows == 0:
            return []

        result: list[str] = []
        for row_idx in range(max_rows):
            merged = self._merge_row(buffers, sorted_layers, row_idx)
            result.append(merged)

        # 去除尾部空行（但保留中间空行）
        while result and result[-1] == "":
            result.pop()

        return result

    def _merge_row(
        self,
        buffers: dict[Layer, LayerBuffer],
        sorted_layers: list[Layer],
        row_idx: int,
    ) -> str:
        """合并单行：从高到低找第一个非 None 值。

        遍历顺序从高层到低层（sorted_layers 已排序，反向遍历）。
        找到第一个非 None 值即返回；全为 None 返回 ""。

        Args:
            buffers: 所有层的 buffer 映射
            sorted_layers: 已排序的层列表（低→高）
            row_idx: 行索引

        Returns:
            合并后的行文本
        """
        # 从最高层到最低层遍历
        for layer in reversed(sorted_layers):
            buf = buffers.get(layer)
            if buf is None:
                continue
            if row_idx < len(buf) and buf[row_idx] is not None:
                return buf[row_idx]

        return ""
