"""_block_parser — RegexFreeBlockParser：无正则块级递归下降解析器。

从 `recursive_parser.py`（2277 行）提取的 RegexFreeBlockParser 类。
职责：将 Markdown 文本解析为 Token 流（块级扫描 + 内联委托）。
"""

from __future__ import annotations

import logging
import string
_logger = logging.getLogger(__name__)

from ._utils import (
    _get_fence_info, parse_highlight_lines, parse_linenos, parse_lineno_options,
    decode_html_entities,
)
from .types import Token, TokenType, RenderContext
from ._table_utils import (
    _is_table_row, _is_table_data_row, _is_table_separator,
    _parse_table_row, _parse_table_alignments, _normalize_table_cells,
    _is_grid_table_border, _is_grid_table_row, parse_grid_table,
    _is_dashed_separator, parse_dashed_table,
)
from ._block_helpers import (
    _is_empty_line, _strip_left, _rstrip_line,
    _is_only_chars,
    _BLOCK_HTML_TAGS, _VOID_HTML_TAGS,
    _is_blockquote_line, _get_blockquote_text, _split_blockquote,
    _is_code_fence_line,
    _rstrip_trailing_hashes,
    _front_matter_delim, _front_matter_format, _is_front_matter_close,
)
from ._block_parser_state import (
    _State, _ADMONITION_TYPES, _HTML_HEADING_LEVELS, _DISPLAY_MATH_ENVS,
)
from ._block_parser_stream import _BlockParserStreamMixin
from ._html_attrs import parse_open_tag, parse_attrs, language_of, align_of


#: 链接定义「下一行标题」中的可转义字符（CommonMark：任意 ASCII 标点）
_REF_TITLE_ESCAPABLE: frozenset[str] = frozenset(string.punctuation)


def _match_display_env(stripped: str) -> tuple[str, str] | None:
    """行首是否为 KaTeX auto-render 显示环境（``\\begin{align}`` 等）。

    Returns:
        ``(环境名, 结束标记)``；非显示环境返回 ``None``。
    """
    if not stripped.startswith("\\begin{"):
        return None
    close = stripped.find("}", 8)
    if close < 0:
        return None
    env = stripped[7:close]
    if env not in _DISPLAY_MATH_ENVS:
        return None
    return env, "\\end{" + env + "}"


# ═══════════════════════════════════════════════════════════
# RegexFreeBlockParser — 无正则的块级递归下降解析器
# ═══════════════════════════════════════════════════════════


