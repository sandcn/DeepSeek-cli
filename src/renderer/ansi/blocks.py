"""块级渲染 — 标题/列表/引用/告示/折叠块/Front Matter/表注 → AnsiLine。

处理 TokenType：PARAGRAPH / HEADING / HR / BLOCKQUOTE / LIST_ITEM /
DEFINITION_ITEM / ADMONITION / DETAILS / EMPTY_LINE / FENCED_DIV /
FRONT_MATTER / TABLE_CAPTION。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from .inline import render_inline
from src.presentation_data import LiveMapping
from .._front_matter import parse_front_matter_items

# ── 块级样式常量 ──────────────────────────────────────────
_HEADING_STYLES: list[Style] = [
    Style(fg=45, bold=True),    # 1 — 亮青
    Style(fg=39, bold=True),    # 2 — 亮蓝
    Style(fg=38, bold=True),    # 3 — 蓝
    Style(fg=242, bold=True),   # 4 — 中灰粗
    Style(fg=242),              # 5
    Style(fg=242),              # 6
]

_STYLE_HR = Style(fg=240)
_STYLE_HR_FADE = Style(fg=237)
_STYLE_BQ = Style(fg=242)
_STYLE_LIST_BULLET = Style(fg=214)

#: 引用块前缀按嵌套深度的颜色分级（外层亮 → 内层暗，视觉层次更清晰）
_BQ_PREFIX_STYLES: tuple = (
    Style(fg=246), Style(fg=243), Style(fg=240), Style(fg=238),
)
#: 无序列表符号按嵌套深度的颜色分级
_BULLET_STYLES: tuple = (
    Style(fg=214), Style(fg=208), Style(fg=203),
)


def bq_prefix_style(depth: int) -> Style:
    """引用块前缀样式（按嵌套深度分级；``depth`` 为 1-based）。"""
    try:
        d = int(depth)
    except (TypeError, ValueError):
        d = 1
    return _BQ_PREFIX_STYLES[max(0, min(d - 1, len(_BQ_PREFIX_STYLES) - 1))]


def bullet_style(depth: int) -> Style:
    """无序列表项目符号样式（按嵌套深度分级；``depth`` 为 1-based）。"""
    try:
        d = int(depth)
    except (TypeError, ValueError):
        d = 1
    return _BULLET_STYLES[max(0, min(d - 1, len(_BULLET_STYLES) - 1))]
_STYLE_LIST_NUMBER = Style(fg=68)
_STYLE_TODO_UNCHECKED = Style(fg=242)
_STYLE_TODO_CHECKED = Style(fg=41)
_STYLE_TODO_CANCELLED = Style(fg=203, dim=True)
_STYLE_DEF_TERM = Style(fg=45, bold=True)
_STYLE_HTML_TAG = Style(fg=240, dim=True)
_STYLE_ADM_TITLE = Style(fg=252)

# ── Front Matter / 表注 ─────────────────────────────────
_STYLE_FM_HEAD = Style(fg=110, bold=True)
_STYLE_FM_KEY = Style(fg=45, bold=True)
_STYLE_FM_VAL = Style(fg=252)
_STYLE_CAPTION = Style(fg=245, italic=True)

# Admonition 类型 → 颜色
_ADMONITION_COLORS: dict[str, Style] = {
    "NOTE": Style(fg=45, bold=True),
    "TIP": Style(fg=41, bold=True),
    "IMPORTANT": Style(fg=68, bold=True),
    "WARNING": Style(fg=220, bold=True),
    "CAUTION": Style(fg=196, bold=True),
    "CITE": Style(fg=242, bold=True),
    "INFO": Style(fg=75, bold=True),
    "SUCCESS": Style(fg=41, bold=True),
    "QUESTION": Style(fg=75, bold=True),
    "BUG": Style(fg=196, bold=True),
    "DANGER": Style(fg=196, bold=True),
}

# 无序列表项目符号（按深度；真源 == 表现层数据注册表 bullet 表）
_BULLETS = LiveMapping("bullet")

# ── 标题 ─────────────────────────────────────────────


def render_heading(token) -> list[AnsiLine]:
    level = int(token.meta.get("level", 1))
    style = _HEADING_STYLES[min(max(level, 1), 6) - 1]
    runs = render_inline(token.content, style)
    line = AnsiLine(runs)
    return [line]


def render_hr(token, width: int = 0) -> list[AnsiLine]:
    """分隔线：按终端宽度整宽渲染（两端渐隐，中部主色）。

    ★ 美化：「──────」固定 40 列改为**整宽自适应**，两端各留一段更暗的
    渐隐段，视觉上更接近现代终端分隔线（``width<=0`` 时回退 40 列，
    不依赖终端宽度，保证无宽度上下文也能渲染）。
    """
    total = width if isinstance(width, int) and width > 0 else 40
    fade = min(8, max(0, total // 6))
    line = AnsiLine()
    if fade:
        line.append("\u2500" * fade, _STYLE_HR_FADE)
    line.append("\u2500" * max(0, total - 2 * fade), _STYLE_HR)
    if fade:
        line.append("\u2500" * fade, _STYLE_HR_FADE)
    return [line]


# ── 段落 ─────────────────────────────────────────────


def render_paragraph(token) -> list[AnsiLine]:
    """段落渲染：整段行内解析后按软换行拆行。

    ★ 修复：修复前对 ``content.split("\\n")`` 逐行独立调用 ``render_inline``，
    跨软换行的行内标记（``**加粗\\n跨行**``、`` `code\\ncode` ``）无法配对，
    标记会原样泄漏到输出（渲染成 `*加粗` / `跨行*`）。整段一次性解析可让
    行内标记跨软换行配对（CommonMark 段落语义），再按 ``\\n`` 把解析结果
    拆回多行（保持终端"软换行=换行"的既有呈现）。
    """
    if not token.content:
        return [AnsiLine()]
    return _inline_lines(token.content)


def _inline_lines(text: str, base=None) -> list[AnsiLine]:
    """行内文本 → 多行 ``AnsiLine``（按 ``\\n`` 拆行；连续换行合并）。

    段落 / 告示 / 折叠块 / HTML 块的正文都可能含 ``<br>``（硬换行）或软换行
    ——``render_inline`` 产出的 ``LineBreakNode`` 渲染为 ``"\\n"``，与段落自身
    的软换行叠加会产生多余空行。此处把连续换行合并为一次换行（段落内不存在
    有意义的空行，段落在空行处已断开）。

    ``base`` 为基础样式（无行内格式的文本以此着色；None 用默认样式）。
    """
    out: list[AnsiLine] = []
    cur = AnsiLine()
    for run in render_inline(text, base):
        link = getattr(run, "link", None)
        segs = (run.text or "").split("\n")
        for i, seg in enumerate(segs):
            if i > 0:
                if cur.runs or not out:
                    out.append(cur)
                cur = AnsiLine()
            if seg:
                cur.append(seg, run.style, link)
    if cur.runs or not out:
        out.append(cur)
    return out


def render_paragraph_line(text: str) -> AnsiLine:
    """段落**单行**渲染（行级增量预览复用；调用方保证无 ``\\n``）。

    ``render_paragraph`` 整段解析行内标记后按软换行拆行；单行输入时二者
    等价——行级渲染便于流式预览按行缓存（见 ``LinePreviewCache``）。
    """
    line = AnsiLine()
    for run in render_inline(text):
        line.append_run(run)
    return line


# ── 列表 ─────────────────────────────────────────────


def _parse_list_heading(text: str) -> tuple[int, str] | None:
    """列表项内容形如 ATX 标题（``# 标题``）→ ``(level, 文本)``，否则 None。"""
    s = text.lstrip()
    if not s.startswith('#'):
        return None
    level = 0
    i = 0
    while i < len(s) and s[i] == '#':
        level += 1
        i += 1
    if not (1 <= level <= 6) or i >= len(s) or s[i] != ' ':
        return None
    body = s[i + 1:].strip()
    while body.endswith('#'):
        body = body[:-1].rstrip()
    return level, body


