"""代码块 — pygments 词法高亮 → 256 色 Style 行（无 Rich）。

复用：
  - ``renderer._rendering._code.get_lexer``（pygments lexer 缓存）
  - ``renderer._utils.get_code_style``（monokai 色板，去背景/文本样式）

映射：pygments token → Style（仅前景色，经 rgb_to_256 降级到 256 色体系）。
失败时降级为纯文本（dim）。

结构拆分（供流式预览增量复用）：
  - ``highlight_code_lines`` — 逐行高亮（无围栏/标题），流式预览按行缓存、
    仅渲染新增行；
  - ``render_fence_line`` / ``render_close_fence_line`` / ``render_title_line``
    — 围栏与标题行构造（整块渲染与预览共用，避免两处实现漂移）；
  - ``render_code_block`` — 组装整块（围栏 + 行 + 围栏），支持
    ``continuation``（被强制刷出的续段：不重复打开围栏/标题，仅补关闭围栏）。
"""

from __future__ import annotations

import logging

from .style import Style
from .style import rgb_to_256
from .helpers import AnsiLine

_logger = logging.getLogger(__name__)

# 围栏/标题栏/降级样式
_STYLE_FENCE = Style(fg=242, dim=True, italic=True)
_STYLE_DIM = Style(fg=244)
_STYLE_TITLE = Style(fg=110, bold=True)
_STYLE_HIGHLIGHT_BG = Style(fg=221)
_STYLE_OMITTED = Style(fg=238)
_STYLE_LINENO = Style(fg=240, dim=True)

#: Diff/patch 差异行样式（+ 绿 / - 红 / @@ 青 / 文件头 灰）
_STYLE_DIFF_ADD = Style(fg=84, bg=22)
_STYLE_DIFF_DEL = Style(fg=203, bg=52)
_STYLE_DIFF_HUNK = Style(fg=45, bold=True)
_STYLE_DIFF_META = Style(fg=245, bold=True)
_STYLE_DIFF_CTX = Style(fg=246)

#: 按行首语义着色的差异语言（pygments 通用 lexer 不做差异语义着色）
_DIFF_LANGS: frozenset = frozenset({"diff", "patch"})


def _diff_line(line: str) -> AnsiLine:
    """Diff/patch 行 → 语义着色行（与 Rich 路径 ``render_diff_line`` 同源语义）。

    - ``@@ ... @@`` hunk 头 → 青色粗体
    - ``+++`` / ``---`` 文件头 → 灰粗体
    - ``+`` 新增行 → 亮绿（深绿底）
    - ``-`` 删除行 → 亮红（深红底）
    - 其余上下文行 → 中性灰
    """
    if not line:
        return AnsiLine()
    if line.startswith("@@"):
        return AnsiLine.of(line, _STYLE_DIFF_HUNK)
    if line.startswith("+++") or line.startswith("---"):
        return AnsiLine.of(line, _STYLE_DIFF_META)
    first = line[0]
    if first == "+":
        return AnsiLine.of(line, _STYLE_DIFF_ADD)
    if first == "-":
        return AnsiLine.of(line, _STYLE_DIFF_DEL)
    return AnsiLine.of(line, _STYLE_DIFF_CTX)

_CODE_THEME = "monokai"

#: hex 颜色 → 256 色号缓存（主题颜色集合有限，避免重复解析 hex + 搜色板）
_HEX_256_CACHE: dict = {}
#: fg 色号 → Style 对象缓存（同色 Style 共享，避免每 token 重建 frozen dataclass）
_FG_STYLE_CACHE: dict = {}
#: ``(主题, pygments token 类型) → Style`` 缓存：token 类型数很少（数十个），
#: 缓存后每 token 只需一次 dict 查找——省去沿 parent 链查样式 + 两次子函数
#: 调用的固定开销（长行每帧数千 token 时收益显著）。
_TTYPE_STYLE_CACHE: dict = {}
_TTYPE_STYLE_CACHE_MAX = 8192
#: 「尚无样式」哨兵（``None`` 是合法样式值，需区分）
_NO_FG = object()
#: 缓存未命中哨兵（与合法的 ``None`` 样式区分）
_MISSING = object()
#: 单行「语言 + 主题 + 源码」→ 高亮行缓存。代码块内重复行（空行、``}``、
#: ``else:`` 等）命中后免词法高亮；有界，超限整体清空（简单、无淘汰开销）。
_LINE_HIGHLIGHT_CACHE: dict = {}
_LINE_HIGHLIGHT_CACHE_MAX = 4096


