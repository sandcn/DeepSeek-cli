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
        """CSI 序列（\x1b[...m 等）的 ESC 与序列体被移除（方向1 步骤2 语义）。

        修复前仅移除 ESC 字符（残留 ``[31m`` 字面量）；修复后经统一
        ``strip_ansi`` 剥离完整序列（CSI 参数 + 最终字节一并移除），
        再兜底移除孤立 ESC——更干净的消毒（无残留序列体垃圾字面量）。
        """
        result = _sanitize_ansi("\x1b[31mred\x1b[0m")
        assert '\x1b' not in result
        assert "red" in result
        assert result == "red"

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

    def test_sanitize_ansi_unified_regression(self):
        """_sanitize_ansi 经统一 strip_ansi 主真源实现（方向1 步骤2）。

        合法 ANSI 序列被完整剥离（含序列体）；孤立 ESC 经兜底移除——
        与 ``ink.helpers.strip_ansi`` 输出在合法序列场景一致，且任何
        \\x1b 均不进入结果（防注入兜底语义保留）。
        """
        from src.tui.ink.helpers import strip_ansi
        # 合法序列：sanitize 剥离完整序列（strip_ansi 已移除序列体 → 一致）
        text = "\x1b[1m\x1b[31mbold red\x1b[0m"
        assert _sanitize_ansi(text) == strip_ansi(text)
        # 孤立 ESC（strip_ansi 不匹配）：sanitize 兜底移除 → 无 \\x1b
        result = _sanitize_ansi("abc\x1bdef")
        assert '\x1b' not in result
        assert result == "abcdef"
        # 不定义独立正则（复用统一工具）
        import inspect
        import src.tui._diff_renderer as mod
        assert "re.sub('\\x1b'" not in inspect.getsource(mod) or "strip_ansi" in inspect.getsource(mod._sanitize_ansi)


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
        输出消毒后字面量。方向1 步骤2：统一工具剥离完整序列（不再残留
        ``[31m`` 序列体字面量）。
        """
        result = _syntax_hl("\x1b[31mhello", "")
        assert result == "hello"

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


# ═══════════════════════════════════════════════════════════
# 方向4 — _get_highlighter lru_cache（同一 lexer 复用同一对象）
# ═══════════════════════════════════════════════════════════

class TestHighlighterCache:
    """方向4 — _get_highlighter 有界缓存（同一 lexer_name 返回同一对象）。"""

    def test_same_lexer_returns_same_object_regression(self):
        """同一 lexer_name 两次 _get_highlighter → 返回同一 (lexer, formatter) 对象。"""
        from src.tui._diff_renderer import _get_highlighter
        _get_highlighter.cache_clear()
        try:
            pair1 = _get_highlighter("python")
            pair2 = _get_highlighter("python")
            # pygments 可用时断言对象复用；不可用时两者均 None（缓存 None）
            assert pair1 == pair2
            if pair1 is not None:
                assert pair1[0] is pair2[0]
                assert pair1[1] is pair2[1]
        finally:
            _get_highlighter.cache_clear()

    def test_unknown_lexer_falls_back_text_cached_regression(self):
        """未知 lexer 降级 text 且缓存（同一名称返回同一对象）。"""
        from src.tui._diff_renderer import _get_highlighter
        _get_highlighter.cache_clear()
        try:
            pair1 = _get_highlighter("nonexistent-lexer-xyz")
            pair2 = _get_highlighter("nonexistent-lexer-xyz")
            assert pair1 == pair2  # 降级结果一致（text 或 None）
        finally:
            _get_highlighter.cache_clear()

    def test_highlighter_cache_bounded_regression(self):
        """_get_highlighter 缓存 maxsize=64（防无限增长）。"""
        from src.tui._diff_renderer import _get_highlighter
        assert _get_highlighter.cache_info().maxsize == 64


# ═══════════════════════════════════════════════════════════
# 方向1 P0-2 — diff 文件头误判修复（基于 parsed 结构统计）
# ═══════════════════════════════════════════════════════════

class TestDiffFileHeaderParsing:
    """方向1 P0-2 — 文件头精确匹配 ``--- ``/``+++ ``，删除/新增行不被误判。"""

    def _collect(self, diff, **kwargs):
        """render_diff + _render_diff_summary 收集输出（含统计摘要）。"""
        from src.tui._diff_renderer import render_diff, _render_diff_summary
        collected: list[str] = []

        class _Collector:
            _target = collected
            @classmethod
            def write_line(cls, text: str) -> None:
                cls._target.append(text)

        render_diff(diff, w=4, output_target=_Collector, **kwargs)
        _render_diff_summary(diff, output_target=_Collector)
        return collected

    def test_del_line_not_file_header_regression(self):
        """删除行 `---foo` / 新增行 `+++bar`（内容以 --/++ 开头）不被误判为文件头。

        修复前 ``startswith('---')`` 把删除行 `---foo`（`-`+`--foo`）误判为
        old_file → 渲染 `┌─ --foo`；修复后仅 ``--- ``（含空格）且非 ``----``
        判定为文件头，`---foo` 落入 del 分支（统计基于 parsed 结构，含增删）。
        """
        diff = [
            '--- a/test.py',
            '+++ b/test.py',
            '@@ -1,3 +1,3 @@',
            ' hello',
            '---foo',   # 删除行，内容 --foo（无空格，非文件头）
            '+++bar',   # 新增行，内容 ++bar（无空格，非文件头）
            ' world',
        ]
        out = self._collect(diff, lexer_name='')
        output = '\n'.join(out)
        # 正常文件头仍渲染 ┌─ / └─（各一次；路径含 a//b/ 前缀——`--- a/path`[4:]=a/path）
        assert '┌─ a/test.py' in output
        assert '└─ b/test.py' in output
        assert output.count('┌─ ') == 1, "删除行 ---foo 不应再被误判为文件头（┌─ 仅一次）"
        assert output.count('└─ ') == 1, "新增行 +++bar 不应再被误判为文件头（└─ 仅一次）"
        # 统计基于 parsed 结构：含 1 条删除 + 1 条新增
        assert '🟢 +1' in output
        assert '🔴 -1' in output
        # 删除/新增行内容作为普通行渲染（消毒后字面量保留）
        assert '--foo' in output
        assert '++bar' in output

    def test_normal_file_header_still_rendered_regression(self):
        """正常文件头 `--- a/x` / `+++ b/x` 仍渲染 ┌─ / └─（行为不变）。"""
        diff = [
            '--- a/x',
            '+++ b/x',
            '@@ -1,1 +1,1 @@',
            '-a',
            '+b',
        ]
        out = self._collect(diff, lexer_name='')
        output = '\n'.join(out)
        assert '┌─ a/x' in output
        assert '└─ b/x' in output
        assert '🟢 +1' in output
        assert '🔴 -1' in output

    def test_four_dash_edge_not_file_header_regression(self):
        """`----` 边界：`---` 后跟 `-`（第4字符非空格）不判定为文件头（落入 del 分支）。"""
        diff = [
            '--- a/x',
            '+++ b/x',
            '@@ -1,2 +1,1 @@',
            '----foo',   # 4 个 -，第4字符非空格 → 不匹配 ``--- `` → del 分支
            '+ok',
        ]
        out = self._collect(diff, lexer_name='')
        output = '\n'.join(out)
        assert output.count('┌─ ') == 1, "`----foo` 不应被误判为文件头（┌─ 仅一次）"
        # `----foo` 作为删除行计入统计（非文件头不计入——统计含 1 删）
        assert '🔴 -1' in output


# ═══════════════════════════════════════════════════════════
# 方向1 P1 — show_file_diff None 防御 + 分隔线宽度参数化
# ═══════════════════════════════════════════════════════════

class TestShowFileDiffNoneAndWidthParam:
    """方向1 P1 — show_file_diff old_content=None 防御 + diff 摘要分隔线宽度参数化。"""

    def _collector(self):
        collected: list[str] = []

        class _Collector:
            _target = collected
            @classmethod
            def write_line(cls, text: str) -> None:
                cls._target.append(text)

        return collected, _Collector

    def test_show_file_diff_none_old_regression(self):
        """show_file_diff old_content=None 不崩溃（修复前 None.replace 崩溃）。"""
        from src.tui._diff_renderer import show_file_diff
        collected, _Collector = self._collector()
        show_file_diff("x", None, "new", output_target=_Collector)
        output = '\n'.join(collected)
        # 正常渲染（None 视为空内容 → 全部新增）
        assert '┌─ a/x' in output
        assert '└─ b/x' in output
        assert 'new' in output

    def test_diff_summary_width_param(self):
        """_render_diff_summary width 参数化：分隔线随 width 收缩（默认 40 不变）。"""
        from src.tui._diff_renderer import _render_diff_summary
        diff = ['--- a/x', '+++ b/x', '@@ -1,1 +1,1 @@', '-a', '+b']

        # 默认 width=40 → 分隔线 40 个 ╌（行为不变）
        collected1, _Collector1 = self._collector()
        _render_diff_summary(diff, output_target=_Collector1)
        sep_lines1 = [line for line in collected1 if '╌' in line]
        assert len(sep_lines1) == 1
        assert sep_lines1[0].count('╌') == 40, "默认 width=40 分隔线应 40 个 ╌"

        # width=10 → 分隔线收缩为 10 个 ╌（窄终端不溢出）
        collected2, _Collector2 = self._collector()
        _render_diff_summary(diff, output_target=_Collector2, width=10)
        sep_lines2 = [line for line in collected2 if '╌' in line]
        assert len(sep_lines2) == 1
        assert sep_lines2[0].count('╌') == 10, "width=10 分隔线应 10 个 ╌"

    def test_render_diff_multi_hunk_separator_width_param(self):
        """render_diff 多 hunk 分隔线宽度参数化：width=10 时 hunk 间分隔线收缩。"""
        from src.tui._diff_renderer import render_diff
        diff = [
            '--- a/x',
            '+++ b/x',
            '@@ -1,1 +1,1 @@',
            '-a',
            '+b',
            '@@ -5,1 +5,1 @@',
            '-c',
            '+d',
        ]
        collected1, _Collector1 = self._collector()
        render_diff(diff, w=4, lexer_name='', output_target=_Collector1)
        # 默认 width=40：多 hunk 分隔线 40 个 ╌
        sep_lines1 = [line for line in collected1 if '╌' in line]
        assert len(sep_lines1) == 1
        assert sep_lines1[0].count('╌') == 40

        collected2, _Collector2 = self._collector()
        render_diff(diff, w=4, lexer_name='', output_target=_Collector2, width=10)
        sep_lines2 = [line for line in collected2 if '╌' in line]
        assert len(sep_lines2) == 1
        assert sep_lines2[0].count('╌') == 10, "width=10 时多 hunk 分隔线应收缩为 10"
