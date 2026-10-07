"""ANSI 流式 Markdown 新增语法测试（HTML / 任务列表 / Fenced 告示 / token 补齐）。

覆盖 TUI 流式路径（``AnsiStreamRenderer`` + ``AnsiRenderEngine``）本轮新增：
  - HTML 块渲染（内联格式保留、实体解码、注释隐藏、缩进、``<br>`` 硬换行）
  - 列表任务取消态 ``[-]``
  - Fenced 告示 ``!!! type "title"`` / ``??? type``（含流式预览一致性）
  - ``LINE_BREAK`` / 旧式 ``BLOCKQUOTE`` token 补齐
  - 硬换行（双空格 / 反斜杠）不产生多余空行
  - 性能：Mermaid/数学预览结果跨帧缓存复用
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.engine import AnsiRenderEngine
from src.renderer.types import Token, TokenType


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _live(chunks, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    out = []
    for c in chunks:
        r.write(c)
        out.append(([l.plain for l in r.take_lines()],
                    [l.plain for l in r.take_preview_lines()]))
    r.close()
    return out


# ── HTML 块 ───────────────────────────────────────────────


def test_html_block_inline_format_and_entities():
    lines = _render("<div>\n  <b>粗体</b> 与 &amp; 实体\n</div>\n")
    joined = "\n".join(lines)
    assert "粗体" in joined
    assert "&" in joined
    assert "&amp;" not in joined
    assert "<b>" not in joined


def test_html_block_comment_hidden():
    lines = _render("<div>\n  前<!-- 隐藏 -->后\n</div>\n")
    joined = "\n".join(lines)
    assert "前后" in joined
    assert "隐藏" not in joined


def test_html_block_br_breaks_line():
    lines = _render("<div>\na<br>b\n</div>\n")
    plains = [p for p in lines if p.strip()]
    assert any(p.strip() == "a" for p in plains)
    assert any(p.strip() == "b" for p in plains)


def test_inline_html_comment_hidden():
    assert _render("前<!-- 注释 -->后\n") == ["前后"]


# ── 任务列表 / 硬换行 ─────────────────────────────────────


def test_task_list_cancelled_state():
    lines = _render("- [x] 完成\n- [ ] 未完成\n- [-] 取消\n")
    assert lines == ["• [x] 完成", "• [ ] 未完成", "• [~] 取消"]


def test_hard_break_no_extra_blank_line():
    assert _render("第一行  \n第二行\n") == ["第一行", "第二行"]
    assert _render("第一行\\\n第二行\n") == ["第一行", "第二行"]


def test_inline_br_no_extra_blank_line():
    assert _render("a<br>b\n") == ["a", "b"]


# ── Fenced 告示 ───────────────────────────────────────────


def test_fenced_admonition_with_title():
    lines = _render('!!! note "重要提示"\n    正文一\n    正文二\n')
    assert lines[0] == "■ NOTE 重要提示"
    assert any("正文一" in p for p in lines)
    assert any("正文二" in p for p in lines)


def test_fenced_admonition_no_title_uses_first_line_as_head():
    lines = _render("!!! tip 提示内容\n")
    assert lines[0] == "■ TIP 提示内容"


def test_fenced_collapsible_admonition():
    lines = _render('??? warning "可折叠"\n    内容\n')
    assert lines[0].startswith("▸ WARNING")


def test_fenced_admonition_unknown_type_is_paragraph():
    assert _render("!!! 未知类型\n") == ["!!! 未知类型"]


def test_fenced_admonition_stream_preview_matches_commit():
    frames = _live(['!!! note "标题"\n', "    第一行\n", "    第二行\n", "\n"])
    assert frames[0][1] == ["■ NOTE 标题"]
    assert frames[1][1] == ["■ NOTE 标题", "    第一行"]
    assert frames[2][1] == ["■ NOTE 标题", "    第一行", "    第二行"]
    assert frames[3][1] == []
    assert frames[3][0] == ["■ NOTE 标题", "    第一行", "    第二行"]


def test_fenced_admonition_ends_at_dedent():
    lines = _render("!!! note\n    内容\n普通段落\n")
    assert any("内容" in p for p in lines)
    assert "普通段落" in lines


# ── Details 折叠块 ────────────────────────────────────────


def test_details_single_line_full():
    assert _render("<details><summary>摘要</summary>内容<br>换行</details>\n") == [
        "\u25b6 摘要", "  内容", "  换行",
    ]


def test_details_without_summary():
    lines = _render("<details>无摘要内容</details>\n")
    assert any("无摘要内容" in p for p in lines)


def test_details_multiline_summary_preserved():
    lines = _render("<details>\n<summary>标题</summary>\n正文\n</details>\n")
    assert "\u25b6 标题" in lines
    assert "  正文" in lines


# ── token 补齐 ────────────────────────────────────────────


def test_line_break_token_renders_blank_line():
    eng = AnsiRenderEngine(width=80)
    lines = eng.render(Token(TokenType.LINE_BREAK))
    assert len(lines) == 1
    assert lines[0].plain == ""


def test_legacy_blockquote_token():
    eng = AnsiRenderEngine(width=80)
    lines = eng.render(Token(TokenType.BLOCKQUOTE, "引用内容", {"depth": 1}))
    assert lines[0].plain == "│ 引用内容"


def test_blockquote_depth_token():
    eng = AnsiRenderEngine(width=80)
    lines = eng.render(Token(TokenType.BLOCKQUOTE, "深层", {"depth": 2}))
    assert lines[0].plain == "│ │ 深层"


# ── 性能：预览缓存 ────────────────────────────────────────


def test_mermaid_preview_cached_across_frames(monkeypatch):
    from src.renderer.ansi import _mermaid_render as _mr
    from src.renderer.ansi.mermaid import clear_mermaid_cache

    calls = {"n": 0}
    orig = _mr._MermaidRenderer.render

    def spy(self, source):
        calls["n"] += 1
        return orig(self, source)

    monkeypatch.setattr(_mr._MermaidRenderer, "render", spy)
    clear_mermaid_cache()

    r = AnsiStreamRenderer(width=80)
    r.write("```mermaid\ngraph TD\nA-->B\n")
    for _ in range(5):
        r.take_preview_lines()
    assert calls["n"] == 1


def test_math_preview_cached_across_frames(monkeypatch):
    from src.renderer.ansi import _math_latex as _ml
    from src.renderer.ansi.math import clear_math_cache

    calls = {"n": 0}
    orig = _ml._LatexRenderer.render

    def spy(self):
        calls["n"] += 1
        return orig(self)

    monkeypatch.setattr(_ml._LatexRenderer, "render", spy)
    clear_math_cache()

    r = AnsiStreamRenderer(width=80)
    r.write("$$\nE = mc^2\n")
    for _ in range(5):
        r.take_preview_lines()
    assert calls["n"] == 1


def test_math_preview_truncates_long_block():
    from src.renderer._block_parser import RegexFreeBlockParser

    limit = RegexFreeBlockParser._PREVIEW_MATH_LINES
    r = AnsiStreamRenderer(width=72)
    r.write("$$\n")
    for i in range(limit + 20):
        r.write(f"x_{i} + y_{i}\n")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert any("行省略" in p for p in preview)
    r.close()


def test_mermaid_preview_truncates_long_block():
    from src.renderer._block_parser import RegexFreeBlockParser

    limit = RegexFreeBlockParser._PREVIEW_MERMAID_LINES
    r = AnsiStreamRenderer(width=72)
    r.write("```mermaid\nflowchart TD\n")
    for i in range(limit + 20):
        r.write(f"  N{i} --> N{i + 1}\n")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert any("行省略" in p for p in preview)
    r.close()


# ── TUI 内容块集成（model/apply 路径） ────────────────────


def test_tui_content_block_renders_new_syntax():
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="$$\nE = mc^2\n$$\n"))
    _do_content(m, ContentCmd(text="```mermaid\ngraph TD; A-->B\n```\n"))
    _do_content(m, ContentCmd(text="!!! note \"提示\"\n    正文\n"))
    _do_content(m, ContentCmd(text="\n"))
    blk = m.blocks[m.content_block_index]
    text = "\n".join(line.plain for line in blk.lines)
    assert "E = mc\u00b2" in text
    assert "\u25bc" in text          # mermaid 箭头
    assert "\u25a0 NOTE \u63d0\u793a" in text  # ■ NOTE 提示
    assert "\u6b63\u6587" in text
