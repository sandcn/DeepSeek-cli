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
class FootnoteRefNode(InlineNode):
    children: list[InlineNode] | None = None  # 叶子节点
    ref_id: str = ""


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
class SmallTextNode(InlineNode):
    """小号文本节点 {-small text-}（可嵌套内联格式）"""
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

# ═══════════════════════════════════════════════════════════
# 内联解析器常量
# ═══════════════════════════════════════════════════════════

_NESTABLE_TYPES = frozenset({
    BoldNode, ItalicNode, BoldItalicNode, UnderlineNode,
    StrikethroughNode, HighlightNode, SpoilerNode,
    CriticAdditionNode, CriticDeletionNode, CriticSubstitutionNode,
    CriticCommentNode, SmallTextNode, ColorTextNode,
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
    'small':  (TextNode, False),    # 小号文本
    'q':      (TextNode, False),    # 行内引用（提取文本，无引号）
    'time':   (TextNode, False),
    'data':   (TextNode, False),
    'bdo':    (TextNode, False),
    # 特殊处理（span 在代码中单独处理）
    'span':   (None, True),
}


class InlineRecursionError(RuntimeError):
    pass


# ═══════════════════════════════════════════════════════════
# 内联节点渲染（转纯文本）
# ═══════════════════════════════════════════════════════════

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
        elif isinstance(node, SpoilerNode):
            result.append(node.content)
        elif isinstance(node, InlineMathNode):
            result.append(node.content)
        elif isinstance(node, SubscriptNode):
            result.append(node.content)
        elif isinstance(node, SuperscriptNode):
            result.append(node.content)
        elif isinstance(node, CriticAdditionNode):
            result.append(node.content)
        elif isinstance(node, CriticDeletionNode):
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
        elif node.children:
            result.append(render_inline_to_text(node.children))
        else:
            result.append(node.content)
    return ''.join(result)