def _hex_to_256(hex_color: str) -> int | None:
    """#RRGGBB → 256 色号（None 表示无前景色，带结果缓存）。"""
    if not hex_color or not hex_color.startswith("#"):
        return None
    if hex_color in _HEX_256_CACHE:
        return _HEX_256_CACHE[hex_color]
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        _HEX_256_CACHE[hex_color] = None
        return None
    result = rgb_to_256(r, g, b)
    _HEX_256_CACHE[hex_color] = result
    return result


def _fg_style(fg: int | None) -> "Style | None":
    """256 色号 → Style（带缓存；None 表示无前景色）。"""
    if fg is None:
        return None
    style = _FG_STYLE_CACHE.get(fg)
    if style is None:
        style = Style(fg=fg)
        _FG_STYLE_CACHE[fg] = style
    return style


def _style_for_ttype(theme: str, ttype, pyg_style) -> "Style | None":
    """pygments token 类型 → ``Style``（沿 parent 链取最近定义的样式，带缓存）。

    pygments token 为层级类型（如 ``Comment.Single``），样式表常只定义基类型
    （``Comment``）——沿 ``parent`` 链向上找最近定义的样式。结果按
    ``(主题, token 类型)`` 缓存：token 类型集合很小，缓存后热路径只做一次
    dict 查找（省去每 token 的链遍历与 ``_hex_to_256``/``_fg_style`` 调用）。
    """
    key = (theme, ttype)
    hit = _TTYPE_STYLE_CACHE.get(key, _MISSING)
    if hit is not _MISSING:
        return hit
    fg = None
    t = ttype
    while t is not None:
        fg = _hex_to_256(pyg_style.styles.get(t, ""))
        if fg is not None:
            break
        t = getattr(t, "parent", None)
    style = _fg_style(fg)
    if len(_TTYPE_STYLE_CACHE) >= _TTYPE_STYLE_CACHE_MAX:
        _TTYPE_STYLE_CACHE.clear()
    _TTYPE_STYLE_CACHE[key] = style
    return style


def _highlight_line(line: str, lexer, pyg_style, theme: str = _CODE_THEME) -> AnsiLine:
    """单行代码词法高亮 → AnsiLine。

    pygments 2.20：样式存于 ``Style.styles``（token → '#RRGGBB' 字符串），
    无 ``get_style_for_token``。解析 hex → 256 色号（带缓存）。

    ★ 性能：连续**同样式** token 先在局部列表累积、样式切换时才构造一个
    ``Run``——修复前逐 token 调 ``AnsiLine.append``（每次构造 ``Run`` 并做
    相邻合并），对「长字符串按字符出 token」的词法器（如 JSON 的超长字符串）
    会退化为每字符一次 Run 构造（20 万字符单行 ~2.5s）。合并后同样式连续段
    只产生一个 Run。

    ★ 性能（token 类型样式缓存）：token → 样式的解析（parent 链 + hex → 256 色）
    按 ``(主题, token 类型)`` 缓存（``_style_for_ttype``），热路径每 token 一次
    dict 查找。
    """
    aline = AnsiLine()
    try:
        buf: list[str] = []
        cur_style = _NO_FG
        for ttype, value in lexer.get_tokens(line):
            if not value:
                continue
            val = value.rstrip("\n")
            if not val:
                continue
            style = _style_for_ttype(theme, ttype, pyg_style)
            if style is not cur_style:
                if buf:
                    aline.append("".join(buf), cur_style)
                buf = [val]
                cur_style = style
            else:
                buf.append(val)
        if buf:
            aline.append("".join(buf), cur_style)
        return aline
    except Exception:
        _logger.debug("代码高亮失败，降级纯文本", exc_info=True)
        return AnsiLine.of(line, _STYLE_DIM)


