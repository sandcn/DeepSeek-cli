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
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import Counter


from .types import Token, TokenType, RenderContext
from ._utils import parse_highlight_lines
from ..tui.widgets.lock import locked_print

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
            tokens: 输入的 Token 列表
            ctx: 渲染上下文

        Returns:
            处理后的 Token 列表
        """


# ═══════════════════════════════════════════════════════════
# Token 管道
# ═══════════════════════════════════════════════════════════

class TokenPipeline:
    """Token 流过滤器链。

    按注册顺序依次应用过滤器。
    """

    def __init__(self):
        self._filters: list[TokenFilter] = []

    def add_filter(self, filter_obj: TokenFilter) -> None:
        """在链尾添加一个过滤器。"""
        self._filters.append(filter_obj)

    def remove_filter(self, filter_obj: TokenFilter) -> None:
        """从链中移除一个过滤器。"""
        self._filters.remove(filter_obj)

    @property
    def filters(self) -> list[TokenFilter]:
        """只读的过滤器列表。"""
        return list(self._filters)

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        """依次经过所有过滤器处理 Token 流。

        支持生命周期钩子：
        - pre_process(tokens, ctx)：所有过滤器运行前调用
        - post_process(tokens, ctx)：所有过滤器运行后调用
        """
        tokens = self._pre_process(tokens, ctx)
        for f in self._filters:
            tokens = f.process(tokens, ctx)
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

class MetricsCollector(TokenFilter):
    """Token 类型统计收集器。

    统计本轮 feed() 中各类型 Token 的数量，存入 ctx 的 metrics 字段。
    可用于诊断和性能分析。
    """

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        if not hasattr(ctx, 'metrics'):
            ctx.metrics = Counter()
        for t in tokens:
            # ★ 优化：用枚举成员做键（省去 .name 的 str 构造）
            ctx.metrics[t.type] += 1
        return tokens

    def get_report(self, ctx: RenderContext) -> str:
        """获取可读的统计报告。"""
        if not hasattr(ctx, 'metrics') or not ctx.metrics:
            return "(no metrics)"
        total = sum(ctx.metrics.values())
        sorted_items = sorted(ctx.metrics.items(), key=lambda x: -x[1])
        lines = [f"  Token 统计: 总计 {total} 个"]
        for ttype, count in sorted_items[:10]:
            pct = count / total * 100
            lines.append(f"    {ttype.name:<20} {count:>4} ({pct:.1f}%)")
        if len(sorted_items) > 10:
            lines.append(f"    ... 及其他 {len(sorted_items) - 10} 种")
        return "\n".join(lines)


class CodeBlockBatcher(TokenFilter):
    """代码块批处理过滤器——将逐行的 CODE_LINE 合并为整块 CODE_BLOCK。

    将 CODE_FENCE_OPEN → (CODE_LINE)* → CODE_FENCE_CLOSE 模式
    合并为单个 CODE_BLOCK Token，供 Engine 用 Rich Syntax 整块高亮。

    收益：
    - 从每行一次 Pygments 调用 → 整个代码块一次
    - 支持跨行语法分析（多行字符串、注释等）
    - 行号渲染由 Syntax 组件统一处理
    """

    MAX_BUFFER_CHARS = 500_000
    """缓冲区字符数上限，超过此值时强制刷出当前累积的代码块，防止 OOM。"""

    MAX_BUFFER_LINES = 2000
    """缓冲区行数上限，超过此值时强制刷出当前累积的代码块。"""

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
        self._flushed_in_feed: bool = False
        self._had_force_flush_this_call: bool = False
        """跨 feed 强制刷出标记：刷出后后续 CODE_LINE 失去上下文，需自动补 fence 对。"""

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        self._had_force_flush_this_call = False
        result: list[Token] = []

        try:
            # 如果有跨 feed 缓冲的未闭合代码块，先恢复状态或强制刷出
            if self._block_meta is not None:
                self._feed_count += 1
                # 缓冲区行数/字符数超过上限时强制刷出
                if len(self._buffer) >= self.MAX_BUFFER_LINES or \
                   self._buffer_chars >= self.MAX_BUFFER_CHARS:
                    _logger.debug(
                        "CodeBlockBatcher 跨 feed 强制刷出: feed_count=%d, lines=%d, chars=%d",
                        self._feed_count, len(self._buffer), self._buffer_chars,
                    )
                    source = "\n".join(self._buffer)
                    lang = self._block_meta.get("lang", "text")
                    attrs = self._block_meta.get("attrs", "")
                    result.append(Token(TokenType.CODE_BLOCK, source, {
                        "lang": lang,
                        "attrs": attrs,
                        "title": self._block_meta.get("title", ""),
                        "highlight_lines": parse_highlight_lines(attrs),
                    }))
                    self._buffer = []
                    self._buffer_chars = 0
                    self._block_meta = None
                    self._feed_count = 0
                    self._flushed_in_feed = True  # 标记刷出状态，后续 CODE_LINE 需自动补 fence 对
                    self._had_force_flush_this_call = True
                    local_buffer: list[str] = []
                    local_buffer_chars: int = 0
                    local_meta: dict | None = None  # 强制刷出后重新初始化局部变量
                else:
                    # 恢复前一次缓存的未闭合代码块状态
                    local_buffer = self._buffer
                    local_buffer_chars = self._buffer_chars
                    local_meta = self._block_meta
                    self._buffer = []
                    self._buffer_chars = 0
                    self._block_meta = None
            else:
                local_buffer = []
                local_buffer_chars: int = 0
                local_meta = None

            for token in tokens:
                if token.type is TokenType.CODE_FENCE_OPEN:
                    # 开始新块
                    if local_meta is not None:
                        if local_buffer:
                            # 前一块未闭合 → 先刷出
                            result.append(Token(TokenType.CODE_FENCE_OPEN, "", local_meta))
                            for bl in local_buffer:
                                result.append(Token(TokenType.CODE_LINE, bl))
                            # ★ 追加 CODE_FENCE_CLOSE，确保前一块的代码块状态正确闭合
                            result.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                                "lang": local_meta.get("lang", "text"),
                                "indented": False,
                            }))
                        else:
                            # 空缓冲区 → 也输出 CODE_FENCE_CLOSE 来闭合旧块，避免旧 OPEN 被静默丢弃
                            result.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                                "lang": local_meta.get("lang", "text"),
                                "indented": False,
                            }))
                        # 清空 local_meta，准备新块
                        local_meta = None
                        local_buffer = []
                        self._flushed_in_feed = False
                    # 创建不包含 "indented" 的副本，避免修改原始 token.meta
                    local_meta = {k: v for k, v in token.meta.items() if k != "indented"}  # block_meta 已排除 indented 键，此时 fence 未闭合转为常规发射
                    if token.meta.get("indented"):
                        # 缩进代码块不批处理（保持原有逐行模式）
                        result.append(token)
                        local_meta = None
                    self._flushed_in_feed = False
                elif token.type is TokenType.CODE_LINE and (local_meta is not None or self._flushed_in_feed):
                    if self._flushed_in_feed and local_meta is None:
                        # 跨 feed 刷出后遇到 CODE_LINE，无上下文 → 自动补上 CODE_FENCE_OPEN/CODE_FENCE_CLOSE 对
                        result.append(Token(TokenType.CODE_FENCE_OPEN, "", {"lang": "text", "indented": False}))
                        result.append(token)
                        result.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                            "lang": "text",
                            "indented": False,
                        }))
                        self._flushed_in_feed = False
                        continue
                    # 累积代码行（增量维护字符计数器，避免 O(n²)）
                    local_buffer.append(token.content)
                    local_buffer_chars += len(token.content)
                    # 增量检查字符数/行数上限，超限立即刷出，避免单次 feed 累积过多
                    if local_buffer_chars >= self.MAX_BUFFER_CHARS or len(local_buffer) >= self.MAX_BUFFER_LINES:
                        _logger.debug(
                            "CodeBlockBatcher 内联强制刷出: lines=%d, chars=%d",
                            len(local_buffer), local_buffer_chars,
                        )
                        source = "\n".join(local_buffer)
                        lang = local_meta.get("lang", "text")
                        attrs = local_meta.get("attrs", "")
                        result.append(Token(TokenType.CODE_BLOCK, source, {
                            "lang": lang,
                            "attrs": attrs,
                            "title": local_meta.get("title", ""),
                            "highlight_lines": parse_highlight_lines(attrs),
                        }))
                        local_buffer = []
                        local_buffer_chars = 0
                        local_meta = None
                        self._feed_count = 0
                        self._flushed_in_feed = True
                        self._had_force_flush_this_call = True
                elif token.type is TokenType.CODE_FENCE_CLOSE and local_meta is not None:
                    # 块结束 → 发射 CODE_BLOCK
                    _logger.debug(
                        "CodeBlockBatcher 块闭合发射: feed_count=%d, lines=%d, chars=%d",
                        self._feed_count, len(local_buffer), local_buffer_chars,
                    )
                    source = "\n".join(local_buffer)
                    lang = local_meta.get("lang", "text")
                    attrs = local_meta.get("attrs", "")
                    result.append(Token(TokenType.CODE_BLOCK, source, {
                        "lang": lang,
                        "attrs": attrs,
                        "title": local_meta.get("title", ""),
                        "highlight_lines": parse_highlight_lines(attrs),
                    }))
                    local_buffer = []
                    local_buffer_chars = 0
                    local_meta = None
                    self._feed_count = 0
                    self._flushed_in_feed = False
                else:
                    # 非代码块 Token → 直接通过
                    if local_meta is not None:
                        # fence_open 之后遇到非 code_line(如 fence 未闭合) → 放弃批处理
                        result.append(Token(TokenType.CODE_FENCE_OPEN, "", local_meta))
                        for bl in local_buffer:
                            result.append(Token(TokenType.CODE_LINE, bl))
                        # ★ 先追加 CODE_FENCE_CLOSE 确保下游 CodeHandler 状态机正常闭合，
                        # 防止 engine.code_state.lang/line_num 泄漏到后续渲染中。
                        # 再追加非代码 Token，避免 CODE_FENCE_CLOSE 插在无关 Token 之后
                        result.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
                            "lang": local_meta.get("lang", "text"),
                            "indented": False,
                        }))
                        result.append(token)
                        local_buffer = []
                        local_buffer_chars = 0
                        local_meta = None
                    else:
                        result.append(token)

            # 缓冲区大小保护：超过上限时强制刷出，防止 OOM
            if local_meta is not None:
                # 末尾兜底检查（增量检查已在循环内进行）
                if local_buffer_chars >= self.MAX_BUFFER_CHARS or len(local_buffer) >= self.MAX_BUFFER_LINES:
                    _logger.debug(
                        "CodeBlockBatcher 末位强制刷出: lines=%d, chars=%d",
                        len(local_buffer), local_buffer_chars,
                    )
                    # 强制以 CODE_BLOCK 形式刷出当前累积的代码块
                    source = "\n".join(local_buffer)
                    lang = local_meta.get("lang", "text")
                    attrs = local_meta.get("attrs", "")
                    result.append(Token(TokenType.CODE_BLOCK, source, {
                        "lang": lang,
                        "attrs": attrs,
                        "title": local_meta.get("title", ""),
                        "highlight_lines": parse_highlight_lines(attrs),
                    }))
                    local_buffer = []
                    local_buffer_chars = 0
                    local_meta = None
                    self._feed_count = 0
                    self._had_force_flush_this_call = True
                    # ★ 末位强制刷出后 block_meta 已清空，但解析器状态机仍在该代码块内。
                    # 下一 feed 的 CODE_LINE 需触发自动补 fence 对逻辑，因此置为 True。
                    self._flushed_in_feed = True

            # 缓冲区残留（未闭合的代码块）→ 缓存到实例属性，等待下次 process 调用
            if local_meta is not None:
                self._buffer = local_buffer
                self._buffer_chars = local_buffer_chars
                self._block_meta = local_meta
                # 不发射任何 token — 等待代码块闭合后再一次性合并
            else:
                self._buffer = []
                self._buffer_chars = 0
                self._block_meta = None
                self._feed_count = 0
                if not self._had_force_flush_this_call:
                    self._flushed_in_feed = False

            return result
        except Exception:
            # 异常恢复：不回滚已发射 Token，仅清理缓冲状态。
            # 当前 feed 中已发射到 result 的 Token 随异常丢失（不可恢复），
            # 但不恢复 saved 快照——否则下次调用会重新发射相同内容造成重复。
            # 仅清理缓冲状态，让下次调用从干净状态开始。
            self._buffer = []
            self._buffer_chars = 0
            self._block_meta = None
            self._feed_count = 0
            self._flushed_in_feed = False
            raise


class DebugPrinter(TokenFilter):
    """调试用过滤器——打印每个经过的 Token。"""

    def __init__(self, stream=None):
        import sys
        self._stream = stream or sys.stderr

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        for t in tokens:
            content_preview = t.content[:50].replace('\n', '\\n')
            locked_print(f"  [PIPE] {t.type.name:<22} {content_preview}",
                  file=self._stream)
        return tokens