class RegexFreeBlockParser(_BlockParserStreamMixin):
    """真正无正则的递归下降块级 Markdown 解析器。

    所有块级语法检测通过字符级扫描完成，无任何正则表达式。
    引用块通过剥离 > 前缀后递归解析实现嵌套。

    状态机：
      NORMAL → 按优先级检测各块级语法
      CODE_FENCE → 代码 fence 块内
      MATH_BLOCK → $$ 数学块内
      DISPLAY_MATH_BLOCK → \\[ 显示数学块内
      MERMAID_BLOCK → Mermaid 块内
      DETAILS_BLOCK → <details> 块内
      INDENTED_CODE → 缩进代码块内
      HTML_BLOCK → HTML 块内
      TABLE_ACTIVE → 表格行解析中
    """

    _MAX_BUFFER_SIZE = 1_000_000

    _PREVIEW_CODE_LINES_MAX = 4000
    """代码类块预览缓冲的行数上限（超出丢弃最旧行，见 ``_emit_code_line``）。"""

    def __init__(self, ctx: RenderContext | None = None):
        self._ctx = ctx if ctx is not None else RenderContext()
        self._buffer = ""
        self._state = _State.NORMAL
        # 必须在 _reset_normal_state 之前初始化列表状态变量和表格缓冲
        self._list_indents: list[int] = []
        self._table_rows: list[list[str]] = []
        self._table_alignments: list[str] = []
        self._table_pending_rows: list[str] = []
        # 流式表格缓冲是否来自引用块（引用块内要求分隔行才成表格）
        self._table_pending_bq: bool = False
        # Grid table（Pandoc ``+---+``）收集缓冲（None = 未在收集）
        self._grid_lines: list[str] | None = None
        #: Grid table 收集时所在的引用块深度（>0 时提交的 TABLE token 补引用前缀）
        self._grid_bq_depth: int = 0
        # Pandoc simple table 收集状态 (header_lines, sep_line, rows)（None = 未收集）
        self._dashed: tuple | None = None
        # 定义列表续行缓冲
        self._def_cont_buffer: list[str] = []
        self._reset_normal_state()

        # 多行块状态
        self._block_fence_char: str = ''
        self._block_fence_len: int = 0
        self._block_lang: str = 'text'
        self._block_attrs: str = ''
        self._block_title: str = ''
        self._block_lines: list[str] = []
        # 代码类块（CODE_FENCE/INDENTED_CODE）已发射行的预览缓冲——
        # 代码行经 CODE_LINE 即时 emit（不进 _block_lines），单独记录供
        # 流式预览整块重渲染。
        self._preview_code_lines: list[str] = []
        # 预览缓冲因上限被丢弃的行数（省略提示需计入，见 _emit_code_line）
        self._preview_code_dropped: int = 0
        # 缩进代码块内「暂存的空行数」：空行仅在后续仍有缩进内容时才作为
        # 块内空行补发，块结束（尾随空行）时丢弃（CommonMark：尾随空行不计入）。
        self._indented_code_pending_blanks: int = 0
        self._block_html_tag: str = ''
        # KaTeX auto-render 显示环境的结束标记（``\end{align}`` 等）；
        # ``None`` 表示当前数学块由 ``$$`` / ``\[`` 定界（见 _DISPLAY_MATH_ENVS）
        self._math_env_end: str | None = None
        # ``<pre>`` 块语言（``class="language-x"`` 推断；进入块时重置）
        self._html_pre_lang: str = ''
        # 当前 HTML 块的开标签属性（``<ol start="3">`` 等结构化收集阶段使用）
        self._html_block_attrs: dict = {}
        self._block_nested_fence: int = 0
        self._block_div_type: str = ''

        # 列表状态
        # _list_indents 已在 _reset_normal_state 之前初始化

        # 引用块状态
        self._bq_active: bool = False
        self._bq_depth_stack: list[int] = []
        self._bq_in_recursion: int = 0
        self._pending_lines: list[str] = []

        # Admonition
        self._in_admonition: bool = False
        self._admonition_type: str = ''
        # Fenced 告示（``!!! type "title"`` / ``??? type``）
        self._adm_title: str = ''
        self._adm_collapsible: bool = False
        # 引用风格告示（``> [!TYPE] 文本``）中与 ``[!TYPE]`` 同行的文本——
        # 渲染为头部标题；正文行另存 ``_block_lines``（不再把首行当标题）。
        self._adm_head_text: str = ''

        # 脚注定义
        self._pending_fn_def: str | None = None
        # 脚注续段待定状态是否跨过空行（GFM 多段落脚注：空行后缩进仍属正文）
        self._pending_fn_blank: bool = False

        # 参考式链接定义的多行标题待定（CommonMark：标题可写在 URL 的**下一
        # 行**、缩进 ≤3 空格并以引号/括号定界）。记录 ref_id，下一行若为标题
        # 行则补全 ref_map、不再作为正文输出（修复前标题行泄漏为段落文本）。
        self._pending_ref_title: str | None = None
        # 参考式链接定义的「URL 在下一行」待定（``[id]:`` 独占一行）——
        # 下一行为缩进 URL（可带同行标题）时补全 ref_map；若下一行不是 URL，
        # 则把暂存的原定义行按段落输出（不静默丢弃）。
        self._pending_ref_def: str | None = None
        self._pending_ref_def_text: str = ""

        # 延迟 fence（流式场景）
        self._deferred_fence: dict | None = None

        # Details 块状态
        self._details_depth: int = 0
        self._details_summary: str = ''
        self._details_open_emitted: bool = False
        # ``<details open>``：默认展开（渲染层据此选 ▼/▶ 图标）
        self._details_open: bool = False

        # 自动关闭 fence 连续匹配计数器（降低误判）
        self._auto_close_streak: int = 0
        # 自动关闭 fence 连续匹配行的结构类型集合（要求结构多样，避免
        # 代码块内连续同类型行——如多行 ``# 注释``——被误判为 Markdown）
        self._auto_close_kinds: set[str] = set()
        # 当前代码块内是否出现过「普通内容行」（非空、非块级结构、或缩进行）
        # ——出现过即放弃后续自动关闭（块内含真实代码内容，不再是「纯
        # Markdown 块漏写闭合围栏」的形态），降低误截断；每个代码块开始时重置。
        self._code_content_seen: bool = False

        # 每个 handler 独立的降级计数器，避免跨 handler 污染
        self._silent_downgrade_count: dict[str, int] = {}

        # 上一个 emit 的 TokenType（用于列表续行检测等需要前后文感知的场景）
        self._last_token_type: TokenType | None = None
        # 上一个 LIST_ITEM 的缩进量（用于续行缩进匹配）
        self._last_list_indent: int = -1
        # 上一个 LIST_ITEM 的内容起始列（= indent + marker宽度）
        self._last_list_content_col: int = -1
        # 列表项内块级内容（嵌套代码块/引用/表格/子列表）的收集缓冲：
        #   ``_list_block_active`` 表示正在收集缩进行；``_list_block_indent``
        #   为该块统一的剥离列宽；``_list_block_lines`` 为剥离后的原始行。
        self._list_block_active: bool = False
        self._list_block_indent: int = 0
        self._list_block_lines: list[str] = []

        # 标题 ID 去重字典
        self._used_heading_ids: dict[str, int] = {}

        # ── Front Matter（文档头元信息块）──
        #   _doc_started：文档是否已开始（首个非空行后置 True）——Front Matter
        #   仅在文档最开头识别；
        #   _pending_fm：待定启始定界符（首行 ``---``/``+++``/``{`` 先暂存，
        #   下一行非空才确认为 Front Matter，空行则回退为分隔线/段落）。
        self._doc_started: bool = False
        self._pending_fm: str | None = None
        self._fm_delim: str = ''
        self._fm_lines: list[str] = []

        # 上一个实际发射的 Token 类型（跨 feed 保留）——表格表注判定用。
        self._prev_emitted_type: TokenType | None = None

        # HTML 块原始行缓冲（``<table>`` 等结构化标签的整体解析用）。
        self._html_lines: list[str] = []
        # HTML 块内同名标签的当前嵌套深度（起始标签计 1，内层 ``</tag>``
        # 只减到 1，不结束块）。
        self._html_depth: int = 0

        # 子解析递归深度（details/fenced div 正文的嵌套块解析）。
        self._sub_parse_depth: int = 0

    def _reset_normal_state(self):
        """重置 NORMAL 状态变量。"""
        self._pending_lines = []
        self._table_pending_rows.clear()
        self._table_pending_bq = False
        self._grid_lines = None
        self._grid_bq_depth = 0
        self._dashed = None
        self._in_admonition = False
        self._admonition_type = ''
        self._pending_fn_def = None
        self._pending_ref_title = None
        self._pending_ref_def = None
        self._pending_ref_def_text = ""
        self._def_cont_buffer.clear()
        self._list_indents.clear()

    # ═══════════════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════════════

    @property
    def list_block_active(self) -> bool:
        """是否正在收集列表项内的块级容器（流式预览据此渲染缩进块）。"""
        return self._list_block_active

    @property
    def bq_depth(self) -> int:
        """当前引用块嵌套深度（0 = 不在引用块内；流式预览补前缀用）。"""
        if self._bq_active and self._bq_depth_stack:
            return max(0, self._bq_depth_stack[-1])
        return 0

    def feed(self, text: str) -> list[Token]:
        """输入文本片段，返回已解析的 Token。"""
        tokens: list[Token] = []
        # 追加前的缓冲长度（下次找换行的起点；feed 开始时缓冲**不含换行**——
        # 上一次 feed 的逐行循环已消费全部完整行，见循环退出条件）。
        prev_len = len(self._buffer)
        self._buffer += text

        # 预扫描参考链接和脚注（仅新增区域的完整行；见 ``_prescan_refs``）
        self._prescan_refs(prev_len)

        # 逐行处理（从 ``prev_len`` 起找换行——缓冲前段无换行，避免超长未换行
        # 活动行下每帧 O(缓冲长) 的全缓冲扫描，累计 O(n²)）
        search_from = prev_len
        while True:
            idx = self._buffer.find('\n', search_from)
            if idx == -1:
                break
            line = self._buffer[:idx + 1]
            self._buffer = self._buffer[idx + 1:]
            # 切片后新缓冲的已扫描前缀（不含已消费行）仍需找后续换行 →
            # 从 0 起（后续 find 各自只扫到下一个换行，总体 O(新增文本)）
            search_from = 0

            _pre_bq_depth = (self._bq_depth_stack[-1]
                             if (self._bq_active and self._bq_depth_stack) else 0)
            _before = len(tokens)
            try:
                if self._state != _State.NORMAL:
                    self._feed_block_line(line, tokens)
                else:
                    self._parse_normal_line(line, tokens)
            except Exception:
                # ★ 修复（跨 chunk 内容丢失）：异常行不再清空整段缓冲
                #   （``self._buffer``）——已从缓冲切出的当前行降级为段落内容，
                #   后续行继续处理，避免单行解析异常丢弃整段未处理输入。
                _logger.debug("行处理异常，降级为段落并继续", exc_info=True)
                self._state = _State.NORMAL
                try:
                    self._handle_paragraph_line(line, tokens)
                except Exception:
                    _logger.debug("异常行降级段落失败", exc_info=True)
            if tokens:
                self._prev_emitted_type = tokens[-1].type
            # 引用块内的块级元素（代码/数学/表格/列表…）统一标记引用深度，
            # 渲染层据此补 ``│`` 前缀。用**处理前**的引用深度——引用内块在
            # 触发引用关闭的行（如空行）收尾时 emit，处理后的深度已归零。
            if _pre_bq_depth > 0:
                self._mark_bq_depth(tokens, _before, _pre_bq_depth)

        # ★ 超长无换行尾部安全消化（替代原「裁剪一半 + 重置状态」）：
        #   逐行循环只消费含 \n 的完整行，循环结束后残留的是模型正在输出的
        #   未换行尾部；单行超限（> _MAX_BUFFER_SIZE）时按当前状态作为一行
        #   完整消化——不丢内容。原实现在此处裁剪缓冲一半并
        #   ``_reset_for_buffer_trim``（丢弃未处理内容 + 清空解析状态），极端
        #   超长输入下内容丢失/错乱。
        if len(self._buffer) > self._MAX_BUFFER_SIZE:
            line = self._buffer + '\n'
            self._buffer = ''
            try:
                if self._state != _State.NORMAL:
                    self._feed_block_line(line, tokens)
                else:
                    self._parse_normal_line(line, tokens)
            except Exception:
                _logger.debug("超长尾部消化异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                try:
                    self._handle_paragraph_line(line, tokens)
                except Exception:
                    _logger.debug("超长尾部降级段落失败", exc_info=True)

        # 降级计数每个 feed 重置（避免跨 chunk 累计掩盖后续真实降级）
        self._silent_downgrade_count.clear()
        return tokens

    def flush(self) -> list[Token]:
        """刷出所有剩余内容。"""
        tokens: list[Token] = []
        _flush_bq = (self._bq_depth_stack[-1]
                     if (self._bq_active and self._bq_depth_stack) else 0)

        # ── 第1步：处理残留缓冲区 ──
        remaining = self._buffer.strip()
        if remaining:
            line = self._buffer + '\n'
            self._buffer = ''
            if self._state != _State.NORMAL:
                self._feed_block_line(line, tokens)
            else:
                self._parse_normal_line(line, tokens)

        # ── 第1.1步：列表项内块级内容收集缓冲 ──
        if self._list_block_active:
            self._flush_list_block(tokens)

        # ── 第1.2步：未决的 Front Matter 起始定界符（该行单独出现且文档结束）──
        if self._pending_fm is not None:
            delim = self._pending_fm
            self._pending_fm = None
            self._doc_started = True
            self._parse_normal_line(delim + '\n', tokens)

        # ── 第1.3步：未补全的 ``[id]:``（URL 待定）按段落输出 ──
        if self._pending_ref_def_text:
            self._pending_lines.append(self._pending_ref_def_text)
            self._pending_ref_def_text = ""
            self._pending_ref_def = None

        # ── 第1.5步：处理未解析的延迟 fence ──
        if self._deferred_fence is not None:
            fence = self._deferred_fence
            self._deferred_fence = None
            self._start_code_fence(fence, tokens)

        # ── 第2步：刷出非 NORMAL 状态的块 ──
        if self._state != _State.NORMAL:
            if self._state == _State.CODE_FENCE:
                if self._block_lines:
                    for l in self._block_lines:
                        tokens.append(Token(TokenType.CODE_LINE, l))
                tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                                    {"lang": self._block_lang}))
            elif self._state == _State.MERMAID_BLOCK:
                source = ''.join(self._block_lines).strip()
                if source:
                    # ★ 修复（Mermaid 内容丢失）：同 ``_emit_mermaid_block``，
                    #   需写入 meta["source"] 供渲染引擎取用。
                    tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source,
                                        {"source": source}))
            elif self._state == _State.INDENTED_CODE:
                tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                    "lang": "text", "indented": True,
                }))
            elif self._state == _State.FENCED_DIV:
                self._emit_fenced_div_close(tokens)
            elif self._state == _State.FRONT_MATTER:
                self._flush_front_matter(tokens)
            elif self._state == _State.ADMONITION_BLOCK:
                self._emit_admonition_block_close(tokens)
            else:
                self._flush_block(tokens)
            self._state = _State.NORMAL

        # ── 第2.5步：标记引用块内的块级 token（flush 阶段不经过 feed 主循环）──
        if _flush_bq:
            self._mark_bq_depth(tokens, 0, _flush_bq)

        # ── 第3步：关闭引用块（先于段落刷出，确保引用内容以 BLOCKQUOTE_LINE 发出） ──
        self._emit_blockquote_close(tokens)

        # ── 第4步：关闭 admonition（引用风格 ``> [!TYPE]``）──
        #   ★ 顺序修复：必须**先于段落刷出**——告示块结束于「首个未引用行」，
        #     该行已进入 ``_pending_lines``；修复前先刷段落再发 ADMONITION_CLOSE
        #     （第5步），紧随告示之后（无空行分隔）的正文会排到告示**之前**
        #     （内容顺序颠倒）。段落缓冲只含告示之后的内容（告示正文经
        #     ``_feed_admonition_line`` 进 ``_block_lines``），交换安全。
        if self._in_admonition:
            self._emit_admonition_close_ref(tokens)

        # ── 第5步：刷出段落缓冲 ──
        self._flush_paragraph(tokens)

        # ── 第6步：再次刷出段落 ──
        self._flush_paragraph(tokens)

        # ── 第7步：刷出残留的流式表格缓冲 ──
        if self._table_pending_rows:
            _pending_bq = self._table_pending_bq
            _mark7 = len(tokens)
            self._emit_pending_table(tokens)
            if _pending_bq and _flush_bq:
                self._mark_bq_depth(tokens, _mark7, _flush_bq)

        # ── 第8步：刷出残留的 grid table 缓冲 ──
        if self._grid_lines is not None:
            self._emit_grid_table(tokens)

        # ── 第9步：刷出残留的 simple table 缓冲 ──
        if self._dashed is not None:
            self._emit_dashed_table(tokens)

        return tokens

    # ═══════════════════════════════════════════════════════════
    # 流式预览（未闭合块实时渲染）
    # ═══════════════════════════════════════════════════════════

    #: 预览尾部行数上限——未闭合块预览每次 write 都整块重渲染，超长块
    #: （如大代码块）限制只预览最近 N 行，避免每帧 O(n²) 重渲染。
    _PREVIEW_MAX_LINES = 200

    #: 数学块预览行数上限（LaTeX 逐行重解析成本高于纯文本行，取更小上限）。
    _PREVIEW_MATH_LINES = 60

    #: Mermaid 图预览行数上限（保留首行类型声明 + 最近行）。
    _PREVIEW_MERMAID_LINES = 120

    def _preview_tail(self, lines: list, max_lines: int | None = None) -> list:
        """取预览尾部行（超限时只保留最近行）。"""
        limit = self._PREVIEW_MAX_LINES if max_lines is None else max_lines
        if len(lines) <= limit:
            return list(lines)
        return lines[-limit:]

    def _preview_table_rows(self, rows: list[list[str]]) -> list[list[str]]:
        """表格预览行（超 ``_PREVIEW_MAX_LINES`` 时保留表头 + 最近行）。

        表格预览每次 write 都整表重渲染（列宽/框线依赖全部行），无上限时
        成本随行数线性增长、累计 O(n²)（与段落/代码块的 ``_preview_tail``
        有界化不一致）。截断后成本有界：表头（列定义）始终保留，数据行只取
        最近 ``limit-1`` 行。返回浅拷贝外层列表——行内容只读，渲染层不修改。
        """
        limit = self._PREVIEW_MAX_LINES
        if len(rows) <= limit:
            return list(rows)
        keep = max(1, limit - 1)
        return [rows[0]] + rows[-keep:]

    @staticmethod
    def _preview_pending_single_row(row: str) -> list[Token]:
        """流式表格缓冲的单行**预览** Token。

        仅表头行到达（分隔行未到）时，前导 pipe 且 ≥2 列的 ``| a | b |`` 按
        **表格形态**预览——避免「字面 pipe → 表格框线」的形态跳变（Markdown
        中此类单行绝大多数是表格的一部分）。提交路径（``_emit_pending_table``）
        保持段落语义不变：无分隔行的单行严格按 GFM 不构成表格。

        注意：若该单行最终未成为表格（如表格结束后的列数超出残留行），预览会
        从表格形态回退为段落——这是流式「信息不足」的固有取舍，仅在罕见路径
        发生。
        """
        if row.lstrip().startswith('|'):
            cells = _parse_table_row(row)
            if len(cells) >= 2:
                return [Token(TokenType.TABLE, "", {
                    "rows": [cells],
                    "alignments": ['left'] * len(cells),
                    "preview": True,
                })]
        return [Token(TokenType.PARAGRAPH, row, {"preview": True})]

    def _peek_incomplete_tail(self) -> str:
        """返回尚未解析的尾部不完整行文本（无换行残留；无内容时为空串）。

        ``feed`` 逐行消费缓冲区：只有含 ``\\n`` 的完整行会被解析，模型正在
        输出的当前行（尚未换行）残留在 ``self._buffer``。流式预览必须把它
        一并渲染——否则逐字输出在换行到来前完全不可见（整行突发上屏）。

        仅去除尾部空白（保留行首缩进，使列表/缩进代码的预览与提交后一致）。
        """
        if not self._buffer:
            return ""
        return self._buffer.rstrip()

    #: 容器未闭合预览的正文行归一化模式（与 ``feed`` 的正文行处理一一对应）
    _CONTAINER_INDENT4 = "indent4"     # fenced 告示：剥离固定 4 空格 / 制表符
    _CONTAINER_QUOTE = "quote"         # 引用风格告示：剥离 ``>`` 前缀
    _CONTAINER_DETAILS = "details"     # <details>：剥离左空白（结束符 </details>）
    _CONTAINER_DIV = "div"             # fenced div：剥离左空白（结束符 :::）

    def _normalized_container_body(self, mode: str) -> tuple[list[str], str]:
        """容器块未闭合预览的正文行（含未换行活动行）+ 容器外的残留行。

        未换行活动行必须与 ``feed`` 对完整行的处理**同一归一化**——修复前
        原样并入预览，导致：

          - fenced 告示正文行保留 4 空格缩进 → 子解析按「缩进代码块」处理，
            预览冒出 ``` 围栏（提交后消失的跳变）；
          - 引用风格告示正文行保留 ``>`` → 预览渲染成 ``│ body``；
          - <details> / fenced div 正文行保留缩进 → 预览多出缩进。

        Returns:
            ``(body_lines, rest)``。``rest`` 非空表示活动行不属于本容器
            （容器在其之前已结束），调用方应交给 NORMAL 预览渲染。
        """
        body = list(self._block_lines)
        tail = self._peek_incomplete_tail()
        if not tail:
            return body, ""
        if mode == self._CONTAINER_INDENT4:
            if tail[:4] == '    ':
                body.append(tail[4:])
            elif tail[0] == '\t':
                body.append(tail[1:])
            else:
                return body, tail  # 非缩进行 → 容器结束，属新内容
            return body, ""
        if mode == self._CONTAINER_QUOTE:
            if tail.lstrip().startswith('>'):
                body.append(_get_blockquote_text(tail.strip()))
            else:
                return body, tail
            return body, ""
        # <details> / fenced div：剥离左空白（与 feed 的正文行归一化一致），
        # 结束定界行归入容器外（由 NORMAL 预览渲染）。
        stripped_tail = _strip_left(tail)
        if mode == self._CONTAINER_DETAILS:
            if stripped_tail.startswith('</details'):
                return body, tail
        elif mode == self._CONTAINER_DIV:
            if stripped_tail.strip() == ':::':
                return body, tail
        body.append(stripped_tail)
        return body, ""

    def peek_pending(self) -> list[Token]:
        """返回当前未闭合状态的可渲染预览 Token（只读，不改解析器状态）。

        流式渲染器每次 ``write`` 后调用：把「尚未闭合」的内容（段落/代码块/
        表格/引用/折叠块/Mermaid/数学）渲染为预览行，使流式输出实时可见；
        待块真正闭合时由 ``feed``/``flush`` 产出确定 Token 替换预览。
        返回 Token 均为**自包含序列**——可脱离解析器状态单独渲染，渲染后
        渲染引擎状态复原（成对的 OPEN/CLOSE 或一次性 token）。
        """
        try:
            return self._peek_pending_inner()
        except Exception:
            _logger.debug("peek_pending 异常，跳过本帧预览", exc_info=True)
            return []

    def _peek_pending_inner(self) -> list[Token]:
        out: list[Token] = []
        st = self._state
        # 尚未换行的尾部活动行（模型正在输出的当前行）——feed 只解析完整行，
        # 不并入预览会导致「整行突发」而非逐字上屏。
        tail = self._peek_incomplete_tail()
        tail_lines = [tail] if tail else []

        # ── Front Matter（未闭合预览：元信息卡片）──
        if st == _State.FRONT_MATTER:
            # ★ 预览行数上限：Front Matter 流式期间每帧都会重建预览内容
            #   （``_front_matter_content`` 全文 join + 渲染层 YAML 解析），
            #   无上限时单帧成本 O(全文)、累计 O(n²)（20k 字符元信息块实测
            #   7.4s）。超限只保留头部（键值结构前部信息量高）并给出省略
            #   提示；提交路径不受影响（``_emit_front_matter`` 仍完整）。
            limit = self._PREVIEW_MAX_LINES
            fm = self._fm_lines
            has_tail = (bool(tail)
                        and not _is_front_matter_close(tail, self._fm_delim))
            total = len(fm) + (1 if has_tail else 0)
            dropped = 0
            if total > limit:
                dropped = total - limit
                fm_lines = list(fm[:limit])
            else:
                fm_lines = list(fm)
                if has_tail:
                    fm_lines.append(tail)
            saved = self._fm_lines
            self._fm_lines = fm_lines
            content = self._front_matter_content()
            self._fm_lines = saved
            meta: dict = {
                "format": _front_matter_format(self._fm_delim),
                "preview": True,
            }
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.FRONT_MATTER, content, meta))
            return out

        # ── 代码类块（fenced / 缩进 / Mermaid / 数学）──
        # 代码块预览行不做截断：渲染层按行增量高亮缓存（只渲染新增行），
        # 显示侧再取尾部若干行并给出省略提示——避免每帧重渲染整段。
        if st == _State.CODE_FENCE:
            out.append(self._make_code_preview_token(
                self._block_lang, self._block_attrs, self._block_title, tail))
            return out
        if st == _State.INDENTED_CODE:
            out.append(self._make_code_preview_token("text", "", "", tail))
            return out
        if st == _State.MERMAID_BLOCK:
            # 预览与提交（``_emit_mermaid_block`` 的 ``''.join(...).strip()``）
            # 一致：去除块内行尾换行/首尾空行，避免多出 ``│ `` 空边框行。
            # 超长图保留首行（图表类型声明，决定分派） + 最近行，超出部分
            # 计入 ``preview_dropped``（渲染层给出省略提示）。
            all_lines = self._block_lines + self._preview_block_tail_lines(st, tail)
            body = all_lines
            dropped = 0
            if len(all_lines) > self._PREVIEW_MERMAID_LINES:
                keep = max(1, self._PREVIEW_MERMAID_LINES - 1)
                body = [all_lines[0]] + all_lines[-keep:]
                dropped = len(all_lines) - len(body)
            src = "".join(body).strip()
            meta = {"source": src, "preview": True}
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.MERMAID_BLOCK_CLOSE, src, meta))
            return out
        if st in (_State.MATH_BLOCK, _State.DISPLAY_MATH_BLOCK):
            all_lines = self._block_lines + self._preview_block_tail_lines(st, tail)
            body = self._preview_tail(all_lines, self._PREVIEW_MATH_LINES)
            src = "\n".join(body)
            meta = {"source": src, "preview": True}
            dropped = len(all_lines) - len(body)
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.MATH_BLOCK_CLOSE, src, meta))
            return out
        if st == _State.DETAILS_BLOCK:
            body_all, rest = self._normalized_container_body(
                self._CONTAINER_DETAILS)
            while body_all and not body_all[-1].strip():
                body_all.pop()
            body = self._preview_tail(body_all)
            meta: dict = {
                "summary": self._details_summary,
                "body_lines": body,
                "preview": True,
                "open": self._details_open,
            }
            dropped = len(body_all) - len(body)
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.DETAILS_CLOSE, "", meta))
            if rest:
                out.extend(self._preview_tokens_for_normal([], rest))
            return out
        if st == _State.FENCED_DIV:
            body_all, rest = self._normalized_container_body(
                self._CONTAINER_DIV)
            while body_all and not body_all[-1].strip():
                body_all.pop()
            body = self._preview_tail(body_all)
            meta: dict = {
                "type": self._block_div_type,
                "body_lines": body,
                "preview": True,
            }
            dropped = len(body_all) - len(body)
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.FENCED_DIV_CLOSE, "", meta))
            if rest:
                out.extend(self._preview_tokens_for_normal([], rest))
            return out
        # HTML_BLOCK：已闭合行即时 emit（引擎直接逐行渲染）；未换行的尾部
        # 活动行同样即时预览，保证实时可见。
        if st == _State.HTML_BLOCK:
            if tail:
                out.append(Token(TokenType.HTML_BLOCK_LINE, tail,
                                 {"preview": True,
                                  "tag": self._block_html_tag}))
            return out

        # ── 表格活动状态 ──
        # 表格行必须整行才可解析（列数/对齐/宽度依赖完整行），未换行尾部
        # 不并入预览——避免半行数据被当成完整单元格渲染出错误框线。
        if st == _State.TABLE_ACTIVE:
            if self._table_rows and self._table_alignments:
                out.append(Token(TokenType.TABLE, "", {
                    "rows": self._preview_table_rows(self._table_rows),
                    "alignments": list(self._table_alignments),
                    "preview": True,
                }))
            return out

        # ── NORMAL 状态 ──
        # 待定 Front Matter 起始定界符（单独一行、下一行未到）：按分隔线预览。
        # 未换行的活动行（下一行内容）一并作为后续内容预览——修复前只预览
        # 分隔线，正在输入的下一行内容完全不可见（整行「突发」上屏）。
        if (self._pending_fm is not None and not self._pending_lines
                and not self._table_pending_rows):
            out.append(Token(TokenType.HR, "", {"preview": True}))
            if tail:
                out.extend(self._preview_tokens_for_normal([], tail))
            return out
        if self._deferred_fence is not None:
            fence = self._deferred_fence
            out.append(Token(TokenType.CODE_BLOCK, "\n".join(tail_lines), {
                "lang": fence.get("lang") or "text",
                "attrs": fence.get("attrs", ""),
                "title": fence.get("title", ""),
                "preview": True, "closed": False,
            }))
            return out

        if self._bq_active:
            depth = self._bq_depth_stack[-1] if self._bq_depth_stack else 1
            body = list(self._pending_lines)
            rest = ""
            if tail and _is_blockquote_line(tail.strip()):
                # 未换行的引用活动行：剥离 > 前缀后并入引用内容（修复前
                # 原样拼入 → 预览出现 ``│ > xxx``，提交后 ``>`` 消失的跳变）
                _, text = _split_blockquote(tail.strip())
                body.append(text)
            elif tail:
                # 不以 > 开头 → 引用块已结束，tail 属于其后的新内容
                rest = tail
            out.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                             {"depth": depth, "preview": True}))
            if body:
                tail_body = self._preview_tail(body)
                meta: dict = {"depth": depth, "preview": True}
                dropped = len(body) - len(tail_body)
                if dropped:
                    meta["preview_dropped"] = dropped
                out.append(Token(TokenType.BLOCKQUOTE_LINE,
                                 "\n".join(tail_body), meta))
            out.append(Token(TokenType.BLOCKQUOTE_CLOSE, "",
                             {"depth": depth, "preview": True}))
            if rest:
                out.extend(self._preview_tokens_for_normal([], rest))
            return out

        if self._in_admonition:
            all_lines, rest = self._normalized_container_body(
                self._CONTAINER_QUOTE)
            body = self._preview_tail(all_lines)
            meta: dict = {
                "type": self._admonition_type,
                "head_text": self._adm_head_text,
                "body_lines": body,
                "preview": True,
            }
            dropped = len(all_lines) - len(body)
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.ADMONITION_CLOSE, "", meta))
            if rest:
                out.extend(self._preview_tokens_for_normal([], rest))
            return out

        if st == _State.ADMONITION_BLOCK:
            # fenced 风格：head_text 恒空（标题仅在 ``!!! type Title`` 同行文本
            # 经 meta["title"] 表达）——与提交路径 ``_emit_admonition_block_close``
            # 语义一致，不再把正文首行当头部。
            all_lines, rest = self._normalized_container_body(
                self._CONTAINER_INDENT4)
            # 去尾部空行：块未闭合时尾部空行不进入预览（提交路径同样丢弃），
            # 中间空行保留（多段正文分隔）。
            while all_lines and not all_lines[-1].strip():
                all_lines.pop()
            body = self._preview_tail(all_lines)
            meta: dict = {
                "type": self._admonition_type,
                "title": self._adm_title,
                "collapsible": self._adm_collapsible,
                "head_text": "",
                "body_lines": body,
                "preview": True,
                "fenced": True,
            }
            dropped = len(all_lines) - len(body)
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.ADMONITION_CLOSE, "", meta))
            if rest:
                out.extend(self._preview_tokens_for_normal([], rest))
            return out

        # ── 流式表格行缓冲（未遇到分隔行）：同样按「整行才可解析」处理，
        # 未换行尾部不并入（半行会改变列数推断）。
        if self._table_pending_rows:
            rows_src = self._table_pending_rows
            if len(rows_src) >= 2:
                header = _parse_table_row(rows_src[0])
                num_cols = len(header)
                aligns = ['left'] * num_cols
                data_rows = [_parse_table_row(r) for r in rows_src[1:]]
                rows = [header] + [
                    (r + [''] * num_cols)[:num_cols] for r in data_rows
                ]
                out.append(Token(TokenType.TABLE, "", {
                    "rows": self._preview_table_rows(rows),
                    "alignments": aligns, "preview": True,
                }))
            else:
                out.extend(self._preview_pending_single_row(rows_src[0]))
            return out

        # ── Grid table（Pandoc ``+---+``）收集中的预览 ──
        if self._grid_lines is not None:
            lines = list(self._grid_lines)
            if tail and (_is_grid_table_border(tail)
                         or _is_grid_table_row(tail)):
                lines.append(tail)
            parsed = parse_grid_table(lines)
            if parsed is not None:
                rows, aligns = parsed
                meta: dict = {
                    "rows": self._preview_table_rows(rows),
                    "alignments": aligns, "preview": True,
                }
                if self._grid_bq_depth > 0:
                    meta["bq_depth"] = self._grid_bq_depth
                out.append(Token(TokenType.TABLE, "", meta))
            else:
                out.append(Token(TokenType.PARAGRAPH, "\n".join(lines),
                                 {"preview": True}))
            return out

        # ── Pandoc simple table 收集中的预览 ──
        if self._dashed is not None:
            header, sep, rows = self._dashed
            rows2 = list(rows)
            if tail and not _is_dashed_separator(tail):
                rows2.append(tail)
            parsed = parse_dashed_table(header, sep, rows2)
            if parsed is not None:
                data, aligns = parsed
                out.append(Token(TokenType.TABLE, "", {
                    "rows": self._preview_table_rows(data),
                    "alignments": aligns, "preview": True,
                }))
            else:
                out.append(Token(TokenType.PARAGRAPH,
                                 "\n".join(list(header) + [sep] + rows2),
                                 {"preview": True}))
            return out

        if self._pending_lines or tail_lines:
            out.extend(self._preview_tokens_for_normal(
                self._pending_lines, tail))
            return out

        return out

    # ═══════════════════════════════════════════════════════════
    # 流式预览：未换行活动行的语法分类（只读，不改解析器状态）
    # ═══════════════════════════════════════════════════════════

    def _preview_tokens_for_normal(self, lines: list[str], tail: str) -> list[Token]:
        """NORMAL 状态未提交内容（段落缓冲行 + 未换行活动行）→ 预览 Token。

        ``lines``（已累积的段落行）以 PARAGRAPH 预览；``tail``（模型正在输出
        的活动行）先尝试块级语法分类——命中则以对应 Token 预览，避免
        「以纯文本预览、提交后格式突变」（``- x``→``• x``、``# x``→标题、
        ``> x``→``│ x``、``---``→分隔线）的视觉跳变。
        """
        out: list[Token] = []
        stripped_tail = _strip_left(tail).rstrip() if tail else ""
        # Setext 下划线（=== / ---）：underline 前的**整个段落**（可跨多行）成为
        # 标题内容（CommonMark：「The setext heading underline can be preceded by
        # multiple lines of text; the whole paragraph becomes the heading.」）。
        # 修复前仅单行上文识别为标题、多行时 ``=`` 生成 PARAGRAPH + HR——与
        # 提交路径现已统一的「多行 → 标题」语义对齐，消除预览/提交跳变。
        if (tail and lines and lines[0].strip()
                and self._is_preview_setext_underline(stripped_tail)):
            level = 1 if stripped_tail[0] == '=' else 2
            heading_lines = self._preview_tail(lines)
            meta: dict = {"level": level, "preview": True}
            dropped = len(lines) - len(heading_lines)
            if dropped:
                meta["preview_dropped"] = dropped
            return [Token(TokenType.HEADING, "\n".join(heading_lines), meta)]
        if lines:
            tail_lines = self._preview_tail(lines)
            meta: dict = {"preview": True}
            dropped = len(lines) - len(tail_lines)
            if dropped:
                meta["preview_dropped"] = dropped
            out.append(Token(TokenType.PARAGRAPH, "\n".join(tail_lines), meta))
        classified = self._classify_preview_line(tail) if tail else None
        if classified:
            out.extend(classified)
        elif tail:
            if out:
                # 保留上一预览 Token 的 meta（含 preview_dropped），仅追加活动行
                prev = out[-1]
                out[-1] = Token(TokenType.PARAGRAPH,
                                prev.content + "\n" + tail, prev.meta)
            else:
                out.append(Token(TokenType.PARAGRAPH, tail,
                                 {"preview": True}))
        return out

    @staticmethod
    def _is_preview_setext_underline(stripped: str) -> bool:
        """是否为 Setext 下划线行（仅 ``=`` 或仅 ``-``，忽略空格，≥3）。"""
        if len(stripped) < 3 or stripped[0] not in ('=', '-'):
            return False
        ch = stripped[0]
        count = 0
        for c in stripped:
            if c == ch:
                count += 1
            elif c != ' ':
                return False
        return count >= 3

    def _classify_preview_line(self, line: str) -> list[Token] | None:
        """把未换行的活动行按块级语法分类为预览 Token（无副作用）。

        仅覆盖「行首标记即可确定」的语法：列表续行 / 引用 / 分隔线 / 缩进
        代码 / 代码围栏 / 标题 / 无序列表 / 有序列表。检测复用主解析路径的
        辅助函数，保证预览与提交后的格式一致；任何不确定或异常情况返回
        ``None``（回退纯文本段落预览），且**绝不修改解析器状态**。
        """
        try:
            stripped = _strip_left(line).rstrip()
            if not stripped:
                return None
            first = stripped[0]

            # 缩进代码块（4 空格 / tab 开头，且不在列表/告示上下文）——与
            # ``_parse_normal_line`` 的缩进代码检测条件一致：预览为代码块，
            # 避免「纯文本预览 → 提交后变带围栏代码块」的形态跳变。
            if (line[:4] == '    ' or (line and line[0] == '\t')) \
                    and not self._in_admonition \
                    and not self._list_indents:
                content = line[4:] if line[:4] == '    ' else line[1:]
                return [Token(TokenType.CODE_BLOCK, content.rstrip('\n'), {
                    "lang": "text", "attrs": "", "title": "",
                    "preview": True, "closed": False,
                })]

            # 列表续行（上一 Token 为 LIST_ITEM 且缩进匹配）
            if (self._last_token_type is TokenType.LIST_ITEM
                    and self._last_list_indent >= 0):
                leading = 0
                for ch in line:
                    if ch in ' \t':
                        leading += 1
                    else:
                        break
                if leading >= self._last_list_content_col:
                    return [Token(TokenType.LIST_ITEM, line.rstrip(), {
                        "continuation": True,
                        "indent": self._last_list_indent,
                        "depth": len(self._list_indents) or 1,
                        "preview": True,
                    })]

            # 引用
            if first == '>':
                depth, text = _split_blockquote(stripped)
                depth = max(1, depth)
                return [
                    Token(TokenType.BLOCKQUOTE_OPEN, "",
                          {"depth": depth, "preview": True}),
                    Token(TokenType.BLOCKQUOTE_LINE, text,
                          {"depth": depth, "preview": True}),
                    Token(TokenType.BLOCKQUOTE_CLOSE, "",
                          {"depth": depth, "preview": True}),
                ]

            # 分隔线（仅由同一字符与空格组成且 ≥3）。★ ``=`` 不是分隔线字符
            # （CommonMark HR 仅 ``-`` / ``*`` / ``_``）——单独出现的 ``=====``
            # 按段落预览（与提交路径一致）；上文多行 + ``=`` 下划线的分隔线
            # 语义由 ``_preview_tokens_for_normal`` 显式处理。
            if first in '-*_':
                n = 0
                ok = True
                for c in stripped:
                    if c == first:
                        n += 1
                    elif c != ' ':
                        ok = False
                        break
                if ok and n >= 3:
                    return [Token(TokenType.HR, "", {"preview": True})]

            # 代码围栏
            if first in ('`', '~') and _is_code_fence_line(stripped):
                info = self._try_code_fence_start(stripped)
                if info is not None:
                    return [Token(TokenType.CODE_BLOCK, "", {
                        "lang": info.get('lang') or 'text',
                        "attrs": info.get('attrs', ""),
                        "title": info.get('title', ""),
                        "preview": True, "closed": False,
                    })]

            # 标题（# 后必须跟空格）
            if first == '#':
                level = 0
                i = 0
                while i < len(stripped) and stripped[i] == '#':
                    level += 1
                    i += 1
                if 1 <= level <= 6 and i < len(stripped) and stripped[i] == ' ':
                    text = _rstrip_trailing_hashes(stripped[i + 1:].strip())
                    if text.endswith('}'):
                        brace = text.rfind('{')
                        if brace >= 0 and self._parse_heading_attrs(
                                text[brace + 1:-1].strip()) is not None:
                            text = text[:brace].strip()
                    return [Token(TokenType.HEADING, text,
                                  {"level": level, "preview": True})]

            # 无序列表
            ul = self._try_ul_item(stripped, line)
            if ul is not None:
                return [Token(TokenType.LIST_ITEM, ul['text'], {
                    "indent": ul['indent'],
                    "depth": self._preview_list_depth(ul['indent']),
                    "bullet": True,
                    "todo": ul.get('todo', False),
                    "checked": ul.get('checked', False),
                    "cancelled": ul.get('cancelled', False),
                    "preview": True,
                })]

            # 有序列表
            ol = self._try_ol_item(stripped, line)
            if ol is not None:
                return [Token(TokenType.LIST_ITEM, ol['text'], {
                    "indent": ol['indent'],
                    "depth": self._preview_list_depth(ol['indent']),
                    "bullet": False, "number": ol['number'],
                    "start": ol.get('start', ol['number']),
                    "todo": ol.get('todo', False),
                    "checked": ol.get('checked', False),
                    "cancelled": ol.get('cancelled', False),
                    "delimiter": ol.get('delimiter', '.'),
                    "preview": True,
                })]
        except Exception:
            return None
        return None

    def _preview_list_depth(self, indent: int) -> int:
        """按 ``_update_list_indent`` 的规则（不修改状态）计算列表深度。"""
        indents = self._list_indents
        if not indents or indent > indents[-1]:
            return len(indents) + 1
        if indent < indents[-1]:
            n = len(indents)
            while n > 0 and indent < indents[n - 1]:
                n -= 1
            if n == 0 or indent != indents[n - 1]:
                return n + 1
            return n
        return len(indents)

    def _preview_code_tail_lines(self, tail: str) -> list[str]:
        """代码块预览的未换行活动行（若为本块的结束围栏则不纳入）。"""
        if not tail:
            return []
        stripped = tail.strip()
        fchar, flen, _ = _get_fence_info(stripped)
        if (fchar and fchar == self._block_fence_char
                and flen >= self._block_fence_len):
            return []
        return [tail]

    def _make_code_preview_token(self, lang: str, attrs: str, title: str,
                                 tail: str) -> Token:
        """构造代码块预览 Token——携带**行列表**（``meta["lines"]``）。

        修复前以 ``"\\n".join(...)`` 传整段字符串，渲染层再 ``split("\\n")``
        还原行——每帧对（可能数千行的）内容做一次 O(全文) 的 join + split 往返。
        改为直接传行列表（尾部活动行为空时零拷贝复用内部缓冲），渲染层按行
        增量高亮，省去往返。
        """
        lines = self._preview_code_lines
        tail_lines = self._preview_code_tail_lines(tail)
        if tail_lines:
            lines = lines + tail_lines
        return Token(TokenType.CODE_BLOCK, "", {
            "lang": lang, "attrs": attrs, "title": title,
            "preview": True, "closed": False,
            "preview_dropped": self._preview_code_dropped,
            "lines": lines,
            "highlight_lines": parse_highlight_lines(attrs),
            "linenos": parse_linenos(attrs),
            "lineno_start": parse_lineno_options(attrs)[1],
            "lineno_step": parse_lineno_options(attrs)[2],
        })

    def _preview_block_tail_lines(self, st, tail: str) -> list[str]:
        """Mermaid/数学块预览的未换行活动行（结束定界符不纳入内容）。"""
        if not tail:
            return []
        stripped = tail.strip()
        if st == _State.MERMAID_BLOCK and _is_code_fence_line(stripped):
            return []
        if st == _State.MATH_BLOCK and stripped == '$$':
            return []
        if st == _State.DISPLAY_MATH_BLOCK and stripped == r'\]':
            return []
        return [tail]

    # ═══════════════════════════════════════════════════════════
    # 预扫描
    # ═══════════════════════════════════════════════════════════

    def _prescan_refs(self, start: int = 0):
        """预扫描参考链接 [id]: url 和脚注定义 [^id]: content。

        ``start``：本次 feed 新增文本在缓冲中的起始下标。feed 开始时缓冲
        **不含换行**（上一次 feed 已消费全部完整行），故首个换行只可能出现在
        ``start`` 之后——从 ``start`` 起找首个换行（而非从 0 起全缓冲扫描），
        避免超长未换行活动行下每帧 O(缓冲长) 的扫描累计 O(n²)。首行仍取
        **完整行**（``buf[:nl+1]``，含此前累积的部分行内容），后续行正常推进。
        """
        try:
            buf = self._buffer
            if start >= len(buf):
                return
            n = len(buf)
            pos = start
            first = True
            while pos < n:
                nl = buf.find('\n', pos)
                if nl == -1:
                    break
                line = buf[:nl + 1] if (first and start) else buf[pos:nl + 1]
                first = False
                pos = nl + 1
                stripped = _strip_left(line)
                if not stripped:
                    continue
                # 注释行（``[//]: # (comment)``）不是引用链接定义——与
                # ``_parse_normal_line`` 的注释跳过逻辑保持一致（修复前
                # 预扫描把它收录进 ``ctx.ref_map``，Rich 路径 close() 渲染出
                # 伪造的「引用链接」条目 ``[//] #``）。
                if stripped.startswith('[//]:'):
                    continue
                if stripped[0] == '[':
                    colon_pos = stripped.find(']:')
                    if colon_pos > 0:
                        ref_id = stripped[1:colon_pos]
                        if '^' not in ref_id:
                            rest = stripped[colon_pos + 2:].strip()
                            url_end = self._find_url_end(rest)
                            url = rest[:url_end]
                            title = ''
                            after_url = rest[url_end:].strip()
                            if after_url and after_url[0] in '"\'':
                                quote = after_url[0]
                                end = after_url.find(quote, 1)
                                if end > 0:
                                    title = after_url[1:end]
                            if url and ref_id:
                                self._ctx.ref_map[ref_id] = (url, title)
                        else:
                            ref_id = ref_id[1:]
                            content = stripped[colon_pos + 2:].strip()
                            if ref_id and content:
                                # ★ 只登记内容，**不**在预扫描阶段预入 fn_order——
                                #   编号顺序的唯一真源是「引用出现顺序」（与流式增量
                                #   渲染一致）。预扫描会在解析前就看到文档后部的定义，
                                #   使一次性渲染 / 历史回放的编号退化为「定义顺序」，
                                #   与流式生成时（引用先出现）的编号互换（同一文档
                                #   两种渲染路径不一致）。未引用定义由
                                #   ``_render_footnotes`` 末尾按字母序补列。
                                self._ctx.fn_map[ref_id] = content
        except Exception:
            _logger.debug("_prescan_refs预扫描异常", exc_info=True)

    @staticmethod
    def _find_url_end(text: str) -> int:
        """找到 URL 的结束位置（遇到空白或结尾）。"""
        i = 0
        in_parentheses = 0
        while i < len(text):
            ch = text[i]
            if ch == '%' and i + 2 < len(text):
                i += 3
                continue
            if ch in ' \t':
                if in_parentheses == 0:
                    break
            elif ch == '(':
                in_parentheses += 1
            elif ch == ')':
                in_parentheses -= 1
                if in_parentheses < 0:
                    break
            i += 1
        return i

    # ═══════════════════════════════════════════════════════════
    # 行处理分发
    # ═══════════════════════════════════════════════════════════

    def _parse_normal_line(self, line: str, tokens: list[Token]):
        """NORMAL 状态：按首字符调度表分派语法检测。"""
        stripped = _strip_left(line).rstrip('\n')

        # ── 收集中的 Pandoc grid / simple table 续行 ──
        # 收集期间的行只做「并入 / 结束」判定（缩进语义不参与，数据行可含
        # 前导空格）；起始判定在列表项内的块级容器收集之后（见下）。
        # ★ 引用行（``>`` 前缀）不在此判定：外层行带前缀会误判「不是表格
        #   续行」而提前提交；引用内的表格由 ``_parse_blockquote`` 剥离前缀
        #   后递归进入本方法处理。
        _bq_line = _is_blockquote_line(stripped)
        if self._dashed is not None and not _bq_line:
            if stripped and not _is_dashed_separator(stripped):
                # 收集**原始行**（保留前导空格）——simple table 的列位置由字符
                # 偏移决定，去左空白会破坏列切分
                self._dashed[2].append(line.rstrip('\n'))
                return
            self._emit_dashed_table(tokens)
        if self._grid_lines is not None and not _bq_line:
            if _is_grid_table_border(stripped) or _is_grid_table_row(stripped):
                self._grid_lines.append(stripped)
                return
            self._emit_grid_table(tokens)

        # ── Front Matter（文档头元信息块）──
        # 仅文档最开头（尚无任何内容行）且非引用块递归内识别；首行定界符
        # 先暂存，下一行非空才确认（空行时回退为分隔线/段落，避免把文档
        # 开头的 ``---`` 分隔线误判为 Front Matter）。
        if (not self._doc_started and self._bq_in_recursion == 0
                and self._deferred_fence is None):
            if self._try_front_matter_line(line, stripped, tokens):
                return

        # ── 延迟 fence 处理 ──
        if self._deferred_fence is not None:
            self._handle_deferred_fence(stripped, tokens)
            return

        # Empty
        if not stripped or _is_empty_line(stripped):
            self._handle_empty_line(tokens)
            return

        # ── 块引用懒续行（CommonMark lazy continuation）──
        # 引用块内未完成段落的续行可省略 ``>`` 前缀：``> foo\nbar`` 中 ``bar``
        # 仍属引用内容（渲染 ``│ foo`` / ``│ bar``）。修复前该行触发引用块关闭，
        # ``bar`` 以无前缀的顶层段落上屏，引用被腰斩。
        # 仅当引用块内已有未完成段落、且本行不是新块起始（列表/标题/围栏/
        # HTML 块…）时视为续行——否则按既有逻辑关闭引用块（保守，避免把真正
        # 的新块吞进引用）。
        if (self._bq_active and self._bq_in_recursion == 0
                and self._pending_lines
                and not _is_blockquote_line(stripped)
                and not self._starts_new_block(stripped)):
            self._handle_paragraph_line(line, tokens)
            return

        # ── 注释行：[//]: # (comment) 或 [//]: # comment ──
        if stripped.startswith('[//]:'):
            return

        # ── 参考式链接定义的多行标题（前一行为 ``[id]: url``，本行为缩进
        #    标题）——补全 ref_map 并消费本行（不作为正文输出）。 ──
        if self._pending_ref_title is not None:
            ref_id = self._pending_ref_title
            self._pending_ref_title = None
            title = self._try_ref_title_line(line)
            if title is not None:
                pair = self._ctx.ref_map.get(ref_id)
                if pair is not None:
                    self._ctx.ref_map[ref_id] = (pair[0], title)
                return

        # ── 参考式链接定义的「URL 在下一行」（前一行为 ``[id]:``，本行为
        #    缩进 URL，可带同行标题）——补全 ref_map 并消费本行。 ──
        if self._pending_ref_def is not None:
            ref_id = self._pending_ref_def
            pending_text = self._pending_ref_def_text
            self._pending_ref_def = None
            self._pending_ref_def_text = ""
            parsed = self._try_ref_def_url_line(line)
            if parsed is not None:
                url, title = parsed
                self._ctx.ref_map[ref_id] = (url, title)
                return
            # 下一行不是 URL → 暂存的原定义行按段落输出（不静默丢弃），
            # 本行继续常规处理。
            if pending_text:
                self._pending_lines.append(pending_text)

        # ── 列表项内的块级容器（缩进 ≥ 列表内容列 → 收集/子解析）──
        if self._handle_list_block_line(line, stripped, tokens):
            return

        # ── Pandoc simple / grid table 起始（列表项外；列表项内的缩进块已由
        #    上方 ``_handle_list_block_line`` 收集后子解析，保留缩进语义）──
        if _is_dashed_separator(stripped) and self._pending_lines:
            header = list(self._pending_lines)
            self._pending_lines = []
            self._dashed = (header, stripped, [])
            return
        if _is_grid_table_border(stripped):
            self._flush_paragraph(tokens)
            self._emit_blockquote_close(tokens)
            if self._table_pending_rows:
                self._emit_pending_table(tokens)
            self._grid_lines = [stripped]
            self._grid_bq_depth = (self._bq_depth_stack[-1]
                                   if self._bq_depth_stack else 0)
            return

        first = stripped[0] if stripped else ''

        # ── 脚注定义续行（缩进 4 空格 / Tab；可跨空行分段）──
        # 必须在字母快速通道与缩进代码检测之前——否则缩进续段会被当作
        # 缩进代码块（``[^1]: 首段`` 后的 ``    次段`` 渲染成代码块）或被
        # 段落通道吞掉。空行保留待定状态（GFM 脚注支持多段落），遇到非
        # 缩进行即清除。
        if self._pending_fn_def is not None and self._pending_fn_def != '__def__':
            if self._is_fn_continuation_line(line):
                cont = _rstrip_line(line).strip()
                if cont and self._pending_fn_def in self._ctx.fn_map:
                    sep = '\n\n' if self._pending_fn_blank else ' '
                    self._ctx.fn_map[self._pending_fn_def] += sep + cont
                    self._pending_fn_blank = False
                    return
                self._pending_fn_def = None
            else:
                self._pending_fn_def = None
                self._pending_fn_blank = False

        # ── 定义续行检查（必须在字母快速通道之前，防止续行被段落吞噬） ──
        if self._pending_fn_def == '__def__':
            if line.startswith('    ') or line.startswith('\t'):
                cont = _rstrip_line(line).strip()
                if cont:
                    self._def_cont_buffer.append(cont)
                    return
            elif stripped.startswith(':'):
                pass
            else:
                self._pending_fn_def = None

        # ── 缩进代码块（CommonMark：4 空格 / Tab 缩进）──
        # 必须在字母快速通道之前检查——否则以字母/数字开头的缩进行会被
        # 段落通道吞掉，缩进代码块语法完全失效。按 CommonMark 语义：不中断
        # 进行中的段落、不在列表/告示上下文内、不抢占脚注/定义续行。
        if ((line[:4] == '    ' or (line and line[0] == '\t'))
                and not self._in_admonition
                and not self._pending_lines
                and not self._table_pending_rows
                and self._pending_fn_def is None
                and not self._list_indents):
            self._flush_paragraph(tokens)
            self._emit_blockquote_close(tokens)
            self._start_indented_code(line, tokens)
            return

        # ── GFM 无前导 pipe 表格行检测（在字母快速通道前拦截） ──
        # 如 "Name|Age" 或 "a|b|c" 样式的行，避免被段落吞噬
        # ★ 排除 blockquote 行（> 前缀）和递归引用块内部
        # ★ 排除列表项：- item | detail 之类不应误判为表格
        first_non_space = stripped.lstrip()
        list_prefix = False
        if first_non_space:
            ch0 = first_non_space[0]
            if ch0 in ('-', '*', '+') and len(first_non_space) > 1 and first_non_space[1] == ' ':
                list_prefix = True
            elif ch0.isdigit() and '. ' in first_non_space[:4]:
                list_prefix = True
        if (self._bq_in_recursion == 0
                and not self._table_pending_rows
                and not list_prefix
                and '|' in stripped
                and not stripped.startswith('|')
                and not _is_blockquote_line(stripped)):
            check = stripped.replace('\\|', '')
            parts = [p.strip() for p in check.split('|') if p.strip()]
            # 至少 2 个非空部分（单 pipe 分隔）且非分隔行
            if len(parts) >= 2 and not _is_table_separator(stripped):
                self._table_pending_rows.append(stripped)
                return

        # ── 字母快速通道 ──
        if self._may_be_paragraph_text(first, stripped):
            if self._bq_active and self._bq_in_recursion == 0:
                self._emit_blockquote_close(tokens)
            if self._table_pending_rows:
                self._emit_pending_table(tokens)

            # ── 列表续行检测：上一 Token 是 LIST_ITEM 且当前行缩进匹配 →
            #   产出续行 Token（渲染为对齐列表内容的缩进行）。
            #   ★ 修复（跨 chunk 状态丢失）：不再要求「本次 feed 的 tokens
            #   末项为 LIST_ITEM」——跨 feed 时 tokens 为空，原条件失效导致
            #   续行降级为普通段落；改用实例级 ``_last_token_type``（跨 feed
            #   保留），且不再把续行拼进上一 Token（拼入的多行内容含 \n，
            #   渲染为字面换行破坏单行模型），统一产出独立续行 Token。
            if (self._last_token_type is TokenType.LIST_ITEM
                    and self._last_list_indent >= 0):
                leading = 0
                for ch in line:
                    if ch in ' \t':
                        leading += 1
                    else:
                        break
                if leading >= self._last_list_content_col:
                    tokens.append(Token(TokenType.LIST_ITEM, line.rstrip('\n'), {
                        "continuation": True,
                        "indent": self._last_list_indent,
                        "depth": len(self._list_indents) or 1,
                    }))
                    return
                # ★ 懒续行（CommonMark lazy continuation）：紧凑列表项的段落续行
                #   可省略（或不足）缩进——``- foo\nbar`` 中 ``bar`` 仍属列表项
                #   内容（渲染 ``• foo`` / ``  bar``）。仅当上一行确实是列表项行
                #   （``_prev_emitted_type``，空行/其它块会打断）且本行不是块起始
                #   时成立，避免把列表后的普通段落误吞进列表项。
                if (leading < self._last_list_content_col
                        and self._prev_emitted_type is TokenType.LIST_ITEM
                        and not self._starts_new_block(stripped)):
                    tokens.append(Token(TokenType.LIST_ITEM, line.rstrip('\n'), {
                        "continuation": True,
                        "indent": self._last_list_indent,
                        "depth": len(self._list_indents) or 1,
                        "lazy": True,
                    }))
                    return

            self._handle_paragraph_line(line, tokens)
            return

        # 关闭引用块
        if self._bq_active and self._bq_in_recursion == 0:
            if first != '>' or not _is_blockquote_line(stripped):
                self._emit_blockquote_close(tokens)

        # ── 非首字符可检测的逻辑 ──
        # （定义续行已移至快速通道前）

        # 脚注续行
        if self._pending_fn_def is not None and self._pending_fn_def != '__def__':
            if line.startswith('    ') or line.startswith('\t') or line.startswith('  '):
                cont = _rstrip_line(line).strip()
                if cont and self._pending_fn_def in self._ctx.fn_map:
                    self._ctx.fn_map[self._pending_fn_def] += ' ' + cont
                    return
                else:
                    self._pending_fn_def = None
            else:
                self._pending_fn_def = None

        # 表格活动状态（与 ``_feed_block_line`` 同规则：已建立表格后用宽松
        # 数据行判定，兼容无前导 pipe 的数据行）
        if self._state == _State.TABLE_ACTIVE:
            header_cols = len(self._table_rows[0]) if self._table_rows else None
            if _is_table_data_row(stripped, header_cols):
                self._table_rows.append(_normalize_table_cells(
                    _parse_table_row(stripped), header_cols))
                return
            self._emit_table(tokens)

        # 流式表格行缓冲
        if self._table_pending_rows:
            if _is_blockquote_line(stripped):
                if not self._table_pending_bq:
                    # 缓冲来自引用块外、当前行是引用行 → 先刷出缓冲
                    self._emit_pending_table(tokens)
                # 引用内表格（_table_pending_bq）：交由引用递归剥离前缀后
                # 继续判断（本分支不处理，避免 ````> | a | b |```` 被当作行）
            elif _is_table_separator(stripped):
                self._start_table(stripped, tokens)
                return
            elif _is_table_row(stripped):
                if len(self._table_pending_rows) >= 100:
                    self._emit_pending_table(tokens)
                self._table_pending_rows.append(stripped)
                return
            else:
                self._emit_pending_table(tokens)

        # Admonition 续行（或新的 [!TYPE] 切换）
        if self._in_admonition and first == '>':
            text = _get_blockquote_text(stripped)
            # 检测是否切换为新的 [!TYPE] 告示
            if text.startswith('[') and '!' in text[:8]:
                close_bracket = text.find(']')
                # 宽松检测：`]` 后任意字符都接受
                if close_bracket > 2:
                    new_type = text[2:close_bracket].upper()
                    if new_type in ('NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION', 'CITE',
                                    'INFO', 'SUCCESS', 'QUESTION', 'BUG', 'DANGER'):
                        # 关闭当前告示，打开新的
                        self._emit_admonition_close_ref(tokens)
                        self._admonition_type = new_type
                        adm_text = text[close_bracket + 1:].strip()
                        self._adm_head_text = adm_text
                        self._block_lines = []  # 正文行（head 另存）
                        tokens.append(Token(TokenType.ADMONITION_OPEN, adm_text,
                                            {"type": new_type, "depth": 1}))
                        return
            self._block_lines.append(text)  # 供流式预览
            tokens.append(Token(TokenType.ADMONITION_LINE, text,
                                {"depth": 1, "type": self._admonition_type}))
            return

        # ── 表格表注（表格下方紧跟的说明行）──
        if self._prev_emitted_type is TokenType.TABLE:
            caption = self._try_table_caption(stripped)
            if caption is not None:
                tokens.append(Token(TokenType.TABLE_CAPTION, caption))
                return

        # ═══════════════════════════════════════════════════════════
        # 首字符调度表
        # ═══════════════════════════════════════════════════════════
        if self._dispatch_normal_line(first, stripped, line, tokens):
            return

        # 段落（fallback）
        self._handle_paragraph_line(line, tokens)

    # ── Front Matter（文档头元信息块） ───────────────────

    def _try_front_matter_line(self, line: str, stripped: str,
                               tokens: list[Token]) -> bool:
        """文档开头 Front Matter 起始/确认逻辑（返回 True 表示行已处理）。

        - 已暂存待定定界符：本行非空 → 确认 Front Matter 并收集；本行空 →
          回退：定界符按普通 Markdown（分隔线）解析，空行照常发射。
        - 本行是定界符且尚未开始文档 → 暂存待定，不产出（等下一行确认）。
        """
        if self._pending_fm is not None:
            delim = self._pending_fm
            self._pending_fm = None
            if not stripped:
                self._doc_started = True
                self._parse_normal_line(delim + '\n', tokens)
                self._handle_empty_line(tokens)
                return True
            self._start_front_matter(delim)
            self._feed_front_matter_line(line, stripped, tokens)
            return True
        delim = _front_matter_delim(stripped)
        if delim is not None:
            self._pending_fm = delim
            return True
        if stripped:
            self._doc_started = True
        return False

    def _start_front_matter(self, delim: str) -> None:
        self._state = _State.FRONT_MATTER
        self._fm_delim = delim
        self._fm_lines = []

    def _feed_front_matter_line(self, line: str, stripped: str,
                                tokens: list[Token]) -> None:
        if _is_front_matter_close(stripped, self._fm_delim):
            self._emit_front_matter(tokens)
        else:
            self._fm_lines.append(line.rstrip('\n'))

    def _emit_front_matter(self, tokens: list[Token]) -> None:
        content = self._front_matter_content()
        tokens.append(Token(TokenType.FRONT_MATTER, content,
                            {"format": _front_matter_format(self._fm_delim)}))
        self._state = _State.NORMAL
        self._doc_started = True
        self._fm_lines = []
        self._fm_delim = ''

    def _front_matter_content(self) -> str:
        """Front Matter 内容文本（JSON 补回 ``{``/``}`` 定界符以构成合法 JSON）。"""
        body = '\n'.join(self._fm_lines)
        if self._fm_delim == '{':
            return '{\n' + body + '\n}' if body else '{}'
        return body

    def _flush_front_matter(self, tokens: list[Token]) -> None:
        """未闭合的 Front Matter → 回退为普通 Markdown 重新解析（不丢内容）。"""
        delim = self._fm_delim or '---'
        lines = [delim] + list(self._fm_lines)
        self._state = _State.NORMAL
        self._doc_started = True
        self._fm_lines = []
        self._fm_delim = ''
        for ln in lines:
            self._parse_normal_line(ln + '\n', tokens)

    # ── 子块递归解析（details / fenced div 正文） ────────

    _MAX_SUB_PARSE_DEPTH = 8

    def _parse_sub_blocks(self, lines: list[str]) -> list[Token]:
        """把块内原始行递归解析为子 Token（块级语法完整支持）。

        供 ``<details>`` 等容器块的正文使用：正文行以普通 Markdown 语义
        （列表 / 代码块 / 引用 / 强调 …）解析，而非仅逐行行内渲染。
        共享同一 ``RenderContext``（脚注 / 参考式链接 / 缩写跨块生效）。
        """
        if not lines or self._sub_parse_depth >= self._MAX_SUB_PARSE_DEPTH:
            return []
        text = "\n".join(lines)
        if not text.strip():
            return []
        sub = RegexFreeBlockParser(ctx=self._ctx)
        sub._sub_parse_depth = self._sub_parse_depth + 1
        out = sub.feed(text + "\n")
        out.extend(sub.flush())
        return out

    # ── 表格表注 ────────────────────────────────────────

    @staticmethod
    def _try_table_caption(stripped: str) -> str | None:
        """识别表格表注行（``: 说明`` / ``Table: 说明`` / ``表: 说明``）。"""
        if not stripped:
            return None
        if stripped[0] == ':' and len(stripped) > 1 and stripped[1] in ' \t':
            text = stripped[1:].strip()
            return text or None
        lower = stripped.lower()
        for prefix in ('table caption:', 'table:', 'caption:', '表注:', '表:'):
            if lower.startswith(prefix):
                text = stripped[len(prefix):].strip()
                return text or None
        return None

    def _dispatch_normal_line(self, first: str, stripped: str, line: str,
                               tokens: list[Token]) -> bool:
        """按首字符查表分派语法检测。返回 True 表示行已处理，False 降级为段落。"""

        def _handle_heading() -> bool:
            try:
                heading = self._try_heading(stripped)
                if heading is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._list_indents.clear()
                    tokens.append(heading)
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('heading', 0) + 1
                self._silent_downgrade_count['heading'] = count
                _logger.warning("Heading解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_fence() -> bool:
            try:
                fence = self._try_code_fence_start(stripped)
                if fence is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    if fence['deferred']:
                        self._deferred_fence = fence
                        return True
                    self._start_code_fence(fence, tokens)
                    if fence.get('extra'):
                        if self._state == _State.MERMAID_BLOCK:
                            tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                        else:
                            tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('fence', 0) + 1
                self._silent_downgrade_count['fence'] = count
                _logger.warning("Code fence解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_html() -> bool:
            lower_check = stripped.lower().lstrip()
            if lower_check.startswith('</details') or lower_check.startswith('</summary'):
                return False
            if self._try_details_open(stripped):
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                self._start_details(stripped, tokens)
                return True
            tag = self._try_block_html(stripped)
            if tag is not None:
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                if tag == 'hr':
                    # HTML 水平分隔线 → 分隔线 Token（修复前渲染为 ``▸ <hr>``
                    # 标签行 + 空行，与 Markdown ``---`` 呈现不一致）。
                    tokens.append(Token(TokenType.HR))
                    return True
                self._start_html_block(tag, tokens, stripped)
                return True
            return False

        def _handle_bracket() -> bool:
            if ']:' in stripped:
                try:
                    fn = self._try_fn_def(stripped)
                    if fn is not None:
                        self._flush_paragraph(tokens)
                        self._handle_fn_def(fn, tokens)
                        return True
                    if self._try_ref_link(stripped):
                        return True
                except Exception:
                    count = self._silent_downgrade_count.get('bracket', 0) + 1
                    self._silent_downgrade_count['bracket'] = count
                    _logger.warning("脚注/参考链接解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            if stripped == '[TOC]' or stripped.rstrip() == '[TOC]':
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                tokens.append(Token(TokenType.TOC_MARKER, ""))
                return True
            return False

        def _handle_fenced_div() -> bool:
            if len(stripped) >= 3 and stripped[:3] == ':::':
                try:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._handle_fenced_div_open(stripped, tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('fenced_div', 0) + 1
                    self._silent_downgrade_count['fenced_div'] = count
                    _logger.warning("Fenced div解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_definition() -> bool:
            try:
                def_item = self._try_definition(stripped)
                if def_item is not None:
                    term = ""
                    if self._pending_lines:
                        last = self._pending_lines[-1]
                        if isinstance(last, str) and last.strip():
                            term = last.strip()
                            self._pending_lines = self._pending_lines[:-1]
                    # ★ 将续行内容合并到前一个 DEFINITION_ITEM 中（不是当前这个）
                    if self._def_cont_buffer:
                        cont_text = '\n'.join(self._def_cont_buffer)
                        if tokens and tokens[-1].type is TokenType.DEFINITION_ITEM:
                            old_content = tokens[-1].content
                            tokens[-1] = Token(TokenType.DEFINITION_ITEM,
                                               old_content + '\n' + cont_text,
                                               tokens[-1].meta)
                        else:
                            # 跨 feed 无前一个 DEFINITION_ITEM，暂存到 pending_lines
                            self._pending_lines.append(cont_text)
                        self._def_cont_buffer.clear()
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    tokens.append(Token(TokenType.DEFINITION_ITEM, def_item,
                                        {"term": term, "indent": 0}))
                    self._pending_fn_def = '__def__'
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('definition', 0) + 1
                self._silent_downgrade_count['definition'] = count
                _logger.warning("定义列表解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_colon() -> bool:
            """处理 : 开头的行：先试 fenced div (:::)，再试定义列表 (: text)。"""
            if _handle_fenced_div():
                return True
            return _handle_definition()

        def _handle_setext_or_hr() -> bool:
            try:
                if self._try_setext_or_hr(stripped, first, tokens):
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('setext_or_hr', 0) + 1
                self._silent_downgrade_count['setext_or_hr'] = count
                _logger.warning("Setext/HR解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            if first in ('-', '*'):
                try:
                    ul = self._try_ul_item(stripped, line)
                    if ul is not None:
                        self._flush_paragraph(tokens)
                        self._emit_blockquote_close(tokens)
                        self._update_list_indent(ul['indent'])
                        tokens.append(Token(TokenType.LIST_ITEM, ul['text'], {
                            "indent": ul['indent'], "depth": len(self._list_indents),
                            "bullet": True,
                            "todo": ul.get('todo', False),
                            "checked": ul.get('checked', False),
                            "cancelled": ul.get('cancelled', False),
                        }))
                        self._last_token_type = TokenType.LIST_ITEM
                        self._last_list_indent = ul['indent']
                        self._last_list_content_col = ul['indent'] + 2
                        return True
                except Exception:
                    count = self._silent_downgrade_count.get('ul_item_in_setext', 0) + 1
                    self._silent_downgrade_count['ul_item_in_setext'] = count
                    _logger.warning("无序列表解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_star() -> bool:
            if self._handle_abbreviation(stripped):
                return True
            return _handle_setext_or_hr()

        def _handle_display_math() -> bool:
            env_info = _match_display_env(stripped)
            if env_info is not None:
                try:
                    env, end_marker = env_info
                    pos = stripped.find(end_marker)
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    if pos >= 0:
                        # 单行闭合环境（``\begin{align}…\end{align}`` 同行）
                        source = stripped[:pos + len(end_marker)]
                        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                                            {"source": source}))
                    else:
                        self._start_math_env(env, stripped, tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('display_env', 0) + 1
                    self._silent_downgrade_count['display_env'] = count
                    _logger.warning("显示环境数学块解析异常，降级为段落", exc_info=True)
                    self._state = _State.NORMAL
                    self._math_env_end = None
                    if count > 5:
                        raise
            if len(stripped) == 2 and stripped == r'\[':
                try:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._start_display_math(tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('display_math', 0) + 1
                    self._silent_downgrade_count['display_math'] = count
                    _logger.warning("显示数学块解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            # ★ 单行显示数学 ``\[ ... \]``：同一行给出开闭定界符 → 直接产出闭合
            #   块（源为中间内容）。修复前只有「独占一行的 ``\[``」才开启显示
            #   数学块，单行形态落入段落 → 定界符被转义成字面 ``[ ... ]``
            #   （``\[ x^2 \]`` → ``[ x² ]``），与多行 ``\[`` 块（数学框渲染）
            #   不一致、也与单行 ``$$ ... $$`` 的处理不对称。
            if (len(stripped) > 4 and stripped.startswith(r'\[')
                    and stripped.endswith(r'\]')):
                source = stripped[2:-2].strip()
                if source:
                    try:
                        self._flush_paragraph(tokens)
                        self._emit_blockquote_close(tokens)
                        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                                            {"source": source}))
                        return True
                    except Exception:
                        count = self._silent_downgrade_count.get('display_math', 0) + 1
                        self._silent_downgrade_count['display_math'] = count
                        _logger.warning("单行显示数学块解析异常，降级为段落",
                                        exc_info=True)
                        if count > 5:
                            raise
            return False

        def _handle_math_block() -> bool:
            if len(stripped) >= 2 and stripped[:2] == '$$' and _is_only_chars(stripped, '$'):
                try:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._start_math_block(tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('math_block', 0) + 1
                    self._silent_downgrade_count['math_block'] = count
                    _logger.warning("数学块解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            # 单行显示数学 ``$$ ... $$``：同一行给出开闭定界符 → 直接产出闭合块
            # （源为中间内容）。修复前落入段落 → 行内 ``$`` 解析把首尾定界符当
            # 行内数学，多出字面 ``$``（``$$x=1$$`` → ``$x=1$``），与多行
            # ``$$`` 块的渲染（数学框）不一致。
            if (len(stripped) > 4 and stripped[:2] == '$$'
                    and stripped.endswith('$$')):
                source = stripped[2:-2].strip()
                if source:
                    try:
                        self._flush_paragraph(tokens)
                        self._emit_blockquote_close(tokens)
                        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                                            {"source": source}))
                        return True
                    except Exception:
                        count = self._silent_downgrade_count.get('math_block', 0) + 1
                        self._silent_downgrade_count['math_block'] = count
                        _logger.warning("单行数学块解析异常，降级为段落", exc_info=True)
                        if count > 5:
                            raise
            return False

        def _handle_table() -> bool:
            # ★ 引用块内同样支持表格（引用内块级元素语义）；无分隔行的多行
            #   含 pipe 文本由 ``_emit_pending_table`` 的严格模式降级为段落
            #   （见 ``_table_pending_bq``）。
            try:
                if _is_table_row(stripped):
                    if self._bq_in_recursion > 0:
                        self._table_pending_bq = True
                    if len(self._table_pending_rows) >= 100:
                        self._emit_pending_table(tokens)
                    self._table_pending_rows.append(stripped)
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('table', 0) + 1
                self._silent_downgrade_count['table'] = count
                _logger.warning("表格行解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_blockquote() -> bool:
            if _is_blockquote_line(stripped):
                try:
                    # ★ 修复嵌套引用: 在进入 blockquote 前，若不在引用内，
                    # 刷新外部段落缓冲为 PARAGRAPH。
                    # 若已在引用内，延迟到 _parse_blockquote 中按深度变更刷新。
                    if self._pending_lines and not self._bq_active:
                        self._flush_paragraph(tokens)
                    self._parse_blockquote(stripped, tokens)
                    return True
                except Exception:
                    count = self._silent_downgrade_count.get('blockquote', 0) + 1
                    self._silent_downgrade_count['blockquote'] = count
                    _logger.warning("引用块解析异常，降级为段落", exc_info=True)
                    if count > 5:
                        raise
            return False

        def _handle_plus() -> bool:
            try:
                ul = self._try_ul_item(stripped, line)
                if ul is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._update_list_indent(ul['indent'])
                    tokens.append(Token(TokenType.LIST_ITEM, ul['text'], {
                        "indent": ul['indent'], "depth": len(self._list_indents),
                        "bullet": True,
                        "todo": ul.get('todo', False),
                        "checked": ul.get('checked', False),
                        "cancelled": ul.get('cancelled', False),
                    }))
                    self._last_token_type = TokenType.LIST_ITEM
                    self._last_list_indent = ul['indent']
                    self._last_list_content_col = ul['indent'] + 2  # "- " marker
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('plus', 0) + 1
                self._silent_downgrade_count['plus'] = count
                _logger.warning("无序列表解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_ordered_list() -> bool:
            try:
                ol = self._try_ol_item(stripped, line)
                if ol is not None:
                    self._flush_paragraph(tokens)
                    self._emit_blockquote_close(tokens)
                    self._update_list_indent(ol['indent'])
                    # marker_width 由 _try_ol_item 计算（"1. " 或 "1) "）
                    marker_width = ol.get('marker_width', len(str(ol['number'])) + 2)
                    tokens.append(Token(TokenType.LIST_ITEM, ol['text'], {
                        "indent": ol['indent'], "depth": len(self._list_indents),
                        "bullet": False, "number": ol['number'],
                        "start": ol.get('start', ol['number']),
                        "todo": ol.get('todo', False),
                        "checked": ol.get('checked', False),
                        "cancelled": ol.get('cancelled', False),
                        "delimiter": ol.get('delimiter', '.'),
                    }))
                    self._last_token_type = TokenType.LIST_ITEM
                    self._last_list_indent = ol['indent']
                    self._last_list_content_col = ol['indent'] + marker_width
                    return True
            except Exception:
                count = self._silent_downgrade_count.get('ordered_list', 0) + 1
                self._silent_downgrade_count['ordered_list'] = count
                _logger.warning("有序列表解析异常，降级为段落", exc_info=True)
                if count > 5:
                    raise
            return False

        def _handle_fenced_admonition() -> bool:
            """``!!! type "title"`` / ``??? type``（MkDocs 风格）告示块。"""
            if first not in ('!', '?'):
                return False
            if stripped[:3] != first * 3:
                return False
            rest = stripped[3:].strip()
            if not rest:
                return False
            parts = rest.split(None, 1)
            atype = parts[0].upper()
            if atype not in _ADMONITION_TYPES:
                return False
            title = ''
            if len(parts) > 1:
                title = parts[1].strip()
                if len(title) >= 2 and title[0] in '"\'' and title[-1] == title[0]:
                    title = title[1:-1]
            self._flush_paragraph(tokens)
            self._emit_blockquote_close(tokens)
            self._start_admonition_block(atype, title, first == '?', tokens)
            return True

        _DISPATCH: dict[str, callable] = {
            '#': _handle_heading,
            '`': _handle_fence,
            '~': _handle_fence,
            '<': _handle_html,
            '[': _handle_bracket,
            ':': _handle_colon,
            '-': _handle_setext_or_hr,
            '=': _handle_setext_or_hr,
            '*': _handle_star,
            '_': _handle_setext_or_hr,
            '\\': _handle_display_math,
            '$': _handle_math_block,
            '|': _handle_table,
            '>': _handle_blockquote,
            '+': _handle_plus,
            '!': _handle_fenced_admonition,
            '?': _handle_fenced_admonition,
        }

        if first.isdigit():
            return _handle_ordered_list()

        handler = _DISPATCH.get(first)
        if handler is not None:
            return handler()
        return False

    # ── Setext 标题 / HR ────────────────────────────────

    def _try_setext_or_hr(self, stripped: str, first: str, tokens: list[Token]) -> bool:
        try:
            if len(stripped) < 3:
                return False
            non_space_count = 0
            all_same = True
            for c in stripped:
                if c == ' ':
                    continue
                if c != first:
                    all_same = False
                    break
                non_space_count += 1
            if all_same and non_space_count >= 3:
                if first in ('=', '-') and self._pending_lines:
                    # CommonMark：setext underline 前的**整个段落**成为标题内容，
                    # 可跨多行（「The setext heading underline can be preceded by
                    # multiple lines of text; the whole paragraph becomes the
                    # heading.」）。修复前要求恰好 1 行，多行时把 underline 误判
                    # 为分隔线——``Foo\nbar\n===`` 渲染为两行正文 + 一条分隔线，
                    # ``===`` 的标题语义完全丢失。
                    if not all(_is_empty_line(ln) for ln in self._pending_lines):
                        level = 1 if first == '=' else 2
                        heading_text = '\n'.join(self._pending_lines)
                        self._pending_lines = []
                        self._list_indents.clear()
                        tokens.append(Token(TokenType.HEADING, heading_text,
                                            {"level": level}))
                        return True
                elif first == '=':
                    # ★ ``=`` 不是分隔线字符（CommonMark 的 HR 仅 ``-`` / ``*`` /
                    #   ``_``）——无上文段落时整行 ``=====`` 应作为普通段落文本，
                    #   修复前被无条件当作 HR（凭空产生一条分隔线，且
                    #   ``text\\n\\n=====`` 也会误生成 HR）。
                    return False
                self._flush_paragraph(tokens)
                self._emit_blockquote_close(tokens)
                self._list_indents.clear()
                tokens.append(Token(TokenType.HR))
                return True
            return False
        except Exception:
            _logger.debug("_try_setext_or_hr异常，返回False", exc_info=True)
            return False

    # ── 表格 ─────────────────────────────────────────────

    def _emit_dashed_table(self, tokens: list[Token]) -> None:
        """提交已收集的 Pandoc simple table → TABLE token（失败按段落回退）。"""
        state = self._dashed
        self._dashed = None
        if state is None:
            return
        header, sep, rows = state
        parsed = parse_dashed_table(header, sep, rows)
        if parsed is None:
            all_lines = list(header) + [sep] + list(rows)
            tokens.append(Token(TokenType.PARAGRAPH, "\n".join(all_lines)))
            return
        data, aligns = parsed
        tokens.append(Token(TokenType.TABLE, "", {
            "rows": data, "alignments": aligns,
        }))

    def _emit_grid_table(self, tokens: list[Token]) -> None:
        """提交已收集的 Grid table 行 → TABLE token（解析失败按段落，不丢内容）。"""
        lines = self._grid_lines
        bq_depth = self._grid_bq_depth
        self._grid_lines = None
        self._grid_bq_depth = 0
        if not lines:
            return
        parsed = parse_grid_table(lines)
        if parsed is None:
            tok = Token(TokenType.PARAGRAPH, "\n".join(lines))
            if bq_depth > 0:
                tok.meta["bq_depth"] = bq_depth
            tokens.append(tok)
            return
        rows, aligns = parsed
        meta: dict = {"rows": rows, "alignments": aligns}
        if bq_depth > 0:
            meta["bq_depth"] = bq_depth
        tokens.append(Token(TokenType.TABLE, "", meta))

    def _start_table(self, sep_line: str, tokens: list[Token]):
        self._table_alignments = _parse_table_alignments(sep_line)
        self._table_pending_bq = False
        # ★ 顺序修复：表格 header 候选行之前若仍有未完成段落（``_pending_lines``
        #   尚未发射），先刷出该段落——否则表格会先于更早的段落行上屏
        #   （内容顺序错乱）。GFM 语义上表格不中断段落，此处保守拆分为
        #   「段落 + 表格」，保证内容完整且顺序正确。
        if self._pending_lines:
            self._flush_paragraph(tokens)
        num_cols = len(self._table_alignments)
        if self._table_pending_rows:
            header_cells = _parse_table_row(self._table_pending_rows[0])
            if len(header_cells) > num_cols:
                self._table_rows = [header_cells[:num_cols]]
            else:
                self._table_rows = [header_cells]
            for row_str in self._table_pending_rows[1:]:
                row_cells = _parse_table_row(row_str)
                self._table_rows.append((row_cells + [''] * num_cols)[:num_cols])
            self._table_pending_rows.clear()
        else:
            self._table_rows = []
        self._state = _State.TABLE_ACTIVE

    def _emit_table(self, tokens: list[Token]):
        if self._table_rows and self._table_alignments:
            tokens.append(Token(TokenType.TABLE, "", {
                "rows": self._table_rows,
                "alignments": self._table_alignments,
            }))
        self._table_rows = []
        self._table_alignments = []
        self._table_pending_rows.clear()
        self._table_pending_bq = False
        self._state = _State.NORMAL

    def _emit_pending_table(self, tokens: list[Token]):
        """将流式缓冲的连续表格行自动发射为 TABLE（≥2行）或 PARAGRAPH/段落续行。

        ★ 引用块严格模式（``_table_pending_bq``）：引用块内的行缓冲必须出现
        分隔行才构成表格；未出现分隔行的多行含 pipe 文本降级为段落（避免
        ``> a | b`` 之类的引用正文被误判为表格）。

        ★ 顺序修复（内容错乱）：降级为段落时**必须与既有段落缓冲
        （``_pending_lines``）保持先后顺序**——修复前直接 ``append``
        PARAGRAPH Token，而更早的段落行仍挂在 ``_pending_lines`` 中，导致
        「含 ``|`` 的行先上屏、其前面的段落行后上屏」（如「A 行\\n"
        "a || b\\nc 行」渲染为 "a || b" 在前、"A 行" 在后）。现将降级行
        按序并入 ``_pending_lines``（无段落缓冲时才直接发射单个 PARAGRAPH，
        多行以内嵌换行保持同一段落语义）。

        同样地，多行候选（≥2）在已有未完成段落时也不再建表——GFM 表格不能
        中断段落，段落内连续多行含 ``|`` 属普通文本。
        """
        strict = self._table_pending_bq
        self._table_pending_bq = False
        rows = self._table_pending_rows
        self._table_pending_rows = []
        if not rows:
            return
        if not strict and len(rows) >= 2 and not self._pending_lines:
            header = _parse_table_row(rows[0])
            num_cols = len(header)
            aligns = ['left'] * num_cols
            data_rows = [_parse_table_row(r) for r in rows[1:]]
            table_rows = [header] + [
                (r + [''] * num_cols)[:num_cols] for r in data_rows
            ]
            tokens.append(Token(TokenType.TABLE, "", {
                "rows": table_rows,
                "alignments": aligns,
            }))
            return
        if self._pending_lines:
            # 既有段落缓冲：按顺序并入（由后续 flush 统一发射，顺序不变）
            self._pending_lines.extend(rows)
            return
        # 无段落缓冲：单行原样、多行以内嵌换行保持「同一段落」语义
        tokens.append(Token(TokenType.PARAGRAPH,
                            rows[0] if len(rows) == 1 else '\n'.join(rows)))

    # ── Fenced 告示（`!!!` / `???`） ───────────────────

    def _start_admonition_block(self, atype: str, title: str,
                                collapsible: bool, tokens: list[Token]):
        """开启 ``!!! type "title"``（或 ``??? type`` 可折叠）告示块。"""
        self._state = _State.ADMONITION_BLOCK
        self._admonition_type = atype
        self._adm_title = title
        self._adm_collapsible = collapsible
        self._block_lines = []
        tokens.append(Token(TokenType.ADMONITION_OPEN, "", {
            "type": atype, "title": title, "collapsible": collapsible,
            "depth": 1, "fenced": True,
        }))

    def _emit_admonition_block_close(self, tokens: list[Token]):
        """关闭 ``!!!`` 告示块（正文行已由 ADMONITION_LINE 输出/缓冲）。

        ``head_text`` 恒为空：fenced 风格（``!!! type`` / ``??? type``）的头部
        标题只来自**同行文本**（``!!! type Title`` → ``meta["title"]``）。修复前
        无同行标题时把**正文首行**提升为 head_text——ANSI 路径首行既作头部又
        作正文（单行正文重复显示；多行正文首行被吞成标题且原始 markdown 泄漏：
        ``- a`` / ``## h`` / ` ```py `），与 Rich 路径（不使用 head_text）不一致。
        """
        body_lines = list(self._block_lines)
        # 去掉尾部空行（正文空行保留用于分隔多段；尾部空行是块结束/文档结束
        # 的产物，渲染出来会多一个无意义空行）
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        title = self._adm_title
        meta: dict = {
            "type": self._admonition_type, "title": title,
            "collapsible": self._adm_collapsible, "depth": 1, "fenced": True,
            "head_text": "",
        }
        # 正文以完整 Markdown 语义子解析（列表/代码/引用…），渲染层整体
        # 缩进渲染（与 ``<details>`` 正文同一机制）。
        body_tokens = self._parse_sub_blocks(body_lines) if body_lines else []
        if body_tokens:
            meta["body_tokens"] = body_tokens
        if body_lines:
            meta["body_lines"] = body_lines
        tokens.append(Token(TokenType.ADMONITION_CLOSE, "", meta))
        self._state = _State.NORMAL
        self._adm_title = ''
        self._adm_collapsible = False
        self._block_lines = []

    def _emit_admonition_close_ref(self, tokens: list[Token]) -> None:
        """关闭引用风格告示（``> [!TYPE]``）：正文以 Markdown 语义子解析。

        ``_adm_head_text`` 为 ``[!TYPE]`` 同行文本（渲染为头部标题，可能为
        空）；``_block_lines`` 为正文行——经子解析器产出完整块级 Token
        （列表 / 代码块 / 引用 / 嵌套告示…），挂到 CLOSE 的
        ``meta["body_tokens"]``，渲染层整体缩进渲染；``meta["body_lines"]``
        保留原始行供流式预览逐行渲染。
        """
        atype = self._admonition_type
        head_text = self._adm_head_text
        body_lines = list(self._block_lines)
        self._block_lines = []
        self._in_admonition = False
        self._admonition_type = ''
        self._adm_head_text = ''
        meta: dict = {"type": atype, "depth": 1, "head_text": head_text}
        if body_lines:
            body_tokens = self._parse_sub_blocks(body_lines)
            if body_tokens:
                meta["body_tokens"] = body_tokens
            meta["body_lines"] = body_lines
        tokens.append(Token(TokenType.ADMONITION_CLOSE, "", meta))

    # ── 引用块 ──────────────────────────────────────────

    def _parse_blockquote(self, stripped: str, tokens: list[Token]):
        if not _is_blockquote_line(stripped):
            return
        if self._bq_in_recursion >= 50:
            self._handle_paragraph_line(stripped + '\n', tokens)
            return
        depth = 0
        in_gt = True
        gt_text = ''
        space_skipped = False
        for ch in stripped:
            if in_gt and ch == '>':
                depth += 1
                space_skipped = False
                continue
            if in_gt and ch == ' ' and not space_skipped:
                # CommonMark：``>`` 后最多跳过一个空格，其余空格属于内容
                # （保留缩进 → 引用内的缩进代码块 / 列表项内块级容器可识别）
                space_skipped = True
                continue
            in_gt = False
            gt_text += ch
        inner_stripped = gt_text.strip()
        inner_has_gt = inner_stripped.startswith('>')
        if inner_stripped.startswith('[') and '!' in inner_stripped[:8]:
            adm_end = inner_stripped.find(']')
            # 宽松检测：`]` 后任意字符都接受，不再要求空格
            if adm_end > 2:
                adm_type = inner_stripped[2:adm_end].upper()
                if adm_type in ('NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION', 'CITE',
                                'INFO', 'SUCCESS', 'QUESTION', 'BUG', 'DANGER'):
                    if self._in_admonition:
                        tokens.append(Token(TokenType.ADMONITION_CLOSE, "",
                                            {"type": self._admonition_type}))
                    if self._bq_active:
                        self._emit_blockquote_close(tokens)
                    self._in_admonition = True
                    self._admonition_type = adm_type
                    adm_text = inner_stripped[adm_end + 1:].strip()
                    self._adm_head_text = adm_text
                    self._block_lines = []  # 正文行（head 另存 _adm_head_text）
                    tokens.append(Token(TokenType.ADMONITION_OPEN, adm_text,
                                        {"type": adm_type, "depth": depth}))
                    return
        if self._in_admonition:
            self._block_lines.append(gt_text.strip())  # 供流式预览
            tokens.append(Token(TokenType.ADMONITION_LINE, gt_text.strip(),
                                {"depth": depth, "type": self._admonition_type}))
            return
        if not self._bq_active:
            self._flush_paragraph(tokens)
            tokens.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                                {"depth": depth}))
            self._bq_active = True
            self._bq_depth_stack = [depth]
        elif depth > self._bq_depth_stack[-1]:
            # ★ 修复嵌套引用: 深度增加前将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
            old_depth = self._bq_depth_stack[-1]
            if self._pending_lines:
                content = '\n'.join(self._pending_lines)
                self._pending_lines.clear()
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": old_depth}))
            tokens.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                                {"depth": depth}))
            self._bq_depth_stack.append(depth)
        elif depth < self._bq_depth_stack[-1]:
            # ★ 修复嵌套引用: 深度减小前将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
            old_depth = self._bq_depth_stack[-1]
            if self._pending_lines:
                content = '\n'.join(self._pending_lines)
                self._pending_lines.clear()
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": old_depth}))
            while self._bq_depth_stack and depth < self._bq_depth_stack[-1]:
                prev = self._bq_depth_stack.pop()
                tokens.append(Token(TokenType.BLOCKQUOTE_CLOSE, "",
                                    {"depth": prev}))
            if not self._bq_depth_stack:
                self._bq_active = False
        elif inner_has_gt:
            # ★ 修复嵌套引用: 深度增加前将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
            old_depth = self._bq_depth_stack[-1]
            if self._pending_lines:
                content = '\n'.join(self._pending_lines)
                self._pending_lines.clear()
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": old_depth}))
            new_depth = depth + 1
            tokens.append(Token(TokenType.BLOCKQUOTE_OPEN, "",
                                {"depth": new_depth}))
            self._bq_depth_stack.append(new_depth)
        self._bq_in_recursion += 1
        _mark = len(tokens)
        try:
            inner_line = gt_text + '\n'
            self._parse_normal_line(inner_line, tokens)
        finally:
            self._bq_in_recursion -= 1
            self._mark_bq_depth(tokens, _mark, depth)

    #: 自带引用前缀的 Token 类型（不额外叠加 bq_depth 前缀）
    _BQ_NO_PREFIX_TYPES: frozenset = frozenset({
        TokenType.BLOCKQUOTE_OPEN,
        TokenType.BLOCKQUOTE_LINE,
        TokenType.BLOCKQUOTE_CLOSE,
    })

    def _mark_bq_depth(self, tokens: list[Token], mark: int,
                       depth: int) -> None:
        """给引用块内新产出的 Token 标记 ``bq_depth``（渲染时补 ``│`` 前缀）。

        引用块内的块级元素（列表 / 代码块 / 表格 / 标题 …）此前无引用前缀，
        与引用正文视觉断裂。标记深度后由渲染层统一补齐前缀（``setdefault``
        保证内层递归的更深深度不被外层覆盖）。
        """
        if depth <= 0:
            return
        for tok in tokens[mark:]:
            if tok.type in self._BQ_NO_PREFIX_TYPES:
                if tok.type is TokenType.BLOCKQUOTE_CLOSE:
                    # 引用块已关闭：其后产出的 Token（如紧随的顶层标题/列表）
                    # 不属于引用内容，不得补 ``│`` 前缀。修复前本行产出**全部**
                    # Token 都被标记——``> foo\n# h`` 的标题被误加引用前缀。
                    break
                continue
            meta = tok.meta
            if meta is None:
                continue
            if 'bq_depth' not in meta:
                meta['bq_depth'] = depth

    def _emit_blockquote_close(self, tokens: list[Token]):
        if not self._bq_active:
            return
        if self._bq_in_recursion > 0:
            return
        # ★ 修复嵌套引用: 将待定段落刷新为当前深度的 BLOCKQUOTE_LINE
        if self._pending_lines:
            depth = self._bq_depth_stack[-1] if self._bq_depth_stack else 1
            content = '\n'.join(self._pending_lines)
            self._pending_lines.clear()
            tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                {"depth": depth}))
        while self._bq_depth_stack:
            depth = self._bq_depth_stack.pop()
            tokens.append(Token(TokenType.BLOCKQUOTE_CLOSE, "",
                                {"depth": depth}))
        self._bq_active = False

    # ── 列表缩进 ────────────────────────────────────────

    @staticmethod
    def _leading_indent_cols(line: str) -> int:
        """行首缩进的显示列数（空格 1 列、Tab 4 列）。"""
        cols = 0
        for ch in line:
            if ch == ' ':
                cols += 1
            elif ch == '\t':
                cols += 4
            else:
                break
        return cols

    @staticmethod
    def _looks_like_block_html(stripped: str) -> bool:
        """行是否以块级 HTML 起始标签开头（``<div>`` / ``<ul>`` / ``</table>``…）。"""
        if len(stripped) < 3 or stripped[0] != '<':
            return False
        i = 1
        if stripped[i] == '/':
            i += 1
        start = i
        n = len(stripped)
        while i < n and (stripped[i].isalnum() or stripped[i] in '-:'):
            i += 1
        if i == start:
            return False
        return stripped[start:i].lower() in _BLOCK_HTML_TAGS

    def _may_start_list_block(self, stripped: str) -> bool:
        """剥离缩进后的行是否开启列表项内的**块级容器**。

        仅识别行首即可确定的块级标记（围栏代码块 / 引用 / 表格 / HTML 块 /
        数学块 / 容器块）——嵌套列表标记与普通文本续行保持既有缩进逻辑
        （``_update_list_indent`` + 续行 Token），避免双重缩进。
        """
        if not stripped:
            return False
        first = stripped[0]
        if first in ('`', '~') and _is_code_fence_line(stripped):
            return True
        if first == '>' and _is_blockquote_line(stripped):
            return True
        if first == '|':
            return True
        if first == '+' and _is_grid_table_border(stripped):
            # Pandoc grid table 边界（列表项内也作为块级容器整体收集）
            return True
        if first == '$' and stripped.startswith('$$'):
            return True
        if stripped == r'\[':
            return True
        if stripped.startswith(':::') or stripped.startswith('!!!') \
                or stripped.startswith('???'):
            return True
        if first == '<' and self._looks_like_block_html(stripped):
            return True
        return False

    def _handle_list_block_line(self, line: str, stripped: str,
                                tokens: list[Token]) -> bool:
        """列表项内的块级容器行（缩进 ≥ 列表内容列）→ 收集 / 子解析。

        返回 True 表示该行已被消费（不进入常规块级分派）。收集到的行在缩进
        回退时整体交给子解析器（完整块级语法，含嵌套引用/代码/表格），产出
        Token 统一标记 ``list_indent``，渲染层据此补列表内容缩进前缀。
        """
        content_col = self._last_list_content_col
        if content_col <= 0:
            return False
        if self._bq_active and stripped[0] == '>' and _is_blockquote_line(stripped):
            # 引用块内的行交给引用递归处理（内层剥离前缀后再次进入本方法），
            # 此处按原样行计算缩进会把 ``>`` 当顶层 → 误清列表上下文。
            # 注意：`- 项` 后的缩进引用行（列表项内的引用）不属此列——
            # ``_bq_active`` 为假时正常走列表项内块级收集。
            return False
        indent = self._leading_indent_cols(line)
        if self._list_block_active:
            if not stripped:
                self._list_block_lines.append('')
                return True
            if indent >= content_col:
                pad = min(self._list_block_indent, indent)
                self._list_block_lines.append(line[pad:].rstrip('\n'))
                return True
            self._flush_list_block(tokens)
            self._reset_list_context_if_needed(stripped)
            return False
        if not stripped or indent < content_col:
            if stripped and indent < content_col:
                # 懒续行候选（CommonMark lazy continuation：缩进不足但上一行是
                # 列表项行、本行非块起始）→ **保留**列表上下文，交由
                # ``_parse_normal_line`` 的续行分支吸收为列表项内容；其余情况
                # 仍按既有逻辑清空上下文（列表已结束）。
                if not (self._prev_emitted_type is TokenType.LIST_ITEM
                        and not self._starts_new_block(stripped)):
                    self._reset_list_context_if_needed(stripped)
            return False
        if not self._may_start_list_block(stripped):
            return False
        self._list_block_active = True
        self._list_block_indent = indent
        self._list_block_lines = [line[indent:].rstrip('\n')]
        return True

    def _flush_list_block(self, tokens: list[Token]) -> None:
        """把收集到的列表项内块级内容子解析为 Token（标记 ``list_indent``）。"""
        lines = self._list_block_lines
        self._list_block_active = False
        self._list_block_lines = []
        if not lines:
            return
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return
        sub = self._parse_sub_blocks(lines)
        list_indent = max(0, self._last_list_indent)
        for tok in sub:
            tok.meta.setdefault('list_indent', list_indent)
        tokens.extend(sub)

    def _reset_list_context_if_needed(self, stripped: str) -> None:
        """非列表项行出现时结束列表上下文（清缩进跟踪，防跨块误缩进）。"""
        if not stripped:
            return
        if (self._bq_active and stripped[0] == '>'
                and _is_blockquote_line(stripped)):
            # 引用块内的行不改变列表上下文（引用递归会剥离前缀后重新判定）
            return
        first = stripped[0]
        is_item = False
        if first in ('-', '*', '+') and len(stripped) > 1 and stripped[1] == ' ':
            is_item = True
        elif first.isdigit() and self._try_ol_item(stripped, stripped) is not None:
            is_item = True
        if not is_item:
            self._last_list_content_col = -1
            self._last_list_indent = -1

    def _update_list_indent(self, indent: int):
        try:
            if not self._list_indents or indent > self._list_indents[-1]:
                self._list_indents.append(indent)
            elif indent < self._list_indents[-1]:
                while self._list_indents and indent < self._list_indents[-1]:
                    self._list_indents.pop()
                if not self._list_indents or indent != self._list_indents[-1]:
                    self._list_indents.append(indent)
        except Exception:
            _logger.debug("_update_list_indent异常", exc_info=True)

    # ── 段落 ─────────────────────────────────────────────

    @staticmethod
    def _starts_new_block(stripped: str) -> bool:
        """行是否开启一个新的块级元素（块引用懒续行 / 列表懒续行的终止判定）。

        只识别「行首即可确定」的块起始标记（标题 / 围栏 / 引用 / HTML 块 /
        列表项 / 分隔线 / 表格 / 容器块 / 定义类行）；普通文本（字母、数字
        后非列表标记等）返回 ``False``，可作为段落续行。

        判定**保守**：宁可判为「块起始」（关闭容器，走既有逻辑）也不把真正
        的新块误吞进引用/列表内容。
        """
        if not stripped:
            return True
        first = stripped[0]
        if first in ('#', '`', '~', '>', '<', '|'):
            return True
        if first in ('-', '*', '+'):
            if len(stripped) > 1 and stripped[1] == ' ':
                return True  # 无序列表项
            core = stripped.replace(' ', '')
            if len(core) >= 3 and _is_only_chars(core, first):
                return True  # 分隔线（``***`` / ``---`` / ``___``）
            if first == '+' and _is_grid_table_border(stripped):
                return True
            return False
        if first.isdigit():
            j = 1
            while j < len(stripped) and stripped[j].isdigit():
                j += 1
            if j < len(stripped) and stripped[j] in '.):' and (
                    j + 1 >= len(stripped) or stripped[j + 1] == ' '):
                return True  # 有序列表项
        if first == '$' and stripped.startswith('$$'):
            return True
        if stripped == r'\[':
            return True
        if (stripped.startswith(':::') or stripped.startswith('!!!')
                or stripped.startswith('???')):
            return True
        if stripped.startswith('[^') or stripped.startswith('[//]:'):
            return True
        if stripped.startswith('*[') and ']:' in stripped:
            return True  # 缩写定义 ``*[ABBR]: ...``
        return False

    @staticmethod
    def _may_be_paragraph_text(first: str, stripped: str) -> bool:
        if first.isalpha():
            return True
        if first.isdigit():
            j = 1
            while j < len(stripped) and stripped[j].isdigit():
                j += 1
            # 支持 "1. " 和 "1) " 两种有序列表标记
            if j + 1 < len(stripped) and stripped[j] in ('.', ')') and stripped[j+1] == ' ':
                return False
            return True
        return False

    @staticmethod
    def _is_fn_continuation_line(line: str) -> bool:
        """脚注定义续段行（缩进 4 空格或 Tab，CommonMark 脚注续段规则）。"""
        return line[:4] == '    ' or line[:1] == '\t'

    def _handle_paragraph_line(self, line: str, tokens: list[Token]):
        self._pending_fn_def = None
        # 定义续段缓冲非空（定义列表的空行后缩进段落）→ 先作为独立缩进段落
        # 输出，避免被紧随其后的普通段落行清空而丢失内容。
        if self._def_cont_buffer:
            cont_text = '\n'.join(self._def_cont_buffer)
            self._def_cont_buffer.clear()
            tokens.append(Token(TokenType.DEFINITION_ITEM, cont_text,
                                {"term": "", "continuation": True}))
        raw = line.rstrip('\n')
        # ★ 尾随双空格 → 硬换行 (<br>)
        if len(raw) >= 2 and raw[-1] == ' ' and raw[-2] == ' ':
            raw = raw[:-2] + '<br>'
        # ★ 尾随反斜杠 → 硬换行（CommonMark 反斜杠换行语法）
        # 反斜杠换行：行末 \ 变成 <br>，但 \\ 是转义的反斜杠保持为 \
        elif len(raw) >= 1 and raw[-1] == '\\':
            backslash_count = 0
            i = len(raw) - 1
            while i >= 0 and raw[i] == '\\':
                backslash_count += 1
                i -= 1
            if backslash_count % 2 == 1:
                # 奇数个反斜杠：最后一个 \ 消耗为硬换行标记
                raw = raw[:-(backslash_count)] + '\\' * (backslash_count // 2) + '<br>'
        self._pending_lines.append(raw)

    def _flush_paragraph(self, tokens: list[Token]):
        """刷新段落到 Token。如果存在待刷的定义续行内容，先合并追加。

        ★ 引用块内（``_bq_active``）的段落以 ``BLOCKQUOTE_LINE`` 发出（携带
        当前嵌套深度）——修复前统一以 ``PARAGRAPH`` 发出：凡是先 ``_flush_paragraph``
        再 ``_emit_blockquote_close`` 的路径（空行/标题/列表/fence 起始）都会
        让引用内容以无前缀的普通段落上屏，且关闭时缓冲已空 → 额外输出一个
        空边框行。与 Rich 路径 ``InlineHandler``（BLOCKQUOTE_LINE 立即带
        深度前缀渲染）语义对齐。
        """
        if self._def_cont_buffer:
            cont_text = '\n'.join(self._def_cont_buffer)
            if self._pending_lines:
                self._pending_lines[-1] = self._pending_lines[-1] + '\n' + cont_text
            else:
                # 空行后的定义续段：作为独立缩进段落（对齐定义内容列），
                # 而非顶格普通段落——修复前 `术语\n: 定义\n\n    续段` 的续段
                # 顶格输出、与定义无缩进关系。
                tokens.append(Token(TokenType.DEFINITION_ITEM, cont_text,
                                    {"term": "", "continuation": True}))
            self._def_cont_buffer.clear()
        if self._pending_lines:
            content = '\n'.join(self._pending_lines)
            self._pending_lines = []
            if self._bq_active:
                depth = self._bq_depth_stack[-1] if self._bq_depth_stack else 1
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                    {"depth": depth}))
            else:
                tokens.append(Token(TokenType.PARAGRAPH, content))

    # ── 空行 ─────────────────────────────────────────────

    def _handle_empty_line(self, tokens: list[Token]):
        if self._list_block_active:
            # 列表项内块级容器的块内空行：并入收集缓冲（不产出 EMPTY_LINE，
            # 也保留列表上下文，使 ``- 项`` 空行后的围栏代码块仍归入该项）。
            self._list_block_lines.append('')
            return
        # 脚注定义 / 定义列表续行待定状态跨空行保留（GFM 多段落：空行后缩进
        # 续段仍归入同一脚注 / 定义）；无待定状态时无需处理。
        # 注意：此处**不清空** ``_def_cont_buffer``——定义续段交由紧随的
        # ``_flush_paragraph`` 输出（清空会导致续段内容丢失）。
        if self._pending_fn_def is not None:
            self._pending_fn_blank = True
        if self._in_admonition:
            self._emit_admonition_close_ref(tokens)
        self._flush_paragraph(tokens)
        self._emit_blockquote_close(tokens)
        self._list_indents.clear()
        # 空行打断「参考式链接定义 + 下一行标题」的关联（标题须紧跟定义行）；
        # 未补全的 ``[id]:``（URL 待定）按段落输出，不静默丢弃。
        self._pending_ref_title = None
        self._pending_ref_def = None
        if self._pending_ref_def_text:
            self._pending_lines.append(self._pending_ref_def_text)
            self._pending_ref_def_text = ""
        if self._table_pending_rows:
            self._emit_pending_table(tokens)
        if self._state == _State.TABLE_ACTIVE:
            self._emit_table(tokens)
        tokens.append(Token(TokenType.EMPTY_LINE))

    # ── 显示数学块 ──────────────────────────────────────

    def _start_display_math(self, tokens: list[Token]):
        self._state = _State.DISPLAY_MATH_BLOCK
        self._block_lines = []
        self._math_env_end = None
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _start_math_block(self, tokens: list[Token]):
        self._state = _State.MATH_BLOCK
        self._block_lines = []
        self._math_env_end = None
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _start_math_env(self, env: str, begin_line: str, tokens: list[Token]):
        """KaTeX auto-render 显示环境（``\\begin{align}`` 无 ``$$`` 定界符）。

        进入 MATH_BLOCK 状态并以 ``\\end{env}`` 作为结束标记；源码保留
        ``\\begin`` / ``\\end`` 行（公式渲染器按环境语义排版）。
        """
        self._state = _State.MATH_BLOCK
        self._block_lines = [begin_line]
        self._math_env_end = "\\end{" + env + "}"
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _emit_math_block(self, tokens: list[Token]):
        source = '\n'.join(self._block_lines)
        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                            {"source": source}))
        self._math_env_end = None
        self._state = _State.NORMAL

    # ── Mermaid ──────────────────────────────────────────

    def _emit_mermaid_block(self, tokens: list[Token]):
        source = ''.join(self._block_lines).strip()
        # ★ 修复（Mermaid 内容丢失）：``source`` 必须同时写入 ``meta["source"]``
        #   ——渲染引擎只读 ``meta["source"]``（``MERMAID_BLOCK_CLOSE`` 分支），
        #   仅放 content 时 ``src`` 取到引擎的空缓冲 → Mermaid 块提交后只剩空框。
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source,
                            {"source": source}))
        self._state = _State.NORMAL

    # ── Details ─────────────────────────────────────────

    def _try_details_open(self, stripped: str) -> bool:
        try:
            lower = stripped.lower()
            return lower.startswith('<details')
        except Exception:
            _logger.debug("_try_details_open异常，返回False", exc_info=True)
            return False

    def _start_details(self, stripped: str, tokens: list[Token]):
        self._state = _State.DETAILS_BLOCK
        self._details_depth = 1
        self._details_summary = ''
        self._details_open_emitted = False
        # ``<details open>``：HTML 布尔属性（存在即为真）——渲染层显示展开图标
        try:
            _tag, attrs = parse_open_tag(stripped)
            self._details_open = "open" in attrs
        except Exception:
            self._details_open = False
        self._block_lines = []
        lower = stripped.lower()
        # 同行 ``</details>``（单行折叠块）：截断标签后的内容，避免正文
        # 夹带字面 ``</details>``（修复前只对含 <summary> 的行做了部分处理，
        # 且 ``find(..., sm_start)`` 在无 summary 时从末尾搜索 → 找不到闭合，
        # 状态残留把后续行全部吞进折叠块）。
        close_pos = lower.find('</details>')
        text = stripped[:close_pos] if close_pos >= 0 else stripped
        sm_start = lower.find('<summary')
        if sm_start >= 0:
            self._handle_details_summary_line(text, tokens)
        else:
            tag_end = text.find('>')
            body = text[tag_end + 1:].strip() if tag_end >= 0 else ''
            if body:
                self._block_lines.append(body)
        if close_pos >= 0:
            self._emit_details_close(tokens)

    # ── HTML 块 ──────────────────────────────────────────

    def _try_block_html(self, stripped: str) -> str | None:
        lower = stripped.lower()
        i = 0
        while i < len(lower) and lower[i] in ' \t':
            i += 1
        if i >= len(lower) or lower[i] != '<':
            return None
        i += 1
        if i < len(lower) and lower[i] == '/':
            i += 1
        tag_start = i
        while i < len(lower) and (lower[i].isalnum() or lower[i] in '-:'):
            i += 1
        tag = lower[tag_start:i]
        if tag and (tag in _BLOCK_HTML_TAGS or tag in _VOID_HTML_TAGS):
            return tag
        return None

    def _is_html_close(self, stripped: str, tag: str) -> bool:
        lower = stripped.lower().strip()
        return lower == f'</{tag}>' or lower.startswith(f'</{tag} ')

    def _start_html_block(self, tag: str, tokens: list[Token],
                           line_content: str = ""):
        self._state = _State.HTML_BLOCK
        self._block_html_tag = tag
        self._html_lines = []
        self._html_depth = 1
        # 开标签属性（``align`` / ``class`` / ``value``…）：渲染层据此做
        # 居中 / 语言推断 / 进度条等语义化呈现（``_html_attrs`` 单一真源）。
        _open_tag, html_attrs = parse_open_tag(line_content)
        # 结构化收集阶段（``<ol start>`` 等）也要用到开标签属性
        self._html_block_attrs = html_attrs
        heading_level = _HTML_HEADING_LEVELS.get(tag)
        if tag in _VOID_HTML_TAGS and tag not in ('hr',):
            # 空元素单行（``<img>`` / ``<input>`` / ``<source>`` / ``<track>``…）：
            # 立即 OPEN+CLOSE（不进入 HTML_BLOCK 状态——空元素没有结束标签，
            # 否则会把后续文档全部吞进本块）。标签后的剩余文本作为段落 Token
            # 发射，保证内容不丢（``<img …> 说明``）。
            tokens.append(Token(TokenType.HTML_BLOCK_OPEN, "",
                                {"tag": tag, "attrs": html_attrs,
                                 "tail": _after_open_tag_text(line_content)}))
            tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "", {"tag": tag}))
            self._state = _State.NORMAL
            return
        if heading_level is not None:
            # ``<h1>``~``<h6>``：内容按 Markdown 标题语义渲染（不显示标签行）
            self._start_html_heading(tokens, line_content, heading_level)
            return
        if tag == 'p':
            # ``<p>`` 块：内容按普通段落渲染（不显示 ``▸ <p>`` 标记行）
            self._start_html_paragraph(tokens, line_content)
            return
        if tag == 'pre':
            # ``<pre>`` 块：内容按代码块渲染
            self._start_html_pre(tokens, line_content)
            return
        if tag == 'blockquote':
            # ``<blockquote>`` 块：内容按引用行渲染（``│`` 前缀）
            self._start_html_blockquote(tokens, line_content)
            return
        tokens.append(Token(TokenType.HTML_BLOCK_OPEN, "",
                            {"tag": tag, "attrs": html_attrs}))
        if not line_content:
            return
        line_text = line_content.rstrip('\n')
        close_tag = f'</{tag}>'
        lower_line = line_content.lower()
        if tag in self._HTML_COLLECT_TAGS:
            # 结构化标签（``<table>``/``<ul>``/``<ol>``）：起始行纳入原始行流
            # （嵌套列表的层数依赖起始 ``<ul>``/``<ol>`` 入栈），整块收集后
            # 尝试解析为结构化 Token，失败则逐行回退（内容不丢）。
            self._html_lines.append(line_text)
            if close_tag in lower_line:
                if self._emit_html_structured(tokens):
                    self._state = _State.NORMAL
                    return
                for raw in self._html_lines:
                    tokens.append(Token(TokenType.HTML_BLOCK_LINE, raw,
                                        {"tag": tag}))
                self._html_lines = []
                tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                    {"tag": tag}))
                self._state = _State.NORMAL
            return
        if close_tag in lower_line:
            tag_end = -1
            search_start = lower_line.find(tag)
            if search_start >= 0:
                tag_end = line_content.find('>', search_start + len(tag))
            if tag_end >= 0:
                inner = line_content[tag_end + 1:]
                close_pos = inner.lower().find(close_tag)
                if close_pos >= 0:
                    content = inner[:close_pos].strip()
                    if content:
                        tokens.append(Token(TokenType.HTML_BLOCK_LINE,
                                            content, {"tag": tag}))
                    tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                        {"tag": tag}))
                    self._state = _State.NORMAL

    #: 需要收集原始行整体解析的 HTML 块标签（``<table>`` → 框线表格；
    #: ``<ul>``/``<ol>`` → 嵌套列表项；``<dl>`` → 定义列表）
    _HTML_COLLECT_TAGS: frozenset = frozenset({'table', 'ul', 'ol', 'dl'})

    def _feed_html_paragraph_line(self, stripped: str,
                                  tokens: list[Token]) -> None:
        """``<p>`` HTML 块内行 → PARAGRAPH（闭合行只取其前的文本）。"""
        text = stripped
        low = text.lower()
        if self._html_depth <= 0:
            self._state = _State.NORMAL
            close = low.find('</p>')
            text = text[:close] if close > 0 else ''
        if text.strip():
            tokens.append(Token(TokenType.PARAGRAPH, text.strip()))

    def _start_html_heading(self, tokens: list[Token],
                            line_content: str, level: int) -> None:
        """``<h1>``~``<h6>`` HTML 标题块 → HEADING Token（按级别样式）。"""
        text = (line_content or "").strip()
        if not text:
            return
        low = text.lower()
        close_tag = f'</h{level}>'
        if low.startswith(f'<h{level}'):
            gt = text.find('>')
            inner = text[gt + 1:] if gt >= 0 else ''
            close = inner.lower().find(close_tag)
            if close >= 0:
                self._state = _State.NORMAL
                content = inner[:close].strip()
                if content:
                    tokens.append(Token(TokenType.HEADING, content,
                                        {"level": level}))
                return
            content = inner.strip()
            if content:
                tokens.append(Token(TokenType.HEADING, content,
                                    {"level": level}))
            return
        if low.startswith(f'</h{level}'):
            self._state = _State.NORMAL
            return
        tokens.append(Token(TokenType.HEADING, text, {"level": level}))

    def _feed_html_heading_line(self, stripped: str, tokens: list[Token],
                                level: int) -> None:
        """``<h1>``~``<h6>`` 块内行 → HEADING（闭合行只取其前文本）。"""
        close_tag = f'</h{level}>'
        text = stripped
        if self._html_depth <= 0:
            self._state = _State.NORMAL
            close = text.lower().find(close_tag)
            text = text[:close] if close > 0 else ''
        if text.strip():
            tokens.append(Token(TokenType.HEADING, text.strip(),
                                {"level": level}))

    def _start_html_pre(self, tokens: list[Token],
                        line_content: str) -> None:
        """``<pre>`` HTML 预格式化块 → 代码块（语言由 ``class`` 推断）。"""
        text = (line_content or "").strip()
        if not text:
            return
        low = text.lower()
        if low.startswith('<pre'):
            # 语言推断：``<pre class="language-python">`` 或内层
            # ``<code class="language-python">``（无则回退 ``text``）。
            self._html_pre_lang = _html_pre_language(text)
            gt = text.find('>')
            inner = text[gt + 1:] if gt >= 0 else ''
            close = inner.lower().find('</pre>')
            if close >= 0:
                self._state = _State.NORMAL
                self._emit_html_pre_lines([inner[:close]], tokens)
                return
            self._html_lines.append(inner)
            return
        self._html_lines.append(text)

    def _feed_html_pre_line(self, raw_line: str, tokens: list[Token]) -> None:
        """``<pre>`` 块内行 → 代码行缓冲（保留原始缩进；闭合时整块输出）。"""
        line = raw_line.rstrip('\n')
        if self._html_depth <= 0:
            self._state = _State.NORMAL
            close = line.lower().find('</pre>')
            if close > 0:
                self._html_lines.append(line[:close])
            self._emit_html_pre_lines(self._html_lines, tokens)
            self._html_lines = []
            return
        self._html_lines.append(line)

    def _emit_html_pre_lines(self, lines: list[str], tokens: list[Token]) -> None:
        content = "\n".join(lines).strip('\n')
        if not content.strip():
            return
        lang = getattr(self, "_html_pre_lang", "") or _html_pre_language(content)
        # ``<pre><code class="...">…</code></pre>``：剥离内层 ``<code>`` 标签
        # （语言已从 class 提取），避免标签字面混入代码内容。
        content = _strip_inline_code_tags(content)
        # HTML 语义：``<pre>`` 内容中的实体解码（``&lt;`` → ``<``）
        content = decode_html_entities(content)
        tokens.append(Token(TokenType.CODE_BLOCK, content,
                            {"lang": lang or "text", "closed": True}))

    def _start_html_blockquote(self, tokens: list[Token],
                               line_content: str) -> None:
        """``<blockquote>`` HTML 引用块 → 引用行（``│`` 前缀渲染）。"""
        text = (line_content or "").strip()
        if not text:
            return
        low = text.lower()
        if low.startswith('<blockquote'):
            gt = text.find('>')
            inner = text[gt + 1:] if gt >= 0 else ''
            close = inner.lower().find('</blockquote>')
            if close >= 0:
                self._state = _State.NORMAL
                content = inner[:close].strip()
                if content:
                    tokens.append(Token(TokenType.BLOCKQUOTE_LINE, content,
                                        {"depth": 1}))
                return
            if inner.strip():
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, inner.strip(),
                                    {"depth": 1}))
            return
        if low.startswith('</blockquote'):
            self._state = _State.NORMAL
            return
        tokens.append(Token(TokenType.BLOCKQUOTE_LINE, text, {"depth": 1}))

    def _feed_html_blockquote_line(self, stripped: str,
                                   tokens: list[Token]) -> None:
        """``<blockquote>`` 块内行 → 引用行（闭合行只取其前文本）。"""
        if self._html_depth <= 0:
            self._state = _State.NORMAL
            close = stripped.lower().find('</blockquote>')
            text = stripped[:close].strip() if close > 0 else ''
            if text:
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, text,
                                    {"depth": 1}))
            return
        if stripped.strip():
            tokens.append(Token(TokenType.BLOCKQUOTE_LINE, stripped.strip(),
                                {"depth": 1}))

    def _start_html_paragraph(self, tokens: list[Token],
                              line_content: str) -> None:
        """``<p>`` HTML 段落块：内容作为普通段落 Token（不显示标签标记）。

        支持单行 ``<p>x</p>`` 与多行 ``<p>\\nx\\n</p>``（每行一个段落 Token，
        行内 Markdown 由段落渲染器解析）。
        """
        text = (line_content or "").strip()
        self._html_depth = 1
        if not text:
            return
        lower = text.lower()
        if lower.startswith('<p'):
            gt = text.find('>')
            inner = text[gt + 1:] if gt >= 0 else ''
            close = inner.lower().find('</p>')
            if close >= 0:
                content = inner[:close].strip()
                if content:
                    tokens.append(Token(TokenType.PARAGRAPH, content))
                self._state = _State.NORMAL
                return
            content = inner.strip()
            if content:
                tokens.append(Token(TokenType.PARAGRAPH, content))
            return
        if lower.startswith('</p'):
            self._state = _State.NORMAL
            return
        tokens.append(Token(TokenType.PARAGRAPH, text))

    def _collect_html_line(self, line: str) -> bool:
        """结构化 HTML 块（``<table>`` / ``<ul>`` / ``<ol>``）的行收集。"""
        if self._block_html_tag not in self._HTML_COLLECT_TAGS:
            return False
        self._html_lines.append(line.rstrip('\n'))
        return True

    def _emit_html_structured(self, tokens: list[Token]) -> bool:
        """结构化 HTML 块（表格 / 列表 / 定义列表）→ 结构化 Token（成功返回 True）。"""
        if self._block_html_tag == 'table':
            return self._emit_html_table(tokens)
        if self._block_html_tag in ('ul', 'ol'):
            return self._emit_html_list(tokens)
        if self._block_html_tag == 'dl':
            return self._emit_html_dl(tokens)
        return False

    def _emit_html_dl(self, tokens: list[Token]) -> bool:
        """把收集的 ``<dl>`` 行解析为 DEFINITION_ITEM Token（术语 + 定义）。

        同一术语的多个 ``<dd>`` 共享术语（首个定义携带 ``term``，其余按续行
        缩进渲染，与 Markdown 定义列表语义一致）。
        """
        lines = self._html_lines
        self._html_lines = []
        items = _parse_html_dl_items("\n".join(lines))
        emitted = False
        current_term = ""
        for kind, text in items:
            if kind == 'dt':
                if current_term:
                    # 上一个术语没有任何定义 → 单独输出术语行（不丢内容）
                    tokens.append(Token(TokenType.DEFINITION_ITEM, "",
                                        {"term": current_term}))
                    emitted = True
                current_term = text
                continue
            if not text:
                continue
            tokens.append(Token(TokenType.DEFINITION_ITEM, text,
                                {"term": current_term}))
            current_term = ""
            emitted = True
        if current_term:
            tokens.append(Token(TokenType.DEFINITION_ITEM, "",
                                {"term": current_term}))
            emitted = True
        return emitted

    def _emit_html_list(self, tokens: list[Token]) -> bool:
        """把收集的 ``<ul>`` / ``<ol>`` 行解析为带嵌套深度的 LIST_ITEM Token。

        ``<ol start="3">`` 与 ``<li value="5">`` 的显式编号生效（无则从 1 开始）。
        """
        lines = self._html_lines
        self._html_lines = []
        start = None
        if self._block_html_tag == 'ol':
            raw_start = (getattr(self, "_html_block_attrs", None) or {}).get("start")
            if raw_start:
                try:
                    start = int(str(raw_start).strip())
                except ValueError:
                    start = None
        items = _parse_html_list_items("\n".join(lines), self._block_html_tag,
                                       start=start)
        emitted = False
        for depth, ordered, number, text in items:
            if not text:
                continue
            tokens.append(Token(TokenType.LIST_ITEM, text, {
                "indent": (depth - 1) * 2,
                "depth": depth,
                "bullet": not ordered,
                "number": number,
            }))
            emitted = True
        return emitted

    def _emit_html_table(self, tokens: list[Token]) -> bool:
        """把收集的 ``<table>`` HTML 行解析为 TABLE token（成功返回 True）。

        列对齐由 ``<td align=...>`` / ``style="text-align:..."`` 推断（显式对齐
        优先，缺省左对齐）；``<caption>`` 转为表注 Token。
        """
        if self._block_html_tag not in self._HTML_COLLECT_TAGS:
            return False
        lines = self._html_lines
        self._html_lines = []
        rows: list[list[str]] = []
        row_aligns: list[list[str]] = []
        caption: str | None = None
        for raw in lines:
            cap = _extract_html_tag_text(raw, 'caption')
            if cap is not None:
                caption = cap
                # ★ 不在取到 caption 后 continue：单行 ``<table><caption>…</caption>
                #   <tr>…</tr></table>`` 的 caption 与行数据在同一行，跳过会
                #   导致该行所有 ``<tr>`` 丢失（表格内容全空，只剩标签头行）。
            for tr in _iter_html_tag_blocks(raw, 'tr'):
                cells = _extract_html_cells(tr)
                if cells:
                    rows.append(cells)
                    row_aligns.append(_extract_html_cell_aligns(tr))
        if not rows:
            return False
        ncols = len(rows[0])
        if ncols == 0:
            return False
        norm = []
        for r in rows:
            if not r or len(r) > ncols:
                continue
            norm.append((r + [''] * ncols)[:ncols])
        if not norm:
            return False
        alignments = ['left'] * ncols
        for aligns in row_aligns:
            for i, align in enumerate(aligns[:ncols]):
                if align and alignments[i] == 'left':
                    alignments[i] = align
        tokens.append(Token(TokenType.TABLE, "", {
            "rows": norm, "alignments": alignments,
        }))
        if caption:
            tokens.append(Token(TokenType.TABLE_CAPTION, caption))
        return True

    # ── Fenced Div ───────────────────────────────────────

    def _handle_fenced_div_open(self, stripped: str, tokens: list[Token]):
        rest = stripped[3:].lstrip()
        div_type = ''
        text = ''
        if rest:
            space = rest.find(' ')
            if space > 0:
                div_type = rest[:space]
                text = rest[space + 1:].strip()
            else:
                div_type = rest
        if not div_type:
            div_type = 'NOTE'
        self._block_div_type = div_type.upper()
        self._block_lines = []
        tokens.append(Token(TokenType.FENCED_DIV_OPEN, text, {"type": self._block_div_type}))
        self._state = _State.FENCED_DIV

    def _feed_fenced_div_line(self, line: str, stripped: str, tokens: list[Token]):
        if not stripped or _is_empty_line(line):
            # ★ 空行在 fenced div 内：不关闭 div，发射空行标记作为一条空白线
            tokens.append(Token(TokenType.FENCED_DIV_LINE, "", {"type": self._block_div_type, "empty": True}))
            self._block_lines.append("")  # 供流式预览
            return
        if stripped.strip() == ':::':
            self._emit_fenced_div_close(tokens)
            return
        tokens.append(Token(TokenType.FENCED_DIV_LINE, stripped, {"type": self._block_div_type}))
        self._block_lines.append(stripped)  # 供流式预览

    def _emit_fenced_div_close(self, tokens: list[Token]) -> None:
        """关闭 fenced div：正文以完整 Markdown 语义子解析（挂 ``body_tokens``）。

        与 ``<details>`` / 告示块同一机制——容器正文支持列表 / 代码块 / 引用 /
        嵌套容器，渲染层整体缩进显示；``body_lines`` 保留原始行供流式预览。
        """
        body_lines = list(self._block_lines)
        # 去尾部空行（与 details / fenced 告示一致）
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        self._block_lines = []
        meta: dict = {"type": self._block_div_type}
        if body_lines:
            body_tokens = self._parse_sub_blocks(body_lines)
            if body_tokens:
                meta["body_tokens"] = body_tokens
            meta["body_lines"] = body_lines
        tokens.append(Token(TokenType.FENCED_DIV_CLOSE, "", meta))
        self._state = _State.NORMAL

    # ── 缩进代码块 ─────────────────────────────────────

    def _emit_code_line(self, content: str, tokens: list[Token]) -> None:
        """发射 CODE_LINE 并记录到预览缓冲（供流式预览整块重渲染）。

        ★ 预览缓冲上限：超过 ``_PREVIEW_CODE_LINES_MAX`` 行时丢弃最旧的行并
        累加 ``_preview_code_dropped``——预览显示侧本就只保留尾部
        ``_PREVIEW_MAX_LINES`` 行并给出省略提示，无上限的累积会长期占用内存、
        且每次 ``peek_pending`` 的整块 join 退化为 O(全文)。
        """
        tokens.append(Token(TokenType.CODE_LINE, content))
        self._preview_code_lines.append(content)
        if len(self._preview_code_lines) > self._PREVIEW_CODE_LINES_MAX:
            drop = len(self._preview_code_lines) - self._PREVIEW_CODE_LINES_MAX
            del self._preview_code_lines[:drop]
            self._preview_code_dropped += drop

    def _start_indented_code(self, line: str, tokens: list[Token]):
        self._state = _State.INDENTED_CODE
        self._preview_code_lines = []
        self._preview_code_dropped = 0
        self._indented_code_pending_blanks = 0
        tokens.append(Token(TokenType.CODE_FENCE_OPEN, "", {
            "lang": "text", "indented": True, "attrs": "",
        }))
        content = line[4:] if line[:4] == '    ' else line[1:]
        self._emit_code_line(content.rstrip('\n'), tokens)

    # ── 缩写定义 ───────────────────────────────────────

    def _handle_abbreviation(self, stripped: str) -> bool:
        if not stripped.startswith('*['):
            return False
        if ']:' not in stripped:
            return False
        close_bracket = stripped.find(']:')
        if close_bracket <= 2:
            return False
        abbr = stripped[2:close_bracket].strip().upper()
        full_text = stripped[close_bracket + 2:].strip()
        if not abbr or not full_text:
            return False
        self._ctx.abbr_map[abbr] = full_text
        return True

    # ── 列表项检测 ─────────────────────────────────────

    @staticmethod
    def _parse_list_item_checkbox(text: str) -> tuple[str, bool, bool, bool]:
        """检测列表项是否为任务列表（checkbox），保留 checkbox 前缀在文本中。

        is_todo() 在下游渲染层通过扫描文本开头的 [ ]/[x]/[-] 来渲染勾选框，
        因此必须保留 checkbox 标记在 content 中。

        Returns:
            (text, is_todo, is_checked, is_cancelled)
            — text 保留 '[x] ' / '[ ] ' / '[-] ' 前缀
        """
        t = text.strip()
        if len(t) >= 4 and t[0] == '[' and t[2] == ']':
            if t[1] in ' xX':
                return text, True, t[1] in 'xX', False
            if t[1] == '-':
                return text, True, False, True
        return text, False, False, False

    def _try_ul_item(self, stripped: str, line: str) -> dict | None:
        try:
            indent = 0
            for ch in line:
                if ch in ' \t':
                    indent += 1
                else:
                    break
            content = _strip_left(stripped)
            if len(content) >= 2 and content[0] in ('-', '*', '+') and content[1] == ' ':
                text = _rstrip_line(content[2:])
                text, todo, checked, cancelled = self._parse_list_item_checkbox(text)
                return {
                    'indent': indent,
                    'text': text,
                    'todo': todo,
                    'checked': checked,
                    'cancelled': cancelled,
                }
            return None
        except Exception:
            _logger.debug("_try_ul_item异常，返回None", exc_info=True)
            return None

    def _try_ol_item(self, stripped: str, line: str) -> dict | None:
        try:
            indent = 0
            for ch in line:
                if ch in ' \t':
                    indent += 1
                else:
                    break
            content = _strip_left(stripped)
            i = 0
            num_str = ''
            while i < len(content) and content[i].isdigit():
                num_str += content[i]
                i += 1
            if not num_str:
                return None
            # 支持 "1. " 和 "1) " 两种有序列表标记
            if i < len(content) and content[i] in ".')":
                delimiter = content[i]
                i += 1
                if i < len(content) and content[i] == ' ':
                    text = _rstrip_line(content[i + 1:])
                    number = int(num_str)
                    text, todo, checked, cancelled = self._parse_list_item_checkbox(text)
                    # marker_width：含缩进后的标记宽度（"1. "=3, "1) "=3, "12. "=4）
                    marker_width = len(num_str) + 2  # digit + delimiter + ' '
                    return {
                        'indent': indent,
                        'number': number,
                        'text': text,
                        'start': number,
                        'todo': todo,
                        'checked': checked,
                        'cancelled': cancelled,
                        'delimiter': delimiter,
                        'marker_width': marker_width,
                    }
            return None
        except Exception:
            _logger.debug("_try_ol_item异常，返回None", exc_info=True)
            return None

    # ── 定义列表 ───────────────────────────────────────

    def _try_definition(self, stripped: str) -> str | None:
        try:
            i = 0
            while i < len(stripped) and stripped[i] in ' \t':
                i += 1
            # ★ 修复: 要求 : 后紧跟空格/制表符，排除 :emoji: 等模式
            if (i < len(stripped) and stripped[i] == ':'
                    and i + 1 < len(stripped) and stripped[i + 1] in ' \t'):
                rest = stripped[i + 1:].strip()
                return rest if rest else None
            return None
        except Exception:
            _logger.debug("_try_definition异常，返回None", exc_info=True)
            return None

    # ── 脚注定义 ───────────────────────────────────────

    def _try_fn_def(self, stripped: str) -> dict | None:
        try:
            if stripped[0] != '[':
                return None
            close = stripped.find(']:')
            if close <= 2 or close > 100:
                return None
            ref_id = stripped[1:close]
            if not ref_id.startswith('^'):
                return None
            ref_id = ref_id[1:]
            content = stripped[close + 2:].strip()
            if ref_id and content:
                return {'ref_id': ref_id, 'content': content}
            return None
        except Exception:
            _logger.debug("_try_fn_def异常，返回None", exc_info=True)
            return None

    def _handle_fn_def(self, fn_info: dict, tokens: list[Token]):
        ref_id = fn_info['ref_id']
        content = fn_info['content']
        # ★ 只登记内容，不在定义处对 fn_order 定序——脚注编号顺序的唯一真源
        #   是「引用出现顺序」（见 ``_prescan_refs`` 同名修复）。定义行在解析
        #   时先于正文引用的**渲染**被处理，若在此处定序，一次性渲染 / 历史
        #   回放的编号会退化为「定义顺序」，与流式增量渲染（引用先入序）编号
        #   互换 → 同一文档两条路径不一致。未引用定义由文末列表按字母序补列。
        self._ctx.fn_map[ref_id] = content
        self._pending_fn_def = ref_id

    # ── 参考链接 ───────────────────────────────────────

    def _try_ref_link(self, stripped: str) -> bool:
        try:
            if stripped[0] != '[':
                return False
            close = stripped.find(']:')
            if close <= 1:
                return False
            ref_id = stripped[1:close]
            if '^' in ref_id:
                return False
            rest = stripped[close + 2:].strip()
            url_end = self._find_url_end(rest)
            url = rest[:url_end]
            title = ''
            after_url = rest[url_end:].strip()
            if after_url and after_url[0] in '"\'':
                quote = after_url[0]
                end = after_url.find(quote, 1)
                if end > 0:
                    title = after_url[1:end]
            if url and ref_id:
                self._ctx.ref_map[ref_id] = (url, title)
                # 同行无标题 → 下一行可能是缩进标题（CommonMark 允许标题写在
                # 定义的下一行）；记录待定，下一行若为标题行则补全。
                self._pending_ref_title = ref_id if not title else None
                self._pending_ref_def = None
                return True
            if ref_id and not rest:
                # ``[id]:`` 独占一行 → URL 在下一行（CommonMark 允许定义的
                # 目标写在下一行）；记录待定，下一行若是缩进 URL 则补全。
                self._pending_ref_title = None
                self._pending_ref_def = ref_id
                self._pending_ref_def_text = stripped.rstrip()
                return True
            self._pending_ref_title = None
            self._pending_ref_def = None
            self._pending_ref_def_text = ""
            # 目标为空且无下一行形态 → 非定义（按段落文本处理，不静默丢弃）
            return False
        except Exception:
            _logger.debug("_try_ref_link异常，返回False", exc_info=True)
            return False

    @staticmethod
    def _try_ref_title_line(line: str) -> str | None:
        """参考式链接定义「下一行标题」行 → 标题文本（否则 ``None``）。

        CommonMark 链接定义允许标题写在 URL 的**下一行**（缩进不超过 3 空格，
        用 ``"`` / ``'`` / ``(`` 定界，标题后仅允许空白）。本方法不消费行、
        不改状态——调用方据返回值决定是否把该行并入定义（而非输出为正文）。
        """
        if not line:
            return None
        # 缩进须 ≤ 3 空格（Tab 缩进不属于标题行）
        i = 0
        n = len(line)
        while i < n and line[i] == ' ':
            i += 1
        if i > 3:
            return None
        if i >= n or line[i] in ('\t',):
            return None
        stripped = line[i:].rstrip('\n').rstrip()
        if len(stripped) < 2:
            return None
        opener = stripped[0]
        if opener in ('"', "'"):
            closer = opener
        elif opener == '(':
            closer = ')'
        else:
            return None
        body = stripped[1:]
        out: list[str] = []
        j = 0
        m = len(body)
        while j < m:
            c = body[j]
            if c == '\\' and j + 1 < m:
                nxt = body[j + 1]
                if nxt in _REF_TITLE_ESCAPABLE:
                    out.append(nxt)
                    j += 2
                    continue
                out.append(c)
                j += 1
                continue
            if c == closer:
                if body[j + 1:].strip():
                    return None  # 标题后还有内容 → 非标题行
                return ''.join(out)
            if closer == ')' and c == '(':
                return None  # 圆括号标题内不允许未转义的 ``(``
            out.append(c)
            j += 1
        return None

    def _try_ref_def_url_line(self, line: str) -> tuple[str, str] | None:
        """参考式链接定义「下一行 URL」行 → ``(url, title)``（否则 ``None``）。

        CommonMark 允许链接定义的目标写在 ``[id]:`` 的**下一行**（缩进不超过
        3 空格）；同行还可跟标题。本方法不消费行、不改状态。
        """
        if not line:
            return None
        i = 0
        n = len(line)
        while i < n and line[i] == ' ':
            i += 1
        if i > 3 or i >= n or line[i] == '\t':
            return None
        stripped = line[i:].rstrip('\n').rstrip()
        if not stripped or stripped[0] in '[\'"(':
            return None
        url_end = self._find_url_end(stripped)
        url = stripped[:url_end]
        if not url:
            return None
        title = ''
        after = stripped[url_end:].strip()
        if after and after[0] in '"\'':
            quote = after[0]
            end = after.find(quote, 1)
            if end > 0:
                title = after[1:end]
        return url, title


    # ── 块刷出 ─────────────────────────────────────────

    _FLUSH_DISPATCH: dict[_State, str] = {
        _State.CODE_FENCE: '_flush_code_fence',
        _State.MATH_BLOCK: '_flush_math_block',
        _State.DISPLAY_MATH_BLOCK: '_flush_math_block',
        _State.MERMAID_BLOCK: '_flush_mermaid_block',
        _State.DETAILS_BLOCK: '_flush_details_block',
        _State.INDENTED_CODE: '_flush_indented_code',
        _State.FENCED_DIV: '_flush_fenced_div',
        _State.HTML_BLOCK: '_flush_html_block',
        _State.TABLE_ACTIVE: '_flush_table',
        _State.FRONT_MATTER: '_flush_front_matter',
    }

    def _flush_code_fence(self, tokens: list[Token]):
        if self._block_lines:
            for l in self._block_lines:
                tokens.append(Token(TokenType.CODE_LINE, l))
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                            {"lang": self._block_lang}))

    def _flush_math_block(self, tokens: list[Token]):
        source = '\n'.join(self._block_lines)
        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                            {"source": source}))

    def _flush_mermaid_block(self, tokens: list[Token]):
        source = ''.join(self._block_lines).strip()
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source))

    def _flush_details_block(self, tokens: list[Token]):
        self._emit_details_close(tokens)

    def _flush_indented_code(self, tokens: list[Token]):
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
            "lang": "text", "indented": True,
        }))

    def _flush_fenced_div(self, tokens: list[Token]):
        self._emit_fenced_div_close(tokens)

    def _flush_html_block(self, tokens: list[Token]):
        if self._emit_html_structured(tokens):
            return
        for raw in self._html_lines:
            tokens.append(Token(TokenType.HTML_BLOCK_LINE, raw,
                                {"tag": self._block_html_tag}))
        self._html_lines = []
        tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                            {"tag": self._block_html_tag}))

    def _flush_table(self, tokens: list[Token]):
        if self._table_rows and self._table_alignments:
            self._emit_table(tokens)
        elif self._table_pending_rows:
            self._emit_pending_table(tokens)

    def _flush_block(self, tokens: list[Token]):
        """刷出当前非 NORMAL 状态的块（dispatch 模式）。"""
        handler_name = self._FLUSH_DISPATCH.get(self._state)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            handler(tokens)
        self._state = _State.NORMAL


