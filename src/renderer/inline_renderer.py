"""inline_renderer — 内联 Markdown → Rich Text 渲染器（无正则版本）。

使用 recursive_parser 中的 _InlineParser（真正无正则的递归下降解析器）
解析内联 Markdown，然后将 InlineNode 树转换为 Rich Text。

职责：
  - 将内联 Markdown 渲染为 Rich Text（Text 对象）
  - 支持：粗体/斜体/高亮/代码/链接/图片/emoji/HTML标签/数学/自动链接
  - 所有预处理（emoji、HTML实体）均为字符级操作，无正则表达式

拆分说明：
  - _inline_preprocess.py — 预处理函数（_has_inline_format, _preprocess_text）
  - _inline_handlers.py   — 节点类型→处理函数调度表（构建函数 _build_dispatch_table）
  - _process_and_linkify: 纯文本快速通道，委托 _preprocess_text 处理 emoji/实体
"""

from __future__ import annotations

import functools

from rich.text import Text
from rich.style import Style

from ._utils import (
    _scan_next_url_or_email,
)
from .emoji_map import EMOJI_MAP
from ._inline_preprocess import (
    _MAX_RECURSION_DEPTH,
    _INLINE_FORMAT_CHARS,
    _has_inline_format,
    _preprocess_text,
)
from ._inline_handlers import _build_dispatch_table
from .inline_nodes import InlineNode


# ── 模块级常量 ──────────────────────────────────────────
# 纯文本链接候选最小长度阈值（短于此长度且无./@特征时跳过链接检测）
_MIN_LINK_CANDIDATE_LEN = 5


# ── 模块级工具函数 ──────────────────────────────────────

def _detect_url_prefix(text: str, pos: int) -> tuple[str, int] | None:
    """检测文本 pos 处是否为 URL/Email 协议前缀。

    返回 (prefix_type, prefix_len) — prefix_type 为
    'http'/'https'/'ftp'/'ftps'/'www'/'email'，
    prefix_len 为前缀的字符长度；无可匹配前缀时返回 None。
    """
    n = len(text)
    if pos >= n:
        return None

    ch = text[pos]

    if ch == '@':
        return ('email', 1)

    if ch in 'hH':
        lower = text[pos:pos + 6].lower()
        if lower == 'https:':
            return ('https', 6)
        if lower.startswith('http:'):
            return ('http', 5)
    elif ch in 'fF':
        lower = text[pos:pos + 5].lower()
        if lower == 'ftps:':
            return ('ftps', 5)
        if lower.startswith('ftp:'):
            return ('ftp', 4)
    elif ch in 'wW':
        if text[pos:pos + 4].lower() == 'www.':
            return ('www', 4)

    return None


