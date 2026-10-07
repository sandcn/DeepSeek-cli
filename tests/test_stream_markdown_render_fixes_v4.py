"""TUI 流式 Markdown 渲染缺陷修复回归（第四批）。

覆盖本轮修复：

  - **脚注编号顺序不一致（前向引用）**：脚注编号顺序的唯一真源应为「引用
    出现顺序」。修复前有两处副作用会按「定义顺序」提前定序——
    ``_prescan_refs``（预扫描在解析前读完整文档、把文末定义先入序）与
    ``_handle_fn_def``（定义行解析时入序）；一次性渲染 / 历史回放的编号
    因此与流式增量渲染（引用先入序）不同（同一文档两套编号）；
  - **预览渲染污染脚注注册表**：未闭合定义行的头部 ``[^a]`` 在预览时被当
    脚注引用渲染、写入 ``fn_order``——提交渲染的编号顺序被临时状态污染；
    现预览前后保存/恢复 ``fn_order``/``fn_map``；
  - **预览渲染异常隔离**：预览是未闭合块的附加显示，其渲染异常不得中断
    ``write``（否则已产出的 committed 行整段丢失）。
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 60, chunk: int | None = None):
    r = AnsiStreamRenderer(width=width)
    if chunk:
        for i in range(0, len(src), chunk):
            r.write(src[i:i + chunk])
    else:
        r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ══════════════════════════════════════════════════════════
# 脚注编号顺序：引用顺序（一次性 / 流式一致）
# ══════════════════════════════════════════════════════════

FWD_REF_DOC = "text[^b] and text2[^a]\n\n[^a]: A note\n\n[^b]: B note\n"


def test_footnote_numbering_follows_reference_order():
    """引用在前、定义在后：编号按引用出现顺序（b=1, a=2）。"""
    lines = _render(FWD_REF_DOC)
    assert lines[0] == "text[1] and text2[2]", lines
    assert lines[-2] == "  [1] B note \u21a9"
    assert lines[-1] == "  [2] A note \u21a9"


def test_footnote_numbering_streamed_matches_oneshot():
    """流式（多种 chunk）编号与一次性渲染完全一致（修复前互换）。"""
    oneshot = _render(FWD_REF_DOC)
    for chunk in (1, 3, 7, 11):
        assert _render(FWD_REF_DOC, chunk=chunk) == oneshot, chunk


def test_footnote_numbering_definitions_first_still_reference_order():
    """定义在文档开头：编号仍按引用出现顺序（与定义顺序无关）。"""
    src = "[^a]: A\n\n[^b]: B\n\ntext[^b] then text2[^a]\n"
    oneshot = _render(src)
    assert oneshot[0] == "text[1] then text2[2]", oneshot
    for chunk in (1, 3, 5):
        assert _render(src, chunk=chunk) == oneshot, chunk


def test_unused_footnotes_listed_alphabetically_tail():
    """未引用定义按 ref_id 字母序排在末尾（流式一致）。"""
    src = "ref[^1]\n\n[^1]: used\n\n[^z]: unused\n\n[^m]: unused2\n"
    oneshot = _render(src)
    assert oneshot[0] == "ref[1]"
    assert oneshot[3:] == ["  [2] unused2 \u21a9", "  [3] unused \u21a9"], oneshot
    for chunk in (2, 5):
        assert _render(src, chunk=chunk) == oneshot, chunk


def test_inline_footnote_shares_reference_order():
    """行内脚注与定义式脚注共用引用顺序编号（流式一致）。"""
    src = "x[^b] ^[inline one] y[^a]\n\n[^a]: A\n\n[^b]: B\n"
    oneshot = _render(src)
    assert oneshot[0] == "x[1] [2] y[3]", oneshot
    for chunk in (1, 4):
        assert _render(src, chunk=chunk) == oneshot, chunk


def test_preview_does_not_pollute_footnote_order():
    """未闭合定义行的 ``[^a]`` 预览渲染不得污染编号顺序。"""
    r = AnsiStreamRenderer(width=60)
    r.write("[^a]: A\n\n[^b]: B\n\ntext[^b] then text2[^a]\n")
    # 段落仍在未闭合状态（预览渲染引用）——预览的临时编号已回滚，
    # 主注册表不含任何引用（定义行不再提前定序）。
    assert r._ctx.fn_order == [], r._ctx.fn_order
    r.close()
    lines = [ln.plain for ln in r.take_lines()]
    assert lines[0] == "text[1] then text2[2]", lines


def test_preview_rollback_keeps_fn_map():
    """预览回滚不丢失已提交渲染登记的脚注内容。"""
    r = AnsiStreamRenderer(width=60)
    r.write("see[^1]\n\n[^1]: body\n\n[^2]: other\n\nmore text")
    assert r._ctx.fn_map.get("1") == "body"
    assert r._ctx.fn_map.get("2") == "other"
    r.close()
    joined = "\n".join(ln.plain for ln in r.take_lines())
    assert "body" in joined and "other" in joined


# ══════════════════════════════════════════════════════════
# 预览渲染异常隔离
# ══════════════════════════════════════════════════════════


def test_preview_render_exception_does_not_break_write():
    """预览渲染抛异常时 ``write`` 不中断，committed 行正常产出。"""
    r = AnsiStreamRenderer(width=40)

    def _boom(tok, eng):
        raise RuntimeError("preview boom")

    r._render_preview_token = _boom  # type: ignore[assignment]
    r.write("第一段已闭合\n\n")
    r.write("第二段未闭合")  # 触发预览（异常被隔离）
    assert [ln.plain for ln in r.take_lines()] == ["第一段已闭合"]
    assert [ln.plain for ln in r.take_preview_lines()] == []
    # 恢复后继续：内容完整（预览异常未丢内容）
    r.write("继续\n\n")
    r.close()
    assert [ln.plain for ln in r.take_lines()] == ["第二段未闭合继续"]
