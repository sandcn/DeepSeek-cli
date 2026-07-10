"""_InlineHtmlMixin — WebRenderTarget 内联 Markdown → HTML 渲染。

将 WebRenderTarget 中所有内联格式渲染方法提取为一个 Mixin。
负责将 Markdown 内联格式（粗体、斜体、代码、链接等）转换为 HTML。
"""

from __future__ import annotations

import html as html_mod

from ..emoji_map import resolve_emoji as _resolve_emoji


class _InlineHtmlMixin:
    """内联 Markdown → HTML 渲染 Mixin。

    依赖宿主类 WebRenderTarget 提供的 _is_safe_url 静态方法。
    """

    # URL 协议白名单（防止 javascript:/data:/vbscript: 等 XSS 注入）
    _ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto", "ftp"})

    # ═══════════════════════════════════════════════════════
    # 内联 Markdown → HTML 渲染
    # ═══════════════════════════════════════════════════════

    def render_inline(self, text: str) -> str:
        """将内联 Markdown 渲染为 HTML。

        支持：**bold**, *italic*, `code`, [link](url),
        ~~strikethrough~~, ==highlight==, 裸 URL, Emoji 等。
        """
        if not text:
            return ""

        text = self._escape_html(text)
        text = self._resolve_emoji_html(text)
        result = self._apply_inline_formatting(text)
        return result

    # ═══════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _escape_html(text: str) -> str:
        """HTML 转义（保留 &, <, >, ", '）。"""
        return html_mod.escape(text, quote=True)

    @staticmethod
    def _resolve_emoji_html(text: str) -> str:
        """解析 Emoji 短代码为 Unicode Emoji 或 HTML span。"""
        return _resolve_emoji(text)

    # ═══════════════════════════════════════════════════════
    # 字符串内联格式解析（不使用 re 模块）
    # ═══════════════════════════════════════════════════════

    def _apply_inline_formatting(self, text: str) -> str:
        """应用内联格式标记，将 Markdown 格式转换为 HTML。"""
        if not text:
            return text

        result = self._replace_delimited(text, '***', '***',
                                          lambda c: f'<strong><em>{c}</em></strong>')
        result = self._replace_delimited(result, '**', '**',
                                          lambda c: f'<strong>{c}</strong>')
        result = self._replace_italic(result)
        result = self._replace_delimited(result, '==', '==',
                                          lambda c: f'<mark>{c}</mark>')
        result = self._replace_delimited(result, '`', '`',
                                          lambda c: f'<code>{c}</code>')
        result = self._replace_delimited(result, '~~', '~~',
                                          lambda c: f'<del>{c}</del>')
        result = self._replace_links(result)
        result = self._replace_images(result)
        result = self._replace_bare_urls(result)

        return result

    @staticmethod
    def _replace_delimited(text: str, open_delim: str, close_delim: str,
                           formatter) -> str:
        """替换成对分隔符包裹的内容。"""
        result = []
        i = 0
        while i < len(text):
            open_idx = text.find(open_delim, i)
            if open_idx == -1:
                result.append(text[i:])
                break
            if open_idx > 0 and text[open_idx - 1] == '\\':
                result.append(text[i:open_idx - 1])
                result.append(open_delim)
                i = open_idx + len(open_delim)
                continue
            result.append(text[i:open_idx])
            content_start = open_idx + len(open_delim)
            close_idx = text.find(close_delim, content_start)
            if close_idx == -1:
                result.append(text[open_idx:])
                break
            content = text[content_start:close_idx]
            result.append(formatter(content))
            i = close_idx + len(close_delim)
        return ''.join(result)

    @staticmethod
    def _replace_italic(text: str) -> str:
        """替换 *italic*，排除 **bold** 和 ***bold italic*** 中的星号。"""
        result = []
        i = 0
        while i < len(text):
            asterisk = text.find('*', i)
            if asterisk == -1:
                result.append(text[i:])
                break
            count = 0
            j = asterisk
            while j < len(text) and text[j] == '*':
                count += 1
                j += 1
            if count >= 2:
                result.append(text[i:j])
                i = j
                continue
            if asterisk > 0 and text[asterisk - 1] == '\\':
                result.append(text[i:asterisk - 1])
                result.append('*')
                i = asterisk + 1
                continue
            result.append(text[i:asterisk])
            content_start = asterisk + 1
            close_found = False
            k = content_start
            while k < len(text):
                if text[k] == '*' and (k + 1 >= len(text) or text[k + 1] != '*'):
                    content = text[content_start:k]
                    result.append(f'<em>{content}</em>')
                    i = k + 1
                    close_found = True
                    break
                k += 1
            if not close_found:
                result.append(text[asterisk:])
                break
        return ''.join(result)

    @staticmethod
    def _replace_links(text: str) -> str:
        """替换 [text](url) 为 <a href="url">text</a>。"""
        result = []
        i = 0
        while i < len(text):
            bracket_open = text.find('[', i)
            if bracket_open == -1:
                result.append(text[i:])
                break
            if bracket_open > 0 and text[bracket_open - 1] == '\\':
                result.append(text[i:bracket_open - 1])
                result.append('[')
                i = bracket_open + 1
                continue
            result.append(text[i:bracket_open])
            bracket_close = text.find(']', bracket_open)
            if bracket_close == -1:
                result.append(text[bracket_open:])
                break
            paren_open = text.find('(', bracket_close)
            if paren_open != bracket_close + 1:
                result.append('[')
                i = bracket_open + 1
                continue
            paren_close = text.find(')', paren_open)
            if paren_close == -1:
                result.append(text[bracket_open:])
                break
            link_text = text[bracket_open + 1:bracket_close]
            url = text[paren_open + 1:paren_close]
            safe_url = url if _InlineHtmlMixin._is_safe_url(url) else "#"
            result.append(f'<a href="{html_mod.escape(safe_url, quote=True)}">{link_text}</a>')
            i = paren_close + 1
        return ''.join(result)

    @staticmethod
    def _replace_images(text: str) -> str:
        """替换 ![alt](url) 为 <img src="url" alt="alt">。"""
        result = []
        i = 0
        while i < len(text):
            bang = text.find('![', i)
            if bang == -1:
                result.append(text[i:])
                break
            if bang > 0 and text[bang - 1] == '\\':
                result.append(text[i:bang - 1])
                result.append('![')
                i = bang + 2
                continue
            result.append(text[i:bang])
            bracket_close = text.find(']', bang + 2)
            if bracket_close == -1:
                result.append(text[bang:])
                break
            paren_open = text.find('(', bracket_close)
            if paren_open != bracket_close + 1:
                result.append('![')
                i = bang + 2
                continue
            paren_close = text.find(')', paren_open)
            if paren_close == -1:
                result.append(text[bang:])
                break
            alt_text = text[bang + 2:bracket_close]
            url = text[paren_open + 1:paren_close]
            safe_url = url if _InlineHtmlMixin._is_safe_url(url) else ""
            result.append(f'<img src="{html_mod.escape(safe_url, quote=True)}" alt="{alt_text}">')
            i = paren_close + 1
        return ''.join(result)

    @staticmethod
    def _replace_bare_urls(text: str) -> str:
        """替换裸 URL（http/https）为 <a href="url">url</a>。"""
        result = []
        i = 0
        url_end_chars = set(' <>"\\]().，、！？；：】》》）)—–—\n\r\t')
        while i < len(text):
            http_idx = text.find('http', i)
            if http_idx == -1:
                result.append(text[i:])
                break
            prefix = text[max(0, http_idx - 10):http_idx]
            if 'href="' in prefix or 'href=\'' in prefix:
                result.append(text[i:http_idx + 4])
                i = http_idx + 4
                continue
            result.append(text[i:http_idx])
            url_end = http_idx
            while url_end < len(text) and text[url_end] not in url_end_chars:
                url_end += 1
            url = text[http_idx:url_end]
            while url and url[-1] in ',.;:!?)]':
                url = url[:-1]
                url_end -= 1
            if url:
                safe_url = url if _InlineHtmlMixin._is_safe_url(url) else "#"
                result.append(f'<a href="{html_mod.escape(safe_url, quote=True)}">{html_mod.escape(safe_url, quote=True)}</a>')
            i = url_end
        return ''.join(result)

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """检查 URL 是否使用白名单协议，防止 XSS。

        只允许 http/https/mailto/ftp 协议，
        javascript:/data:/vbscript: 等协议直接拦截。
        """
        url = url.strip()
        if ':' not in url:
            return True
        scheme = url.split(':', 1)[0].lower()
        return scheme in _InlineHtmlMixin._ALLOWED_URL_SCHEMES
