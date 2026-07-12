"""
终端颜色和样式管理（兼容性外观层）

已拆分为:
  - core/constants.py:  ANSI 颜色常量（权威源，core 层可安全导入）
  - ansi.py:            ANSI 工具函数（视觉宽度/截断/清洗）
  - theme.py:           语义化主题颜色映射
  - console.py:         rich.Console 惰性初始化

兼容性：本模块重新导出 core.constants 中的颜色常量 + ansi.py 中的工具函数，
外部代码可继续使用 from ..ui.colors import GREEN, YELLOW, ...

渐变工具使用方法：
  - hex_to_256("#FF8800") → int: 将十六进制颜色转为最接近的 xterm-256 色号
  - gradient_step(start, end, steps, index) → int: 线性插值单步色号
  - gradient_range(start, end, steps) → list[int]: 生成均匀分布的色号列表
  - 预定义调色板：GRADIENT_SUNSET、GRADIENT_OCEAN、GRADIENT_FOREST、GRADIENT_FIRE、GRADIENT_NEON、
    GRADIENT_AURORA（极光渐变）、GRADIENT_CORAL（珊瑚渐变）、GRADIENT_MINT（薄荷渐变）、
    GRADIENT_TWILIGHT（暮光渐变）

渐变工具为纯函数（无 I/O 副作用），使用 @lru_cache 避免重复计算热点渐变，
可直接在单元测试中独立验证。
"""
from __future__ import annotations

from functools import lru_cache

from ..core.constants import (
    GRAY, WHITE, CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA,
    BOLD, DIM, RESET, ITALIC, UNDERLINE,
    BRIGHT_CYAN, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE,
    BRIGHT_MAGENTA, BRIGHT_RED, BRIGHT_WHITE, BRIGHT_BLACK,
    BG_BLUE, BG_CYAN, BG_GREEN, BG_YELLOW,
    ORANGE, TEAL, PINK, LAVENDER,
    SOFT_GREEN, SOFT_BLUE, SOFT_YELLOW, DARK_GRAY,
    # ── 256 色常量（从 core.constants 提升至外观层） ──
    GRAY_256, WHITE_256, CYAN_256, GREEN_256, YELLOW_256,
    RED_256, BLUE_256, MAGENTA_256,
    BRIGHT_CYAN_256, BRIGHT_GREEN_256, BRIGHT_YELLOW_256,
    BRIGHT_BLUE_256, BRIGHT_MAGENTA_256, BRIGHT_RED_256,
    BRIGHT_WHITE_256, BRIGHT_BLACK_256,
    BG_BLUE_256, BG_CYAN_256, BG_GREEN_256, BG_YELLOW_256,
    ORANGE_256, TEAL_256, PINK_256, LAVENDER_256,
    SOFT_GREEN_256, SOFT_BLUE_256, SOFT_YELLOW_256, DARK_GRAY_256,
    DIM_256,
)
from .ansi import (
    strip_ansi, visual_width, truncate_ansi_visual,
    skip_ansi_sgr, truncate_ansi_sgr,
)
from .theme import THEME
from .console import get_console as _get_console

# 向后兼容：colors.console 可正常访问
console = _get_console()

__all__: list[str] = [
    "THEME", "console",
    # ── 8-bit 颜色常量 ──
    "GRAY", "WHITE", "CYAN", "GREEN", "YELLOW", "RED", "BLUE", "MAGENTA",
    "BOLD", "DIM", "RESET", "ITALIC", "UNDERLINE",
    "BRIGHT_CYAN", "BRIGHT_GREEN", "BRIGHT_YELLOW", "BRIGHT_BLUE",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_BLACK",
    "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_YELLOW",
    "ORANGE", "TEAL", "PINK", "LAVENDER",
    "SOFT_GREEN", "SOFT_BLUE", "SOFT_YELLOW", "DARK_GRAY",
    # ── 256 色常量 ──
    "GRAY_256", "WHITE_256", "CYAN_256", "GREEN_256", "YELLOW_256",
    "RED_256", "BLUE_256", "MAGENTA_256",
    "BRIGHT_CYAN_256", "BRIGHT_GREEN_256", "BRIGHT_YELLOW_256",
    "BRIGHT_BLUE_256", "BRIGHT_MAGENTA_256", "BRIGHT_RED_256",
    "BRIGHT_WHITE_256", "BRIGHT_BLACK_256",
    "BG_BLUE_256", "BG_CYAN_256", "BG_GREEN_256", "BG_YELLOW_256",
    "ORANGE_256", "TEAL_256", "PINK_256", "LAVENDER_256",
    "SOFT_GREEN_256", "SOFT_BLUE_256", "SOFT_YELLOW_256", "DARK_GRAY_256",
    # ── 渐变工具函数 ──
    "hex_to_256", "gradient_step", "gradient_range",
    # ── 预定义渐变调色板 ──
    "GRADIENT_SUNSET", "GRADIENT_OCEAN", "GRADIENT_FOREST",
    "GRADIENT_FIRE", "GRADIENT_NEON",
    "GRADIENT_AURORA", "GRADIENT_CORAL", "GRADIENT_MINT", "GRADIENT_TWILIGHT",
    "GRADIENT_SUNRISE", "GRADIENT_PURPLE", "GRADIENT_ICE",
    "GRADIENT_SOFT", "GRADIENT_EMERALD",
]