# ═══════════════════════════════════════════════════════════
# HTML 块级结构解析辅助（``<table>`` → 表格 Token）
# ═══════════════════════════════════════════════════════════

def _iter_html_tag_blocks(text: str, tag: str):
    """逐个产出 ``text`` 中 ``<tag ...>...</tag>`` 的内部文本（字符级扫描）。

    标签名按边界匹配（``<tr`` 不匹配 ``<track``）；自闭合写法产出空串；
    未闭合（无结束标签）时取其后的全部文本。
    """
    low = text.lower()
    open_pat = '<' + tag
    close_pat = '</' + tag + '>'
    i = 0
    n = len(text)
    while i < n:
        s = low.find(open_pat, i)
        if s < 0:
            return
        after = s + len(open_pat)
        if after < n and (low[after].isalnum() or low[after] in '-:'):
            i = after
            continue
        gt = text.find('>', s)
        if gt < 0:
            return
        if text[gt - 1:gt] == '/':  # 自闭合
            i = gt + 1
            yield ''
            continue
        e = low.find(close_pat, gt)
        if e < 0:
            yield text[gt + 1:]
            return
        yield text[gt + 1:e]
        i = e + len(close_pat)


def _iter_html_cells(tr_text: str):
    """逐个产出 ``<tr>`` 内的 ``(单元格文本, 属性文本)``（保持文档顺序）。

    单元格文本为 ``<td>``/``<th>`` 的内部内容（未剥离行内标签，交由行内解析
    器处理）；属性文本为该单元格开标签的属性区（供列对齐等语义使用）。
    """
    low = tr_text.lower()
    i = 0
    n = len(tr_text)
    while i < n:
        s_td = low.find('<td', i)
        s_th = low.find('<th', i)
        cands = [x for x in (s_td, s_th) if x >= 0]
        if not cands:
            break
        s = min(cands)
        tag = 'td' if s == s_td else 'th'
        after = s + 3
        if after < n and (low[after].isalnum() or low[after] in '-:'):
            i = after
            continue
        gt = tr_text.find('>', s)
        if gt < 0:
            break
        attrs = tr_text[s:gt + 1]
        close = low.find('</' + tag + '>', gt)
        if close < 0:
            yield tr_text[gt + 1:].strip(), attrs
            break
        yield tr_text[gt + 1:close].strip(), attrs
        i = close + len(tag) + 3


