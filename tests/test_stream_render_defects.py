"""流式 Markdown 渲染缺陷修复回归测试（P0/P1/P2）。

覆盖修复清单：
  - P0-1 未换行活动行实时预览（逐字上屏，不再整行突发）
  - P0-2 引用块渲染（单层前缀 / 嵌套不丢内容 / 无多余空边框）
  - P0-3 代码块预览按行增量高亮（消除每帧整段重渲染的 O(n²) 开销）
  - P1-1 超长代码块分段后仍为单一逻辑块（另见 test_stream_markdown_fixes）
  - P1-2 跨软换行行内标记配对 + 未闭合标记原样输出
  - P1-3 预览行与 committed 行同样按宽度预 wrap
  - P2-1 ``[//]:`` 注释行不进入引用链接表
  - P2-2 预览截断省略提示
  - P2-3 内容块源文本上限 + 超限跳过 resize 重渲染
  - P2-4 队列满时流式内容命令兜底合并（不新增条目、保序）
  - P2-5 捕获缓冲公开接口转发（不再直达私有字段）
  - TokenStreamOptimizer 段落合并使用软换行
  - TokenPipeline 过滤器异常隔离 / CodeBlockBatcher 异常返回已产出
"""
from __future__ import annotations

import itertools
import queue
import threading

import pytest

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer._utils import cjk_display_width


def _display_width(text: str) -> int:
    return sum(cjk_display_width(ch) for ch in text)


def _render(chunks, width=80):
    """流式喂入 chunks，close 后返回全部行明文。"""
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    r.close()
    return [line.plain for line in r.take_lines()]


# ═══════════════════════════════════════════════════════════
# P0-1 未换行活动行实时预览
# ═══════════════════════════════════════════════════════════


def test_incomplete_line_visible_in_preview():
    """未换行的当前行在 preview 中实时可见（而非空预览）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("Hello wor")
    assert [l.plain for l in r.take_preview_lines()] == ["Hello wor"]
    r.write("ld\n")
    # 段落仍开放：内容在 preview 中（未提交）
    assert [l.plain for l in r.take_preview_lines()] == ["Hello world"]
    r.write("\n")
    assert [l.plain for l in r.take_lines()] == ["Hello world"]


def test_incomplete_code_line_visible_in_preview():
    """未闭合代码块的未换行活动行同样实时可见（含已确定的前置行）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```python\na = 1\n")
    r.write("b = 2")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev == ["```python [python]", "a = 1", "b = 2"]
    r.write("\n```\n")
    committed = [l.plain for l in r.take_lines()]
    assert committed == ["```python [python]", "a = 1", "b = 2", "```"]


