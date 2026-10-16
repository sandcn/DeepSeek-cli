"""TUI 流式 Markdown 渲染：折行续行悬挂缩进（行首对齐）回归。

覆盖本轮修复：行首带固定标记 / 缩进的渲染行（列表项 ``1. `` / ``• ``、
任务列表复选框、引用 ``│ ``、容器正文缩进）在终端宽度折行时，**续行行首
与首行内容起始列对齐**——修复前续行顶格列 0，长列表项折行后视觉上像新的
顶层内容（用户报告：有序列表第 4/7 项的续行与项目正文错位）。

实现契约：
  - ``AnsiLine.cont_prefix`` 承载续行前缀（悬挂缩进），``wrap_line`` 折行时
    续行以它起始、可用宽度扣减其宽度；
  - 前缀为空（普通段落 / 代码等）时行为与性能路径完全不变（快路径保持）；
  - 前缀宽到放不下（窄屏）时自动禁用，保证「行宽 <= 终端宽度」不变量。
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════

def _render_lines(src: str, width: int = 80):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return r.take_lines()


def _wrapped(src: str, width: int) -> list[str]:
    """渲染 markdown 源并按其宽度折行 → 纯文本行列表。"""
    out: list[str] = []
    for ln in _render_lines(src, width):
        out.extend(w.plain for w in wrap_line(ln, width))
    return out


def _streamed(src: str, width: int, chunk: int = 3) -> list[str]:
    """逐 chunk 流式写入（走预览路径）后取出 committed 行并折行。"""
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
        r.take_preview_lines()
    r.close()
    out: list[str] = []
    for ln in r.take_lines():
        out.extend(w.plain for w in wrap_line(ln, width))
    return out


def _leading_spaces(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


# ══════════════════════════════════════════════════════════
# wrap_line 的续行前缀语义
# ══════════════════════════════════════════════════════════

def test_wrap_line_cont_prefix_indents_continuation():
    """给定续行前缀时，续行（非首行）以该前缀起始且行宽不超上限。"""
    line = AnsiLine.of("1. " + "alpha beta gamma delta epsilon zeta")
    line.cont_prefix = "   "
    parts = wrap_line(line, 20)
    assert len(parts) > 1
    assert parts[0].plain.startswith("1. ")
    for part in parts[1:]:
        assert part.plain.startswith("   "), parts
    assert all(p.width <= 20 for p in parts), [p.width for p in parts]


def test_wrap_line_explicit_cont_prefix_argument():
    """``cont_prefix`` 参数优先于行自身的续行前缀。"""
    line = AnsiLine.of("x" * 30)
    line.cont_prefix = " " * 8
    parts = wrap_line(line, 12, cont_prefix="  ")
    assert all(p.plain.startswith("  ") for p in parts[1:]), parts


def test_wrap_line_fits_without_wrapping_keeps_prefix():
    """不超宽的行原样返回（续行前缀保留但本行不加缩进）。"""
    line = AnsiLine.of("short")
    line.cont_prefix = "   "
    parts = wrap_line(line, 20)
    assert [p.plain for p in parts] == ["short"]
    assert parts[0].cont_prefix == "   "


def test_wrap_line_empty_prefix_keeps_legacy_shape():
    """空前缀（普通段落）折行结果与既有语义一致（续行不加缩进）。"""
    line = AnsiLine.of("one two three four five six seven")
    parts = wrap_line(line, 12)
    assert len(parts) > 1
    assert all(not p.plain.startswith(" ") for p in parts), parts
    assert parts[0].cont_prefix == ""


def test_wrap_line_cont_prefix_clone_preserved():
    """``clone`` 保留续行前缀（宽度重排时仍能悬挂缩进）。"""
    line = AnsiLine.of("text")
    line.cont_prefix = "    "
    assert line.clone().cont_prefix == "    "


def test_wrap_line_narrow_screen_disables_prefix():
    """窄屏（剩余可用宽度不足 2 列）时禁用续行前缀，不破坏行宽不变量。"""
    line = AnsiLine.of("abcdef" * 4)
    line.cont_prefix = " " * 10
    parts = wrap_line(line, 11)
    assert len(parts) > 1
    assert all(p.width <= 11 for p in parts), [p.width for p in parts]
    # 前缀被禁用 → 续行不缩进（回退既有字符级硬拆语义）
    assert all(_leading_spaces(p.plain) == 0 for p in parts), parts


def test_wrap_line_cjk_cont_prefix_width_bounded():
    """CJK 内容带续行前缀时行宽仍不超过上限，且续行对齐。"""
    line = AnsiLine.of("1. " + "中文测试内容" * 5)
    line.cont_prefix = "   "
    parts = wrap_line(line, 16)
    assert len(parts) > 1
    assert all(p.width <= 16 for p in parts), [p.width for p in parts]
    assert all(p.plain.startswith("   ") for p in parts[1:]), parts


# ══════════════════════════════════════════════════════════
# 列表项：续行与内容起始列对齐（用户报告场景）
# ══════════════════════════════════════════════════════════

USER_SRC = (
    "1. `call_model_async = getattr(agent, '_call_model_async', None)`\n"
    "4. `await _event_facets.llm_stream(agent, call, _make_invoke(call))` → "
    "调 `_invoke_model()` → `call_model_async(...)` → 返回 "
    "`(\"\", \"部分回答\", {}, [])`\n"
    "7. `if await interrupt_port.is_interrupted(): "
    "agent._append_assistant_msg(content or _INTERRUPTED_MSG, reasoning)` → "
    "content=\"部分回答\" → append \"部分回答\""
    "21934791237491274913274912374912374912734912374912374\n"
)


def test_ordered_list_continuation_aligned_with_content():
    """有序列表长项折行：续行缩进 = 内容起始列（``N. `` 后）。"""
    rows = _wrapped(USER_SRC, 100)
    heads = [r for r in rows if r.startswith("4. ") or r.startswith("7. ")]
    assert len(heads) == 2, rows
    # 找出各列表项之后的续行（同项折出的行）
    start = rows.index(heads[0])
    cont = rows[start + 1]
    assert _leading_spaces(cont) == 3, rows
    assert "部分回答" in cont
    start = rows.index(heads[1])
    assert _leading_spaces(rows[start + 1]) == 3, rows
    assert _leading_spaces(rows[start + 2]) == 3, rows
    assert "21934791237491274913274912374912374912734912374912374" in rows[start + 2], rows


def test_ordered_list_streaming_matches_one_shot():
    """流式（逐 chunk）与一次性渲染的折行输出一致（含续行缩进）。"""
    for width in (60, 100):
        assert _streamed(USER_SRC, width) == _wrapped(USER_SRC, width), width


def test_unordered_list_continuation_aligned():
    """无序列表：续行缩进 = ``• `` 前缀宽（2 列）。"""
    src = "- " + "word " * 30 + "\n"
    rows = _wrapped(src, 40)
    assert len(rows) > 1
    assert all(_leading_spaces(r) == 2 for r in rows[1:]), rows


def test_todo_list_continuation_aligned_after_checkbox():
    """任务列表：续行缩进到复选框之后（``[x] `` 的内容起始列）。"""
    src = "- [x] " + "task " * 30 + "\n"
    rows = _wrapped(src, 40)
    assert len(rows) > 1
    assert rows[0].startswith("\u2022 [x] "), rows
    assert all(_leading_spaces(r) == 6 for r in rows[1:]), rows


def test_nested_list_continuation_aligned():
    """嵌套列表：续行缩进包含嵌套层前缀（``  - `` → 4 列）。"""
    src = "- outer\n  - " + "deep " * 30 + "\n"
    rows = _wrapped(src, 40)
    inner = [i for i, r in enumerate(rows) if r.lstrip().startswith("deep") or
             r.lstrip().startswith("•")]
    assert inner, rows
    item = inner[-1]
    assert rows[item].startswith("  "), rows
    assert all(_leading_spaces(r) == 4 for r in rows[item + 1:]), rows


# ══════════════════════════════════════════════════════════
# 引用 / 容器 / 其它缩进行
# ══════════════════════════════════════════════════════════

def test_blockquote_continuation_keeps_prefix():
    """引用块长行折行：续行仍带 ``│ `` 前缀（引用视觉连续）。"""
    src = "> " + "quote " * 30 + "\n"
    rows = _wrapped(src, 40)
    assert len(rows) > 1
    assert all(r.startswith("│ ") for r in rows), rows


def test_blockquote_nested_list_continuation_aligned():
    """引用内列表：续行 = 引用前缀 + 列表内容起始列。"""
    src = "> 1. " + "quoted item " * 20 + "\n"
    rows = _wrapped(src, 40)
    assert len(rows) > 1
    assert all(r.startswith("│ ") for r in rows), rows
    assert all(r.startswith("│    ") for r in rows[1:]), rows


def test_admonition_body_continuation_indented():
    """告示正文长行折行：续行与正文缩进（4 列）对齐。"""
    src = "> [!NOTE]\n> " + "body " * 30 + "\n"
    rows = _wrapped(src, 40)
    body_idx = next(i for i, r in enumerate(rows) if r.lstrip().startswith("body"))
    assert rows[body_idx].startswith("    "), rows
    assert all(_leading_spaces(r) == 4 for r in rows[body_idx:]), rows


def test_list_block_element_continuation_aligned():
    """列表项内块级元素（缩进段落）：续行与元素前缀对齐。"""
    src = "- item\n\n  " + "indented " * 30 + "\n"
    rows = _wrapped(src, 40)
    idx = next(i for i, r in enumerate(rows) if r.lstrip().startswith("indented"))
    assert rows[idx].startswith("  "), rows
    assert all(_leading_spaces(r) == 2 for r in rows[idx:]), rows


def test_direct_wrap_line_keeps_prefix_runs_styled():
    """续行前缀并入行首无样式 run（不产生无意义 run 碎片）。"""
    line = AnsiLine([Run("abc def ghi", None)])
    line.cont_prefix = "  "
    parts = wrap_line(line, 8)
    assert parts[1].plain.startswith("  "), parts
    assert len(parts[1].runs) == 1, parts[1].runs