def render_list_item(token) -> list[AnsiLine]:
    meta = token.meta
    depth = int(meta.get("depth", 1))
    indent = int(meta.get("indent", 0))
    # 列表续行（continuation）：对齐列表内容的缩进行（无项目符号）
    if meta.get("continuation"):
        prefix = "  " * max(0, indent) + "  "
        out: list[AnsiLine] = []
        for sub in _inline_lines(token.content.lstrip()):
            line = AnsiLine.of(prefix, _STYLE_BQ)
            for run in sub.runs:
                line.append_run(run)
            out.append(line)
        return out or [AnsiLine.of(prefix, _STYLE_BQ)]
    # 缩进按 indent；项目符号按嵌套深度（depth 为 1-based）
    prefix = "  " * max(0, indent)
    if meta.get("bullet"):
        bullet = _BULLETS[min(max(depth - 1, 0), len(_BULLETS) - 1)]
        head = f"{prefix}{bullet} "
        head_style = bullet_style(depth)
    else:
        number = meta.get("number", 1)
        head = f"{prefix}{number}. "
        head_style = _STYLE_LIST_NUMBER
    line = AnsiLine.of(head, head_style)
    content = token.content
    # 待办 checkbox
    stripped = content.lstrip()
    if meta.get("todo"):
        checked = meta.get("checked", False)
        cancelled = meta.get("cancelled", False)
        if cancelled:
            checkbox = "[~]"
            cstyle = _STYLE_TODO_CANCELLED
        elif checked:
            checkbox = "[x]"
            cstyle = _STYLE_TODO_CHECKED
        else:
            checkbox = "[ ]"
            cstyle = _STYLE_TODO_UNCHECKED
        line.append(checkbox + " ", cstyle)
        content = stripped[4:].lstrip() if len(stripped) > 4 else ""
    # 列表项内的 ATX 标题（``- # 标题``）：内容按标题样式渲染，去掉 ``#``
    heading = _parse_list_heading(content)
    if heading is not None:
        level, body = heading
        sub_lines = _inline_lines(body, _HEADING_STYLES[min(level, 6) - 1])
    else:
        sub_lines = _inline_lines(content)
    first = sub_lines[0] if sub_lines else AnsiLine()
    for run in first.runs:
        line.append_run(run)
    out_lines: list[AnsiLine] = [line]
    for extra in sub_lines[1:]:
        el = AnsiLine.of(prefix + "  ", _STYLE_BQ)
        for run in extra.runs:
            el.append_run(run)
        out_lines.append(el)
    return out_lines


