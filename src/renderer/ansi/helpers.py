"""ansi 工具 — Run/AnsiLine 输出模型 + 换行/截断/测量 + ANSI→样式解析。

本模块自绘 ANSI（零 Rich、零 tui 依赖）：输出模型为 AnsiLine（Run 序列），
宽度依据统一用 renderer._utils.cjk_display_width，样式用本包自含 Style。
"""

from __future__ import annotations

import re
from dataclasses import field

from src._compat import dataclass

from src.renderer._utils import cjk_display_width, expand_tabs
from src._text_width import char_width
from .style import Style


# ═══════════════════════════════════════════════════════════
# Run / AnsiLine — 输出模型
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True)
class Run:
    """一段带样式的文本。

    ``link`` 非空时表示该段文本是超链接（终端 OSC 8 可点击）——宽度计算
    不计入（由渲染层包裹控制序列），换行/截断时随可见文本一起保留。

    ★ 内存（长会话）：``slots=True`` —— 无实例 ``__dict__``（CPython 3.9 下
    每个空实例字典约 104B）。提交历史每行至少一个 Run，长会话下省出的常驻
    内存可观。显示宽度缓存 ``_w`` 原为 ``__post_init__`` 动态设置的实例属性，
    现为显式字段（``init=False``/``compare=False``/``repr=False``——不参与
    构造签名、相等性与 repr，与原行为一致）。
    """

    text: str
    style: Style | None = None
    #: 超链接 URL（OSC 8；None = 普通文本）
    link: str | None = None
    #: 行内多行块（二维公式等）：非 None 时 ``text`` 为**展平降级文本**
    #: （不认识 block 的消费者仍能显示内容），``inline_lines`` 识别后按基线
    #: 水平拼接其多行布局。
    block: object = field(default=None, compare=False)
    #: 显示宽度缓存（-1 = 未计算）
    _w: int = field(init=False, compare=False, repr=False, default=-1)

    def __post_init__(self) -> None:
        # ★ 显示错乱修复（2026-10-05）：文本规范化——展开制表符 / 剔除回车
        #   （控制字符宽度 0 但终端按 tab 展开/回行首，宽度与渲染分裂 →
        #   含 `\t` 的行触发终端自动换行、后续行错位）。见
        #   ``renderer._utils.expand_tabs``。
        if "\t" in self.text or "\r" in self.text:
            self.text = expand_tabs(self.text)
        # 显示宽度缓存（-1 = 未计算）。不可变文本段，惰性计算一次；
        # ``AnsiLine.append`` 合并文本时新建 Run 对象，缓存自然失效。
        self._w = -1

    def render(self) -> str:
        if self.style:
            return self.style.apply(self.text)
        return self.text

    @property
    def width(self) -> int:
        w = self._w
        if w < 0:
            w = cjk_display_width(self.text)
            self._w = w
        return w


class AnsiLine:
    """一行输出（Run 序列）。"""

    __slots__ = ("runs", "_esc_checked", "_w")

    def __init__(self, runs: list[Run] | None = None) -> None:
        self.runs: list[Run] = list(runs) if runs else []
        # ANSI 消毒缓存标记：True 表示已确认本行（当前内容）不含原始转义序列，
        # 消毒路径可直接跳过扫描。内容经 append/append_run 修改时重置。
        self._esc_checked = False
        # 显示宽度缓存（-1 = 未计算）；``append`` 修改内容时失效。
        self._w = -1

    @classmethod
    def of(cls, text: str, style: Style | None = None) -> "AnsiLine":
        return cls([Run(text, style)])

    def append(self, text: str, style: Style | None = None,
               link: str | None = None) -> None:
        if not text:
            return
        self._esc_checked = False
        self._w = -1
        if (self.runs and self.runs[-1].style == style
                and self.runs[-1].link == link):
            self.runs[-1] = Run(self.runs[-1].text + text, style, link)
            return
        self.runs.append(Run(text, style, link))

    def append_run(self, run: Run) -> None:
        if run and run.text:
            self.append(run.text, run.style, getattr(run, "link", None))

    def render(self) -> str:
        return "".join(r.render() for r in self.runs)

    @property
    def plain(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def width(self) -> int:
        w = self._w
        if w < 0:
            w = sum(r.width for r in self.runs)
            self._w = w
        return w

    def exceeds_width(self, limit: int) -> bool:
        """显示宽度是否超过 ``limit``（可提前退出的轻量判定）。

        ★ 超长单行性能：仅需「是否超宽」的场景（预览行是否需要换行）不必
        求出整行精确宽度——逐字符累加到达上限即返回，成本 O(limit) 而非
        O(整行字符数)（超长活动行 4096 字符 → 判 CJK 只需前 ``limit/2`` 个）。
        已知宽度缓存时直接比较。
        """
        w = self._w
        if w >= 0:
            return w > limit
        total = 0
        for run in self.runs:
            text = run.text
            if not text:
                continue
            if text.isascii() and text.isprintable():
                total += len(text)
                if total > limit:
                    return True
                continue
            for ch in text:
                total += char_width(ch)
                if total > limit:
                    return True
        return False

    def clone(self) -> "AnsiLine":
        return AnsiLine(list(self.runs))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"AnsiLine({self.plain!r})"


# ═══════════════════════════════════════════════════════════
# 宽度 / 换行 / 截断
# ═══════════════════════════════════════════════════════════


def visual_width(text: str) -> int:
    """纯字符串显示宽度（剥离 ANSI 后测量）。"""
    return cjk_display_width(strip_ansi(text))


_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)