class InlineRenderer:
    """内联 Markdown → Rich Text 渲染器（线程安全，无状态）。"""

    # 节点类型 → 处理函数调度表（由 _build_dispatch_table 在模块加载时构建）
    _NODE_DISPATCH: dict = {}

    def render(self, text: str, ctx=None) -> Text:
        """渲染内联 Markdown 为 Rich Text。

        Args:
            text: 内联 Markdown 文本
            ctx: 可选的 RenderContext（用于脚注/引用链接解析）

        Returns:
            渲染后的 Rich Text
        """
        if not text:
            return Text()

        # 纯文本快速通道：不含任何格式标记 → 缓存单遍扫描 Emoji + HTML实体 + URL/Email
        if not _has_inline_format(text):
            result = self._process_and_linkify(text)
            if ctx and hasattr(ctx, 'abbr_map') and ctx.abbr_map:
                result = self._apply_abbreviations(result, ctx)
            return result

        # 含内联格式标记：直接交由 _InlineParser 解析（它原生支持 emoji 和 HTML 实体）
        from .inline_parser import _InlineParser
        parser = _InlineParser(text)
        nodes = parser.parse()

        # 转换 InlineNode 树 → Rich Text
        result = self._nodes_to_rich(nodes, ctx, 0)
        if ctx and hasattr(ctx, 'abbr_map') and ctx.abbr_map:
            result = self._apply_abbreviations(result, ctx)
        return result

    def _nodes_to_rich(self, nodes: list, ctx, _depth: int) -> Text:
        """将 InlineNode 列表转换为 Rich Text。"""
        result = Text()
        for node in nodes:
            rich = self._node_to_rich(node, ctx, _depth)
            if rich:
                result.append_text(rich)
        return result

    def _node_to_rich(self, node, ctx, _depth: int) -> Text | None:
        """将单个 InlineNode 转换为 Rich Text（类型调度表，O(1) 查找）。"""
        # 递归深度守卫，防止过度递归导致 RecursionError
        if _depth >= _MAX_RECURSION_DEPTH:
            return Text(str(node.content)) if node.content else Text("")
        handler = self._NODE_DISPATCH.get(type(node))
        if handler is not None:
            return handler(self, node, ctx, _depth)
        # fallback
        if isinstance(node, InlineNode):
            return Text(node.content)
        raise TypeError(f"不支持的节点类型: {type(node).__name__}")

    # ── F3 缩写定义自动替换 ──────────────────────────────

    def _apply_abbreviations(self, text: Text, ctx) -> Text:
        """扫描 Text 中的缩写词，替换为带样式的文本。

        abbr_map 中的 key 为大写缩写，匹配时忽略大小写。
        """
        if not text or not text.plain:
            return text
        abbr_map = ctx.abbr_map
        if not abbr_map:
            return text

        plain = text.plain
        n = len(plain)
        result = Text()
        i = 0

        # ★ 修复 Bug: Rich Text 类没有 get_style_at 方法，hasattr 永远 False。
        #    改为从 text.spans 预先构建 offset→style 映射。
        def _get_style_at_offset(offset: int):
            """从 Text 的 spans 中获取指定偏移的合并样式。"""
            combined = text.style or Style()
            for span in text.spans:
                if span.start <= offset < span.end:
                    s = span.style if isinstance(span.style, Style) else Style.parse(span.style)
                    combined = Style.combine([combined, s])
            return combined

        while i < n:
            # 跳过非字母数字字符，保留原始样式
            if not plain[i].isalnum():
                result.append(plain[i], style=_get_style_at_offset(i))
                i += 1
                continue
            # 查找单词边界
            start = i
            while i < n and plain[i].isalnum():
                i += 1
            word = plain[start:i]
            # 检查是否在缩写列表中
            upper_word = word.upper()
            if upper_word in abbr_map:
                styled = Text(word, style=Style(color="yellow", underline=True, italic=True))
                result.append_text(styled)
            else:
                result.append(word)
        return result

    # ── 裸 URL/Email 链接化（字符级扫描，无正则） ──────────

    def _linkify_text(self, text: str) -> Text:
        """链接化文本中的 URL 和 Email 地址（与 _process_and_linkify 共享扫描器）。

        使用统一的 _scan_next_url_or_email 扫描器，与 _process_and_linkify
        保持一致的 URL/Email 检测逻辑。

        Args:
            text: 纯文本（不含 Markdown 格式标记）

        Returns:
            带链接样式的 Rich Text
        """
        if not text:
            return Text()

        # ☆ 优化：极短文本快速跳过（<5字符且不含 '.' 和 '@'，99.9% 不含 URL/Email）
        if len(text) < _MIN_LINK_CANDIDATE_LEN and '.' not in text and '@' not in text:
            return Text(text)

        # 快速跳过：不包含 URL 或 Email 特征字符
        if '://' not in text and 'www.' not in text and '@' not in text:
            return Text(text)

        result = Text()
        pos = 0
        n = len(text)

        while pos < n:
            # 快速退出：剩余文本不含 URL/Email 特征，避免无效全量扫描
            remaining = text[pos:]
            if '://' not in remaining and 'www.' not in remaining and '@' not in remaining:
                result.append(remaining)
                break

            found = _scan_next_url_or_email(text, pos)
            if found is None:
                result.append(text[pos:])
                break

            start, end, content, kind = found
            if start > pos:
                result.append(text[pos:start])

            if kind == 'url':
                result.append(content, style=Style(color="cyan", underline=True))
            else:
                result.append(content, style=Style(color="cyan", underline=True, italic=True))

            pos = end

        return result

    # ── 合并预处理+链接化（单遍扫描，替代 _linkify_text） ──────

    @staticmethod
    @functools.lru_cache(maxsize=256)
    def _cached_process_and_linkify(text: str) -> Text:
        """缓存版本的纯文本快速通道：Emoji替换 + HTML实体解码 + URL/Email链接化 + 智能排版。

        使用 lru_cache 缓存相同文本的渲染结果，避免重复扫描。
        仅用于不含内联格式标记的纯文本路径。
        """
        if not text:
            return Text()

        # 快速跳过：不包含任何需要处理的字符
        if (':' not in text and '&' not in text and '--' not in text and '...' not in text
                and '://' not in text and 'www.' not in text and '@' not in text
                and '->' not in text and '<-' not in text and '=>' not in text
                and '<->' not in text and '<=' not in text and '>=' not in text
                and '!=' not in text and '~=' not in text and '+-' not in text
                and '+/-' not in text
                and '==>' not in text and '<==' not in text and '<==>' not in text
                and '(c)' not in text.lower() and '(r)' not in text.lower()
                and '(tm)' not in text.lower()
                and '1/2' not in text and '1/4' not in text and '3/4' not in text
                and '/3' not in text and '/5' not in text and '/6' not in text and '/8' not in text):
            return Text(text)

        # ★ 预处理：Emoji 短代码 + HTML 实体 + 智能排版
        #   触发条件包含所有智能排版特性（-- → –, --- → —, ... → …, -> → →,
        #   <- → ←, => → ⇒, <-> → ↔, <= → ≤, >= → ≥, != → ≠, ~= → ≈, +- → ±,
        #   ==> → ⟹, <== → ⟸, <==> → ⟺,
        #   (c) → ©, (r) → ®, (tm) → ™, 1/2 → ½, 1/4 → ¼, 3/4 → ¾）
        if (':' in text or '&' in text or '--' in text or '...' in text
                or '->' in text or '<-' in text or '=>' in text
                or '<->' in text or '<=' in text or '>=' in text
                or '!=' in text or '~=' in text or '+-' in text
                or '+/-' in text
                or '==>' in text or '<==' in text or '<==>' in text
                or '(c)' in text.lower() or '(r)' in text.lower()
                or '(tm)' in text.lower()
                or '1/2' in text or '1/4' in text or '3/4' in text
                or '/3' in text or '/5' in text or '/6' in text or '/8' in text):
            text = _preprocess_text(text)

        # 预处理后再次快速跳过 URL/Email 扫描
        if '://' not in text and 'www.' not in text and '@' not in text:
            return Text(text)

        result = Text()
        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            # ── URL 或 Email 候选字符（合并扫描，单遍） ──
            if ch in 'hHfFwW@' and i + 3 < n:
                if _detect_url_prefix(text, i) is not None:
                    # ★ 修复 Bug: Email 检测需要 start 在 @ 之前，
                    #    否则 _scan_next_url_or_email 内 local_start > start 永远为 False
                    scan_start = max(0, i - 64) if ch == '@' else i
                    info = _scan_next_url_or_email(text, scan_start)
                    if info is not None:
                        url_start, url_end, url_text, url_type = info
                        # ★ 修复：Email 可能从 i 之前开始（local part 已被逐字符追加），
                        #    需回溯已追加的字符后重新附加带样式的完整 Email/URL。
                        if url_start <= i < url_end:
                            if url_start < i:
                                # 回溯：移除 url_start..i-1 已逐字符追加的 plain text
                                result = Text(result.plain[:url_start])
                            if url_type == 'url':
                                result.append(url_text, style=Style(color="cyan", underline=True))
                            else:
                                result.append(url_text, style=Style(color="cyan", underline=True, italic=True))
                            i = url_end
                            continue

            # ── 普通字符 ──
            result.append(ch)
            i += 1

        # ★ 修复 Bug: lru_cache 返回可变 Text 对象，调用方可能修改缓存值。
        #    copy() 已移至 _process_and_linkify，确保每次调用返回独立副本。
        return result

    def _process_and_linkify(self, text: str) -> Text:
        """单遍扫描：Emoji替换 + HTML实体解码 + URL/Email链接化。"""
        return self._cached_process_and_linkify(text).copy()


# ── 构建调度表（类定义完成后执行） ────────────────────────
InlineRenderer._NODE_DISPATCH = _build_dispatch_table()


# ── 单例（可选，减少实例化开销） ────────────────────────────
_DEFAULT_RENDERER = InlineRenderer()


def render_inline(text: str, ctx=None) -> Text:
    """便捷函数——使用默认 InlineRenderer 渲染内联 Markdown。"""
    return _DEFAULT_RENDERER.render(text, ctx)