def _extract_html_cells(tr_text: str) -> list[str]:
    """提取 ``<tr>`` 内部的 ``<th>``/``<td>`` 单元格文本（保持文档顺序）。"""
    return [text for text, _attrs in _iter_html_cells(tr_text)]


def _extract_html_cell_aligns(tr_text: str) -> list[str]:
    """提取 ``<tr>`` 内每个单元格的列对齐（``align`` / ``style=text-align``）。

    返回与 ``_extract_html_cells`` 等长的列表，未知对齐为空串（渲染层按左对齐）。
    """
    aligns: list[str] = []
    for _text, attrs in _iter_html_cells(tr_text):
        try:
            aligns.append(align_of(parse_attrs(attrs)))
        except Exception:
            aligns.append("")
    return aligns


def _html_pre_language(text: str) -> str:
    """从 ``<pre>`` / 内层 ``<code>`` 标签提取代码语言（无则空串）。

    支持 ``<pre class="language-python">``、``<code class="lang-py">``、
    ``<pre lang="js">`` 等写法（``_html_attrs.language_of`` 单一真源）。
    """
    low = text.lower()
    for tag in ('code', 'pre'):
        s = low.find('<' + tag)
        while s >= 0:
            after = s + 1 + len(tag)
            if after >= len(low) or not (low[after].isalnum()
                                         or low[after] in '-:'):
                gt = text.find('>', s)
                if gt < 0:
                    break
                try:
                    _t, attrs = parse_open_tag(text[s:gt + 1])
                except Exception:
                    attrs = {}
                lang = language_of(attrs)
                if lang:
                    return lang
            s = low.find('<' + tag, after)
    return ""