#: 「无前驱样式」哨兵（避免用 None 兼作哨兵——None 是合法的无样式值，
#: 需要区分「尚无前驱」与「前驱样式为 None」）
_NO_STYLE = object()


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _uniform_line(chars: list, styles: list, start: int, end: int,
                  links: list | None = None) -> AnsiLine:
    """按样式分段构造一行（``chars``/``styles`` 平行列表的 [start, end) 区间）。

    相邻同样式字符合并为同一 run（与 ``wrap_line`` 通用路径的输出结构一致）；
    传入 ``links``（超链接平行列表）时把链接差异也视为分段边界。
    """
    line = AnsiLine()
    if end <= start:
        return line
    seg_style = styles[start]
    seg_link = links[start] if links is not None else None
    seg_start = start
    for k in range(start + 1, end):
        st = styles[k]
        lk = links[k] if links is not None else None
        if st != seg_style or lk != seg_link:
            line.append("".join(chars[seg_start:k]), seg_style, seg_link)
            seg_style = st
            seg_link = lk
            seg_start = k
    line.append("".join(chars[seg_start:end]), seg_style, seg_link)
    return line


def _wrap_uniform(chars: list, styles: list, per_line: int,
                  word_break: bool, links: list | None = None) -> list[AnsiLine]:
    """等宽字符行的换行（每字符显示宽度相同，按固定步长切分）。

    调用方（``wrap_line``）保证 ``chars`` 中每个字符显示宽度相同且无强制
    换行符；``word_break=True``（宽 1 的 ASCII 行）时按「行内最后一个空格」
    优先断行——语义与通用路径一致（空格不保留在行尾、行首空格不作断点）。
    """
    n = len(chars)
    lines: list[AnsiLine] = []
    i = 0
    while i < n:
        end = i + per_line
        if end >= n:
            lines.append(_uniform_line(chars, styles, i, n, links))
            break
        if word_break:
            # 断点范围含 end 位置本身：通用路径在「下一个字符放不下」时已
            # 记录该位置的空格（``last_space`` 可为 break 处的 j == end），
            # 且要求断点严格大于行首（行首空格不作断点）。
            sp = -1
            for k in range(end, i, -1):
                if chars[k] == " ":
                    sp = k
                    break
            if sp > i:
                lines.append(_uniform_line(chars, styles, i, sp, links))
                i = sp + 1
                continue
        lines.append(_uniform_line(chars, styles, i, end, links))
        i = end
    return lines


