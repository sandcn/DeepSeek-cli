"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。

动效（2026-07-12）：
  - 宽屏："!" 使用 error_pulse 脉动（红↔亮红），消息文本使用红色 glow 呼吸
  - 窄屏：降级为静态 _STYLE_ERROR_GRADIENT
"""

from __future__ import annotations

from rich.text import Text

from ..const import _STYLE_ERROR_GRADIENT, _MAX_ERROR_LENGTH
from ..utils import _truncate_msg
from ...ui.tui._animator import AnimatorContext
from ...ui.tui._terminal import is_narrow
from ...ui.tui._text_utils import build_glow_ansi, build_warning_pulse_ansi
from ._base import TuiComponent


class ErrorBlock(TuiComponent):
    """错误提示块 — 红色 ! 前缀。"""
    def __init__(self, message: str):
        self.message = _truncate_msg(message, _MAX_ERROR_LENGTH)

    def render(self) -> Text:
        if is_narrow():
            return Text.assemble(
                ("\n  ! ", _STYLE_ERROR_GRADIENT),
                (self.message, _STYLE_ERROR_GRADIENT),
            )
        # 宽屏：! 前缀脉动 + 消息文本红色呼吸辉光
        frame = AnimatorContext.get_default().frame
        pulse_ansi = build_warning_pulse_ansi(frame, "error")
        glow_ansi = build_glow_ansi(frame, 196, 12)
        ansi_str = f"\n  {pulse_ansi}! \033[0m{glow_ansi}{self.message}\033[0m"
        return Text.from_ansi(ansi_str)