def render_definition_item(token) -> list[AnsiLine]:
    term = token.meta.get("term", "")
    sub_lines = _inline_lines(token.content)
    first = sub_lines[0] if sub_lines else AnsiLine()
    if term:
        line = AnsiLine.of(f"{term}: ", _STYLE_DEF_TERM)
    elif token.meta.get("continuation"):
        # 定义续段（空行后的缩进段落）→ 对齐定义内容列
        line = AnsiLine.of("    ", _STYLE_BQ)
    else:
        # 同一术语的后续定义（无 term）→ 缩进续行，不再重复 ``term:``
        line = AnsiLine.of("  ", _STYLE_BQ)
    for run in first.runs:
        line.append_run(run)
    out: list[AnsiLine] = [line]
    for extra in sub_lines[1:]:
        el = AnsiLine.of("    ", _STYLE_BQ)
        for run in extra.runs:
            el.append_run(run)
        out.append(el)
    return out


# ── Front Matter（文档头元信息块） ────────────────────


def render_front_matter(token) -> list[AnsiLine]:
    """Front Matter 元信息块渲染（键值卡片）。

    ``▍ 元信息 (YAML)`` 头 + 对齐键值行；解析失败时原样缩进显示内容。
    """
    text = token.content or ""
    fmt = str(token.meta.get("format", "yaml")).lower()
    items = parse_front_matter_items(text, fmt)
    out: list[AnsiLine] = [
        AnsiLine.of(f"\u258d 元信息 ({fmt.upper()})", _STYLE_FM_HEAD),
    ]
    if not items:
        if text.strip():
            for seg in _inline_lines(text):
                body = AnsiLine.of("  ", _STYLE_BQ)
                for run in seg.runs:
                    body.append_run(run)
                out.append(body)
        return out
    kw = max((len(k) for k, _ in items), default=0)
    pad = " " * (kw + 3)
    for key, value in items:
        value_lines = (value or "").split("\n")
        for li, vline in enumerate(value_lines):
            line = AnsiLine.of("  ", _STYLE_BQ)
            if li == 0 and key:
                line.append(f"{key:<{kw}}", _STYLE_FM_KEY)
                line.append(" : ", _STYLE_BQ)
            elif li > 0:
                line.append(pad, _STYLE_BQ)
            for run in render_inline(vline, _STYLE_FM_VAL):
                line.append_run(run)
            out.append(line)
    return out