def code_line_number_width(total_lines: int, linenostart: int = 1,
                           linenostep: int = 1) -> int:
    """行号列宽（按最大**显示**行号位数；与 ``highlight_code_lines`` 同一规则）。

    ``total_lines`` 为逻辑总行数（1-based）；显示行号 = linenostart +
    (idx-1)*linenostep，故最大显示值取最后一行。
    """
    total = max(int(total_lines), 1)
    last_display = linenostart + (total - 1) * linenostep
    return max(2, len(str(max(last_display, 1))))


def highlight_code_lines(
    lines: list[str],
    lang: str = "",
    theme: str = _CODE_THEME,
    highlight_lines: list[int] | None = None,
    start_index: int = 1,
    linenos: bool = False,
    total_lines: int | None = None,
    linenostart: int = 1,
    linenostep: int = 1,
    use_cache: bool = True,
) -> list[AnsiLine]:
    """逐行高亮代码（不含围栏/标题），返回 AnsiLine 列表。

    Args:
        lines: 源码行列表（不含换行符）。
        lang: 语言名（空/``text`` 时降级为纯文本 dim）。
        theme: pygments 主题名。
        highlight_lines: 需高亮的行号（1-based，相对 ``start_index``）。
        start_index: 首行对应的逻辑行号（供流式预览增量渲染时续接行号）。
        linenos: 是否输出行号前缀（``{.numberLines}`` / ``{linenos}``）。
        total_lines: 逻辑总行数（决定行号列宽；None 时按 ``lines`` 推断）。
        linenostart: 行号起始值（``linenostart=3``）。
        linenostep: 行号步长（``linenostep=2``）。
        use_cache: 是否使用/写入逐行高亮缓存（``_LINE_HIGHLIGHT_CACHE``）。
            **流式预览的「活动行」应传 False**——活动行内容每帧变化，写入缓存
            只会持续膨胀（并可能触发整体清空，连带丢弃已确定行的缓存条目）。

    Returns:
        与 ``lines`` 等长的 AnsiLine 列表。
    """
    from src.renderer._rendering._code import get_lexer
    from src.renderer._utils import get_code_style

    if not lines:
        return []
    is_diff = (lang or "").lower() in _DIFF_LANGS
    # Diff/patch 走语义着色，不需要（且 patch 无）pygments lexer
    lexer = (None if is_diff
             else (get_lexer(lang) if lang and lang != "text" else None))
    hl = set(highlight_lines or [])
    pyg_style = get_code_style(theme) if lexer is not None else None
    if linenos:
        total = total_lines if total_lines else start_index + len(lines) - 1
        num_width = code_line_number_width(total, linenostart, linenostep)
    else:
        num_width = 0
    out: list[AnsiLine] = []
    for offset, src_line in enumerate(lines):
        idx = start_index + offset
        if is_diff:
            # Diff/patch：行首语义着色（+ 绿 / - 红 / @@ 青），与 Rich 路径
            # ``render_diff_line`` 语义一致（TUI 路径此前无差异高亮）。
            aline = _diff_line(src_line)
        elif lexer is not None:
            if not use_cache:
                aline = _highlight_line(src_line, lexer, pyg_style, theme)
            else:
                cache_key = (lang, theme, src_line)
                aline = _LINE_HIGHLIGHT_CACHE.get(cache_key)
                if aline is None:
                    aline = _highlight_line(src_line, lexer, pyg_style, theme)
                    if len(_LINE_HIGHLIGHT_CACHE) >= _LINE_HIGHLIGHT_CACHE_MAX:
                        _LINE_HIGHLIGHT_CACHE.clear()
                    _LINE_HIGHLIGHT_CACHE[cache_key] = aline
        else:
            aline = AnsiLine.of(src_line, _STYLE_DIM)
        if idx in hl:
            aline = _apply_highlight(aline)
        if num_width:
            display = linenostart + (idx - 1) * linenostep
            aline = _prepend_line_number(aline, display, num_width)
        out.append(aline)
    return out


def _prepend_line_number(line: AnsiLine, idx: int, width: int) -> AnsiLine:
    """在行首插入右对齐行号前缀（dim 样式，宽字符行号列固定宽度）。"""
    aline = AnsiLine.of(f"{idx:>{width}} ", _STYLE_LINENO)
    for run in line.runs:
        aline.append_run(run)
    return aline


