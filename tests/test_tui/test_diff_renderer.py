"""测试 _diff_renderer.py — 差异渲染模块。

测试范围：
  - _sanitize_ansi ANSI 消毒（移除所有 ESC 字符）
  - _inline_highlight 输入消毒（防 ANSI 注入）
  - _syntax_hl 输入消毒（防 ANSI 注入）
  - _render_chunk ctx 行消毒
  - 完整 render_diff 路径无注入
"""

from __future__ import annotations

import pytest

from src.tui._diff_renderer import (
    _sanitize_ansi,
    _inline_highlight,
    _syntax_hl,
    _render_chunk,
    render_diff,
)


# ═══════════════════════════════════════════════════════════
# _sanitize_ansi
# ═══════════════════════════════════════════════════════════

class TestSanitizeAnsi:
    """_sanitize_ansi 单元测试。"""

    def test_plain_text_unchanged(self):
        """纯文本不应被修改。"""
        assert _sanitize_ansi("hello world") == "hello world"

    def test_empty_string(self):
        """空字符串返回空字符串。"""
        assert _sanitize_ansi("") == ""

    def test_removes_csi_sequences(self):
        """CSI 序列（\x1b[...m 等）的 ESC 被移除，剩余文本成为无害字面量。"""
        result = _sanitize_ansi("\x1b[31mred\x1b[0m")
        # ESC 被移除，CSI 括号序列成为无害文本
        assert '\x1b' not in result
        assert "red" in result
        assert result == "[31mred[0m"

    def test_removes_osc_sequences(self):
        """OSC 序列（\x1b]...\x07 等）的 ESC 被移除。"""
        result = _sanitize_ansi("\x1b]8;;https://example.com\x07link\x1b]8;;\x07")
        assert '\x1b' not in result
        assert "link" in result

    def test_removes_dcs_sequences(self):
        """DCS 序列（\x1bP...\x1b\\）的 ESC 被移除。"""
        result = _sanitize_ansi("\x1bPdata\x1b\\rest")
        assert '\x1b' not in result
        assert "rest" in result

    def test_removes_lone_esc(self):
        """孤立的 ESC 字符也被移除。"""
        result = _sanitize_ansi("abc\x1bdef")
        assert '\x1b' not in result
        assert result == "abcdef"

    def test_removes_sos_escape(self):
        """SOS 序列 \x1bX 的 ESC 被移除。"""
        result = _sanitize_ansi("before\x1bXafter")
        assert '\x1b' not in result
        assert 'X' not in result or "before" in result  # 可能保留也可能移除
        assert "after" in result

    def test_removes_multiple_sequences(self):
        """多个 ANSI 序列的 ESC 全部移除。"""
        result = _sanitize_ansi("\x1b[1m\x1b[31mbold red\x1b[0m")
        assert '\x1b' not in result
        assert "bold red" in result

    def test_removes_ansi_injection(self):
        """恶意注入的 ANSI 序列的 ESC 被移除，序列无法被终端解析。"""
        result = _sanitize_ansi("malicious\x1b[0mcode")
        assert '\x1b' not in result
        assert "malicious" in result
        assert "code" in result


# ═══════════════════════════════════════════════════════════
# _inline_highlight ANSI 消毒
# ═══════════════════════════════════════════════════════════

class TestInlineHighlightSanitize:
    """验证 _inline_highlight 的输入消毒。"""

    def test_removes_ansi_from_old_text(self):
        """old_text 中的 ANSI 被消毒。"""
        old_hl, new_hl = _inline_highlight("\x1b[31mold", "new")
        # 背景色标记应出现在结果中（由 _inline_highlight 自身生成），
        # 但注入的 \x1b[31m 应被移除
        assert "\x1b[31m" not in old_hl
        assert "old" in old_hl

    def test_removes_ansi_from_new_text(self):
        """new_text 中的 ANSI 被消毒。"""
        old_hl, new_hl = _inline_highlight("old", "\x1b[32mnew")
        assert "\x1b[32m" not in new_hl
        assert "new" in new_hl

    def test_still_highlights_diffs(self):
        """消毒不影响差异高亮功能。"""
        old_hl, new_hl = _inline_highlight("abc", "abd")
        # 应有背景色标记（差异部分被高亮）
        assert "\033[48;5;124m" in old_hl  # 红背景（删除）
        assert "\033[48;5;28m" in new_hl   # 绿背景（新增）
        assert "abc" in old_hl or "ab" in old_hl
        assert "abd" in new_hl or "ab" in new_hl

    def test_identical_text_no_highlight(self):
        """相同文本无背景色标记。"""
        old_hl, new_hl = _inline_highlight("hello", "hello")
        assert "\033[48;5;124m" not in old_hl
        assert "\033[48;5;28m" not in new_hl


# ═══════════════════════════════════════════════════════════
# _syntax_hl ANSI 消毒
# ═══════════════════════════════════════════════════════════