def render_table_caption(token) -> list[AnsiLine]:
    """表格表注（``: 说明`` / ``Table: 说明``）渲染为缩进斜体说明行。"""
    text = token.content or ""
    line = AnsiLine.of("  ", _STYLE_BQ)
    for run in render_inline(text, _STYLE_CAPTION):
        line.append_run(run)
    return [line]


# ── 引用 ─────────────────────────────────────────────


def render_blockquote(token, depth: int = 0) -> list[AnsiLine]:
    content = token.content if hasattr(token, "content") else ""
    return [render_blockquote_line(seg, depth)
            for seg in str(content).split("\n")]


def render_blockquote_line(text: str, depth: int = 0) -> AnsiLine:
    """引用**单行**渲染（行级增量预览复用；调用方保证无 ``\\n``）。

    前缀 ``│`` 按嵌套深度分级着色（外层亮 → 内层暗）。
    """
    prefix = "\u2502 " * max(1, depth + 1)
    line = AnsiLine.of(prefix, bq_prefix_style(depth + 1))
    for run in render_inline(text):
        line.append_run(run)
    return line


# ── HTML 块 ──────────────────────────────────────────

#: 结构化 HTML 块标签 → 打开行说明（``▸ <tag>``）
_HTML_OPEN_LABELS: dict[str, str] = {
    "figure": "figure 插图",
    "figcaption": "figcaption 图注",
    "dl": "dl 定义列表",
    "dt": "dt 术语",
    "dd": "dd 定义",
    "ul": "ul 列表",
    "ol": "ol 有序列表",
    "li": "li 列表项",
    "video": "video 视频",
    "audio": "audio 音频",
    "iframe": "iframe 内嵌",
    "picture": "picture 图片",
    "source": "source 媒体源",
    "track": "track 字幕",
    "caption": "caption 表注",
    "progress": "progress 进度",
    "meter": "meter 度量",
}


def render_html_block_open(tag: str) -> list[AnsiLine]:
    """HTML 块起始标记（dim 标签行；结构化标签附语义说明）。"""
    label = _HTML_OPEN_LABELS.get(tag, tag)
    return [AnsiLine.of(f"\u25b8 <{label}>", _STYLE_HTML_TAG)]