def wrap_line(line: AnsiLine, max_width: int) -> list[AnsiLine]:
    """将 AnsiLine 按显示宽度换行为多行（CJK 安全 + 词边界优先）。

    词边界换行（方向8）：超宽且当前行内含空格断点时**优先在空格处断行**
    （保留词完整性，如 ``file.txt`` 不被拆成 ``file.tx``/``t``）——修复前
    逐字符硬拆，工具卡/用户消息中的长行单词被拦腰截断。断点取行内最后一个
    空格（空格不保留在行尾；下一行从空格后开始）；行首空格不成为断点。
    无空格断点（长单词/长 CJK）时回退字符级硬拆（与既有行为一致，
    ``test_wrap_by_width``/``test_wrap_cjk`` 锁定）。

    实现：先展开为 (ch, style) 序列，贪心填充每行；超宽时若有空格断点则
    回退到断点（下一行从断点后继续），否则字符级断开。最后每行经
    ``AnsiLine.append`` 合并相邻同 style run（样式保持）。
    """
    if max_width <= 0:
        return [line] if line.runs else []
    runs = line.runs
    if not any(r.text for r in runs):
        return []
    # ★ 快路径（超长单行性能）：宽度不超 max_width 且无强制换行 → 单行直接
    #   返回（免逐字符 Python 循环 + items 列表构造）。宽度经 ``Run.width`` /
    #   ``char_width`` 缓存，热路径命中后为 O(run 数)。
    #   仅当行结构已「规范化」（无空文本 run、相邻 run 样式不同——通用路径
    #   会合并相邻同样式 run 并丢弃空 run）时才克隆原行，保证产出 runs 结构
    #   与通用路径逐字段一致。
    total = 0
    has_nl = False
    has_link = False
    for r in runs:
        total += r.width
        if not has_nl and "\n" in r.text:
            has_nl = True
        if not has_link and getattr(r, "link", None):
            has_link = True
    if not has_nl and total <= max_width:
        normalized = True
        prev_style = _NO_STYLE
        prev_link = None
        for r in runs:
            link = getattr(r, "link", None)
            if not r.text or (r.style == prev_style and link == prev_link):
                normalized = False
                break
            prev_style = r.style
            prev_link = link
        if normalized:
            return [line.clone()]
    items_chars: list[str] = []
    items_styles: list = []
    items_links: list | None = [] if has_link else None
    for run in runs:
        text = run.text
        if not text:
            continue
        items_chars.extend(text)
        items_styles.extend([run.style] * len(text))
        if items_links is not None:
            items_links.extend([getattr(run, "link", None)] * len(text))
    n = len(items_chars)
    if n == 0:
        return []
    # ★ 快路径（等宽字符，超长单行主路径）：整行字符显示宽度同为 2
    #   （CJK / 全角 / emoji）或同为 1（纯 ASCII，无控制字符）→ 按固定步长
    #   切分，免逐字符宽度查询与贪心扫描。判定严格：
    #     - ``total == 2n`` ⟺ 每字符宽 2（宽度取值 0/1/2，全部为 2 才成立）
    #     - ``total == n`` 且纯 ASCII ⟺ 每字符宽 1（含控制字符时 total < n）
    if total == 2 * n:
        per_line = max_width // 2
        if per_line > 0:
            return _wrap_uniform(items_chars, items_styles, per_line, False,
                                 items_links)
    elif total == n and all(r.text.isascii() for r in runs if r.text):
        return _wrap_uniform(items_chars, items_styles, max_width, True,
                             items_links)
    width_of = char_width
    lines: list[AnsiLine] = []
    i = 0
    while i < n:
        # 贪心填充一行（不超 max_width）
        j = i
        width = 0
        last_space = -1  # 本行内最后一个空格的索引（绝对）
        while j < n:
            ch = items_chars[j]
            if ch == "\n":
                break  # 强制换行
            # ★ 先记录空格断点再判超宽：超宽字符本身是空格时（行恰好填满
            #   后在空格前断行），该空格须作为断点（end=空格、下一行从空格
            #   后开始）——修复前 break 在 last_space 记录之前，行内最后一个
            #   空格未被记录，断点回退到字符级 → 下一行以空格开头（如
            #   "brown" 被拆成 " brow"/"n"）。
            if ch == " ":
                last_space = j
            cw = width_of(ch)
            if width + cw > max_width and j > i:
                break
            width += cw
            j += 1
        if j == i:
            # 行首字符即超宽（无法放下）或行首为强制换行
            if items_chars[i] == "\n":
                # 行首强制换行：产生空行（Newline 组件渲染语义）
                lines.append(AnsiLine())
                i += 1
                continue
            # 行首字符即超宽：硬塞一个字符（CJK 宽字符仍可能单字符超宽——
            # 无法避免，与既有行为一致）。
            end = i + 1
            next_i = i + 1
        elif j < n and items_chars[j] == "\n":
            # 强制换行：本行到 \n 前，下一行从 \n 后开始
            end = j
            next_i = j + 1
        elif j < n and last_space > i:
            # 词边界断行：本行到空格前（不含空格），下一行从空格后开始
            end = last_space
            next_i = last_space + 1
        else:
            # 无空格断点：字符级断开（本行到 j）
            end = j
            next_i = j
        line_out = AnsiLine()
        # ★ 方向8（性能）：段级 join 追加（同 style 字符累积到 list，一次
        #   join 后 append）——修复前逐字符 ``line_out.append(ch, st)`` 在
        #   大单行（100k 字符）下 ``last.text + text`` 反复复制累积串导致
        #   O(n²)（100k 字符 wrap 耗 1.5s+）。行宽有界，同 style 段 join
        #   成本 O(行宽)；样式切换处段级拆分（跨 style 不合并）。
        chars: list[str] = []
        seg_style = items_styles[i] if i < end else None
        seg_link = (items_links[i] if (items_links is not None and i < end)
                    else None)
        for k in range(i, end):
            st = items_styles[k]
            lk = items_links[k] if items_links is not None else None
            if st != seg_style or lk != seg_link:
                if chars:
                    line_out.append("".join(chars), seg_style, seg_link)
                    chars = []
                seg_style = st
                seg_link = lk
            chars.append(items_chars[k])
        if chars:
            line_out.append("".join(chars), seg_style, seg_link)
        if line_out.runs:
            lines.append(line_out)
        i = next_i
    return lines


