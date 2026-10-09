r"""ansi — 自绘 ANSI 内容引擎（零 Rich，复用解析层）。

``AnsiStreamRenderer`` 是 TUI 内容路径的入口（替代 IncrementalRenderer 角色）：
  write(chunk) → RecursiveDescentParser.feed → TokenPipeline（CodeBlockBatcher/
  HeadingAnchorFilter/TokenStreamOptimizer）→ AnsiRenderEngine → AnsiLine 追加。

子模块：
  engine.py  — AnsiRenderEngine（token → AnsiLine）
  inline.py  — 行内格式（粗体/斜体/行内码/链接）
  blocks.py  — 标题/列表/引用/告示/折叠块
  _html_block.py — HTML 块语义化（原始内容隐藏 / 控件 / 居中 / 媒体行）
  code.py    — 代码块（pygments → 256 色）
  table.py   — 表格（wcswidth 对齐 + 框线）
  mermaid.py / math.py — 图表/公式终端渲染（_mermaid_render / _math_latex）
  _math_box.py / _math_style.py / _math_letters.py / _math_cmds.py /
  _math_env.py — 公式排版的布局原语 / 样式 / Unicode 字母族 / 命令 / 环境
  _math_tex.py — TeX 原语与旧式结构（\over/\atop/\choose/\matrix/\cases/CD…）
  _math_macros.py — 宏定义与展开（\def/\newcommand/\let/条件/工具命令）
  _math_html.py — HTML/链接/盒子类命令的终端映射（\href/\url/\html*…）
  _mathml.py — MathML → LaTeX → 二维排版（HTML ``<math>`` 块与行内公式）
  helpers.py — Run/AnsiLine 模型 + 换行/截断/ANSI→Style
"""

from __future__ import annotations

from .helpers import Run, AnsiLine, wrap_line, truncate_line, ansi_to_line
from .style import Style
from .inline import (
    render_inline, use_render_context, _last_typo_pos, _TYPO_LOOKBACK,
)
from .engine import AnsiRenderEngine, _apply_list_prefix, _apply_bq_prefix
from ._preview_cache import LinePreviewCache
from ._line_delims import ParagraphBoundaryScanner
from .table import TablePreviewCache
from src.renderer.types import TokenType, Token

#: 预览单行字符上限——活动行（每次 write 变化的未换行行）超过该长度时只渲染
#: 尾部窗口，把单帧渲染成本封顶（避免超长无换行行逐字符增长导致的累计 O(n²)）。
#: 历史行经 ``LinePreviewCache`` 前缀复用只渲染一次，不受此影响。
_PREVIEW_MAX_LINE_CHARS = 4096

#: 行内「触发位置」元数据缓存（唯一真源 ``inline_parser``）：核心格式触发
#: 字符（不含裸 URL 首字母）/ 裸 URL 首字母 / 裸 URL 前缀。惰性初始化避免
#: 模块导入顺序耦合。
_TRIGGER_META_CACHE: tuple | None = None

#: 段落增量扫描的向前回看字符数（覆盖裸 URL 前缀跨增量边界的情形，如
#: ``ht`` + ``tp://x``；取最长前缀 ``https:`` 的长度）。
_TRIGGER_LOOKBACK = 7


def _trigger_meta() -> tuple:
    """行内「触发位置」元数据（核心字符 / 裸 URL 首字母 / 裸 URL 前缀）。

    唯一真源 ``src.renderer.inline_parser``（与解析器的兴趣位置表、快速判否
    ``text_has_inline_markup`` 同口径），惰性导入并缓存。
    """
    global _TRIGGER_META_CACHE
    if _TRIGGER_META_CACHE is None:
        from src.renderer.inline_parser import (
            _CORE_FORMAT_CHARS, _URL_LETTERS, _URL_PREFIXES,
        )

        _TRIGGER_META_CACHE = (
            frozenset(_CORE_FORMAT_CHARS), tuple(_URL_LETTERS), tuple(_URL_PREFIXES),
        )
    return _TRIGGER_META_CACHE


def _last_core_trigger_pos(text: str) -> int:
    """``text`` 中最后一个核心格式触发字符的下标（无则 -1）。

    对每个核心字符做一次 C 级 ``str.rfind``（18 个）——对短增量文本
    （30Hz 下每次 write 的 delta）远快于逐字符 Python 循环。
    """
    pos = -1
    for ch in _trigger_meta()[0]:
        idx = text.rfind(ch)
        if idx > pos:
            pos = idx
    return pos


def _last_url_prefix_pos(text: str) -> int:
    """``text`` 中最后一个裸 URL 前缀起点的下标（无则 -1）。

    前缀 ``http:`` / ``https:`` / ``ftp:`` / ``ftps:`` / ``www.`` 大小写不敏感
    （用一次 ``str.lower`` 副本匹配）——仅当文本含 ``h/H/f/F/w/W`` 字母时才
    生成小写副本（否则直接 -1）。
    """
    _, url_letters, url_prefixes = _trigger_meta()
    has_letter = False
    for ch in url_letters:
        if ch in text:
            has_letter = True
            break
    if not has_letter:
        return -1
    low = text.lower()
    pos = -1
    for prefix in url_prefixes:
        idx = low.rfind(prefix)
        if idx > pos:
            pos = idx
    return pos


#: 空样式单例（纯文本段落行复用；``Style()`` 为不可变值对象，等价）。
_EMPTY_STYLE = Style()

#: 数学块预览的源码增长阈值（长源码的节流基数，见 ``_throttle_preview``）。
_MATH_PREVIEW_REFRESH_STEP = 24

#: 节流预览「逐帧刷新」的源码长度上限：源码短于该值时排版成本可忽略（<1ms），
#: 保持逐帧实时刷新；超过后按 ``len//8`` 节流（刷新次数 ~O(log n)，总成本与
#: 长度线性）。若一律按固定步长节流，短块（常见的小公式/小图）会出现「最后
#: 几十字符不刷新」的明显滞后。
_PREVIEW_THROTTLE_FULL_LIMIT = 48

#: 容器块（告示 / <details> / fenced div）预览「逐帧刷新」的正文行数上限：
#: 正文行数短于该值时逐帧实时刷新（渲染成本可忽略）；超过后按 ``行数//8``
#: 节流，活动行增长按字符节流（见 ``_throttle_container_preview``）。
_CONTAINER_PREVIEW_FULL_LINES = 48

#: 列表项内块级容器预览的节流槽键（唯一实例；解析器同一时刻只收集一个
#: 列表项内块级容器）。
_LIST_BLOCK_THROTTLE_KEY = ("list_block",)

#: 代码块**活动行**（未换行尾行）预览的窗口上限：超长活动行只渲染尾部该
#: 字符数。pygments 高亮成本与行长度成正比，流式期间每帧对**整行**重解析
#: 会退化为 O(n²)（minified JSON / base64 / 长 URL 单行可达数十万字符）；
#: 取较小的独立窗口既保证「最新内容始终可见」（始终显示尾部），又把单帧
#: 成本封顶（与段落/HTML 的 ``_PREVIEW_MAX_LINE_CHARS`` 解耦——后者对
#: 纯文本有快速路径，代码高亮没有）。
_CODE_ACTIVE_MAX_CHARS = 512

#: 数学块预览的源码长度上限：超过后预览降级为提示框（二维排版成本随长度
#: 线性增长，超长公式即便节流也仍随长度增长；提交路径不受影响）。真实
#: LaTeX 公式远小于该值，超过基本为模型异常输出。
_MATH_PREVIEW_MAX_SRC = 1024

#: 预览子解析缓存容量（告示等容器正文的块级预览：内容未变化的帧复用结果）。
_PREVIEW_SUB_CACHE_MAX = 32


def _has_block_markers(lines) -> bool:
    """正文行列表是否含块级 Markdown 标记（列表/围栏/引用/表格/标题…）。

    供容器块（告示）流式预览判定：含块级标记时走子解析 + 完整 Markdown
    渲染，否则走逐行增量缓存（纯文本行的常见路径零额外开销）。
    """
    for raw in lines:
        s = (raw or "").strip()
        if not s:
            continue
        first = s[0]
        if first in '>|#`~':
            return True
        if first in '-*+' and len(s) > 1 and s[1] == ' ':
            return True
        if first == '<' and len(s) > 1 and (s[1].isalpha() or s[1] == '/'):
            return True
        if s.startswith(':::') or s.startswith('!!!') or s.startswith('???'):
            return True
        if s.startswith('$$') or s == r'\[':
            return True
        if first.isdigit():
            j = 1
            while j < len(s) and s[j].isdigit():
                j += 1
            if j + 1 < len(s) and s[j] in '.):' and s[j + 1] == ' ':
                return True
        if len(s) >= 3 and first in '-=_*' and all(c in (first, ' ') for c in s):
            return True
    return False