def render_html_block_line(text: str, tag: str = "") -> list[AnsiLine]:
    """HTML 块内容行：按标签语义化渲染 + 行内 Markdown（实体解码/注释隐藏）。

    ``tag`` 为块级标签；若整行为单层行内标签（``<li>x</li>`` /
    ``<figcaption>x</figcaption>`` / ``<dt>x</dt>`` / ``<dd>x</dd>``）则按该
    子标签语义渲染（列表符号 / 图注 / 术语 / 定义缩进）。
    """
    effective_tag = tag
    content_text = text
    split = split_inline_html_tag(text)
    if split is not None and split[0] in _HTML_INLINE_SEMANTIC_TAGS:
        effective_tag, content_text = split
    if effective_tag == "li":
        prefix, base = "  \u2022 ", _STYLE_LIST_BULLET
    elif effective_tag == "dt":
        prefix, base = "  ", _STYLE_DEF_TERM
    elif effective_tag == "dd":
        prefix, base = "      ", _STYLE_BQ
    elif effective_tag in ("figcaption", "caption"):
        prefix, base = "  ", _STYLE_CAPTION
    elif effective_tag in ("source", "track"):
        prefix, base = "    ", _STYLE_HTML_TAG
    elif effective_tag in ("video", "audio", "iframe", "picture"):
        prefix, base = "  ", _STYLE_HTML_TAG
    else:
        prefix, base = "  ", _STYLE_BQ
    out: list[AnsiLine] = []
    for sub in _inline_lines(content_text, base):
        line = AnsiLine.of(prefix, base)
        for run in sub.runs:
            line.append_run(run)
        out.append(line)
    return out or [AnsiLine.of(prefix, base)]


#: 行内单层标签中具备语义样式的标签集合
_HTML_INLINE_SEMANTIC_TAGS: frozenset = frozenset({
    "li", "dt", "dd", "figcaption", "caption", "source", "track",
    "video", "audio", "iframe", "picture",
})


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


# ── Admonition ───────────────────────────────────────


def render_admonition(token) -> list[AnsiLine]:
    """告示块渲染：``■ TYPE [title]`` 头 + 缩进正文。

    支持 src parser 两种来源：
      - 引用风格 ``> [!NOTE]``：``meta["head_text"]`` 为 ``[!TYPE]`` 同行
        文本（头部标题），``content`` 为正文行；
      - Fenced 风格 ``!!! note "标题"``（meta ``title`` / ``collapsible``）：
        ``content`` 为全部正文行，标题在 meta 中。

    未提供 ``head_text``（旧契约）时回退「首行作头部标题」语义。
    """
    atype = str(token.meta.get("type", "NOTE")).upper()
    title = str(token.meta.get("title", "") or "")
    collapsible = bool(token.meta.get("collapsible", False))
    parts = str(token.content).split("\n") if token.content else []
    head_text = token.meta.get("head_text", None)
    if head_text is None:
        head_text = parts[0] if parts else ""
        body_lines = parts[1:]
    else:
        body_lines = parts
    head = render_admonition_head(atype, str(head_text or ""),
                                  title=title, collapsible=collapsible)
    lines: list[AnsiLine] = [head]
    lines.extend(_preview_omitted(token.meta))
    for seg in body_lines:
        for sub in _inline_lines(str(seg)):
            body = AnsiLine.of("    ", _STYLE_BQ)
            for run in sub.runs:
                body.append_run(run)
            lines.append(body)
    return lines


def render_admonition_head(atype: str, text: str, title: str = "",
                           collapsible: bool = False) -> AnsiLine:
    """告示首行（``■ TYPE [title] 正文``；可折叠时为 ``▸``）。"""
    atype = str(atype).upper()
    color = _ADMONITION_COLORS.get(atype, _STYLE_LIST_BULLET)
    glyph = "\u25b8" if collapsible else "\u25a0"
    head = AnsiLine.of(f"{glyph} {atype} ", color)
    if title:
        head.append(str(title), _STYLE_ADM_TITLE)
        return head
    for run in render_inline(text):
        head.append_run(run)
    return head


def render_admonition_body(text: str) -> AnsiLine:
    """告示正文行（缩进；行级增量预览复用）。"""
    body = AnsiLine.of("    ", _STYLE_BQ)
    for run in render_inline(text):
        body.append_run(run)
    return body


# ── 折叠块（DETAILS） ────────────────────────────────


