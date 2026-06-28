"""Panel 组件 — 带 header/footer 的语义化面板容器。

内部聚合 Box 组件完成边框渲染，提供 header/body/footer 三段式 API。
使用者无需手动拼接 Box + title + children 的样板代码。

使用示例:
    Panel(header="标题", children=[Text("内容")], border_style="round")
    Panel(header="信息", footer="版本 1.0", header_color="blue", footer_color="dim")
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .base import TuiComponent
from ..components.box import Box
from ..components.text import Text
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Panel(TuiComponent):
    """Panel 面板组件 — header/body/footer 三段式边框容器。

    内部聚合 Box 完成边框渲染：
    - header（若存在）→ StyledText 样式化标题行
    - body → render_children() 子组件输出
    - footer（若存在）→ StyledText 样式化底部行
    - 整体由 Box 包裹边框

    Props:
        header: 面板标题文本（可选，None 时不渲染标题行）。
        footer: 面板底部文本（可选，None 时不渲染底部行）。
        header_color: 标题前景色名（如 'blue'、'red'），None 使用终端默认色。
        header_bold: 标题是否加粗。
        footer_color: 底部文本前景色名。
        footer_bold: 底部文本是否加粗。
        border_style: 边框样式，透传给内部 Box（默认 'single'）。
        children: 子组件列表（构成 body 内容）。
        **box_props: 其余关键字参数透传给 Box（如 padding_x、padding_y、
                     border_color、title 等）。
    """

    def __init__(
        self,
        header: str | None = None,
        footer: str | None = None,
        header_color: str | None = None,
        header_bold: bool = False,
        footer_color: str | None = None,
        footer_bold: bool = False,
        border_style: str = "single",
        children=None,
        **box_props: Any,
    ):
        super().__init__(children=children)
        self._header = header
        self._footer = footer
        self._header_color = header_color
        self._header_bold = header_bold
        self._footer_color = footer_color
        self._footer_bold = footer_bold
        self._border_style = border_style
        # 过滤 children 键防止与 Box(children=...) 冲突
        box_props.pop("children", None)
        self._box_props = box_props

    # ── 属性 ──────────────────────────────────────────────

    @property
    def key(self) -> str:
        """稳定标识符 — 用于 VNode Diff 的 key 匹配。"""
        return "panel"

    # ── 渲染 ──────────────────────────────────────────────

    def render(self) -> str | StyledText:
        """渲染 Panel 面板。

        流程:
            1. 调用 render_children() 获取 body 文本
            2. 若有 header: 在 body 前添加 StyledText(header, ...) + 分隔空行
            3. 若有 footer: 在 body 后添加 分隔空行 + StyledText(footer, ...)
            4. 创建内部 Text 子组件包裹拼接内容
            5. 调用 Box(border_style, children=[text], **box_props).render() 返回
        """
        # 1. 获取 body 文本
        body = self.render_children()
        body_str = str(body) if body else ""

        # 2-3. 拼接 header / body / footer
        parts: list[str] = []
        if self._header is not None:
            parts.append(str(StyledText(
                self._header, fg=self._header_color, bold=self._header_bold)))
        if self._header is not None and body_str:
            parts.append("")  # header 与 body 之间的分隔空行
        if body_str:
            parts.append(body_str)
        if self._footer is not None and body_str:
            parts.append("")  # body 与 footer 之间的分隔空行
        elif self._footer is not None and self._header is not None and not body_str:
            parts.append("")  # header+footer 无 body：一个分隔空行
        if self._footer is not None:
            parts.append(str(StyledText(
                self._footer, fg=self._footer_color, bold=self._footer_bold)))

        content = "\n".join(parts) if parts else ""

        # 4-5. 创建内部 Text 子组件作为 Box 的 children，调用 Box.render()
        text_component = Text(children=content)
        box = Box(
            border_style=self._border_style,
            children=[text_component],
            **self._box_props,
        )
        return box.render()

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。

        Returns:
            VNode(type="panel", key="panel", props={"text": ...})
        """
        from ..vdom.vnode import VNode
        result = self.render()
        return VNode(
            type="panel",
            key=self.key,
            props={"text": str(result)} if result else {},
        )

    # ── 生命周期 ──────────────────────────────────────────

    def update(self, props: dict) -> bool:
        """接收新 props，检测变更决定是否需要重渲染。

        比较 header/footer/header_color/header_bold/footer_color/
        footer_bold/border_style 及 box_props 中各键值，
        任一项变化即返回 True。

        Args:
            props: 新的属性字典。

        Returns:
            True 如果任何属性发生变化需要重渲染。
        """
        changed = False
        panel_keys = {
            "header", "footer", "header_color", "header_bold",
            "footer_color", "footer_bold", "border_style",
        }

        for key in panel_keys:
            if key in props:
                attr = f"_{key}"
                if props[key] != getattr(self, attr):
                    setattr(self, attr, props[key])
                    changed = True

        # 其余 props 视为 box_props 变更（排除 children 防冲突）
        box_updates = {k: v for k, v in props.items()
                       if k not in panel_keys and k != "children"}
        for k, v in box_updates.items():
            if self._box_props.get(k) != v:
                self._box_props[k] = v
                changed = True

        return changed
