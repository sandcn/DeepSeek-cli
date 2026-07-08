"""_inline_formatting — _InlineParser 格式标记解析 Mixin。

包含粗体、斜体、粗斜体、删除线、高亮、下标、上标的相关方法。
"""

from __future__ import annotations

import logging

from .inline_nodes import (
    InlineNode, TextNode,
    BoldNode, ItalicNode, BoldItalicNode,
    StrikethroughNode, HighlightNode,
    SubscriptNode, SuperscriptNode,
    UnderlineNode,
    SpoilerNode,
    CriticAdditionNode, CriticDeletionNode,
    CriticSubstitutionNode, CriticCommentNode,
    SmallTextNode, ColorTextNode,
    WikiLinkNode, InlineCommentNode,
    render_inline_to_text,
)

_logger = logging.getLogger(__name__)


class InlineFormattingMixin:
    """_InlineParser 格式标记解析 Mixin。

    提供以下方法：
      _try_bold_italic()
      _try_bold()
      _try_italic()
      _parse_italic_content()
      _try_strikethrough()
      _try_highlight()
      _try_subscript()
      _try_superscript()
    """

    # ── 粗斜体 *** / ___ ──────────────────────────────────

    def _try_bold_italic(self, depth: int) -> InlineNode | None:
        try:
            triple = self._text[self._pos:self._pos + 3]
            if not (self._pos + 3 < self._n and triple in ('***', '___')):
                return None
            # ★ Bug B3 fix: ___ 在词内（如 ___init___）不应触发粗斜体
            if triple == '___' and (self._is_word_boundary_underscore(3)
                                     or self._is_dunder_pattern_triple()):
                return None
            saved = self._pos
            self._pos += 3
            children, found = self._parse_until(triple, depth + 1)
            if found:
                self._pos += 3
                return self._make_nestable(BoldItalicNode, children)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_bold_italic 异常，降级处理", exc_info=True)
            return None

    # ── 粗体 ** / __ ───────────────────────────────────

    def _try_bold(self, depth: int) -> InlineNode | None:
        try:
            saved = self._pos
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '**'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '*')):
                self._pos += 2
                children, found = self._parse_until('**', depth + 1)
                if found:
                    self._pos += 2
                    return self._make_nestable(BoldNode, children)
                self._pos = saved
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '__'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '_')
                    and not self._is_word_boundary_underscore(2)
                    and not self._is_dunder_pattern()):
                self._pos += 2
                children, found = self._parse_until('__', depth + 1)
                if found:
                    self._pos += 2
                    return self._make_nestable(BoldNode, children)
                self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_bold 异常，降级处理", exc_info=True)
            return None

    # ── 斜体 * / _ ─────────────────────────────────────

    def _try_italic(self, depth: int) -> InlineNode | None:
        try:
            saved = self._pos
            if (self._text[self._pos] == '*'
                    and not (self._pos + 1 < self._n
                             and self._text[self._pos + 1] == '*')):
                self._pos += 1
                children, found = self._parse_italic_content('*', depth + 1)
                if found:
                    self._pos += 1
                    return self._make_nestable(ItalicNode, children)
                self._pos = saved
            if (self._text[self._pos] == '_'
                    and not (self._pos + 1 < self._n
                             and self._text[self._pos + 1] == '_')
                    and not self._is_word_boundary_underscore()):
                self._pos += 1
                children, found = self._parse_italic_content('_', depth + 1)
                if found:
                    self._pos += 1
                    return self._make_nestable(ItalicNode, children)
                self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_italic 异常，降级处理", exc_info=True)
            return None

    def _parse_italic_content(self, delim: str, depth: int
                              ) -> tuple[list[InlineNode], bool]:
        if depth > self._MAX_DEPTH:
            return [TextNode(content=self._text[self._pos:])], False
        try:
            nodes: list[InlineNode] = []
            plain_buf: list[str] = []

            def _emit_plain():
                if plain_buf:
                    nodes.append(TextNode(content=''.join(plain_buf)))
                    plain_buf.clear()

            while self._pos < self._n:
                ch = self._text[self._pos]
                if ch == delim:
                    if (self._pos + 1 < self._n
                            and self._text[self._pos + 1] == delim):
                        node = self._try_format(depth)
                        if node is not None:
                            _emit_plain()
                            nodes.append(node)
                            continue
                        plain_buf.append(self._text[self._pos])
                        plain_buf.append(self._text[self._pos + 1])
                        self._pos += 2
                        continue
                    else:
                        _emit_plain()
                        return nodes, True
                node = self._try_format(depth)
                if node is not None:
                    _emit_plain()
                    nodes.append(node)
                    continue
                plain_buf.append(ch)
                self._pos += 1
            _emit_plain()
            return nodes, False
        except Exception:
            _logger.debug("_parse_italic_content 异常，降级处理", exc_info=True)
            return [], False

    # ── 删除线 ~~ ───────────────────────────────────────

    def _try_strikethrough(self, depth: int) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '~~'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '~')):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('~~', depth + 1)
                if found:
                    self._pos += 2
                    return self._make_nestable(StrikethroughNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_strikethrough 异常，降级处理", exc_info=True)
            return None

    # ── 高亮 == ─────────────────────────────────────────

    def _try_highlight(self, depth: int) -> InlineNode | None:
        try:
            # ★ 修复: == 后跟 > 是粗箭头 ==>, 前邻 < 是粗箭头 <==
            #    避免 highlight 语法吞掉箭头
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '=='
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '=')
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '>'
                             and (self._pos == 0 or self._text[self._pos - 1] != '='))
                    and not (self._pos > 0
                             and self._text[self._pos - 1] == '<')):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('==', depth + 1)
                if found:
                    self._pos += 2
                    return self._make_nestable(HighlightNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_highlight 异常，降级处理", exc_info=True)
            return None

    # ── 下标 ~ ──────────────────────────────────────────

    def _try_subscript(self, depth: int) -> InlineNode | None:
        try:
            if (self._text[self._pos] == '~'
                    and not (self._pos + 1 < self._n
                             and self._text[self._pos + 1] == '~')):
                saved = self._pos
                self._pos += 1
                # ★ 修复 Bug: 空格后不应触发下标（~ text~ 不是合法下标）
                if self._pos < self._n and self._text[self._pos] in ' \t\n\r':
                    self._pos = saved
                    return None
                children, found = self._parse_until('~', depth + 1)
                if found:
                    # ★ 修复 Bug: 若闭合 ~ 后紧跟另一个 ~（~~strikethrough），
                    # 回退以避免吞噬父级 ~~ 闭合定界符
                    if self._pos + 1 < self._n and self._text[self._pos + 1] == '~':
                        self._pos = saved
                        return None
                    self._pos += 1
                    return self._make_nestable(SubscriptNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_subscript 异常，降级处理", exc_info=True)
            return None

    # ── 上标 ^ ──────────────────────────────────────────

    def _try_superscript(self, depth: int) -> InlineNode | None:
        try:
            if self._text[self._pos] == '^':
                saved = self._pos
                self._pos += 1
                # ★ 修复 Bug: 空格后不应触发上标（^ text^ 不是合法上标）
                if self._pos < self._n and self._text[self._pos] in ' \t\n\r':
                    self._pos = saved
                    return None
                children, found = self._parse_until('^', depth + 1)
                if found:
                    self._pos += 1
                    return self._make_nestable(SuperscriptNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_superscript 异常，降级处理", exc_info=True)
            return None

    # ── 词内下划线保护 ─────────────────────────────────

    def _is_word_boundary_underscore(self, count: int = 1) -> bool:
        """如果当前位置的 `_`×count 被字母数字包围，则不视为格式标记。

        Args:
            count: 下划线数量（1=斜体, 2=粗体, 3=粗斜体）

        对 count>=2 的特殊规则：行首的 __ 后紧跟字母数字 → __init__ 类 dunder 名，不触发格式。
        """
        try:
            if self._pos + count >= self._n:
                return False
            next_ch = self._text[self._pos + count]
            if self._pos > 0:
                prev_ch = self._text[self._pos - 1]
                return prev_ch.isalnum() and next_ch.isalnum()
            else:
                # 行首位置：
                #   count=1 (_xxx_)：无前邻字符，不视为词内（斜体正常触发）
                #   count>=2 (__xxx__)：后紧跟字母数字 → __init__ 类 dunder 前缀
                if count >= 2:
                    return next_ch.isalnum()
                return False
        except Exception:
            _logger.debug("_is_word_boundary_underscore 异常，降级处理", exc_info=True)
            return False

    def _is_dunder_pattern(self) -> bool:
        """检测 `__` 后是否紧跟 Python dunder 模式（如 __init__）。

        若 `__` 后紧跟字母数字或下划线（兼容 ___xxx 的 triple underscore），
        且向前扫描 ~30 字符内能找到闭合 `__`
        且中间内容全为字母数字/下划线 → dunder 名称，不视为粗体标记。
        """
        try:
            pos_after = self._pos + 2
            if pos_after >= self._n:
                return False
            ch_after = self._text[pos_after]
            if not (ch_after.isalnum() or ch_after == '_'):
                return False
            # 向前扫描最多 32 字符寻找闭合 __
            limit = min(pos_after + 32, self._n)
            close_pos = self._text.find('__', pos_after, limit)
            if close_pos < 0:
                return False
            # 检查闭合 __ 后是否是非字母数字（排除 dunder 嵌套 __xxx__yyy 模式）
            after_close = close_pos + 2
            if after_close < self._n and self._text[after_close].isalnum():
                return False
            # 检查中间内容是否全为字母数字或下划线
            middle = self._text[pos_after:close_pos]
            return bool(middle) and all(ch.isalnum() or ch == '_' for ch in middle)
        except Exception:
            _logger.debug("_is_dunder_pattern 异常，降级处理", exc_info=True)
            return False

    def _is_dunder_pattern_triple(self) -> bool:
        """检测 `___` 后是否紧跟 triple-dunder 模式（如 ___init___）。

        与 _is_dunder_pattern 类似，但扫描 `___` 闭合。
        """
        try:
            pos_after = self._pos + 3
            if pos_after >= self._n:
                return False
            if not self._text[pos_after].isalnum():
                return False
            limit = min(pos_after + 32, self._n)
            close_pos = self._text.find('___', pos_after, limit)
            if close_pos < 0:
                return False
            after_close = close_pos + 3
            if after_close < self._n and self._text[after_close].isalnum():
                return False
            middle = self._text[pos_after:close_pos]
            return all(ch.isalnum() or ch == '_' for ch in middle)
        except Exception:
            _logger.debug("_is_dunder_pattern_triple 异常，降级处理", exc_info=True)
            return False

    # ── 下划线 ++ ──────────────────────────────────────

    def _try_underline(self, depth: int) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '++'):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('++', depth + 1)
                if found:
                    self._pos += 2
                    return self._make_nestable(UnderlineNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_underline 异常，降级处理", exc_info=True)
            return None

    # ── 剧透/黑幕 || ─────────────────────────────────────

    def _try_spoiler(self, depth: int) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == '||'):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('||', depth + 1)
                if found:
                    self._pos += 2
                    return self._make_nestable(SpoilerNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_spoiler 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {++added++} ────────────────────────

    def _try_critic_addition(self, depth: int) -> InlineNode | None:
        """{++...++} → CriticAdditionNode（绿色背景文本）。"""
        try:
            if (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{++'
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '+')):
                saved = self._pos
                self._pos += 3
                children, found = self._parse_until('++}', depth + 1)
                if found:
                    self._pos += 3  # skip ++}
                    return self._make_nestable(CriticAdditionNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_critic_addition 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {--deleted--} ──────────────────────

    def _try_critic_deletion(self, depth: int) -> InlineNode | None:
        """{--...--} → CriticDeletionNode（红色删除线文本）。"""
        try:
            if (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{--'
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '-')):
                saved = self._pos
                self._pos += 3
                children, found = self._parse_until('--}', depth + 1)
                if found:
                    self._pos += 3  # skip --}
                    return self._make_nestable(CriticDeletionNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_critic_deletion 异常，降级处理", exc_info=True)
            return None

    # ── 小号文本 {-small-} ──────────────────────────────

    def _try_small_text(self, depth: int) -> InlineNode | None:
        """{-...-} → SmallTextNode（dim 小号文本）。

        注意：{- 不能被 -- 匹配（优先走 _try_critic_deletion）。
        """
        try:
            if (self._pos + 3 < self._n
                    and self._text[self._pos:self._pos + 2] == '{-'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '-')):
                saved = self._pos
                self._pos += 2
                children, found = self._parse_until('-}', depth + 1)
                if found:
                    self._pos += 2  # skip -}
                    return self._make_nestable(SmallTextNode, children)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_small_text 异常，降级处理", exc_info=True)
            return None

    # ── 彩色文本 {color:red}text{color} ─────────────────

    # 已知颜色名白名单（Rich 终端色名）
    _KNOWN_COLORS: frozenset[str] = frozenset({
        'red', 'green', 'blue', 'yellow', 'cyan', 'magenta',
        'white', 'black', 'grey30', 'grey50', 'bright_red',
        'bright_green', 'bright_blue', 'bright_yellow',
        'bright_cyan', 'bright_magenta', 'bright_white',
        'orange1', 'purple', 'pink1',
    })

    def _try_color_text(self, depth: int) -> InlineNode | None:
        """{color:COLOR}...{color} → ColorTextNode。

        检测 {color: 前缀 + 已知颜色名 + } 模式。
        """
        try:
            if not (self._pos + 7 < self._n
                    and self._text[self._pos:self._pos + 7] == '{color:'):
                return None
            # 查找 } 结束标记提取颜色名
            saved = self._pos
            color_start = self._pos + 7
            color_end = self._text.find('}', color_start)
            if color_end < 0 or color_end - color_start > 20:
                return None
            color_name = self._text[color_start:color_end].lower()
            if color_name not in self._KNOWN_COLORS:
                return None
            self._pos = color_end + 1  # skip {color:COLOR}
            children, found = self._parse_until('{color}', depth + 1)
            if found:
                self._pos += 7  # skip {color}
                text = render_inline_to_text(children)
                return ColorTextNode(content=text, children=children, color=color_name)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_color_text 异常，降级处理", exc_info=True)
            return None

    # ── CriticMarkup {~~old~>new~~} 替换 ─────────────────

    def _try_critic_substitution(self, depth: int) -> InlineNode | None:
        """{~~old~>new~~} → CriticSubstitutionNode（删除线旧文本 + 绿色插入新文本）。

        解析为 children=旧文本节点列表, meta['new_children']=新文本节点列表。
        ★ P0 修复: 支持反斜杠转义分隔符 ~>，防止内容中的 ~> 被错误分割。
        """
        try:
            if not (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 3] == '{~~'
                    and not (self._pos + 4 < self._n
                             and self._text[self._pos + 3] == '~')):
                return None
            saved = self._pos
            self._pos += 3  # skip {~~
            # 在内容中查找未转义的 ~> 分隔符（支持 \~> 转义）
            old_children, found_sep = self._parse_until_unescaped('~>', depth + 1, escape_char='\\')
            if not found_sep or not old_children:
                self._pos = saved
                return None
            self._pos += 2  # skip ~>
            new_children, found_close = self._parse_until('~~}', depth + 1)
            if not found_close:
                self._pos = saved
                return None
            self._pos += 3  # skip ~~}
            old_text = render_inline_to_text(old_children)
            new_text = render_inline_to_text(new_children)
            full_text = f"{old_text}→{new_text}"
            return CriticSubstitutionNode(
                content=full_text,
                children=old_children,
                meta={"new_children": new_children},
            )
        except Exception:
            _logger.debug("_try_critic_substitution 异常，降级处理", exc_info=True)
            return None

    def _parse_until_unescaped(self, delim: str, depth: int, escape_char: str = '\\'
                               ) -> tuple[list[InlineNode], bool]:
        """解析直到遇到未转义的分隔符，支持 escape_char 转义。

        Returns:
            (children, found) — found=True 表示找到分隔符（含转义处理）。
        """
        if depth > self._MAX_DEPTH:
            return [TextNode(content=self._text[self._pos:])], False
        nodes: list[InlineNode] = []
        plain_buf: list[str] = []
        delim_len = len(delim)

        def _emit_plain():
            if plain_buf:
                nodes.append(TextNode(content=''.join(plain_buf)))
                plain_buf.clear()

        while self._pos < self._n:
            # 转义字符：跳过下一个字符作为字面量
            if escape_char and self._text[self._pos] == escape_char:
                if self._pos + 1 < self._n:
                    plain_buf.append(self._text[self._pos + 1])
                    self._pos += 2
                else:
                    plain_buf.append(escape_char)
                    self._pos += 1
                continue
            # 检查分隔符
            if self._pos + delim_len <= self._n and self._text[self._pos:self._pos + delim_len] == delim:
                _emit_plain()
                return nodes, True
            # 尝试内联格式
            node = self._try_format(depth)
            if node is not None:
                _emit_plain()
                nodes.append(node)
                continue
            plain_buf.append(self._text[self._pos])
            self._pos += 1
        _emit_plain()
        return nodes, False

    # ── CriticMarkup {>>comment<<} 批注 ─────────────────

    def _try_critic_comment(self, depth: int) -> InlineNode | None:
        """{>>...<<} → CriticCommentNode（批注/注释文本）。"""
        try:
            if not (self._pos + 3 < self._n
                    and self._text[self._pos:self._pos + 3] == '{>>'):
                return None
            saved = self._pos
            self._pos += 3  # skip {>>
            children, found = self._parse_until('<<}', depth + 1)
            if found:
                self._pos += 3  # skip <<}
                text = render_inline_to_text(children)
                return CriticCommentNode(content=text, children=children)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_critic_comment 异常，降级处理", exc_info=True)
            return None

    # ── Wiki 链接 [[target]] 或 [[target|display]] ──────────

    def _try_wikilink(self, depth: int) -> InlineNode | None:
        """[[target]] 或 [[target|display]] → WikiLinkNode。

        双 [[ 开头，匹配到 ]] 结束。
        支持 | 分隔 display 文本。
        """
        try:
            if not (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 2] == '[['):
                return None
            saved = self._pos
            self._pos += 2  # skip [[
            # 扫描到 ]] ，收集内容
            content_start = self._pos
            while self._pos < self._n:
                if (self._pos + 1 < self._n
                        and self._text[self._pos:self._pos + 2] == ']]'):
                    content = self._text[content_start:self._pos]
                    self._pos += 2  # skip ]]
                    # 解析 target 和可选的 display
                    pipe_idx = content.find('|')
                    if pipe_idx >= 0:
                        target = content[:pipe_idx].strip()
                        display = content[pipe_idx + 1:].strip() or None
                    else:
                        target = content.strip()
                        display = None
                    if target:
                        return WikiLinkNode(
                            content=display or target,
                            target=target,
                            display=display,
                        )
                    self._pos = saved
                    return None
                self._pos += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_wikilink 异常，降级处理", exc_info=True)
            return None

    # ── 行内注释 %% comment %% ────────────────────────────

    def _try_inline_comment(self, depth: int) -> InlineNode | None:
        """%% comment %% → InlineCommentNode（隐藏文本）。

        规则：
        - 开头 %% 不能紧跟第三个 %（避免 %%% 混淆）
        - 开头 %% 不能紧跟在前一个 % 之后（避免 x%%% 中 2-3位触发）
        - 结尾 %% 不能紧跟在前一个 % 之后（避免 %%text%%% 中末三位触发）
        """
        try:
            if not (self._pos + 4 < self._n
                    and self._text[self._pos:self._pos + 2] == '%%'
                    and not (self._pos + 3 < self._n
                             and self._text[self._pos + 2] == '%')):
                return None
            # ★ 修复: 开头 %% 不能紧跟在前一个 % 之后（如 text%%% → 位置5的%%不应触发）
            if self._pos > 0 and self._text[self._pos - 1] == '%':
                return None
            saved = self._pos
            self._pos += 2  # skip %%
            content_start = self._pos
            while self._pos < self._n:
                if (self._pos + 1 < self._n
                        and self._text[self._pos:self._pos + 2] == '%%'
                        and not (self._pos + 2 < self._n
                                 and self._text[self._pos + 2] == '%')):
                    # ★ 修复: 结尾 %% 不能紧跟在前一个 % 之后（如 %%text%%% → 末三位）
                    if self._pos > 0 and self._text[self._pos - 1] == '%':
                        self._pos += 1
                        continue
                    content = self._text[content_start:self._pos]
                    self._pos += 2  # skip %%
                    return InlineCommentNode(content=content)
                self._pos += 1
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_inline_comment 异常，降级处理", exc_info=True)
            return None
