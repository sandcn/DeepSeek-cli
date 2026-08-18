"""输出模型 — StyledRun / Line / Frame（用 core.style）。

输出模型是帧渲染的载体：
  - StyledRun：一段带样式的文本（text + Style）
  - Line：一行 = StyledRun 序列（render() 合并为 ANSI 字符串）
  - Frame：一帧 = Line 序列（整帧文档，供 InkRenderer 行级 diff）
  - FrameBuilder：流式构建 Frame 的辅助器（按宽换行/追加）

零 Rich 依赖：样式一律用 ``src.tui.core.style.Style``，
宽度一律用 ``_width.wcswidth_simple``（唯一宽度依据）。
"""

from __future__ import annotations

from src._compat import dataclass
from dataclasses import field
from typing import Iterable

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple


# ═══════════════════════════════════════════════════════════
# StyledRun — 带样式的文本片段
# ═══════════════════════════════════════════════════════════


def _text_width(text: str) -> int:
    """字符串显示宽度（Line.append 增量维护用）。

    ★ 性能（2026-08-05）：纯可打印 ASCII 批量快路径——宽度 == 字符数
    （``isascii()`` + ``isprintable()`` C 实现单趟扫描，免逐字符
    ``wcswidth_simple`` 调用）。渲染热路径（``_build_lines`` / 补全弹窗 /
    状态栏等 append 段）以 ASCII 文本为主。
    """
    if text.isascii() and text.isprintable():
        return len(text)
    return wcswidth_simple(text)


