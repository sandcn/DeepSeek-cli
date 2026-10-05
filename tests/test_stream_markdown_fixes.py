"""流式 Markdown 渲染修复测试。

覆盖本次修复的多个问题：
  - details / fenced div 块内正文与 summary 渲染（问题4）
  - 流式预览：段落 / 代码块 / 表格实时渲染（问题1）
  - 跨 chunk 语法状态（列表续行 / 优化器 / 异常缓冲）（问题2）
  - 事件层节流定时器兜底（问题3）
  - TOC / 引用链接输出位置（问题7）
  - ANSI 消毒保留合法 SGR（问题8）
"""
from __future__ import annotations

import time

from src.renderer.ansi import AnsiStreamRenderer


def _render(chunks, width=80):
    """流式喂入 chunks，close 后返回全部行明文。"""
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    r.close()
    return [line.plain for line in r.take_lines()]


def _render_live(chunks, width=80):
    """逐 chunk 返回 (chunk, committed 行, preview 行)。"""
    r = AnsiStreamRenderer(width=width)
    out = []
    for c in chunks:
        r.write(c)
        committed = [line.plain for line in r.take_lines()]
        preview = [line.plain for line in r.take_preview_lines()]
        out.append((c, committed, preview))
    r.close()
    return out


# ── 问题4：details / fenced div 正文 ──────────────────────


def test_details_body_rendered():
    lines = _render([
        "<details>\n", "<summary>点我</summary>\n",
        "折叠正文\n", "第二行\n", "</details>\n",
    ])
    text = "\n".join(lines)
    assert "点我" in text
    assert "折叠正文" in text
    assert "第二行" in text


def test_fenced_div_body_rendered():
    lines = _render([":::note 标题\n", "div 正文\n", "第二行\n", ":::\n"])
    text = "\n".join(lines)
    assert "NOTE" in text
    assert "标题" in text
    assert "div 正文" in text
    assert "第二行" in text


# ── 问题1：段落实时预览 ──────────────────────────────────


def test_paragraph_preview_live():
    """段落未闭合时通过 preview 实时可见（不再等到块结束）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("第一行\n")
    assert [l.plain for l in r.take_lines()] == []
    preview = [l.plain for l in r.take_preview_lines()]
    assert preview == ["第一行"]
    r.write("第二行\n")
    r.take_lines()
    preview = [l.plain for l in r.take_preview_lines()]
    assert preview == ["第一行", "第二行"]
    r.close()
    committed = [l.plain for l in r.take_lines()]
    assert committed == ["第一行", "第二行"]
    # 关闭后预览清空
    assert r.take_preview_lines() == []


def test_paragraph_preview_becomes_heading():
    """段落行后续被 setext 变为标题时，预览被替换（无重复）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("标题文字\n")
    assert [l.plain for l in r.take_preview_lines()] == ["标题文字"]
    r.write("=====\n")
    r.take_lines()
    assert [l.plain for l in r.take_preview_lines()] == []


# ── 问题1：代码块实时预览 ────────────────────────────────


