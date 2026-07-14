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

    def reset(self) -> None:
        """清空缓冲区。"""
        self._parts.clear()

    # ═══════════════════════════════════════════════════════
    # 内联 Markdown → HTML 渲染
    # ═══════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════
    # 块级元素便捷方法
    # ═══════════════════════════════════════════════════════

    def write_paragraph(self, content: str) -> None:
        """输出 <p> 段落。"""
        html_content = self.render_inline(content)
        self.write_line(f'<p>{html_content}</p>')
        self.write_line()

    def write_heading(self, content: str, level: int = 1) -> None:
        """输出 <h1>~<h6> 标题。"""
        tag = f'h{min(max(level, 1), 6)}'
        html_content = self.render_inline(content)
        self.write_line(f'<{tag}>{html_content}</{tag}>')
        self.write_line()

    def write_hr(self) -> None:
        """输出 <hr>。"""
        self.write_line('<hr>')

    def write_blockquote(self, content: str, depth: int = 1) -> None:
        """输出 <blockquote>（嵌套使用多个标签）。"""
        tag = '<blockquote>' * depth
        close = '</blockquote>' * depth
        html_content = self.render_inline(content)
        self.write_line(f'{tag}{html_content}{close}')

    def write_code_block(self, source: str, lang: str = "text",
                         title: str = "") -> None:
        """输出 <pre><code> 代码块。"""
        escaped = html_mod.escape(source)
        lang_class = f' class="language-{lang}"' if lang else ''
        if title:
            self.write_line(f'<div class="code-block-title">{title}</div>')
        self.write_line(f'<pre><code{lang_class}>{escaped}</code></pre>')
        self.write_line()

    def write_table(self, rows: list[list[str]],
                    alignments: list[str] | None = None) -> None:
        """输出 <table>。"""
        if not rows:
            return
        alignments = alignments or []
        self.write_line('<table>')
        self._indent_level += 1

        # 表头
        headers = rows[0]
        self.write_line('<thead>')
        self._indent_level += 1
        self.write_line('<tr>')
        self._indent_level += 1
        for i, h in enumerate(headers):
            align = f' style="text-align:{alignments[i]}"' if i < len(alignments) else ''
            self.write_line(f'<th{align}>{self.render_inline(h)}</th>')
        self._indent_level -= 1
        self.write_line('</tr>')
        self._indent_level -= 1
        self.write_line('</thead>')

        # 表体
        if len(rows) > 1:
            self.write_line('<tbody>')
            self._indent_level += 1
            for row in rows[1:]:
                self.write_line('<tr>')
                self._indent_level += 1
                for i, cell in enumerate(row):
                    align = f' style="text-align:{alignments[i]}"' if i < len(alignments) else ''
                    self.write_line(f'<td{align}>{self.render_inline(cell)}</td>')
                self._indent_level -= 1
                self.write_line('</tr>')
            self._indent_level -= 1
            self.write_line('</tbody>')

        self._indent_level -= 1
        self.write_line('</table>')
        self.write_line()

    def write_list_item(self, content: str, ordered: bool = False,
                        depth: int = 1) -> None:
        """输出 <li>（列表项由外层 <ul>/<ol> 包裹）。"""
        html_content = self.render_inline(content)
        self.write_line(f'<li>{html_content}</li>')

    def write_definition_item(self, content: str, term: str = "") -> None:
        """输出 <dl><dt><dd>。"""
        if term:
            self.write_line(f'<dt>{self.render_inline(term)}</dt>')
        self.write_line(f'<dd>{self.render_inline(content)}</dd>')

    def write_math(self, source: str) -> None:
        """输出数学块（服务端渲染：KaTeX class 占位 / 可选服务端预渲染）。

        默认输出含 data-katex 属性的 div，前端 KaTeX 自动接管渲染。
        也可启用服务端预渲染（import katex 后调用 render_to_string）。
        """
        source = source.strip()
        escaped = html_mod.escape(source)

        # 尝试服务端 KaTeX 预渲染（仅在 kaTeX 可用时）
        katex = _get_katex()
        if katex is not None:
            try:
                rendered = katex.render_to_string(source)
                self.write_line(
                    f'<div class="math-block katex-rendered">{rendered}</div>'
                )
                return
            except Exception:
                # 预渲染失败，降级为前端渲染
                pass

        # 服务端 KaTeX 不可用 → 输出 data-katex 占位，前端渲染
        self.write_line(
            f'<div class="math-block" data-katex="{escaped}">'
            f'<code>{escaped}</code>'
            f'</div>'
        )

    def write_mermaid(self, source: str) -> None:
        """输出 Mermaid 图表块。"""
        escaped = html_mod.escape(source)
        self.write_line(
            f'<div class="mermaid">'
            f'{escaped}'
            f'</div>'
        )

    def write_details(self, summary: str = "", content_lines: list[str] | None = None) -> None:
        """输出 <details><summary> 折叠块。"""
        self.write_line('<details>')
        self._indent_level += 1
        if summary:
            self.write_line(f'<summary>{self.render_inline(summary)}</summary>')
        if content_lines:
            for line in content_lines:
                self.write_line(f'<div>{self.render_inline(line)}</div>')
        self._indent_level -= 1
        self.write_line('</details>')
        self.write_line()

    def write_admonition(self, adm_type: str = "NOTE",
                         title: str = "", content_lines: list[str] | None = None) -> None:
        """输出告示块（使用 CSS class）。"""
        css_class = f'admonition admonition-{adm_type.lower()}'
        self.write_line(f'<div class="{css_class}">')
        self._indent_level += 1
        label = adm_type.upper()
        self.write_line(f'<div class="admonition-title">{label}</div>')
        if content_lines:
            for line in content_lines:
                self.write_line(f'<div>{self.render_inline(line)}</div>')
        self._indent_level -= 1
        self.write_line('</div>')
        self.write_line()

    def __str__(self) -> str:
        return self.get_html()
