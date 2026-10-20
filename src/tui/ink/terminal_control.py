"""TerminalController — 终端模式/能力管理（P2-2 会话拆分）。

从 ``InkSession`` 抽出的终端职责：

  - 备用屏 / 鼠标上报 / 括号粘贴（``TerminalModeManager``）；
  - raw 模式（``RawModeController``，termios 不可用时降级）；
  - 能力查询（``is_raw_mode_supported`` / ``mouse_enabled`` /
    ``bracketed_paste_active``）与一次性序列写入。

``InkSession`` 保留同名公开方法作为门面委托，测试/外部调用面不变。
"""

from __future__ import annotations

import logging
from typing import Any

from .terminal import RawModeController, TerminalModeManager

_logger = logging.getLogger(__name__)


class TerminalController:
    """终端模式与 raw 模式控制器（会话级）。"""

    def __init__(self, stream: Any, input_fd: int | None = None) -> None:
        self.modes = TerminalModeManager(stream)
        self.raw_mode: RawModeController | None = None
        self.requested: dict[str, bool] = {}
        self.mouse_enabled = False
        self.bracketed_paste_active = False
        self.raw_mode_supported = False
        self.interactive = False
        self._input_fd = input_fd

    # ── 终端模式 ─────────────────────────────────────

    def set_modes(
        self,
        *,
        alternate_screen: bool = False,
        mouse: bool = False,
        bracketed_paste: bool = False,
        interactive: bool = False,
    ) -> None:
        self.interactive = bool(interactive)
        self.requested = {
            "alternateScreen": bool(alternate_screen),
            "mouse": bool(mouse),
            "bracketedPaste": bool(bracketed_paste),
        }
        self.apply()

    def apply(self) -> None:
        for name, wanted in self.requested.items():
            if wanted:
                self.modes.enable(name)
        self.mouse_enabled = bool(self.requested.get("mouse"))
        self.bracketed_paste_active = bool(
            self.requested.get("bracketedPaste")
        ) and self.modes.is_enabled("bracketedPaste")

    def exit_modes(self) -> None:
        self.modes.disable_all()
        self.bracketed_paste_active = False

    def write_raw(self, data: str) -> None:
        self.modes.write_raw(data)

    # ── raw 模式 ─────────────────────────────────────

    def set_interactive(self, enabled: bool, input_fd: int | None = None) -> None:
        self.interactive = bool(enabled)
        if input_fd is not None:
            self._input_fd = input_fd
        if enabled and self._input_fd is not None:
            self.raw_mode = RawModeController(self._input_fd)
        else:
            self.raw_mode = None
        self.raw_mode_supported = bool(
            self.raw_mode is not None and self.raw_mode.supported
        )

    def enable_raw(self) -> None:
        if self.raw_mode is not None:
            self.raw_mode.enable()

    def disable_raw(self) -> None:
        if self.raw_mode is not None:
            self.raw_mode.disable()

    def set_raw(self, enabled: bool) -> bool:
        if self.raw_mode is None or not self.raw_mode.supported:
            return False
        if enabled:
            self.raw_mode.enable()
        else:
            self.raw_mode.disable()
        return True


__all__ = ["TerminalController"]
