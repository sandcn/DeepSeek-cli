"""ANSI 流式 Markdown 渲染修复（第二十批）测试。

本轮修复用户报告的「流式渲染这段 markdown 出错」：

  用户给出的内容包含多段「粗体 + 行内代码」行，其中 ``_resolve_grid`` /
  ``__init__`` / ``from_args`` 等标识符位于行内代码中。流式预览的**增量
  追加快路径**在上一帧以行内定界符结尾时直接拼接新增纯文本：

      ``...;`_resolve_``  →  ``...;`_resolve_g``

  上一帧把 ``_resolve_`` 解析为斜体 ``resolve``（未闭合代码段内的反引号是
  字面文本，``_resolve_`` 本身构成强调），直接追加 ``g`` 得到
  ``resolveg``；而整行重解析应得到字面 ``_resolve_g``（``_resolve_g`` 中的
  下划线是词内下划线，不构成强调）。预览因此与一次性渲染不一致——用户看到
  标识符下划线「消失」。

修复：增量追加 / 整段节流复用前检查「上一帧是否以行内定界符结尾」
（``_tail_ends_with_delimiter``）。CommonMark 定界符能否闭合取决于**其后
字符**（flanking / 词内下划线规则），追加文本会改变该判定，必须整行重解析。
"""
from __future__ import annotations

import pytest

from src.renderer.ansi import (
    AnsiStreamRenderer,
    _delta_can_append_plain,
    _tail_ends_with_delimiter,
    _whole_delta_keeps_rendered_rows,
)

#: 用户报告的内容（原样）
REPORTED = (
    "**问题 3 的 schema `seconds`**:schema properties 有 seconds;`__init__` 无;"
    "`from_args` 忽略多余参数。确认。\n"
    "这确实是\"schema 列出但实现不接受\"。\n"
    "\n"
    "**问题 3 的 `shot` type**:schema `\"shot\": {\"type\": \"string\"}`,"
    "描述说 \"或 true\"。确认。\n"
    "\n"
    "**问题 3 的 `grid`**:schema type number,描述 \"0 或 true\";"
    "`_resolve_grid` 对字符串 \"true\" 报错。确认。\n"
    "\n"
    "**问题 4 wait_window**:确认 `describe_windows(infos)` 默认 40;"
    "`match` 可能 None。代码依据。\n"
)

_EXPECTED = [
    "问题 3 的 schema seconds:schema properties 有 seconds;__init__ 无;"
    "from_args 忽略多余参数。确认。",
    "这确实是\"schema 列出但实现不接受\"。",
    "问题 3 的 shot type:schema \"shot\": {\"type\": \"string\"},"
    "描述说 \"或 true\"。确认。",
    "问题 3 的 grid:schema type number,描述 \"0 或 true\";"
    "_resolve_grid 对字符串 \"true\" 报错。确认。",
    "问题 4 wait_window:确认 describe_windows(infos) 默认 40;"
    "match 可能 None。代码依据。",
]


def _once(src: str, width: int = 100) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.lines]


def _stream(src: str, chunk: int, width: int = 100) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
    r.close()
    return [ln.plain for ln in r.lines]


def _frame(renderer: AnsiStreamRenderer) -> list[str]:
    """当前帧可见内容（已提交行 + 未闭合预览行）。"""
    return [ln.plain for ln in renderer.lines] + [
        ln.plain for ln in renderer.take_preview_lines()
    ]


# ══════════════════════════════════════════════════════════
# 用户报告内容：最终渲染
# ══════════════════════════════════════════════════════════

def test_reported_sample_final_rendering():
    assert _once(REPORTED) == _EXPECTED


def test_reported_sample_has_no_literal_markers():
    out = "\n".join(_once(REPORTED))
    assert "**" not in out
    assert "`" not in out


def test_reported_sample_bold_spans():
    """粗体范围：``**问题 3 的 schema `seconds`**`` 整体加粗（含行内码）。"""
    r = AnsiStreamRenderer(width=100)
    r.write(REPORTED)
    r.close()
    line = r.lines[0]
    bold = "".join(
        run.text for run in line.runs
        if run.style is not None and run.style.bold
    )
    # 行内代码在粗体内继承 bold（``seconds`` / ``__init__`` / ``from_args``）
    assert bold == "问题 3 的 schema seconds__init__from_args"