def _after_open_tag_text(line: str) -> str:
    """开标签后的剩余文本（``<img …> 说明`` → ``说明``；无则空串）。

    属性值中的 ``>``（罕见）按首个 ``>`` 截断——与解析器其它 HTML 扫描口径
    一致；空元素单行识别只关心标签后是否有可见文本。
    """
    s = (line or "").strip()
    if not s.startswith("<"):
        return ""
    gt = s.find(">")
    if gt < 0:
        return ""
    return s[gt + 1:].strip()


def _strip_inline_code_tags(text: str) -> str:
    """剥离 ``<pre>`` 内容中的行内 ``<code ...>`` / ``</code>`` 标签。

    仅删除 **code** 标签本身（语言由 class 提前提取），其余行内标签原样保留
    （由行内解析器处理），保证代码内容不被标签字面污染。
    """
    out: list[str] = []
    low = text.lower()
    i = 0
    n = len(text)
    while i < n:
        s = low.find("<code", i)
        c = low.find("</code>", i)
        cands = [x for x in (s, c) if x >= 0]
        if not cands:
            out.append(text[i:])
            break
        x = min(cands)
        out.append(text[i:x])
        if x == c:
            i = x + len("</code>")
            continue
        after = x + 5
        if after < n and (low[after].isalnum() or low[after] in "-:_"):
            out.append(text[x:after])
            i = after
            continue
        gt = text.find(">", x)
        if gt < 0:
            out.append(text[x:])
            break
        i = gt + 1
    return "".join(out)


