"""块级渲染 — 标题/列表/引用/告示/折叠块/Front Matter/表注 → AnsiLine。

处理 TokenType：PARAGRAPH / HEADING / HR / BLOCKQUOTE / LIST_ITEM /
DEFINITION_ITEM / ADMONITION / DETAILS / EMPTY_LINE / FENCED_DIV /
FRONT_MATTER / TABLE_CAPTION。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from .inline import render_inline, inline_lines, inline_lines_with_baseline
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

#: 无序列表项目符号（按嵌套深度；真源 == 表现层数据注册表 ``nested_bullet``
#: 表——与 Rich 路径 ``_rendering/_blocks.py`` 同一真源）。修复前误用
#: ``bullet`` 表（仅 3 个符号）→ 第 4 层起符号与第 3 层重复（``▪`` 连续出现），
#: 与 Rich 路径（``▸ ▹ ◆``）不一致。
_NESTED_BULLETS = LiveMapping("nested_bullet")

#: 注册表 ``nested_bullet`` 缺席（被禁用/未注册）时的内置兜底快照。
_FALLBACK_BULLETS: tuple = ("\u2022", "\u25e6", "\u25aa", "\u25b8", "\u25b9", "\u25c6")


def bullet_symbol(depth: int) -> str:
    """块级无序列表项目符号（按嵌套深度；越界层取最后一项）。

    ``nested_bullet`` 注册表可按 Patch/Overlay 覆盖/禁用；缺席或为非序列时
    回退内置快照，保证渲染不崩（与 Rich 路径 ``_bullet_symbols`` 同语义）。
    """
    try:
        symbols = _NESTED_BULLETS
        n = len(symbols)
    except Exception:
        n = 0
    if n <= 0:
        symbols = _FALLBACK_BULLETS
        n = len(symbols)
    try:
        idx = min(max(int(depth) - 1, 0), n - 1)
    except (TypeError, ValueError):
        idx = 0
    try:
        return str(symbols[idx])
    except Exception:
        return _FALLBACK_BULLETS[0]

# ── 标题 ─────────────────────────────────────────────


def render_heading(token) -> list[AnsiLine]:
    """标题渲染（支持多行内容——setext 标题可跨多行，整段成为标题）。

    单行内容与既有行为完全一致；多行内容按软换行拆为多行标题（各行同标题
    样式）。
    """
    level = int(token.meta.get("level", 1))
    style = _HEADING_STYLES[min(max(level, 1), 6) - 1]
    return inline_lines(token.content, style)


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
    """行内文本 → 多行 ``AnsiLine``（真源：``inline.inline_lines``）。"""
    return inline_lines(text, base)


def render_paragraph_line(text: str) -> AnsiLine:
    """段落**单行**渲染（行级增量预览复用；调用方保证无 ``\\n``）。

    ``render_paragraph`` 整段解析行内标记后按软换行拆行；单行输入时二者
    等价——行级渲染便于流式预览按行缓存（见 ``LinePreviewCache``）。
    含行内多行块（二维公式）时取首行（预览增量路径请用
    ``render_paragraph_lines``，避免丢失公式的其余行）。
    """
    line = AnsiLine()
    for run in render_inline(text):
        line.append_run(run)
    return line


def render_paragraph_lines(text: str, base=None) -> list[AnsiLine]:
    """段落文本 → 渲染行（支持行内多行块：二维公式与周围文本按基线拼接）。

    与 ``render_paragraph`` 的差异：输入为**单行源文本**（不含软换行），
    供流式预览的行级增量渲染使用（``render_line`` 可返回多行）。
    """
    if not text:
        return [AnsiLine()]
    return inline_lines(text, base)


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
        bullet = bullet_symbol(depth)
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
        # ★ GFM：``[x]`` 后须为空白/行尾（``_parse_list_item_checkbox`` 已保证），
        #   故安全跳过 3 字符标记 + 其后空白——修复前固定跳过 4 个字符，
        #   ``- [x]done`` 会吞掉正文首字符。
        content = stripped[3:].lstrip()
    # 列表项内的 ATX 标题（``- # 标题``）：内容按标题样式渲染，去掉 ``#``
    heading = _parse_list_heading(content)
    if heading is not None:
        level, body = heading
        sub_lines, base_idx = inline_lines_with_baseline(
            body, _HEADING_STYLES[min(level, 6) - 1])
    else:
        sub_lines, base_idx = inline_lines_with_baseline(content)
    # 项目符号落在**基线行**：行内多行块（二维公式）的非基线行缩进对齐，
    # 保证 ``- $\frac{a}{b}$`` 的符号与分数线同行而非公式顶行。
    out_lines: list[AnsiLine] = []
    for idx, sub in enumerate(sub_lines):
        target = line if idx == base_idx else AnsiLine.of(prefix + "  ", _STYLE_BQ)
        for run in sub.runs:
            target.append_run(run)
        out_lines.append(target)
    if not out_lines:
        out_lines = [line]
    return out_lines


def render_definition_item(token) -> list[AnsiLine]:
    term = token.meta.get("term", "")
    sub_lines, base_idx = inline_lines_with_baseline(token.content)
    if term:
        line = AnsiLine.of(f"{term}: ", _STYLE_DEF_TERM)
    elif token.meta.get("continuation"):
        # 定义续段（空行后的缩进段落）→ 对齐定义内容列
        line = AnsiLine.of("    ", _STYLE_BQ)
    else:
        # 同一术语的后续定义（无 term）→ 缩进续行，不再重复 ``term:``
        line = AnsiLine.of("  ", _STYLE_BQ)
    out: list[AnsiLine] = []
    for idx, sub in enumerate(sub_lines):
        target = line if idx == base_idx else AnsiLine.of("    ", _STYLE_BQ)
        for run in sub.runs:
            target.append_run(run)
        out.append(target)
    if not out:
        out = [line]
    return out


# ── Front Matter（文档头元信息块） ────────────────────


def render_front_matter(token) -> list[AnsiLine]:
    """Front Matter 元信息块渲染（键值卡片）。

    ``▍ 元信息 (YAML)`` 头 + 对齐键值行；解析失败时原样缩进显示内容。
    预览超限（``preview_dropped``）时在头部后插入省略提示行（与其它块同源）。
    """
    text = token.content or ""
    fmt = str(token.meta.get("format", "yaml")).lower()
    items = parse_front_matter_items(text, fmt)
    out: list[AnsiLine] = [
        AnsiLine.of(f"\u258d 元信息 ({fmt.upper()})", _STYLE_FM_HEAD),
    ]
    out.extend(_preview_omitted(token.meta))
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
    out: list[AnsiLine] = []
    for seg in str(content).split("\n"):
        out.extend(render_blockquote_lines(seg, depth))
    return out


def render_blockquote_lines(text: str, depth: int = 0) -> list[AnsiLine]:
    """引用单行源文本 → 渲染行（支持行内多行块：公式非基线行同样带前缀）。

    前缀 ``│`` 按嵌套深度分级着色（外层亮 → 内层暗）。
    """
    prefix = "\u2502 " * max(1, depth + 1)
    style = bq_prefix_style(depth + 1)
    out: list[AnsiLine] = []
    for sub in inline_lines(text):
        line = AnsiLine.of(prefix, style)
        for run in sub.runs:
            line.append_run(run)
        out.append(line)
    return out or [AnsiLine.of(prefix, style)]


def render_blockquote_line(text: str, depth: int = 0) -> AnsiLine:
    """引用**单行**渲染（行级增量预览复用；调用方保证无 ``\\n``）。

    含行内多行块时取首行；预览增量路径请用 ``render_blockquote_lines``。
    """
    return render_blockquote_lines(text, depth)[0]


# ── HTML 块 ──────────────────────────────────────────
#
# HTML 块渲染（标签说明表 / 内容行 / 空元素 / 控件 / 居中）全部位于
# ``ansi._html_block``（单一真源）——本模块只负责 Markdown 块级元素，
# 不再提供 HTML 兼容入口（避免两模块互相导入形成依赖环）。


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
    """告示首行（``■ TYPE [title] 正文``；可折叠时为 ``▸``）。

    标题 / 同行文本非空时才追加分隔空格——修复前恒拼 ``"■ TYPE "``（无标题
    时行尾多一个空格：宽度测量含之，右侧对齐/换行预算偏移）。
    """
    atype = str(atype).upper()
    color = _ADMONITION_COLORS.get(atype, _STYLE_LIST_BULLET)
    glyph = "\u25b8" if collapsible else "\u25a0"
    head = AnsiLine.of(f"{glyph} {atype}", color)
    if title:
        head.append(" " + str(title), _STYLE_ADM_TITLE)
        return head
    if text:
        head.append(" ")
        for run in render_inline(text):
            head.append_run(run)
    return head


def render_container_body(text: str, indent: str = "  ") -> AnsiLine:
    """容器块正文行（前缀缩进 + 行内格式解析；预览行级缓存复用）。

    含行内多行块时取首行；多行场景请用 ``render_container_body_lines``。
    """
    return render_container_body_lines(text, indent)[0]


def render_container_body_lines(text: str, indent: str = "  ") -> list[AnsiLine]:
    """容器块正文 → 渲染行（支持行内多行块：公式各行均带缩进前缀）。"""
    out: list[AnsiLine] = []
    for sub in inline_lines(text):
        line = AnsiLine.of(indent, _STYLE_BQ)
        for run in sub.runs:
            line.append_run(run)
        out.append(line)
    return out or [AnsiLine.of(indent, _STYLE_BQ)]


def render_admonition_body(text: str) -> AnsiLine:
    """告示正文行（缩进；行级增量预览复用）。"""
    return render_container_body(text, "    ")


def render_admonition_body_lines(text: str) -> list[AnsiLine]:
    """告示正文 → 渲染行（支持行内多行块；预览增量路径使用）。"""
    return render_container_body_lines(text, "    ")


# ── 折叠块（DETAILS） ────────────────────────────────


def render_details_head(summary, is_open: bool = False) -> AnsiLine:
    """折叠块头行（展开 ``▼ summary`` / 折叠 ``▶ summary``）。"""
    head = AnsiLine.of("\u25bc " if is_open else "\u25b6 ", _STYLE_LIST_BULLET)
    for run in render_inline(str(summary)):
        head.append_run(run)
    return head


def render_details_body(text: str) -> AnsiLine:
    """折叠块正文行（2 空格缩进；流式预览行级缓存复用）。"""
    return render_container_body(text, "  ")


def render_details_body_lines(text: str) -> list[AnsiLine]:
    """折叠块正文 → 渲染行（支持行内多行块；预览增量路径使用）。"""
    return render_container_body_lines(text, "  ")


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
    lines = [render_details_head(summary, bool(token.meta.get("open", False)))]
    lines.extend(_preview_omitted(token.meta))
    lines.extend(_container_body_lines(token.meta.get("body_lines")))
    return lines


def _container_body_lines(body_lines) -> list[AnsiLine]:
    """容器正文行逐行渲染（2 空格缩进 + 行内格式；``<details>``/fenced div 共用）。"""
    out: list[AnsiLine] = []
    for seg in (body_lines or []):
        for sub in _inline_lines(str(seg)):
            line = AnsiLine.of("  ", _STYLE_BQ)
            for run in sub.runs:
                line.append_run(run)
            out.append(line)
    return out


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
    """Fenced Div 头行（``▪ TYPE 文本``；供容器正文嵌套渲染使用）。

    文本非空时才追加分隔空格（修复前恒拼 ``"▪ TYPE "``，无文本时行尾多空格）。
    """
    dtype = str(dtype).upper()
    color = _ADMONITION_COLORS.get(dtype, _STYLE_LIST_BULLET)
    head = AnsiLine.of(f"\u25aa {dtype}", color)
    if text:
        head.append(" ")
        for run in render_inline(str(text)):
            head.append_run(run)
    return head


def render_fenced_div_body(text: str) -> AnsiLine:
    """Fenced Div 正文行（2 空格缩进；流式预览行级缓存复用）。"""
    return render_container_body(text, "  ")


def render_fenced_div_body_lines(text: str) -> list[AnsiLine]:
    """Fenced Div 正文 → 渲染行（支持行内多行块；预览增量路径使用）。"""
    return render_container_body_lines(text, "  ")


def render_fenced_div(token) -> list[AnsiLine]:
    """Fenced Div（``:::type``）：``▪ TYPE`` 头 + 逐行缩进正文。

    ★ 修复（流式渲染引擎丢内容）：修复前正文行（FENCED_DIV_LINE）被 engine
    丢弃——仅显示容器头。现支持 meta["body_lines"]（正文行列表，缩进渲染）。
    """
    dtype = str(token.meta.get("type", "NOTE")).upper()
    head = render_fenced_div_head(dtype, token.content or "")
    lines = [head]
    lines.extend(_preview_omitted(token.meta))
    lines.extend(_container_body_lines(token.meta.get("body_lines")))
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
    "render_paragraph_lines",
    "render_list_item",
    "render_definition_item",
    "render_blockquote",
    "render_blockquote_line",
    "render_blockquote_lines",
    "render_admonition",
    "render_admonition_head",
    "render_admonition_body",
    "render_admonition_body_lines",
    "render_container_body",
    "render_container_body_lines",
    "render_details",
    "render_details_head",
    "render_details_body",
    "render_details_body_lines",
    "render_fenced_div",
    "render_fenced_div_head",
    "render_fenced_div_body",
    "render_fenced_div_body_lines",
    "bullet_symbol",
    "render_front_matter",
    "render_table_caption",
    "render_empty_line",
]
