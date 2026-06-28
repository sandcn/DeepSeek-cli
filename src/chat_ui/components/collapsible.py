"""Collapsible — 轻量级折叠容器组件。

TuiComponent 子类，提供无边框的折叠/展开切换。
使用简单的文本前缀 ▶/▼ 而非 Box 边框，适合非边框场景中的折叠内容
（如树节点、日志块）。

与 Box 折叠模式互补：Box 折叠模式仍渲染上边框行，Collapsible 仅使用
前缀符号，无任何边框开销。

使用示例:
    coll = Collapsible(title="详情", collapsed=True)
    coll.add_child(Text("内容行1"))
    coll.add_child(Text("内容行2"))
    print(coll.render())
    # ▶ 详情

    coll.update({"collapsed": False})
    print(coll.render())
    # ▼ 详情
    #   内容行1
    #   内容行2
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Collapsible(TuiComponent):
    """轻量级折叠容器。

    Attributes:
        title: 折叠标题文本，空字符串时无标题行。
        collapsed: True 时仅显示折叠标题行，False 时展开子组件。
    """

    COLLAPSED_PREFIX: str = "▶"
    EXPANDED_PREFIX: str = "▼"

    def __init__(
        self,
        title: str = "",
        collapsed: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        """初始化折叠容器。

        Args:
            title: 折叠标题文本。空字符串时折叠态返回空字符串，
                   展开态直接渲染子组件（无标题行）。
            collapsed: 初始折叠状态，默认 False（展开）。
            children: 子组件列表，默认空列表。
        """
        super().__init__(children=children)
        self._title: str = title
        self._collapsed: bool = collapsed

    @property
    def key(self) -> str:
        return "collapsible"

    def update(self, props: dict) -> bool:
        """接收新 props，对比变化决定是否重渲染。

        Args:
            props: 新的属性字典，支持 "collapsed" 和 "title" 键。

        Returns:
            True 如果 collapsed 或 title 发生变化。
        """
        changed = False
        if "collapsed" in props:
            new_collapsed = bool(props["collapsed"])
            if new_collapsed != self._collapsed:
                self._collapsed = new_collapsed
                changed = True
        if "title" in props:
            new_title = str(props["title"])
            if new_title != self._title:
                self._title = new_title
                changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染折叠容器。

        折叠态:
            - 有 title → dim 样式的 "▶ {title}"
            - 无 title → 空字符串
        展开态:
            - 有 title → bold 样式的 "▼ {title}" + 换行 + 缩进后的子组件输出
            - 无 title → 缩进后的子组件输出（无标题行）

        Returns:
            渲染后的样式化文本或纯文本。
        """
        has_title = bool(self._title)

        # ── 折叠态 ──
        if self._collapsed:
            if not has_title:
                return ""
            return StyledText(
                f"{self.COLLAPSED_PREFIX} {self._title}",
                dim=True,
            )

        # ── 展开态 ──
        parts: list[str | StyledText] = []

        if has_title:
            parts.append(
                StyledText(
                    f"{self.EXPANDED_PREFIX} {self._title}",
                    bold=True,
                )
            )

        children_output = self.render_children()
        if children_output:
            if has_title:
                parts.append("\n")
            parts.append(self._indent_output(children_output))

        if not parts:
            return ""

        # 全部为 str → 直接拼接（避免不必要的 StyledText 开销）
        if all(isinstance(p, str) for p in parts):
            return "".join(parts)

        # 混合类型 → 用 StyledText.assemble 拼接
        return StyledText.assemble(*parts)

    def _indent_output(self, output: str | StyledText) -> str | StyledText:
        """将输出内容每行前缀添加 2 空格缩进。

        Args:
            output: render_children() 的原始输出。

        Returns:
            每行添加 "  " 前缀后的输出，保留原有样式。
        """
        indent = "  "

        if isinstance(output, str):
            lines = output.split("\n")
            return "\n".join(f"{indent}{line}" for line in lines)

        # StyledText: 渲染为 ANSI 字符串 → 按行拆分 → 加前缀 → 重新解析
        raw = str(output)  # ANSI 包裹的字符串
        raw_lines = raw.split("\n")
        indented_raw = "\n".join(f"{indent}{line}" for line in raw_lines)
        return StyledText.from_ansi(indented_raw)

    def render_vnode(self) -> "VNode":
        """产出 VNode。

        Returns:
            VNode(type="collapsible", key="collapsible", props=...)
        """
        from ..vdom.vnode import VNode

        result = self.render()
        return VNode(
            type="collapsible",
            key=self.key,
            props={
                "collapsed": self._collapsed,
                "title": self._title,
                "text": str(result) if result else "",
            },
        )
