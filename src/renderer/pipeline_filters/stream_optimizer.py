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

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        if not tokens:
            return tokens

        # ★ 每次 process 调用重置所有实例状态，防止跨调用残留。
        #   try/finally 确保异常时状态也被重置，避免泄漏到下一次 process()。
        self._discarded_empty = False
        self._list_depth: int | None = None
        try:
            return self._process_inner(tokens)
        except Exception:
            # 异常时强制重置状态，防止残留影响下一次 process()
            self._discarded_empty = False
            self._list_depth = None
            raise

    def _process_inner(self, tokens: list[Token]) -> list[Token]:
        result: list[Token] = []
        prev_type: TokenType | None = None
        empty_count = 0  # 连续 EMPTY_LINE 计数

        for token in tokens:
            curr = token.type

            # ── EMPTY_LINE 处理 ──
            if curr is TokenType.EMPTY_LINE:
                empty_count += 1
                prev_type = curr
                continue

            # ── 从 EMPTY_LINE 切换到实际 Token ──
            if empty_count > 0:
                # 列表项之间空行处理：同深度丢弃，不同深度保留
                if curr is TokenType.LIST_ITEM:
                    item_depth = token.meta.get("depth")
                    if self._list_depth == item_depth:
                        # 同深度列表 → 丢弃空行（同一列表续行）
                        empty_count = 0
                    else:
                        # 不同深度或首个列表 → 保留空行（列表边界）
                        result.append(Token(TokenType.EMPTY_LINE))
                        empty_count = 0
                elif prev_type is TokenType.EMPTY_LINE and curr is TokenType.PARAGRAPH:
                    empty_count = 0  # 段落前丢弃空行
                    self._discarded_empty = True
                else:
                    # 保留 1 个空行
                    result.append(Token(TokenType.EMPTY_LINE))
                    empty_count = 0
                prev_type = curr
                if curr is not TokenType.LIST_ITEM:
                    self._list_depth = None

            # ── PARAGRAPH 合并（仅当 result 最后一个是 PARAGRAPH 时才合并）──
            if curr is TokenType.PARAGRAPH and prev_type is TokenType.PARAGRAPH and result and result[-1].type is TokenType.PARAGRAPH:
                # ★ 修复：如果上一个段落前有空行被丢弃，不合并
                if self._discarded_empty:
                    self._discarded_empty = False
                    result.append(token)
                    prev_type = curr
                    continue
                # 合并到前一个 PARAGRAPH
                prev_token = result[-1]
                prev_token.content += "\n\n" + token.content
                prev_type = curr
                continue

            # ── 列表状态跟踪（按 depth 而非布尔标志）──
            if curr is TokenType.LIST_ITEM:
                self._list_depth = token.meta.get("depth")
            elif curr not in (TokenType.LIST_ITEM, TokenType.EMPTY_LINE):
                self._list_depth = None

            # ── 普通 Token ──
            result.append(token)
            prev_type = curr

        # 末尾残留的 EMPTY_LINE 保留 1 个
        if empty_count > 0:
            result.append(Token(TokenType.EMPTY_LINE))

        return result
