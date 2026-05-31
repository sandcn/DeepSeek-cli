"""上屏历史管理器 — 已屏蔽（No-op 版本）。

原职责：记录终端渲染历史并在 resize 后重放。
已禁用：所有记录和重放方法均为空实现，不占用内存、不执行任何 I/O。

保留类接口兼容性，便于后续重新启用。
依赖 _const（Style 常量）已移除，所有状态管理和 replay 逻辑已清空。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter


class ScreenHistoryManager:
    """上屏历史记录管理器（已屏蔽）。

    所有记录和重放方法均为空实现（No-op）。
    保留接口兼容性，ContentRenderer 可直接组合使用。

    使用方式（由 ContentRenderer 组合）：
      self._shm = ScreenHistoryManager(...)
      self._shm.append_reasoning(text)    # → no-op
      self._shm.append_content(text)      # → no-op
      self._shm.flush_reasoning()         # → no-op
      self._shm.flush_content()           # → no-op
      self._shm.record(kind, *args)       # → no-op
      self._shm.replay(tool_adapter, ...) # → no-op
    """

    def __init__(
        self,
        on_display_messages: Callable[[list, int], None] | None = None,
    ):
        """初始化上屏历史管理器（No-op）。

        Args:
            on_display_messages: 保留参数但不使用（为接口兼容保留）。
        """
        self._on_display_messages = on_display_messages

    # ── 公开属性 ────────────────────────────────────

    @property
    def screen_history(self) -> list[tuple]:
        """上屏历史记录列表（始终为空）。"""
        return []

    @property
    def on_display_messages(self) -> Callable[..., None] | None:
        """display_messages 回调。"""
        return self._on_display_messages

    # ── 累积与刷新（均 No-op） ──────────────────────

    def append_reasoning(self, text: str) -> None:
        """追加推理文本到累积缓冲区（已屏蔽：No-op）。"""

    def append_content(self, text: str) -> None:
        """追加内容文本到累积缓冲区（已屏蔽：No-op）。"""

    def flush_reasoning(self) -> None:
        """将累积的推理文本保存为单条历史记录（已屏蔽：No-op）。"""

    def flush_content(self) -> None:
        """将累积的内容文本保存为单条历史记录（已屏蔽：No-op）。"""

    def flush_all(self) -> None:
        """刷新所有累积缓冲区（已屏蔽：No-op）。"""

    def record(self, kind: str, *args) -> None:
        """记录非累积类型到上屏历史（已屏蔽：No-op）。"""

    def clear(self) -> None:
        """清空上屏历史记录和累积缓冲区（已屏蔽：No-op）。"""

    # ── 重放（已屏蔽） ──────────────────────────────

    def replay(self, tool_adapter: "OutputAdapter", bottom_bar) -> None:
        """终端尺寸变化后重放上屏历史内容（已屏蔽：No-op）。"""