def render_details_head(summary) -> AnsiLine:
    """折叠块头行（``▶ summary``）。"""
    head = AnsiLine.of("\u25b6 ", _STYLE_LIST_BULLET)
    for run in render_inline(str(summary)):
        head.append_run(run)
    return head


def render_details(token) -> list[AnsiLine]:
    """折叠块（<details>）：``▶ summary`` 头 + 逐行缩进正文。

    ★ 修复（流式渲染引擎丢内容）：修复前仅接受 meta["summary"] 且正文行被
    engine 直接丢弃——summary 不显示、正文全部丢失。现支持 meta["summary"]
    （头）与 meta["body_lines"]（正文行列表，缩进渲染）。

    ★ 一致性修复（预览截断提示）：预览正文超限被截断时 ``meta`` 携带
    ``preview_dropped``——在正文前插入省略提示行（与代码块预览同真源）。

    注：提交路径的正文由引擎经 ``_render_nested_blocks`` 以完整 Markdown
    语义递归渲染（``meta["body_tokens"]``），本函数服务流式预览与旧接口。
    """
    summary = token.meta.get("summary", "")
    lines = [render_details_head(summary)]
    lines.extend(_preview_omitted(token.meta))
    for seg in (token.meta.get("body_lines") or []):
        for sub in _inline_lines(str(seg)):
            body = AnsiLine.of("  ", _STYLE_BQ)
            for run in sub.runs:
                body.append_run(run)
            lines.append(body)
    return lines


def _preview_omitted(meta) -> list[AnsiLine]:
    """预览截断提示行（无截断时返回空列表；与代码块预览同一真源）。"""
    try:
        dropped = int(meta.get("preview_dropped", 0) or 0)
    except (TypeError, ValueError):
        return []
    if not dropped:
        return []
    from .code import render_omitted_line
    return [render_omitted_line(dropped)]


# ── FencedDiv ────────────────────────────────────────


def render_fenced_div_head(dtype: str, text: str = "") -> AnsiLine:
    """Fenced Div 头行（``▪ TYPE 文本``；供容器正文嵌套渲染使用）。"""
    dtype = str(dtype).upper()
    color = _ADMONITION_COLORS.get(dtype, _STYLE_LIST_BULLET)
    head = AnsiLine.of(f"\u25aa {dtype} ", color)
    if text:
        for run in render_inline(str(text)):
            head.append_run(run)
    return head


def render_fenced_div(token) -> list[AnsiLine]:
    """Fenced Div（``:::type``）：``▪ TYPE`` 头 + 逐行缩进正文。

    ★ 修复（流式渲染引擎丢内容）：修复前正文行（FENCED_DIV_LINE）被 engine
    丢弃——仅显示容器头。现支持 meta["body_lines"]（正文行列表，缩进渲染）。
    """
    dtype = str(token.meta.get("type", "NOTE")).upper()
    head = render_fenced_div_head(dtype, token.content or "")
    lines = [head]
    lines.extend(_preview_omitted(token.meta))
    for seg in (token.meta.get("body_lines") or []):
        for sub in _inline_lines(str(seg)):
            body = AnsiLine.of("  ", _STYLE_BQ)
            for run in sub.runs:
                body.append_run(run)
            lines.append(body)
    return lines


# ── 空行 ─────────────────────────────────────────────


def render_empty_line(token) -> list[AnsiLine]:
    return [AnsiLine.of("")]


__all__ = [
    "render_heading",
    "render_hr",
    "bq_prefix_style",
    "bullet_style",
    "render_paragraph",
    "render_paragraph_line",
    "render_list_item",
    "render_definition_item",
    "render_blockquote",
    "render_blockquote_line",
    "render_html_block_open",
    "render_html_block_line",
    "render_admonition",
    "render_admonition_head",
    "render_admonition_body",
    "render_details",
    "render_details_head",
    "render_fenced_div",
    "render_fenced_div_head",
    "render_front_matter",
    "render_table_caption",
    "render_empty_line",
]
