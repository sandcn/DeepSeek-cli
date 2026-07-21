"""targets.web — WebRenderTarget：Web 渲染目标（HTML 输出）。

将 Markdown 渲染树（VNode）输出为 HTML 片段，
支持服务端渲染（SSR）和客户端水合（Hydration）。

与 TerminalRenderTarget 共享 RenderTarget 接口，
VNodePatcher/IncrementalVNodeRenderer 无需修改即可输出 HTML。

当前支持：
  - 块级：段落、标题(1-6)、分隔线、引用、列表、代码块、表格
  - 特殊：数学块(KaTeX 渲染)、Mermaid 占位、告示、折叠块、HTML块
  - 内联：粗体、斜体、代码、链接、图片、删除线、高亮、Emoji
"""

from __future__ import annotations

import html as html_mod
import logging
from typing import Any

from ...tui.widgets.lock import locked_print
from .base import RenderTarget
from ._web_inline import _InlineHtmlMixin

logger = logging.getLogger(__name__)


def _get_katex() -> object | None:
    """获取服务端 KaTeX 模块（可选依赖）。

    需要安装 katex Python 包：
      pip install katex

    未安装时返回 None，数学公式由前端 KaTeX.js 渲染。
    """
    try:
        import katex as katex_mod
        return katex_mod
    except ImportError:
        return None


class WebRenderTarget(_InlineHtmlMixin, RenderTarget):
    """Web 渲染目标——输出 HTML 字符串。

    将渲染内容输出为 HTML 格式，可用于：
    - 服务端渲染（SSR）：生成完整的 HTML 页面
    - API 响应：返回 HTML 片段给前端
    - 静态站点生成：将 Markdown 转换为 HTML

    用法：
      target = WebRenderTarget()
      target.write("<h1>Title</h1>")
      target.write_line("<p>Paragraph</p>")
      html_output = target.get_html()

    或使用便捷方法：
      target = WebRenderTarget()
      target.write_paragraph("Hello **world**")  # 自动渲染内联格式
      target.write_heading("Title", level=1)
      target.write_code_block("print('hi')", lang="python")
      locked_print(target.get_html())
    """

    def __init__(self):
        self._parts: list[str] = []
        self._indent_level = 0

    # ═══════════════════════════════════════════════════════
    # RenderTarget 接口实现
    # ═══════════════════════════════════════════════════════

    def write(self, renderable: Any) -> None:
        """输出 HTML 内容。"""
        self._parts.append(str(renderable))

    def write_line(self, text: str = "") -> None:
        """输出一行 HTML（带换行）。"""
        indent = "  " * self._indent_level
        self._parts.append(f"{indent}{text}\n")

    def write_raw(self, text: str) -> None:
        """追加纯文本（不转义）。"""
        self._parts.append(text)

    def clear_line(self) -> None:
        """清除最后一行（如果可以）。"""
        if self._parts:
            last = self._parts[-1].rstrip('\n')
            if '\n' not in last:
                self._parts[-1] = ""
            else:
                self._parts[-1] = last[:last.rfind('\n') + 1]

    @property
    def width(self) -> int:
        return 120  # Web 端宽度由 CSS 控制

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    # ═══════════════════════════════════════════════════════
    # HTML 输出
    # ═══════════════════════════════════════════════════════

    def get_html(self) -> str:
        """获取累积的 HTML 输出。"""
        return ''.join(self._parts)

    def __str__(self) -> str:
        return self.get_html()