@dataclass(frozen=True)
class StyledRun:
    """一段带样式的文本。

    Attributes:
        text: 文本内容。
        style: 样式（None 表示无样式）。
    """

    text: str
    style: Style | None = None
    #: 显示宽度缓存（PERF：frozen 不可变 → ``__post_init__`` 一次性计算；
    #: 热路径（Line.width/diff/truncate/measure）免重复 ``wcswidth_simple``）。
    #: ``compare=False``（eq/hash 不参与——text 相同则宽度必相同，语义不变）
    #: ``repr=False``（调试输出不显示）。
    width: int = field(init=False, repr=False, compare=False, default=0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", wcswidth_simple(self.text))

    def render(self) -> str:
        """渲染为 ANSI 字符串（无样式时原样返回）。"""
        if self.style:
            return self.style.apply(self.text)
        return self.text


# ═══════════════════════════════════════════════════════════
# Line — 一行 StyledRun 序列
# ═══════════════════════════════════════════════════════════


class Line:
    """一行渲染输出（StyledRun 序列）。

    - ``render()`` 合并所有 run 为 ANSI 字符串（★ 渲染结果缓存——同 Line
      对象跨帧复用零重建，见 ``_r``）。
    - ``width`` 为所有 run 的显示宽度总和（惰性缓存：首次访问计算，append 增量
      维护——渲染热路径（diff/截断/画布转换）免重复 ``wcswidth_simple``）。
    - ``append(text, style)`` 追加一段；``append_run(run)`` 追加 StyledRun。

    ★ P2-3（review 方向，隐式不变式固化）：``Line.__init__`` 对 list 参数
    直接复用引用（PERF-7，免 O(n) 拷贝）——**构造后不得原地修改传入的 runs
    列表**：``_r`` ANSI 渲染缓存假设 runs 只经 ``append`` 修改（append 会
    主动失效缓存）；外部原地修改 runs（``line.runs.append(...)`` /
    ``line.runs[0] = ...``）绕过失效路径，``render()`` 返回过期缓存。
    不变式由调用方保证（构造时传入的均为临时新建 list；渲染热路径中
    committed 前缀/快照缓存命中时 Line 对象跨帧只读复用）。

    ★ 性能（PERF-24）：ANSI 渲染缓存——``render()`` 结果缓存在 ``_r``。
    StyledRun 为 frozen dataclass（text/style 不可变）+ Style frozen（to_ansi
    lru_cache），同 runs 列表的渲染结果确定性——同一 Line 对象跨帧复用（committed
    前缀 / 快照缓存命中）时 ``render()`` 零重建。仅 ``append`` 修改 runs 时失效
    （全项目唯一修改点：``self.runs[-1] = ...`` / ``self.runs.append(...)``，无
    外部直接改 runs 的路径——审计确认）。实测：200 行 × 200 帧 diff 渲染从
    ~1.18s 降至 ~0.1s 量级（差异行之外零 ANSI 构建）。
    """

    __slots__ = ("runs", "_w", "_r")

    def __init__(self, runs: Iterable[StyledRun] | None = None) -> None:
        # ★ 性能（PERF-7）：传入 list 时直接复用引用（免 O(n) 拷贝）——
        #   ``Line([StyledRun(...), ...])`` 等调用方传入的均为临时新建 list
        #   （构造后不再修改），复用安全；生成器/元组等非 list 才转换。
        #   渲染热路径中每帧创建大量 Line（_canvas_row_to_line 等），
        #   省一次列表拷贝。
        if runs is None:
            self.runs = []
        elif isinstance(runs, list):
            self.runs = runs
        else:
            self.runs = list(runs)
        # 宽度惰性缓存（None=未计算；append 增量维护）
        self._w: int | None = None
        # ANSI 渲染缓存（None=未计算；append 修改 runs 时失效）
        self._r: str | None = None

    @classmethod
    def of(cls, text: str, style: Style | None = None) -> "Line":
        """从纯文本创建单 run 行。"""
        return cls([StyledRun(text, style)])

    def append(self, text: str, style: Style | None = None) -> None:
        """追加一段文本（自动合并相邻同 style 的 run）。"""
        if not text:
            return
        # 修改 runs → ANSI 渲染缓存失效（PERF-24）
        self._r = None
        if self.runs and self.runs[-1].style == style:
            last = self.runs[-1]
            # ★ 增量宽度：替换末 run（新宽 = 旧宽 + text 宽）——直接加 text 宽
            self.runs[-1] = StyledRun(last.text + text, style)
            if self._w is not None:
                self._w += _text_width(text)
            return
        self.runs.append(StyledRun(text, style))
        if self._w is not None:
            self._w += _text_width(text)

    def append_run(self, run: StyledRun) -> None:
        """追加 StyledRun。"""
        if not run or not run.text:
            return
        self.append(run.text, run.style)

    def render(self) -> str:
        """合并为 ANSI 字符串（渲染结果缓存——同 Line 对象跨帧零重建）。"""
        cached = self._r
        if cached is None:
            cached = "".join(r.render() for r in self.runs)
            self._r = cached
        return cached

    @property
    def width(self) -> int:
        """显示宽度总和（惰性缓存：首次访问计算，append 增量维护）。"""
        w = self._w
        if w is None:
            total = 0
            for r in self.runs:
                total += r.width  # StyledRun 缓存宽度（O(1)/run）
            self._w = total
            return total
        return w

    @property
    def plain(self) -> str:
        """纯文本（去样式）。"""
        return "".join(r.text for r in self.runs)

    def clone(self) -> "Line":
        """深拷贝行（runs 为不可变 StyledRun，浅拷贝列表即可）。

        显式 ``list(self.runs)`` 拷贝——``Line.__init__`` 对 list 直接复用
        引用（PERF-7 优化），clone 必须创建独立 runs 列表（副本追加不影响
        原行）。宽度缓存与 ANSI 渲染缓存同步复制（runs 未变，结果相同）。
        """
        new = Line(list(self.runs))
        new._w = self._w
        new._r = self._r
        return new

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Line({self.plain!r})"

    def __eq__(self, other) -> bool:
        """值比较（runs 序列相等）。

        ★ 标准 React Ink 组件化（2026-08-05）：subagent_lines 从 ANSI 字符串
        行迁移为 Line 行后，控制器「变更检测」（``lines != _last_pushed_frame``）
        需要值比较——旧实现为字符串列表值比较，迁移后 Line 默认身份比较恒
        不相等（每次空转推送）。本方法按 runs 值比较（StyledRun 为 frozen
        dataclass，值语义）。
        """
        if not isinstance(other, Line):
            return NotImplemented
        return self.runs == other.runs

    # ★ P3（review）：显式声明不可哈希（固化契约）——定义 ``__eq__`` 后
    #   Python 自动置 ``__hash__ = None``；Line 为**可变**对象（append 可
    #   修改 runs，值比较基于 runs），可变对象实现哈希会违反哈希不变式
    #   （append 后哈希漂移，已入 set/dict 的对象丢失）。显式声明 + 注释
    #   固化：如需集合/字典键用法须经 ``_hashable`` 归一化（业务值转
    #   可哈希键，全项目现状即如此）。
    __hash__ = None


# ═══════════════════════════════════════════════════════════
# Frame — 一帧（行列表）
# ═══════════════════════════════════════════════════════════


class Frame:
    """一帧渲染输出（Line 列表）。

    整个 UI 是一个输出文档：静态聊天历史 + 尾部 live 区（状态栏 + 输入）。
    每帧 = 完整文档的 Line 列表，供 InkRenderer 行级 diff。

    ★ P2-3（review 方向，隐式不变式固化）：``Frame.__init__`` 对 list 参数
    直接复用引用（PERF-7，免 O(n) 拷贝）——**构造后不得原地修改传入的 lines
    列表**：行级 diff（``first_diff_line``）依赖 lines 跨帧稳定性（Line 对象
    身份/值不变则跳过）；外部原地修改 lines（追加/替换/删除元素）绕过
    ``stable_prefix`` 偏移与身份短路假设，diff 结果不可预期。不变式由调用方
    保证（``render_frame`` 构建 Frame 后只读消费；测试直接构造 Frame 时传入
    临时新建 list）。

    Attributes:
        lines: 帧行列表。
        _stable_prefix: 稳定前缀列表对象（committed 前缀复用）；None 表示无。
        _stable_prefix_offset: 稳定前缀在 lines 中的起始行号（0-based）。
        _stable_prefix_len: 稳定前缀覆盖的行数。
    """

    __slots__ = ("lines", "_stable_prefix", "_stable_prefix_offset", "_stable_prefix_len")

    def __init__(
        self,
        lines: Iterable[Line] | None = None,
        stable_prefix: list | None = None,
        stable_prefix_offset: int = 0,
        stable_prefix_len: int = 0,
    ) -> None:
        # ★ 性能（PERF-7）：传入 list 时直接复用引用（免 O(n) 拷贝）——
        #   ``render_frame`` 的 ``Frame(prefix + tail)`` 等调用方传入的均为
        #   临时新建 list（构造后不再修改），复用安全；生成器/元组等非
        #   list 才转换。渲染热路径中每帧创建 1 个 Frame（大文档含数千行），
        #   省一次全列表拷贝。
        # ★ 稳定前缀（PERF-7）：``render_frame`` 构建时标记 committed 前缀
        #   为复用列表对象——`first_diff_line` 据此 O(1) 跳过前缀区间
        #   （前缀元素跨帧同一 Line 对象 → 必然无差异），避免大文档每帧
        #   全量逐行扫描（第一差异行位于尾部 live 区时）。
        if lines is None:
            self.lines = []
        elif isinstance(lines, list):
            self.lines = lines
        else:
            self.lines = list(lines)
        self._stable_prefix = stable_prefix
        self._stable_prefix_offset = stable_prefix_offset
        self._stable_prefix_len = stable_prefix_len

    @property
    def height(self) -> int:
        """文档总行数。"""
        return len(self.lines)

    def render_line(self, index: int) -> str:
        """渲染第 index 行为 ANSI 字符串。"""
        return self.lines[index].render()

    def to_ansi(self) -> str:
        """渲染整帧为 ANSI（行间以 \\n 连接，末尾换行）。"""
        if not self.lines:
            return ""
        return "\n".join(line.render() for line in self.lines) + "\n"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Frame({len(self.lines)} lines)"


# ═══════════════════════════════════════════════════════════
# FrameBuilder — 流式构建 Frame（按宽换行）
# ═══════════════════════════════════════════════════════════


class FrameBuilder:
    """流式构建 Frame 的辅助器。

    - ``append(text, style)``：追加文本到当前行，超宽自动换行。
    - ``newline()``：结束当前行。
    - ``build()``：返回 Frame。

    宽度依据为 ``wcswidth_simple``；换行宽度上限由构造参数给定。
    若 width<=0 表示不换行（保持原样，每行一逻辑行）。
    """

    __slots__ = ("_width", "_lines", "_current", "_current_width")

    def __init__(self, width: int = 0) -> None:
        self._width = width
        self._lines: list[Line] = []
        self._current: Line = Line()
        self._current_width = 0

    @property
    def width(self) -> int:
        return self._width

    def append(self, text: str, style: Style | None = None) -> None:
        """追加文本到当前行，超宽自动换行。"""
        if not text:
            return
        if self._width <= 0:
            # ★ BUG-34（review 方向）：width<=0 分支也按 ``\n`` 拆行（与正宽
            #   分支语义一致）——修复前含换行文本整段入行，Line 内嵌字面换行
            #   符破坏帧行号。
            segs = text.split("\n")
            for si, seg in enumerate(segs):
                if si > 0:
                    self._newline()
                if seg:
                    self._current.append(seg, style)
            return
        # 字符先累积到 list、段级一次性 join 追加——避免逐字符调用
        # Line.append 段拼接 O(n²)；段长受换行宽度约束有界，join 成本可接受。
        buf_chars: list[str] = []
        for ch in text:
            # ★ BUG-35（review 方向）：width>0 分支也须按 ``\n`` 拆行——
            #   修复前 ``\n`` 宽度 0，被当普通字符累积进 run，Line 内嵌字面
            #   换行符破坏「帧行内无换行」不变量（wrap/截断均按整行语义）。
            if ch == "\n":
                if buf_chars:
                    self._current.append("".join(buf_chars), style)
                    buf_chars = []
                self._newline()
                continue
            cw = wcswidth_simple(ch)
            if self._current_width + cw > self._width and (self._current.runs or buf_chars):
                if buf_chars:
                    self._current.append("".join(buf_chars), style)
                    buf_chars = []
                self._newline()
            buf_chars.append(ch)
            self._current_width += cw
        if buf_chars:
            self._current.append("".join(buf_chars), style)

    def append_run(self, run: StyledRun) -> None:
        """追加 StyledRun（按宽换行）。"""
        self.append(run.text, run.style)

    def append_line(self, line: Line) -> None:
        """追加一整行（完整行语义，不额外插入空行）。

        当前行有内容时先结束当前行；否则直接追加（修复前无条件
        ``_newline()``——在已结束行后调用会多追加一个空行）。
        """
        if self._current.runs:
            self._newline()
        self._lines.append(line)
        self._current = Line()
        self._current_width = 0

    def newline(self) -> None:
        """结束当前行（空行也结束）。"""
        self._newline()

    def _newline(self) -> None:
        self._lines.append(self._current)
        self._current = Line()
        self._current_width = 0

    def build(self) -> Frame:
        """返回已构建的 Frame（含未结束的当前行）。"""
        if self._current.runs:
            self._lines.append(self._current)
            self._current = Line()
            self._current_width = 0
        return Frame(self._lines)


__all__ = [
    "StyledRun",
    "Line",
    "Frame",
    "FrameBuilder",
]