def test_incomplete_line_appends_to_pending_paragraph():
    """已闭合段落行 + 未换行活动行合并预览（不丢失待定行）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("已闭合行\n")
    r.write("活动行")
    assert [l.plain for l in r.take_preview_lines()] == ["已闭合行", "活动行"]


# ═══════════════════════════════════════════════════════════
# P0-2 引用块渲染
# ═══════════════════════════════════════════════════════════


def test_blockquote_single_level_has_prefix():
    """单层引用以空行结束时保留 ``│ `` 前缀且不产出空边框行。"""
    assert _render(["> 第一行\n", "> 第二行\n", "\n"]) == ["│ 第一行", "│ 第二行"]


def test_blockquote_followed_by_paragraph():
    """引用块后紧跟普通段落：引用带前缀，段落在外。"""
    assert _render(["> 引用内容\n", "普通段落\n"]) == ["│ 引用内容", "普通段落"]


def test_nested_blockquote_keeps_all_content():
    """嵌套引用不丢内容、按文档顺序输出、前缀体现层级。"""
    assert _render(["> 外层引用\n", "> > 内层引用\n", "> 外层续\n"]) == [
        "│ 外层引用", "│ │ 内层引用", "│ 外层续",
    ]


def test_blockquote_token_stream_uses_line_tokens():
    """解析层引用内段落以 BLOCKQUOTE_LINE 发出（不再是 PARAGRAPH）。"""
    from src.renderer.recursive_parser import RegexFreeBlockParser
    from src.renderer.types import TokenType

    p = RegexFreeBlockParser()
    toks = list(p.feed("> 第一行\n> 第二行\n\n")) + list(p.flush())
    types = [t.type for t in toks]
    assert TokenType.BLOCKQUOTE_LINE in types
    assert TokenType.PARAGRAPH not in types
    line = next(t for t in toks if t.type is TokenType.BLOCKQUOTE_LINE)
    assert line.content == "第一行\n第二行"
    assert line.meta.get("depth") == 1


# ═══════════════════════════════════════════════════════════
# P0-3 代码块预览增量高亮
# ═══════════════════════════════════════════════════════════


def test_code_preview_highlights_incrementally(monkeypatch):
    """逐行流式：每次 write 只渲染 O(1) 行（升格行 + 活动行），非整块重渲染。

    ★ 语义更新（活动行实时渲染修复）：未换行的**活动行**内容逐帧变化，现每帧
    重渲（原实现只在行数增加时渲染——活动行内容被忽略、显示陈旧文本）；
    完整行升格时以其最终内容渲染一次并写入逐行高亮缓存（闭合提交时命中，
    免整块重新词法高亮）。故单次渲染行数 <= 2、累计与总行数线性（非二次）。
    """
    from src.renderer.ansi import code as _code

    total = {"lines": 0, "max": 0}
    orig = _code.highlight_code_lines

    def spy(lines, lang="", theme="monokai", highlight_lines=None,
            start_index=1, **kwargs):
        total["lines"] += len(lines)
        if len(lines) > total["max"]:
            total["max"] = len(lines)
        return orig(lines, lang, theme, highlight_lines, start_index, **kwargs)

    monkeypatch.setattr(_code, "highlight_code_lines", spy)
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    for i in range(300):
        r.write(f"v{i} = {i}\n")
        r.take_lines()
        r.take_preview_lines()
    # 单次渲染不超过 2 行（升格行 + 活动行）
    assert total["max"] <= 2
    # 累计线性（<= 2 × 行数 + 常数），远低于每帧整段重渲染的 O(n²)
    assert total["lines"] <= 2 * 301 + 4, total["lines"]


def test_code_preview_cache_reset_on_content_divergence(monkeypatch):
    """预览缓存按行复用；内容分歧（非前缀）时整体重置重渲染。"""
    from src.renderer.ansi import code as _code
    from src.renderer.types import Token, TokenType

    total = {"lines": 0}
    orig = _code.highlight_code_lines

    def spy(lines, lang="", theme="monokai", highlight_lines=None,
            start_index=1, **kwargs):
        total["lines"] += len(lines)
        return orig(lines, lang, theme, highlight_lines, start_index, **kwargs)

    monkeypatch.setattr(_code, "highlight_code_lines", spy)
    r = AnsiStreamRenderer(width=80)

    def tok(content: str) -> "Token":
        return Token(TokenType.CODE_BLOCK, content,
                     {"lang": "python", "preview": True, "closed": False})

    # 初始：'a'（已确定行）+ 'b'（活动行）
    r._render_code_preview(tok("a\nb"))
    assert total["lines"] == 2
    # 前缀命中（只追加）→ 渲染升格行 'b'（最终内容）+ 新活动行 'c'
    r._render_code_preview(tok("a\nb\nc"))
    assert total["lines"] == 4
    # 内容分歧（不以缓存为前缀）→ 整体重渲染 3 行
    r._render_code_preview(tok("a\nX\nc"))
    assert total["lines"] == 7
    # 语言变化 → 缓存键失效 → 整体重渲染
    r._render_code_preview(Token(TokenType.CODE_BLOCK, "x\ny",
                                 {"lang": "js", "preview": True, "closed": False}))
    assert total["lines"] == 9


# ═══════════════════════════════════════════════════════════
# P1-2 行内标记跨软换行 / 未闭合标记
# ═══════════════════════════════════════════════════════════


def test_inline_bold_across_soft_break():
    """跨软换行的 ``**加粗**`` 正确配对（不再泄漏星号）。"""
    assert _render(["这是 **加粗\n", "跨行** 结尾\n", "\n"]) == ["这是 加粗", "跨行 结尾"]


def test_unclosed_bold_marker_rendered_literally():
    """未闭合的 ``**`` 原样输出（不再吞掉一个字符）。"""
    assert _render(["未闭合 **加粗\n", "\n"]) == ["未闭合 **加粗"]


def test_unclosed_strike_marker_rendered_literally():
    """未闭合的 ``~~`` 原样输出。"""
    assert _render(["未闭合 ~~删除\n", "\n"]) == ["未闭合 ~~删除"]


def test_paragraph_trailing_soft_break_no_extra_blank():
    """段落尾部软换行不产出额外空行（保持既有行模型）。"""
    assert _render(["单行段落\n", "\n"]) == ["单行段落"]


# ═══════════════════════════════════════════════════════════
# P1-3 预览行按宽度预 wrap
# ═══════════════════════════════════════════════════════════


def test_preview_line_wrapped_like_committed():
    """未闭合块预览行按终端宽度预 wrap（与 committed 行同一真源）。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui.app.chat_view import _block_styled_lines
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 20
    _do_content(m, ContentCmd(text="一段很长的预览文本内容超过宽度限制"))
    blk = m.blocks[m.content_block_index]
    rows = _block_styled_lines(blk, blk.committed_line_count, 20)
    texts = ["".join(r.text for r in row) for row in rows]
    assert texts, "预览行缺失"
    assert all(_display_width(t) <= 20 for t in texts), texts


