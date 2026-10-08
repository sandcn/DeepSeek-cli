"""TUI 流式 Markdown 渲染「英文」性能回归（inline_parser / ansi 预览）。

背景：``_InlineParser._FORMAT_CHARS`` 中的 ``h/H/f/F/w/W`` 并非格式标记，而是
裸 URL 前缀（``http:`` / ``https:`` / ``ftp:`` / ``ftps:`` / ``www.``）的首字母。
英文文本中这几个字母出现频率约 10%，修复前解析主循环对它们逐个尝试格式解析
（``_try_format`` 内的切片 + ``str.lower`` 前缀判定），且紧随其后的普通文本段
也要一次扫描——长英文段落流式预览（每帧对 4096 字符窗口整段解析）实测
30k 字符逐字符写入需 ~22.5s（30Hz 帧预算内的不可接受成本）。

修复：
  1. 解析器按「兴趣位置表」（核心格式触发字符 + 裸 URL 前缀起点）二分查找，
     一次跳过整段普通文本，普通字母不再进入 ``_try_format``；
  2. ``text_has_inline_markup``（快速判否，唯一真源）用于 ``render_inline``
     快路径与段落活动行预览判定——英文纯文本直接单 Run；
  3. 段落预览的触发位置增量跟踪区分「核心字符」与「裸 URL 前缀」（回看窗口
     覆盖前缀跨增量边界）。

本文件固化**语义等价性**（新路径 vs 修复前逐字符路径）与**性能边界**。
"""
from __future__ import annotations

import random
import time

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import Run
from src.renderer.ansi.inline import _emit_nodes, render_inline
from src.renderer.ansi.style import Style
from src.renderer.inline_parser import (
    _CORE_FORMAT_CHARS,
    _InlineParser,
    text_has_inline_markup,
)

_FORMAT_CHARS = _InlineParser._FORMAT_CHARS


# ═══════════════════════════════════════════════════════════
# 修复前等价实现（逐字符扫描 + 全部 _FORMAT_CHARS 位置都尝试）
# ═══════════════════════════════════════════════════════════


def _legacy_parse(text: str):
    """模拟修复前的解析路径：``_FORMAT_CHARS`` 的**每个**位置都尝试格式解析。

    修复前 ``_parse_until`` 对 ``h/f/w`` 字母同样进入 ``_try_format``（裸 URL
    判定），并由 ``_find_next_format_char`` 逐字符扫描到下一个 ``_FORMAT_CHARS``
    成员。把兴趣位置表替换为「全部 _FORMAT_CHARS 位置」即等价复现。
    """
    parser = _InlineParser(text)
    positions = [i for i, ch in enumerate(text) if ch in _FORMAT_CHARS]
    parser._interest_positions = positions
    parser._interest_set = frozenset(positions)
    return parser.parse()


def _legacy_runs(text: str, base: Style | None = None) -> list:
    base = base if base is not None else Style()
    out: list[Run] = []
    _emit_nodes(_legacy_parse(text), base, None, out, 0)
    return out


def _runs_key(runs) -> list:
    return [(r.text, r.style, getattr(r, "link", None)) for r in runs]


# ═══════════════════════════════════════════════════════════
# 快速判否等价性（判定 False ⟹ 单 Run 纯文本）
# ═══════════════════════════════════════════════════════════


_PLAIN_TEXTS = [
    "",
    "plain english text with no markup at all",
    "the quick brown fox jumps over the lazy dog",
    "Streaming renderers must re-render the active block on every write.",
    "中文文本内容及数字123 空格",
    "a" * 500,
    "word " * 200,
    "normal sentence with punctuation and commas",
    "filename with spaces.txt",
    "Users path to file",
    "百分之五十 with mixed 中文 and english",
    "trailing spaces   ",
    "tab\tseparated\twords",
]