# ══════════════════════════════════════════════════════════
# 用户报告内容：流式每一帧与一次性渲染一致
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 5, 7])
def test_reported_sample_stream_frames_match_full_render(chunk):
    """任意前缀的「已提交 + 预览」必须等于对该前缀的一次性渲染。

    修复前 ``_resolve_grid`` 在 ``_resolve_`` 后追加 ``grid`` 时预览显示
    ``resolvegrid``（下划线被吞），与一次性渲染不一致。
    """
    r = AnsiStreamRenderer(width=100)
    for i in range(0, len(REPORTED), chunk):
        r.write(REPORTED[i:i + chunk])
        prefix = REPORTED[:i + chunk]
        expected = _frame(_fresh(prefix, width=100))
        assert _frame(r) == expected, (chunk, i, prefix[-20:])


def _fresh(src: str, width: int = 100) -> AnsiStreamRenderer:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    return r


# ══════════════════════════════════════════════════════════
# 精确复现：行内代码中的前导/词内下划线
# ══════════════════════════════════════════════════════════

def test_resolve_grid_preview_keeps_underscore():
    r = AnsiStreamRenderer(width=100)
    r.write("**问题 3 的 `grid`**:schema type number，描述 \"0 或 true\";`_resolve")
    assert r.take_preview_lines()[-1].plain.endswith("`_resolve")
    # 追加闭合定界符：``_resolve_`` 整体成为斜体（未闭合代码段内是字面文本）
    r.write("_")
    assert r.take_preview_lines()[-1].plain.endswith("`resolve")
    # 纯文本增量（BPE 常见切分）不得直接拼接到斜体结果上——否则显示
    # ``resolvegrid``；整行重解析应为字面 ``_resolve_grid``。
    r.write("grid")
    last = r.take_preview_lines()[-1].plain
    assert last.endswith("`_resolve_grid")
    assert "resolvegrid" not in last


def test_leading_underscore_preview_keeps_underscore():
    r = AnsiStreamRenderer(width=100)
    r.write("**问题 6 release 接管**:确认 `_MANAGED_")
    r.write("OPS")
    last = r.take_preview_lines()[-1].plain
    assert last.endswith("`_MANAGED_OPS")


def test_intraword_underscore_preview_matches_once():
    """``_collect_done_background_messages`` 逐 token 到达时逐帧一致。"""
    src = "任务完成后 `_collect_done_background_messages` 不生成消息。"
    r = AnsiStreamRenderer(width=100)
    for i in range(len(src)):
        r.write(src[i])
        assert _frame(r) == _frame(_fresh(src[:i + 1])), i


# ══════════════════════════════════════════════════════════
# 增量判定原语
# ══════════════════════════════════════════════════════════

class TestTailDelimiterGuard:
    @pytest.mark.parametrize("tail", ["a_", "a*", "a`", "a~", "a^", "a$",
                                      "a=", "a+", "a|", "a%", "a\\"])
    def test_tail_delimiter_detected(self, tail):
        assert _tail_ends_with_delimiter(tail)

    @pytest.mark.parametrize("tail", ["", "abc", "a b", "（", "。", "a)"])
    def test_plain_tail_not_detected(self, tail):
        assert not _tail_ends_with_delimiter(tail)

    def test_append_rejected_after_delimiter(self):
        assert _delta_can_append_plain("a_b_", "g") is False
        assert _delta_can_append_plain("**a**", "b") is False
        assert _delta_can_append_plain("`x`", "y") is False

    def test_append_allowed_for_plain_tail(self):
        assert _delta_can_append_plain("hello", " world") is True
        assert _delta_can_append_plain("**a** b", "c") is True
        assert _delta_can_append_plain("`x` done", "?") is True

    def test_whole_paragraph_throttle_rejects_delimiter_tail(self):
        # 整段节流复用同样必须拒绝：``_x_`` + 字母会改变闭合判定
        assert _whole_delta_keeps_rendered_rows("a_b_", "g") is False
        assert _whole_delta_keeps_rendered_rows("text", " more") is True