# ═══════════════════════════════════════════════════════════
# P2-1 注释行不进入引用链接表
# ═══════════════════════════════════════════════════════════


def test_comment_line_not_in_ref_map():
    """``[//]: # (comment)`` 是注释，不应被预扫描收录为引用链接定义。"""
    r = AnsiStreamRenderer(width=80)
    r.write("[//]: # (this is a comment)\n\n正文\n")
    assert "//" not in r._ctx.ref_map
    assert _render(["[//]: # (this is a comment)\n", "\n", "正文\n"]) == ["正文"]


def test_real_ref_link_still_collected():
    """正常引用链接定义仍被收录（注释过滤不误伤）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("[docs]: https://example.com \"示例\"\n\n正文\n")
    assert r._ctx.ref_map.get("docs") == ("https://example.com", "示例")


# ═══════════════════════════════════════════════════════════
# P2-2 预览截断省略提示
# ═══════════════════════════════════════════════════════════


def test_preview_truncation_notice():
    """超长代码块预览保留尾部并在头部给出省略提示。"""
    from src.renderer._block_parser import RegexFreeBlockParser

    limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
    total = limit + 50
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    for i in range(total):
        r.write(f"line_{i} = {i}\n")
        r.take_lines()
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev[0].startswith("```python")
    assert prev[1] == "… 前 50 行省略（本块结束后完整显示）"
    assert len(prev) == limit + 2
    assert prev[2] == "line_50 = 50"


# ═══════════════════════════════════════════════════════════
# P2-3 源文本上限
# ═══════════════════════════════════════════════════════════


def test_source_text_capped_and_rerender_skipped(monkeypatch):
    """源文本达上限后停止累积并标记；resize 重渲染跳过（保留现有行）。"""
    from src.tui.app import apply as _apply
    from src.tui.app.model import AppModel
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    monkeypatch.setattr(_apply, "_SOURCE_TEXT_MAX", 10)
    _apply._do_content(m, ContentCmd(text="0123456789ABCDEF\n"))
    blk = m.blocks[m.content_block_index]
    assert blk.extra.get("source_truncated") is True
    assert len(blk.source_text) == 10
    before = list(blk.lines)
    assert m._rerender_open_block(blk, 100) is False
    assert blk.lines == before


def test_source_text_accumulates_within_limit():
    """未超限的源文本正常累积（resize 重渲染可用）。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="第一段\n"))
    _do_content(m, ContentCmd(text="第二段\n"))
    blk = m.blocks[m.content_block_index]
    assert blk.source_text == "第一段\n第二段\n"
    assert not blk.extra.get("source_truncated")


