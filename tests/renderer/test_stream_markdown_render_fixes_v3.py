"""TUI 流式 Markdown 渲染缺陷修复回归（第三批）。

覆盖本轮修复：

  - **嵌套引用空行预览崩溃**：``sliding_drop`` 在 ``ignore_tail=True`` 且新
    源行为空（``n`` 被减成 -1，绕过 ``n == 0`` 判空）时 ``b[0]`` 抛
    ``IndexError``——逐字符流式渲染 ``>>o\\n>>e\\n>>\\n>`` 时预览线程崩溃、
    后续内容整段丢失；
  - **引用块内的前向引用不重渲染**：``_FORWARD_REF_RE`` 只认行首标记，
    ``> [1]: url`` / ``> > [TOC]`` 不触发关闭重渲染，引用块内流式渲染的
    参考式链接永久停留为 ``[?1]`` 占位（与一次性渲染不一致）；
  - **resize 后已关闭块表格错乱**：``reflow_committed`` 原仅对未关闭块按源
    重渲染，已关闭块只逐行 ``wrap``——表格框线被拆断、单元格跨行；现对
    关闭块同样按源文本整块重渲染（历史回放 / subagent 块亦保留源文本）。
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi._line_match import sliding_drop
from src.tui._const import DisplayMsgsCmd
from src.tui.app.apply import (
    _do_display_messages,
    _do_subagent_markdown,
    _flush_renderer_to_block,
)
from src.tui.app.model import AppModel
from src.tui._const import SubagentMarkdownCmd


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _per_char(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for ch in src:
        r.write(ch)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _tui_stream(md: str, width: int = 60, chunk: int = 1):
    model = AppModel()
    model.width = width
    renderer = model.ensure_content()
    for i in range(0, len(md), chunk):
        part = md[i:i + chunk]
        renderer.write(part)
        _flush_renderer_to_block(model, "content", renderer, source_delta=part)
    model.close_content()
    return model, [ln.plain for ln in model.committed_lines]


# ══════════════════════════════════════════════════════════
# sliding_drop 空列表防御（嵌套引用空行预览崩溃）
# ══════════════════════════════════════════════════════════


def test_sliding_drop_empty_b_with_ignore_tail():
    """空新源行 + ``ignore_tail`` 不得崩溃（返回 0）。"""
    assert sliding_drop(["a", "b", "c"], [], ignore_tail=True) == 0
    assert sliding_drop(["a", "b"], [""], ignore_tail=True) == 0


def test_sliding_drop_still_detects_window():
    """修复判空后滑窗复用语义不变。"""
    assert sliding_drop(["a", "b", "c", "d"], ["c", "d", "e", "f"],
                        ignore_tail=True) == 2


def test_nested_quote_empty_line_stream_no_crash():
    """嵌套引用空行逐字符流式渲染不抛异常且与一次性渲染一致。"""
    src = ">>o\n>>e\n>>\n>\n"
    assert _per_char(src) == _render(src)


def test_nested_quote_empty_line_tui_stream_no_crash():
    """TUI 路径逐字符流式渲染同上（异常会导致后续内容整段丢失）。"""
    src = ">>o\n>>e\n>>\n>\n"
    _model, per_char = _tui_stream(src, width=40, chunk=1)
    _model2, oneshot = _tui_stream(src, width=40, chunk=len(src))
    assert per_char == oneshot


# ══════════════════════════════════════════════════════════
# 引用块内的前向引用（[TOC] / 参考式链接定义）关闭重渲染
# ══════════════════════════════════════════════════════════


def test_forward_ref_regex_matches_container_prefix():
    from src.tui.app.model import _FORWARD_REF_RE

    assert _FORWARD_REF_RE.search("> [1]: http://example.com\n")
    assert _FORWARD_REF_RE.search("> > [TOC]\n")
    assert _FORWARD_REF_RE.search("> > > [ref]: http://a.com\n")
    assert _FORWARD_REF_RE.search("[1]: http://example.com\n")
    # 缩进 4 空格是代码块，不应命中
    assert not _FORWARD_REF_RE.search("    [1]: http://example.com\n")
    # 脚注定义 / 注释不纳入
    assert not _FORWARD_REF_RE.search("> [^1]: note\n")
    assert not _FORWARD_REF_RE.search("> [//]: # comment\n")


def test_quote_forward_reference_link_resolved_on_close():
    """引用块内的参考式链接在关闭重渲染后解析（不再残留 [?1]）。"""
    md = "> > see [ref][1]\n> > \n> > [1]: http://example.com\n"
    per_char = _tui_stream(md, width=60, chunk=1)[1]
    oneshot = _tui_stream(md, width=60, chunk=len(md))[1]
    assert per_char == oneshot
    assert any("http://example.com" in p for p in per_char)
    assert not any("[?1]" in p for p in per_char)


def test_quote_toc_resolved_on_close():
    """引用块内的 [TOC] 关闭重渲染后完整（与一次性渲染一致）。"""
    md = "> > [TOC]\n\n> > # A\n\n> > ## B\n"
    per_char = _tui_stream(md, width=60, chunk=1)[1]
    oneshot = _tui_stream(md, width=60, chunk=len(md))[1]
    assert per_char == oneshot
    assert any("目录" in p for p in per_char)


# ══════════════════════════════════════════════════════════
# resize：已关闭块表格重排
# ══════════════════════════════════════════════════════════


def _reflow(md: str, w1: int, w2: int):
    model, _lines = _tui_stream(md, width=w1, chunk=len(md))
    model.width = w1
    model.reflow_committed(w2)
    return [ln.plain for ln in model.committed_lines]


def test_reflow_closed_block_rerenders_wide_table():
    """终端变窄后已关闭 content 块的宽表格按新宽度重画（框线完整）。"""
    md = ("| name | value |\n|------|-------|\n"
          "| alpha-long-content | 11111 |\n| beta | 22222 |\n")
    rows = _reflow(md, 60, 24)
    body = [r for r in rows if r and r != ""]
    assert all(len(r) <= 24 for r in body), body
    # 框线完整（每行首尾为表格边框字符，未被 wrap 拆断）
    table = [r for r in body if r.startswith(("┌", "│", "├", "└"))]
    assert table
    for r in table:
        assert r[0] in "┌│├└" and len(r) <= 24
    assert sum(1 for r in table if r.startswith("┌")) == 1
    assert sum(1 for r in table if r.startswith("└")) == 1


def test_reflow_closed_block_matches_native_width_render():
    """resize 重排结果与直接用新宽度渲染一致（同源）。"""
    md = ("| name | value |\n|------|-------|\n"
          "| alpha-long-content | 11111 |\n| beta | 22222 |\n")
    rows = _reflow(md, 60, 30)
    direct, _ = _tui_stream(md, width=30, chunk=len(md))
    assert rows == [ln.plain for ln in direct.committed_lines]


def test_reflow_history_replay_block_rerenders_table():
    """历史回放块（/editmsg 等）resize 后表格同样重排。"""
    model = AppModel()
    model.width = 60
    msg = {
        "role": "assistant",
        "content": ("| name | value |\n|------|-------|\n"
                    "| alpha-long-content | 11111 |\n"),
    }
    _do_display_messages(model, DisplayMsgsCmd(messages=[msg]))
    model.reflow_committed(24)
    rows = [ln.plain for ln in model.committed_lines]
    assert all(ln.width <= 24 for ln in model.committed_lines)
    assert sum(1 for r in rows if r.startswith("┌")) == 1


def test_subagent_markdown_block_keeps_source_for_reflow():
    """subagent markdown 块保留源文本（resize 可重排）。"""
    model = AppModel()
    model.width = 40
    _do_subagent_markdown(model, SubagentMarkdownCmd(text="# 标题\n\n正文\n"))
    assert model.blocks[-1].source_text == "# 标题\n\n正文\n"
    model.reflow_committed(70)
    rows = [ln.plain for ln in model.committed_lines]
    assert any("标题" in r for r in rows)
