"""ansi 工具 — Run/AnsiLine 输出模型 + 换行/截断/测量 + ANSI→样式解析。

本模块自绘 ANSI（零 Rich、零 tui 依赖）：输出模型为 AnsiLine（Run 序列），
宽度依据统一用 renderer._utils.cjk_display_width，样式用本包自含 Style。
"""

from __future__ import annotations

import re
from dataclasses import field
from typing import Any

from src._ansi_text import ANSI_RE
from src._ansi_text import strip_ansi as strip_ansi  # noqa: F401 — 全项目单一真源 re-export
from src._compat import dataclass

from src.renderer._utils import cjk_display_width, expand_tabs
from src._text_width import (
    char_width,
    cluster_width,
    grapheme_clusters,
    _has_grapheme_special,
)
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
    style: Any = None
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
    """一行输出（Run 序列）。

    ``cont_prefix`` 为**续行前缀**（悬挂缩进）：本行按终端宽度折行时，除
    首行外的每行以此前缀起始、可用宽度相应扣减——用于「列表项 / 引用行 /
    容器缩进行」折行后续行与首行内容起始列对齐（见 ``wrap_line``）。空字符串
    表示无续行对齐需求（普通段落 / 代码等），不影响本行自身内容与宽度。
    """

    __slots__ = ("runs", "_esc_checked", "_w", "cont_prefix")

    def __init__(self, runs: list[Run] | None = None,
                 cont_prefix: str = "") -> None:
        self.runs: list[Run] = list(runs) if runs else []
        # ANSI 消毒缓存标记：True 表示已确认本行（当前内容）不含原始转义序列，
        # 消毒路径可直接跳过扫描。内容经 append/append_run 修改时重置。
        self._esc_checked = False
        # 显示宽度缓存（-1 = 未计算）；``append`` 修改内容时失效。
        self._w = -1
        # 续行前缀（悬挂缩进）；仅 ``wrap_line`` 折行时消费。
        self.cont_prefix = cont_prefix

    @classmethod
    def of(cls, text: str, style: Any = None) -> "AnsiLine":
        """单 run 行。

        ``text`` 为空时返回**真正的空行**（无 runs）——避免空 run 让下游的
        前缀注入（列表 / 引用 / 容器缩进）把空行当作有内容行、渲染出带缩进的
        「伪空行」（行尾残留空白）。
        """
        if not text:
            return cls()
        return cls([Run(text, style)])

    def append(self, text: str, style: Any = None,
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
            if _has_grapheme_special(text):
                # 组合 emoji / keycap 按字形簇测宽（逐码点会高估 → 误判超宽）
                for cluster in grapheme_clusters(text):
                    total += cluster_width(cluster)
                    if total > limit:
                        return True
                continue
            for ch in text:
                total += char_width(ch)
                if total > limit:
                    return True
        return False

    def clone(self) -> "AnsiLine":
        return AnsiLine(list(self.runs), self.cont_prefix)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"AnsiLine({self.plain!r})"


# ═══════════════════════════════════════════════════════════
# 宽度 / 换行 / 截断
# ═══════════════════════════════════════════════════════════


def visual_width(text: str) -> int:
    """纯字符串显示宽度（剥离 ANSI 后测量）。"""
    return cjk_display_width(strip_ansi(text))


# ANSI 转义序列处理收敛到 ``src._ansi_text``（tui / renderer / clawbot 三层
# 唯一真源）；此处保留 ``_ANSI_RE`` / ``strip_ansi`` 旧导入路径兼容。
_ANSI_RE = ANSI_RE

#: 「无前驱样式」哨兵（避免用 None 兼作哨兵——None 是合法的无样式值，
#: 需要区分「尚无前驱」与「前驱样式为 None」）
_NO_STYLE = object()


def _run_spans(runs: list) -> tuple:
    """Run 列表 → （拼接文本, 段起始, 段结束, 段样式, 段链接）平行结构。

    供 ``wrap_line`` 的等宽快路径用「按段切片发射」代替逐字符 tuple 展开
    （超长单行每帧换行的主成本之一）。空文本 run 跳过（与通用路径展开时
    丢弃空 run 的语义一致），段偏移为拼接文本中的字符下标。
    """
    starts: list[int] = []
    ends: list[int] = []
    styles: list = []
    links: list = []
    parts: list[str] = []
    pos = 0
    for r in runs:
        text = r.text
        if not text:
            continue
        starts.append(pos)
        ends.append(pos + len(text))
        styles.append(r.style)
        links.append(getattr(r, "link", None))
        parts.append(text)
        pos += len(text)
    return "".join(parts), starts, ends, styles, links


def _append_run(line: AnsiLine, text: str, style, link, width: int) -> None:
    """向行追加一段文本（宽度已知），相邻同样式/链接段合并。

    合并语义与 ``AnsiLine.append`` 一致（相邻同 style/link 合并为同一 Run）；
    额外把已知显示宽度写入 ``Run._w`` 缓存——等宽快路径按固定字符宽度切分，
    下游消费（``StyledRun.fast`` / ``_to_styled_runs``）免重算整段宽度。
    """
    runs = line.runs
    if runs and runs[-1].style == style and runs[-1].link == link:
        prev = runs[-1]
        merged = Run(prev.text + text, style, link)
        if prev._w >= 0 and width >= 0:
            merged._w = prev._w + width
        runs[-1] = merged
    else:
        run = Run(text, style, link)
        if width >= 0:
            run._w = width
        runs.append(run)
    line._esc_checked = False
    line._w = -1


def _emit_span_range(line: AnsiLine, text: str, spans, start: int, end: int,
                     char_w: int) -> None:
    """把拼接文本 ``[start, end)`` 按段边界切分后追加到行。

    二分定位首个覆盖 ``start`` 的段（避免逐行全量扫描段表），再沿实际重叠段
    追加（样式 / 链接随段保留）；每段宽度由等宽字符宽度 ``char_w`` 直接得出。
    """
    from bisect import bisect_right

    starts, ends, styles, links = spans
    n_spans = len(starts)
    idx = bisect_right(starts, start) - 1
    if idx < 0:
        idx = 0
    while idx < n_spans:
        a = starts[idx]
        if a >= end:
            break
        b = ends[idx]
        lo = a if a > start else start
        hi = b if b < end else end
        if lo < hi:
            _append_run(line, text[lo:hi], styles[idx], links[idx],
                        (hi - lo) * char_w)
        idx += 1


def _wrap_uniform(text: str, spans, n: int, per_line: int, word_break: bool,
                  char_w: int) -> list[AnsiLine]:
    """等宽字符行的换行（每字符显示宽度相同，按固定步长切分）。

    调用方（``wrap_line``）保证 ``text`` 中每个字符显示宽度相同（``char_w``）
    且无强制换行符；``word_break=True``（宽 1 的行）时按「行内最后一个空格」
    优先断行——语义与通用路径一致（空格不保留在行尾、行首空格不作断点）。
    断点用 C 级 ``str.rfind``（修复前逐字符 Python 循环找行内最后空格）。

    ★ 性能（超长单 Run 行）：单 span（绝大多数纯文本段落 / 代码行）直接构造
    一段 ``Run`` 并写入已知宽度，免每行 ``bisect`` + 段循环 + 临时对象；
    超长 ASCII 行（80KB+）换行成本显著降低。
    """
    starts, ends, styles, links = spans
    single = len(starts) == 1
    style0 = styles[0] if single else None
    link0 = links[0] if single else None

    def _emit(line: AnsiLine, a: int, b: int) -> None:
        if single:
            run = Run(text[a:b], style0, link0)
            run._w = (b - a) * char_w
            line.runs.append(run)
            line._w = (b - a) * char_w
            return
        _emit_span_range(line, text, spans, a, b, char_w)

    lines: list[AnsiLine] = []
    i = 0
    while i < n:
        end = i + per_line
        if end >= n:
            line = AnsiLine()
            _emit(line, i, n)
            lines.append(line)
            break
        if word_break:
            # 断点范围含 end 位置本身：通用路径在「下一个字符放不下」时已
            # 记录该位置的空格（``last_space`` 可为 break 处的 j == end），
            # 且要求断点严格大于行首（行首空格不作断点）。
            # ★ 半行约束（与通用路径同口径）：断点左侧须占本行容量的半数以上
            #   ——避免行首标记/缩进里的空白成为唯一断点，首行只剩标记。
            sp = text.rfind(" ", i, end + 1)
            if sp > i and (sp - i) * 2 >= per_line:
                line = AnsiLine()
                _emit(line, i, sp)
                lines.append(line)
                i = sp + 1
                continue
        line = AnsiLine()
        _emit(line, i, end)
        lines.append(line)
        i = end
    return lines


def _all_width_one(runs: list) -> bool:
    """所有字符显示宽度是否恒为 1（可打印 ASCII 或宽 1 的非 ASCII 字符）。

    供 ``wrap_line`` 的等宽快路径判定：宽度恒 1 ⇒ 可按 ``max_width`` 字符
    步长切片（免逐字符测宽与贪心扫描），词边界语义与通用路径一致。判定用
    ``str.isascii``/``str.isprintable``（C 级）与每 run 一次 ``set`` 去重后的
    少量 ``char_width`` 查询——成本 O(唯一字符数) 而非 O(字符数)。
    """
    for r in runs:
        text = r.text
        if not text:
            continue
        if text.isascii():
            # ASCII 控制字符宽 0（isprintable 排除）；其余宽 1。
            if not text.isprintable():
                return False
            continue
        for ch in set(text):
            if char_width(ch) != 1:
                return False
    return True


def wrap_line(line: AnsiLine, max_width: int,
              cont_prefix: str | None = None) -> list[AnsiLine]:
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

    ★ 续行悬挂缩进（``cont_prefix``，缺省取 ``line.cont_prefix``）：行首带
    固定标记/缩进的行（列表项 ``1. `` / ``• ``、引用 ``│ ``、容器缩进行）
    折行时，续行以「首行内容起始列」等宽的前缀起始、可用宽度扣减前缀宽度
    ——修复前续行顶格列 0，长列表项折行后与首行内容错位（续行看起来像新的
    顶层内容）。空前缀（普通段落）走原路径，快路径语义与性能不变；前缀
    宽度不足 2 列余量（窄屏）时禁用前缀，避免撑破行宽不变量。
    """
    if max_width <= 0:
        return [line] if line.runs else []
    if cont_prefix is None:
        cont_prefix = getattr(line, "cont_prefix", "") or ""
    # 续行前缀宽度：剩余可用宽度 < 2 列时禁用（窄屏防御——宽 2 字符仍能
    # 放下；否则前缀会把内容挤到 0 列并破坏「行宽 <= max_width」不变量）。
    cp = ""
    cp_w = 0
    if cont_prefix:
        cp_w = cjk_display_width(cont_prefix)
        if cp_w > 0 and cp_w + 2 <= max_width:
            cp = cont_prefix
        else:
            cp_w = 0
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
    n = 0
    has_nl = False
    has_link = False
    special = False
    for r in runs:
        text = r.text
        if not text:
            continue
        total += r.width
        n += len(text)
        if not has_nl and "\n" in text:
            has_nl = True
        if not has_link and getattr(r, "link", None):
            has_link = True
        if not special and _has_grapheme_special(text):
            special = True
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
    if n == 0:
        return []
    # ★ 快路径（等宽字符，超长单行主路径）：整行字符显示宽度同为 2
    #   （CJK / 全角 / emoji）或同为 1（宽 1 字符）→ 按固定步长切分，免逐字符
    #   宽度查询与贪心扫描，也免 items 逐字符展开（按段切片发射）。判定严格：
    #     - ``total == 2n`` ⟺ 每字符宽 2（宽度取值 0/1/2，全部为 2 才成立）
    #     - ``total == n`` 且所有字符宽 1（``_all_width_one``）⟺ 每字符宽 1
    #   ``not has_nl`` 显式守卫（上述两条判定已隐含无 ``\n``，保留以固化契约）。
    if not has_nl and not cp_w and not special:
        if total == 2 * n:
            per_line = max_width // 2
            if per_line > 0:
                spans = _run_spans(runs)
                return _wrap_uniform(spans[0], spans[1:], n, per_line, False, 2)
        elif total == n and _all_width_one(runs):
            spans = _run_spans(runs)
            return _wrap_uniform(spans[0], spans[1:], n, max_width, True, 1)
    items_chars: list[str] = []
    items_styles: list = []
    items_links: list | None = [] if has_link else None
    for run in runs:
        text = run.text
        if not text:
            continue
        if special:
            # 字形簇切分：ZWJ 组合 emoji / 肤色修饰 / keycap / 国旗不被
            # 换行拆散（每个簇作为一个不可分割的「字符」参与贪心填充）。
            for cluster in grapheme_clusters(text):
                items_chars.append(cluster)
                items_styles.append(run.style)
                if items_links is not None:
                    items_links.append(getattr(run, "link", None))
            continue
        items_chars.extend(text)
        items_styles.extend([run.style] * len(text))
        if items_links is not None:
            items_links.extend([getattr(run, "link", None)] * len(text))
    n = len(items_chars)
    width_of = cluster_width if special else char_width
    lines: list[AnsiLine] = []
    i = 0
    while i < n:
        # 本行可用宽度：首行（尚无输出行）用 max_width；续行扣除续行前缀
        # 宽度（悬挂缩进——前缀在下面追加到行首）。
        limit = max_width if (not cp_w or not lines) else max_width - cp_w
        # 贪心填充一行（不超 limit）
        j = i
        width = 0
        last_space = -1  # 本行内最后一个空格的索引（绝对）
        #: 该空格**左侧**已填充的显示宽度（断点回退量判定用，见下方半行约束）
        last_space_w = 0
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
                last_space_w = width
            cw = width_of(ch)
            if width + cw > limit and j > i:
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
        elif j < n and last_space > i and last_space_w * 2 >= limit:
            # 词边界断行：本行到空格前（不含空格），下一行从空格后开始。
            # ★ 半行约束（修复行首标记/缩进被当作断点）：断点左侧内容须占本行
            #   可用宽度的一半以上，否则视为「过于靠前」→ 回退字符级硬拆。
            #   修复前只要行内有空格就回退到「最后一个空格」，而行首标记
            #   （``• `` / ``│ `` / ``1. ``）或缩进里的空白常是该行**唯一**的
            #   空格（中文长行无空格断点）→ 首行只输出标记本身（``•`` / ``│``），
            #   内容被整体挤到下一行。
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
            # 续行（非首行）补悬挂缩进前缀：等宽前缀并入首个无样式 run，
            # 减少 run 碎片；首行不含前缀（行首已有项目符号/缩进本身）。
            if cp and lines:
                first = line_out.runs[0]
                if first.style is None and not getattr(first, "link", None):
                    lines.append(AnsiLine(
                        [Run(cp + first.text)] + list(line_out.runs[1:])))
                else:
                    lines.append(AnsiLine([Run(cp)] + list(line_out.runs)))
            else:
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
        text = run.text
        if _has_grapheme_special(text):
            for cluster in grapheme_clusters(text):
                cw = cluster_width(cluster)
                if width + cw > max_width:
                    return out
                out.append(cluster, run.style, link)
                width += cw
            continue
        for ch in text:
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


def ansi_to_runs(text: str, base_style: Any = None) -> list[Run]:
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


def ansi_to_line(text: str, base_style: Any = None) -> AnsiLine:
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