# ═══════════════════════════════════════════════════════════
# P2-4 队列满时流式命令兜底合并
# ═══════════════════════════════════════════════════════════


def _make_session(maxsize: int = 10000):
    """构造仅含队列依赖的 InkSession 桩（不启动渲染线程）。"""
    from src.tui._config import TuiConfig
    from src.tui.ink.session import InkSession

    cfg = TuiConfig.defaults().with_overrides(cmd_queue_maxsize=maxsize)
    s = object.__new__(InkSession)
    s._cmd_queue = queue.PriorityQueue(maxsize=cfg.cmd_queue_maxsize)
    s._cmd_seq = itertools.count()
    s._cmd_event = threading.Event()
    s._consecutive_full = 0
    s._cmd_queue_dropped = 0
    s._render_running = True
    s._config = cfg
    s._write_emergency = lambda *a, **k: None
    return s


def test_stream_cmd_merged_when_queue_full():
    """队列满且无 LOW 可腾位时，流式内容命令合并到队尾同类（不新增条目）。"""
    from src.tui._const import ContentCmd

    s = _make_session(maxsize=2)
    s.push_cmd(ContentCmd(text="a"))
    s.push_cmd(ContentCmd(text="b"))
    assert s._cmd_queue.qsize() == 2
    s.push_cmd(ContentCmd(text="c"))
    assert s._cmd_queue.qsize() == 2
    assert s._cmd_queue_dropped == 0
    items = sorted(s._cmd_queue.queue, key=lambda it: it[1])
    assert items[-1][2].text == "bc"
    assert items[0][2].text == "a"


def test_stream_cmd_not_merged_when_tail_is_other_kind():
    """队尾非同类型命令时不合并（保序优先），走既有丢弃/背压路径。"""
    from src.tui._const import ContentCmd, WriteLineCmd

    s = _make_session(maxsize=2)
    s.push_cmd(ContentCmd(text="a"))
    s.push_cmd(WriteLineCmd(text="log"))
    assert s._cmd_queue.qsize() == 2
    s.push_cmd(ContentCmd(text="b"))
    # 腾位会移除 LOW 的 WRITE_LINE，因此 ContentCmd 能正常入队（顺序不颠倒）
    items = sorted(s._cmd_queue.queue, key=lambda it: it[1])
    assert [it[2].text for it in items] == ["a", "b"]


def test_stream_merge_respects_max_chars():
    """超过合并文本上限后不再合并（避免单条命令无限膨胀）。"""
    from src.tui._const import ContentCmd
    from src.tui.ink import _session_queue_mixin as _mix

    s = _make_session(maxsize=1)
    s.push_cmd(ContentCmd(text="x" * 100))
    s.push_cmd(ContentCmd(text="y" * (_mix._STREAM_MERGE_MAX_CHARS)))
    # 第二次：队列满 → 合并被上限拒绝 → 背压/丢弃兜底（不产生超长条目）
    items = sorted(s._cmd_queue.queue, key=lambda it: it[1])
    assert len(items) == 1
    assert len(items[0][2].text) <= _mix._STREAM_MERGE_MAX_CHARS


# ═══════════════════════════════════════════════════════════
# P2-5 捕获缓冲公开接口
# ═══════════════════════════════════════════════════════════


def test_output_adapter_captured_output_property():
    """OutputAdapter 暴露公开 captured_output 读写接口。"""
    from io import StringIO
    from rich.console import Console
    from src.renderer.output import OutputAdapter

    cap: list = []
    adapter = OutputAdapter(Console(file=StringIO(), force_terminal=True),
                            captured_output=cap)
    assert adapter.captured_output is cap
    adapter.captured_output = []
    assert adapter.captured_output == []