def test_code_block_preview_live():
    """未闭合代码块通过 preview 实时可见，且不含伪造的关闭围栏。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    r.write("print(1)\n")
    r.take_lines()
    preview = [l.plain for l in r.take_preview_lines()]
    assert any("print(1)" in p for p in preview)
    # 未闭合：不应出现关闭围栏
    assert not any(p.strip() == "```" for p in preview)
    r.write("```\n")
    committed = [l.plain for l in r.take_lines()]
    assert any("print(1)" in c for c in committed)
    assert r.take_preview_lines() == []
    r.close()


# ── 问题1：表格实时预览 ──────────────────────────────────


def test_table_preview_live():
    """未闭合表格通过 preview 实时可见。"""
    r = AnsiStreamRenderer(width=80)
    r.write("| a | b |\n")
    r.write("| - | - |\n")
    r.write("| 1 | 2 |\n")
    r.take_lines()
    preview = "\n".join(l.plain for l in r.take_preview_lines())
    assert "a" in preview and "1" in preview
    r.write("\n")
    r.take_lines()
    assert r.take_preview_lines() == []

# ── 问题2：跨 chunk 语法状态 ──────────────────────────────


def test_list_continuation_cross_chunk():
    """列表续行跨 chunk 与同 chunk 渲染一致（不再降级为普通段落）。"""
    same = _render(["- 第一项\n  续行内容\n- 第二项\n"])
    r = AnsiStreamRenderer(width=80)
    r.write("- 第一项\n")
    r.write("  续行内容\n")
    r.write("- 第二项\n")
    r.close()
    cross = [line.plain for line in r.take_lines()]
    assert same == cross == ["• 第一项", "  续行内容", "• 第二项"]


def test_parser_exception_keeps_buffer():
    """行解析异常不清空整段缓冲（后续行继续处理）。"""
    from src.renderer._block_parser import RegexFreeBlockParser
    p = RegexFreeBlockParser()
    original = p._parse_normal_line
    calls = {"n": 0}

    def boom(line, tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(line, tokens)

    p._parse_normal_line = boom
    tokens = p.feed("异常行\n第二行\n第三行\n")
    tokens.extend(p.flush())
    text = "\n".join(t.content for t in tokens)
    assert "第二行" in text or "第三行" in text


def test_stream_optimizer_cross_feed_state():
    """TokenStreamOptimizer 跨 feed 状态保留（连续空行压缩跨 chunk 生效）。"""
    from src.renderer.pipeline_filters.stream_optimizer import TokenStreamOptimizer
    from src.renderer.types import Token, TokenType, RenderContext
    opt = TokenStreamOptimizer()
    ctx = RenderContext()
    t1 = opt.process([Token(TokenType.PARAGRAPH, "a"), Token(TokenType.EMPTY_LINE)], ctx)
    # 跨 feed：新空行 + 段落 —— 上一个空行被压缩（不重复产出空行）
    t2 = opt.process([Token(TokenType.EMPTY_LINE), Token(TokenType.PARAGRAPH, "b")], ctx)
    combined = [t.type for t in (t1 + t2)]
    assert combined.count(TokenType.EMPTY_LINE) <= 1


# ── 问题3：事件层节流定时器兜底 ────────────────────────────


def test_stream_handler_timer_flush(monkeypatch):
    """时间节流命中时调度定时器兜底 flush（缓冲不滞留）。"""
    from src.api.stream.handlers import _base
    published: list = []
    monkeypatch.setattr(
        _base, "publish_event",
        lambda evt, **kw: published.append((evt, kw)),
    )

    class _H(_base.StreamChunkHandler):
        _EVENT_TYPE = "TestChunkEvent"

        def handle(self, ctx, text, token_est=None):
            self.buffer(text, "lbl")

    h = _H()
    h._MIN_INTERVAL = 0.05
    h._last_flush_time = time.time()  # 首次必被节流
    h.buffer("abc", "lbl")
    assert published == []  # 节流命中，未立即发布
    deadline = time.time() + 1.0
    while not published and time.time() < deadline:
        time.sleep(0.02)
    assert published, "定时器未兜底 flush"
    assert published[0][0] == "TestChunkEvent"
    assert published[0][1].get("text") == "abc"

# ── 问题7：TOC 位置 ──────────────────────────────────────


def test_no_auto_toc_appendix():
    """无 [TOC] 标记时不在末尾自动追加目录。"""
    lines = _render(["# 标题\n", "正文内容\n"])
    text = "\n".join(lines)
    assert "目录" not in text
    assert "标题" in text


def test_toc_marker_renders_in_place():
    """[TOC] 标记处就地渲染目录。"""
    lines = _render(["# 一级标题\n", "# 二级标题\n", "[TOC]\n"])
    text = "\n".join(lines)
    assert "目录" in text


# ── 问题8：ANSI 消毒保留合法 SGR ─────────────────────────


def test_take_lines_keeps_sgr_color():
    """合法 SGR 颜色序列解析为 Run 样式保留（不再一律剥离）。"""
    from src.renderer.ansi.helpers import AnsiLine
    r = AnsiStreamRenderer(width=80)
    r._lines.append(AnsiLine.of("\x1b[31m红色文本\x1b[0m"))
    lines = r.take_lines()
    assert lines[0].plain == "红色文本"
    assert lines[0].runs[0].style is not None
    assert lines[0].runs[0].style.fg == 31


def test_take_lines_removes_stray_esc():
    """非 SGR 控制序列（CSI 清屏等）移除，文本保留。"""
    from src.renderer.ansi.helpers import AnsiLine
    r = AnsiStreamRenderer(width=80)
    r._lines.append(AnsiLine.of("前\x1b[2J后"))
    lines = r.take_lines()
    assert "\x1b" not in lines[0].plain
    assert "前后" in lines[0].plain

# ── 问题6：resize 重排未关闭块 ───────────────────────────


def test_rerender_open_block_on_width_change():
    """未关闭块在宽度变化时按新宽度整块重渲染（表格重排）。"""
    from src.tui.app.model import AppModel
    m = AppModel()
    m.ensure_content()
    blk = m.blocks[m.content_block_index]
    blk.source_text = "| col1 | col2 |\n| --- | --- |\n| xxxxxxxx | yyyyyyyy |\n"
    assert m._rerender_open_block(blk, 80)
    wide = max((line.width for line in blk.lines), default=0)
    assert m._rerender_open_block(blk, 24)
    narrow = max((line.width for line in blk.lines), default=0)
    assert narrow <= 24
    assert wide >= narrow


# ── 问题1：UI 层未闭合块预览（model/apply 集成） ─────────


def test_model_content_preview_lines():
    """内容块流式期间 preview_lines 更新（段落未闭合实时可见）。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd
    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="正在输出第一行\n"))
    blk = m.blocks[m.content_block_index]
    assert [line.plain for line in blk.preview_lines] == ["正在输出第一行"]
    # 空行结束段落 → 确定行进入 lines，预览清空
    _do_content(m, ContentCmd(text="\n"))
    assert [line.plain for line in blk.preview_lines] == []
    assert any("正在输出第一行" in line.plain for line in blk.lines)