def _html_int_attr(raw_tag_text: str, name: str) -> int | None:
    """从标签内文本提取整数属性（``<ol start="3">`` / ``<li value="5">``）。

    属性不存在或非整数时返回 ``None``（调用方保持默认编号）。
    """
    if not raw_tag_text or name not in raw_tag_text.lower():
        return None
    try:
        attrs = parse_attrs("<" + raw_tag_text + ">")
        value = attrs.get(name)
        if value is None or value == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_html_dl_items(text: str) -> list[tuple[str, str]]:
    """解析 ``<dl>`` 内容 → ``[(kind, text)]``（``kind`` 为 ``dt`` / ``dd``）。

    保持文档顺序；未闭合标签取其后的全部文本（内容不丢）。
    """
    items: list[tuple[str, str]] = []
    low = text.lower()
    i = 0
    n = len(text)
    while i < n:
        s_dt = low.find('<dt', i)
        s_dd = low.find('<dd', i)
        cands = [x for x in (s_dt, s_dd) if x >= 0]
        if not cands:
            break
        s = min(cands)
        kind = 'dt' if s == s_dt else 'dd'
        after = s + 3
        if after < n and (low[after].isalnum() or low[after] in '-:'):
            i = after
            continue
        gt = text.find('>', s)
        if gt < 0:
            break
        close = low.find('</' + kind + '>', gt)
        if close < 0:
            items.append((kind, text[gt + 1:].strip()))
            break
        items.append((kind, text[gt + 1:close].strip()))
        i = close + len(kind) + 3
    return items