def test_styled_adapter_forwards_captured_output():
    """_StyledOutputAdapter 经公开接口转发捕获缓冲（不再直达私有字段）。"""
    from io import StringIO
    from rich.console import Console
    from src.renderer import _StyledOutputAdapter
    from src.renderer.output import OutputAdapter

    cap: list = []
    adapter = OutputAdapter(Console(file=StringIO(), force_terminal=True),
                            captured_output=cap)
    styled = _StyledOutputAdapter(adapter, style="dim")
    assert styled.captured_output is cap
    new_cap: list = []
    styled.captured_output = new_cap
    assert adapter.captured_output is new_cap


def test_styled_adapter_tolerates_adapter_without_capture():
    """底层适配器不支持捕获时安全降级（返回 None，不抛异常）。"""
    from src.renderer import _StyledOutputAdapter

    class _Bare:
        width = 80

        def force_refresh_width(self):
            return None

    styled = _StyledOutputAdapter(_Bare(), style="dim")
    assert styled.captured_output is None
    styled.captured_output = []   # 不抛异常


# ═══════════════════════════════════════════════════════════
# 其它：优化器 / 管道容错 / 批处理器异常
# ═══════════════════════════════════════════════════════════


def test_stream_optimizer_merges_with_soft_break():
    """连续 PARAGRAPH 合并使用软换行（不凭空引入段落分隔）。"""
    from src.renderer.pipeline_filters.stream_optimizer import TokenStreamOptimizer
    from src.renderer.types import Token, TokenType, RenderContext

    opt = TokenStreamOptimizer()
    ctx = RenderContext()
    out = opt.process([
        Token(TokenType.PARAGRAPH, "a"),
        Token(TokenType.PARAGRAPH, "b"),
    ], ctx)
    assert len(out) == 1
    assert out[0].content == "a\nb"


def test_pipeline_filter_exception_isolation():
    """单个过滤器异常不中断管道（其余 Token 正常返回）。"""
    from src.renderer.pipeline import TokenPipeline, TokenFilter
    from src.renderer.types import Token, TokenType, RenderContext

    class _Boom(TokenFilter):
        def process(self, tokens, ctx):
            raise RuntimeError("boom")

    pipeline = TokenPipeline()
    pipeline.add_filter(_Boom())
    ctx = RenderContext()
    out = pipeline.process([Token(TokenType.PARAGRAPH, "x")], ctx)
    assert len(out) == 1 and out[0].content == "x"


def test_code_batcher_exception_returns_partial(monkeypatch):
    """批处理器内部异常不向外传播，缓冲被清理（下次调用可继续）。"""
    from src.renderer import pipeline as _pipeline
    from src.renderer.types import Token, TokenType, RenderContext

    b = _pipeline.CodeBlockBatcher()
    ctx = RenderContext()
    ctx2 = RenderContext()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(b, "_finish_block", _boom)
    out = b.process([
        Token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
        Token(TokenType.CODE_LINE, "a = 1"),
        Token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
    ], ctx)
    assert isinstance(out, list)
    assert b._block_meta is None and b._buffer == []

    monkeypatch.undo()
    out2 = b.process([
        Token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
        Token(TokenType.CODE_LINE, "b = 2"),
        Token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "python"}),
    ], ctx2)
    assert len(out2) == 1 and out2[0].type is TokenType.CODE_BLOCK
    assert out2[0].content == "b = 2"


def test_ansi_engine_has_no_blockquote_buffer():
    """ANSI 引擎不再依赖单缓冲引用状态（行级即时渲染）。"""
    from src.renderer.ansi.engine import AnsiRenderEngine

    engine = AnsiRenderEngine(width=80)
    assert not hasattr(engine, "_bq_lines")