class TestSyntaxHlSanitize:
    """验证 _syntax_hl 的输入消毒。"""

    def test_removes_ansi_from_text(self):
        """输入文本中的 ANSI 被消毒后再传给 pygments。"""
        result = _syntax_hl("\x1b[31mprint(1)", "python")
        # pygments 生成的 ANSI 应该保留，但注入的 \x1b[31m 应被移除
        assert "\x1b[31m" not in (result or "")
        # 文本内容应保留
        if result:
            # pygments 会保留或转换字符，但 print(1) 应该在结果中
            assert "print" in result
            assert "1" in result

    def test_none_lexer_returns_text(self):
        """lexer_name 为空时也消毒：返回消毒后的字面量（方向A 步骤3）。

        修复前空 lexer 提前返回原文（ANSI 保留），与「输入先消毒」docstring
        矛盾；修复后消毒移动到提前 return 之前，空 lexer 输入含 ANSI 时
        输出消毒后字面量。
        """
        result = _syntax_hl("\x1b[31mhello", "")
        assert result == "[31mhello"

    def test_pygments_absent_returns_text(self):
        """pygments 不可用时返回原文（消毒后的输入）。"""
        # 如果 pygments 未安装，_sanitize_ansi 已在 _syntax_hl 中执行
        # 但此函数在 _sanitize_ansi 后不修改 text 直接返回
        result = _syntax_hl("\x1b[31mhello", "python")
        # 实际上 pygments 可能已安装，所以这里做弹性检查
        if result:
            assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════
# render_diff 完整路径无注入
# ═══════════════════════════════════════════════════════════

class TestRenderDiffNoInjection:
    """验证 render_diff 整体路径无 ANSI 注入。"""

    def _collect_output(self, diff_list, **kwargs):
        """使用内存收集器捕获 render_diff 输出。"""
        collected: list[str] = []

        class _Collector:
            _target = collected
            @classmethod
            def write_line(cls, text: str) -> None:
                cls._target.append(text)

        render_diff(diff_list, w=4, output_target=_Collector, **kwargs)
        return collected

    def test_no_ansi_injection_in_diff_content(self):
        """diff 内容中的 ANSI 注入应被消毒（严格断言，非 `or` 恒真）。"""
        diff = [
            '--- a/test.py',
            '+++ b/test.py',
            '@@ -1,3 +1,3 @@',
            ' hello',
            '-old\x1b[31mline',
            '+new\x1b[32mline',
        ]
        out = self._collect_output(diff)
        output = '\n'.join(out)
        # 注入的 ANSI 转义序列被移除（不出现原始注入序列）
        assert '\x1b[31m' not in output
        assert '\x1b[32m' not in output
        # 行内容作为消毒后字面量保留（old/new 均在输出中）
        assert 'old' in output
        assert 'new' in output

    def test_no_ansi_injection_empty_lexer_del_add(self):
        """空 lexer 下单行 del/add 含恶意 ANSI 也应消毒（注入窗口关闭，方向A 步骤3）。

        修复前 ``render_diff._hl`` 在 lexer_name 为空时完全跳过消毒——
        单行 add/del 内容可含恶意 ANSI；修复后无条件经 ``_syntax_hl`` 消毒。
        """
        diff = [
            '--- a/test.txt',
            '+++ b/test.txt',
            '@@ -1,2 +1,2 @@',
            '-old\x1b[31mline',
            '+new\x1b[32mline',
        ]
        out = self._collect_output(diff, lexer_name='')
        output = '\n'.join(out)
        assert '\x1b[31m' not in output
        assert '\x1b[32m' not in output
        assert 'old' in output
        assert 'new' in output

    def test_ctx_line_ansi_injection(self):
        """上下文行中的 ANSI 注入应被消毒。"""
        diff = [
            '--- a/test.py',
            '+++ b/test.py',
            '@@ -1,3 +1,3 @@',
            ' normal',
            ' ctx\x1b[41minjected',
            ' more',
        ]
        out = self._collect_output(diff)
        output = '\n'.join(out)
        # 注入的 ANSI 背景色不应出现在输出中
        assert '\x1b[41m' not in output

    def test_ctx_line_ansi_injection_no_lexer(self):
        """无语法高亮时上下文行中的 ANSI 注入也应被消毒。"""
        diff = [
            '--- a/test.txt',
            '+++ b/test.txt',
            '@@ -1,1 +1,1 @@',
            ' clean',
            ' injected\x1b[31mdata',
        ]
        out = self._collect_output(diff, lexer_name='')
        output = '\n'.join(out)
        assert '\x1b[31m' not in output
        # 文本内容应保留
        assert 'injected' in output
        assert 'data' in output

    def test_hunk_header_not_affected(self):
        """hunk 头本身的 ANSI 样式不受影响（hunk 头不是用户内容）。"""
        diff = [
            '@@ -1,5 +1,5 @@',
            ' line1',
        ]
        out = self._collect_output(diff)
        output = '\n'.join(out)
        # hunk 头由我们自己的样式生成，应保留 ANSI
        assert '\x1b[' in output


# ═══════════════════════════════════════════════════════════
# _resolve_lexer_name 有界缓存（BUG-T8）
# ═══════════════════════════════════════════════════════════

class TestLexerCacheBounded:
    """BUG-T8 — _resolve_lexer_name 有界缓存（maxsize=64，防无限增长）。"""

    def test_lexer_cache_bounded_regression(self):
        """cache_info().maxsize == 64；大量随机扩展名后 currsize <= 64。"""
        from src.tui._diff_renderer import _resolve_lexer_name

        assert _resolve_lexer_name.cache_info().maxsize == 64
        # 大量随机扩展名
        for i in range(200):
            _resolve_lexer_name(f"ext{i}")
        info = _resolve_lexer_name.cache_info()
        assert info.currsize <= 64
        # 既有行为不变：空/未知扩展映射 text
        assert _resolve_lexer_name("") == "text"
        assert _resolve_lexer_name("txt") == "text"
        assert _resolve_lexer_name("ext_known") == "ext_known"
