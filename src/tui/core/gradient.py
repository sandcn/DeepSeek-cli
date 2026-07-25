"""颜色渐变基础设施（Layer 0 Core）。

提供 xterm-256 调色板查找表、十六进制颜色转换、线性渐变插值工具。

包含：
  - _build_xterm_palette(): 构建 xterm-256 调色板的 RGB 查找表
  - _XTERM_PALETTE: 预计算的 xterm 全色表（模块级常量）
  - hex_to_256(): 十六进制转最接近的 xterm-256 色号
  - gradient_step(): 带 @lru_cache 的线性插值单步色号
  - gradient_range(): 带 @lru_cache 的均匀分布色号列表

设计原则：
  - 纯函数，无 I/O 副作用
  - 使用 @lru_cache 避免重复计算热点渐变
  - 不导入任何 src.ui 或 src.tui 内部模块
"""

from __future__ import annotations

from functools import lru_cache


def _build_xterm_palette() -> list[tuple[int, int, int]]:
    """构建 xterm-256 调色板的 RGB 颜色查找表（索引 0-255）。

    一次预计算，后续只读查找。

    Returns:
        len=256 的 RGB 元组列表，索引对应 256 色号。
    """
    palette: list[tuple[int, int, int]] = [(0, 0, 0)] * 256

    # ── 标准 16 色（0-15）──
    system: list[tuple[int, int, int]] = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ]
    for i, rgb in enumerate(system):
        palette[i] = rgb

    # ── 6×6×6 色彩立方体（16-231）──
    levels: list[int] = [0, 95, 135, 175, 215, 255]
    for r in range(6):
        for g in range(6):
            for b in range(6):
                idx: int = 16 + r * 36 + g * 6 + b
                palette[idx] = (levels[r], levels[g], levels[b])

    # ── 24 级灰度梯度（232-255）──
    for i in range(24):
        val: int = 8 + i * 10
        palette[232 + i] = (val, val, val)

    return palette


_XTERM_PALETTE: list[tuple[int, int, int]] = _build_xterm_palette()
"""xterm-256 全色表（0-255 索引 → RGB 元组），模块加载时预计算一次。"""


def _find_closest_256(r: int, g: int, b: int) -> int:
    """在 xterm-256 调色板中查找最接近的色号。

    使用欧氏距离（平方）度量颜色差异，返回距离最小的色号。
    精确匹配时提前退出。

    Args:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。

    Returns:
        最接近的色号（0-255）。
    """
    best_idx: int = 0
    best_dist: int = 2**31 - 1

    for i, (cr, cg, cb) in enumerate(_XTERM_PALETTE):
        dr, dg, db = r - cr, g - cg, b - cb
        dist: int = dr * dr + dg * dg + db * db
        if dist < best_dist:
            best_dist = dist
            best_idx = i
            if best_dist == 0:  # 精确匹配，提前退出
                break

    return best_idx


def hex_to_256(hex_color: str) -> int:
    """将十六进制颜色转换为最接近的 xterm-256 色号（0-255）。

    参数:
        hex_color: 十六进制颜色字符串，支持 ``#FF8800`` 或 ``ff8800`` 格式。

    返回:
        最接近的 xterm-256 色号。异常输入返回 15（白色）兜底。
    """
    try:
        cleaned = hex_color.lstrip("#")
        r, g, b = (int(cleaned[i: i + 2], 16) for i in (0, 2, 4))
        return _find_closest_256(r, g, b)
    except (ValueError, IndexError, AttributeError, TypeError):
        return 15  # 白色兜底


@lru_cache(maxsize=256)
def gradient_step(start: int, end: int, steps: int, index: int) -> int:
    """计算 start→end 间线性插值的第 index 步色号。

    参数:
        start: 起始色号（0-255）
        end: 结束色号（0-255）
        steps: 总步数（>= 1）
        index: 当前步索引，自动 clamp 到 [0, steps-1]

    返回:
        插值后的色号（0-255），四舍五入取整。
    """
    if steps <= 1:
        return start
    idx = max(0, min(index, steps - 1))
    value: float = start + (end - start) * idx / (steps - 1)
    return max(0, min(255, round(value)))


@lru_cache(maxsize=256)
def gradient_range(start: int, end: int, steps: int) -> list[int]:
    """返回 start→end 间 steps 个均匀分布的色号列表。

    参数:
        start: 起始色号（0-255）
        end: 结束色号（0-255）
        steps: 色号数量

    返回:
        steps=0 → 空列表；steps=1 → [start]；steps>=2 → 均匀插值序列。
    """
    if steps <= 0:
        return []
    if steps == 1:
        return [start]
    return [gradient_step(start, end, steps, i) for i in range(steps)]
