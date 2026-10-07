"""pipeline — Token 流过滤器链（Parser → Engine 之间的中间件层）。

在 RecursiveDescentParser 产出 Token 之后、RenderEngine 消费之前插入可配置的过滤器链。
每个过滤器可以修改/合并/增删 Token，实现跨行预处理。

内置过滤器：
  - CodeBlockBatcher：将连续 CODE_LINE 聚合并用 Pygments 整块高亮
  - HeadingAnchorFilter：收集标题 TOC 条目
  - TokenStreamOptimizer：合并连续段落/空行 Token，减少冗余输出

扩展过滤器（位于 pipeline_filters/ 包）：
  - HeadingAnchorFilter: 从 pipeline_filters.heading_anchor 导入（收集 TOC 条目）
  - TokenStreamOptimizer: 从 pipeline_filters.stream_optimizer 导入

使用方式：
  pipeline = TokenPipeline()
  pipeline.add_filter(CodeBlockBatcher())
  # 可选：pipeline.add_filter(HeadingAnchorFilter())
  # 可选：pipeline.add_filter(TokenStreamOptimizer())
  processed = pipeline.process(tokens, ctx)

容错：``TokenPipeline.process`` 对每个过滤器独立 try/except——单个过滤器
异常不中断整条渲染链路（记录日志后跳过该过滤器，保留其余已处理结果）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .types import Token, TokenType, RenderContext
from ._utils import parse_highlight_lines, parse_linenos, parse_lineno_options
_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 过滤器基类
# ═══════════════════════════════════════════════════════════

class TokenFilter(ABC):
    """Token 过滤器基类。

    子类实现 process() 方法，对 Token 流进行变换。
    """

    @abstractmethod
    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        """处理 Token 流。

        Args:
            tokens: 输入的 Token 列表。
            ctx: 渲染上下文。

        Returns:
            处理后的 Token 列表。
        """

# ═══════════════════════════════════════════════════════════
# Token 管道
# ═══════════════════════════════════════════════════════════

class TokenPipeline:
    """Token 流过滤器链。

    按注册顺序依次应用过滤器；单个过滤器异常降级为「跳过该过滤器」，
    避免一处解析/渲染异常导致整条流式输出中断（内容静默丢失）。
    """

    def __init__(self):
        self._filters: list[TokenFilter] = []

    def add_filter(self, filter_obj: TokenFilter) -> None:
        """在链尾添加一个过滤器。"""
        self._filters.append(filter_obj)

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        """依次经过所有过滤器处理 Token 流。

        支持生命周期钩子：
        - pre_process(tokens, ctx)：所有过滤器运行前调用
        - post_process(tokens, ctx)：所有过滤器运行后调用
        """
        tokens = self._pre_process(tokens, ctx)
        for f in self._filters:
            try:
                tokens = f.process(tokens, ctx)
            except Exception:
                _logger.warning("Token 过滤器 %r 处理异常，本批跳过该过滤器",
                                type(f).__name__, exc_info=True)
        tokens = self._post_process(tokens, ctx)
        return tokens

    def _pre_process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        """预处理钩子 — 过滤器链运行前调用，子类可重写。"""
        return tokens

    def _post_process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        """后处理钩子 — 过滤器链运行后调用，子类可重写。"""
        return tokens

# ═══════════════════════════════════════════════════════════
# 内置过滤器
# ═══════════════════════════════════════════════════════════

class CodeBlockBatcher(TokenFilter):
    """代码块批处理过滤器——将逐行的 CODE_LINE 合并为整块 CODE_BLOCK。

    将 CODE_FENCE_OPEN → (CODE_LINE)* → CODE_FENCE_CLOSE 模式
    合并为单个 CODE_BLOCK Token，供 Engine 用 Rich Syntax 整块高亮。

    收益：
    - 从每行一次 Pygments 调用 → 整个代码块一次
    - 支持跨行语法分析（多行字符串、注释等）
    - 行号渲染由 Syntax 组件统一处理

    ★ 超长代码块分段（continuation 语义）：
      缓冲区超过 ``MAX_BUFFER_LINES`` / ``MAX_BUFFER_CHARS`` 时必须刷出
      （防 OOM），但**逻辑上仍是同一个代码块**——刷出的段标记
      ``meta["continuation"]``（首段 False、后续段 True）与
      ``meta["closed"]``（逻辑块是否真正闭合）。渲染层据此只在首段输出
      打开围栏、只在末段输出关闭围栏：修复前分段后逐行补 fence 对，导致
      一个代码块被渲染成多个带围栏/语言标签的独立块（视觉破碎、行号与
      语法上下文丢失）。
    """

    MAX_BUFFER_CHARS = 1_000_000
    """缓冲区字符数上限，超过此值时强制刷出当前累积的代码段，防止 OOM。

    与 ``RegexFreeBlockParser._MAX_BUFFER_SIZE`` 保持一致（同一数据在解析层
    与过滤层的缓冲上限不应割裂，避免过滤层先于解析层触发分段）。"""

    MAX_BUFFER_LINES = 2000
    """缓冲区行数上限，超过此值时强制刷出当前累积的代码段。"""

    def __init__(self):
        super().__init__()
        self._buffer: list[str] = []
        """跨 feed 调用时累积的代码行（用于跨调用合并）。"""
        self._buffer_chars: int = 0
        """跨 feed 调用时累积的字符数（避免每次 sum 计算）。"""
        self._block_meta: dict | None = None
        """跨 feed 调用时未闭合代码块的 meta 信息。"""
        self._feed_count = 0
        """当前未闭合代码块经历的连续 feed 调用次数（仅用于诊断）。"""
        self._block_continuation: bool = False
        """当前逻辑代码块是否已因缓冲上限被分段刷出（后续段为续段）。"""
        self._had_force_flush_this_call: bool = False

    # ── 内部辅助 ─────────────────────────────────────

    def _make_block(self, lines: list[str], meta: dict, *, closed: bool) -> Token:
        """组装 CODE_BLOCK token（含 continuation/closed 语义）。"""
        attrs = meta.get("attrs", "")
        _linenos, _lineno_start, _lineno_step = parse_lineno_options(attrs)
        out_meta = {
            "lang": meta.get("lang", "text"),
            "attrs": attrs,
            "title": meta.get("title", ""),
            "highlight_lines": parse_highlight_lines(attrs),
            "linenos": parse_linenos(attrs),
            "lineno_start": _lineno_start,
            "lineno_step": _lineno_step,
            "continuation": self._block_continuation,
            "closed": closed,
        }
        # 保留引用块深度（引用内的代码块需带 ``│`` 前缀渲染）与列表项缩进
        # （列表项内代码块需对齐列表内容列；0 为合法值，须按存在性判断）
        bq_depth = meta.get("bq_depth", 0)
        if bq_depth:
            out_meta["bq_depth"] = bq_depth
        if "list_indent" in meta:
            out_meta["list_indent"] = meta.get("list_indent", 0)
        return Token(TokenType.CODE_BLOCK, "\n".join(lines), out_meta)

    def _flush_segment(self, result: list[Token], lines: list[str],
                       meta: dict) -> None:
        """因缓冲上限刷出一段（逻辑块未闭合）——后续行属同一块的续段。"""
        result.append(self._make_block(lines, meta, closed=False))
        self._block_continuation = True
        self._had_force_flush_this_call = True

    def _finish_block(self, result: list[Token], lines: list[str],
                      meta: dict) -> None:
        """逻辑代码块结束（闭合 / 被非代码 Token 打断）→ 发射闭合段。"""
        result.append(self._make_block(lines, meta, closed=True))
        self._block_continuation = False

    # ── 主处理 ───────────────────────────────────────

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        self._had_force_flush_this_call = False
        result: list[Token] = []

        try:
            # 恢复上一次 feed 缓存的未闭合代码块状态
            if self._block_meta is not None:
                self._feed_count += 1
                local_buffer = self._buffer
                local_buffer_chars = self._buffer_chars
                local_meta = self._block_meta
                self._buffer = []
                self._buffer_chars = 0
                self._block_meta = None
            else:
                local_buffer = []
                local_buffer_chars = 0
                local_meta = None

            for token in tokens:
                if token.type is TokenType.CODE_FENCE_OPEN:
                    # 前一块未闭合又开新块（异常 markdown）→ 以闭合块收尾
                    if local_meta is not None:
                        self._finish_block(result, local_buffer, local_meta)
                        local_buffer, local_buffer_chars, local_meta = [], 0, None
                    # 创建不包含 "indented" 的副本，避免修改原始 token.meta
                    local_meta = {k: v for k, v in token.meta.items()
                                  if k != "indented"}
                    if token.meta.get("indented"):
                        # 缩进代码块不批处理（保持原有逐行模式）
                        result.append(token)
                        local_meta = None
                    self._block_continuation = False

                elif token.type is TokenType.CODE_LINE and local_meta is not None:
                    local_buffer.append(token.content)
                    local_buffer_chars += len(token.content)
                    # 增量检查上限，超限立即刷出本段（保留 local_meta：
                    # 逻辑块未结束，后续行继续缓冲为续段）
                    if (local_buffer_chars >= self.MAX_BUFFER_CHARS
                            or len(local_buffer) >= self.MAX_BUFFER_LINES):
                        _logger.debug(
                            "CodeBlockBatcher 分段刷出: feed_count=%d, lines=%d, chars=%d",
                            self._feed_count, len(local_buffer), local_buffer_chars,
                        )
                        self._flush_segment(result, local_buffer, local_meta)
                        local_buffer = []
                        local_buffer_chars = 0

                elif token.type is TokenType.CODE_FENCE_CLOSE and local_meta is not None:
                    # 块结束 → 发射闭合段
                    _logger.debug(
                        "CodeBlockBatcher 块闭合发射: feed_count=%d, lines=%d, chars=%d",
                        self._feed_count, len(local_buffer), local_buffer_chars,
                    )
                    self._finish_block(result, local_buffer, local_meta)
                    local_buffer = []
                    local_buffer_chars = 0
                    local_meta = None
                    self._feed_count = 0

                else:
                    # 非代码块 Token：fence 未闭合（异常 markdown）→ 收尾后透传
                    if local_meta is not None:
                        self._finish_block(result, local_buffer, local_meta)
                        local_buffer = []
                        local_buffer_chars = 0
                        local_meta = None
                    result.append(token)

            # 末尾兜底检查：本次 feed 累积超过上限则刷出（同上，保留 meta）
            if local_meta is not None and (
                local_buffer_chars >= self.MAX_BUFFER_CHARS
                or len(local_buffer) >= self.MAX_BUFFER_LINES
            ):
                _logger.debug(
                    "CodeBlockBatcher 末位分段刷出: lines=%d, chars=%d",
                    len(local_buffer), local_buffer_chars,
                )
                self._flush_segment(result, local_buffer, local_meta)
                local_buffer = []
                local_buffer_chars = 0

            # 未闭合的代码块 → 缓存到实例属性，等待下次 process 调用
            if local_meta is not None:
                self._buffer = local_buffer
                self._buffer_chars = local_buffer_chars
                self._block_meta = local_meta
            else:
                self._buffer = []
                self._buffer_chars = 0
                self._block_meta = None
                self._feed_count = 0

            return result
        except Exception:
            # 异常恢复：清理缓冲状态（避免下次重复发射）并**返回已产出结果**
            # ——修复前 re-raise 使本批已发射 Token 全部丢失（内容静默缺失）。
            # 异常信息以 error 日志保留（不静默吞掉），上层继续渲染其余 Token。
            _logger.exception("CodeBlockBatcher 处理异常，返回已产出 Token")
            self._buffer = []
            self._buffer_chars = 0
            self._block_meta = None
            self._feed_count = 0
            self._block_continuation = False
            return result