# ════════════════════════════════════════════════════════
# 渐变基础设施
# ════════════════════════════════════════════════════════

def _build_xterm_palette() -> list[tuple[int, int, int]]:
    """构建 xterm-256 调色板的 RGB 颜色查找表（索引 0-255）。"""
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


def hex_to_256(hex_color: str) -> int:
    """将十六进制颜色转换为最接近的 xterm-256 色号（0-255）。

    参数:
        hex_color: 十六进制颜色字符串，支持 ``#FF8800`` 或 ``ff8800`` 格式。

    返回:
        最接近的 xterm-256 色号。异常输入返回 15（白色）兜底。
    """
    try:
        cleaned = hex_color.lstrip("#")
        r, g, b = (int(cleaned[i : i + 2], 16) for i in (0, 2, 4))

        best_idx: int = 0
        best_dist: int = 2**31 - 1  # int max

        for i, (cr, cg, cb) in enumerate(_XTERM_PALETTE):
            dr, dg, db = r - cr, g - cg, b - cb
            dist: int = dr * dr + dg * dg + db * db
            if dist < best_dist:
                best_dist = dist
                best_idx = i
                if best_dist == 0:  # 精确匹配，提前退出
                    break

        return best_idx
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


# ── 预定义渐变调色板常量 ─────────────────────────────
# 使用 gradient_range 在模块加载时计算，类型 list[int]

GRADIENT_SUNSET: list[int] = gradient_range(196, 224, 8)
"""日落渐变：红色(196)→琥珀色(224)，8 阶。"""

GRADIENT_OCEAN: list[int] = gradient_range(26, 87, 6)
"""海洋渐变：深蓝(26)→青色(87)，6 阶。"""

GRADIENT_FOREST: list[int] = gradient_range(22, 47, 6)
"""森林渐变：深绿(22)→亮绿(47)，6 阶。"""

GRADIENT_FIRE: list[int] = gradient_range(52, 220, 9)
"""火焰渐变：深红(52)→亮黄(220)，9 阶。"""

GRADIENT_NEON: list[int] = [
    57, 93, 129, 165, 171, 177, 183, 189, 195, 87
]
"""霓虹渐变：紫(57)→粉→青(87)，10 阶（非均匀插值，手工精选）。"""

GRADIENT_AURORA: list[int] = gradient_range(57, 47, 8)
"""极光渐变：紫蓝(57)→亮绿(47)，8 阶。"""

GRADIENT_CORAL: list[int] = gradient_range(203, 224, 6)
"""珊瑚渐变：珊瑚红(203)→米白(224)，6 阶。"""

GRADIENT_MINT: list[int] = gradient_range(29, 114, 6)
"""薄荷渐变：深青绿(29)→柔和绿(114)，6 阶。"""

GRADIENT_TWILIGHT: list[int] = gradient_range(53, 195, 8)
"""暮光渐变：深紫(53)→亮青(195)，8 阶。"""

# ── 新增调色板（第四阶段美化） ───────────────────────────
GRADIENT_SUNRISE: list[int] = gradient_range(208, 220, 8)
"""日出渐变：暖橙(208)→亮黄(220)，8 阶。"""

GRADIENT_PURPLE: list[int] = gradient_range(55, 177, 8)
"""紫渐变：深紫(55)→亮紫(177)，8 阶。"""

GRADIENT_ICE: list[int] = gradient_range(24, 87, 8)
"""冰蓝渐变：深蓝(24)→亮青(87)，8 阶。"""

GRADIENT_SOFT: list[int] = gradient_range(175, 218, 8)
"""柔和粉渐变：粉红(175)→亮粉(218)，8 阶。"""

GRADIENT_EMERALD: list[int] = gradient_range(22, 47, 8)
"""翡翠渐变：深绿(22)→亮绿(47)，8 阶。"""