def truncate_line(line: AnsiLine, max_width: int) -> AnsiLine:
    """截断 AnsiLine 至 max_width（CJK 安全，宽字符不拆）。"""
    if max_width < 0:
        return AnsiLine()
    if line.width <= max_width:
        return line.clone()
    out = AnsiLine()
    width = 0
    for run in line.runs:
        link = getattr(run, "link", None)
        for ch in run.text:
            cw = cjk_display_width(ch)
            if width + cw > max_width:
                return out
            out.append(ch, run.style, link)
            width += cw
    return out


def pad_line(line: AnsiLine, width: int) -> AnsiLine:
    """填充至指定宽度（不足补空格，超宽截断）。"""
    out = truncate_line(line, width)
    pad = width - out.width
    if pad > 0:
        out.append(" " * pad)
    return out


# ═══════════════════════════════════════════════════════════
# ANSI → Style 解析（紧急回退：带 ANSI 的纯文本转 Run 序列）
# ═══════════════════════════════════════════════════════════

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def parse_sgr_params(params: str) -> tuple[Style | None, bool]:
    """解析 SGR 参数串（'' 表示 0）为 Style 增量。

    Returns:
        (style, is_reset)：is_reset 表示遇到 0（重置）。
    """
    if not params:
        return (None, True)
    reset = False
    fg: int | tuple[int, int, int] | None = None
    bg: int | tuple[int, int, int] | None = None
    bold = italic = dim = underline = False
    parts = params.split(";")
    i = 0
    while i < len(parts):
        p = parts[i]
        # 0 / 00 / 000 → 重置（Rich 有时输出 \x1b[39;49;00m 形式的重置）
        if p == "" or (p.isdigit() and int(p) == 0):
            reset = True
        elif p == "1":
            bold = True
        elif p == "2":
            dim = True
        elif p == "3":
            italic = True
        elif p == "4":
            underline = True
        elif p == "38" or p == "48":
            if i + 1 < len(parts) and parts[i + 1] == "5" and i + 2 < len(parts):
                try:
                    n = int(parts[i + 2])
                    if p == "38":
                        fg = n
                    else:
                        bg = n
                except ValueError:
                    pass
                i += 2
            elif i + 1 < len(parts) and parts[i + 1] == "2" and i + 4 < len(parts):
                try:
                    rgb = (int(parts[i + 2]), int(parts[i + 3]), int(parts[i + 4]))
                    if p == "38":
                        fg = rgb
                    else:
                        bg = rgb
                except ValueError:
                    pass
                i += 4
        elif p.isdigit():
            n = int(p)
            if 30 <= n <= 37:
                fg = n
            elif 90 <= n <= 97:
                fg = n - 90 + 8
            elif 40 <= n <= 47:
                bg = n - 40
            elif 100 <= n <= 107:
                bg = n - 100 + 8
        i += 1
    style = Style(fg=fg, bg=bg, bold=bold, italic=italic, dim=dim, underline=underline)
    return (style, reset)


def ansi_to_runs(text: str, base_style: Style | None = None) -> list[Run]:
    """将含 ANSI 转义序列的文本解析为 Run 序列（紧急回退）。"""
    runs: list[Run] = []
    current = Style() if base_style is None else base_style
    buf = ""
    pos = 0
    for m in _SGR_RE.finditer(text):
        if m.start() > pos:
            buf += text[pos:m.start()]
        pos = m.end()
        if buf:
            runs.append(Run(buf, current) if current else Run(buf, None))
            buf = ""
        style, is_reset = parse_sgr_params(m.group(1))
        if is_reset:
            # ★ 组合 SGR「重置 + 颜色」修复（方向1）：``\x1b[0;31m`` 等
            #   （Pygments/Rich 常输出）——终端语义先 reset 再应用颜色。
            #   修复前直接 ``current = Style()`` 把同序列解析出的 fg=31 丢弃，
            #   reset 后文本渲染成默认色而非红色。
            current = Style() if base_style is None else base_style
            if style:
                current = current.merge(style)
        else:
            current = current.merge(style) if style else current
    if pos < len(text):
        buf += text[pos:]
    if buf:
        runs.append(Run(buf, current) if current else Run(buf, None))
    return runs


def ansi_to_line(text: str, base_style: Style | None = None) -> AnsiLine:
    """将含 ANSI 的文本转为 AnsiLine（紧急回退）。"""
    return AnsiLine(ansi_to_runs(text, base_style))


__all__ = [
    "Run",
    "AnsiLine",
    "visual_width",
    "strip_ansi",
    "wrap_line",
    "truncate_line",
    "pad_line",
    "parse_sgr_params",
    "ansi_to_runs",
    "ansi_to_line",
]
