"""块级渲染 — 标题/列表/引用/告示/折叠块 → AnsiLine。

处理 TokenType：PARAGRAPH / HEADING / HR / BLOCKQUOTE / LIST_ITEM /
DEFINITION_ITEM / ADMONITION / DETAILS / EMPTY_LINE / FENCED_DIV。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from .inline import render_inline
from src.presentation_data import LiveMapping

# ── 块级样式常量 ──────────────────────────────────────────
_HEADING_STYLES: list[Style] = [
    Style(fg=45, bold=True),    # 1 — 亮青
    Style(fg=39, bold=True),    # 2 — 亮蓝
    Style(fg=38, bold=True),    # 3 — 蓝
    Style(fg=242, bold=True),   # 4 — 中灰粗
    Style(fg=242),              # 5
    Style(fg=242),              # 6
]

_STYLE_HR = Style(fg=239)
_STYLE_BQ = Style(fg=242)
_STYLE_LIST_BULLET = Style(fg=214)
_STYLE_LIST_NUMBER = Style(fg=68)
_STYLE_TODO_UNCHECKED = Style(fg=242)
_STYLE_TODO_CHECKED = Style(fg=41)
_STYLE_TODO_CANCELLED = Style(fg=203, dim=True)
_STYLE_DEF_TERM = Style(fg=45, bold=True)
_STYLE_HTML_TAG = Style(fg=240, dim=True)
_STYLE_ADM_TITLE = Style(fg=252)

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


def render_hr(token) -> list[AnsiLine]:
    return [AnsiLine.of("\u2500" * 40, _STYLE_HR)]


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


def _inline_lines(text: str) -> list[AnsiLine]:
    """行内文本 → 多行 ``AnsiLine``（按 ``\\n`` 拆行；连续换行合并）。

    段落 / 告示 / 折叠块 / HTML 块的正文都可能含 ``<br>``（硬换行）或软换行
    ——``render_inline`` 产出的 ``LineBreakNode`` 渲染为 ``"\\n"``，与段落自身
    的软换行叠加会产生多余空行。此处把连续换行合并为一次换行（段落内不存在
    有意义的空行，段落在空行处已断开）。
    """
    out: list[AnsiLine] = []
    cur = AnsiLine()
    for run in render_inline(text):
        segs = (run.text or "").split("\n")
        for i, seg in enumerate(segs):
            if i > 0:
                if cur.runs or not out:
                    out.append(cur)
                cur = AnsiLine()
            if seg:
                cur.append(seg, run.style)
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
        head_style = _STYLE_LIST_BULLET
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
    line = AnsiLine.of(f"{term}: ", _STYLE_DEF_TERM)
    for run in first.runs:
        line.append_run(run)
    out: list[AnsiLine] = [line]
    for extra in sub_lines[1:]:
        el = AnsiLine.of("    ", _STYLE_BQ)
        for run in extra.runs:
            el.append_run(run)
        out.append(el)
    return out


# ── 引用 ─────────────────────────────────────────────


def render_blockquote(token, depth: int = 0) -> list[AnsiLine]:
    content = token.content if hasattr(token, "content") else ""
    return [render_blockquote_line(seg, depth)
            for seg in str(content).split("\n")]


def render_blockquote_line(text: str, depth: int = 0) -> AnsiLine:
    """引用**单行**渲染（行级增量预览复用；调用方保证无 ``\\n``）。"""
    prefix = "\u2502 " * max(1, depth + 1)
    line = AnsiLine.of(prefix, _STYLE_BQ)
    for run in render_inline(text):
        line.append_run(run)
    return line


# ── HTML 块 ──────────────────────────────────────────


def render_html_block_open(tag: str) -> list[AnsiLine]:
    """HTML 块起始标记（dim 标签行；块级标签本身弱化显示）。"""
    return [AnsiLine.of(f"\u25b8 <{tag}>", _STYLE_HTML_TAG)]


def render_html_block_line(text: str) -> list[AnsiLine]:
    """HTML 块内容行：内联渲染（保留行内格式 / 解码实体 / 隐藏注释）。"""
    out: list[AnsiLine] = []
    for sub in _inline_lines(text):
        line = AnsiLine.of("  ", _STYLE_BQ)
        for run in sub.runs:
            line.append_run(run)
        out.append(line)
    return out or [AnsiLine.of("  ", _STYLE_BQ)]


# ── Admonition ───────────────────────────────────────


def render_admonition(token) -> list[AnsiLine]:
    """告示块渲染：``■ TYPE [title]`` 头 + 缩进正文。

    支持 src parser 两种来源：
      - 引用风格 ``> [!NOTE]``：content 首行为正文首行；
      - Fenced 风格 ``!!! note "标题"``（meta ``title`` / ``collapsible``）：
        content 为全部正文行，标题在 meta 中（有标题时正文不再抢占头行）。
    """
    atype = str(token.meta.get("type", "NOTE")).upper()
    title = str(token.meta.get("title", "") or "")
    collapsible = bool(token.meta.get("collapsible", False))
    parts = str(token.content).split("\n")
    if not parts:
        parts = [""]
    if title:
        head = render_admonition_head(atype, "", title=title, collapsible=collapsible)
        body_lines = parts
    else:
        head = render_admonition_head(atype, parts[0], collapsible=collapsible)
        body_lines = parts[1:]
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


def render_details(token) -> list[AnsiLine]:
    """折叠块（<details>）：``▶ summary`` 头 + 逐行缩进正文。

    ★ 修复（流式渲染引擎丢内容）：修复前仅接受 meta["summary"] 且正文行被
    engine 直接丢弃——summary 不显示、正文全部丢失。现支持 meta["summary"]
    （头）与 meta["body_lines"]（正文行列表，缩进渲染）。

    ★ 一致性修复（预览截断提示）：预览正文超限被截断时 ``meta`` 携带
    ``preview_dropped``——在正文前插入省略提示行（与代码块预览同真源）。
    """
    summary = token.meta.get("summary", "")
    head = AnsiLine.of("\u25b6 ", _STYLE_LIST_BULLET)
    for run in render_inline(str(summary)):
        head.append_run(run)
    lines = [head]
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


def render_fenced_div(token) -> list[AnsiLine]:
    """Fenced Div（``:::type``）：``▪ TYPE`` 头 + 逐行缩进正文。

    ★ 修复（流式渲染引擎丢内容）：修复前正文行（FENCED_DIV_LINE）被 engine
    丢弃——仅显示容器头。现支持 meta["body_lines"]（正文行列表，缩进渲染）。
    """
    dtype = str(token.meta.get("type", "NOTE")).upper()
    color = _ADMONITION_COLORS.get(dtype, _STYLE_LIST_BULLET)
    head = AnsiLine.of(f"\u25aa {dtype} ", color)
    if token.content:
        for run in render_inline(str(token.content)):
            head.append_run(run)
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
    "render_fenced_div",
    "render_empty_line",
]
