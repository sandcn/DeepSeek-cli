# DEPRECATED: 此文件仅为向后兼容保留，请直接导入 src.ui.tui._terminal
"""窄屏自适应模块 — 兼容包装层

实现在 `src/ui/tui/_terminal.py` 中。
此文件保持向后兼容，所有函数从 tui 子模块重新导出。

新代码应直接导入：
    from src.ui.tui._terminal import is_narrow, ...
"""

from src.chat_ui.tui._terminal import (
    is_narrow,
    narrow_truncate,
    narrow_indent,
    narrow_sep_width,
    get_terminal_width,
)