def test_fast_path_implies_single_plain_run():
    """判定为「无行内标记」时必须产出单一纯文本 Run（与解析器结果一致）。"""
    for text in _PLAIN_TEXTS:
        assert text_has_inline_markup(text) is False, repr(text)
        runs = render_inline(text)
        assert _runs_key(runs) == _runs_key(_legacy_runs(text)), repr(text)
        if not text:
            continue
        assert len(runs) == 1, repr(text)
        # ``Run`` 构造期会规范化制表符/回车（展开制表符），故含控制字符的
        # 用例只比对 run 结构（上面的 legacy 比对已覆盖内容一致性）
        if "\t" not in text and "\r" not in text:
            assert runs[0].text == text, repr(text)


def test_fast_path_true_when_markup_or_url_present():
    """含格式标记或裸 URL 前缀时必须进入解析器。"""
    for text in (
        "a *b* c",
        "see `code` here",
        "link [t](http://x)",
        "visit http://example.com now",
        "go to www.example.com",
        "HTTPS://EXAMPLE.COM",
        "ftp://host/file",
        "email me a@b.com",
        "&amp; entity",
        "under_score_ *",
        "brace {color:red}x{color}",
    ):
        assert text_has_inline_markup(text) is True, repr(text)


def test_fast_path_matches_parser_plainness():
    """判定口径与解析器实际「是否产生非文本节点」一致（随机模糊）。"""
    rnd = random.Random(20261008)
    alphabet = list("abcdefghijklmnopqrstuvwxyzHWFT .:,;!?-_/*`~[]()<>@&=\\^|%$+{}")
    for _ in range(400):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
        if text_has_inline_markup(text):
            continue
        runs = render_inline(text)
        assert len(runs) <= 1, (text, _runs_key(runs))
        if text:
            assert runs[0].text == text and runs[0].style == Style(), text


# ═══════════════════════════════════════════════════════════
# 新解析路径 ≡ 修复前逐字符路径（产出等价）
# ═══════════════════════════════════════════════════════════

_EQUIV_TEXTS = [
    "plain text",
    "**bold** and *italic* and `code`",
    "mixed 中文 **粗体** english text",
    "a http://example.com/path b",
    "The HTTP protocol and the WWW prefix",
    "an ftp://host/x link and FTP://other",
    "visit www.example.com for details",
    "hello world, this is a fairly long english sentence with h/f/w letters",
    "**unclosed and *unclosed",
    "text with [link](http://u) and ![img](http://i.png)",
    "<https://auto.link> and <a@b.com>",
    "escaped \\*star\\* and \\`tick\\`",
    "~~strike~~ ==hl== ++u++ ||spoiler||",
    "$x^2$ and \\(y\\) math",
    "{color:red}red{color} and {-small-}",
    "&amp; &lt; entities and :smile: emoji",
    "wikilink [[Page Name]] here",
    "critic {++add++}{--del--}{>>note<<}",
    "trailing http://",
    "with http and https words but no scheme",
    "The theory of everything found within",
    "final sentence without markers",
]


def test_parse_equivalent_to_legacy_path():
    for text in _EQUIV_TEXTS:
        got = _runs_key(render_inline(text))
        exp = _runs_key(_legacy_runs(text))
        assert got == exp, text


def test_parse_equivalent_to_legacy_path_fuzz():
    rnd = random.Random(97)
    words = ["the", "quick", "http", "www.", "https://x.com", "bug", "*b*",
             "`c`", "[t](u)", "中文", "h", "w", "f", "://", "a@b.com", "%",
             "#t", "~s~", "=", "^", "<br>", "&amp;", "{++x++}", "|s|"]
    for _ in range(300):
        text = " ".join(rnd.choice(words) for _ in range(rnd.randint(0, 12)))
        got = _runs_key(render_inline(text))
        exp = _runs_key(_legacy_runs(text))
        assert got == exp, text


def test_url_detection_unchanged_in_english_text():
    """英文句子中的裸 URL 仍被识别为链接（OSC 8 link 附在 run 上）。"""
    runs = render_inline("see http://example.com/a for more")
    linked = [r for r in runs if getattr(r, "link", None)]
    assert linked and linked[0].link == "http://example.com/a"
    assert linked[0].text == "http://example.com/a"

    runs = render_inline("go to www.example.com now")
    assert any(getattr(r, "link", "") == "www.example.com" for r in runs)


