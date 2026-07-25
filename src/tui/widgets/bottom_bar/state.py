"""_BottomBarState — 底部栏状态管理数据类。

从 bar.py 提取的 30+ 实例属性，按职责分组为 dataclass 统一管理，
消除 bar.py 中分散在 __init__ 各处的属性初始化。

使用方法：
    state = _BottomBarState.create()
    bar._state = state
    # 访问属性
    bar._state._last_text
    # 修改属性
    bar._state._last_text = "new"
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from .theme import _BOTTOM_MIN_LINES, _MIN_INPUT_ROWS
from ...animation.animator import AnimatorContext


@dataclasses.dataclass
class _BottomBarState:
    """底部栏状态数据类，包含所有状态属性。

    按职责分组（7 组）：
      1. 活跃状态          — _active, _last_status
      2. StatusMixin 字段  — _status_active, _model_name, _tool_count, _tool_fail_count, _tool_total
      3. 文本状态          — _last_text, _input_cursor_pos, _last_rendered_text, _last_cursor_pos
      4. 布局相关          — _last_bottom_lines, _last_scroll_end, _last_height, _last_sync_height
         + 子Agent面板    — _subagent_lines, _last_subagent_lines
         + 缓存           — _cached_wrapped_for/_width/_lines, _cached_input_rows
      5. 系统监控          — _cached_cpu_percent, _cached_mem_percent, _last_system_stats_time,
                             _SYSTEM_STATS_INTERVAL
      6. 终端尺寸          — _cached_height, _cached_width, _last_dimension_refresh, _DIMENSION_TTL
         + resize保护     — _needs_full_repaint
      7. 阶段状态          — _main_phase, _main_phase_start, _tool_phase_start
         + 动画           — _animator

    设计原则：
      - 纯状态容器，不包含业务逻辑（业务逻辑保留在 _BottomBar 中）
      - 所有字段有默认值，通过 create() classmethod 创建默认实例
      - 字段命名保持与 bar.py 一致的 _ 前缀，便于迁移
      - 不引用 bar.py 中定义的类型（如 _CompletionPopup）
    """

    # ── 组 1: 活跃状态 ──────────────────────────────────
    _active: bool = False
    _last_status: str = ""

    # ── 组 2: StatusMixin 字段 ──────────────────────────
    _status_active: bool = False
    _model_name: str = ""
    _tool_count: int = 0
    _tool_fail_count: int = 0
    _tool_total: int = 0

    # ── 组 3: 文本状态 ──────────────────────────────────
    _last_text: str = ""
    _input_cursor_pos: int = -1
    _last_rendered_text: str = ""
    _last_cursor_pos: int = -1

    # ── 组 4: 布局相关 ──────────────────────────────────
    _last_bottom_lines: int = _BOTTOM_MIN_LINES
    _last_scroll_end: int = 0
    _last_height: int = 0  # 哨兵值，首次 force_redraw() 必然触发全量重绘（终端高度始终 ≥1）
    _last_sync_height: int = 0

    # ── 子Agent面板 ──
    _subagent_lines: list[str] = dataclasses.field(default_factory=list)
    _last_subagent_lines: list[str] = dataclasses.field(default_factory=list)

    # ── 缓存（拆行/输入行数） ──
    _cached_wrapped_for: str = ""
    _cached_wrapped_width: int = 0
    _cached_wrapped_lines: list[str] | None = None
    _cached_input_rows: int = _MIN_INPUT_ROWS

    # ── 组 5: 系统监控 ──────────────────────────────────
    _cached_cpu_percent: float = 0.0
    _cached_mem_percent: float = 0.0
    _last_system_stats_time: float = 0.0
    _SYSTEM_STATS_INTERVAL: float = 1.0

    # ── 组 6: 终端尺寸 ──────────────────────────────────
    _cached_height: int = 0
    _cached_width: int = 0
    _last_dimension_refresh: float = 0.0
    _DIMENSION_TTL: float = 0.1

    # ── resize 保护 ──
    _needs_full_repaint: bool = False

    # ── 组 7: 阶段状态 ──────────────────────────────────
    _main_phase: str = ""
    _main_phase_start: float = 0.0
    _tool_phase_start: float = 0.0

    # ── 动画（AnimatorContext 单例，通过 get_default() 延迟获取） ──
    _animator: AnimatorContext = dataclasses.field(
        default_factory=AnimatorContext.get_default
    )

    @classmethod
    def create(cls) -> _BottomBarState:
        """创建默认状态的 _BottomBarState 实例。

        所有字段使用 dataclass 定义的默认值，
        与 bar.py 中 __init__ 的初始值语义一致。
        """
        return cls()
