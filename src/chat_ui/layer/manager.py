"""TUI 层级渲染系统 — Layer 管理器。

管理多个渲染层的独立 buffer，提供写入/追加/清空/调整大小操作。
"""

from __future__ import annotations

import logging
from typing import Optional

from .types import Layer, LayerBuffer, DEFAULT_LAYER, MAX_LAYERS

logger = logging.getLogger(__name__)


class LayerManager:
    """多层级 buffer 管理器。

    每层维护独立的行列表 buffer。None 表示该位置透明（允许下层穿透）。
    """

    def __init__(
        self,
        height: int,
        width: int,
        layers: Optional[list[Layer]] = None,
    ) -> None:
        """初始化层级管理器。

        Args:
            height: 终端行数（buffer 行数）
            width: 终端列数（每行字符数，用于填充对齐）
            layers: 要管理的层级列表，默认 [CONTENT, OVERLAY]
        """
        if layers is None:
            layers = [Layer.CONTENT, Layer.OVERLAY]
        if len(layers) > MAX_LAYERS:
            logger.warning(
                "层级数 %d 超过最大值 %d，将截断", len(layers), MAX_LAYERS
            )
            layers = layers[:MAX_LAYERS]

        self._height = height
        self._width = width
        self._layers = list(layers)
        # 每层 buffer: list[LayerBuffer]
        self._buffers: dict[Layer, LayerBuffer] = {}
        for layer in self._layers:
            self._buffers[layer] = self._create_buffer()

    # ── 公共 API ──────────────────────────────────────────

    @property
    def height(self) -> int:
        return self._height

    @property
    def width(self) -> int:
        return self._width

    @property
    def layers(self) -> list[Layer]:
        return list(self._layers)

    def write(self, layer: Layer, row: int, text: str) -> None:
        """写入指定层的指定行。

        Args:
            layer: 目标层级
            row: 行索引（0-based）
            text: 要写入的文本（不含换行符）
        """
        if layer not in self._buffers:
            logger.debug("Layer %s 不存在，跳过写入", layer)
            return
        if not (0 <= row < self._height):
            logger.debug("行索引 %d 越界 [0, %d)，跳过写入", row, self._height)
            return
        self._buffers[layer][row] = text

    def write_lines(self, layer: Layer, start_row: int, lines: list[str]) -> None:
        """批量写入多行到指定层。

        Args:
            layer: 目标层级
            start_row: 起始行索引（0-based）
            lines: 要写入的行列表
        """
        for i, line in enumerate(lines):
            self.write(layer, start_row + i, line)

    def append(self, layer: Layer, text: str) -> int:
        """追加文本到指定层末尾（自动处理换行）。

        Args:
            layer: 目标层级
            text: 要追加的文本（可含 \\n）

        Returns:
            实际写入的行数
        """
        if layer not in self._buffers:
            logger.debug("Layer %s 不存在，跳过追加", layer)
            return 0

        buffer = self._buffers[layer]
        # 找到第一个 None 行作为起始位置
        start_row = 0
        for i in range(self._height):
            if buffer[i] is None:
                start_row = i
                break
        else:
            # buffer 已满
            logger.debug("Layer %s buffer 已满，无法追加", layer)
            return 0

        split_lines = text.split("\n")
        written = 0
        for i, line in enumerate(split_lines):
            row = start_row + i
            if row >= self._height:
                break
            buffer[row] = line
            written += 1

        return written

    def get_buffer(self, layer: Layer) -> LayerBuffer:
        """获取指定层的 buffer 副本。

        Args:
            layer: 目标层级

        Returns:
            层的行列表副本（修改不影响内部 buffer）
        """
        if layer not in self._buffers:
            return []
        return list(self._buffers[layer])

    def get_all_buffers(self) -> dict[Layer, LayerBuffer]:
        """获取所有层的 buffer 副本。

        Returns:
            {Layer: LayerBuffer} 映射
        """
        return {layer: list(buf) for layer, buf in self._buffers.items()}

    def resize(self, height: int, width: int) -> None:
        """调整所有 buffer 尺寸（终端大小变化时调用）。

        Args:
            height: 新的行数
            width: 新的列数
        """
        if height == self._height and width == self._width:
            return

        old_height = self._height
        old_width = self._width
        self._height = height
        self._width = width

        for layer in self._layers:
            old_buffer = self._buffers[layer]
            new_buffer = self._create_buffer()
            # 保留旧内容（截断或填充 None）
            copy_rows = min(old_height, height)
            for i in range(copy_rows):
                new_buffer[i] = old_buffer[i]
            self._buffers[layer] = new_buffer

        logger.debug(
            "LayerManager 调整大小: (%d, %d) → (%d, %d)",
            old_height, old_width,
            height, width,
        )

    def clear(self, layer: Layer) -> None:
        """清空指定层。

        Args:
            layer: 目标层级
        """
        if layer in self._buffers:
            self._buffers[layer] = self._create_buffer()

    def clear_all(self) -> None:
        """清空所有层。"""
        for layer in self._layers:
            self._buffers[layer] = self._create_buffer()

    # ── 内部方法 ──────────────────────────────────────────

    def _create_buffer(self) -> LayerBuffer:
        """创建新的空 buffer（全 None）。"""
        return [None] * self._height
