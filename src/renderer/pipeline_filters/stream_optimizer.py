"""TokenStreamOptimizer — 合并连续段落/空行 Token，减少冗余输出。"""

from __future__ import annotations

import logging

from ..pipeline import TokenFilter
from ..types import Token, TokenType, RenderContext

_logger = logging.getLogger(__name__)


class TokenStreamOptimizer(TokenFilter):
    """Token 流优化器：合并/压缩连续 Token 减少冗余输出。

    优化规则：
      1. 合并连续 PARAGRAPH Token → 用双换行连接 content
      2. 压缩连续 2+ 个 EMPTY_LINE → 保留 1 个
      3. 移除列表项（LIST_ITEM）之间多余的 EMPTY_LINE
      4. 连续 EMPTY_LINE 后跟 PARAGRAPH 时移除 EMPTY_LINE

    用法：
      pipeline.add_filter(TokenStreamOptimizer())
    """

    def __init__(self):
        super().__init__()
        # ★ 跨 feed 状态（流式）：连续空行计数 / 上一 Token 类型 / 列表深度跨
        #   feed 保留——修复前每次 process 重置，跨 chunk 的连续空行压缩与
        #   列表边界空行处理失效（同一列表被 chunk 拆分时空行判定不一致）。
        #   ``_discarded_empty`` 仅本次 feed 内有效（每次 process 重置）。
        self._discarded_empty = False
        self._list_depth: int | None = None
        self._empty_count = 0
        self._prev_type: TokenType | None = None

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        if not tokens:
            return tokens
        # ★ 每次 process 仅重置「本次 feed 内有效」的标记；跨 feed 状态
        #   （_empty_count/_prev_type/_list_depth）保留，使流式跨 chunk 的
        #   空行/列表处理连续一致。try/finally 确保异常时本次标记复位。
        self._discarded_empty = False
        try:
            return self._process_inner(tokens)
        except Exception:
            self._discarded_empty = False
            raise

    def _process_inner(self, tokens: list[Token]) -> list[Token]:
        result: list[Token] = []

        for token in tokens:
            curr = token.type

            # ── EMPTY_LINE 处理 ──
            if curr is TokenType.EMPTY_LINE:
                self._empty_count += 1
                self._prev_type = curr
                continue

            # ── 从 EMPTY_LINE 切换到实际 Token ──
            if self._empty_count > 0:
                # 列表项之间空行处理：同深度丢弃，不同深度保留
                if curr is TokenType.LIST_ITEM:
                    item_depth = token.meta.get("depth")
                    if self._list_depth == item_depth:
                        # 同深度列表 → 丢弃空行（同一列表续行）
                        self._empty_count = 0
                    else:
                        # 不同深度或首个列表 → 保留空行（列表边界）
                        result.append(Token(TokenType.EMPTY_LINE))
                        self._empty_count = 0
                elif self._prev_type is TokenType.EMPTY_LINE and curr is TokenType.PARAGRAPH:
                    self._empty_count = 0  # 段落前丢弃空行
                    self._discarded_empty = True
                else:
                    # 保留 1 个空行
                    result.append(Token(TokenType.EMPTY_LINE))
                    self._empty_count = 0
                self._prev_type = curr
                if curr is not TokenType.LIST_ITEM:
                    self._list_depth = None

            # ── PARAGRAPH 合并（仅当 result 最后一个是 PARAGRAPH 时才合并）──
            if curr is TokenType.PARAGRAPH and self._prev_type is TokenType.PARAGRAPH and result and result[-1].type is TokenType.PARAGRAPH:
                # ★ 修复：如果上一个段落前有空行被丢弃，不合并
                if self._discarded_empty:
                    self._discarded_empty = False
                    result.append(token)
                    self._prev_type = curr
                    continue
                # 合并到前一个 PARAGRAPH（软换行连接——两者之间原本没有空行
                # Token，用 "\n\n" 连接会凭空引入段落分隔、改变语义）
                prev_token = result[-1]
                prev_token.content += "\n" + token.content
                self._prev_type = curr
                continue

            # ── 列表状态跟踪（按 depth 而非布尔标志）──
            if curr is TokenType.LIST_ITEM:
                self._list_depth = token.meta.get("depth")
            elif curr not in (TokenType.LIST_ITEM, TokenType.EMPTY_LINE):
                self._list_depth = None

            # ── 普通 Token ──
            result.append(token)
            self._prev_type = curr

        return result
