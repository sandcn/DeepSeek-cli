"""Code 组件 — React Ink 风格代码块组件。

提供 <Code> 组件，用于在终端中渲染带单线边框的代码块，
支持行号显示和语言标签。

使用示例:
    code = Code(code="print('hello')\nprint('world')", language="python")
    print(code.render())

    code = Code(code="line1\nline2", numbered_lines=False)
    print(code.render())
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from ..components.base import TuiComponent

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


def _visual_width(text: str) -> int:
    """计算文本的终端视觉宽度（CJK 字符计为 2 列，其余 1 列）。

    与 box.py / text.py 中的同名函数保持一致。
    """
    w = 0
    for ch in text:
        w += 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
    return w


class Code(TuiComponent):
    """React Ink Code 组件 — 终端代码块渲染。

    渲染带单线边框的代码块，手动拼接边框（不依赖 Box 类），
    支持语言标签和行号显示。

    Props:
        code: str — 代码文本，多行以 \\n 分隔。
        language: str | None — 语言标识（如 "python"、"bash"），
                 非 None 时显示在顶部边框。
        numbered_lines: bool — 是否显示行号（默认 True）。
    """

    def __init__(
        self,
        code: str = "",
        language: str | None = None,
        numbered_lines: bool = True,
        children: list[TuiComponent] | None = None,
    ) -> None:
        """初始化 Code 组件。

        Args:
            code: 代码文本，多行以 \\n 分隔。
            language: 语言标识，非 None 时嵌入顶部边框。
            numbered_lines: 是否显示行号。
            children: 子组件列表（保留以兼容 TuiComponent 接口）。
        """
        super().__init__(children=children)
        self._code = code
        self._language = language
        self._numbered_lines = numbered_lines

    @property
    def key(self) -> str:
        """稳定标识符 — 用于 VNode Diff 的 key 匹配。"""
        return "code"

    def update(self, props: dict) -> bool:
        """接收新 props，对比变化决定是否重渲染。

        Args:
            props: 可能包含 'code'、'language'、'numbered_lines' 键的字典。

        Returns:
            True 如果任何属性发生变化。
        """
        changed = False
        if "code" in props and props["code"] != self._code:
            self._code = props["code"]
            changed = True
        if "language" in props and props["language"] != self._language:
            self._language = props["language"]
            changed = True
        if ("numbered_lines" in props
                and props["numbered_lines"] != self._numbered_lines):
            self._numbered_lines = props["numbered_lines"]
            changed = True
        return changed

    def render(self) -> str:
        """渲染代码块。

        流程:
            1. 将 code 按 \\n 分割为行
            2. 计算行号宽度（基于总行数位数）
            3. 构建每行的显示文本（前缀 + 代码行）
            4. 计算 content_width = 各行最大视觉宽度
            5. 拼接顶部边框（可选语言标签）+ 内容行 + 底部边框

        Returns:
            带单线边框的多行字符串。
        """
        code = self._code
        if not code:
            code_lines = [""]
        else:
            code_lines = code.split('\n')

        total_lines = len(code_lines)
        num_width = max(1, len(str(total_lines)))

        # ── 构建每行的显示文本（前缀 + 代码） ──────────
        display_lines: list[str] = []
        for i, line in enumerate(code_lines):
            prefix = self._make_prefix(i + 1, num_width)
            display_lines.append(prefix + line)

        # ── 计算内容宽度 ──────────────────────────────
        content_width = max(
            (_visual_width(dl) for dl in display_lines), default=0
        )
        # 若有语言标签，确保内容宽度足够容纳标签行
        if self._language is not None:
            min_for_label = _visual_width(self._language) + 3
            content_width = max(content_width, min_for_label)

        # ── 构建输出 ──────────────────────────────────
        result_lines: list[str] = []

        # 顶部边框
        top_line = self._build_top_border(content_width)
        result_lines.append(top_line)

        # 内容行
        for dl in display_lines:
            pad_w = content_width - _visual_width(dl)
            padding = " " * max(0, pad_w)
            result_lines.append(f"│{dl}{padding}│")

        # 底部边框
        result_lines.append("└" + "─" * content_width + "┘")

        return "\n".join(result_lines)

    def _make_prefix(self, line_num: int, num_width: int) -> str:
        """构建行前缀。

        Args:
            line_num: 当前行号（1-based）。
            num_width: 行号字段宽度。

        Returns:
            若 numbered_lines=True: "{N:>{w}} │ "，
            否则: "  │ "。
        """
        if self._numbered_lines:
            return f"{line_num:>{num_width}} │ "
        else:
            return "  │ "

    def _build_top_border(self, content_width: int) -> str:
        """构建顶部边框行，可选嵌入语言标签。

        Args:
            content_width: 内容区视觉宽度（不含左右边框）。

        Returns:
            带 ┌─...─┐ 的顶部边框行。若有语言标签，
            格式为 "┌─ {language} ──...──┐"。
        """
        if self._language is not None:
            lang_vis = _visual_width(self._language)
            # "┌─ {lang} " 的视觉宽度 = 4 + lang_vis
            # 总宽度应为 content_width + 2 (= "│" + content + "│")
            # 所以 dashes = (content_width + 2) - (4 + lang_vis) - 1 = content_width - lang_vis - 3
            dashes = max(0, content_width - lang_vis - 3)
            return f"┌─ {self._language} " + "─" * dashes + "┐"
        else:
            return "┌" + "─" * content_width + "┐"

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。"""
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="code",
            key=self.key,
            props={
                "code": self._code,
                "language": self._language,
                "numbered_lines": self._numbered_lines,
                "text": str(rendered) if rendered else "",
            },
        )
