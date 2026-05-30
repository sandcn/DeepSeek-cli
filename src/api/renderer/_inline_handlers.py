"""内联渲染节点类型 → 处理函数调度表。

所有节点处理器函数集中在此模块，避免 inline_renderer.py 中
模块级代码与类定义混合，便于维护和扩展。
"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._inline_preprocess import _preprocess_text


# ── 模块级调度器辅助函数（避免 lambda 闭包问题） ──────────

def _inline_math_handler(self, node, ctx, _depth):
    """InlineMathNode 调度器：延迟导入 MathRenderer 避免循环导入。"""
    from .math_renderer import MathRenderer
    return MathRenderer().render_inline(node.content)


def _footnote_ref_handler(self, node, ctx, _depth):
    """FootnoteRefNode 调度器：递增脚注计数器并渲染。"""
    fn_num = ctx.fn_next_number() if ctx else 0
    return Text(
        f"[{fn_num}]" if ctx else f"[^{node.ref_id}]",
        style=Style(color="bright_cyan", italic=True, bold=True),
    )


def _inline_code_handler(self, n, ctx, d):
    """内联代码处理器：绿色文字 + 暗灰背景 + 粗体 + 底部边框效果。"""
    return Text(
        f" {n.content} ",
        style=Style(color="bright_green", bgcolor="grey15", bold=True),
    )


def _abbr_node_handler(self, n, ctx, d):
    """AbbrNode 处理器：黄色下划线 + 末尾 dim 提示"""
    result = Text(n.content, style=Style(color="yellow", underline=True, italic=True))
    if n.title:
        result.append(f" ({n.title})", style=Style(dim=True, color="bright_black"))
    return result


# ── 上下标 Unicode 渲染辅助函数 ──────────────────────────

_SUB_SCRIPT_MAP = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
    '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
    'a': 'ₐ', 'e': 'ₑ', 'h': 'ₕ', 'i': 'ᵢ', 'j': 'ⱼ',
    'k': 'ₖ', 'l': 'ₗ', 'm': 'ₘ', 'n': 'ₙ', 'o': 'ₒ',
    'p': 'ₚ', 'r': 'ᵣ', 's': 'ₛ', 't': 'ₜ', 'u': 'ᵤ',
    'v': 'ᵥ', 'x': 'ₓ',
    '+': '₊', '-': '₋', '(': '₍', ')': '₎',
}

_SUPER_SCRIPT_MAP = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '(': '⁽', ')': '⁾',
    'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ', 'd': 'ᵈ', 'e': 'ᵉ',
    'f': 'ᶠ', 'g': 'ᵍ', 'h': 'ʰ', 'i': 'ⁱ', 'j': 'ʲ',
    'k': 'ᵏ', 'l': 'ˡ', 'm': 'ᵐ', 'n': 'ⁿ', 'o': 'ᵒ',
    'p': 'ᵖ', 'r': 'ʳ', 's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ',
    'v': 'ᵛ', 'w': 'ʷ', 'x': 'ˣ', 'y': 'ʸ', 'z': 'ᶻ',
}

_CIRCLED_DIGITS = [
    '①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
    '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳',
]


def _render_subscript_node(self, node, ctx, depth) -> Text:
    """渲染 SubscriptNode：有子节点时递归渲染 + 应用样式，无子节点时使用 Unicode 转换。

    使用 Text(style=...) 设置基准样式（dim italic），再逐个追加子节点内容。
    子节点自身的 span 样式会叠加在基准样式之上，确保嵌套格式（如粗体）保留。

    ★ 修复：子节点路径下也尝试 Unicode 转换。
    先渲染子节点获取纯文本，若全部字符可转换（含空格），
    则应用 Unicode 下标转换后返回；否则返回原始样式文本。
    """
    if node.children:
        base = Style(dim=True, italic=True)
        result = Text(style=base)
        for child in node.children:
            child_rich = self._node_to_rich(child, ctx, depth + 1)
            if child_rich:
                result.append_text(child_rich)
        # ★ 尝试 Unicode 下标转换：全部字符可转换时用 Unicode 替代 dim italic
        plain = result.plain
        if plain and all(ch in _SUB_SCRIPT_MAP or ch.isspace() for ch in plain):
            # ★ 修复 Bug: Unicode 转换路径丢失子节点样式。
            #    从 result 的 spans 构建 offset→style 映射，逐字符转换并附加原样式。
            char_styles = {}
            for span in result.spans:
                s = span.style if isinstance(span.style, Style) else Style.parse(span.style)
                for j in range(span.start, span.end):
                    existing = char_styles.get(j)
                    char_styles[j] = Style.combine([existing, s]) if existing else s
            converted = Text(style=Style(dim=True, italic=True))
            for j, ch in enumerate(plain):
                unicode_ch = _SUB_SCRIPT_MAP.get(ch, ch)
                style = char_styles.get(j)
                if style:
                    converted.append(unicode_ch, style=style)
                else:
                    converted.append(unicode_ch)
            return converted
        return result
    # 叶子节点：使用 Unicode 下标字符转换
    converted = ''.join(_SUB_SCRIPT_MAP.get(ch, ch) for ch in node.content)
    return Text(converted, style=Style(dim=True, italic=True))


def _render_superscript_node(self, node, ctx, depth) -> Text:
    """渲染 SuperscriptNode：有子节点时递归渲染 + 应用样式，无子节点时使用 Unicode 转换。

    使用 Text(style=...) 设置基准样式，子节点的 span 样式会叠加在之上。

    ★ 修复：子节点路径下也尝试 Unicode 转换。
    先渲染子节点获取纯文本，若全部字符可转换（含空格），
    则应用 Unicode 上标转换后返回；否则返回原始样式文本。
    """
    if node.children:
        base = Style(color="bright_cyan", italic=True)
        result = Text(style=base)
        for child in node.children:
            child_rich = self._node_to_rich(child, ctx, depth + 1)
            if child_rich:
                result.append_text(child_rich)
        # ★ 尝试 Unicode 上标转换
        plain = result.plain
        if plain and all(ch in _SUPER_SCRIPT_MAP or ch.isspace() for ch in plain):
            # ★ 修复 Bug: Unicode 转换路径丢失子节点样式。
            #    从 result 的 spans 构建 offset→style 映射，逐字符转换并附加原样式。
            char_styles = {}
            for span in result.spans:
                s = span.style if isinstance(span.style, Style) else Style.parse(span.style)
                for j in range(span.start, span.end):
                    existing = char_styles.get(j)
                    char_styles[j] = Style.combine([existing, s]) if existing else s
            converted = Text(style=Style(color="bright_cyan", italic=True))
            for j, ch in enumerate(plain):
                unicode_ch = _SUPER_SCRIPT_MAP.get(ch, ch)
                style = char_styles.get(j)
                if style:
                    converted.append(unicode_ch, style=style)
                else:
                    converted.append(unicode_ch)
            return converted
        return result
    # 叶子节点：使用 Unicode 上标字符转换
    converted = ''.join(_SUPER_SCRIPT_MAP.get(ch, ch) for ch in node.content)
    return Text(converted, style=Style(color="bright_cyan", italic=True))


# ── 导入 InlineNode 类型及解析器（条件导入，防止循环导入） ──
try:
    from .inline_parser import _InlineParser
    from .inline_nodes import (
        InlineNode as _InlineNode,
        TextNode as _TextNode,
        BoldNode as _BoldNode,
        ItalicNode as _ItalicNode,
        BoldItalicNode as _BoldItalicNode,
        UnderlineNode as _UnderlineNode,
        KbdNode as _KbdNode,
        AbbrNode as _AbbrNode,
        InlineCodeNode as _InlineCodeNode,
        LinkNode as _LinkNode,
        ImageNode as _ImageNode,
        StrikethroughNode as _StrikethroughNode,
        HighlightNode as _HighlightNode,
        SubscriptNode as _SubscriptNode,
        SuperscriptNode as _SuperscriptNode,
        InlineMathNode as _InlineMathNode,
        FootnoteRefNode as _FootnoteRefNode,
        AutoLinkNode as _AutoLinkNode,
        AutoLinkEmailNode as _AutoLinkEmailNode,
        SpoilerNode as _SpoilerNode,
        CriticAdditionNode as _CriticAdditionNode,
        CriticDeletionNode as _CriticDeletionNode,
        CriticSubstitutionNode as _CriticSubstitutionNode,
        CriticCommentNode as _CriticCommentNode,
        SmallTextNode as _SmallTextNode,
        ColorTextNode as _ColorTextNode,
        LineBreakNode as _LineBreakNode,
        WikiLinkNode as _WikiLinkNode,
        InlineCommentNode as _InlineCommentNode,
        render_inline_to_text,
    )
    _LAZY_IMPORT_OK = True
except ImportError:
    # ★ 非惰性模式下立即重抛 —— 占位类只会掩盖导入失败，导致内联渲染静默失效
    _LAZY_IMPORT_OK = False
    _InlineParser = None
    raise


# ── 文本节点处理器 ──────────────────────────────────────

def _text_node_handler(self, n, ctx, depth):
    """TextNode 处理器：快速跳过极短且无 URL/Email 特征的文本。

    同时应用智能排版预处理（-- → –, --- → —, ... → …）。
    """
    text = n.content
    if not text:
        return Text()
    # 智能排版预处理（含 Emoji + HTML 实体 + 智能排版）
    text = _preprocess_text(text)
    if len(text) < 8 and '://' not in text and 'www.' not in text and '@' not in text:
        return Text(text)
    return self._linkify_text(text)


def _link_node_handler(self, n, ctx, depth):
    """LinkNode 处理器：渲染链接文本 + 下划线/蓝色样式。

    支持：
      - 标准链接 `[text](url)` → 蓝色下划线样式
      - 参考式链接 `[text][ref]` → 从 ctx.ref_map 解析 URL 并显示引用编号

    注意：Text.stylize() 原地修改并返回 None，不可链式调用。
    """
    result = self._nodes_to_rich(n.children, ctx, depth)

    # ── 参考式链接解析 [ref:xxx] ────────────────────────────
    url = getattr(n, 'url', '')
    if url and url.startswith('[ref:') and ctx:
        ref_id = url[5:-1]
        resolved = ctx.ref_map.get(ref_id)
        if resolved:
            actual_url, title = resolved
            result.stylize(Style(color="cyan", underline=True))
            # ── 圈数字编号（基于 ref_map 插入顺序） ────────────
            ref_keys = list(ctx.ref_map.keys())
            ref_idx = ref_keys.index(ref_id) + 1  # 1-based
            circled = _CIRCLED_DIGITS[ref_idx - 1] if ref_idx <= 20 else f"[{ref_idx}]"
            result.append(f" {circled}", style=Style(dim=True, color="bright_black"))
            # ── URL 行尾悬停提示 ──────────────────────────────
            result.append(f" ({actual_url})", style=Style(dim=True, color="bright_black"))
            return result
        else:
            # 未解析的参考链接：黄色高亮 + 显示 ref_id
            result.stylize(Style(color="yellow", italic=True))
            result.append(f"[?{ref_id}]", style=Style(dim=True, color="bright_black"))
            return result

    result.stylize(Style(color="cyan", underline=True))
    n_title = getattr(n, 'title', '')
    if n_title:
        result.append(f" \"{n_title}\"", style=Style(dim=True, color="bright_black"))
    return result


# ── 通用子节点样式辅助函数 ──────────────────────────────

def _style_children(self, node, ctx, depth, style):
    children = self._nodes_to_rich(node.children, ctx, depth)
    children.stylize(style)
    return children


def _spoiler_node_handler(self, n, ctx, d):
    """Spoiler 处理器：使用 █ 字符掩码替代原文内容，不显示真实文字。"""
    # 获取原始文本（优先子节点，其次直接 content）
    if n.children:
        text = render_inline_to_text(n.children)
    else:
        text = n.content or ""
    # 用 █ 替换每个可见字符（保留空白字符不变）
    masked = ''.join('█' if not c.isspace() else ' ' for c in text)
    return Text(masked, style=Style(color="bright_black", dim=True))


def _render_strikethrough_handler(self, n, ctx, d):
    """增强删除线：strike + dim + red-ish color 三重保障终端可见性。"""
    result = self._nodes_to_rich(n.children, ctx, d + 1)
    result.stylize(Style(strike=True, dim=True, color="bright_red"))
    return result


def _render_critic_substitution_node(self, n, ctx, d):
    """CriticSubstitutionNode 渲染：旧文本（删除线）+ '→' + 新文本（绿色）。"""
    # 旧文本（children）渲染为删除线
    old_result = self._nodes_to_rich(n.children, ctx, d + 1)
    old_result.stylize(Style(strike=True, dim=True, color="bright_red"))

    # 添加箭头分隔
    result = Text()
    result.append_text(old_result)
    result.append(" → ", style=Style(bold=True, color="white"))

    # 新文本（meta['new_children']）渲染为绿色粗体
    new_children = n.meta.get("new_children", [])
    new_result = self._nodes_to_rich(new_children, ctx, d + 1)
    new_result.stylize(Style(color="green", bold=True))
    result.append_text(new_result)

    return result


def _render_critic_comment_node(self, n, ctx, d):
    """CriticCommentNode 渲染：批注样式（dim+italic+yellow-ish角标）。"""
    result = self._nodes_to_rich(n.children, ctx, d + 1)
    result.stylize(Style(dim=True, italic=True, color="bright_black"))
    # 添加批注标记前缀/后缀
    wrapped = Text()
    wrapped.append("┌[批注]", style=Style(dim=True, color="yellow"))
    wrapped.append_text(result)
    wrapped.append("┘", style=Style(dim=True, color="yellow"))
    return wrapped


def _render_color_text_node(self, n, ctx, d):
    """ColorTextNode 处理器：用指定颜色渲染文本。"""
    color = n.color or "white"
    return _style_children(self, n, ctx, d + 1, Style(color=color, bold=True))


def _render_wikilink_node(self, n, ctx, d):
    """WikiLinkNode 处理器：渲染为紫色虚线链接样式。

    [[target]] 显示 target，[[target|display]] 显示 display。
    """
    display = n.display or n.target
    result = Text(display, style=Style(color="bright_magenta", underline=True, italic=False))
    return result


def _render_inline_comment_node(self, n, ctx, d):
    """InlineCommentNode 处理器：渲染为极淡隐藏文本。"""
    return Text(n.content or "", style=Style(dim=True, color="bright_black", italic=True))

# ── 构建 InlineRenderer 的节点类型→处理函数调度表（模块加载时一次性构建） ──
# 注意：此代码在模块加载时执行，向 InlineRenderer._NODE_DISPATCH 注入条目。
# 因此 _inline_handlers 必须在 inline_renderer 之后被导入。

def _build_dispatch_table():
    """构建节点类型 → 处理函数的 O(1) 查找表。

    在 InlineRenderer 类定义完成、内联节点模块已导入后调用。
    """
    # 导入失败检查：如果节点类型全部是同一个占位类，调度表将完全失效
    if not _LAZY_IMPORT_OK:
        raise ImportError(
            "内联渲染节点导入失败（_LAZY_IMPORT_OK=False），"
            "请检查 inline_parser.py 及其依赖模块是否正常"
        )
    d = {}
    d[_TextNode] = _text_node_handler
    d[_KbdNode] = lambda self, n, ctx, d: Text(
        f" ⌨{n.content} ",
        style=Style(color="bright_white", bgcolor="grey30", bold=True),
    )
    d[_InlineCodeNode] = _inline_code_handler
    d[_AbbrNode] = _abbr_node_handler
    d[_LinkNode] = _link_node_handler
    def _image_node_handler(self, n, ctx, d):
        dim = ""
        w = n.meta.get("width", 0)
        h = n.meta.get("height", 0)
        if w and h:
            dim = f" ={w}x{h}"
        url_text = n.url[:50] + '...' if len(n.url) > 50 else n.url
        title_text = f" \"{n.title}\"" if getattr(n, 'title', '') else ""
        return Text(
            f"🖼️ {n.content or 'image'} ({url_text}{dim}){title_text}",
            style=Style(color="magenta", dim=True))

    d[_ImageNode] = _image_node_handler
    d[_SubscriptNode] = _render_subscript_node
    d[_SuperscriptNode] = _render_superscript_node
    d[_AutoLinkNode] = lambda self, n, ctx, d: Text(n.url, style=Style(color="cyan", underline=True))
    d[_AutoLinkEmailNode] = lambda self, n, ctx, d: Text(n.email, style=Style(color="cyan", underline=True, italic=True))
    d[_LineBreakNode] = lambda self, n, ctx, d: Text("\n")
    d[_InlineMathNode] = _inline_math_handler
    d[_FootnoteRefNode] = _footnote_ref_handler

    d[_BoldNode] = lambda self, n, ctx, d: _style_children(self, n, ctx, d + 1, Style(bold=True))
    d[_ItalicNode] = lambda self, n, ctx, d: _style_children(self, n, ctx, d + 1, Style(italic=True))
    d[_BoldItalicNode] = lambda self, n, ctx, d: _style_children(self, n, ctx, d + 1, Style(bold=True, italic=True))
    d[_UnderlineNode] = lambda self, n, ctx, d: _style_children(self, n, ctx, d + 1, Style(underline=True))
    d[_StrikethroughNode] = _render_strikethrough_handler
    d[_HighlightNode] = lambda self, n, ctx, d: _style_children(self, n, ctx, d + 1, Style(bgcolor="yellow", color="black", bold=True))  # 高亮：黄底黑字+粗体
    d[_SpoilerNode] = _spoiler_node_handler  # 剧透：字符掩码 ████
    d[_CriticAdditionNode] = lambda self, n, ctx, d: _style_children(
        self, n, ctx, d + 1, Style(color="green", bgcolor="dark_green", bold=True)
    )  # CriticMarkup 添加：绿底绿字+粗体
    d[_CriticDeletionNode] = lambda self, n, ctx, d: _render_strikethrough_handler(
        self, n, ctx, d
    )  # CriticMarkup 删除：红色删除线（复用 strikethrough 处理器）
    d[_SmallTextNode] = lambda self, n, ctx, d: _style_children(
        self, n, ctx, d + 1, Style(dim=True, italic=True)
    )  # 小号文本：dim + 斜体
    d[_ColorTextNode] = _render_color_text_node  # 彩色文本：直接使用颜色名
    d[_CriticSubstitutionNode] = _render_critic_substitution_node
    d[_CriticCommentNode] = _render_critic_comment_node
    d[_WikiLinkNode] = _render_wikilink_node  # Wiki 链接：紫色虚线样式
    d[_InlineCommentNode] = _render_inline_comment_node  # 行内注释：极淡隐藏文本
    # 默认 fallback：对未知节点类型降级为纯文本输出
    # 注意：inline_renderer._node_to_rich 中已有 isinstance(node, InlineNode) fallback，
    # 此处注册 InlineNode 基类处理器作为额外安全网。
    d[_InlineNode] = lambda self, n, ctx, d: Text(n.content) if n.content else Text("")
    return d
