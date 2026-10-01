"""wrap_runs_by_width ASCII 多 run 快路径 — 与通用算法等价性测试。"""

from __future__ import annotations

import random

from src.tui.ink.output import StyledRun
from src.tui.ink._runs_utils import wrap_runs_by_width
from src.tui.ink.helpers import wrap_runs_by_width as facade_wrap
from src.tui.core.style import Style


def _reference(runs, max_width, hard):
    """通用算法参考实现（逐字符）+ 词边界断行（与历史实现一致）。"""
    if max_width <= 0:
        out = []
        cur = []
        for r in runs:
            for si, seg in enumerate(r.text.split("\n")):
                if si > 0:
                    out.append(cur)
                    cur = []
                if seg:
                    cur.append(seg)
        if cur:
            out.append(cur)
        return ["".join(x) for x in out]
    items = [(ch, r.style) for r in runs for ch in r.text]
    n = len(items)
    lines = []
    i = 0
    while i < n:
        j = i
        width = 0
        last_space = -1
        while j < n:
            ch, _ = items[j]
            if ch == "\n":
                break
            if ch == " ":
                last_space = j
            cw = len(ch)
            if width + cw > max_width and j > i:
                break
            width += cw
            j += 1
        if j == i + 1 and width > max_width:
            i += 1
            continue
        if j == i:
            lines.append("")
            i += 1
            continue
        if j < n and items[j][0] == "\n":
            end, next_i = j, j + 1
        elif j < n and last_space > i and not hard:
            end, next_i = last_space, last_space + 1
        else:
            end, next_i = j, j
        lines.append("".join(ch for ch, _ in items[i:end]))
        i = next_i
    return lines


def _styles(text_index):
    return [Style(fg=1), Style(fg=2), Style(fg=3)][text_index % 3]


def test_wrap_fastpath_matches_reference_single_run():
    cases = ["hello world foo", "a" * 25, "one two three four", "  leading"]
    for text in cases:
        for w in (1, 3, 5, 8, 40):
            for hard in (False, True):
                runs = [StyledRun(text)]
                got = [l.plain for l in wrap_runs_by_width(runs, w, hard)]
                assert got == _reference(runs, w, hard), (text, w, hard)


def test_wrap_fastpath_known_outputs():
    """固定期望值（独立于参考实现，防「复制变异」掩盖共同缺陷）。"""
    assert [l.plain for l in wrap_runs_by_width([StyledRun("aaaa bbbb")], 4)] == ["aaaa", "bbbb"]
    assert [l.plain for l in wrap_runs_by_width([StyledRun("aaaa bbbb")], 4, True)] == ["aaaa", " bbb", "b"]
    assert [l.plain for l in wrap_runs_by_width([StyledRun("hello world foo")], 8)] == ["hello", "world", "foo"]
    assert [l.plain for l in wrap_runs_by_width([StyledRun("abcdefghij")], 4)] == ["abcd", "efgh", "ij"]
    assert [l.plain for l in wrap_runs_by_width(
        [StyledRun("ab", Style(fg=1)), StyledRun("cdefgh", Style(fg=2))], 4)] == ["abcd", "efgh"]


def test_wrap_fastpath_matches_reference_multi_run():
    runs = [
        StyledRun("hello ", Style(fg=1)),
        StyledRun("world", Style(fg=2)),
        StyledRun(" again and again", Style(fg=1)),
    ]
    for w in (1, 2, 4, 7, 11, 50):
        for hard in (False, True):
            got = [l.plain for l in wrap_runs_by_width(runs, w, hard)]
            assert got == _reference(runs, w, hard), (w, hard)


def test_wrap_fastpath_randomized_matches_reference():
    rng = random.Random(20261001)
    alphabet = "abcde "
    for _ in range(200):
        n_runs = rng.randint(1, 4)
        runs = []
        for k in range(n_runs):
            length = rng.randint(1, 12)
            text = "".join(rng.choice(alphabet) for _ in range(length))
            runs.append(StyledRun(text, _styles(k)))
        w = rng.randint(1, 10)
        hard = rng.random() < 0.5
        got = [l.plain for l in wrap_runs_by_width(runs, w, hard)]
        assert got == _reference(runs, w, hard), (runs, w, hard)


def test_wrap_fastpath_preserves_styles():
    runs = [StyledRun("ab", Style(fg=1)), StyledRun("cdef", Style(fg=2))]
    lines = wrap_runs_by_width(runs, 3)
    assert [l.plain for l in lines] == ["abc", "def"]
    assert len(lines[0].runs) == 2


def test_facade_wrap_same_function():
    assert facade_wrap is wrap_runs_by_width


def test_wrap_fastpath_large_multirun_is_not_pathological():
    """多 run 大文本快路径不退化（旧 O(lines×runs) 扫描会明显超时）。"""
    import time

    runs = [StyledRun("x" * 40) for _ in range(1000)]
    start = time.perf_counter()
    lines = wrap_runs_by_width(runs, 80)
    elapsed = time.perf_counter() - start
    assert len(lines) == 500
    assert elapsed < 1.0


def test_wrap_fastpath_single_run_with_spaces_uses_fast_path():
    runs = [StyledRun("word " * 500)]
    lines = wrap_runs_by_width(runs, 20)
    assert all(l.width <= 20 for l in lines)
    assert "".join(l.plain for l in lines).replace(" ", "") == "word" * 500
