"""Link 组件 — React Ink 风格超链接组件。

提供 <Link url="..."> 组件，在支持 OSC 8 的终端中输出可点击超链接，
不支持时降级为带下划线的文本 + URL 显示。

使用示例:
    link = Link(url="https://example.com", fallback_text="Click here")
    print(link.render())

    # 配合 Text 子组件
    link = Link(url="https://example.com", children=[Text("Click", color="cyan")])
    print(link.render())
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


def _supports_hyperlinks() -> bool:
    """检测当前终端是否支持 OSC 8 超链接。

    检查以下环境变量判断终端能力：
    - TERM: 已知支持 OSC 8 的终端标识（xterm、alacritty、kitty 等）
    - TERM_PROGRAM: iTerm2 / WezTerm 等设置的标识
    - WT_SESSION: Windows Terminal
    - KONSOLE_VERSION: Konsole
    - VTE_VERSION: VTE 系终端（GNOME Terminal、Tilix 等）

    Returns:
        True 如果终端支持 OSC 8 超链接。
    """
    term = os.environ.get("TERM", "")
    if term.startswith(("xterm", "alacritty", "kitty", "wezterm", "foot", "contour")):
        return True
    if os.environ.get("TERM_PROGRAM"):
        return True
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("KONSOLE_VERSION"):
        return True
    if os.environ.get("VTE_VERSION"):
        return True
    return False


class Link(TuiComponent):
    """React Ink Link 组件 — 终端超链接。

    在支持 OSC 8 的终端中渲染为可点击的超链接，
    不支持时降级为带下划线的蓝色文本，后附 URL。

    Props:
        url: str — 链接目标 URL。
        fallback_text: str — 无 children 时使用的纯文本标签。
        children: list[TuiComponent] — 作为链接标签渲染的子组件。
                  子组件的 ANSI 样式会被保留在 OSC 8 包裹内。
    """

    def __init__(self, url: str = "", fallback_text: str = "",
                 children: list[TuiComponent] | None = None):
        """初始化 Link 组件。

        Args:
            url: 链接目标 URL。
            fallback_text: 降级显示文本（无 children 时使用）。
            children: 子组件列表，作为显示文本。
        """
        super().__init__(children=children)
        self._url = url
        self._fallback_text = fallback_text

    @property
    def key(self) -> str:
        return "link"

    def update(self, props: dict) -> bool:
        """接收新 props，对比变化决定是否重渲染。"""
        changed = False
        if "url" in props and props["url"] != self._url:
            self._url = props["url"]
            changed = True
        if "fallback_text" in props and props["fallback_text"] != self._fallback_text:
            self._fallback_text = props["fallback_text"]
            changed = True
        return changed

    def _render_text(self) -> str:
        """从 children 或 fallback_text 获取显示文本。

        若 children 存在，遍历调用 render() 并拼接输出。
        StyledText 子组件通过 str() 保留其 ANSI SGR 样式。
        若 children 渲染结果为空，回退到 fallback_text，
        fallback_text 也为空时回退到 url 本身。

        Returns:
            纯文本或含 ANSI SGR 样式的文本（未包裹 OSC 8）。
        """
        ch = self._ensure_children()
        if ch:
            outputs: list[str] = []
            for child in ch:
                rendered = child.render()
                if isinstance(rendered, StyledText):
                    # 使用 str() 保留 ANSI SGR 样式
                    outputs.append(str(rendered))
                elif isinstance(rendered, str):
                    outputs.append(rendered)
            text = "".join(outputs)
            if text:
                return text
        if self._fallback_text:
            return self._fallback_text
        return self._url

    def render(self) -> str | StyledText:
        """渲染超链接。

        - 支持 OSC 8 的终端：输出 \\033]8;;{url}\\033\\\\{text}\\033]8;;\\033\\\\
          （OSC 8 包裹不影响内部 ANSI SGR 样式）。
        - 不支持时降级为带下划线的蓝色文本，后附 URL：{text} ({url})。
        - url 为空时直接返回文本，不做任何包装。
        """
        text = self._render_text()
        if not text:
            return ""
        if not self._url:
            return text
        if _supports_hyperlinks():
            # OSC 8: 包裹文本，保留内部 ANSI SGR 样式
            return f"\033]8;;{self._url}\033\\{text}\033]8;;\033\\"
        # 降级：下划线 + dim + blue 文本，附 URL
        return StyledText.assemble(
            (text, "underline dim blue"),
            (f" ({self._url})", "dim"),
        )

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。"""
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="link",
            key=self.key,
            props={
                "url": self._url,
                "text": str(rendered) if rendered else "",
            },
        )
