"""xterm-256 调色板 — 构建 + 最近色查找（纯数据/算法）。

模块边界（2026-08-05 架构优化）：从 ``core/color.py`` 拆分——xterm-256
调色板（16 系统色 + 216 cube + 24 灰阶）与最近色查找为纯数据/算法，供
``TrueColor.to_256``/``Color256.from_rgb``/``rgb_to_256``/``lerp_color``
共享。无外部依赖（仅标准库）。
"""

from __future__ import annotations


def _build_xterm_palette() -> list[tuple[int, int, int]]:
    """构建标准 xterm-256 调色板（RGB 分量列表）。

    包含：16 色系统色 + 216 色 cube + 24 级灰度。
    """
    palette: list[tuple[int, int, int]] = []
    # 0-15: 系统色（ANSI 基本 16 色）
    sys_colors = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ]
    palette.extend(sys_colors)
    # 16-231: 6×6×6 color cube
    for r in range(6):
        for g in range(6):
            for b_val in range(6):
                palette.append((
                    0 if r == 0 else 55 + r * 40,
                    0 if g == 0 else 55 + g * 40,
                    0 if b_val == 0 else 55 + b_val * 40,
                ))
    # 232-255: 24 级灰度
    for i in range(24):
        v = 8 + i * 10
        palette.append((v, v, v))
    return palette


_XTERM_PALETTE: list[tuple[int, int, int]] = _build_xterm_palette()


def _find_closest_256(r: int, g: int, b: int) -> int:
    """在 xterm-256 调色板中查找最接近给定 RGB 的色号（欧氏距离）。

    Args:
        r, g, b: RGB 分量（0-255）。

    Returns:
        最接近的 xterm-256 色号（0-255）。
    """
    best_idx = 0
    best_dist = float('inf')
    for idx, (pr, pg, pb) in enumerate(_XTERM_PALETTE):
        dr = r - pr
        dg = g - pg
        db = b - pb
        dist = dr * dr + dg * dg + db * db
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


__all__ = ["_build_xterm_palette", "_XTERM_PALETTE", "_find_closest_256"]
