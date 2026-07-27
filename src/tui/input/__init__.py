"""统一 TUI 输入管理。

本包提供 InputBuffer（输入文本缓冲）、InputParser（ANSI CSI/SS3 解析）、
CursorPositioner（光标定位计算）以及 Input 门面类，整合当前分散在
EscapeMonitor / _BottomBar / InteractiveLoop / _run_selection_raw 中的四类重复逻辑。

模块：
    _buffer:    InputBuffer — 输入文本缓冲 + 历史导航 + 回显回调
    _parser:    InputParser + KeyEvent — ANSI CSI/SS3 序列解析
    _cursor:    CursorPositioner — 光标定位计算
    _input:     Input — 门面类，组合上述组件
"""

from ._buffer import InputBuffer
from ._parser import InputParser, KeyEvent
from ._cursor import CursorPositioner
from ._input import Input

__all__ = ["InputBuffer", "InputParser", "KeyEvent", "CursorPositioner", "Input"]
