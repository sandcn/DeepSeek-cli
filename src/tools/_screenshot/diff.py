"""截图差异比较（``bash_opt`` 输入动作的 ``diff`` / ``wait_for`` 判定层）。

用途：操作 GUI 后确认「界面是否真的变了」——

  - ``diff=true``：注入前后各截一张图，比较像素差异，回传是否变化、变化
    比例与变化区域（bounding box），帮助判断点击 / 按键是否生效；
  - ``wait_for='change'``：轮询截图直到画面相对注入前发生变化（点击后等
    界面刷新到位再返回）；
  - ``wait_for='stable'``：轮询截图直到连续两次画面相同（动画 / 加载结束）。

比较策略（性能）：整块字节相等 → 无变化（最快路径）；否则逐行做字节比较，
只对**内容不同的行**做逐像素容差计算，避免全图 Python 循环。

尺寸变化（窗口被缩放 / 换了窗口）视为「已变化」，不逐像素比较。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .png_decode import DecodedImage, decode_png_file

logger = logging.getLogger(__name__)

#: 默认颜色容差（每通道 0..255）：抗 PNG 编码 / 抗锯齿造成的极微小差异。
#: 0 表示要求像素完全一致。
DEFAULT_TOLERANCE = 8

#: 每像素 3 通道，颜色差按三通道之和与 ``tolerance * 3`` 比较
_CHANNELS = 3


@dataclass(frozen=True)
class DiffResult:
    """两张截图之间的差异。

    Attributes:
        changed: 是否发生变化（尺寸不同或存在超容差像素）。
        changed_pixels: 超容差的像素数。
        total_pixels: 比较的像素总数（尺寸不同时取较大者）。
        changed_ratio: ``changed_pixels / total_pixels``（0..1）。
        size_changed: 两张图尺寸是否不同。
        region: 变化区域的 bounding box ``{x, y, width, height}``（像素，
            以 before 图左上角为原点）；无变化时为 ``None``。
    """

    changed: bool
    changed_pixels: int
    total_pixels: int
    changed_ratio: float
    size_changed: bool = False
    region: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "changed": self.changed,
            "changed_pixels": self.changed_pixels,
            "total_pixels": self.total_pixels,
            "changed_ratio": round(self.changed_ratio, 6),
            "size_changed": self.size_changed,
        }
        if self.region is not None:
            payload["region"] = dict(self.region)
        return payload

    def summary(self) -> str:
        """一行可读摘要（结果 hint 用）。"""
        if not self.changed:
            return "画面未发生变化"
        if self.size_changed:
            return "画面尺寸已变化（窗口被缩放或换了窗口）"
        percent = self.changed_ratio * 100
        return (f"画面已变化：{self.changed_pixels} 像素（{percent:.2f}%）"
                f"，区域 {self.region}")


def compare_png_files(before_path: str, after_path: str, *,
                      tolerance: int = DEFAULT_TOLERANCE) -> DiffResult:
    """比较两个 PNG 文件（读取失败时抛出底层异常）。"""
    return compare_images(decode_png_file(before_path),
                          decode_png_file(after_path),
                          tolerance=tolerance)


def compare_images(before: DecodedImage, after: DecodedImage, *,
                   tolerance: int = DEFAULT_TOLERANCE) -> DiffResult:
    """比较两张已解码图像，返回差异结果。

    Args:
        before: 变化前的图像。
        after: 变化后的图像。
        tolerance: 每通道颜色容差（0..255）。
    """
    limit = max(int(tolerance), 0)
    if before.width != after.width or before.height != after.height:
        total = max(before.width * before.height, after.width * after.height)
        return DiffResult(
            changed=True,
            changed_pixels=total,
            total_pixels=total,
            changed_ratio=1.0,
            size_changed=True,
            region=None,
        )
    width, height = before.width, before.height
    total = width * height
    if before.rgb == after.rgb:
        return DiffResult(False, 0, total, 0.0)
    row_bytes = width * _CHANNELS
    source = before.rgb
    target = after.rgb
    changed_pixels = 0
    min_x, min_y = width, height
    max_x, max_y = -1, -1
    threshold = limit * _CHANNELS
    for row in range(height):
        start = row * row_bytes
        end = start + row_bytes
        if source[start:end] == target[start:end]:
            continue
        row_changed = 0
        row_min_x = width
        row_max_x = -1
        base = start
        for column in range(width):
            offset = base + column * _CHANNELS
            delta = (abs(source[offset] - target[offset])
                     + abs(source[offset + 1] - target[offset + 1])
                     + abs(source[offset + 2] - target[offset + 2]))
            if delta > threshold:
                row_changed += 1
                if column < row_min_x:
                    row_min_x = column
                if column > row_max_x:
                    row_max_x = column
        if row_changed:
            changed_pixels += row_changed
            min_x = min(min_x, row_min_x)
            max_x = max(max_x, row_max_x)
            min_y = min(min_y, row)
            max_y = max(max_y, row)
    if changed_pixels == 0:
        return DiffResult(False, 0, total, 0.0)
    region = {
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x + 1,
        "height": max_y - min_y + 1,
    }
    return DiffResult(
        changed=True,
        changed_pixels=changed_pixels,
        total_pixels=total,
        changed_ratio=changed_pixels / total if total else 0.0,
        size_changed=False,
        region=region,
    )


def images_equal(before: DecodedImage, after: DecodedImage, *,
                 tolerance: int = DEFAULT_TOLERANCE) -> bool:
    """两张图是否可视为相同（``wait_for='stable'`` 判定用）。"""
    return not compare_images(before, after, tolerance=tolerance).changed


__all__ = [
    "DEFAULT_TOLERANCE",
    "DiffResult",
    "compare_images",
    "compare_png_files",
    "images_equal",
]
