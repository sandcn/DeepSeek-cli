"""内联渲染节点类型 → 处理函数调度表。

所有节点处理器函数集中在此模块，避免 inline_renderer.py 中
模块级代码与类定义混合，便于维护和扩展。
"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._inline_preprocess import _preprocess_text
from ._inline_links import normalize_ref_label as _normalize_ref_label
from src.presentation_data import LiveMapping, circled_digits


# ── 模块级调度器辅助函数（避免 lambda 闭包问题） ──────────

def _inline_math_handler(self, node, ctx, _depth):
    """InlineMathNode 调度器：延迟导入 MathRenderer 避免循环导入。"""
    from .math_renderer import MathRenderer
    return MathRenderer().render_inline(node.content)


def _mathml_node_handler(self, node, ctx, _depth):
    """MathMLNode 调度器：MathML → LaTeX（延迟导入 ANSI 转换器）→ 公式渲染。

    转换不可用（内容非 MathML）时回退原文样式文本，内容不丢。
    """
    source = node.content or ""
    latex = None
    try:
        from .ansi._mathml import mathml_to_latex
        latex = mathml_to_latex(source)
    except Exception:
        latex = None
    if not latex:
        return Text(source, style=Style(color="bright_magenta", italic=True))
    from .math_renderer import MathRenderer
    return MathRenderer().render_inline(latex)


def _progress_node_handler(self, node, ctx, _depth):
    """``<progress>`` / ``<meter>`` → 文本进度条（``[████░░░░] 70%``）。"""
    meta = node.meta or {}
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
    width = 16
    filled = int(round(ratio * width))
    color = "green" if ratio >= 0.67 else ("yellow" if ratio >= 0.34 else "red")
    bar = Text("[")
    bar.append("█" * filled, style=Style(color=color))
    bar.append("░" * (width - filled), style=Style(color="grey37"))
    bar.append(f"] {ratio * 100:.0f}%")
    return bar


def _footnote_ref_handler(self, node, ctx, _depth):
    """FootnoteRefNode 调度器：按引用先后编号并渲染。

    ★ 修复（review 方向）：编号改为「ref_id 在 fn_order 中的位置 +1」，
    与 render_footnotes 的脚注列表排序同源——修复前用独立计数器按引用
    顺序编号、而列表按**定义**顺序（fn_order 在定义处填充）排列，引用
    顺序与定义顺序不一致时 [n] 指向错误的脚注条目。引用处同步补录
    fn_order（首个出现即定序：引用或定义谁先出现谁先编号）。
    """
    if ctx:
        if node.ref_id not in ctx.fn_order:
            ctx.fn_order.append(node.ref_id)
        fn_num = ctx.fn_order.index(node.ref_id) + 1
    else:
        fn_num = 0
    return Text(
        f"[{fn_num}]" if ctx else f"[^{node.ref_id}]",
        style=Style(color="bright_cyan", italic=True, bold=True),
    )


def _inline_footnote_handler(self, node, ctx, _depth):
    """InlineFootnoteNode 调度器（行内脚注 ``^[文本]``）。

    正文渲染为序号 ``[n]``；脚注内容以「归一化内容」为键注册进
    ``ctx.fn_map``（幂等：内容相同的行内脚注合并编号与条目，重复渲染不重复
    追加），供文末脚注列表输出。
    """
    content = (node.content or "").strip()
    if not content:
        return Text("")
    if ctx is None:
        # 无渲染上下文：保留原语法文本，不臆造编号
        return Text(f"[^{content}]", style=Style(dim=True))
    ref_id = "inline:" + content
    fn_map = getattr(ctx, "fn_map", None)
    if fn_map is not None:
        fn_map[ref_id] = content
    num = 0
    fn_order = getattr(ctx, "fn_order", None)
    if fn_order is not None:
        if ref_id not in fn_order:
            fn_order.append(ref_id)
        num = fn_order.index(ref_id) + 1
    return Text(
        f"[{num}]",
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


#: 行内属性 span 类名 → Rich 样式（与 ANSI 路径 ``inline._SPAN_CLASS_STYLES``
#: 语义对齐：仅覆盖有明确终端语义的类名，未知类名忽略）。
_SPAN_CLASS_RICH = {
    "red": Style(color="red"), "green": Style(color="green"),
    "blue": Style(color="blue"), "yellow": Style(color="yellow"),
    "cyan": Style(color="cyan"), "magenta": Style(color="magenta"),
    "white": Style(color="white"), "black": Style(color="black"),
    "gray": Style(color="grey50"), "grey": Style(color="grey50"),
    "bold": Style(bold=True), "strong": Style(bold=True),
    "italic": Style(italic=True), "em": Style(italic=True),
    "underline": Style(underline=True), "ins": Style(underline=True),
    "strike": Style(strike=True), "strikethrough": Style(strike=True),
    "del": Style(strike=True), "highlight": Style(reverse=True),
    "mark": Style(reverse=True), "code": Style(color="bright_green"),
    "mono": Style(color="bright_green"), "monospace": Style(color="bright_green"),
    "small": Style(dim=True), "big": Style(bold=True), "large": Style(bold=True),
    "dim": Style(dim=True), "muted": Style(dim=True),
    "error": Style(color="red", bold=True), "danger": Style(color="red", bold=True),
    "warning": Style(color="yellow"), "warn": Style(color="yellow"),
    "success": Style(color="green"), "ok": Style(color="green"),
    "info": Style(color="cyan"),
}


def _span_node_handler(self, n, ctx, d):
    """SpanNode 处理器：按 ``.class`` 映射叠加样式（未知类名忽略）。"""
    style = None
    for cls in (n.meta or {}).get("classes") or ():
        extra = _SPAN_CLASS_RICH.get(str(cls).lower())
        if extra is None:
            continue
        style = extra if style is None else Style.combine([style, extra])
    if n.children:
        result = self._nodes_to_rich(n.children, ctx, d)
    else:
        result = Text(n.content or "")
    if style:
        result.stylize(style)
    return result


def _citation_node_handler(self, n, ctx, d):
    """CitationNode 处理器：``[@key]`` → ``[key]`` 标记（琥珀斜体）。"""
    keys = (n.meta or {}).get("keys") or []
    if not keys:
        return Text(n.content or "")
    return Text("[" + "; ".join(keys) + "]", style=Style(color="yellow", italic=True))


# ── 上下标 Unicode 渲染辅助函数 ──────────────────────────

_SUB_SCRIPT_MAP = LiveMapping("inline_subscript")

_SUPER_SCRIPT_MAP = LiveMapping("inline_superscript")

_CIRCLED_DIGITS = LiveMapping("circled_digits")


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
        MathMLNode as _MathMLNode,
        ProgressNode as _ProgressNode,
        FootnoteRefNode as _FootnoteRefNode,
        InlineFootnoteNode as _InlineFootnoteNode,
        AutoLinkNode as _AutoLinkNode,
        AutoLinkEmailNode as _AutoLinkEmailNode,
        SpoilerNode as _SpoilerNode,
        CriticAdditionNode as _CriticAdditionNode,
        CriticDeletionNode as _CriticDeletionNode,
        CriticHighlightNode as _CriticHighlightNode,
        CriticSubstitutionNode as _CriticSubstitutionNode,
        CriticCommentNode as _CriticCommentNode,
        SmallTextNode as _SmallTextNode,
        BigTextNode as _BigTextNode,
        QuotedNode as _QuotedNode,
        ColorTextNode as _ColorTextNode,
        LineBreakNode as _LineBreakNode,
        WikiLinkNode as _WikiLinkNode,
        InlineCommentNode as _InlineCommentNode,
        SpanNode as _SpanNode,
        CitationNode as _CitationNode,
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
        # ★ 标签归一化后查表（与解析层写入键口径一致：大小写不敏感 + 空白折叠）
        resolved = ctx.ref_map.get(ref_id) or ctx.ref_map.get(_normalize_ref_label(ref_id))
        label = str((getattr(n, "meta", None) or {}).get("label") or ref_id)
        if resolved:
            actual_url, title = resolved
            result.stylize(Style(color="cyan", underline=True))
            # ── 圈数字编号（基于 ref_map 插入顺序） ────────────
            ref_keys = list(ctx.ref_map.keys())
            try:
                ref_idx = ref_keys.index(ref_id) + 1  # 1-based
            except ValueError:
                ref_idx = ref_keys.index(_normalize_ref_label(ref_id)) + 1
            digits = circled_digits()
            circled = digits[ref_idx - 1] if ref_idx <= len(digits) else f"[{ref_idx}]"
            result.append(f" {circled}", style=Style(dim=True, color="bright_black"))
            # ── URL 行尾悬停提示 ──────────────────────────────
            result.append(f" ({actual_url})", style=Style(dim=True, color="bright_black"))
            return result
        else:
            # 快捷引用式链接 ``[ref]`` 未命中定义 → 原样输出（不误改方括号文本）
            if getattr(n, "meta", None) and n.meta.get("shortcut"):
                return Text(f"[{label}]")
            # 未解析的参考链接：黄色高亮 + 显示 ref_id
            result.stylize(Style(color="yellow", italic=True))
            result.append(f"[?{label}]", style=Style(dim=True, color="bright_black"))
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


def _render_critic_highlight_node(self, n, ctx, d):
    """CriticHighlightNode 渲染：黄底黑字高亮（与 ``==x==`` 同行内视觉）。"""
    return _style_children(self, n, ctx, d + 1,
                           Style(bgcolor="yellow", color="black", bold=True))


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


def _render_quoted_node(self, n, ctx, d):
    """``<q>`` 短引用：内容两侧加中文引号（保留内部行内样式）。"""
    inner = _style_children(self, n, ctx, d + 1, Style(color="bright_white", italic=True))
    return Text("\u300c") + inner + Text("\u300d")


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
        f" {n.content} ",
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
        url = n.url or ""
        title = getattr(n, 'title', '')
        ref_placeholder = ""
        # ★ 快捷 / 折叠引用式图片未命中定义 → 回退原文（与 ANSI 路径一致）
        shortcut = bool(n.meta.get("shortcut"))
        collapsed = bool(n.meta.get("collapsed"))
        # 参考式图片 ``![alt][ref]``：url 为 ``[ref:id]`` 占位 → 查定义表展开
        if url.startswith('[ref:') and ctx:
            ref_id = url[5:-1]
            ref_map = getattr(ctx, 'ref_map', None) or {}
            resolved = (ref_map.get(ref_id)
                        or ref_map.get(_normalize_ref_label(ref_id)))
            if resolved:
                try:
                    url, ref_title = resolved
                    title = title or ref_title
                except (TypeError, ValueError):
                    url = ""
            elif shortcut:
                return Text(f"![{n.content}][]" if collapsed
                            else f"![{n.content}]")
            else:
                ref_placeholder = f"[ref:{n.meta.get('label') or ref_id}]"
                url = ""
        elif url.startswith('[ref:'):
            if shortcut:
                return Text(f"![{n.content}][]" if collapsed
                            else f"![{n.content}]")
            ref_placeholder = url
            url = ""
        url_text = url[:50] + '...' if len(url) > 50 else url
        title_text = f" \"{title}\"" if title else ""
        if url_text:
            shown = f" ({url_text}{dim})"
        elif ref_placeholder:
            shown = f" ({ref_placeholder})"
        else:
            shown = ""
        return Text(
            f"🖼️ {n.content or 'image'}{shown}{title_text}",
            style=Style(color="magenta", dim=True))

    d[_ImageNode] = _image_node_handler
    d[_SubscriptNode] = _render_subscript_node
    d[_SuperscriptNode] = _render_superscript_node
    d[_AutoLinkNode] = lambda self, n, ctx, d: Text(n.url, style=Style(color="cyan", underline=True))
    d[_AutoLinkEmailNode] = lambda self, n, ctx, d: Text(n.email, style=Style(color="cyan", underline=True, italic=True))
    d[_LineBreakNode] = lambda self, n, ctx, d: Text("\n")
    d[_InlineMathNode] = _inline_math_handler
    d[_MathMLNode] = _mathml_node_handler
    d[_ProgressNode] = _progress_node_handler
    d[_FootnoteRefNode] = _footnote_ref_handler
    d[_InlineFootnoteNode] = _inline_footnote_handler

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
    d[_CriticHighlightNode] = _render_critic_highlight_node  # CriticMarkup 高亮：黄底黑字
    d[_SmallTextNode] = lambda self, n, ctx, d: _style_children(
        self, n, ctx, d + 1, Style(dim=True, italic=True)
    )  # 小号文本：dim + 斜体
    d[_BigTextNode] = lambda self, n, ctx, d: _style_children(
        self, n, ctx, d + 1, Style(bold=True)
    )  # ``<big>`` 放大文本：终端以粗体近似
    d[_QuotedNode] = _render_quoted_node  # ``<q>`` 短引用：中文引号包裹
    d[_ColorTextNode] = _render_color_text_node  # 彩色文本：直接使用颜色名
    d[_CriticSubstitutionNode] = _render_critic_substitution_node
    d[_CriticCommentNode] = _render_critic_comment_node
    d[_WikiLinkNode] = _render_wikilink_node  # Wiki 链接：紫色虚线样式
    d[_InlineCommentNode] = _render_inline_comment_node  # 行内注释：极淡隐藏文本
    d[_SpanNode] = _span_node_handler  # 行内属性 span：按 .class 映射样式
    d[_CitationNode] = _citation_node_handler  # 引用 citation：[@key] → [key]
    # 默认 fallback：对未知节点类型降级为纯文本输出
    # 注意：inline_renderer._node_to_rich 中已有 isinstance(node, InlineNode) fallback，
    # 此处注册 InlineNode 基类处理器作为额外安全网。
    d[_InlineNode] = lambda self, n, ctx, d: Text(n.content) if n.content else Text("")
    return d