# ═══════════════════════════════════════════════════════════
# 段落活动行预览：快路径开关产出等价（英文用例）
# ═══════════════════════════════════════════════════════════


def _frames(text: str, chunk: int, width: int, fast: bool):
    r = AnsiStreamRenderer(width=width)
    if not fast:
        r._plain_active_window = lambda content: False
    frames = []
    for i in range(0, len(text), chunk):
        r.write(text[i:i + chunk])
        frames.append([[(run.text, run.style) for run in ln.runs]
                       for ln in r.take_preview_lines()])
    r.close()
    return frames


_EN_CASES = {
    "plain_english": "Streaming renderers must re-render the active block "
                     "on every single write. " * 120,
    "english_with_url": "See the docs at http://example.com/guide for the "
                        "full explanation of how it works. " * 60,
    "english_with_markers": "A sentence with `code` and **bold** words and "
                            "the rest plain. " * 60,
    "marker_then_english": "**Header** " + "plain english words here " * 200,
    "english_then_marker": ("plain english words here " * 200) + " **tail**",
}


def test_english_preview_fast_path_equivalent():
    for name, text in _EN_CASES.items():
        assert _frames(text, 16, 120, True) == _frames(text, 16, 120, False), name


def test_english_preview_plain_window_uses_single_run():
    """纯英文段落的活动行预览走单 Run 快路径（无解析器往返）。"""
    r = AnsiStreamRenderer(width=120)
    r.write("the quick brown fox jumps over the lazy dog ")
    lines = r.take_preview_lines()
    assert lines
    plain = "".join(ln.plain for ln in lines)
    assert "the quick brown fox" in plain
    assert lines[-1].runs and len(lines[-1].runs) == 1


# ═══════════════════════════════════════════════════════════
# 增量触发位置维护（核心字符 / 裸 URL 前缀）
# ═══════════════════════════════════════════════════════════


def test_note_triggers_url_prefix_across_chunks():
    r = AnsiStreamRenderer(width=80)
    r._note_paragraph_triggers("prefix www")
    assert r._para_last_url == -1
    r._note_paragraph_triggers("prefix www.")
    assert r._para_last_url == 7


def test_note_triggers_core_positions_incrementally():
    r = AnsiStreamRenderer(width=80)
    r._note_paragraph_triggers("plain english")
    assert r._para_last_core == -1
    r._note_paragraph_triggers("plain english *x")
    assert r._para_last_core == 14
    r._note_paragraph_triggers("plain english *x and more words")
    assert r._para_last_core == 14


def test_plain_active_window_excludes_english_with_url():
    r = AnsiStreamRenderer(width=80)
    text = "the quick brown fox " * 20
    r._note_paragraph_triggers(text)
    assert r._plain_active_window(text) is True
    text2 = text + "see https://example.com"
    r._note_paragraph_triggers(text2)
    assert r._plain_active_window(text2) is False


# ═══════════════════════════════════════════════════════════
# 性能边界（宽松上界；修复前同场景 ~22s）
# ═══════════════════════════════════════════════════════════

_SENTENCE = ("Streaming renderers must re-render the active block on every "
             "single write, so the per-write cost dominates the perceived "
             "smoothness of the interface. ")


def _stream(text: str, chunk: int, width: int = 100) -> float:
    r = AnsiStreamRenderer(width=width)
    t0 = time.perf_counter()
    for i in range(0, len(text), chunk):
        r.write(text[i:i + chunk])
        r.take_preview_lines()
    r.close()
    return time.perf_counter() - t0


def test_long_english_paragraph_stream_budget():
    """30k 字符英文长段落逐字符流式写入耗时有界（修复前 ~22.5s）。"""
    text = _SENTENCE * 100 + "\n\n"
    _stream(text, 64)  # 预热（导入 / 缓存）
    elapsed = _stream(text, 1)
    assert elapsed < 3.0, f"英文长段落流式耗时 {elapsed:.3f}s"