# ── 问题5：强制刷出保留语言 ───────────────────────────────


def test_code_batcher_force_flush_keeps_lang():
    """CodeBlockBatcher 强制刷出后补的 fence 沿用原语言（不再固定 text）。"""
    from src.renderer.pipeline import CodeBlockBatcher
    from src.renderer.types import Token, TokenType, RenderContext
    b = CodeBlockBatcher()
    b.MAX_BUFFER_LINES = 3
    ctx = RenderContext()
    toks = [Token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"})]
    toks += [Token(TokenType.CODE_LINE, f"line{i}") for i in range(4)]
    out = b.process(toks, ctx)
    opens = [t for t in out if t.type is TokenType.CODE_FENCE_OPEN]
    assert opens, "未触发强制刷出/补 fence"
    assert any(t.meta.get("lang") == "python" for t in opens)

def test_chat_view_block_includes_preview():
    """ChatView 的 open 块渲染包含 preview_lines（UI 层预览可见）。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui.app.chat_view import _block_styled_lines
    from src.tui._const import ContentCmd
    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="预览段落\n"))
    blk = m.blocks[m.content_block_index]
    rows = _block_styled_lines(blk, blk.committed_line_count, 80)
    texts = ["".join(r.text for r in row) for row in rows]
    assert any("预览段落" in t for t in texts)

def test_empty_code_block_no_blank_line():
    """空代码块不产生多余的空代码行（仅围栏行）。"""
    lines = _render(["```js\n", "```\n"])
    assert lines == ["```js [js]", "```"]
