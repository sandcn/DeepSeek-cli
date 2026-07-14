"""错误提示块 — ErrorBlock。

红色 ! 前缀，用于显示系统错误信息。

动效（2026-07-12）：
  - 宽屏："!" 使用 error_pulse 脉动（红↔亮红），消息文本使用红色 glow 呼吸
  - 窄屏：降级为静态 _STYLE_ERROR_GRADIENT

动效（2026-07-12 美化）：
  - 宽屏：左边缘添加呼吸边框字符 │，使用 THEME['border_breath'] 色号
  - 窄屏：降级为无边框
"""

from __future__ import annotations

import re

from rich.text import Text

from ..consumer.const import _STYLE_ERROR_GRADIENT, _MAX_ERROR_LENGTH
from ..consumer.utils import _truncate_msg
from ...ui.theme import THEME
from ..core.animator import AnimatorContext
from ..terminal.terminal import is_narrow
from ..core.text_utils import build_glow_ansi, build_left_border_ansi, build_warning_pulse_ansi
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
        # 宽屏：! 前缀脉动 + 消息文本红色 glow 呼吸
        frame = AnimatorContext.get_default().frame
        pulse_ansi = build_warning_pulse_ansi(frame, "error")
        glow_ansi = build_glow_ansi(frame, 196, 12)
        border_match = re.search(r"38;5;(\d+)", THEME['border_breath'])
        border_base = int(border_match.group(1)) if border_match else 23
        edge_ansi = build_left_border_ansi(frame, border_base, 24)
        ansi_str = f"\n  {edge_ansi} {pulse_ansi}! \033[0m{glow_ansi}{self.message}\033[0m"
        return Text.from_ansi(ansi_str)
