"""词边界换行测试（方向8 完善 react ink —— textWrap="wrap" 语义）。"""

from __future__ import annotations

from src.tui.ink import StyledRun
from src.tui.ink.helpers import wrap_runs_by_width
from src.renderer.ansi.helpers import AnsiLine, wrap_line


class TestWordBreakWrap:
    """词边界优先断行：空格处断行，单词完整。"""

    def test_sentence_breaks_at_words(self):
        """句子在空格处断行（不拆单词）。"""
        lines = wrap_runs_by_width([StyledRun("The quick brown fox")], 10)
        plains = [l.plain for l in lines]
        assert plains == ["The quick", "brown fox"]
        assert all(" " not in p.strip() or p in ("The quick", "brown fox") for p in plains)

    def test_filepath_not_split(self):
        """file.txt 不因换行被拆成 file.tx/t。"""
        line = AnsiLine.of("  -rw-r--r-- 1 user user 100 file.txt")
        segs = [s.plain for s in wrap_line(line, 36)]
        assert any(s == "file.txt" for s in segs), f"file.txt 应完整: {segs!r}"

    def test_no_space_falls_back_char_level(self):
        """无空格断点回退字符级硬拆（既有行为锁定）。"""
        lines = wrap_runs_by_width([StyledRun("abcdefgh")], 3)
        assert [l.plain for l in lines] == ["abc", "def", "gh"]

    def test_cjk_breaks_char_level(self):
        """CJK 无空格，按字符级断行。"""
        lines = wrap_runs_by_width([StyledRun("中文测试")], 3)
        assert "".join(l.plain for l in lines) == "中文测试"
        assert all(l.width <= 3 for l in lines)

    def test_wrap_line_matches_ink(self):
        """renderer wrap_line 与 ink wrap_runs_by_width 词边界语义一致。"""
        text = "The quick brown fox jumps over the lazy dog"
        ink = [l.plain for l in wrap_runs_by_width([StyledRun(text)], 15)]
        rdr = [s.plain for s in wrap_line(AnsiLine.of(text), 15)]
        assert ink == rdr == ["The quick brown", "fox jumps over", "the lazy dog"]

    def test_styles_preserved_across_breaks(self):
        """词边界断行保持样式（同一 style 段不拆）。"""
        runs = [StyledRun("hello world", Style(fg=45))]
        lines = wrap_runs_by_width(runs, 5)
        assert [l.plain for l in lines] == ["hello", "world"]
        assert all(r.style is not None for l in lines for r in l.runs)

    def test_ascii_fast_path_equivalent(self):
        """纯 ASCII 无空格批量快路径（PERF-22）与通用路径语义等价。"""
        # 单 run 纯 ASCII：按宽度切片（每行 max_width 字符）
        text = "abcdefghij"
        lines = wrap_runs_by_width([StyledRun(text)], 3)
        assert [l.plain for l in lines] == ["abc", "def", "ghi", "j"]
        # 拼接还原
        assert "".join(l.plain for l in lines) == text
        # 行宽不超限
        assert all(l.width <= 3 for l in lines)

    def test_ascii_fast_path_style_preserved(self):
        """纯 ASCII 快路径保持样式（StyledRun.style 原样携带）。"""
        style = Style(fg=42)
        lines = wrap_runs_by_width([StyledRun("abcdef", style)], 2)
        assert [l.plain for l in lines] == ["ab", "cd", "ef"]
        assert all(l.runs[0].style == style for l in lines)

    def test_ascii_fast_path_exact_width(self):
        """快路径恰好整除 max_width：不产生多余空行。"""
        lines = wrap_runs_by_width([StyledRun("abcdef")], 3)
        assert [l.plain for l in lines] == ["abc", "def"]

    def test_ascii_fast_path_not_triggered_with_space(self):
        """含空格不走快路径（词边界断点语义保留）。"""
        lines = wrap_runs_by_width([StyledRun("ab cd ef")], 4)
        assert [l.plain for l in lines] == ["ab", "cd", "ef"]


from src.tui.core.style import Style  # noqa: E402  （测试顶部注释后导入）
