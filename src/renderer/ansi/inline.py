"""行内格式 — 复用递归下降内联解析器（``_InlineParser``）→ Run 序列。

统一真源：与解析层共用 ``src.renderer.inline_parser._InlineParser``
（字符级递归下降、无正则），解析结果（InlineNode 树）经本模块的节点渲染
调度表转换为 ANSI ``Run`` 列表。新增内联语法只需扩展解析器 + 本节点表，
两条渲染路径自动同步（开闭原则）。

支持的内联语法（与解析层同源）：
  粗体 ``**x**`` / ``__x__``、斜体 ``*x*`` / ``_x_``、粗斜体 ``***x***``、
  行内码 ``` `x` ```、删除线 ``~~x~~``、高亮 ``==x==``、上标 ``^x^``、
  下标 ``~x~``、下划线 ``++x++``、剧透 ``||x||``、链接 ``[t](url)``、
  参考式链接 ``[t][ref]``、图片 ``![alt](url)``、自动链接 ``<url>``、
  邮箱 ``<a@b.com>`` / 裸邮箱、脚注引用 ``[^id]``、行内数学 ``$x$`` /
  ``\\(x\\)``、HTML `<math>`（MathML，转 LaTeX 后紧凑排版）、
  HTML `<progress>` / `<meter>`（终端进度条）、Emoji ``:name:``、
  反斜杠转义、``<br>`` 硬换行、
  维基链接 ``[[page]]``、CriticMarkup ``{-- --}{++ ++}{~~ ~> ~~}{>> <<}``、
  小字 ``{-x-}``、着色 ``{color:red}x{color}``、行内注释 ``%%x%%``、
  HTML 标签/注释/实体、缩写定义 ``*[ABBR]: ...`` 自动替换。

性能契约（超长单行 / 英文文本）：纯文本（不产生任何行内格式节点）经
``text_has_inline_markup`` 快速判否后直接返回单 Run，不进入解析器；解析器
自身按「兴趣位置表」二分跳过整段普通文本（非逐字符扫描，且普通字母
``h/f/w`` 不再逐个尝试裸 URL 判定）——英文文本中 ``h/f/w`` 出现频率约 10%，
修复前几乎每 10 个字符就产生一次无效的格式尝试。判否口径与兴趣位置表完全
一致，故判定为「无标记」时产出与解析器路径等价。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Literal

from .style import Style
from .helpers import Run, AnsiLine
from .._inline_links import normalize_ref_label as _normalize_ref_label
from src.presentation_data import LiveMapping


# ═══════════════════════════════════════════════════════════
# 渲染上下文（脚注编号 / 参考式链接 / 缩写替换）
# ═══════════════════════════════════════════════════════════
#
# 内联渲染深嵌在块级调用链中（engine → blocks → inline），逐层透传 ``ctx``
# 会污染所有中间函数签名并破坏既有调用契约。改用 ``ContextVar`` 承载当前
# 渲染上下文：渲染入口（``AnsiRenderEngine.render`` / 预览刷新）设置一次，
# 内部所有 ``render_inline`` 调用自动取用；显式传入的 ``ctx`` 参数优先。
_RENDER_CTX: ContextVar = ContextVar("ansi_render_ctx", default=None)


class _RenderContextScope:
    """``use_render_context`` 的上下文管理器（栈式 set/reset）。"""

    __slots__ = ("_token",)

    def __init__(self, ctx) -> None:
        self._token = _RENDER_CTX.set(ctx)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> Literal[False]:
        _RENDER_CTX.reset(self._token)
        return False


def use_render_context(ctx) -> "_RenderContextScope":
    """在 ``with`` 块内设置当前渲染上下文（返回栈式作用域）。"""
    return _RenderContextScope(ctx)


def current_render_context():
    """当前渲染上下文（未设置时为 None）。"""
    return _RENDER_CTX.get()


# ═══════════════════════════════════════════════════════════
# 行内样式常量（避免每个 run 重建 frozen dataclass）
# ═══════════════════════════════════════════════════════════
_STYLE_CODE = Style(fg=46, bold=True)
_STYLE_BOLD = Style(bold=True)
_STYLE_ITALIC = Style(italic=True)
_STYLE_BOLD_ITALIC = Style(bold=True, italic=True)
_STYLE_UNDERLINE = Style(underline=True)
_STYLE_STRIKE = Style(dim=True)
_STYLE_HIGHLIGHT = Style(bg=11, fg=0, bold=True)
_STYLE_SPOILER = Style(fg=240, dim=True)
_STYLE_SUB = Style(dim=True, italic=True)
_STYLE_SUP = Style(fg=45, italic=True)
_STYLE_MATH = Style(fg=213, italic=True)
_STYLE_LINK = Style(fg=45, underline=True)
_STYLE_EMAIL = Style(fg=45, underline=True, italic=True)
_STYLE_FOOTNOTE = Style(fg=45, bold=True, italic=True)
_STYLE_KBD = Style(fg=231, bg=240, bold=True)
_STYLE_ABBR = Style(fg=220, underline=True, italic=True)
_STYLE_CRIT_ADD = Style(fg=40, bg=22, bold=True)
_STYLE_CRIT_DEL = Style(fg=203, dim=True)
#: CriticMarkup 高亮 ``{==…==}``：黄底黑字加粗（与 ``==x==`` 行内高亮同视觉）
_STYLE_CRIT_HL = Style(bg=11, fg=0, bold=True)
_STYLE_SMALL = Style(dim=True, italic=True)
_STYLE_BIG = Style(bold=True)
_STYLE_QUOTE = Style(fg=252, italic=True)
_STYLE_WIKI = Style(fg=201, underline=True)
_STYLE_COMMENT = Style(fg=240, dim=True, italic=True)
_STYLE_IMAGE = Style(fg=201, dim=True)
#: 引用 citation ``[@key]``（Pandoc）：琥珀斜体标记
_STYLE_CITATION = Style(fg=180, italic=True)

#: 行内属性 span 的类名 → 样式映射（``[文本]{.red}``）。
#: 仅覆盖有明确终端语义的类名；未知类名忽略（内容照常渲染，不丢文本）。
_SPAN_CLASS_STYLES: dict = {
    "red": Style(fg=196), "green": Style(fg=40), "blue": Style(fg=33),
    "yellow": Style(fg=220), "cyan": Style(fg=44), "magenta": Style(fg=201),
    "white": Style(fg=231), "black": Style(fg=0), "orange": Style(fg=214),
    "purple": Style(fg=93), "pink": Style(fg=213),
    "gray": Style(fg=244), "grey": Style(fg=244),
    "bold": _STYLE_BOLD, "strong": _STYLE_BOLD,
    "italic": _STYLE_ITALIC, "em": _STYLE_ITALIC,
    "underline": _STYLE_UNDERLINE, "ins": _STYLE_UNDERLINE,
    "strike": _STYLE_STRIKE, "strikethrough": _STYLE_STRIKE,
    "del": _STYLE_STRIKE,
    "highlight": _STYLE_HIGHLIGHT, "mark": _STYLE_HIGHLIGHT,
    "code": _STYLE_CODE, "mono": _STYLE_CODE, "monospace": _STYLE_CODE,
    "small": _STYLE_SMALL, "big": _STYLE_BIG, "large": _STYLE_BIG,
    "smallcaps": Style(bold=True), "small-caps": Style(bold=True),
    "dim": Style(dim=True), "muted": Style(dim=True),
    "kbd": _STYLE_KBD, "spoiler": _STYLE_SPOILER,
    "error": Style(fg=196, bold=True), "danger": Style(fg=196, bold=True),
    "warning": Style(fg=220), "warn": Style(fg=220),
    "success": Style(fg=40), "ok": Style(fg=40), "info": Style(fg=45),
}
#: 行内进度条（``<progress>`` / ``<meter>``）配色与宽度
_STYLE_PROGRESS_FILL = Style(fg=41, bold=True)
_STYLE_PROGRESS_MID = Style(fg=220, bold=True)
_STYLE_PROGRESS_LOW = Style(fg=203, bold=True)
_STYLE_PROGRESS_EMPTY = Style(fg=238)
_STYLE_PROGRESS_TEXT = Style(fg=252)
_PROGRESS_WIDTH = 24

#: 最大递归深度（与解析器同量级，防异常嵌套 RecursionError）
_MAX_DEPTH = 32


class InlineBlock:
    """行内多行块（二维公式等）：行列表 + 基线行号。

    由行内公式节点（``_emit_math``）产出；``inline_lines`` 识别后与同一
    行组的其它文本按**基线**水平拼接（公式两侧文本落在公式基线上，公式
    的其它行单独占行）。不认识 block 的消费者使用 ``Run.text``（展平降级
    文本），内容不丢。
    """

    __slots__ = ("lines", "baseline")

    def __init__(self, lines: list[AnsiLine], baseline: int = 0) -> None:
        self.lines = lines or []
        self.baseline = max(0, int(baseline))

#: ``base.merge(style)`` 结果缓存：``base`` 取值集合有限（默认样式 / 标题 /
#: 表格单元格等），缓存后同一基础样式的合并结果跨帧复用（免每次构造 frozen
#: dataclass）。有界，超限清空。
_MERGE_CACHE: dict = {}
_MERGE_CACHE_MAX = 512


def _merge(base: Style, style: Style) -> Style:
    """``base.merge(style)`` 的缓存版本（样式对象不可变，可安全复用）。"""
    key = (base, style)
    merged = _MERGE_CACHE.get(key)
    if merged is None:
        merged = base.merge(style)
        if len(_MERGE_CACHE) >= _MERGE_CACHE_MAX:
            _MERGE_CACHE.clear()
        _MERGE_CACHE[key] = merged
    return merged


#: 颜色名 → 256 色号（``{color:NAME}`` 着色语法；与解析器白名单一致）
_COLOR_TO_256: dict[str, int] = {
    "red": 196, "green": 40, "blue": 33, "yellow": 220,
    "cyan": 44, "magenta": 201, "white": 231, "black": 0,
    "grey30": 240, "grey50": 244,
    "bright_red": 203, "bright_green": 84, "bright_blue": 75,
    "bright_yellow": 227, "bright_cyan": 87, "bright_magenta": 213,
    "bright_white": 231, "orange1": 214, "purple": 93, "pink1": 213,
}

#: Unicode 上下标映射（真源 == 表现层数据注册表；缺失时回退样式文本）
_SUB_SCRIPT_MAP = LiveMapping("inline_subscript")
_SUPER_SCRIPT_MAP = LiveMapping("inline_superscript")


#: 智能排版（typographer）多字符触发对：文本含其中任意一项才可能发生替换
#: （``--``→en/em-dash、``...``→省略号、``->``/``<-``/``=>``/``<->``/``==>``
#: /``<==``→箭头、``<=``/``>=``/``!=``/``~=``/``+-``/``+/-``→数学符号）。
#: 单字符触发（``&`` 实体 / ``:`` emoji / ``/`` 分数 / ``(c)`` 等）另行判定。
_TYPO_PAIRS = (
    '--', '...', '->', '<-', '=>', '<=', '>=', '!=', '~=', '+-', '+/-',
)

#: 智能排版增量扫描的向前回看长度（覆盖跨增量边界的 pattern：最长 pair 为
#: 3 字符 ``+/-``）。
_TYPO_LOOKBACK = 3


def _needs_typography(text: str) -> bool:
    """文本是否**可能**被智能排版预处理改写（快速判否）。

    返回 ``False`` 时 ``_preprocess_text(text) == text``（无需进入预处理）。
    判定**宁可多报**（多报只多一次预处理，不改变输出），覆盖
    ``_inline_preprocess`` 的全部替换规则：破折号/省略号（``--`` ``...``）、
    箭头（``->`` ``<-`` ``=>`` ``<->`` ``==>`` ``<==``）、数学符号
    （``<=`` ``>=`` ``!=`` ``~=`` ``+-`` ``+/-``）、版权/商标（``(c)``
    ``(r)`` ``(tm)``）、分数（``N/M``）、Emoji 短代码（``:name:``）与 HTML
    实体（``&…;``）。

    ★ 性能：单字符 ``-`` / ``+`` / ``=`` 不单独触发（英文文本中连字符、
    ``+`` 号极常见，如 ``re-render``、``C++``）——仅在上表的**多字符组合**
    出现时才进入预处理，避免英文长段落每帧做 4 遍全量扫描（累计 O(n²)）。
    """
    for pair in _TYPO_PAIRS:
        if pair in text:
            return True
    if '&' in text or ':' in text:
        return True
    if '/' in text:
        # 分数 ``N/M`` 才触发（``and/or``、``I/O`` 等普通斜杠不触发——英文
        # 文本中斜杠并不罕见，一律判真会让快路径失效并拖慢长段落流式渲染）。
        i = text.find('/')
        while i >= 0:
            if (i > 0 and text[i - 1].isdigit()
                    and i + 1 < len(text) and text[i + 1].isdigit()):
                return True
            i = text.find('/', i + 1)
    if '(' in text:
        low = text.lower()
        if '(c)' in low or '(r)' in low or '(tm)' in low:
            return True
    return False


#: 智能排版预处理函数（惰性绑定：仅在实际需要预处理时导入一次，避免每次
#: 调用都走 ``import`` 查找）。
_PREPROCESS_TEXT = None


def _last_typo_pos(text: str) -> int:
    """``text`` 中最后一个智能排版触发**起点**的下标（无则 -1）。

    供段落预览的「活动行窗口是否纯文本」判定做**增量**维护（见
    ``AnsiStreamRenderer._note_paragraph_triggers`` / ``_plain_active_window``）
    ——避免每帧对 4096 字符窗口重扫。覆盖 ``_preprocess_text`` 的全部替换起点：
    ``--`` / ``...`` / ``->`` / ``<-`` / ``=>`` / ``<=`` / ``>=`` / ``!=`` /
    ``~=`` / ``+-`` / ``+/-``、``(c)`` / ``(r)`` / ``(tm)``、``N/M`` 分数、
    ``:emoji:``、``&entity;``——判定口径**宁可多报**（多报只多一次预处理）。

    实现用 C 级 ``str.rfind`` 逐个候选收集（每个 pattern 一次反向 memchr），
    比逐字符 Python 循环快得多。
    """
    pos = -1
    for pair in _TYPO_PAIRS:
        idx = text.rfind(pair)
        if idx > pos:
            pos = idx
    for ch in '&:(':
        idx = text.rfind(ch)
        if idx > pos:
            pos = idx
    i = text.rfind('/')
    while i > 0:
        if text[i - 1].isdigit():
            if i - 1 > pos:
                pos = i - 1
            break
        i = text.rfind('/', 0, i)
    return pos


def _preprocess_plain(text: str) -> str:
    """智能排版预处理（与 ``_preprocess_text`` 同源同产出）。

    TUI 内容路径此前完全不做智能排版——同一段 Markdown 经历史渲染实现为
    ``a → b`` / ``x — y``，经 TUI 路径却原样保留 ``a -> b`` / ``x --- y``，
    两条路径语义分裂。现按 TextNode 粒度统一预处理（代码/链接 URL 等不经
    TextNode 的内容不受影响，与解析层 ``_text_node_handler`` 一致）。
    """
    if not text or not _needs_typography(text):
        return text
    global _PREPROCESS_TEXT
    if _PREPROCESS_TEXT is None:
        from src.renderer._inline_preprocess import _preprocess_text as _fn
        _PREPROCESS_TEXT = _fn
    return _PREPROCESS_TEXT(text)


def _append(out: list[Run], text: str, style: Style | None,
            link: str | None = None) -> None:
    """追加 Run 并合并相邻同样式（且同链接）段（输出紧凑 + 宽度缓存友好）。

    带 ``block`` 的 Run（行内多行块）**不参与合并**：它承载结构信息，一旦
    与普通文本合并会丢失多行布局。
    """
    if not text:
        return
    if (out and out[-1].style == style
            and getattr(out[-1], "link", None) == link
            and getattr(out[-1], "block", None) is None):
        out[-1] = Run(out[-1].text + text, style, link)
        return
    out.append(Run(text, style, link))


# ═══════════════════════════════════════════════════════════
# 节点 → Run 渲染
# ═══════════════════════════════════════════════════════════


class RunCollector(list):
    """Run 列表（``render_inline`` 的内部产出），附带**源区间**信息。

    源区间供表格排版使用：单元格里的 HTML 内联块（``<span style="color:red">``
    / ``<mark>`` / ``<font>``）在解析层是「透明容器」（属性解析后内容以纯文本
    ``TextNode`` 返回），若按单元格整体调色会整格上色。渲染层可据
    ``source_markup`` 找到原文中的标签、解析其样式，再按行间映射把样式只施加
    到标签覆盖的字符上。

    Attributes:
        source: 解析所用的**源文本**（``render_inline`` 的输入）。
        source_markup: 文本是否含 HTML 标签（``<`` 且其后为字母/``/``）——
            仅此时渲染层才需要做标签定位与样式映射（普通文本零成本）。
    """

    __slots__ = ("source", "source_markup")

    def __init__(self, iterable=(), source: str = "",
                 source_markup: bool = False) -> None:
        super().__init__(iterable)
        self.source = source
        self.source_markup = source_markup


def _nodes_have_html_tag(nodes) -> bool:
    """节点树中是否含**原始内容以标签开头**的节点（表格行间映射的必要条件）。

    HTML 语义标签（``<sup>`` / ``<code>`` / ``<kbd>`` / ``<abbr>``）解析出的
    节点，其 ``content`` 是标签内部文本（不以 ``<`` 开头）；含**嵌套结构**
    的标签（``<span style="color:red"><b>x</b></span>``）则由容器节点保存
    「渲染后纯文本」，其中可能夹带标签原文。此处递归检查节点子树，只要出现
    以 ``<`` 开头的原始内容即认为需要行间映射（判定可保守多报——多报只会
    让表格做出一次无害的标签扫描）。
    """
    for node in nodes or ():
        content = getattr(node, "content", "") or ""
        if content.lstrip().startswith("<"):
            return True
        if _nodes_have_html_tag(getattr(node, "children", None)):
            return True
        meta = getattr(node, "meta", None) or {}
        if _nodes_have_html_tag(meta.get("new_children")):
            return True
    return False


def _emit_nodes(nodes, base: Style, ctx, out: list[Run], depth: int) -> None:
    """渲染 InlineNode 列表为 Run（按顺序追加）。

    产出对象是 ``RunCollector``（``list`` 子类）时，一并记录源文本与
    「是否含 HTML 标签」——供表格排版只给标签覆盖的字符上色（见
    ``RunCollector``）。
    """
    for node in nodes or ():
        _emit_node(node, base, ctx, out, depth)
    return out


def _node_children(node):
    """取可嵌套节点的子节点列表（叶子节点返回 None）。"""
    children = getattr(node, "children", None)
    return children if children else None


def _emit_children(node, base: Style, ctx, out: list[Run], depth: int) -> None:
    children = _node_children(node)
    if children:
        _emit_nodes(children, base, ctx, out, depth + 1)
    elif getattr(node, "content", ""):
        _append(out, node.content, base)


def _emit_text(node, base, ctx, out, depth):
    text = _preprocess_plain(node.content or "")
    abbr_map = getattr(ctx, "abbr_map", None) if ctx is not None else None
    if text and abbr_map:
        _emit_text_with_abbr(text, base, abbr_map, out)
    else:
        _append(out, text, base)


def _emit_text_with_abbr(text: str, base: Style, abbr_map: dict, out: list[Run]) -> None:
    """纯文本按缩写定义拆分（命中词加高亮样式，其余保持 base）。"""
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if not ch.isalnum():
            _append(out, ch, base)
            i += 1
            continue
        start = i
        while i < n and text[i].isalnum():
            i += 1
        word = text[start:i]
        if word.upper() in abbr_map:
            _append(out, word, _merge(base, _STYLE_ABBR))
        else:
            _append(out, word, base)


def _emit_bold(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_BOLD), ctx, out, depth)


def _emit_italic(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_ITALIC), ctx, out, depth)


def _emit_bold_italic(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_BOLD_ITALIC), ctx, out, depth)


def _emit_underline(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_UNDERLINE), ctx, out, depth)


def _emit_strike(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_STRIKE), ctx, out, depth)


def _emit_highlight(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_HIGHLIGHT), ctx, out, depth)


def _emit_spoiler(node, base, ctx, out, depth):
    """剧透：用 █ 掩码替换可见字符（空白保留），不显示真实内容。"""
    from src.renderer.inline_nodes import render_inline_to_text
    children = _node_children(node)
    text = render_inline_to_text(children) if children else (node.content or "")
    masked = "".join("█" if not c.isspace() else c for c in text)
    _append(out, masked, _merge(base, _STYLE_SPOILER))


def _emit_code(node, base, ctx, out, depth):
    """行内代码：``_STYLE_CODE`` 基础样式 + Pandoc 行内代码属性类名样式。

    `` `x`{.red} `` / `` `x`{.highlight} `` 的类名按行内 span 同一映射
    （``_SPAN_CLASS_STYLES``）叠加；未知类名 / ``#id`` / 键值对忽略，
    代码内容照常显示。
    """
    style = _STYLE_CODE
    classes = (getattr(node, "meta", None) or {}).get("classes") or []
    for cls in classes:
        extra = _SPAN_CLASS_STYLES.get(str(cls).lower())
        if extra is not None:
            style = style.merge(extra)
    _append(out, node.content or "", _merge(base, style))


def _emit_kbd(node, base, ctx, out, depth):
    """``<kbd>`` 键盘按键：键帽样式（前后空格着色形成键帽块）。

    修复前每个 ``<kbd>`` 前置 ``⌨`` 图标——``<kbd>Ctrl</kbd>+<kbd>C</kbd>``
    渲染为 ``⌨Ctrl+⌨C``（图标重复且与按键文本粘连）；键帽背景色已足以
    表达按键语义，图标冗余。
    """
    _append(out, f" {node.content or ''} ", _merge(base, _STYLE_KBD))


def _emit_abbr(node, base, ctx, out, depth):
    _append(out, node.content or "", _merge(base, _STYLE_ABBR))
    title = getattr(node, "title", "")
    if title:
        _append(out, f" ({title})", _merge(base, _STYLE_COMMENT))


def _emit_link(node, base, ctx, out, depth):
    url = getattr(node, "url", "") or ""
    meta = getattr(node, "meta", None) or {}
    shortcut = bool(meta.get("shortcut"))
    label = str(meta.get("label") or "")
    if url.startswith("[ref:"):
        ref_id = url[5:-1]
        ref_map = (getattr(ctx, "ref_map", None) or {}) if ctx is not None else {}
        # ★ 标签归一化（大小写不敏感 + 空白折叠 + 转义还原）后查表——与解析层
        #   写入 ref_map 的键口径一致（``[Foo][]`` 命中 ``[foo]: url``）。
        resolved = ref_map.get(ref_id) or ref_map.get(_normalize_ref_label(ref_id))
        if resolved:
            actual_url, title = resolved
            _emit_children(node, _merge(base, _STYLE_LINK), ctx, out, depth)
            keys = list(ref_map.keys())
            try:
                idx = keys.index(ref_id) + 1
            except ValueError:
                idx = keys.index(_normalize_ref_label(ref_id)) + 1
            digits = circled_digits()
            circled = digits[idx - 1] if idx <= len(digits) else f"[{idx}]"
            _append(out, f" {circled}", _merge(base, _STYLE_COMMENT))
            _append(out, f" ({actual_url})", _merge(base, _STYLE_COMMENT))
            if title:
                _append(out, f' "{title}"', _merge(base, _STYLE_COMMENT))
            return
        if shortcut:
            # 快捷引用式链接未命中定义 → 原样输出 ``[ref]``（不误改普通方括号
            # 文本）；用原始标签显示（归一化只用于查表，不改显示形态）。
            _append(out, f"[{label or ref_id}]", base)
            return
        _emit_children(node, _merge(base, _STYLE_ABBR), ctx, out, depth)
        _append(out, f"[?{label or ref_id}]", _merge(base, _STYLE_COMMENT))
        return
    # ★ 增强（OSC 8 可点击链接）：链接文本 run 携带 url —— TUI 输出层（ink）
    #   据此包裹 OSC 8 序列，现代终端中可直接点击打开（宽度计算不计入）。
    #   先渲染到临时列表再统一附加，避免 ``_append`` 的相邻合并把链接文本
    #   并进前一个同样式的非链接 run。
    if url:
        sub: list[Run] = []
        _emit_children(node, _merge(base, _STYLE_LINK), ctx, sub, depth)
        out.extend(Run(r.text, r.style, url) for r in sub)
    else:
        _emit_children(node, _merge(base, _STYLE_LINK), ctx, out, depth)
    title = getattr(node, "title", "")
    if title:
        _append(out, f' "{title}"', _merge(base, _STYLE_COMMENT))


def _plain_alt_text(text: str) -> str:
    """图片 alt 文本 → 纯文本（剥离行内强调/代码等标记，不泄漏 ``**`` / 反引号）。

    CommonMark 的 alt 是**标签内容的纯文本**（``![**粗** 图](u)`` 的 alt 为
    ``粗 图``）；修复前原样拼接 alt 源码，强调/代码标记字面上屏。
    仅当文本含可能的行内标记字符时才进入解析器（纯文本零成本）。

    ★ 含链接/图片/引用等**非格式节点**时保留原文：``![a [b]](u)`` 的 ``[b]``
    是未定义引用的字面文本（CommonMark alt 保留方括号），不能被展开成
    ``b (ref:b)`` 或丢掉方括号。
    """
    if not text:
        return text
    if not any(ch in text for ch in "*_`~[]<>!|:\\$"):
        return text
    try:
        from src.renderer.inline_parser import _InlineParser
        from src.renderer.inline_nodes import (
            LinkNode, ImageNode, WikiLinkNode, CitationNode,
            render_inline_to_text,
        )

        def _has_special(nodes) -> bool:
            for node in nodes or ():
                if isinstance(node, (LinkNode, ImageNode, WikiLinkNode,
                                     CitationNode)):
                    return True
                if _has_special(getattr(node, "children", None)):
                    return True
                if _has_special(getattr(node, "meta", {}).get("new_children")):
                    return True
            return False

        parsed = _InlineParser(text).parse()
        if _has_special(parsed):
            return text
        plain = render_inline_to_text(parsed)
    except Exception:
        return text
    return plain or text


def _emit_image(node, base, ctx, out, depth):
    url = getattr(node, "url", "") or ""
    title = getattr(node, "title", "")
    ref_placeholder = ""
    meta = getattr(node, "meta", None) or {}
    shortcut = bool(meta.get("shortcut"))
    collapsed = bool(meta.get("collapsed"))
    # 参考式图片 ``![alt][ref]``：url 为 ``[ref:id]`` 占位 → 查定义表展开
    if url.startswith("[ref:"):
        ref_id = url[5:-1]
        ref_map = (getattr(ctx, "ref_map", None) or {}) if ctx is not None else {}
        resolved = ref_map.get(ref_id) or ref_map.get(_normalize_ref_label(ref_id))
        if resolved:
            try:
                url, ref_title = resolved
                title = title or ref_title
            except (TypeError, ValueError):
                url = ""
        elif shortcut:
            # ★ 快捷/折叠引用式图片未命中定义 → 回退原文（``![alt]`` /
            #   ``![alt][]``），与快捷引用式链接未命中保留方括号文本一致
            #   （完整引用式 ``![alt][ref]`` 未命中仍保留 ``[ref:id]`` 占位，
            #   不丢失引用信息）。
            raw = (f"![{node.content}][]" if collapsed
                   else f"![{node.content}]")
            _append(out, raw, base)
            return
        else:
            # 未解析（如流式前置引用）：保留占位提示，不丢失引用信息
            ref_placeholder = f"[ref:{meta.get('label') or ref_id}]"
            url = ""
    shown = url[:50] + "..." if len(url) > 50 else url
    dim = ""
    w = node.meta.get("width", 0)
    h = node.meta.get("height", 0)
    if w and h:
        dim = f" ={w}x{h}"
    elif w:
        dim = f" ={w}"
    elif h:
        dim = f" =x{h}"
    alt = _plain_alt_text(node.content or "") or "image"
    style = _merge(base, _STYLE_IMAGE)
    if url:
        _append(out, f"\U0001f5bc\ufe0f {alt} ({shown}{dim})", style)
    elif ref_placeholder:
        _append(out, f"\U0001f5bc\ufe0f {alt} ({ref_placeholder})", style)
    else:
        _append(out, f"\U0001f5bc\ufe0f {alt}", style)
    if title:
        _append(out, f' "{title}"', _merge(base, _STYLE_COMMENT))


def _emit_math(node, base, ctx, out, depth):
    """行内数学：二维终端排版（多行块交由 ``inline_lines`` 按基线拼接）。

    复杂公式（分数 / 根式 / 大算符上下限 / 矩阵 / 上下标注）在行内同样
    使用二维排版；单行内容直接作为文本 run 追加。
    """
    content = node.content or ""
    if not content:
        return
    lines: list[AnsiLine] = []
    baseline = 0
    try:
        from .math import render_math_inline_block
        lines, baseline = render_math_inline_block(content)
    except Exception:
        _append(out, content, _merge(base, _STYLE_MATH))
        return
    if not lines:
        return
    if len(lines) == 1:
        for run in lines[0].runs:
            _append(out, run.text, run.style if run.style is not None else base)
        return
    # 多行布局：以展平文本作为降级文本，block 供 inline_lines 按基线拼接
    flat = " ".join(ln.plain for ln in lines)
    out.append(Run(flat, base, None, InlineBlock(lines, baseline)))


def _emit_mathml(node, base, ctx, out, depth):
    """行内 MathML：转 LaTeX 后二维排版（多行块交由 ``inline_lines`` 拼接）。

    与 ``_emit_math`` 同一渲染语义——复杂 MathML（分式 / 根式 / 矩阵）在
    行内同样展开为多行并按基线对齐；转换失败时保留原文，内容不丢。
    """
    src = node.content or ""
    if not src:
        return
    lines: list[AnsiLine] = []
    baseline = 0
    try:
        from ._mathml import mathml_to_latex
        latex = mathml_to_latex(src)
    except Exception:
        latex = None
    if latex:
        try:
            from .math import render_math_inline_block
            lines, baseline = render_math_inline_block(latex)
        except Exception:
            lines, baseline = [], 0
    if not lines:
        # 无法解析为 MathML：保留原文（不丢内容）
        _append(out, src, _merge(base, _STYLE_MATH))
        return
    if len(lines) == 1:
        for run in lines[0].runs:
            _append(out, run.text, run.style if run.style is not None else base)
        return
    flat = " ".join(ln.plain for ln in lines)
    out.append(Run(flat, base, None, InlineBlock(lines, baseline)))


def _progress_ratio(meta: dict) -> float:
    """``<progress>`` / ``<meter>`` 属性表 → 0..1 比例（非法/缺失按 0）。"""
    def _num(key: str, default: float) -> float:
        raw = str((meta or {}).get(key, "") or "").strip().rstrip("%")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    value = _num("value", 0.0)
    maxv = _num("max", 1.0)
    if maxv <= 0:
        maxv = 1.0
    return max(0.0, min(1.0, value / maxv))


def _emit_progress(node, base, ctx, out, depth):
    """行内 ``<progress>`` / ``<meter>``：终端进度条。

    自绘（不依赖 HTML 块渲染模块）——避免 ``inline → _html_block → blocks →
    inline`` 的模块依赖环。
    """
    meta = node.meta or {}
    ratio = _progress_ratio(meta)
    filled = int(round(ratio * _PROGRESS_WIDTH))
    if ratio < 0.34:
        fill_style = _STYLE_PROGRESS_LOW
    elif ratio < 0.67:
        fill_style = _STYLE_PROGRESS_MID
    else:
        fill_style = _STYLE_PROGRESS_FILL
    _append(out, "  ", base)
    _append(out, "\u2588" * filled, fill_style)
    _append(out, "\u2591" * (_PROGRESS_WIDTH - filled), _STYLE_PROGRESS_EMPTY)
    _append(out, f" {ratio * 100:.0f}%", _STYLE_PROGRESS_TEXT)


def _emit_footnote(node, base, ctx, out, depth):
    ref_id = getattr(node, "ref_id", "")
    if ctx is not None:
        fn_order = getattr(ctx, "fn_order", None)
        if fn_order is not None:
            if ref_id not in fn_order:
                fn_order.append(ref_id)
            num = fn_order.index(ref_id) + 1
        else:
            num = 0
        _append(out, f"[{num}]", _merge(base, _STYLE_FOOTNOTE))
    else:
        _append(out, f"[^{ref_id}]", _merge(base, _STYLE_FOOTNOTE))


def _emit_inline_footnote(node, base, ctx, out, depth):
    """行内脚注 ``^[文本]``：正文显示序号 ``[n]``，内容进文末脚注列表。

    幂等注册（流式预览与提交会对同一段落渲染多次）：以「归一化内容」为
    脚注键——内容相同的行内脚注合并为同一编号与同一条目，重复渲染不会
    重复追加（``fn_map[ref_id] = content`` 覆盖同值、``fn_order`` 去重）。
    """
    content = (node.content or "").strip()
    if not content:
        return
    if ctx is None:
        # 无渲染上下文（纯测量/独立调用）：保留原语法文本，不臆造编号
        _append(out, f"[^{content}]", _merge(base, _STYLE_FOOTNOTE))
        return
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
    _append(out, f"[{num}]", _merge(base, _STYLE_FOOTNOTE))


def _emit_autolink(node, base, ctx, out, depth):
    url = node.content or ""
    _append(out, url, _merge(base, _STYLE_LINK), link=url or None)


def _emit_autolink_email(node, base, ctx, out, depth):
    addr = node.content or ""
    _append(out, addr, _merge(base, _STYLE_EMAIL),
            link=f"mailto:{addr}" if addr else None)


def _emit_line_break(node, base, ctx, out, depth):
    _append(out, "\n", base)


def _emit_wikilink(node, base, ctx, out, depth):
    _append(out, node.display or node.target or "", _merge(base, _STYLE_WIKI))


def _emit_inline_comment(node, base, ctx, out, depth):
    _append(out, node.content or "", _merge(base, _STYLE_COMMENT))


def _emit_critic_addition(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_CRIT_ADD), ctx, out, depth)


def _emit_critic_deletion(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_CRIT_DEL), ctx, out, depth)


def _emit_critic_highlight(node, base, ctx, out, depth):
    """``{==…==}`` CriticMarkup 高亮：黄底黑字（与 ``==x==`` 同行内视觉）。"""
    _emit_children(node, _merge(base, _STYLE_CRIT_HL), ctx, out, depth)


def _emit_critic_comment(node, base, ctx, out, depth):
    _append(out, "┌[批注]", _merge(base, _STYLE_ABBR))
    _emit_children(node, _merge(base, _STYLE_COMMENT), ctx, out, depth)
    _append(out, "┘", _merge(base, _STYLE_ABBR))


def _emit_critic_substitution(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_CRIT_DEL), ctx, out, depth)
    _append(out, " → ", _merge(base, _STYLE_BOLD))
    new_children = node.meta.get("new_children") if node.meta else None
    if new_children:
        _emit_nodes(new_children, _merge(base, _STYLE_CRIT_ADD), ctx, out, depth + 1)


def _emit_small(node, base, ctx, out, depth):
    _emit_children(node, _merge(base, _STYLE_SMALL), ctx, out, depth)


def _emit_big(node, base, ctx, out, depth):
    """``<big>`` 放大文本（终端无字号，用加粗近似）。"""
    _emit_children(node, _merge(base, _STYLE_BIG), ctx, out, depth)


def _emit_quoted(node, base, ctx, out, depth):
    """``<q>`` 短引用：中文引号包裹内容（保留内部行内格式）。"""
    _append(out, "\u300c", _merge(base, _STYLE_COMMENT))
    _emit_children(node, _merge(base, _STYLE_QUOTE), ctx, out, depth)
    _append(out, "\u300d", _merge(base, _STYLE_COMMENT))


def _emit_color(node, base, ctx, out, depth):
    """着色文本：颜色名或 ``#rrggbb``（未知值回退白色）。

    支持前景色（``color`` / ``{color:red}``）与背景色（``bg``，来自
    ``<span style="background-color:…">`` / ``<font bgcolor=…>``）；仅设置背景
    色时前景继承基础样式（不再强制白色）。
    """
    name = (getattr(node, "color", "") or "").lower()
    bg_name = (getattr(node, "bg", "") or "").lower()
    fg = None
    if name:
        fg = _COLOR_TO_256.get(name)
        if fg is None:
            fg = _hex_to_256(name)
        if fg is None:
            fg = 231
    bg = None
    if bg_name:
        bg = _COLOR_TO_256.get(bg_name)
        if bg is None:
            bg = _hex_to_256(bg_name)
    if fg is None and bg is None:
        _emit_children(node, base, ctx, out, depth)
        return
    style = Style(fg=fg, bg=bg, bold=fg is not None)
    _emit_children(node, _merge(base, style), ctx, out, depth)


def _hex_to_256(value: str) -> int | None:
    """``#rrggbb`` / ``#rgb`` → 256 色号（非该格式或解析失败返回 ``None``）。"""
    if not value.startswith('#'):
        return None
    raw = value[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return None
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return None
    from .style import rgb_to_256
    return rgb_to_256(r, g, b)


def _emit_span(node, base, ctx, out, depth):
    """行内属性 span ``[文本]{.class}``：按类名映射叠加样式后渲染内容。

    未知类名 / ``#id`` / 其它键值对不影响内容渲染（终端无对应语义），
    内容仍按行内 Markdown 递归渲染（嵌套粗体/代码等保留）。
    """
    classes = (node.meta or {}).get("classes") or []
    style = None
    for cls in classes:
        extra = _SPAN_CLASS_STYLES.get(str(cls).lower())
        if extra is None:
            continue
        style = extra if style is None else style.merge(extra)
    _emit_children(node, _merge(base, style) if style else base, ctx, out, depth)


def _emit_citation(node, base, ctx, out, depth):
    """引用 citation ``[@key]``：渲染为 ``[key]`` / ``[-key]`` 标记（多条 ``; `` 连接）。"""
    keys = (node.meta or {}).get("keys") or []
    if not keys:
        _append(out, node.content or "", base)
        return
    _append(out, "[" + "; ".join(keys) + "]", _merge(base, _STYLE_CITATION))


def _emit_subscript(node, base, ctx, out, depth):
    _emit_script(node, base, ctx, out, depth, _SUB_SCRIPT_MAP, _STYLE_SUB)


def _emit_superscript(node, base, ctx, out, depth):
    _emit_script(node, base, ctx, out, depth, _SUPER_SCRIPT_MAP, _STYLE_SUP)


def _emit_script(node, base, ctx, out, depth, unicode_map, fallback_style):
    """上下标：全部字符可 Unicode 转换时用 Unicode，否则回退样式文本。"""
    from src.renderer.inline_nodes import render_inline_to_text
    children = _node_children(node)
    if children:
        plain = render_inline_to_text(children)
    else:
        plain = node.content or ""
    if plain and all(ch in unicode_map or ch.isspace() for ch in plain):
        converted = "".join(unicode_map.get(ch, ch) for ch in plain)
        _append(out, converted, _merge(base, fallback_style))
        return
    if children:
        _emit_children(node, _merge(base, fallback_style), ctx, out, depth)
    else:
        _append(out, plain, _merge(base, fallback_style))


# ── 节点类型 → 渲染函数调度表（模块加载时一次性构建） ──
_DISPATCH: dict = {}


def _build_dispatch() -> dict:
    from src.renderer import inline_nodes as N

    d = {
        N.TextNode: _emit_text,
        N.BoldNode: _emit_bold,
        N.ItalicNode: _emit_italic,
        N.BoldItalicNode: _emit_bold_italic,
        N.UnderlineNode: _emit_underline,
        N.StrikethroughNode: _emit_strike,
        N.HighlightNode: _emit_highlight,
        N.SpoilerNode: _emit_spoiler,
        N.InlineCodeNode: _emit_code,
        N.KbdNode: _emit_kbd,
        N.AbbrNode: _emit_abbr,
        N.LinkNode: _emit_link,
        N.ImageNode: _emit_image,
        N.InlineMathNode: _emit_math,
        N.MathMLNode: _emit_mathml,
        N.ProgressNode: _emit_progress,
        N.FootnoteRefNode: _emit_footnote,
        N.InlineFootnoteNode: _emit_inline_footnote,
        N.AutoLinkNode: _emit_autolink,
        N.AutoLinkEmailNode: _emit_autolink_email,
        N.LineBreakNode: _emit_line_break,
        N.WikiLinkNode: _emit_wikilink,
        N.InlineCommentNode: _emit_inline_comment,
        N.CriticAdditionNode: _emit_critic_addition,
        N.CriticDeletionNode: _emit_critic_deletion,
        N.CriticHighlightNode: _emit_critic_highlight,
        N.CriticCommentNode: _emit_critic_comment,
        N.CriticSubstitutionNode: _emit_critic_substitution,
        N.SmallTextNode: _emit_small,
        N.BigTextNode: _emit_big,
        N.QuotedNode: _emit_quoted,
        N.ColorTextNode: _emit_color,
        N.SubscriptNode: _emit_subscript,
        N.SuperscriptNode: _emit_superscript,
        N.SpanNode: _emit_span,
        N.CitationNode: _emit_citation,
    }
    # 兜底：未知节点 → 纯文本（不吞内容）
    d[N.InlineNode] = lambda node, base, ctx, out, depth: _append(
        out, node.content or "", base
    )
    return d


def _emit_node(node, base: Style, ctx, out: list[Run], depth: int) -> None:
    if depth > _MAX_DEPTH:
        _append(out, getattr(node, "content", "") or "", base)
        return
    handler = _DISPATCH.get(type(node)) or _DISPATCH.get(_INLINE_BASE)
    if handler is None:
        _append(out, getattr(node, "content", "") or "", base)
        return
    handler(node, base, ctx, out, depth)


_INLINE_BASE: Any = None
try:
    from src.renderer.inline_nodes import InlineNode as _INLINE_BASE_IMPL

    _INLINE_BASE = _INLINE_BASE_IMPL
except Exception:  # pragma: no cover - 导入失败时兜底为 None
    _INLINE_BASE = None


def circled_digits() -> list:
    """圈数字符号表（参考式链接编号用；真源 == 表现层数据注册表）。"""
    from src.presentation_data import circled_digits as _digits
    return _digits()


# ═══════════════════════════════════════════════════════════
# 公共入口
# ═══════════════════════════════════════════════════════════


def render_inline(text: str, base_style: Style | None = None, ctx=None) -> list[Run]:
    """解析行内 Markdown 为 Run 序列。

    Args:
        text: 行内文本。
        base_style: 基础样式。
        ctx: 可选的 ``RenderContext``（脚注编号 / 参考式链接 / 缩写替换）。

    Returns:
        Run 列表（解析失败时单 run 纯文本）。
    """
    if not text:
        return []
    base = base_style if base_style is not None else Style()
    if ctx is None:
        ctx = _RENDER_CTX.get()
    try:
        # 快速判否：不产生任何行内格式节点（无核心格式字符、无裸 URL 前缀）
        # → 单 Run（免进解析器）。
        # ★ 例外：上下文含缩写定义表（``abbr_map``）时短语可能被缩写替换为
        #   高亮样式，纯文本也需进入解析器（与解析层
        #   ``inline_renderer`` 的 abbr 分支同口径）。
        if _FAST_ISDISJOINT(text) and not (
                ctx is not None and getattr(ctx, "abbr_map", None)):
            # 智能排版：文本无行内标记时，仍需应用 ``--``/``...``/``->`` 等替换
            # （与解析层同源）——预处理不产生新的标记字符，故可直接返回单 Run。
            text = _preprocess_plain(text)
            return [Run(text, base)]
        from src.renderer.inline_parser import _InlineParser
        nodes = _InlineParser(text).parse()
        out: list[Run] = []
        _emit_nodes(nodes, base, ctx, out, 0)
        return out
    except Exception:
        return [Run(text, base)]


def _make_fast_isdisjoint():
    """构造「文本不产生任何行内格式节点」的 C 级判定函数。

    ★ 英文流式渲染性能：判定口径与解析器「兴趣位置」表
    （``inline_parser._build_interest_positions``）一致——核心格式触发字符
    出现，或含裸 URL 前缀（``http:`` / ``https:`` / ``ftp:`` / ``ftps:`` /
    ``www.``，大小写不敏感）才需进入解析器。修复前把裸 URL 首字母
    （``h/f/w``，英文文本中出现频率约 10%）也当作「触发字符」判否，导致几乎
    所有英文句子都进入解析器逐字符扫描。判定为「无标记」时解析结果必为单一
    纯文本 Run，产出与解析器路径完全一致。
    """
    from src.renderer.inline_parser import text_has_inline_markup

    def _check(text: str) -> bool:
        return not text_has_inline_markup(text)

    return _check


_FAST_ISDISJOINT = _make_fast_isdisjoint()
_DISPATCH.update(_build_dispatch())


# ═══════════════════════════════════════════════════════════
# 表格单元格：行内渲染 + HTML 内联标签的「行间样式映射」
# ═══════════════════════════════════════════════════════════
#
# 解析层把 ``<span style="color:red">`` / ``<mark>`` / ``<font color>`` 等
# 行内 HTML 当作**透明容器**（属性解析后只返回内部文本，见 ``_inline_html``
# 的 ``ColorTextNode`` 特殊处理外分支）。块级段落由行内解析器直接产生
# ``ColorTextNode`` 节点、语义标签（``<sup>`` / ``<kbd>`` / ``<code>`` 等）
# 产生带样式的节点；但表格单元格的渲染路径（``table._cell_runs``）整格按同一
# 基础样式调色，导致 ``style`` 属性标记的标签样式在选择 ``ColorTextNode``
# 之外的全部场景（``<span>`` 包裹嵌套格式、``<font>`` 与 ``<mark>`` 叠加、
# ``<span style="background-color">``）丢失。
#
# 修复思路（只作用于表格单元格，块级段落路径零改动）：单元格原文仍由行内
# 解析器渲染（链接 / 复制语义不变），随后扫描原文的 HTML 标签，把标签对应的
# **样式片段**按「行内文本」与「渲染文本」的字符映射逐字符投影到 Run 上——
# 只有被标签覆盖的字符获得该样式，整格不被误染色。

#: 行内标签的样式属性解析缓存上限（按标签原文缓存，表格预览逐帧复用）
_INLINE_TAG_STYLE_CACHE: dict = {}
_INLINE_TAG_STYLE_CACHE_MAX = 512

#: 不产生可见字符的行内标签（投影时跳过，不占映射位置）
_INLINE_HIDDEN_TAGS: frozenset = frozenset({
    "wbr", "br", "hr", "img", "input", "source", "track", "embed",
    "area", "base", "col", "meta", "link", "param", "colgroup",
    "script", "style", "template", "noscript", "canvas", "svg", "math",
    "option", "optgroup", "datalist",
})


def _parse_inline_tag(text: str, i: int) -> tuple[str, bool, str, int] | None:
    """解析 ``text[i:]`` 开始的一个 HTML 标签。

    Returns:
        ``(tag_name, is_close, raw_tag, next_index)``；不是标签时 ``None``。
        ``raw_tag`` 为标签原文（含 ``<>``），用于属性解析与缓存键。
    """
    n = len(text)
    if i >= n or text[i] != "<":
        return None
    j = i + 1
    is_close = False
    if j < n and text[j] == "/":
        is_close = True
        j += 1
    start = j
    while j < n and (text[j].isalnum() or text[j] in "-:"):
        j += 1
    if j == start or j - start > 32:
        return None
    name = text[start:j].lower()
    quote = ""
    while j < n:
        ch = text[j]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return name, is_close, text[i:j + 1], j + 1
        j += 1
    return None


def _inline_tag_style(raw_tag: str) -> Style | None:
    """行内 HTML 标签 → 终端样式（无样式语义的标签返回 ``None``）。

    支持：``<b>``/``<strong>``/``<i>``/``<em>``/``<u>``/``<ins>``/``<s>``/
    ``<del>``/``<strike>``/``<mark>``/``<small>``/``<big>``/``<code>``/
    ``<samp>``/``<tt>``/``<kbd>``/``<span style|class>``/``<font color|bgcolor>``
    /``<q>`` 等。解析失败或无样式时返回 ``None``（内容照常显示）。
    """
    cached = _INLINE_TAG_STYLE_CACHE.get(raw_tag)
    if cached is not None or raw_tag in _INLINE_TAG_STYLE_CACHE:
        return cached
    style: Style | None = None
    try:
        from .._html_attrs import parse_attrs
        from .._inline_html import _extract_style_background, _extract_style_color

        parsed = _parse_inline_tag(raw_tag, 0)
        tag_name = parsed[0] if parsed is not None else ""
        is_close = bool(parsed[1]) if parsed is not None else False
        # ★ 属性区：去掉 ``<tag`` 前缀与尾部 ``>`` / ``/``——修复前传入的是
        #   ``span style="color:red"``（含标签名），``parse_attrs`` 会把标签名
        #   当作属性名、``class`` / ``color`` 等属性识别错位。
        inner = raw_tag[1:].rstrip(">").rstrip("/")
        if tag_name and inner.startswith(tag_name):
            inner = inner[len(tag_name):]
        attrs = parse_attrs(inner)
        span_classes = attrs.get("class") or attrs.get("classes") or ""
        if isinstance(span_classes, str):
            class_list = span_classes.split()
        else:
            class_list = list(span_classes or ())

        base = _TAG_BASE_STYLES.get(tag_name)
        color = ""
        bg = ""
        if tag_name in ("span", "font"):
            color = _extract_style_color(inner)
            bg = _extract_style_background(inner)
            if tag_name == "font":
                color = attrs.get("color") or color
                bg = attrs.get("bgcolor") or bg
        if tag_name == "q":
            # ``<q>`` 由解析层渲染为「…」包裹，样式沿用引用配色
            base = _STYLE_QUOTE
        if tag_name == "span" and base is None:
            # 无样式属性的 ``<span class="red">x</span>``：类名映射即标签
            # 本身要表达的样式——从**空样式**起步，类名循环才能叠加
            # （若保持 ``style is None``，``_merge(None, …)`` 会抛异常 →
            # 整个标签投影被跳过、类名样式丢失）。无类名时该空样式即
            # 「透明容器」语义（``_merge`` 后不改变字符样式）。
            style = Style()
        elif base is not None:
            style = base
        if color:
            fg = _COLOR_TO_256.get(str(color).lower())
            if fg is None:
                fg = _hex_to_256(str(color))
            if fg is not None:
                style = Style(fg=fg) if style is None else _merge(style, Style(fg=fg))
        if bg:
            bg_code = _COLOR_TO_256.get(str(bg).lower())
            if bg_code is None:
                bg_code = _hex_to_256(str(bg))
            if bg_code is not None:
                style = Style(bg=bg_code) if style is None else _merge(style, Style(bg=bg_code))
        if style is not None and is_close:
            # 闭合标签（``</span>``）本身不表达样式：不产生投影区间。
            style = None
        for cls in class_list:
            extra = _SPAN_CLASS_STYLES.get(str(cls).lower())
            if extra is not None:
                style = extra if style is None else _merge(style, extra)
    except Exception:  # pragma: no cover - 属性解析异常时退化为无样式
        style = None
    if len(_INLINE_TAG_STYLE_CACHE) >= _INLINE_TAG_STYLE_CACHE_MAX:
        _INLINE_TAG_STYLE_CACHE.clear()
    _INLINE_TAG_STYLE_CACHE[raw_tag] = style
    return style


#: 标签名 → 基础样式（与块级段落的行内标签语义保持一致）
_TAG_BASE_STYLES: dict = {
    "b": _STYLE_BOLD, "strong": _STYLE_BOLD,
    "i": _STYLE_ITALIC, "em": _STYLE_ITALIC, "cite": _STYLE_ITALIC,
    "dfn": _STYLE_ITALIC, "var": _STYLE_ITALIC,
    "u": _STYLE_UNDERLINE, "ins": _STYLE_UNDERLINE,
    "s": _STYLE_STRIKE, "del": _STYLE_STRIKE, "strike": _STYLE_STRIKE,
    "mark": _STYLE_HIGHLIGHT,
    "code": _STYLE_CODE, "samp": _STYLE_CODE, "tt": _STYLE_CODE,
    "pre": _STYLE_CODE,
    "kbd": _STYLE_KBD, "small": _STYLE_SMALL, "big": _STYLE_BIG,
    "sub": _STYLE_SUB, "sup": _STYLE_SUP,
    "abbr": _STYLE_ABBR, "acronym": _STYLE_ABBR,
    "h1": _STYLE_BOLD, "h2": _STYLE_BOLD, "h3": _STYLE_BOLD,
    "h4": _STYLE_BOLD, "h5": _STYLE_BOLD, "h6": _STYLE_BOLD,
}


def _plain_projection(text: str) -> tuple[str, list[int]]:
    """原文 → 行内解析后的**可见纯文本** + 每个可见字符的源下标。

    跳过 HTML 标签、``<!-- 注释 -->`` 与 HTML 实体（``&amp;`` 投影为解码后
    字符，映射到实体起点）；其它字符原样进入投影。该投影与行内解析器的
    ``render_inline_to_text`` 口径一致（标签不显示、注释隐藏、实体解码）。
    """
    from .._utils import decode_html_entities

    out: list[str] = []
    index: list[int] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "<":
            if text.startswith("<!--", i):
                end = text.find("-->", i + 4)
                if end >= 0:
                    i = end + 3
                    continue
            parsed = _parse_inline_tag(text, i)
            if parsed is not None:
                if parsed[0] in _INLINE_HIDDEN_TAGS:
                    # 不产生可见字符（``<br>`` 由解析器的换行节点单独处理——
                    # 单元格拆分在调用方按 ``<br>`` 切分，此处不会遇到）
                    i = parsed[3]
                    continue
                i = parsed[3]
                continue
        if ch == "&":
            semi = text.find(";", i + 1)
            if 0 < semi - i <= 32:
                entity = text[i:semi + 1]
                decoded = decode_html_entities(entity)
                if decoded != entity:
                    for c in decoded:
                        out.append(c)
                        index.append(i)
                    i = semi + 1
                    continue
        out.append(ch)
        index.append(i)
        i += 1
    return "".join(out), index


def _collect_inline_style_ranges(source: str) -> list[tuple[int, int, Style]]:
    """扫描原文中的行内 HTML 标签 → ``(投影起点, 投影终点, 样式)`` 区间。

    区间以 ``_plain_projection`` 的可见文本下标计（``[start, end)``）；标签
    嵌套时样式重叠，渲染层按顺序合并（外层先、内层后）。无样式标签（如
    ``<a>`` / 容器 ``<div>`` / 未知标签）不产生区间。
    """
    if "<" not in source:
        return []
    plain, index = _plain_projection(source)
    if not index:
        return []
    n_src = len(source)
    ranges: list[tuple[int, int, Style]] = []
    stack: list[tuple[str, int, Style | None]] = []
    i = 0
    while i < n_src:
        if source[i] != "<":
            i += 1
            continue
        if source.startswith("<!--", i):
            end = source.find("-->", i + 4)
            i = end + 3 if end >= 0 else n_src
            continue
        parsed = _parse_inline_tag(source, i)
        if parsed is None:
            i += 1
            continue
        name, is_close, raw, nxt = parsed
        if not is_close:
            style = _inline_tag_style(raw)
            stack.append((name, i, style))
        else:
            # 找到最近的同名开启标签（容忍未配对标签）
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == name:
                    _name, open_pos, style = stack.pop(k)
                    if style is not None:
                        start = _project_index(index, open_pos, nxt)
                        end = _project_index(index, i, i)
                        if end > start:
                            ranges.append((start, end, style))
                    break
        i = nxt
    # 未闭合的行内标签：覆盖到文本末尾（与浏览器/解析层「标签延续」一致）
    text_end = len(plain)
    for _name, open_pos, style in stack:
        if style is None:
            continue
        start = _project_index(index, open_pos, open_pos + 1)
        if text_end > start:
            ranges.append((start, text_end, style))
    return ranges


def _project_index(index: list[int], src_pos: int, _unused: int) -> int:
    """源下标 ``src_pos`` → 投影下标（首个源下标 ≥ ``src_pos`` 的可见字符）。"""
    lo, hi = 0, len(index)
    while lo < hi:
        mid = (lo + hi) // 2
        if index[mid] < src_pos:
            lo = mid + 1
        else:
            hi = mid
    return lo


def render_inline_cells(text: str, base_style: Style | None = None,
                        ctx=None) -> tuple[list[list[Run]], int]:
    """表格单元格行内渲染 → ``(多行 Run 行, 基线行号)``。

    与 ``inline_lines_with_baseline`` 同一产出结构（按软换行 ``\\n`` 拆分、
    行内二维公式按基线横向拼接）；在此基础上补做**行内 HTML 标签样式投影**：

      - ``<span style="color:red">`` / ``<font color>`` / ``<mark>`` 等标签的
        颜色 / 背景 / 粗体等样式只作用于标签覆盖的字符；
      - 标签本身不显示（内容照常渲染），与块级段落同一语义（修复前表格
        单元格整格统一调色，标签样式全部丢失）。

    无 HTML 标签（绝大多数单元格）时与既有路径**逐字节等价**，并走同一段
    代码（仅多一次 ``"<" in text`` 判定）。
    """
    base = base_style if base_style is not None else Style()
    groups = _inline_lines_impl(text, base)
    rows: list[list[Run]] = []
    baseline = 0
    if len(groups) <= 1:
        joined, baseline = _join_inline_group(groups[0] if groups else [])
        rows = [list(ln.runs) for ln in joined] or [[]]
    else:
        for grp in groups:
            if not grp:
                rows.append([])
                continue
            joined, _ = _join_inline_group(grp)
            rows.extend(list(ln.runs) for ln in joined)
        baseline = 0
    if "<" not in text or not rows:
        return rows, baseline
    try:
        ranges = _collect_inline_style_ranges(text)
    except Exception:  # pragma: no cover - 标签扫描异常时退化为无投影
        return rows, baseline
    if not ranges:
        return rows, baseline
    # 对齐判定：只有当每行「渲染文本」恰为「原文投影行」的连续片段时，字符
    # 位置才能与投影一一对应。含链接（渲染追加 ` (url)`）/ 图片 / 缩写等
    # 补充文本的单元格无法精确对应，保持既有渲染（不做投影，避免错位染色）。
    plain, _index = _plain_projection(text)
    rendered_all = "".join(
        "".join(run.text or "" for run in row) for row in rows
    )
    if rendered_all != plain.replace("\n", "").replace("\r", ""):
        return rows, baseline
    # 逐字符投影样式（按投影文本下标）
    per_char: list[Style | None] = [None] * len(plain)
    for start, end, style in ranges:
        for k in range(max(0, start), min(end, len(per_char))):
            cur = per_char[k]
            per_char[k] = style if cur is None else _merge(cur, style)
    if all(s is None for s in per_char):
        return rows, baseline
    # 逐行 / 逐 run 重建：投影文本中的换行与渲染文本的换行一一对应，故按
    # 字符顺序推进游标即可（投影中的 ``\n`` 不占渲染位置，跳过）。
    cursor = 0
    out_rows: list[list[Run]] = []
    for row in rows:
        rebuilt: list[Run] = []
        for run in row:
            text_run = run.text or ""
            if not text_run:
                continue
            run_link = getattr(run, "link", None)
            run_block = getattr(run, "block", None)
            buf: list[str] = []
            buf_style: Style | None = run.style
            for ch in text_run:
                while cursor < len(plain) and plain[cursor] in "\r\n":
                    cursor += 1
                extra = per_char[cursor] if cursor < len(per_char) else None
                cursor += 1
                target = run.style
                if extra is not None:
                    target = extra if target is None else _merge(target, extra)
                if buf and buf_style == target:
                    buf.append(ch)
                    continue
                if buf:
                    rebuilt.append(Run("".join(buf), buf_style, run_link, run_block))
                buf = [ch]
                buf_style = target
            if buf:
                rebuilt.append(Run("".join(buf), buf_style, run_link, run_block))
        out_rows.append(rebuilt or list(row))
    return out_rows, baseline


def _hjoin_lines(parts: list) -> list["AnsiLine"]:
    """按基线水平拼接多行块（各元素每行按自身宽度补齐）。

    ``parts`` 为 ``(lines, baseline)`` 序列；拼接规则与 ``_math_box._hjoin``
    一致（数学排版语义）：每个元素按**自身列宽**逐行补齐，保证非基线行
    （公式的分子/分母）不与后续元素错位。
    """
    max_above = max(b for _, b in parts)
    max_below = max((len(lines) - 1 - b) for lines, b in parts)
    total = max_above + max_below + 1
    widths = [max((ln.width for ln in lines), default=0) for lines, _ in parts]
    out: list[AnsiLine] = []
    last = len(parts) - 1
    for r in range(total):
        line = AnsiLine()
        for i, (lines, base_idx) in enumerate(parts):
            row = r - (max_above - base_idx)
            src = lines[row] if 0 <= row < len(lines) else None
            if src is not None:
                for run in src.runs:
                    line.append_run(run)
            if i < last:
                used = src.width if src is not None else 0
                pad = widths[i] - used
                if pad > 0:
                    line.append(" " * pad)
        out.append(line)
    return out


def _join_inline_group(elements: list) -> tuple[list["AnsiLine"], int]:
    """行内元素组 → ``(渲染行, 基线行号)``。

    ``elements`` 为 ``(text, block, style, link)`` 序列：``block`` 非空表示
    多行块（二维公式）；无多行块时结果为单行（基线 0）。
    """
    if not any(el[1] is not None for el in elements):
        line = AnsiLine()
        for text, _block, style, link in elements:
            line.append(text, style, link)
        return [line], 0
    parts: list = []
    for text, block, style, link in elements:
        if block is not None:
            parts.append((block.lines, block.baseline))
        else:
            line = AnsiLine()
            line.append(text, style, link)
            parts.append(([line], 0))
    return _hjoin_lines(parts), max(b for _, b in parts)


def _inline_lines_impl(text: str, base: Style | None) -> list:
    """``inline_lines`` 主体：按**显式换行**切分行组，逐组渲染（含基线信息）。

    ★ 修复（行内 HTML 容器跨行）：跨行行内标签（``<span …>a\\nb</span>``）与
    跨行行内代码（`` `a\\nb` ``）在解析层已被正确配对（一次全文扫描，见
    ``_try_html_tag`` 的 C 级闭合查找），渲染结果中的软换行是**段落内折行**
    ——切行后每一行都携带正确的样式，不再泄漏标签原文。

    切分规则：``Run.text`` 中的 ``\\n``（软换行与 ``<br>`` 硬换行同形）切分
    行组，保持终端「软换行 = 换行」的既有呈现；无换行的文本零改动。
    """
    runs = render_inline(text, base)
    groups: list[list] = []
    group: list = []
    for run in runs:
        link = getattr(run, "link", None)
        block = getattr(run, "block", None)
        if block is not None:
            group.append(("", block, None, None))
            continue
        segs = (run.text or "").split("\n")
        for i, seg in enumerate(segs):
            if i > 0:
                groups.append(group)
                group = []
            if seg:
                group.append((seg, None, run.style, link))
    groups.append(group)
    return groups


def inline_lines_with_baseline(text: str,
                              base: Style | None = None) -> tuple[list["AnsiLine"], int]:
    """行内文本 → ``(多行 AnsiLine, 基线行号)``。

    单个行组（无软换行）时基线准确可用（列表项 / 定义项据此把项目符号放在
    公式基线行）；多个行组时返回基线 0（整体拼接语义由调用方决定）。
    """
    groups = _inline_lines_impl(text, base)
    if len(groups) <= 1:
        rows, baseline = _join_inline_group(groups[0] if groups else [])
        return rows or [AnsiLine()], baseline
    out: list[AnsiLine] = []
    for grp in groups:
        if not grp:
            if not out:
                out.append(AnsiLine())
            continue
        rows, _ = _join_inline_group(grp)
        out.extend(rows)
    return out or [AnsiLine()], 0


def inline_lines(text: str, base: Style | None = None) -> list["AnsiLine"]:
    """行内文本 → 多行 ``AnsiLine``（按 ``\\n`` 拆行；连续换行合并）。

    段落 / 告示 / 折叠块 / HTML 块的正文都可能含 ``<br>``（硬换行）或软换行
    ——``render_inline`` 产出的 ``LineBreakNode`` 渲染为 ``"\\n"``，与段落自身
    的软换行叠加会产生多余空行。此处把连续换行合并为一次换行（段落内不存在
    有意义的空行，段落在空行处已断开）。

    **行内多行块**（二维公式）：同一行组内与周围文本按基线水平拼接，公式的
    非基线行单独成行。

    ``base`` 为基础样式（无行内格式的文本以此着色；None 用默认样式）。
    """
    out: list[AnsiLine] = []
    for grp in _inline_lines_impl(text, base):
        if not grp:
            if not out:
                out.append(AnsiLine())
            continue
        rows, _ = _join_inline_group(grp)
        out.extend(rows)
    return out or [AnsiLine()]


__all__ = [
    "render_inline", "use_render_context", "current_render_context",
    "inline_lines", "inline_lines_with_baseline", "InlineBlock",
]