def test_rich_path_code_block_single_fence():
    """Rich 路径（IncrementalRenderer）超长代码块分段后仍只输出一对围栏。"""
    from io import StringIO
    from src.renderer import IncrementalRenderer

    buf = StringIO()
    r = IncrementalRenderer(show_indicator=False, _file=buf)
    r.write("```python\n")
    for i in range(2005):
        r.write(f"v{i} = {i}\n")
    r.write("```\n")
    r.close()
    text = buf.getvalue()
    assert text.count("```python") == 1, "打开围栏应只出现一次"
    assert text.count("```") == 2, "围栏总数应为开/闭各一次"


@pytest.mark.parametrize("chunks, expected", [
    (["> a\n", "\n"], ["│ a"]),
    (["> > a\n", "\n"], ["│ │ a"]),
])
def test_blockquote_preview_has_depth_prefix(chunks, expected):
    """引用块未闭合时的预览同样带深度前缀（预览与提交一致）。"""
    r = AnsiStreamRenderer(width=80)
    for c in chunks[:-1]:
        r.write(c)
    preview = [l.plain for l in r.take_preview_lines()]
    assert preview == expected


# ═══════════════════════════════════════════════════════════
# 代码块预览：活动行实时刷新 + 高亮缓存不被活动行污染
# ═══════════════════════════════════════════════════════════


def test_code_preview_active_line_refreshed_each_frame():
    """未换行的活动行内容逐帧刷新（原实现按行数增量→显示陈旧文本）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```python\nv")
    shown = "\n".join(ln.plain for ln in r.take_preview_lines())
    assert "v" in shown
    r.write("alue")
    shown = "\n".join(ln.plain for ln in r.take_preview_lines())
    assert "value" in shown


def test_code_preview_rows_align_with_source_lines():
    """预览行与源行一一对应（不重复、不残留陈旧槽位）。"""
    r = AnsiStreamRenderer(width=80)
    body = ["a = 1", "b = 2", "c = 3"]
    for i, line in enumerate(body):
        r.write(line + ("\n" if i < len(body) - 1 else ""))
        r.take_preview_lines()
    rows = [ln.plain for ln in r.take_preview_lines()]
    assert rows[0].startswith("a = 1")
    assert rows[1].startswith("b = 2")
    assert rows[2].startswith("c = 3")
    assert len(rows) == 3


def test_code_preview_active_line_not_cached():
    """活动行不写共享高亮缓存（避免逐帧前缀污染）；升格行以最终内容进缓存。"""
    from src.renderer.ansi import code as _code

    _code._LINE_HIGHLIGHT_CACHE.clear()
    r = AnsiStreamRenderer(width=80)
    r.write("```python\nfoo = 1")
    r.take_preview_lines()
    assert not any(k[2] == "foo = 1" for k in _code._LINE_HIGHLIGHT_CACHE)
    # 行升格（换行）→ 以最终内容渲染并进缓存
    r.write("\nbar = 2")
    r.take_preview_lines()
    assert any(k[2] == "foo = 1" for k in _code._LINE_HIGHLIGHT_CACHE)


def test_code_block_close_reuses_preview_highlight_cache(monkeypatch):
    """代码块闭合时复用预览阶段的行高亮缓存（免整块重新词法高亮）。"""
    from src.renderer.ansi import code as _code

    _code._LINE_HIGHLIGHT_CACHE.clear()
    misses = {"n": 0}
    orig = _code._highlight_line

    def spy(line, lexer, style):
        misses["n"] += 1
        return orig(line, lexer, style)

    monkeypatch.setattr(_code, "_highlight_line", spy)
    r = AnsiStreamRenderer(width=80)
    body = "\n".join(f"x{i} = {i}" for i in range(50))
    r.write("```python\n" + body + "\n")
    r.take_preview_lines()
    misses["n"] = 0
    r.write("```\n\n")  # 闭合围栏
    r.close()
    # 闭合时仅需渲染尚未进缓存的行（活动行/空行），远小于 50 行
    assert misses["n"] <= 3, misses["n"]
