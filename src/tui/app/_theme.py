"""app/_theme — app 组件共享样式与时间基 glow（re-export 存根 + sep_line）。

实现（样式常量/调色板/呼吸色/分隔线样式）已下沉至 ``src/tui/core/_theme.py``
（2026-08-05 公共工具归位 core 层）；本模块保持 re-export 存根——旧导入
路径（``from src.tui.app._theme import ...``）与测试 patch 路径
（``patch("src.tui.app._theme.time.monotonic")`` 等）兼容。

唯一保留的本地实现：``sep_line``——构建 ink ``Line`` 分隔线（依赖
``ink.output``，属 UI 组件层，不下沉 core）。

依赖约束：仅依赖 src/tui/core（style/_theme）与标准库（math/time），
不依赖 _animator、不依赖任何 app 组件（sep_line 的 ink 依赖为函数内惰性
导入）。
"""

from __future__ import annotations

#: re-export time 模块：兼容测试 ``patch("src.tui.app._theme.time.monotonic")``
#: （patch 解析本模块命名空间的 ``time`` 属性；替换的是全局 time 模块对象，
#: core/_theme 实现随之生效）。
import time as time  # noqa: F401
from src.tui.core._theme import (
    _S_ACCENT,
    _S_ACCENT_BOLD,
    _S_DIM,
    _S_SEP,
    _S_TIME,
    _S_USER_ICON,
    _S_USER_TEXT,
    _S_NOTICE,
    _S_TEXT,
    time_glow,
    sep_style,
    Palette,
    ThemeRegistry,
    resolve_theme,
    get_active_palette,
    _invalidate_palette_cache,
    _PALETTE_SLOTS,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.tui.ink.output import Line
# ★ 模块级状态/缓存 re-export：测试直接读写 ``theme._active_palette_cache``
#   （test_app_theme.py TTL 边界用例）、``theme._glow_bucket``（lru 缓存
#   清除/命中计数）——re-export 同一对象（函数对象/当前值），旧访问路径
#   兼容。注意：``_active_palette_cache`` 为**可变绑定**，core/_theme 内
#   重新赋值不会同步回本存根命名空间——测试若需观测最新缓存应经
#   ``core._theme`` 访问（test_app_theme.py 已迁移）。
from src.tui.core._theme import _glow_bucket, _active_palette_cache, _sep_style_active


def sep_line(width: int, content: "Line | None" = None,
             active: bool = False) -> "Line":
    """构建分隔线行（通用组件，方向5 收敛）：左侧 ``┅`` 填充 + 右侧内容。

    Claude TUI parity 分隔线——input_area 上/下分隔线（CPU/MEM、时间戳）与
    status_bar 分隔线共用同一构建语义：
      - ``content is None``：纯填充行（status_bar 满宽分隔线，行宽 = width）；
      - ``content`` 非 None：左侧填充 + 右侧内容（input_area 分隔线，行宽
        恒 = width——BUG-72 行宽不变量：按内容实际宽填充而非预算）。
    样式统一经 ``sep_style(active)``（活跃呼吸 / 空闲静态）。

    Args:
        width: 行总宽（终端列宽）。
        content: 右侧内容行（可选；None 时纯填充）。已按预算截断。
        active: 是否活跃（流式/工具运行等）。

    Returns:
        分隔线行（Line）。
    """
    # 惰性导入避免模块级循环依赖（ink.output 不依赖 _theme）
    from src.tui.ink.output import Line
    style = sep_style(active)
    if content is None:
        return Line.of("\u2501" * max(1, width), style)
    # ★ 健壮性（通用组件防御）：content 可能未按预算截断（调用方直接传超宽
    #   行时）——``sep_len = max(0, width - content.width)`` 为 0 → 行宽 =
    #   content.width > width，破坏行级 diff 行宽不变量。防御：content 超宽时
    #   截断至 width（复用 ink.helpers.truncate_line，不拆 CJK）再填充。
    #   正常路径（调用方已按预算截断）行为不变（truncate_line 宽度不足时
    #   原样返回）。
    if content.width > width and width > 0:
        from src.tui.ink.helpers import truncate_line
        content = truncate_line(content, width)
    sep_len = max(0, width - content.width)
    line = Line.of("\u2501" * sep_len, style)
    for run in content.runs:
        line.append_run(run)
    return line


__all__ = [
    "_S_ACCENT",
    "_S_ACCENT_BOLD",
    "_S_DIM",
    "_S_SEP",
    "_S_TIME",
    "_S_USER_ICON",
    "_S_USER_TEXT",
    "_S_NOTICE",
    "_S_TEXT",
    "time_glow",
    "sep_style",
    "sep_line",
    "Palette",
    "ThemeRegistry",
    "resolve_theme",
    "get_active_palette",
    "_invalidate_palette_cache",
    "_PALETTE_SLOTS",
]
