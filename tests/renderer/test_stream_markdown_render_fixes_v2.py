"""TUI 流式 Markdown 渲染缺陷修复回归（第二批）。

覆盖本轮修复：

  - **Fenced 告示无同行标题**（``!!! note`` / ``??? note``）：正文首行不再被
    提升为头部——修复前单行正文重复显示、多行正文首行被吞、列表/标题/代码
    围栏首行作为头部泄漏原始 markdown；
  - **容器未换行活动行归一化**：告示（4 空格缩进 / ``>`` 前缀）、``<details>``、
    fenced div 的当前活动行按 ``feed`` 同规则归一化——修复前预览把缩进当
    缩进代码块（冒出 ``` 围栏）、把 ``>`` 当引用（渲染成 ``│ body``），与提交
    结果跳变；
  - **容器头尾随空格**：无标题/无同行文本时不再多一个尾随空格；
  - **单行显示数学** ``$$ ... $$``：走数学块渲染，不再泄漏字面 ``$``；
  - **行内 ``$`` 定界规则**：金额 ``$5`` 不再被当公式吞掉；``$$...$$`` 成对
    解析为行内显示数学；
  - **嵌套列表符号**：与 Rich 路径同表（第 4 层起不再重复 ``▪``）；
  - **TUI 关闭时前向引用重渲染**：``[TOC]`` 目录 / 参考式链接定义等「内容
    取决于标记之后文本」的结构，块关闭时按完整源文本整块重渲染，最终渲染
    与一次性渲染一致。
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.tui.app.apply import _flush_renderer_to_block
from src.tui.app.model import AppModel


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _preview(src: str, width: int = 72):
    """逐字符写入后的未闭合块预览行（不 close）。"""
    r = AnsiStreamRenderer(width=width)
    for ch in src:
        r.write(ch)
    return [ln.plain for ln in r.take_preview_lines()]


def _tui_stream(md: str, width: int = 60, chunk: int = 3):
    """按 apply 的真实路径流式写入内容块 → 返回 (model, committed 明文)。"""
    model = AppModel()
    model.width = width
    renderer = model.ensure_content()
    for i in range(0, len(md), chunk):
        part = md[i:i + chunk]
        renderer.write(part)
        _flush_renderer_to_block(model, "content", renderer, source_delta=part)
    model.close_content()
    return model, [ln.plain for ln in model.committed_lines]


def _tui_body(md: str, **kwargs):
    """TUI 流式内容的正文行（去角色头与卡片尾空行）。"""
    _model, lines = _tui_stream(md, **kwargs)
    if lines and lines[0].startswith("\u258d"):
        lines = lines[1:]
    while lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


# ══════════════════════════════════════════════════════════
# Fenced 告示：无同行标题时正文首行不再被当作头部
# ══════════════════════════════════════════════════════════


def test_fenced_admonition_single_line_body_not_duplicated():
    lines = _render("!!! note\n    body\n")
    assert lines == ["\u25a0 NOTE", "    body"]


def test_fenced_admonition_multiline_body_keeps_first_line():
    lines = _render("!!! warning\n    first\n    second\n    third\n")
    assert lines[0] == "\u25a0 WARNING"
    assert lines[1:] == ["    first", "    second", "    third"]


def test_fenced_admonition_list_body_not_consumed_as_head():
    lines = _render("!!! note\n    - a\n    - b\n")
    assert lines[0] == "\u25a0 NOTE"
    assert lines[1] == "    \u2022 a"
    assert lines[2] == "    \u2022 b"


def test_fenced_admonition_heading_body_not_raw_markdown_in_head():
    lines = _render("!!! note\n    ## heading\n    body\n")
    assert lines[0] == "\u25a0 NOTE"
    assert all(p.startswith("    ") for p in lines[1:])


def test_fenced_admonition_code_block_body_intact():
    lines = _render("!!! note\n    ```python\n    x = 1\n    ```\n")
    assert lines[0] == "\u25a0 NOTE"
    assert lines[-1] == "    ```"
    assert any("x = 1" in p for p in lines)
    assert not any(p.startswith("\u25a0 NOTE `") for p in lines)


def test_fenced_admonition_inline_title_still_works():
    lines = _render("!!! tip 提示内容\n")
    assert lines == ["\u25a0 TIP 提示内容"]


def test_fenced_collapsible_admonition_body_not_duplicated():
    lines = _render("??? note\n    hidden\n")
    assert lines == ["\u25b8 NOTE", "    hidden"]


def test_reference_admonition_then_paragraph_keeps_order():
    """引用风格告示后紧跟未引用行（无空行分隔）：正文不得排到告示之前。"""
    assert _render("> [!NOTE]\n> body\ntail\n") == [
        "\u25a0 NOTE", "    body", "tail",
    ]
    assert _render("> [!WARNING]\n> - a\nafter para\n") == [
        "\u25a0 WARNING", "    \u2022 a", "after para",
    ]


# ══════════════════════════════════════════════════════════
# 容器未换行活动行归一化：预览与提交一致
# ══════════════════════════════════════════════════════════


def test_fenced_admonition_preview_tail_is_dedented():
    prev = _preview("!!! note\n    - a\n    - b")
    assert prev == ["\u25a0 NOTE", "    \u2022 a", "    \u2022 b"]


def test_fenced_admonition_preview_matches_commit():
    md = "!!! note\n    - a\n    - b\n"
    assert _preview(md.rstrip("\n")) == ["\u25a0 NOTE", "    \u2022 a", "    \u2022 b"]
    assert _render(md) == ["\u25a0 NOTE", "    \u2022 a", "    \u2022 b"]


def test_admonition_ref_preview_tail_strips_quote_prefix():
    prev = _preview("> [!NOTE]\n> body")
    assert prev == ["\u25a0 NOTE", "    body"]


def test_admonition_ref_preview_matches_commit():
    md = "> [!NOTE]\n> - a\n> - b\n"
    assert _preview(md.rstrip("\n")) == _render(md)


def test_details_preview_matches_commit():
    md = "<details><summary>S</summary>\n- a\n- b\n</details>\n"
    assert _preview("<details><summary>S</summary>\n- a\n- b") == [
        "\u25b6 S", "  \u2022 a", "  \u2022 b",
    ]
    assert _render(md)[0] == "\u25b6 S"


def test_fenced_div_preview_matches_commit():
    md = "::: warning\n- a\n- b\n:::\n"
    assert _preview("::: warning\n- a\n- b") == [
        "\u25aa WARNING", "  \u2022 a", "  \u2022 b",
    ]
    assert _render(md) == ["\u25aa WARNING", "  \u2022 a", "  \u2022 b"]


# ══════════════════════════════════════════════════════════
# 容器头尾随空格
# ══════════════════════════════════════════════════════════


def test_container_head_without_text_has_no_trailing_space():
    assert _render("> [!TIP]\n> body\n")[0] == "\u25a0 TIP"
    assert _render("::: warning\ncontent\n:::\n")[0] == "\u25aa WARNING"
    assert _render("!!! note\n    body\n")[0] == "\u25a0 NOTE"


# ══════════════════════════════════════════════════════════
# 数学：单行 $$ 块 + 行内 $ 定界规则
# ══════════════════════════════════════════════════════════


def test_single_line_display_math_renders_block():
    lines = _render("$$x=1$$\n")
    assert lines[0].startswith("\u256d")
    assert any("x=1" in p for p in lines)
    assert not any("$" in p for p in lines)


def test_single_line_display_math_fraction_uses_math_box():
    lines = _render("$$\\frac{a}{b}$$\n")
    assert lines[0].startswith("\u256d")
    assert not any("$" in p for p in lines)


def test_multiline_display_math_unchanged():
    lines = _render("$$\nx=1\n$$\n")
    assert lines[0].startswith("\u256d")
    assert any("x=1" in p for p in lines)


def test_inline_math_currency_not_swallowed():
    assert _render("It costs $5 and $6 today.\n") == ["It costs $5 and $6 today."]
    assert _render("price $5.\n") == ["price $5."]


def test_inline_math_still_works():
    assert _render("value $x^2$ end\n") == ["value x\u00b2 end"]


def test_inline_display_math_pair_rendered_without_dollars():
    assert _render("a $$x^2$$ b\n") == ["a x\u00b2 b"]


def test_unpaired_dollar_kept_literal():
    assert _render("$ x $\n") == ["$ x $"]


# ══════════════════════════════════════════════════════════
# 嵌套列表符号（与 Rich 路径同表）
# ══════════════════════════════════════════════════════════


def test_nested_list_bullets_distinct_up_to_six_levels():
    src = ("- a\n"
           "  - b\n"
           "    - c\n"
           "      - d\n"
           "        - e\n"
           "          - f\n")
    lines = _render(src)
    markers = [ln.strip().split(" ")[0] for ln in lines]
    assert len(set(markers)) == 6, markers


# ══════════════════════════════════════════════════════════
# TUI 关闭时前向引用重渲染
# ══════════════════════════════════════════════════════════


def test_tui_close_completes_toc():
    md = "[TOC]\n\n# A\n\n## B\n"
    assert _tui_body(md) == _render(md, width=60)


def test_tui_close_resolves_forward_reference_link():
    md = "see [ref][1]\n\n[1]: http://example.com\n"
    body = _tui_body(md)
    assert any("\u2460" in p for p in body), body
    assert not any("[?1]" in p for p in body), body
    assert body == _render(md, width=60)


def test_tui_close_rerender_not_triggered_by_code_block_regex_class():
    """代码块里的正则字符组 ``[^...]`` 不应触发整块重渲染。"""
    model = AppModel()
    model.width = 60
    renderer = model.ensure_content()
    md = "```python\npattern = r\"[^abc]+\"\n```\n"
    renderer.write(md)
    _flush_renderer_to_block(model, "content", renderer, source_delta=md)
    block = model.blocks[model.content_block_index]
    assert model._needs_final_rerender(block) is False


def test_tui_plain_content_unaffected_by_rerender():
    md = "# H\n\nhello **world**\n\n- a\n- b\n"
    body = _tui_body(md)
    assert body == _render(md, width=60)


def test_tui_truncated_source_keeps_incremental_render():
    """源文本超上限（不可重渲染）时不得丢内容。"""
    md = "see [ref][1]\n\n[1]: http://example.com\n"
    model = AppModel()
    model.width = 60
    renderer = model.ensure_content()
    renderer.write(md)
    _flush_renderer_to_block(model, "content", renderer, source_delta=md)
    block = model.blocks[model.content_block_index]
    block.extra["source_truncated"] = True
    model.close_content()
    lines = [ln.plain for ln in model.committed_lines]
    assert any("http://example.com" in p for p in lines)