def test_long_english_paragraph_frame_cost_bounded():
    """英文长段落单帧成本有界且不随段落增长（复杂度由 O(n) 降为 O(增量)）。"""
    text = _SENTENCE * 200 + "\n\n"
    r = AnsiStreamRenderer(width=100)
    worst_early = 0.0
    worst_late = 0.0
    n = len(text)
    for i in range(0, n, 4):
        t0 = time.perf_counter()
        r.write(text[i:i + 4])
        dt = time.perf_counter() - t0
        if i < n // 4:
            worst_early = max(worst_early, dt)
        elif i > n * 3 // 4:
            worst_late = max(worst_late, dt)
    r.close()
    # 段落尾部（内容最长）单帧成本不应显著高于前段（允许 4x 抖动余量）
    assert worst_late < max(worst_early * 4, 0.02), (worst_early, worst_late)


def test_english_and_chinese_stream_cost_comparable():
    """英文与中文同为纯文本时流式成本同量级（修复前英文因 h/f/w 显著更慢）。"""
    en = _SENTENCE * 40
    zh = "流式渲染器必须在每次写入时重渲染活动块，因此单次写入成本决定了界面的流畅程度。" * 40
    _stream(en, 64)
    _stream(zh, 64)
    t_en = _stream(en, 4)
    t_zh = _stream(zh, 4)
    assert t_en < max(t_zh * 4, 0.5), (t_en, t_zh)


# ═══════════════════════════════════════════════════════════
# 核心字符集合与兴趣表构建
# ═══════════════════════════════════════════════════════════


def test_core_format_chars_excludes_url_letters():
    assert _CORE_FORMAT_CHARS == _FORMAT_CHARS - frozenset("hHfFwW")
    for ch in "hHfFwW":
        assert ch not in _CORE_FORMAT_CHARS


def test_interest_positions_contains_core_and_url_starts():
    from src.renderer.inline_parser import _build_interest_positions

    text = "a*b http://x www.y c"
    pos = _build_interest_positions(text)
    assert pos == sorted(pos)
    assert 1 in pos                      # '*'
    assert 4 in pos                      # 'http:' 起点
    assert text.index("www.") in pos     # 'www.' 起点
    assert text.index(":") in pos        # ':'（核心字符，URL 内）
    assert _build_interest_positions("") == []
    # 普通英文单词（含 h/f/w）不产生兴趣位置
    assert _build_interest_positions("the quick brown fox") == []


def test_interest_positions_url_prefix_case_insensitive():
    from src.renderer.inline_parser import _build_interest_positions

    # 前缀起点（0）+ 其中的 ':'（核心字符，位于 5）
    assert _build_interest_positions("HTTPS://X") == [0, 5]
    assert _build_interest_positions("FtP://x") == [0, 3]
    assert _build_interest_positions("WWW.x.y") == [0]


def test_interest_positions_empty_for_plain_english():
    """长英文纯文本不产生任何兴趣位置（表规模与文本长度无关）。"""
    from src.renderer.inline_parser import _build_interest_positions

    assert _build_interest_positions("the quick brown fox " * 500) == []


def test_interest_table_disabled_for_pathological_input():
    """位置数超限（病态输入）时禁用兴趣表并回退逐字符扫描。"""
    from src.renderer.inline_parser import (
        _INTEREST_POSITIONS_MAX, _build_interest_positions,
    )

    text = "*" * (_INTEREST_POSITIONS_MAX + 10)
    assert _build_interest_positions(text) is None
    parser = _InlineParser(text)
    assert parser._interest_set is None
    # 回退路径仍可正确解析（粗体标记逐字符尝试）
    text2 = "**bold** and *italic*"
    assert "".join(r.text for r in render_inline(text2)) == "bold and italic"


def test_disabled_table_equivalent_to_enabled():
    """兴趣表禁用（回退路径）与启用时解析产出完全一致。"""
    text = "**bold** and ***" + "_" * 50 + " tail with h/f/w letters"
    enabled = _runs_key(render_inline(text))

    parser = _InlineParser(text)
    parser._interest_positions = None
    parser._interest_set = None
    out: list = []
    _emit_nodes(parser.parse(), Style(), None, out, 0)
    assert _runs_key(out) == enabled