def _container_body_continuation(prev_body: list, body: list, delta: int) -> bool:
    """容器正文是否为「上一帧正文的继续」（追加 / 截断滑窗推进）。

    容器预览正文按 ``_PREVIEW_MAX_LINES`` 截断为**尾部窗口**：未达上限时
    逐行**追加**（窗口起点不动）；达到上限后窗口整体**右移**（每来一行，
    首行滑出）。``delta = total - prev_total`` 为新增行数。

    ★ 末行是「活动行」（渲染时尚未换行）——上一帧渲染时它可能只有部分内容，
    之后才补全，故**不纳入比较**（去掉 ``prev_body`` 末行再比对稳定行）。
    稳定行在追加时以上一帧为前缀（窗口起点 shift=0）；截断滑窗时窗口起点
    右移 ``delta`` 行（与上一帧稳定行的 ``delta`` 偏移对齐）——两种形态都
    判为继续。

    ``delta < 0``（新容器 / 行数回退）判定为**非继续**——强制重渲染，避免
    跨容器复用陈旧行（两个同类容器在同一帧内先后出现时，节流槽被前者占据）。
    """
    if delta < 0:
        return False
    if delta == 0:
        return list(body) == prev_body
    prev_stable = prev_body[:-1] if prev_body else []
    n = len(prev_stable)
    if n == 0 or delta >= n:
        return True  # 无稳定行可比 / 旧稳定行已全部滑出窗口
    # 追加：窗口起点不动，新正文以上一帧稳定行为前缀
    m = min(len(body), n)
    if m > 0 and list(body[:m]) == prev_stable[:m]:
        return True
    # 截断滑窗：窗口起点右移 delta 行，与上一帧稳定行的 delta 偏移对齐
    m2 = min(len(body), n - delta)
    if m2 > 0 and list(body[:m2]) == prev_stable[delta:delta + m2]:
        return True
    return False


def _plain_paragraph_line(text: str) -> "AnsiLine":
    """无行内触发字符的段落行（与 ``blocks.render_paragraph_line`` 快路径产出等价）。

    ``render_inline`` 对不产生行内格式节点的文本直接返回单 Run
    （``Run(text, Style())``），故此处等价构造，省去每帧 O(窗口长度) 的
    触发字符扫描。仅在调用方**已验证**文本无触发位置时使用；空文本与常规
    路径一致返回空行。
    """
    if not text:
        return AnsiLine()
    return AnsiLine([Run(text, _EMPTY_STYLE)])


__all__ = [
    "AnsiStreamRenderer",
    "AnsiRenderEngine",
    "Run",
    "AnsiLine",
    "wrap_line",
    "truncate_line",
    "ansi_to_line",
]