def _extract_html_tag_text(text: str, tag: str) -> str | None:
    """提取 ``text`` 中 ``<tag>...</tag>`` 的文本（无则 None）。"""
    for block in _iter_html_tag_blocks(text, tag):
        return block.strip()
    return None


#: HTML 列表块内的结构标签（其余标签作为行内文本保留，交由行内解析器处理）
_HTML_LIST_STRUCT_TAGS: frozenset = frozenset({'ul', 'ol', 'li', 'dl', 'dt', 'dd'})


def _parse_html_list_items(text: str, root_kind: str = 'ul', start: int | None = None):
    """把 HTML 列表块（``<ul>`` / ``<ol>``）解析为列表项序列。

    维护标签栈跟踪嵌套层级（``<ul>``/``<ol>`` 入栈 → 决定该层项目符号类型
    与深度），``<li>`` 开启列表项（其文本收集到下一个结构标签之前）。
    行内标签（``<b>``/``<a href>``/``<br>`` 等）与实体原样保留在文本中，
    由行内解析器处理——不丢失行内格式。

    Args:
        text: 收集到的 HTML 行（含标签）。
        root_kind: 根列表类型（``ul`` / ``ol``）。
        start: ``<ol start="n">`` 的起始编号（``None`` 为 1）。

    Returns:
        ``(depth, ordered, number, text)`` 元组序列（``depth`` 1-based）。
    """
    items: list[tuple[int, bool, int, str]] = []
    stack: list[str] = []
    counters: list[int] = []
    cur_text: list[str] = []
    cur_depth = 1
    cur_ordered = False
    in_item = False

    def _flush():
        nonlocal cur_text, in_item
        if not in_item:
            return
        content = ' '.join(''.join(cur_text).split())
        number = counters[-1] if (counters and cur_ordered) else 1
        items.append((max(1, cur_depth), cur_ordered, number, content))
        cur_text = []
        in_item = False

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != '<':
            j = text.find('<', i)
            if j < 0:
                j = n
            if in_item:
                cur_text.append(text[i:j])
            i = j
            continue
        gt = text.find('>', i)
        if gt < 0:
            if in_item:
                cur_text.append(text[i:])
            break
        raw = text[i + 1:gt]
        low = raw.lower().strip()
        i = gt + 1
        if low.startswith('/'):
            name = low[1:].split()[0] if low[1:].strip() else ''
            if name in ('ul', 'ol'):
                _flush()
                if stack:
                    stack.pop()
                if counters:
                    counters.pop()
                if stack:
                    cur_depth = len(stack)
                    cur_ordered = stack[-1] == 'ol'
            elif name == 'li':
                _flush()
            elif name in ('dl', 'dt', 'dd', 'p'):
                _flush()
            elif in_item:
                cur_text.append(text[i - len(raw) - 2:i])
            continue
        name = low.split()[0] if low else ''
        if name in ('ul', 'ol'):
            _flush()
            stack.append(name)
            counters.append(
                (start - 1) if (start is not None and name == root_kind
                                and len(stack) == 1) else 0)
            cur_depth = len(stack)
            cur_ordered = name == 'ol'
        elif name == 'li':
            _flush()
            if not stack and root_kind in ('ul', 'ol'):
                # 兜底：起始 ``<ul>``/``<ol>`` 行缺失（纯 ``<li>`` 片段输入）时
                # 按根列表类型建栈，保证深度与编号正确。
                stack.append(root_kind)
                counters.append(0)
            in_item = True
            cur_depth = max(1, len(stack))
            cur_ordered = bool(stack) and stack[-1] == 'ol'
            value = _html_int_attr(raw, 'value')
            if value is not None and counters:
                counters[-1] = value - 1
            if counters and cur_ordered:
                counters[-1] += 1
        elif name == 'dt':
            _flush()
            in_item = True
            cur_depth = max(1, len(stack))
            cur_ordered = False
        elif name == 'dd':
            _flush()
            in_item = True
            cur_depth = max(1, len(stack))
            cur_ordered = False
        elif in_item:
            # 非结构标签（行内 HTML）原样保留
            cur_text.append(text[i - len(raw) - 2:i])
    _flush()
    return items


__all__ = ["RegexFreeBlockParser"]
