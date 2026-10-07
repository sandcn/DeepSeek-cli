"""_html_block — HTML 块级语义渲染（ANSI，TUI 流式内容路径）。

把 ``HTML_BLOCK_OPEN`` / ``HTML_BLOCK_LINE`` / ``HTML_BLOCK_CLOSE`` 三个
Token 渲染为终端行，并给出比「逐行原样显示」更贴近 HTML 语义的呈现：

  - **结构化标签**：``<ol>`` / ``<ul>`` / ``<dl>`` / ``<table>`` 在解析层已
    转为列表/定义列表/表格 Token；本模块处理其余容器标签（figure /
    figcaption / video / audio / iframe / picture / source / track / caption…）
    的语义前缀与图标；
  - **原始内容标签**：``<script>`` / ``<style>`` / ``<template>`` /
    ``<noscript>`` / ``<canvas>`` / ``<svg>`` / ``<math>`` 的内容不作为可见
    文本显示（改为占位说明 + 省略行数），避免把 JS/CSS 刷屏——其中
    ``<svg>`` 提取 ``<title>`` / ``<desc>`` 呈现语义、``<math>`` 转为公式
    二维排版（见 ``_mathml``）；
  - **表单/度量控件**：``<input type=checkbox|radio>`` → ☑/☐/◉/○，
    ``<progress>`` / ``<meter>`` → 终端进度条；``<button>`` → ``[ 文本 ]``、
    ``<select>`` → 逐 ``<option>`` 选项行、``<textarea>`` → 文本域、
    ``<object>`` / ``<iframe>`` / ``<audio|video>`` → 地址与类型说明行；
  - **布局属性**：``<center>`` / ``align="center"`` / ``style="text-align:center"``
    → 内容按终端宽度居中；
  - **媒体/空元素**：``<img>`` / ``<source>`` / ``<track>`` / ``<br>`` 等
    单独成行时语义化渲染（不再显示标签字面）。

标签属性（``align`` / ``value`` / ``max`` / ``checked`` / ``class``…）由
``renderer._html_attrs`` 统一解析；样式复用 ``blocks`` 的块级配色，保证与
其它块级元素视觉一致。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from .inline import inline_lines
from . import blocks as _blocks
from .._html_attrs import (
    parse_open_tag, attr, align_of, number_of, bool_attr,
    RAW_TEXT_TAGS,
)

# ── 样式（HTML 专属；块级通用配色复用 blocks 常量） ─────────

_STYLE_HTML_TAG = Style(fg=240, dim=True)
_STYLE_MEDIA = Style(fg=110, bold=True)
_STYLE_PROGRESS_FILL = Style(fg=41, bold=True)
_STYLE_PROGRESS_MID = Style(fg=220, bold=True)
_STYLE_PROGRESS_LOW = Style(fg=203, bold=True)
_STYLE_PROGRESS_EMPTY = Style(fg=238)
_STYLE_PROGRESS_TEXT = Style(fg=252)
_STYLE_CHECK_ON = Style(fg=84, bold=True)
_STYLE_CHECK_OFF = Style(fg=242)
_STYLE_OMITTED = Style(fg=240, italic=True)

#: 标签 → 标签行说明（``▸ <说明>``）。结构化容器的语义标签在解析层已转 Token，
#: 此处覆盖其余标签的图标与中文说明。
TAG_LABELS: dict[str, str] = {
    # 结构容器
    "div": "div 区块", "section": "section 章节", "article": "article 文章",
    "header": "header 页眉", "footer": "footer 页脚", "main": "main 主体",
    "aside": "aside 侧栏", "nav": "nav 导航", "address": "address 联系信息",
    "dialog": "dialog 对话框", "menu": "menu 菜单",
    "form": "form 表单", "fieldset": "fieldset 字段组",
    "center": "center 居中",
    # 列表 / 定义列表（解析层结构化，标签行保留）
    "ul": "ul 列表", "ol": "ol 有序列表", "li": "li 列表项",
    "dl": "dl 定义列表", "dt": "dt 术语", "dd": "dd 定义",
    # 表格（解析层结构化）
    "table": "table 表格", "caption": "caption 表注",
    # 图形
    "figure": "figure 插图", "figcaption": "figcaption 图注",
    # 媒体
    "video": "video ▶ 视频", "audio": "audio ♪ 音频",
    "iframe": "iframe ⧉ 内嵌", "picture": "picture ▣ 图片",
    "source": "source ◆ 媒体源", "track": "track ◆ 字幕",
    "embed": "embed 嵌入", "object": "object 对象",
    # 控件
    "progress": "progress 进度", "meter": "meter 度量",
    "input": "input 输入", "button": "button 按钮",
    "select": "select 下拉", "option": "option 选项",
    "optgroup": "optgroup 选项组", "datalist": "datalist 候选",
    "textarea": "textarea 文本域",
    "param": "param 参数", "colgroup": "colgroup 列组",
    "legend": "legend 图例", "slot": "slot 插槽",
    # 原始内容（内容不显示）
    "script": "script 脚本", "style": "style 样式",
    "template": "template 模板", "canvas": "canvas 画布",
    "noscript": "noscript 无脚本回退", "svg": "svg 矢量图",
    "math": "math 数学标记",
    # 空元素（单行时通常不显示标签行，此处供无状态入口/异常序列使用）
    "img": "img 图片", "br": "br 换行", "wbr": "wbr 断行",
    "area": "area 热区", "base": "base 基地址", "col": "col 列",
    "meta": "meta 元信息", "link": "link 外部资源", "param": "param 参数",
}

#: 无内容容器的标签（内容仅作占位说明，不逐行显示）
_HIDDEN_CONTENT_TAGS = RAW_TEXT_TAGS

#: 空元素（void）标签（单独成行时语义化渲染）
_VOID_TAGS = frozenset({
    "input", "img", "source", "track", "br", "wbr", "hr",
    "embed", "area", "base", "col", "meta", "link", "param",
})

#: 媒体类空元素
_MEDIA_VOID_TAGS = frozenset({"source", "track", "embed"})

#: 静默空元素（不产生任何可见行）
_SILENT_VOID_TAGS = frozenset({
    "wbr", "area", "base", "col", "meta", "link", "param",
})

#: 块级文本标签：整行单层标签对（``<p>x</p>`` / ``<blockquote>x</blockquote>``…）
#: 时按内容渲染（不显示标签字面）
_BLOCK_TEXT_TAGS = frozenset({
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "nav", "address", "blockquote", "summary", "label", "output", "span",
    "figure", "dialog", "form", "fieldset", "menu",
})

#: 标题标签（内容按对应级别标题样式渲染）
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: 进度条默认宽度（字符数）
_PROGRESS_WIDTH = 24

#: 控件标签：``OPEN`` 只输出语义头（不显示 ``▸ <标签>``），内容行单独渲染；
#: ``CLOSE`` 不留空行（避免控件块后出现空白行）。
_CONTROL_TAGS: frozenset = frozenset({
    "button", "select", "textarea", "object",
})

#: 静默块级标签（内容不显示，也不产生标签行）
_SILENT_BLOCK_TAGS: frozenset = frozenset({
    "colgroup", "col", "datalist",
})


class HtmlBlockState:
    """单个 HTML 块的渲染状态（``OPEN`` 建立，``CLOSE`` 丢弃）。

    - ``hidden``：内容隐藏（``<script>`` 等原始标签）；
    - ``center``：内容按终端宽度居中（``<center>`` / ``align=center``）；
    - ``omitted``：已隐藏的内容行数（``CLOSE`` 时给出省略提示）；
    - ``math_lines``：``<math>`` 块收集的 MathML 原文（``CLOSE`` 时转公式
      排版；``None`` 表示不是 math 块）。
    """

    __slots__ = ("tag", "attrs", "hidden", "center", "omitted", "void",
                 "math_lines", "hidden_lines")

    def __init__(self, tag: str, attrs: dict | None = None) -> None:
        self.tag = tag or ""
        self.attrs = attrs or {}
        # ``<math>`` 内容不是「原始内容」而是 MathML：保留并按公式排版
        self.hidden = (self.tag in _HIDDEN_CONTENT_TAGS
                       and self.tag != "math")
        self.center = (self.tag == "center") or (align_of(self.attrs) == "center")
        self.omitted = 0
        # 空元素单行块（``<img>`` / ``<input>``…）：无内容行、无结束行
        self.void = self.tag in _VOID_TAGS
        # ``<math>`` 块：收集 MathML 原文（CLOSE 时统一转换排版）
        self.math_lines: list[str] | None = [] if self.tag == "math" else None
        # ``<svg>`` 等隐藏内容块：保留原文以便提取 ``<title>`` / ``<desc>``
        self.hidden_lines: list[str] | None = [] if self.tag == "svg" else None


# ── 标签行 ─────────────────────────────────────────────────


def tag_head_line(tag: str, attrs: dict | None = None) -> AnsiLine:
    """HTML 块起始行（``▸ <说明>``；隐藏内容 / 居中附注提示）。"""
    label = TAG_LABELS.get(tag, tag)
    line = AnsiLine.of(f"\u25b8 <{label}>", _STYLE_HTML_TAG)
    if tag in _HIDDEN_CONTENT_TAGS:
        line.append("（原始内容不显示）", _STYLE_OMITTED)
    elif tag == "center" or align_of(attrs or {}) == "center":
        line.append("（居中）", _STYLE_OMITTED)
    return line


# ── 控件 / 媒体行 ──────────────────────────────────────────


def _format_number(value: float) -> str:
    """数值显示（整数不带小数点）。"""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def progress_line(tag: str, attrs: dict) -> AnsiLine:
    """``<progress value max>`` / ``<meter value max>`` → 终端进度条。

    颜色按比例分级（低 → 红、中 → 黄、高 → 绿）；``<meter>`` 的阈值语义以
    33% / 66% 近似。缺 ``value`` 时显示空条（不确定态）。
    """
    value = number_of(attrs, "value")
    maxv = number_of(attrs, "max")
    if maxv is None or maxv <= 0:
        maxv = 1.0
    if value is None:
        value = 0.0
    ratio = max(0.0, min(1.0, value / maxv))
    filled = int(round(ratio * _PROGRESS_WIDTH))
    if ratio < 0.34:
        fill_style = _STYLE_PROGRESS_LOW
    elif ratio < 0.67:
        fill_style = _STYLE_PROGRESS_MID
    else:
        fill_style = _STYLE_PROGRESS_FILL
    line = AnsiLine.of("  ", None)
    line.append("█" * filled, fill_style)
    line.append("░" * (_PROGRESS_WIDTH - filled), _STYLE_PROGRESS_EMPTY)
    line.append(f" {ratio * 100:.0f}%", _STYLE_PROGRESS_TEXT)
    if "value" in attrs and maxv != 1.0:
        line.append(f" （{_format_number(value)}/{_format_number(maxv)}）",
                    _STYLE_OMITTED)
    return line


def input_line(attrs: dict) -> AnsiLine:
    """``<input>`` → 复选框 / 单选框 / 按钮 / 通用输入提示。"""
    itype = attr(attrs, "type", "text").strip().lower()
    if itype == "checkbox":
        checked = bool_attr(attrs, "checked")
        return AnsiLine.of("  ☑ " if checked else "  ☐ ",
                           _STYLE_CHECK_ON if checked else _STYLE_CHECK_OFF)
    if itype == "radio":
        checked = bool_attr(attrs, "checked")
        return AnsiLine.of("  ◉ " if checked else "  ○ ",
                           _STYLE_CHECK_ON if checked else _STYLE_CHECK_OFF)
    if itype in ("submit", "button", "reset"):
        label = attr(attrs, "value") or itype.title()
        return AnsiLine.of(f"  [ {label} ]", _STYLE_MEDIA)
    if itype in ("range", "number"):
        value = attr(attrs, "value")
        suffix = f" = {value}" if value else ""
        return AnsiLine.of(f"  ▸ <input {itype}{suffix}>", _STYLE_HTML_TAG)
    placeholder = attr(attrs, "placeholder")
    suffix = f" “{placeholder}”" if placeholder else ""
    return AnsiLine.of(f"  ▸ <input {itype}>{suffix}", _STYLE_HTML_TAG)


def image_line(attrs: dict) -> AnsiLine:
    """``<img>`` → 图片占位（替代文本 + 尺寸 + 地址）。"""
    src = attr(attrs, "src") or attr(attrs, "data-src")
    alt = attr(attrs, "alt") or "图片"
    width = attr(attrs, "width")
    height = attr(attrs, "height")
    dim = f" ={width}x{height}" if width and height else ""
    shown = src if len(src) <= 48 else src[:45] + "..."
    line = AnsiLine.of("  ▣ ", _STYLE_MEDIA)
    line.append(alt, _STYLE_PROGRESS_TEXT)
    if shown:
        line.append(f" ({shown}{dim})", _STYLE_HTML_TAG)
    return line


def media_source_line(tag: str, attrs: dict) -> AnsiLine:
    """``<source>`` / ``<track>`` / ``<embed>`` → 媒体源说明行。

    地址优先 ``src``，其次 ``data``，最后 ``srcset``（响应式图片的候选集，
    只显示首个候选，避免整串 URL 刷屏）。
    """
    src = (attr(attrs, "src") or attr(attrs, "data")
           or attr(attrs, "data-src") or _first_srcset(attrs))
    kind = attr(attrs, "type") or attr(attrs, "kind")
    label = {"source": "媒体源", "track": "字幕轨", "embed": "嵌入"}.get(tag, tag)
    line = AnsiLine.of("    ◆ ", _STYLE_MEDIA)
    line.append(label, _STYLE_HTML_TAG)
    if kind:
        line.append(f" [{kind}]", _STYLE_HTML_TAG)
    if src:
        line.append(f" {src}", _STYLE_HTML_TAG)
    return line


def _first_srcset(attrs: dict) -> str:
    """``srcset="a.png 1x, b.png 2x"`` → 首个候选地址（无则空串）。"""
    raw = attr(attrs, "srcset") or attr(attrs, "data-srcset")
    if not raw:
        return ""
    first = raw.split(",")[0].strip()
    return first.split(" ")[0] if first else ""


def button_line(text: str, attrs: dict | None = None) -> list[AnsiLine]:
    """``<button>`` → 按钮行 ``[ 文本 ]``（禁用态附注）。"""
    label = (text or "").strip()
    if not label:
        label = attr(attrs or {}, "value") or "按钮"
    line = AnsiLine.of("  ", None)
    disabled = bool_attr(attrs or {}, "disabled")
    style = _STYLE_HTML_TAG if disabled else _STYLE_MEDIA
    line.append("[ " + label + " ]", style)
    if disabled:
        line.append("（禁用）", _STYLE_OMITTED)
    return [line]


def textarea_head(attrs: dict | None = None) -> AnsiLine:
    """``<textarea>`` 头部行（文本域 + 行数）。"""
    attrs = attrs or {}
    rows = attr(attrs, "rows")
    line = AnsiLine.of("  ▤ 文本域", _STYLE_HTML_TAG)
    if rows:
        line.append(f"（{rows} 行）", _STYLE_OMITTED)
    return line


def textarea_body(text: str, attrs: dict | None = None) -> list[AnsiLine]:
    """``<textarea>`` 内容行（逐行呈现；空内容时显示占位提示）。"""
    attrs = attrs or {}
    placeholder = attr(attrs, "placeholder")
    out: list[AnsiLine] = []
    body = (text or "").strip()
    if body:
        for seg in body.split("\n"):
            if not seg.strip():
                continue
            line = AnsiLine.of("    ", None)
            for run in inline_lines(seg, None):
                for r in run.runs:
                    line.append_run(r)
                break
            out.append(line)
    elif placeholder:
        out.append(AnsiLine.of(f"    （占位：{placeholder}）", _STYLE_OMITTED))
    return out


def textarea_line(text: str, attrs: dict | None = None) -> list[AnsiLine]:
    """``<textarea>`` → 头部 + 内容行（单行闭合时使用）。"""
    attrs = attrs or {}
    placeholder = attr(attrs, "placeholder")
    out = [textarea_head(attrs)]
    out.extend(textarea_body(text, attrs))
    if placeholder and (text or "").strip():
        out.append(AnsiLine.of(f"    （占位：{placeholder}）", _STYLE_OMITTED))
    return out


def option_lines(text: str, attrs: dict | None = None) -> list[AnsiLine]:
    """``<select>`` 内容行 → 逐 ``<option>`` 选项行。"""
    out: list[AnsiLine] = []
    for option_attrs, label in _iter_option_items(text or ""):
        selected = bool_attr(option_attrs, "selected")
        line = AnsiLine.of("    ", None)
        line.append("◉ " if selected else "○ ",
                    _STYLE_CHECK_ON if selected else _STYLE_CHECK_OFF)
        line.append(label or option_attrs.get("value", ""), _STYLE_PROGRESS_TEXT)
        out.append(line)
    return out


def select_lines(text: str, attrs: dict | None = None) -> list[AnsiLine]:
    """``<select>`` → 下拉列表（头部 + 逐 ``<option>`` 行）。"""
    attrs = attrs or {}
    head = AnsiLine.of("  ▾ 下拉列表", _STYLE_HTML_TAG)
    if bool_attr(attrs, "multiple"):
        head.append("（多选）", _STYLE_OMITTED)
    return [head] + option_lines(text, attrs)


def object_line(attrs: dict) -> AnsiLine:
    """``<object>`` → 对象嵌入行（类型 + 地址）。"""
    data = attr(attrs, "data") or attr(attrs, "data-src")
    kind = attr(attrs, "type")
    line = AnsiLine.of("  ⧉ 对象", _STYLE_MEDIA)
    if kind:
        line.append(f" [{kind}]", _STYLE_HTML_TAG)
    if data:
        line.append(f" {data}", _STYLE_HTML_TAG)
    return line


def _iter_option_items(text: str):
    """扫描 ``<option ...>标签</option>`` → ``(属性表, 文本)`` 迭代。"""
    low = text.lower()
    pos = 0
    while True:
        start = low.find("<option", pos)
        if start < 0:
            return
        gt = text.find(">", start)
        if gt < 0:
            return
        _tag, attrs = parse_open_tag(text[start:gt + 1])
        close = low.find("</option>", gt + 1)
        if close < 0:
            yield attrs, text[gt + 1:].strip()
            return
        yield attrs, text[gt + 1:close].strip()
        pos = close + len("</option>")


def _void_line(tag: str, attrs: dict, tail: str = "") -> AnsiLine | None:
    """空元素整行 → 语义化行（``None`` 表示按普通内容行处理）。

    ``tail`` 为标签后的同行文本（``<input …> 完成`` → ``  ☑ 完成``）；
    静默空元素（``<meta>``/``<link>``…）只保留 ``tail``，标签本身不显示
    （``tail`` 仍是标记时一并忽略）。
    """
    if tag in _SILENT_VOID_TAGS or tag == "br":
        text = tail if tail and not tail.startswith("<") else ""
        return AnsiLine.of(text) if text else AnsiLine.of("")
    if tag == "input":
        line = input_line(attrs)
        if tail:
            line.append(f" {tail}", None)
        return line
    if tag == "img":
        line = image_line(attrs)
        if tail:
            line.append(f" {tail}", None)
        return line
    if tag in _MEDIA_VOID_TAGS:
        line = media_source_line(tag, attrs)
        if tail:
            line.append(f" {tail}", None)
        return line
    return None


# ── 内容行 ─────────────────────────────────────────────────


def _iter_tag_text(text: str, tag: str):
    """扫描 ``<tag ...>内容</tag>`` → 内容文本迭代（浅层，不处理同名嵌套）。

    标签名做边界匹配（``<option`` 不会匹配 ``<options``）；自闭合标签跳过；
    未闭合时把剩余文本作为最后一项（内容不丢）。
    """
    low = (text or "").lower()
    open_prefix = "<" + tag
    close_tag = "</" + tag + ">"
    pos = 0
    while True:
        start = low.find(open_prefix, pos)
        if start < 0:
            return
        after = start + len(open_prefix)
        if after < len(low) and low[after] not in " \t\r\n/>":
            pos = after
            continue
        gt = (text or "").find(">", start)
        if gt < 0:
            return
        if text[gt - 1] == "/":
            pos = gt + 1
            continue
        end = low.find(close_tag, gt + 1)
        if end < 0:
            yield text[gt + 1:]
            return
        yield text[gt + 1:end]
        pos = end + len(close_tag)


def _html_title_desc(lines) -> tuple[str, str]:
    """HTML 片段行 → ``(<title> 文本, <desc> 文本)``（无则空串）。"""
    text = "\n".join(str(ln) for ln in (lines or []))
    title = ""
    desc = ""
    for seg in _iter_tag_text(text, "title"):
        if seg.strip():
            title = seg.strip()
            break
    for seg in _iter_tag_text(text, "desc"):
        if seg.strip():
            desc = seg.strip()
            break
    return title, desc


class _ListToken:
    """列表项 Token 桩（复用 ``blocks.render_list_item`` 的渲染语义）。"""

    __slots__ = ("content", "meta")

    def __init__(self, content: str, meta: dict) -> None:
        self.content = content
        self.meta = meta


def _single_line_list(text: str) -> list[AnsiLine] | None:
    """整行为 HTML 列表（``<ul><li>a</li><li>b</li></ul>``）→ 列表行。

    返回 ``None`` 表示不是列表行（调用方按普通内容行处理）；非列表内容保留
    ``<li>`` 内的行内格式（``<b>`` / ``<a>`` 等交由行内解析器）。
    """
    s = (text or "").strip()
    low = s.lower()
    if not (low.startswith("<ul") or low.startswith("<ol")):
        return None
    if not (low.endswith("</ul>") or low.endswith("</ol>")):
        return None
    root = "ol" if low.startswith("<ol") else "ul"
    try:
        from .._block_parser import _parse_html_list_items
    except Exception:
        return None
    start = None
    tag, attrs = parse_open_tag(s)
    if tag == root:
        value = number_of(attrs, "start")
        start = int(value) if value else None
    items = _parse_html_list_items(s, root_kind=root, start=start)
    if not items:
        return None
    rows: list[AnsiLine] = []
    for depth, ordered, number, content in items:
        meta: dict = {"depth": max(1, int(depth)),
                      "indent": max(0, int(depth) - 1),
                      "number": number}
        if not ordered:
            meta["bullet"] = True
        rows.extend(_blocks.render_list_item(_ListToken(content, meta)))
    return rows or None


def split_inline_html_tag(text: str) -> tuple[str, str] | None:
    """整行形如 ``<tag ...>内容</tag>`` 时返回 ``(tag, 内容)``，否则 None。"""
    s = text.strip()
    n = len(s)
    if n < 4 or s[0] != '<' or s[1] == '/':
        return None
    j = 1
    while j < n and (s[j].isalnum() or s[j] in '-:'):
        j += 1
    tag = s[1:j].lower()
    if not tag:
        return None
    gt = s.find('>', j)
    if gt < 0:
        return None
    close = f'</{tag}>'
    low = s.lower()
    if not low.endswith(close):
        return None
    return tag, s[gt + 1:n - len(close)]


#: 行内单层标签中具备语义样式的标签集合（前缀 / 配色）
_INLINE_SEMANTIC_TAGS: frozenset = frozenset({
    "li", "dt", "dd", "figcaption", "caption", "source", "track",
    "video", "audio", "iframe", "picture",
})


def _prefix_style(tag: str) -> tuple[str, Style]:
    """内容行前缀与基础样式（按标签语义）。"""
    if tag == "li":
        return "  • ", _blocks._STYLE_LIST_BULLET
    if tag == "dt":
        return "  ", _blocks._STYLE_DEF_TERM
    if tag == "dd":
        return "      ", _blocks._STYLE_BQ
    if tag in ("figcaption", "caption"):
        return "  ", _blocks._STYLE_CAPTION
    if tag in ("source", "track", "embed"):
        return "    ", _STYLE_HTML_TAG
    if tag in ("video", "audio", "iframe", "picture", "img", "object"):
        return "  ", _STYLE_HTML_TAG
    if tag in ("address", "form", "fieldset", "dialog", "menu", "nav"):
        return "  ", _blocks._STYLE_BQ
    return "  ", _blocks._STYLE_BQ


def html_block_line(text: str, tag: str = "",
                    attrs: dict | None = None) -> list[AnsiLine]:
    """HTML 块内容行 → 语义化渲染（单行空元素 / 行内标签语义 + 行内 Markdown）。

    ``tag`` 为所属块的标签（控件块据此决定内容行的语义），``attrs`` 为块
    起始标签属性（控件禁用/占位/多选等）。
    """
    s = (text or "").strip()
    if not s:
        return [AnsiLine.of("  ", _blocks._STYLE_BQ)]
    if tag in _SILENT_BLOCK_TAGS:
        return []
    # 控件块内容行：按控件语义渲染（头行已由 ``open_html_block`` 输出）
    if tag == "select":
        return option_lines(s, attrs)
    if tag == "textarea":
        return textarea_body(s, attrs)
    if tag == "button":
        return button_line(text, attrs)
    # 整行为 HTML 列表（``<ul><li>a</li><li>b</li></ul>``）→ 结构化列表行
    list_rows = _single_line_list(s)
    if list_rows is not None:
        return list_rows
    # 整行为单层标签（``<li>x</li>`` / ``<figcaption>x</figcaption>``…）→ 语义前缀
    effective_tag = tag
    content = text
    split = split_inline_html_tag(text)
    if split is not None:
        inner_tag, inner_text = split
        if inner_tag in _SILENT_BLOCK_TAGS:
            return []
        if inner_tag in ("progress", "meter"):
            _t, attrs = parse_open_tag(s)
            return [progress_line(inner_tag, attrs)]
        if inner_tag == "button":
            _t, attrs = parse_open_tag(s)
            return button_line(inner_text, attrs)
        if inner_tag == "textarea":
            _t, attrs = parse_open_tag(s)
            return textarea_line(inner_text, attrs)
        if inner_tag == "select":
            _t, attrs = parse_open_tag(s)
            return select_lines(inner_text, attrs)
        if inner_tag == "object":
            _t, attrs = parse_open_tag(s)
            return [object_line(attrs)]
        if inner_tag == "option":
            _t, attrs = parse_open_tag(s)
            selected = bool_attr(attrs, "selected")
            line = AnsiLine.of("  ", None)
            line.append("◉ " if selected else "○ ",
                        _STYLE_CHECK_ON if selected else _STYLE_CHECK_OFF)
            for run in inline_lines(inner_text, None):
                for r in run.runs:
                    line.append_run(r)
                break
            return [line]
        if inner_tag in _VOID_TAGS and not inner_text.strip():
            _t, attrs = parse_open_tag(s)
            line = _void_line(inner_tag, attrs, "")
            if line is not None:
                return [line]
        if inner_tag in _INLINE_SEMANTIC_TAGS:
            effective_tag, content = inner_tag, inner_text
        elif inner_tag in _HEADING_TAGS:
            # ``<h1>``~``<h6>`` 整行：按对应级别标题样式渲染
            level = int(inner_tag[1])
            style = _blocks._HEADING_STYLES[min(max(level, 1), 6) - 1]
            return _styled_lines(inner_text, style)
        elif inner_tag == "blockquote":
            return [_blocks.render_blockquote_line(inner_text, 0)]
        elif inner_tag in _BLOCK_TEXT_TAGS:
            # 块级文本标签整行（``<p>x</p>`` / ``<span>x</span>``…）：按内容渲染
            effective_tag, content = "p", inner_text
    # 整行为单个空元素（``<img …>`` / ``<input …>`` / ``<source …>``…）→ 语义化
    if s.startswith("<") and ">" in s:
        void_tag, attrs = parse_open_tag(s)
        if void_tag in _VOID_TAGS:
            gt = s.find(">")
            tail = s[gt + 1:].strip()
            if not tail or void_tag in ("input", "img", "source", "track", "embed"):
                # 同行后续仍有标签（``<source …><img …>``）：空元素行后递归渲染剩余
                same_line = tail.startswith("<")
                line = _void_line(void_tag, attrs, "" if same_line else tail)
                rows = [line] if line is not None else []
                if same_line:
                    rows.extend(html_block_line(tail, tag))
                if rows:
                    return rows
    # 块级控件标签的内容行（``<button>文本</button>`` 的内层「文本」行）
    if not s.startswith("<"):
        if effective_tag == "button":
            return button_line(content)
        if effective_tag == "textarea":
            return textarea_line(content)
    prefix, base = _prefix_style(effective_tag)
    rows: list[AnsiLine] = []
    for sub in inline_lines(content, base):
        line = AnsiLine.of(prefix, base)
        for run in sub.runs:
            line.append_run(run)
        rows.append(line)
    return rows or [AnsiLine.of(prefix, base)]


def _styled_lines(text: str, style: Style) -> list[AnsiLine]:
    """行内文本按指定基础样式渲染为多行（无内容时返回单空行）。"""
    rows: list[AnsiLine] = []
    for sub in inline_lines(text, style):
        line = AnsiLine()
        for run in sub.runs:
            line.append_run(run)
        rows.append(line)
    return rows or [AnsiLine()]


def center_lines(lines: list[AnsiLine], width: int) -> list[AnsiLine]:
    """按终端宽度居中（``width<=0`` 或行更宽时原样返回）。"""
    if not lines or not width or width <= 0:
        return lines
    out: list[AnsiLine] = []
    for line in lines:
        pad = (width - line.width) // 2
        if pad <= 0:
            out.append(line)
            continue
        nl = AnsiLine.of(" " * pad)
        for run in line.runs:
            nl.append_run(run)
        out.append(nl)
    return out


# ── 有状态入口（AnsiRenderEngine 使用） ─────────────────────


def open_html_block(tag: str, attrs: dict | None = None,
                    width: int = 0,
                    tail: str = "") -> tuple[list[AnsiLine], HtmlBlockState]:
    """HTML 块开始 → ``(渲染行, 块状态)``。

    ``tail`` 为空元素单行的同行文本（``<input …> 完成``）；空元素不显示标签
    行（只显示语义行），非空元素显示 ``▸ <说明>`` 标签行。``<math>`` 块不
    显示标签行（其内容在 ``CLOSE`` 时转为公式排版）。
    """
    state = HtmlBlockState(tag, attrs)
    if tag == "math":
        return [], state
    if tag in _SILENT_BLOCK_TAGS:
        return [], state
    if tag == "button":
        # 按钮文本行由 ``html_block_line`` 渲染（此处不显示标签行）
        return [], state
    if tag == "select":
        head = AnsiLine.of("  ▾ 下拉列表", _STYLE_HTML_TAG)
        if bool_attr(state.attrs, "multiple"):
            head.append("（多选）", _STYLE_OMITTED)
        return [head], state
    if tag == "textarea":
        return [textarea_head(state.attrs)], state
    if tag == "object":
        return [object_line(state.attrs)], state
    if tag in _VOID_TAGS:
        line = _void_line(tag, attrs, tail)
        lines = [line] if line is not None else []
    else:
        lines = [tag_head_line(state.tag, state.attrs)]
        if state.tag in ("progress", "meter"):
            lines.append(progress_line(state.tag, state.attrs))
        elif state.tag == "input" and not state.hidden:
            lines.append(input_line(state.attrs))
        elif state.tag == "img" and not state.hidden:
            lines.append(image_line(state.attrs))
        elif state.tag in ("audio", "video") and not state.hidden:
            src = attr(state.attrs, "src")
            if src:
                line = AnsiLine.of("    ◆ 源 ", _STYLE_MEDIA)
                line.append(src, _STYLE_HTML_TAG)
                lines.append(line)
        elif state.tag == "object" and not state.hidden:
            lines.append(object_line(state.attrs))
        elif state.tag == "iframe" and not state.hidden:
            src = attr(state.attrs, "src")
            if src:
                width = attr(state.attrs, "width")
                height = attr(state.attrs, "height")
                dim = f" （{width}×{height}）" if width and height else ""
                line = AnsiLine.of("    ◆ 地址 ", _STYLE_MEDIA)
                line.append(src, _STYLE_HTML_TAG)
                if dim:
                    line.append(dim, _STYLE_OMITTED)
                lines.append(line)
        elif state.tag in _MEDIA_VOID_TAGS and not state.hidden:
            lines.append(media_source_line(state.tag, state.attrs))
    if state.center:
        # 标签行保持左对齐（避免出现整行浮动的标签），其余行居中
        head = [] if tag in _VOID_TAGS else lines[:1]
        lines = head + center_lines(lines[len(head):], width)
    return lines, state


def render_html_line(text: str, state: HtmlBlockState | None,
                     width: int = 0) -> list[AnsiLine]:
    """HTML 块内容行 → 渲染行（隐藏块计数省略；``<math>`` 块收集原文）。"""
    if state is not None and state.math_lines is not None:
        # ``<math>`` 块：内容为 MathML 原文，CLOSE 时统一转换排版
        if text:
            state.math_lines.append(text)
        return []
    if state is not None and state.hidden_lines is not None and text:
        # ``<svg>`` 等隐藏内容块：保留原文（CLOSE 时提取标题/说明）
        state.hidden_lines.append(text)
    if state is not None and state.hidden:
        state.omitted += 1
        return []
    tag = state.tag if state is not None else ""
    rows = html_block_line(text, tag, state.attrs if state is not None else None)
    if state is not None and state.center:
        rows = center_lines(rows, width)
    return rows


def _render_mathml_lines(lines) -> list[AnsiLine]:
    """``<math>`` 块原文 → 渲染行（MathML → LaTeX → 二维排版）。

    - 内容是 MathML（含标签且可转换）→ 公式二维排版（带边框）；
    - 内容是普通公式源码（不含标签）→ 直接按 LaTeX 渲染；
    - 其余（无法解析的标签片段）→ 回退「标签行 + 原文行」，内容不丢。
    """
    src = "\n".join(str(ln) for ln in (lines or [])).strip()
    if not src:
        return []
    latex = None
    try:
        from ._mathml import mathml_to_latex
        latex = mathml_to_latex(src)
    except Exception:
        latex = None
    if latex is None and "<" not in src:
        # 非 MathML 标签片段（模型直接写公式源码）：按 LaTeX 渲染
        latex = src
    if latex:
        try:
            from .math import render_math_block
            return list(render_math_block(latex, label="公式"))
        except Exception:
            pass
    out: list[AnsiLine] = [
        AnsiLine.of("\u25b8 <math 数学标记>", _STYLE_HTML_TAG),
    ]
    for raw in src.split("\n"):
        out.append(AnsiLine.of("  " + raw, _blocks._STYLE_BQ))
    return out


def close_html_block(state: HtmlBlockState | None) -> list[AnsiLine]:
    """HTML 块结束 → 收尾行（隐藏块给出省略行数说明，``<math>`` 块转公式）。

    空元素单行块（``<img>`` / ``<input>``…）没有独立结束行，返回空列表
    （避免多出无意义空行）；其余块保留一个空行分隔。
    """
    if state is not None and state.math_lines is not None:
        return _render_mathml_lines(state.math_lines)
    if state is not None and state.tag in _CONTROL_TAGS:
        return []
    if state is not None and state.tag in _SILENT_BLOCK_TAGS:
        return []
    if state is not None and state.hidden_lines is not None:
        # ``<svg>`` 等：内容不逐行显示，但提取 ``<title>`` / ``<desc>`` 呈现语义
        title, desc = _html_title_desc(state.hidden_lines)
        out: list[AnsiLine] = []
        if title:
            line = AnsiLine.of("  ✎ 标题: ", _STYLE_MEDIA)
            line.append(title, _STYLE_PROGRESS_TEXT)
            out.append(line)
        if desc:
            line = AnsiLine.of("  ✎ 说明: ", _STYLE_MEDIA)
            line.append(desc, _STYLE_PROGRESS_TEXT)
            out.append(line)
        if state.omitted:
            out.append(AnsiLine.of(f"  … 已省略 {state.omitted} 行原始内容",
                                   _STYLE_OMITTED))
        return out
    if state is not None and state.void:
        if state.hidden and state.omitted:
            return [AnsiLine.of(f"  … 已省略 {state.omitted} 行原始内容",
                                _STYLE_OMITTED)]
        return []
    lines: list[AnsiLine] = []
    if state is not None and state.hidden and state.omitted:
        lines.append(AnsiLine.of(f"  … 已省略 {state.omitted} 行原始内容",
                                 _STYLE_OMITTED))
    return lines if lines else [AnsiLine.of("")]


# ── 模块接口 ─────────────────────────────────────────────


__all__ = [
    "HtmlBlockState", "TAG_LABELS", "tag_head_line", "progress_line",
    "input_line", "image_line", "media_source_line", "html_block_line",
    "split_inline_html_tag", "center_lines", "open_html_block",
    "render_html_line", "close_html_block",
    "button_line", "textarea_line", "textarea_head", "textarea_body",
    "select_lines", "option_lines", "object_line",
]