class AnsiStreamRenderer:
    """流式 ANSI 内容渲染器（TUI 内容路径）。

    复用解析层（RecursiveDescentParser + TokenPipeline + CodeBlockBatcher），
    渲染为 AnsiLine 追加到内部缓冲；``take_lines()`` 消费缓冲。

    Args:
        code_theme: pygments 代码主题名。
        width: 终端宽度（TOC 边框用；可由 set_width 更新）。
    """

    def __init__(self, code_theme: str = "monokai", width: int = 80):
        from src.renderer.recursive_parser import RecursiveDescentParser
        from src.renderer.types import RenderContext
        from src.renderer.pipeline import TokenPipeline
        from src.renderer.extensions import builtin_filter_factories, filter_factories

        self._ctx = RenderContext()
        self._parser = RecursiveDescentParser(ctx=self._ctx)
        self._pipeline = TokenPipeline()
        # 内置过滤器（渲染扩展注册表提供，可被禁用/替换）
        for _factory in builtin_filter_factories():
            try:
                self._pipeline.add_filter(_factory())
            except Exception:
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "内置 ANSI 渲染过滤器装配失败: %r", _factory, exc_info=True
                )
        # 扩展过滤器（插件经 ctx.renderer.register_filter 注册）
        for _factory in filter_factories():
            try:
                self._pipeline.add_filter(_factory())
            except Exception:
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "扩展 ANSI 渲染过滤器注册失败: %r", _factory, exc_info=True
                )
        self._engine = AnsiRenderEngine(code_theme=code_theme, width=width, ctx=self._ctx)
        self._code_theme = code_theme
        # ★ 流式预览：未闭合块（段落/代码块/表格/引用等）每次 write 后整块
        #   重渲染为预览行。使用**独立引擎实例**——预览渲染不污染主引擎的
        #   流式缓冲状态（引用/告示/代码等的 OPEN-LINE 缓冲）。
        self._preview_engine = AnsiRenderEngine(code_theme=code_theme, width=width, ctx=self._ctx)
        self._preview_lines: list[AnsiLine] = []
        # ★ 代码块预览增量高亮缓存（键 = (lang, theme/skip)；流式只追加时仅
        #   渲染新增行，显示侧再按 _PREVIEW_MAX_LINES 截断并给出省略提示）。
        self._code_preview_key: tuple | None = None
        self._code_preview_rows: list[AnsiLine] = []
        #: 上次预览渲染对应的源行数（``_code_preview_rows`` 覆盖的行数）——
        #: 用于增量渲染时定位「已确定行前缀」与活动行（最后一行）。
        self._code_preview_n = 0
        # 代码块预览的增量 split 缓存：上次完整 content + 完整行列表——
        # 流式只追加时只 split 新增片段（避免每帧 O(全文) split/比较）。
        self._code_preview_content: str = ""
        self._code_preview_full_src: list[str] = []
        # ★ 表格预览的行级增量缓存（列宽 + 行渲染复用）。
        self._table_preview_cache = TablePreviewCache()
        # ★ 二维布局块（数学 / Mermaid 等）预览的节流缓存：键 → (上次源码, 渲染行)。
        #   排版成本随源码长度线性增长，逐帧重排会累计 O(n²)（见
        #   ``_throttle_preview``）。
        self._preview_throttle: dict = {}
        # ★ 段落/引用/告示预览的行级增量缓存（对齐代码块按行缓存策略）：
        #   未变化的历史行渲染结果跨帧复用，仅渲染新增/变化的行。按块类型
        #   分实例（同一帧预览可能同时含段落与引用 token，共用实例会互相
        #   重置缓存）。
        self._line_preview_caches: dict = {}
        # ★ 段落行边界「未闭合行内定界符」增量跟踪器（见 _line_delims）：
        #   判定段落哪些完整行可安全逐行渲染，避免长段落每帧整段重解析。
        self._para_boundary = ParagraphBoundaryScanner()
        self._lines: list[AnsiLine] = []
        self._closed = False
        self._width = width
        # 当前未闭合代码块已提交（分段刷出）的代码行数——预览据此跳过，
        # 避免与 committed 行重复显示（见 ``_note_committed_code``）。
        self._committed_code_lines = 0
        # ★ 性能（超长活动行）：段落预览「窗口是否纯文本」的增量判定状态。
        #   ``_para_scan_text`` 为上次判定的未闭合段落文本（增量前缀比对基准）；
        #   ``_para_last_core`` / ``_para_last_url`` 分别是该文本中**最后一个
        #   核心格式触发字符 / 裸 URL 前缀起点**的下标（-1=无），由
        #   ``_note_paragraph_triggers`` 按 ``startswith`` 增量维护；
        #   ``_para_last_nl`` 为最后一个换行符下标（-1=无换行）——供
        #   ``_preview_src_lines`` 判定「窗口前是否还有历史行」时免 O(全文)
        #   反向扫描（超长单行段落的主要成本）。
        self._para_scan_text = ""
        self._para_last_core = -1
        self._para_last_url = -1
        self._para_last_nl = -1
        # 智能排版（typographer）触发位置：最后一个可能被 ``_preprocess_text``
        # 改写的起点下标（增量维护，供 ``_plain_active_window`` O(1) 判否）。
        self._para_last_typo = -1
        # 容器块（告示）预览的子解析结果缓存（见 ``_preview_sub_parse``）。
        self._preview_sub_cache: dict = {}
        # 列表项内块级容器预览（内容行元组 → 渲染行 + 缓存键）。
        self._list_block_preview_key: tuple | None = None
        self._list_block_preview_rows: list[AnsiLine] = []

    def set_width(self, width: int) -> None:
        """更新终端宽度（TOC 边框 + 表格宽度自适应用）。"""
        self._width = width
        self._engine.set_width(width)
        self._preview_engine.set_width(width)
        # 列表项内块级容器预览缓存含宽度相关渲染（表格框线/代码换行），
        # 宽度变化时失效重算。
        self._list_block_preview_key = None

    def write(self, text: str) -> None:
        """流式写入内容块（解析 + 渲染 + 追加）。

        渲染已确定的 committed 行后刷新未闭合块预览（``_refresh_preview``）。
        """
        if self._closed or not text:
            return
        tokens = self._parser.feed(text)
        tokens = self._pipeline.process(tokens, self._ctx)
        for token in tokens:
            self._note_committed_code(token)
            if token.type is TokenType.TOC_MARKER:
                self._lines.extend(self._render_toc())
                continue
            self._lines.extend(self._engine.render(token))
        self._refresh_preview()

    def _note_committed_code(self, token) -> None:
        """跟踪当前未闭合代码块已提交（分段刷出）的代码行数。

        超长代码块超过 ``CodeBlockBatcher`` 缓冲上限时会被分段刷出，已刷出
        的行进入 committed 行；预览若仍从第 0 行整块重渲，会与 committed 行
        **重复显示**（同一批代码行出现两次）。记录已提交行数，供
        ``_render_code_preview`` 跳过——预览只呈现尚未提交的尾部。
        """
        if token.type is TokenType.CODE_BLOCK:
            if token.meta.get("closed", True):
                self._committed_code_lines = 0
            else:
                self._committed_code_lines += token.content.count("\n") + 1
        else:
            self._committed_code_lines = 0

    def _render_toc(self) -> list[AnsiLine]:
        """渲染 ``[TOC]`` 标记处的目录（当前已收集标题）。

        ★ 问题7（位置修复）：TOC 仅由文档中的 ``[TOC]`` 标记触发、在标记
        位置渲染；不再于 ``close()`` 时无条件追加到内容末尾（修复前每条
        subagent markdown / 历史回放消息末尾都会追加一个目录框，位置错误）。
        """
        toc = getattr(self._ctx, "toc", None)
        if not toc:
            return []
        from .toc import render_toc
        with use_render_context(self._ctx):
            return list(render_toc(toc, self._width))

    def _refresh_preview(self) -> None:
        """刷新未闭合块预览行（独立引擎 + 渲染状态回滚，互不污染）。

        解析器 ``peek_pending`` 返回当前未闭合状态的自包含 Token 序列；渲染为
        ``_preview_lines`` 供 UI 整体替换（块闭合后由 committed 行替换，预览清空）。

        ★ 增量渲染：代码块走 ``_render_code_preview``（按行高亮缓存）；段落/
        引用/告示走 ``LinePreviewCache``（按行前缀 + 头部滑窗复用）——均不整块
        重渲染，消除长块流式期间每帧 O(预览行数) 的重复开销。其余 token 由
        独立预览引擎渲染（引擎状态隔离，不污染主引擎）。

        ★ 渲染注册表回滚：预览与提交渲染共用 ``_ctx``，而预览会把**未完成行
        片段**当正文渲染——例如脚注定义行 ``[^a]: ...`` 尚未收尾时，其头部
        ``[^a]`` 被当作脚注引用渲染（``_emit_footnote`` 写 ``fn_order``），
        导致提交渲染的脚注编号顺序被临时状态污染（流式编号与一次性渲染
        不一致）。预览前后保存/恢复 ``fn_order``/``fn_map``，副作用不外泄。
        """
        ctx = self._ctx
        fn_order = getattr(ctx, "fn_order", None)
        fn_map = getattr(ctx, "fn_map", None)
        saved_order = list(fn_order) if fn_order is not None else None
        saved_map = dict(fn_map) if fn_map is not None else None
        try:
            with use_render_context(ctx):
                self._refresh_preview_impl()
        finally:
            if fn_order is not None and saved_order is not None:
                fn_order[:] = saved_order
            if fn_map is not None and saved_map is not None:
                fn_map.clear()
                fn_map.update(saved_map)

    def _refresh_preview_impl(self) -> None:
        try:
            # 列表项内的块级容器（收集中的缩进块）优先走专用预览：解析器
            # 状态机在收集期间不产出 pending token，若不做处理则「列表内
            # 代码块/引用/表格」在流式期间完全不可见（整块突发上屏）。
            if self._parser.list_block_active:
                self._preview_lines = self._list_block_preview()
                return
            ptokens = self._parser.peek_pending()
        except Exception:
            self._clear_preview()
            return
        if not ptokens:
            self._clear_preview()
            return
        eng = self._preview_engine
        eng.reset()
        # ★ 预览渲染异常隔离：未闭合块预览是「额外显示」，其渲染失败**不得**
        #   中断 ``write``（committed 行已产出并进入缓冲，异常向上传播会被
        #   TUI 命令层捕获吞掉 → 本 chunk 的已渲染内容整段丢失）。异常时清空
        #   预览（未闭合块本期不显示），下一帧重新尝试。
        try:
            # 常见路径：单一预览 token → 直接采用其输出列表（免一次 O(行数) 复制）
            if len(ptokens) == 1:
                self._preview_lines = self._render_preview_token(ptokens[0], eng)
                return
            lines: list[AnsiLine] = []
            for tok in ptokens:
                lines.extend(self._render_preview_token(tok, eng))
            self._preview_lines = lines
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "预览渲染异常（清空预览，不影响 committed 行）", exc_info=True
            )
            self._clear_preview()

    def _list_block_preview(self) -> list[AnsiLine]:
        """列表项内块级容器（收集中的缩进块）的流式预览行。

        解析器在收集列表项内块级容器期间处于 NORMAL 状态且无 pending Token
        ——若不做处理，列表内的代码块/引用/表格在整块结束前不可见。此处把
        已收集行（+ 未换行活动行）交给临时子解析器，取其「未闭合预览 Token」
        （或完整 token），经独立管线合并（``CodeBlockBatcher`` 等）后渲染。

        性能：以「已收集行元组」为缓存键——内容未变化的帧（30Hz 空转）零开销；
        追加行时重算一次（与告示正文块级预览同一策略）。
        """
        p = self._parser
        body = list(p._list_block_lines)
        tail = p._peek_incomplete_tail()
        if tail:
            body.append(tail)
        while body and not body[-1].strip():
            body.pop()
        if not body:
            return []
        # 预览行数上限（与解析器 ``_PREVIEW_MAX_LINES`` 同量级）：超长列表内
        # 代码块只预览「首个非空行 + 尾部」——每帧子解析成本有界（提交仍完整）。
        total = len(p._list_block_lines) + (1 if tail else 0)
        limit = 200
        dropped = 0
        if len(body) > limit:
            head = next((ln for ln in body if ln.strip()), "")
            keep = max(1, limit - 1)
            body = ([head] if head else []) + body[-keep:]
            dropped = max(0, total - len(body))
        # ★ 节流（见 ``_render_container_preview``）：列表项内块级容器预览每帧
        #   重子解析 + 渲染整段正文，成本随行数增长 → 累计 O(n²)（800 行列表内
        #   代码块实测 1.3s）。按「已收集行数（单调递增）」节流：短块逐帧实时；
        #   长块按 ``行数//8`` 推进（刷新次数 ~O(log n)，总成本与长度线性）。
        cached = self._list_block_throttle_lookup(body, total)
        if cached is not None:
            return cached
        key = tuple(body)
        if key == self._list_block_preview_key:
            return self._list_block_preview_rows
        tokens = self._parse_list_block_preview(body)
        rows: list[AnsiLine] = []
        eng = self._preview_engine
        eng.reset()
        with use_render_context(self._ctx):
            for tok in tokens:
                # 外层统一补列表/引用前缀（token 特化预览路径如代码块增量
                # 渲染不经 ``engine.render``，不会自带前缀）。
                tok.meta.pop("list_indent", None)
                tok.meta.setdefault("preview", True)
                rows.extend(self._render_preview_token(tok, eng))
        rows = _apply_list_prefix(rows, self._list_block_indent_level())
        bq = self._list_block_bq_depth()
        if bq > 0:
            rows = _apply_bq_prefix(rows, bq)
        if dropped and rows:
            # 截断提示行（与代码块/表格预览同一真源）：插在首行之后
            from .code import render_omitted_line
            rows.insert(1, render_omitted_line(dropped))
        self._list_block_preview_key = key
        self._list_block_preview_rows = rows
        self._preview_throttle[_LIST_BLOCK_THROTTLE_KEY] = (
            list(body), total, rows, len(body[-1]) if body else 0)
        return rows

    def _list_block_throttle_lookup(self, body: list, total: int) -> list | None:
        """列表项内块级容器预览的节流命中查询（见 ``_list_block_preview``）。

        与 ``_throttle_container_preview`` 同族：``total``（已收集行数）单调
        递增；短块（< ``_CONTAINER_PREVIEW_FULL_LINES``）逐帧实时（``delta==0``
        要求正文完全一致）；长块 ``delta==0``（仅活动行增长）按活动行字符
        增长节流，增量达 ``total//8`` 行且首行未变时复用。返回命中行；否则
        返回 ``None``（调用方重渲染）。
        """
        state = self._preview_throttle.get(_LIST_BLOCK_THROTTLE_KEY)
        if state is None or len(state) != 4:
            return None
        prev_body, prev_total, rows, prev_tail_len = state
        delta = total - prev_total
        if delta < 0:
            return None
        tail_len = len(body[-1]) if body else 0
        if delta == 0:
            if prev_total >= _CONTAINER_PREVIEW_FULL_LINES:
                step = max(1, prev_total // 8)
                return rows if tail_len - prev_tail_len < step else None
            return rows if list(body) == prev_body else None
        step = (1 if prev_total < _CONTAINER_PREVIEW_FULL_LINES
                else max(1, prev_total // 8))
        if delta < step and body and prev_body and body[0] == prev_body[0]:
            return rows
        return None

    def _list_block_indent_level(self) -> int:
        """当前列表项缩进层（0 = 顶层列表项）。"""
        return max(0, self._parser._last_list_indent)

    def _list_block_bq_depth(self) -> int:
        """当前引用块嵌套深度（0 = 不在引用内；列表项块预览补前缀用）。"""
        return self._parser.bq_depth

    def _parse_list_block_preview(self, body: list[str]):
        """子解析列表项内块级内容 → 预览 Token 序列（独立管线合并）。"""
        from src.renderer.recursive_parser import RecursiveDescentParser
        from src.renderer.pipeline import TokenPipeline
        from src.renderer.extensions import (
            builtin_filter_factories, filter_factories,
        )

        sub = RecursiveDescentParser(ctx=self._ctx)
        committed = sub.feed("\n".join(body) + "\n")
        pending = sub.peek_pending()
        if pending:
            tokens = pending
        else:
            tokens = committed or sub.flush()
        if not tokens:
            return []
        pipeline = TokenPipeline()
        for factory in builtin_filter_factories():
            try:
                pipeline.add_filter(factory())
            except Exception:
                continue
        for factory in filter_factories():
            try:
                pipeline.add_filter(factory())
            except Exception:
                continue
        return pipeline.process(tokens, self._ctx)

    def _render_preview_token(self, tok, eng) -> list[AnsiLine]:
        """渲染单个预览 token（增量分派；非增量类型走预览引擎）。"""
        if tok.meta.get("preview"):
            t = tok.type
            if t is TokenType.CODE_BLOCK:
                return self._render_code_preview(tok)
            if t is TokenType.TABLE:
                return self._render_table_preview(tok)
            if t is TokenType.MATH_BLOCK_CLOSE:
                return self._render_math_preview(tok)
            if t is TokenType.MERMAID_BLOCK_CLOSE:
                return self._render_mermaid_preview(tok)
            if t is TokenType.FRONT_MATTER:
                return self._render_front_matter_preview(tok)
            if t is TokenType.PARAGRAPH:
                return self._render_paragraph_preview(tok)
            if t is TokenType.BLOCKQUOTE_LINE:
                return self._render_blockquote_preview(tok)
            if t is TokenType.ADMONITION_CLOSE:
                return self._render_admonition_preview(tok)
            if t is TokenType.DETAILS_CLOSE:
                return self._render_details_preview(tok)
            if t is TokenType.FENCED_DIV_CLOSE:
                return self._render_fenced_div_preview(tok)
            if t is TokenType.HTML_BLOCK_LINE:
                tok = self._window_html_line_token(tok)
        return eng.render(tok)

    @staticmethod
    def _window_html_line_token(tok):
        """HTML 块活动行预览的窗口化（超长时只保留尾部）。

        HTML 行的行内解析成本与行长成正比，流式期间活动行每帧解析 → 累计
        O(n²)（40k 字符单行 HTML 块实测 ~1s；长 base64/长文本行更明显）。
        与段落活动行同一口径取尾部窗口（提交路径不受影响，仍渲染完整行）。
        """
        text = tok.content or ""
        limit = _PREVIEW_MAX_LINE_CHARS
        if limit <= 0 or len(text) <= limit:
            return tok
        return Token(tok.type, text[-limit:], dict(tok.meta))

    def _clear_preview(self) -> None:
        """清空预览行与所有增量缓存（块闭合/预览为空时）。"""
        self._preview_lines = []
        self._reset_code_preview_cache()
        self._table_preview_cache.reset()
        self._para_boundary.reset()
        self._list_block_preview_key = None
        self._list_block_preview_rows = []
        self._preview_throttle.clear()
        # 段落切换：重置活动行触发位置增量状态（下次全量重扫；不重置亦正确，
        # 但可省一次长字符串前缀比较）。
        self._para_scan_text = ""
        self._para_last_core = -1
        self._para_last_url = -1
        self._para_last_nl = -1
        self._para_last_typo = -1
        for cache in self._line_preview_caches.values():
            cache.reset()

    def _line_cache(self, kind: str) -> LinePreviewCache:
        """取（或建）指定块类型的行级预览缓存（key 固定，不累积）。"""
        cache = self._line_preview_caches.get(kind)
        if cache is None:
            cache = LinePreviewCache()
            self._line_preview_caches[kind] = cache
        return cache

    @staticmethod
    def _window_preview_line(line: str) -> str:
        """活动行尾部窗口化（超长时只保留尾部，封顶单帧渲染成本）。"""
        limit = _PREVIEW_MAX_LINE_CHARS
        if limit > 0 and len(line) > limit:
            return line[-limit:]
        return line

    def _preview_src_lines(self, content: str, last_nl: int | None = None) -> list[str]:
        """预览源行：仅对**活动行**（最后一行）做尾部窗口化。

        历史行经 ``LinePreviewCache`` 前缀复用只渲染一次，无需窗口；活动行
        每次 write 变化，窗口化把单帧成本封顶（超长无换行行不再逐字符重解析）。

        ★ 超长内容 O(窗口) 化：修复前对整段 ``content.split("\\n")``——每帧
        复制整段（超长活动行 200k 字符时单帧 ~0.06ms、且随行增长线性上升）。
        现在只在**尾部窗口**内定位换行，历史行部分按需切分：单行超长内容
        直接返回窗口切片（O(窗口)），不再全文复制。

        ``last_nl``：已知的「全文最后一个换行符下标」（-1=无换行），由
        ``_note_paragraph_triggers`` 增量维护。提供时用它替代
        ``content.rfind("\\n", 0, len-limit)`` 的 O(全文) 反向扫描（超长单行
        段落的每帧主要成本）；``None`` 时回退反向扫描（通用调用方）。
        """
        if not content:
            return []
        limit = _PREVIEW_MAX_LINE_CHARS
        if limit <= 0 or len(content) <= limit:
            # 短内容：整体切分（最后一行本来就 <= 窗口上限）
            return content.split("\n")
        # 超长内容：窗口内定位最后一个换行，窗口之前的内容按需切分
        window = content[-limit:]
        nl = window.rfind("\n")
        if nl < 0:
            # 窗口内无换行 → 最后一行长度超过窗口上限，窗口即活动行尾部
            # （历史行仅在窗口前存在换行时才有）
            if last_nl is not None:
                prev_nl = last_nl if last_nl < len(content) - limit else -1
            else:
                prev_nl = content.rfind("\n", 0, len(content) - limit)
            if prev_nl < 0:
                return [window]
            parts = content[:prev_nl].split("\n")
            parts.append(window)
            return parts
        head = content[:len(content) - limit + nl]
        parts = head.split("\n")
        parts.append(window[nl + 1:])
        return parts

    def _render_paragraph_preview(self, token) -> list[AnsiLine]:
        """段落预览：稳定前缀逐行增量渲染，仅未闭合尾部整段解析。

        ``ParagraphBoundaryScanner`` 增量判定「行边界处无未闭合行内定界符」
        的完整行数 ``cut``：前 ``cut`` 行逐行解析与整段解析等价 → 走行级增量
        缓存；第 ``cut`` 行起（存在跨软换行标记）整段解析，与提交路径
        （``render_paragraph``）语义一致，消除「预览泄漏 → 提交配对」跳变。

        ★ 性能：修复前多行段落只要含任一「行内标记起始字符」就整段解析，
        长段落流式每帧 O(整段) 重解析（累计 O(n²)）。现常见段落（行内标记均
        行内闭合）只增量渲染新增行。

        ★ 性能（超长单行活动行）：单行段落（无 ``\n``）走
        ``_render_paragraph_lines`` → ``render_paragraph_line`` →
        ``render_inline``，后者即使对**纯文本**也要扫描查找触发字符。现先经
        ``_plain_active_window``（增量维护的触发位置，与 ``render_inline`` 快
        路径判否同一口径）判定窗口是否为纯文本——是则直接等价构造单 Run 行
        （``_plain_paragraph_line``），跳过解析器往返。
        """
        from . import blocks as _blocks
        content = token.content or ""
        self._note_paragraph_triggers(content)
        src = self._preview_src_lines(content, last_nl=self._para_last_nl)
        if len(src) <= 1:
            # 单行段落无跨行配对 → 直接行级渲染
            if src and self._plain_active_window(content):
                rows = [_plain_paragraph_line(src[0])]
            else:
                rows = self._render_paragraph_lines(src)
        else:
            cut = min(self._para_boundary.stable_line_count(content),
                      len(src) - 1)
            if cut <= 0:
                # 首行即存在未闭合标记 → 整段解析
                rows = _blocks.render_paragraph(
                    Token(TokenType.PARAGRAPH, "\n".join(src))
                )
            elif cut >= len(src) - 1:
                # 仅最后一行（活动行）尚未确定：逐行渲染即整段语义
                rows = self._render_paragraph_lines(src)
            else:
                head_rows = self._render_paragraph_lines(src[:cut])
                tail = src[cut:]
                if len(tail) == 1:
                    tail_rows = _blocks.render_paragraph_lines(tail[0])
                else:
                    tail_rows = _blocks.render_paragraph(
                        Token(TokenType.PARAGRAPH, "\n".join(tail))
                    )
                rows = head_rows + tail_rows
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        if dropped:
            return [self._omitted_line(dropped)] + rows
        return rows

    def _render_paragraph_lines(self, src: list[str]) -> list[AnsiLine]:
        """逐行渲染段落源行（行级增量缓存，跨帧复用未变化行）。

        单行源文本可产出多行（行内二维公式），缓存按源行记录产出行数。
        """
        from . import blocks as _blocks
        return self._line_cache("paragraph").render(
            ("paragraph",), src,
            lambda text: _blocks.render_paragraph_lines(text),
        )

    def _note_paragraph_triggers(self, content: str) -> None:
        """增量维护「未闭合段落文本中最后一个行内触发位置」（核心字符 / URL 前缀）。

        流式只追加：``content`` 以上次文本为前缀时，核心触发字符只需扫描新增
        ``delta``；裸 URL 前缀另向前回看 ``_TRIGGER_LOOKBACK`` 个字符（覆盖
        ``ht`` + ``tp://x`` 这类跨增量边界的形态）。前缀关系不成立（解析器
        重建缓冲 / 段落切换）时全量重扫。状态供 ``_plain_active_window`` O(1)
        判定「活动行窗口是否纯文本」——口径与 ``render_inline`` 的快路径判否
        （``inline_parser.text_has_inline_markup``）一致。

        ★ 性能（超长活动行）：判定「是否追加」只用**窗口相邻区域**（≤ 窗口
        长度 ``_PREVIEW_MAX_LINE_CHARS``）比对，而非整段前缀
        ``content.startswith(prev)``——后者在超长单行段落（200k~800k 字符）
        下每帧 O(len(prev))，流式累计 O(n²)（400k 单行实测 4.2s、800k 22s）。
        窗口内触发位置由本方法增量维护，窗口区域一致即保证
        ``_plain_active_window`` 判定正确（提交路径不受影响）。
        """
        prev = self._para_scan_text
        if content is prev:
            return
        prev_len = len(prev)
        if prev_len and len(content) >= prev_len:
            check = _PREVIEW_MAX_LINE_CHARS
            if check > prev_len:
                check = prev_len
            if content[prev_len - check:prev_len] == prev[prev_len - check:]:
                if len(content) == prev_len:
                    return
                delta = content[prev_len:]
                idx = _last_core_trigger_pos(delta)
                if idx >= 0:
                    self._para_last_core = prev_len + idx
                # 最后一个换行（供 ``_preview_src_lines`` 免全量反向扫描）：仅当
                # 增量含换行时更新（``rfind`` 只扫增量）。
                nl = delta.rfind("\n")
                if nl >= 0:
                    self._para_last_nl = prev_len + nl
                # 裸 URL 前缀：回看窗口可能让「起点在旧文本、后缀在新文本」的
                # 前缀被识别（回看长度 >= 最长前缀长度）。
                back = _TRIGGER_LOOKBACK if prev_len > _TRIGGER_LOOKBACK else prev_len
                start = prev_len - back
                probe = content[start:]
                idx = _last_url_prefix_pos(probe)
                if idx >= 0:
                    self._para_last_url = start + idx
                # 智能排版触发位置（增量：只扫「回看 + 新增」小窗口，覆盖跨
                # 增量边界的 pattern，如 ``-`` + ``-``）。回看窗口会让新值可能
                # 小于旧值——仅在更大时更新（保持「最后一个触发位置」语义且
                # 偏保守，避免把仍含触发的窗口误判为纯文本）。
                tstart = prev_len - _TYPO_LOOKBACK
                if tstart < 0:
                    tstart = 0
                tidx = _last_typo_pos(content[tstart:])
                if tidx >= 0 and tstart + tidx > self._para_last_typo:
                    self._para_last_typo = tstart + tidx
                self._para_scan_text = content
                return
        # 全量重扫（前缀关系不成立：解析器重建缓冲 / 段落切换）
        self._para_last_core = _last_core_trigger_pos(content)
        self._para_last_url = _last_url_prefix_pos(content)
        self._para_last_nl = content.rfind("\n")
        self._para_last_typo = _last_typo_pos(content)
        self._para_scan_text = content

    def _plain_active_window(self, content: str) -> bool:
        """活动行渲染窗口是否为「纯文本」（无任何行内触发位置）。

        窗口与 ``_preview_src_lines`` 的口径一致：整段（``len <= 上限``）或
        尾部 ``_PREVIEW_MAX_LINE_CHARS`` 字符。判定依赖
        ``_note_paragraph_triggers`` 增量维护的两个触发位置——与
        ``render_inline`` 的快路径判否（``text_has_inline_markup``）**同一
        口径**，故判定为真时单 Run 快路径必然命中，产出与常规渲染完全一致。

        原实现只跟踪「核心格式字符」且把裸 URL 首字母（``h/f/w``）也算作
        触发字符——英文段落几乎永远判为非纯文本，活动行每帧整段
        ``render_inline``（长段落流式预览的主要成本）。
        """
        # ★ 上下文含缩写定义表时不可走纯文本快路径（普通词可能被缩写替换为
        #   高亮样式，需进入行内解析——与 ``render_inline`` 的快路径例外
        #   同口径）。
        if getattr(self._ctx, "abbr_map", None):
            return False
        n = len(content)
        start = n - _PREVIEW_MAX_LINE_CHARS
        if start < 0:
            start = 0
        if self._para_last_core >= start or self._para_last_url >= start:
            return False
        # 智能排版（--/.../->/(c)/:emoji:/&entity;）同样会把窗口改写为不同文本，
        # 判定窗口需一并排除（``_plain_paragraph_line`` 直接用原文构造单 Run）。
        # 触发位置由 ``_note_paragraph_triggers`` 增量维护 → 此处 O(1)，不再
        # 每帧对 4096 字符窗口切片重扫。
        return self._para_last_typo < start

    @staticmethod
    def _omitted_line(dropped: int) -> AnsiLine:
        """预览截断提示行（与代码块预览同一真源）。"""
        from .code import render_omitted_line
        return render_omitted_line(dropped)

    def _render_blockquote_preview(self, token) -> list[AnsiLine]:
        from . import blocks as _blocks
        depth = max(1, int(token.meta.get("depth", 1))) - 1
        src = self._preview_src_lines(token.content or "")
        rows = self._line_cache("blockquote").render(
            ("blockquote", depth), src,
            lambda text: _blocks.render_blockquote_lines(text, depth),
        )
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        if dropped:
            return [self._omitted_line(dropped)] + rows
        return rows

    def _render_container_preview(self, head: AnsiLine, body: list,
                                  dropped: int, kind: str, cache_key,
                                  render_line, indent: str) -> list[AnsiLine]:
        """容器块（告示 / <details> / fenced div）预览的统一渲染。

        正文含块级标记（列表 / 围栏代码 / 引用 / 表格…）→ 子解析后按完整
        Markdown 渲染（与提交路径 ``_render_nested_blocks`` 一致，消除
        「预览纯文本 → 提交变列表」跳变）；否则逐行走行级增量缓存。

        ``render_line(text) -> list[AnsiLine]`` 允许多行产出（行内二维公式）。

        ★ 性能（长容器流式 O(n²)）：正文含块级标记时每帧重渲染整段正文
        （子解析 + 完整 Markdown），成本与正文行数成正比 → 累计 O(n²)
        （200 行列表实测 5.4ms/帧、8k 字符 8s）。现按「正文总行数（含截断
        丢弃数，单调递增）」节流刷新（``_throttle_container_preview``）：
        短正文（< ``_PREVIEW_THROTTLE_FULL_LIMIT`` 行）逐帧实时；长正文按
        ``total//8`` 递增刷新（刷新次数 ~O(log n)，总成本与长度线性）。纯
        文本正文虽走行级增量缓存，但块级标记判定 ``_has_block_markers`` 为
        O(正文) 全扫——一并纳入节流（只渲染新增行的收益得以保持）。
        """
        total = len(body) + max(0, int(dropped or 0))
        key = ("container", kind, cache_key)

        def _render() -> list[AnsiLine]:
            if body and _has_block_markers(body):
                body_tokens = self._preview_sub_parse(body)
                if body_tokens:
                    return self._preview_engine._render_nested_blocks(
                        head, body_tokens, dropped, indent=indent)
            rest = [self._window_preview_line(seg) for seg in body]
            rows = self._line_cache(kind).render(cache_key, rest, render_line)
            if dropped:
                return [head, self._omitted_line(dropped)] + rows
            return [head] + rows

        return self._throttle_container_preview(key, body, total, _render)

    def _throttle_container_preview(self, key, body: list, total: int,
                                    render) -> list[AnsiLine]:
        """容器块预览的行数节流（见 ``_render_container_preview``）。

        与 ``_throttle_preview``（按源码字符数节流二维排版）同族，但容器正文
        会被解析器截断为**尾部滑窗**——不能用「源码前缀关系」判定继续，改用
        「正文总行数（含丢弃数）单调递增 + 重叠行一致」（``_container_body_continuation``）。
        复用 ``self._preview_throttle`` 槽（键 ``("container", kind, cache_key)``
        与其它节流键不冲突；``_clear_preview`` 统一清空）。

        ★ 活动行（未换行的最后一行）内容逐字符增长时行数不变（``delta == 0``）
        ——短正文逐帧刷新（实时）；长正文额外按活动行字符增长节流，避免
        「短行 × 小 chunk」下逐行完成即重渲染（实测 chunk=8 时刷新次数仍与
        行数同阶）。

        Args:
            key: 节流槽（区分容器类型 / 头参数）。
            body: 当前正文行（可能已截断为尾部窗口）。
            total: 正文总行数（``len(body) + dropped``，单调递增）。
            render: 无参渲染回调，返回 ``list[AnsiLine]``。
        """
        tail_len = len(body[-1]) if body else 0
        state = self._preview_throttle.get(key)
        if state is not None and len(state) == 4:
            prev_body, prev_total, rows, prev_tail_len = state
            delta = total - prev_total
            if delta >= 0:
                step = (1 if prev_total < _CONTAINER_PREVIEW_FULL_LINES
                        else max(1, prev_total // 8))
                if (delta == 0
                        and prev_total >= _CONTAINER_PREVIEW_FULL_LINES):
                    # 行数不变（仅活动行增长）：长容器按活动行字符增长节流
                    if tail_len - prev_tail_len < step:
                        return rows
                elif (delta < step
                      and _container_body_continuation(prev_body, body, delta)):
                    return rows
        rows = render()
        self._preview_throttle[key] = (list(body), total, rows, tail_len)
        return rows

    def _render_admonition_preview(self, token) -> list[AnsiLine]:
        from . import blocks as _blocks
        atype = str(token.meta.get("type", "NOTE")).upper()
        title = str(token.meta.get("title", "") or "")
        collapsible = bool(token.meta.get("collapsible", False))
        head_text = str(token.meta.get("head_text", "") or "")
        body = list(token.meta.get("body_lines") or [])
        if token.content:
            body = str(token.content).split("\n") + body
        head = _blocks.render_admonition_head(atype, head_text, title=title,
                                              collapsible=collapsible)
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        return self._render_container_preview(
            head, body, dropped, "admonition",
            ("admonition", atype, title, collapsible, head_text),
            _blocks.render_admonition_body_lines, indent="    ",
        )

    def _render_details_preview(self, token) -> list[AnsiLine]:
        """``<details>`` 折叠块预览（头 + 正文，与提交路径同语义）。"""
        from . import blocks as _blocks
        summary = str(token.meta.get("summary", "") or "")
        body = list(token.meta.get("body_lines") or [])
        if token.content:
            body = str(token.content).split("\n") + body
        head = _blocks.render_details_head(
            summary, bool(token.meta.get("open", False)))
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        return self._render_container_preview(
            head, body, dropped, "details", ("details", summary),
            _blocks.render_details_body_lines, indent="  ",
        )

    def _render_fenced_div_preview(self, token) -> list[AnsiLine]:
        """Fenced Div（``:::type``）预览（头 + 正文，与提交路径同语义）。"""
        from . import blocks as _blocks
        dtype = str(token.meta.get("type", "NOTE") or "NOTE")
        head_text = str(token.meta.get("head_text", "") or "")
        body = list(token.meta.get("body_lines") or [])
        if token.content:
            body = str(token.content).split("\n") + body
        head = _blocks.render_fenced_div_head(dtype, head_text)
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        return self._render_container_preview(
            head, body, dropped, "fenced_div", ("fenced_div", dtype),
            _blocks.render_fenced_div_body_lines, indent="  ",
        )

    def _preview_sub_parse(self, lines: list[str]) -> list:
        """预览用子解析（带小容量缓存：同一内容重复帧零成本）。

        `正文含块级标记的告示在流式期间每帧重渲染，缓存键为内容行元组——
        内容未变化的帧（如 30Hz 空转）直接复用解析结果。
        """
        key = tuple(lines)
        cached = self._preview_sub_cache.get(key)
        if cached is not None:
            return cached
        try:
            tokens = self._parser._parse_sub_blocks(list(lines))
        except Exception:
            tokens = []
        if len(self._preview_sub_cache) >= _PREVIEW_SUB_CACHE_MAX:
            self._preview_sub_cache.clear()
        self._preview_sub_cache[key] = tokens
        return tokens

    def _render_table_preview(self, token) -> list[AnsiLine]:
        """表格预览：列宽与行渲染的行级增量（``TablePreviewCache``）。

        未闭合表格每次 write 都整表重渲染（列宽对每单元格做行内解析 +
        每行重排）；缓存后列宽不变时只渲染新增行、历史行对象复用
        （UI 侧 styled 缓存因此可命中）。
        """
        rows = token.meta.get("rows") or []
        aligns = token.meta.get("alignments") or []
        # ★ 契约显式化：缓存键用表头元组区分表格实例——修复前恒传 ``()``，
        #   键实际失效、完全依赖内部 ``_header`` 判断（隐性契约，新增字段/
        #   复用实例时易误复用）。表头相同即同一表格的连续预览（键稳定）。
        key = tuple(rows[0]) if rows else ()
        return self._table_preview_cache.render(key, rows, aligns, self._width)

    def _throttle_preview(self, key, src: str, render) -> list[AnsiLine]:
        """二维布局块（数学 / Mermaid 等）预览的节流渲染。

        这类块的排版成本与源码长度成正比，而流式期间每帧都会重排未闭合块
        → 累计 O(n²)。节流口径：``src`` 以上次源码为前缀且增长量小于步长时
        复用上次结果。步长按上次源码长度自适应——短源码（< ``_PREVIEW_THROTTLE_FULL_LIMIT``）
        逐帧刷新（排版成本可忽略，保证实时），长源码按 ``len//8`` 递增
        （刷新次数约 O(log n)，总排版成本与长度线性）。前缀关系破裂（解析器
        重建 / 块切换）时立即重排，保证内容不错位。

        Args:
            key: 节流槽（区分块类型/样式参数）。**不得包含随帧变化的元数据**
                （如截断行数）——否则每帧都会成为新槽位、节流失效（且槽位
                无限累积）。
            src: 当前源码（增长序列）。
            render: 无参渲染回调，返回 ``list[AnsiLine]``。

        Returns:
            本次应显示的预览行（可能复用上次结果）。
        """
        state = self._preview_throttle.get(key)
        if state is not None and state[0] and src.startswith(state[0]):
            prev_len = len(state[0])
            step = (1 if prev_len < _PREVIEW_THROTTLE_FULL_LIMIT
                    else max(1, prev_len // 8))
            if len(src) - prev_len < step:
                return state[1]
        rows = render()
        self._preview_throttle[key] = (src, rows)
        return rows

    def _throttle_preview_sliding(self, key, total: int, first_line: str,
                                  render) -> list[AnsiLine]:
        """**尾部滑窗**类块（Mermaid）预览的节流渲染（见 ``_throttle_preview``）。

        与 ``_throttle_preview`` 的区别：这类块的源码被解析器截断为
        「首行 + 尾部窗口」（``_PREVIEW_MERMAID_LINES``）——源码不再是增长
        序列而是**滑窗**，``src.startswith(prev)`` 恒不成立 → 前缀节流失效、
        每帧重排（800 边图实测 2s）。改用单调递增的**行数**（``total``）节流，
        并以首行（块类型声明）区分块实例（切换块 → 首行变化 → 立即重排，
        避免复用上一块的陈旧图形）。

        Args:
            key: 节流槽（区分块类型）。
            total: 源码行数 + 截断丢弃行数（单调递增）。
            first_line: 源码首行（块身份锚点）。
            render: 无参渲染回调。
        """
        state = self._preview_throttle.get(key)
        if state is not None and len(state) == 3:
            prev_total, prev_first, rows = state
            delta = total - prev_total
            if delta >= 0 and first_line == prev_first:
                step = (1 if prev_total < _PREVIEW_THROTTLE_FULL_LIMIT
                        else max(1, prev_total // 8))
                if delta == 0:
                    # 行数不变（仅活动行增长）：短源码逐帧实时，长源码节流
                    if prev_total >= _PREVIEW_THROTTLE_FULL_LIMIT:
                        return rows
                elif delta < step:
                    return rows
        rows = render()
        self._preview_throttle[key] = (total, first_line, rows)
        return rows

    def _render_math_preview(self, token) -> list[AnsiLine]:
        """数学块流式预览：节流二维排版 + 超长降级为提示框。

        数学排版（分数/根式/大算符/矩阵/上下标的二维布局）成本与公式长度
        成正比，流式期间每帧重排整段公式 → 累计 O(n²)（8k 字符单行公式逐
        8 字符写入实测 ~8s，渲染线程被占满）。节流见 ``_throttle_preview``；
        超过 ``_MATH_PREVIEW_MAX_SRC`` 的超长公式降级为提示框（提交路径仍
        完整排版）。
        """
        src = token.meta.get("source") or ""
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        from . import math as _math
        if len(src) > _MATH_PREVIEW_MAX_SRC:
            over_key = "math_overflow"
            state = self._preview_throttle.get(over_key)
            if state is None or state[0] != len(src):
                rows = _math.render_math_omitted(len(src))
                self._preview_throttle[over_key] = (len(src), rows)
            return self._preview_throttle[over_key][1]
        key = ("math",)
        rows = self._throttle_preview(
            key, src, lambda: _math.render_math_block(src, dropped=dropped))
        # 提示框槽与正文槽互斥：本次为正文预览时清理残留的 overflow 槽
        self._preview_throttle.pop("math_overflow", None)
        return rows

    def _render_mermaid_preview(self, token) -> list[AnsiLine]:
        """Mermaid 块流式预览：节流图形布局（见 ``_throttle_preview_sliding``）。

        图形布局成本随节点/边数量（即源码长度）增长，逐帧重排同样退化为
        O(n²)；节流后与长度线性。源码行数已由解析器按 ``_PREVIEW_MERMAID_LINES``
        截断为「首行 + 尾部窗口」（超出部分带省略提示）——源码是**滑窗**而非
        增长序列，故用 ``_throttle_preview_sliding``（按行数节流 + 首行锚定）。
        """
        src = token.meta.get("source") or ""
        dropped = int(token.meta.get("preview_dropped", 0) or 0)
        from . import mermaid as _mermaid
        first_line = src.split("\n", 1)[0] if src else ""
        total = (src.count("\n") + 1 if src else 0) + dropped
        return self._throttle_preview_sliding(
            ("mermaid",), total, first_line,
            lambda: _mermaid.render_mermaid_block(src, dropped=dropped))

    def _render_front_matter_preview(self, token) -> list[AnsiLine]:
        """Front Matter 流式预览：节流键值卡片渲染（见 ``_throttle_preview``）。

        卡片渲染对每行键值做行内解析 + 对齐拼装，成本与行数成正比；流式期间
        每帧重建整张卡片（解析器已按 ``_PREVIEW_MAX_LINES`` 截断行数）仍会
        累计 O(n²)。节流后刷新次数约 O(log n)。
        """
        src = token.content or ""
        from . import blocks as _blocks
        return self._throttle_preview(
            ("front_matter",), src,
            lambda: _blocks.render_front_matter(token))

    def _reset_code_preview_cache(self) -> None:
        """清空代码块预览增量缓存（块闭合/预览清空时调用）。"""
        self._code_preview_key = None
        self._code_preview_rows = []
        self._code_preview_n = 0
        self._code_preview_content = ""
        self._code_preview_full_src = []

    @staticmethod
    def _code_num_width(total: int, linenostart: int = 1,
                        linenostep: int = 1) -> int:
        """行号列宽（与 ``ansi.code.highlight_code_lines`` 同一规则）。"""
        from .code import code_line_number_width
        return code_line_number_width(total, linenostart, linenostep)

    def _code_lines_from_content(self, src: str) -> tuple[list[str], bool]:
        """兼容路径：从 ``content`` 字符串增量 split 出完整行列表。

        返回 ``(full_lines, reset_rows)``——``reset_rows=True`` 表示本次内容与
        缓存无前缀关系（首次 / 分歧），调用方需重置高亮缓存。

        缓存上次 content 与行列表：新 content 以旧为前缀时只 split 增量片段并
        就地扩展（避免每帧 O(全文) split）。解析器预览 token 走 ``meta["lines"]``
        （不经此路径）；此路径服务测试 / 旧接口。
        """
        if not src:
            self._code_preview_content = ""
            self._code_preview_full_src = []
            return [], True
        old_content = self._code_preview_content
        if old_content and src.startswith(old_content):
            delta = src[len(old_content):]
            full_lines = self._code_preview_full_src
            if delta:
                parts = delta.split("\n")
                if full_lines:
                    full_lines[-1] = full_lines[-1] + parts[0]
                    if len(parts) > 1:
                        full_lines.extend(parts[1:])
                else:
                    full_lines = parts
                    self._code_preview_full_src = full_lines
            self._code_preview_content = src
            return full_lines, False
        full_lines = src.split("\n")
        self._code_preview_content = src
        self._code_preview_full_src = full_lines
        return full_lines, True

    @staticmethod
    def _code_active_window(src_lines: list[str], start: int, n: int) -> list[str]:
        """代码块预览「活动行」（未换行尾行）的渲染源，超长时取尾部窗口。

        pygments 词法高亮的成本与行长度成正比；流式期间活动行每帧重渲，超长
        单行（minified JSON / base64 / 长 URL）会退化为 O(n²)。仅对**活动行**
        取 ``_CODE_ACTIVE_MAX_CHARS`` 字符的尾部窗口——始终显示**最新**内容，
        单帧成本封顶。已确定的历史行不受影响（它们由逐行高亮缓存复用）。
        提交路径仍渲染完整行，预览窗口只影响流式中间态的可见长度。

        调用契约：``start``、``n`` 为活动行区间（通常恰为最后一行
        ``n-1..n``）；返回新列表（不修改调用方/解析器持有的行列表）。
        """
        if n <= start:
            return []
        limit = _CODE_ACTIVE_MAX_CHARS
        out: list[str] = []
        last = n - 1
        for idx in range(start, n):
            line = src_lines[idx]
            if limit > 0 and idx == last and len(line) > limit:
                out.append(line[-limit:])
            else:
                out.append(line)
        return out

    def _render_code_preview(self, token) -> list[AnsiLine]:
        """代码块流式预览：按行增量高亮缓存 + 尾部截断省略提示。

        逐行高亮对「只追加」的流式输入可安全缓存（``highlight_code_lines``
        本身逐行处理、无跨行状态）——修复前每次 write 对整段预览（最多
        ``_PREVIEW_MAX_LINES`` 行）重新词法高亮，600 行代码流式输出实测约
        27s，渲染线程在 30Hz 下近乎满载，进而造成命令队列背压。

        增量口径（每次 write 最多渲染 2 行）：
          - **已确定行**（``src_lines[:-1]``）按行数增量渲染；某行升格为完整
            行时以其**最终内容**渲染一次并写入逐行高亮缓存（``use_cache=True``）
            ——闭合提交时命中该缓存，免整块重新词法高亮（300 行实测
            110ms → 0.3ms）；
          - **活动行**（未换行的最后一行，内容逐帧变化）每帧重渲
            （``use_cache=False``——不污染共享缓存，否则每个中间前缀都会成为
            缓存条目并触发整体清空）。

        ★ 修复前只按「行数增加」增量渲染：行数不变时活动行的新内容被忽略
        （预览显示陈旧文本），且某行升格时其槽位保留旧内容（预览出现重复/
        错位行），完整行也从未以最终内容渲染（闭合提交时全部行缓存失效）。
        """
        from . import code as _code
        from .._block_parser import RegexFreeBlockParser

        lang = token.meta.get("lang", "")
        title = token.meta.get("title", "")
        closed = bool(token.meta.get("closed", True))
        dropped = int(token.meta.get("preview_dropped", 0) or 0)

        # 解析器预览 token 直接携带行列表（``meta["lines"]``，尾部活动行为空
        # 时零拷贝复用内部缓冲）——免除每帧 ``"\n".join`` + 渲染层 ``split``
        # 的 O(全文) 往返；仅兼容路径（测试 / 旧接口）才从 ``content`` 增量 split。
        provided = token.meta.get("lines")
        if provided is not None:
            full_lines = provided
            reset_rows = False
        else:
            full_lines, reset_rows = self._code_lines_from_content(token.content or "")

        # 超长代码块的分段提交：已提交的前 skip 行由 committed 显示，预览只
        # 呈现尚未提交的尾部（否则同一批行在 committed 与 preview 中重复显示）。
        skip = min(self._committed_code_lines, len(full_lines))
        src_lines = full_lines[skip:] if skip else full_lines
        hl = token.meta.get("highlight_lines") or ()
        linenos = bool(token.meta.get("linenos", False))
        lineno_start = int(token.meta.get("lineno_start", 1) or 1)
        lineno_step = int(token.meta.get("lineno_step", 1) or 1)
        # 行号列宽随总行数位数变化（2→3 位时历史行需重建以保持对齐）——
        # 纳入缓存键，宽度变化时整体重渲染一次（跨越 99/999 行的罕见时刻）。
        num_width = (self._code_num_width(skip + len(full_lines),
                                          lineno_start, lineno_step)
                     if linenos else 0)
        # 首行纳入缓存键：内容「滑窗」（列表项内块级预览截断为尾部窗口）时
        # 前缀不再稳定，必须重置增量缓存，否则沿用旧行导致内容错位。
        first_line = full_lines[0] if full_lines else ""
        key = (lang, self._code_theme, skip, dropped, tuple(hl), num_width,
               first_line, lineno_start, lineno_step)
        if reset_rows or key != self._code_preview_key:
            self._code_preview_key = key
            self._code_preview_rows = []
            self._code_preview_n = 0
        rows = self._code_preview_rows
        # ★ 增量高亮（修正「活动行内容变化未刷新」）：``src_lines`` 的**最后
        #   一行**是尚未换行的活动行（内容逐帧增长），其前 ``n-1`` 行是已确定
        #   的完整行。修复前只按「行数增加」增量渲染——行数不变时活动行的新
        #   内容被忽略（预览显示陈旧文本；某行升格为完整行时其槽位保留旧内容），
        #   且完整行从未以最终内容渲染 → ``_LINE_HIGHLIGHT_CACHE`` 无对应条目，
        #   闭合提交时整块重新词法高亮（300 行实测约 110ms，阻塞渲染线程）。
        #   现改为：已确定行按行数增量渲染（升格时以最终内容渲染一次并入缓存），
        #   活动行每帧重渲（O(1) 行）。
        n = len(src_lines)
        stable_prev = self._code_preview_n - 1 if self._code_preview_n else 0
        if stable_prev > n - 1:
            stable_prev = n - 1 if n else 0
        if len(rows) > stable_prev:
            del rows[stable_prev:]
        if n > stable_prev:
            # 已确定行（不含活动行）走带缓存的逐行高亮（升格时以最终内容
            # 渲染一次并进 ``_LINE_HIGHLIGHT_CACHE``——闭合提交时命中，
            # 免整块重新词法高亮）。
            stable_new = n - 1
            if stable_new > stable_prev:
                rows.extend(
                    _code.highlight_code_lines(
                        src_lines[stable_prev:stable_new], lang, self._code_theme,
                        highlight_lines=hl,
                        start_index=skip + stable_prev + 1,
                        linenos=linenos,
                        total_lines=skip + len(src_lines),
                        linenostart=lineno_start,
                        linenostep=lineno_step,
                    )
                )
            # 活动行（最后一行，内容逐帧变化）：每帧重渲以保证预览实时，
            # 但不写入共享缓存（否则活动行的每个中间前缀都会成为缓存条目，
            # 持续膨胀并可能触发整体清空）。
            # ★ 性能（超长单行）：活动行只词法高亮**尾部窗口**
            #   （``_CODE_ACTIVE_MAX_CHARS``）——pygments 高亮成本与行长度成正
            #   比，无窗口时 minified JSON / base64 等超长单行（可达数十万字符）
            #   每帧重解析整行，累计 O(n²)（20 万字符单行实测 35s）。窗口始终
            #   含**最新**内容，故刷新实时、无滞后；提交时仍完整渲染。
            rows.extend(
                _code.highlight_code_lines(
                    self._code_active_window(src_lines, stable_new, n),
                    lang, self._code_theme,
                    highlight_lines=hl,
                    start_index=skip + stable_new + 1,
                    linenos=linenos,
                    total_lines=skip + len(src_lines),
                    linenostart=lineno_start,
                    linenostep=lineno_step,
                    use_cache=False,
                )
            )
        self._code_preview_n = n
        limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
        omitted = max(0, len(rows) - limit) + dropped
        out: list[AnsiLine] = []
        if not skip:
            # 打开围栏/标题已随首段提交时不重复
            if title:
                out.append(_code.render_title_line(title))
            out.append(_code.render_fence_line(lang))
        if omitted:
            out.append(_code.render_omitted_line(omitted))
        out.extend(rows[-limit:] if omitted else rows)
        if closed:
            out.append(_code.render_close_fence_line())
        return out

    def take_preview_lines(self) -> list[AnsiLine]:
        """返回当前未闭合块预览行（全量，不消费——UI 每次整体替换）。

        与 ``take_lines`` 不同：预览是「当前未闭合状态的整体快照」，UI 侧
        以**替换**语义使用（每次 write 后用最新预览替换上一帧预览），因此
        本方法不清空缓冲；``close()`` 后预览恒为空列表。

        ★ 不再复制列表：调用方（apply/model）本就按替换语义复制（``list(...)``）
        ——避免每帧对整段预览行多一次 O(行数) 复制。**调用方不得原地修改**
        返回列表。
        """
        return self._sanitize_lines(self._preview_lines)

    def close(self) -> None:
        """关闭渲染器：flush 解析器残差并渲染（幂等）。

        流式 markdown 结束时在末尾渲染 TOC（目录，若 ctx.toc 有标题）。
        """
        if self._closed:
            return
        self._closed = True
        try:
            tokens = self._parser.flush()
            tokens = self._pipeline.process(tokens, self._ctx)
            for token in tokens:
                # [TOC] 标记在 flush 残差中同样就地渲染
                if token.type is TokenType.TOC_MARKER:
                    self._lines.extend(self._render_toc())
                    continue
                self._lines.extend(self._engine.render(token))
            # ★ 文档尾附录：脚注定义列表 + 参考式链接列表（与 Rich 路径同源语义）。
            #   仅当文档实际定义过脚注 / 参考链接时才输出（无定义零额外行）。
            self._lines.extend(self._render_footnotes())
            self._lines.extend(self._render_ref_links())
        finally:
            self._engine.reset()
            self._clear_preview()

    # ── 文末附录（脚注 / 参考链接） ───────────────────

    def _render_footnotes(self) -> list[AnsiLine]:
        """渲染脚注定义列表（按引用顺序，未引用者按字母序排末尾）。"""
        fn_map = getattr(self._ctx, "fn_map", None)
        if not fn_map:
            return []
        fn_order = list(getattr(self._ctx, "fn_order", ()) or ())
        ordered = [r for r in fn_order if r in fn_map]
        ordered.extend(sorted(set(fn_map.keys()) - set(ordered)))
        lines: list[AnsiLine] = [
            AnsiLine.of("\u2500" * max(1, self._width), Style(fg=240)),
        ]
        for i, ref_id in enumerate(ordered, 1):
            content = fn_map.get(ref_id)
            if content is None:
                continue
            # 多段落脚注（正文含 ``\n``）→ 逐段渲染为多行，续行缩进对齐；
            # 段间空行不输出（避免脚注列表被空行割裂）。
            segs = [s for s in str(content).split("\n") if s.strip()] or [""]
            for si, seg in enumerate(segs):
                line = AnsiLine.of(
                    f"  [{i}] " if si == 0 else "      ", Style(fg=45))
                for run in render_inline(seg, ctx=self._ctx):
                    line.append_run(run)
                if si == len(segs) - 1:
                    line.append(" \u21a9", Style(fg=45, dim=True))
                lines.append(line)
        return lines

    def _render_ref_links(self) -> list[AnsiLine]:
        """渲染参考式链接定义列表（``[id]: url "title"``）。"""
        ref_map = getattr(self._ctx, "ref_map", None)
        if not ref_map:
            return []
        lines: list[AnsiLine] = [
            AnsiLine.of(""),
            AnsiLine.of("\U0001f517 \u5f15\u7528\u94fe\u63a5", Style(fg=45, bold=True)),
            AnsiLine.of("\u2500" * max(1, self._width), Style(fg=240)),
        ]
        for ref_id, pair in sorted(ref_map.items()):
            try:
                url, title = pair
            except (TypeError, ValueError):
                url, title = pair, ""
            line = AnsiLine.of(f"  [{ref_id}] {url}", Style(fg=45, underline=True))
            if title:
                line.append(f' "{title}"', Style(fg=240, italic=True))
            lines.append(line)
        return lines

    def take_lines(self) -> list[AnsiLine]:
        """取出全部已渲染行（消费缓冲）。

        ★ 消毒残留原始 ANSI：markdown 源文本可能透传输入里的原始转义序列
        （如子代理结果内嵌 read_file 高亮、模型原文）。保留进 ``Run.text``
        会让宽度测量把转义码当可见字符（宽度膨胀 → 误触发 wrap），
        ``wrap_line`` 逐字符截断把转义序列拦腰截断（残留 ``;49;00m``）渲染
        错乱。输出统一消毒——**合法 SGR 解析为 Run 样式保留颜色**（问题8：
        修复前一律剥离，模型/工具的合法高亮被误伤），非 SGR 控制序列/孤立
        ESC 移除（防注入）；无 ESC 时零拷贝原样返回（fast path）。
        """
        lines = self._lines
        self._lines = []
        return self._sanitize_lines(lines)

    @staticmethod
    def _sanitize_lines(lines: list[AnsiLine]) -> list[AnsiLine]:
        """ANSI 消毒：合法 SGR → Run 样式（保留颜色），其余控制序列移除。

        ★ 行级缓存（``AnsiLine._esc_checked``）：预览/已渲染行跨帧复用同一
        AnsiLine 对象，已确认无转义序列的行直接跳过扫描——避免每帧对全部行
        重扫（大预览下 ``_has_esc`` 曾占预览刷新耗时一半以上）。
        """
        def _has_esc(runs) -> bool:
            return any(
                "\x1b" in (r.text or "") or "\x07" in (r.text or "")
                for r in runs
            )

        # 定位首个未检查行（已检查行恒为前缀——预览行按前缀复用）
        idx = 0
        n = len(lines)
        while idx < n and getattr(lines[idx], "_esc_checked", False):
            idx += 1
        if idx == n:
            return lines

        # 只扫描未检查的尾部：全部干净则打标记并零构建返回原 list
        dirty = False
        for i in range(idx, n):
            line = lines[i]
            if getattr(line, "_esc_checked", False):
                continue
            if _has_esc(line.runs):
                dirty = True
                break
            line._esc_checked = True
        if not dirty:
            return lines

        # 有原始转义序列（罕见）→ 全量消毒
        from .helpers import ansi_to_runs, strip_ansi
        out: list[AnsiLine] = []
        for line in lines:
            if getattr(line, "_esc_checked", False):
                out.append(line)
                continue
            new_line = AnsiLine()
            for r in line.runs:
                text = r.text or ""
                if "\x1b" not in text and "\x07" not in text:
                    new_line.append(text, r.style, getattr(r, "link", None))
                    continue
                for sub in ansi_to_runs(text, r.style):
                    clean = strip_ansi(sub.text).replace("\x1b", "").replace("\x07", "")
                    if clean:
                        new_line.append(clean, sub.style,
                                        getattr(r, "link", None))
            new_line._esc_checked = True
            out.append(new_line)
        return out

    @property
    def lines(self) -> list[AnsiLine]:
        """当前已渲染行（不消费）。"""
        return self._lines

    @property
    def is_closed(self) -> bool:
        return self._closed
