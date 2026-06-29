"""输入行 — InputLine。

> 提示符 + 用户输入文本 + 光标。
由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
"""

from __future__ import annotations

from ._base import TuiComponent


class InputLine(TuiComponent):
    """输入行 — > 提示符 + 用户输入文本 + 光标。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.text: str = ""
        self.cursor_pos: int = 0

    def render(self) -> str:
        return f"> {self.text}"