def render_fence_line(lang: str = "") -> AnsiLine:
    """代码块打开围栏行（``\u0060\u0060\u0060lang [lang]``）。"""
    lang_tag = lang if lang and lang != "text" else ""
    line = AnsiLine.of(f"```{lang_tag}", _STYLE_FENCE)
    if lang_tag:
        line.append(f" [{lang}]", Style(fg=45, bold=True))
    return line


def render_close_fence_line() -> AnsiLine:
    """代码块关闭围栏行。"""
    return AnsiLine.of("```", _STYLE_FENCE)


def render_title_line(title: str) -> AnsiLine:
    """代码块标题行（``┌─ title``）。"""
    return AnsiLine.of(f"┌─ {title}", _STYLE_TITLE)


def render_omitted_line(omitted: int) -> AnsiLine:
    """预览截断提示行（超长块只预览最近若干行时的可见说明）。"""
    return AnsiLine.of(f"\u2026 前 {omitted} 行省略（本块结束后完整显示）",
                       _STYLE_OMITTED)


def render_code_block(
    source: str,
    lang: str = "",
    theme: str = _CODE_THEME,
    highlight_lines: list[int] | None = None,
    title: str = "",
    closed: bool = True,
    continuation: bool = False,
    linenos: bool = False,
    linenostart: int = 1,
    linenostep: int = 1,
) -> list[AnsiLine]:
    """渲染代码块（含标题栏与围栏）为 AnsiLine 列表。

    Args:
        source: 源码（多行，\\n 分隔）。
        lang: 语言名（可空）。
        theme: pygments 主题名。
        highlight_lines: 高亮行号（1-based）。
        title: 代码块标题（文件名等）。
        closed: 代码块是否已闭合。流式预览未闭合块时传 False——不渲染
            伪造的关闭围栏（```）。
        continuation: 是否为「被强制刷出的续段」——True 时不重复渲染标题栏
            与打开围栏（逻辑上仍是同一个代码块，仅因缓冲上限分段输出）。
        linenos: 是否输出行号前缀（``{.numberLines}`` / ``{linenos}``）。
        linenostart: 行号起始值。
        linenostep: 行号步长。

    Returns:
        渲染后的行列表。
    """
    out: list[AnsiLine] = []
    if title and not continuation:
        out.append(render_title_line(title))
    if not continuation:
        out.append(render_fence_line(lang))
    lines = source.split("\n") if source else []
    out.extend(highlight_code_lines(
        lines, lang, theme, highlight_lines, linenos=linenos,
        total_lines=len(lines), linenostart=linenostart, linenostep=linenostep,
    ))
    if closed:
        out.append(render_close_fence_line())
    return out


#: 高亮行背景（琥珀）——单独使用或与行内前景色合并
_STYLE_HL_BG_ONLY = Style(bg=58)
_STYLE_HL_BG_CODE = 58


def _with_highlight_bg(style: "Style | None") -> "Style":
    """为行内样式叠加高亮行背景（保留前景/粗斜体等，仅替换背景）。"""
    if style is None:
        return _STYLE_HL_BG_ONLY
    return Style(fg=style.fg, bg=_STYLE_HL_BG_CODE, bold=style.bold,
                 italic=style.italic, dim=style.dim,
                 underline=style.underline)


def _apply_highlight(line: AnsiLine) -> AnsiLine:
    """高亮行：整行叠加琥珀背景 + ``▸`` 前缀标记（更醒目）。"""
    from .helpers import Run
    runs = [Run("\u25b8 ", _STYLE_HIGHLIGHT_BG)]
    for run in line.runs:
        runs.append(Run(run.text, _with_highlight_bg(run.style)))
    return AnsiLine(runs)


def render_inline_code(text: str) -> AnsiLine:
    """内联代码 → AnsiLine（亮绿 + 粗体）。"""
    return AnsiLine.of(f" {text} ", Style(fg=46, bold=True))


__all__ = [
    "highlight_code_lines",
    "code_line_number_width",
    "render_fence_line",
    "render_close_fence_line",
    "render_title_line",
    "render_omitted_line",
    "render_code_block",
    "render_inline_code",
]
