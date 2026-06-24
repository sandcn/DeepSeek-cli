"""Transform 组件 — ANSI 感知的逐行文本变换。

对子组件渲染输出的每一行调用 transform(line, index) 函数进行变换。
提供 ANSI 感知的辅助函数，用于安全处理含 ANSI 转义序列的文本行。

使用示例::

    from src.chat_ui.react_ink._transform import Transform, _strip_ansi_prefix, _preserve_ansi_prefix

    # 大小写变换
    Transform(transform=lambda line, i: line.upper(), children=text_component)

    # 悬挂缩进（首行不缩进，其余缩进4格）
    Transform(
        transform=lambda line, i: line if i == 0 else '    ' + line,
        children=text_component,
    )

    # ANSI 感知变换：仅对内容文本操作，保留前导 ANSI 样式
    Transform(
        transform=lambda line, i: (
            _preserve_ansi_prefix(line)[0] + _strip_ansi_prefix(line).upper()
        ),
        children=styled_component,
    )
"""

from __future__ import annotations

import re
from typing import Callable

from .._components import TuiComponent
from .._styled import StyledText

# ── ANSI 序列匹配 ─────────────────────────────────────

# 匹配单个 ANSI SGR 转义序列（\033[...m）
_ANSI_SGR_RE = re.compile(r'\033\[[\d;]*m')


# ── ANSI 辅助函数 ──────────────────────────────────────

def _strip_ansi_prefix(text: str) -> str:
    """去除行首连续的 ANSI 转义序列前缀，返回后续纯文本。

    仅去除行首的 ANSI SGR 序列（如颜色、样式），
    保留行中和行尾的 ANSI 序列不动。
    用于在 transform 函数中判断去除样式后的实际文本内容。

    Args:
        text: 可能以 ANSI 序列开头的文本行。

    Returns:
        去除所有前导 ANSI 序列后的文本。

    Examples:
        >>> _strip_ansi_prefix('\\033[31mhello')
        'hello'
        >>> _strip_ansi_prefix('\\033[1m\\033[31mworld')
        'world'
        >>> _strip_ansi_prefix('plain text')
        'plain text'
        >>> _strip_ansi_prefix('\\033[31mred\\033[0m text')
        'red\\033[0m text'
    """
    pos = 0
    while pos < len(text):
        m = _ANSI_SGR_RE.match(text, pos)
        if m:
            pos = m.end()
        else:
            break
    return text[pos:]


def _preserve_ansi_prefix(text: str) -> tuple[str, str]:
    """分离 ANSI 前缀和内容文本。

    提取行首所有连续的 ANSI 转义序列作为前缀，
    其余部分（从第一个非 ANSI 字符开始）作为内容返回。

    用于在 transform 函数中：先分离前缀，对内容做变换，
    然后重新拼接前缀以保留原始样式。

    Args:
        text: 可能以 ANSI 序列开头的文本行。

    Returns:
        (ansi_prefix, content) 二元组。
        - ansi_prefix: 行首所有连续 ANSI 序列的拼接字符串，无前导 ANSI 时为空字符串。
        - content: 去除前导 ANSI 后的剩余文本（可能仍含行中/行尾的 ANSI 序列）。

    Examples:
        >>> _preserve_ansi_prefix('\\033[31mhello')
        ('\\033[31m', 'hello')
        >>> _preserve_ansi_prefix('\\033[1m\\033[31mworld')
        ('\\033[1m\\033[31m', 'world')
        >>> _preserve_ansi_prefix('plain text')
        ('', 'plain text')
        >>> _preserve_ansi_prefix('\\033[31mred\\033[0m text')
        ('\\033[31m', 'red\\033[0m text')
    """
    pos = 0
    while pos < len(text):
        m = _ANSI_SGR_RE.match(text, pos)
        if m:
            pos = m.end()
        else:
            break
    return text[:pos], text[pos:]


# ── Transform 组件 ────────────────────────────────────

class Transform(TuiComponent):
    """逐行文本变换组件。

    继承自 TuiComponent，渲染子组件后对输出的每一行调用 transform 函数，
    用换行符重新拼接后返回。

    transform 函数签名为 (line: str, index: int) -> str：
    - line: 当前行文本（可能包含 ANSI 转义序列）
    - index: 行号（从 0 开始）

    可通过 _strip_ansi_prefix / _preserve_ansi_prefix 辅助函数
    在 transform 中安全处理含 ANSI 序列的行。

    Attributes:
        transform: 逐行变换可调用对象。
        children: 子组件列表（继承自 TuiComponent）。
    """

    transform: Callable[[str, int], str]

    def __init__(
        self,
        transform: Callable[[str, int], str],
        children: TuiComponent | list[TuiComponent] | None = None,
    ):
        """初始化 Transform 组件。

        Args:
            transform: 逐行变换函数。
                       接收当前行文本（可能含 ANSI 序列）和行号（从 0 开始），
                       返回变换后的行文本。
            children: 子组件，可为单个 TuiComponent 或列表。
                      为 None 时表示无子组件。
        """
        if children is None:
            _children: list[TuiComponent] | None = None
        elif isinstance(children, TuiComponent):
            _children = [children]
        else:
            _children = list(children)
        super().__init__(children=_children)
        self.transform = transform

    def render(self) -> str | StyledText:
        """渲染子组件并逐行变换。

        执行流程:
            1. 调用 render_children() 渲染所有子组件获取完整输出
            2. 对 StyledText 输出取其 ANSI 渲染字符串（保留样式序列）
            3. 按 \\n 分割为行列表
            4. 对每一行调用 self.transform(line, line_index)
            5. 用 \\n 重新拼接所有变换后的行

        Returns:
            变换后的文本字符串（str 类型）。
        """
        # 1. 渲染子组件
        raw_output = self.render_children()

        # 2. 获取字符串形式的输出
        if isinstance(raw_output, StyledText):
            # StyledText.__str__() 返回含 ANSI 序列的完整字符串
            text: str = str(raw_output)
        elif isinstance(raw_output, str):
            text = raw_output
        else:
            text = str(raw_output)

        # 3. 空输出直接返回
        if not text:
            return ""

        # 4. 按行分割并逐行变换
        lines = text.split("\n")
        transformed = [self.transform(line, i) for i, line in enumerate(lines)]

        # 5. 重新拼接
        return "\n".join(transformed)
