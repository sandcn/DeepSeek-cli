"""inline_nodes — 内联 Markdown 节点类型定义。

从 recursive_parser.py 拆分而来，集中存放 InlineNode 类型体系。
所有内联解析的 AST 节点类型定义于此。

原位置：recursive_parser.py（第151-297行）
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════
# 内联节点类型
# ═══════════════════════════════════════════════════════════

@dataclass
class InlineNode:
    """内联节点基类。"""
    content: str = ""
    children: list[InlineNode] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class TextNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class BoldNode(InlineNode):
    pass


@dataclass
class ItalicNode(InlineNode):
    pass


@dataclass
class BoldItalicNode(InlineNode):
    pass


@dataclass
class UnderlineNode(InlineNode):
    pass


@dataclass
class InlineCodeNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class LinkNode(InlineNode):
    url: str = ""
    title: str = ""


@dataclass
class ImageNode(InlineNode):
    url: str = ""
    title: str = ""


@dataclass
class StrikethroughNode(InlineNode):
    pass


@dataclass
class HighlightNode(InlineNode):
    pass


@dataclass
class SubscriptNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class SuperscriptNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class SpoilerNode(InlineNode):
    pass


@dataclass
class InlineMathNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class MathMLNode(InlineNode):
    """行内 MathML 节点 ``<math>…</math>``（内容的 Presentation MathML 原文）。

    渲染层把 MathML 转为 LaTeX 后复用二维/紧凑公式排版（ANSI 路径），
    Rich 路径转为 LaTeX 后交给 ``MathRenderer``。
    """
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class ProgressNode(InlineNode):
    """``<progress>`` / ``<meter>`` 行内进度节点（``meta`` 携带属性表）。

    属性（``value`` / ``max`` / ``min`` / ``optimum``）在解析时写入 ``meta``，
    渲染层据此绘制终端进度条。
    """
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class FootnoteRefNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点
    ref_id: str = ""


@dataclass
class InlineFootnoteNode(InlineNode):
    """行内脚注节点 ``^[脚注文本]``（Pandoc inline footnote）。

    正文位置渲染为脚注序号 ``[n]``，脚注文本在文末脚注列表中输出
    （与定义式脚注 ``[^id]: ...`` 共用同一编号序列）。
    """
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class AutoLinkNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点
    url: str = ""


@dataclass
class AutoLinkEmailNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点
    email: str = ""


@dataclass
class KbdNode(InlineNode):
    """键盘快捷键节点 <kbd>text</kbd>（不可嵌套）"""
    content: str = ""
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class AbbrNode(InlineNode):
    """缩写节点 <abbr title="...">text</abbr>（不可嵌套）"""
    content: str = ""
    title: str = ""  # 展开的全称
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class CriticAdditionNode(InlineNode):
    """CriticMarkup 添加节点 {++added text++}（可嵌套内联格式）"""
    pass


@dataclass
class CriticDeletionNode(InlineNode):
    """CriticMarkup 删除节点 {--deleted text--}（可嵌套内联格式）"""
    pass


@dataclass
class CriticHighlightNode(InlineNode):
    """CriticMarkup 高亮节点 ``{==highlighted text==}``（可嵌套内联格式）

    CriticMarkup 标准五类标记之一（``{++ ++}`` 添加 / ``{-- --}`` 删除 /
    ``{~~ ~~}`` 替换 / ``{== ==}`` 高亮 / ``{>> <<}`` 批注）。与 ``==x==``
    行内高亮语法不同：后者不带花括号，前者是 CriticMarkup 的审阅标记。
    """
    pass


@dataclass
class SmallTextNode(InlineNode):
    """小号文本节点 {-small text-}（可嵌套内联格式）"""
    pass


@dataclass
class BigTextNode(InlineNode):
    """放大文本节点 ``<big>text</big>``（终端以加粗近似）"""
    pass


@dataclass
class QuotedNode(InlineNode):
    """短引用节点 ``<q>text</q>``（渲染为中文引号包裹）"""
    pass


@dataclass
class ColorTextNode(InlineNode):
    """彩色文本节点 {color:red}text{color}（可嵌套内联格式）"""
    color: str = ""  # 颜色名（如 red, green, blue, yellow, cyan, magenta）


@dataclass
class CriticSubstitutionNode(InlineNode):
    """CriticMarkup 替换节点 {~~old text~>new text~~}（可嵌套内联格式）

    old_text 存储在 children 中（渲染为删除线），
    new_text 存储在 meta['new_children'] 中（渲染为绿色插入）。
    """
    pass


@dataclass
class CriticCommentNode(InlineNode):
    """CriticMarkup 批注节点 {>>comment text<<}（可嵌套内联格式）"""
    pass


@dataclass
class LineBreakNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点


@dataclass
class WikiLinkNode(InlineNode):
    """Wiki 链接节点 [[target]] 或 [[target|display]]（非 HTML 语法）

    target: 链接目标（页面名/标识符）
    display: 可选显示文本，None 时显示 target
    """
    target: str = ""
    display: str | None = None


@dataclass
class InlineCommentNode(InlineNode):
    """行内注释节点 %% comment %%（非 HTML 语法，渲染为隐藏/dim 文本）"""
    pass


@dataclass
class SpanNode(InlineNode):
    """行内属性 span ``[文本]{.class #id key=value}``（Pandoc bracketed span）。

    属性（``meta``）：
      - ``classes``: 类名列表（``.red`` / ``.highlight``…），渲染层按类名映射样式；
      - ``id``: 锚点 id（``#anchor``，终端渲染忽略，仅保留语义）；
      - ``attrs``: 其它键值对（``lang=python`` / ``title=...``…）。
    """
    pass


@dataclass
class CitationNode(InlineNode):
    """引用节点 ``[@key]`` / ``[-@key]`` / ``[@a; @b]``（Pandoc citation）。

    无文献数据库（终端渲染无法解析条目），按引用标记显示：正文渲染为
    ``[key]`` 样式文本，多条引用以 ``; `` 连接（``suppress_author`` 的
    ``-@`` 前缀渲染为 ``[-key]``）。
    """
    pass

# ═══════════════════════════════════════════════════════════
# 内联解析器常量
# ═══════════════════════════════════════════════════════════

_NESTABLE_TYPES = frozenset({
    BoldNode, ItalicNode, BoldItalicNode, UnderlineNode,
    StrikethroughNode, HighlightNode, SpoilerNode,
    CriticAdditionNode, CriticDeletionNode, CriticSubstitutionNode,
    CriticCommentNode, CriticHighlightNode, SmallTextNode, BigTextNode,
    QuotedNode, ColorTextNode,
})

_HTML_TAG_MAP: dict[str, tuple[type[InlineNode], bool]] = {
    # 粗体
    'b':      (BoldNode, True),
    'strong': (BoldNode, True),
    # 斜体
    'i':      (ItalicNode, True),
    'em':     (ItalicNode, True),
    'cite':   (ItalicNode, True),
    'dfn':    (ItalicNode, True),
    'var':    (ItalicNode, True),    # 数学变量 → 斜体
    # 下划线
    'u':      (UnderlineNode, True),
    'ins':    (UnderlineNode, True),
    # 删除线
    's':      (StrikethroughNode, True),
    'del':    (StrikethroughNode, True),
    # 高亮
    'mark':   (HighlightNode, True),
    # 角标
    'sub':    (SubscriptNode, False),
    'sup':    (SuperscriptNode, False),
    # 等宽/代码
    'code':   (InlineCodeNode, False),
    'kbd':    (KbdNode, False),
    'samp':   (InlineCodeNode, False),  # 示例输出 → 代码样式
    'tt':     (InlineCodeNode, False),  # 电传打字 → 代码样式
    # 缩写（title 属性在解析器中提取）
    'abbr':   (AbbrNode, False),
    'acronym': (AbbrNode, False),   # 旧式缩写标签，语义同 abbr
    # 文本样式
    'small':  (SmallTextNode, True),   # 小号文本（dim）
    'big':    (BigTextNode, True),     # 放大文本（终端加粗近似）
    'q':      (QuotedNode, True),      # 短引用（加引号）
    'time':   (TextNode, False),
    'data':   (TextNode, False),
    'bdo':    (TextNode, False),
    # 透明容器（内容照常显示，仅结构含义）
    'span':     (None, True),
    'label':    (None, True),
    'output':   (None, True),
    'legend':   (None, True),
    'bdi':      (None, True),
    'nobr':     (None, True),
    'blink':    (None, True),
    'marquee':  (None, True),
    'center':   (None, True),
    'div':      (None, True),
    'section':  (None, True),
    'article':  (None, True),
    'header':   (None, True),
    'footer':   (None, True),
    'main':     (None, True),
    'aside':    (None, True),
    'nav':      (None, True),
    'address':  (None, True),
    'figure':   (None, True),
    'figcaption': (None, True),
    'dialog':   (None, True),
    'menu':     (None, True),
    'form':     (None, True),
    'fieldset': (None, True),
    'option':   (None, True),
    'optgroup': (None, True),
    'textarea': (None, True),
    'canvas':   (None, True),
    'picture':  (None, True),
    'iframe':   (None, True),
    'video':    (None, True),
    'audio':    (None, True),
    'progress': (ProgressNode, False),
    'meter':    (ProgressNode, False),
    'summary':  (None, True),
    'template': (None, True),
    'noscript': (None, True),
    'colgroup': (None, True),
    'thead':    (None, True),
    'tbody':    (None, True),
    'tfoot':    (None, True),
    'tr':       (None, True),
    'td':       (None, True),
    'th':       (None, True),
    'caption':  (None, True),
    'font':     (None, True),   # 颜色属性在解析器中提取（无颜色 → 透明）
}


class InlineRecursionError(RuntimeError):
    pass


# ═══════════════════════════════════════════════════════════
# 内联节点渲染（转纯文本）
# ═══════════════════════════════════════════════════════════

def _mathml_text(source: str) -> str:
    """MathML 原文 → 纯文本（转 LaTeX 后交公式渲染器；失败回退原文）。

    ``inline_nodes`` 无 ANSI 依赖，故延迟导入转换器——不可用时保留原文。
    """
    try:
        from .ansi._mathml import mathml_to_latex
        latex = mathml_to_latex(source)
    except Exception:
        latex = None
    return latex if latex else (source or "")


def _progress_text(meta: dict) -> str:
    """``<progress>`` / ``<meter>`` 属性 → 纯文本百分比（用于纯文本渲染）。"""
    meta = meta or {}
    try:
        value = float(str(meta.get("value", "")).strip().rstrip("%") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    try:
        maxv = float(str(meta.get("max", "")).strip() or 1.0)
    except (TypeError, ValueError):
        maxv = 1.0
    if maxv <= 0:
        maxv = 1.0
    ratio = max(0.0, min(1.0, value / maxv))
    return f"{ratio * 100:.0f}%"


def render_inline_to_text(nodes: list[InlineNode]) -> str:
    """将内联节点列表渲染为纯文本。"""
    result: list[str] = []
    for node in nodes:
        if isinstance(node, LinkNode):
            if node.url.startswith('[ref:'):
                result.append(f'{node.content} (ref:{node.url[5:-1]})')
            else:
                result.append(f'{node.content} ({node.url})')
        elif isinstance(node, ImageNode):
            result.append(f'[Image: {node.content}]')
        elif isinstance(node, AutoLinkNode):
            result.append(node.content)
        elif isinstance(node, AutoLinkEmailNode):
            result.append(node.content)
        elif isinstance(node, LineBreakNode):
            result.append('\n')
        elif isinstance(node, FootnoteRefNode):
            result.append(f'[^{node.ref_id}]')
        elif isinstance(node, InlineFootnoteNode):
            result.append('[n]')  # 行内脚注：正文占位（实际渲染为 ``[n]`` 序号）
        elif isinstance(node, SpoilerNode):
            result.append(node.content)
        elif isinstance(node, InlineMathNode):
            result.append(node.content)
        elif isinstance(node, MathMLNode):
            result.append(_mathml_text(node.content))
        elif isinstance(node, ProgressNode):
            result.append(_progress_text(node.meta))
        elif isinstance(node, SubscriptNode):
            result.append(node.content)
        elif isinstance(node, SuperscriptNode):
            result.append(node.content)
        elif isinstance(node, CriticAdditionNode):
            result.append(node.content)
        elif isinstance(node, CriticDeletionNode):
            result.append(node.content)
        elif isinstance(node, CriticHighlightNode):
            result.append(node.content)
        elif isinstance(node, CriticSubstitutionNode):
            result.append(node.content)
        elif isinstance(node, CriticCommentNode):
            result.append(node.content)
        elif isinstance(node, SmallTextNode):
            result.append(node.content)
        elif isinstance(node, ColorTextNode):
            result.append(node.content)
        elif isinstance(node, WikiLinkNode):
            result.append(node.display or node.target)
        elif isinstance(node, InlineCommentNode):
            result.append('')  # 注释不产生可见文本
        elif isinstance(node, CitationNode):
            keys = (node.meta or {}).get("keys") or []
            result.append('[' + '; '.join(keys) + ']' if keys else node.content)
        elif node.children:
            result.append(render_inline_to_text(node.children))
        else:
            result.append(node.content)
    return ''.join(result)
