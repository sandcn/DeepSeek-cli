"""ANSI 流式 Markdown 语法/渲染修复（第十三批）测试。

本轮改动：强调/双字符 span 定界符的 CommonMark flanking 判定新增 **CJK 语义
软化**（``_InlineParser._delim_flanking``）——定界符紧贴**中文（非 ASCII）
标点**、另一侧是中文/全角（非 ASCII、非空白）字符时，该标点不再阻断
flanking。

修复用户报告的「流式渲染这段 markdown 出错」：

  - 修复前 ``10. **原版「传送门特效、水下雾」**已有。`` 渲染为**带字面 ``**``
    的原样文本**——闭合 ``**`` 前是 ``」``（标点）、后是汉字 ``已``，CommonMark
    的 right-flanking 规则判否（「前为标点且后非空白/标点」不可闭合），标记
    上屏；
  - 同类失效：``已经**「加粗」**完成``（开启 ``**`` 前为汉字、后为 ``「``）、
    ``==高亮」==说明`` / ``~~删除」~~已``（span 定界符同族判定）。

软化条件：**两侧同为非 ASCII（且非空白）**——非 ASCII 字符不可能是 Markdown
运算符/标识符，故纯 ASCII 技术文本（``foo*(bar)*baz``、``**(a)**baz``、
``mode=="change"``、``a == b == c``）的判定逐字不变（与 markdown-it 等
CommonMark 实现一致）；ASCII 标点 + ASCII 词字符、空白相邻等形态也不受影响。

同时固化：流式分块写入与一次性写入产出一致；定界符闭合后的任一帧（提交行 +
预览行）都不得再出现字面 ``**``。
"""

from __future__ import annotations

import re

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline

#: 用户报告的内容（模型输出的「剩余原版功能清单」）
REPORTED = (
    "剩余原版功能（我掌握的清单）：\n"
    "1. **原版「F3 的 chunk 更新计数 / 帧率图表」**（F3+图表？）。\n"
    "2. **原版「世界边界 F3 行」**（WorldBorder 信息）。有世界边界系统 → F3 可加行（边界中心/大小/距离）。\n"
    "3. **原版「F3 的 Looking at 方块/流体标签」**（已有 Targeted Block）。\n"
    "4. **原版「物品栏界面的合成配方书」已做**。\n"
    "5. **原版「成就/进度界面的搜索/筛选」**？\n"
    "6. **原版「统计界面的分类页」**？\n"
    "7. **原版「选项界面的更多分类（音乐/声音、语言、聊天设置、控制、资源包…）」** —— 检查项目设置界面完整度。\n"
    "8. **原版「F3 内存行已做」**。\n"
    "9. **原版「生物群系提示（F3 已有）」**。\n"
    "10. **原版「传送门特效、水下雾」**已有。\n"
)

#: 报告中被吞（未渲染为加粗）的目标行——闭合 ``**`` 前为 ``」``、后为汉字
REPORTED_TARGET = "10. **原版「传送门特效、水下雾」**已有。\n"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _render(src: str, width: int = 120):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return r.take_lines()


def _plain(src: str, width: int = 120) -> str:
    return "\n".join(ln.plain for ln in _render(src, width))


def _chunked_plain(src: str, size: int = 1, width: int = 120) -> str:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return "\n".join(ln.plain for ln in r.take_lines())


def _styled_texts(src: str, pred, width: int = 120) -> list:
    """渲染后满足样式条件 ``pred(style)`` 的 run 文本列表。"""
    out: list = []
    for ln in _render(src, width):
        for run in ln.runs:
            if run.style is not None and pred(run.style):
                out.append(run.text)
    return out


def _bold_texts(src: str) -> list:
    return _styled_texts(src, lambda st: st.bold)


def _inline_bold_texts(src: str) -> list:
    return [r.text for r in render_inline(src) if r.style is not None and r.style.bold]


# ══════════════════════════════════════════════════════════
# 1. 用户报告内容（本次修复核心）
# ══════════════════════════════════════════════════════════


class TestReportedList:
    def test_target_line_renders_bold(self):
        """第 10 项必须渲染为加粗（修复前整行带字面 ``**``）。"""
        assert _bold_texts(REPORTED_TARGET) == ["原版「传送门特效、水下雾」"]

    def test_no_literal_markers_in_target(self):
        assert "**" not in _plain(REPORTED_TARGET)

    def test_target_line_text(self):
        assert _plain(REPORTED_TARGET) == "10. 原版「传送门特效、水下雾」已有。"

    def test_reported_list_no_literal_markers(self):
        """整段清单渲染后不得残留任何字面 ``**``。"""
        got = _plain(REPORTED)
        assert "**" not in got, got

    def test_reported_list_bold_items(self):
        """10 个列表项全部渲染为加粗（其中 4/8/9/10 项为「已做」形态）。"""
        bold = _bold_texts(REPORTED)
        assert len(bold) == 10, bold
        assert "原版「传送门特效、水下雾」" in bold
        assert "原版「F3 内存行已做」" in bold

    def test_reported_list_line_structure(self):
        lines = _plain(REPORTED).split("\n")
        assert lines[0] == "剩余原版功能（我掌握的清单）："
        assert lines[1].startswith("1. 原版「F3 的 chunk 更新计数 / 帧率图表」")
        assert lines[10] == "10. 原版「传送门特效、水下雾」已有。"

    def test_chunked_equivalence(self):
        base = _plain(REPORTED)
        for size in (1, 2, 3, 5, 8, 17, 64):
            assert _chunked_plain(REPORTED, size=size) == base, size

    def test_streaming_frames_after_close_show_no_literal_markers(self):
        """定界符闭合之后的任一帧（提交行 + 预览行）不得出现字面 ``**``。"""
        close_at = REPORTED.rindex("**") + 2
        r = AnsiStreamRenderer(width=120)
        for i in range(0, len(REPORTED), 3):
            end = min(i + 3, len(REPORTED))
            r.write(REPORTED[i:end])
            text = "\n".join(
                [ln.plain for ln in r.lines]
                + [ln.plain for ln in r.take_preview_lines()]
            )
            if end >= close_at:
                assert "**" not in text, (end, text)
        r.close()

    def test_inline_path_renders_bold(self):
        assert _inline_bold_texts(REPORTED_TARGET) == ["原版「传送门特效、水下雾」"]

# ══════════════════════════════════════════════════════════
# 2. CJK 语境软化（正例：中文标点紧贴强调/span 定界符）
# ══════════════════════════════════════════════════════════

BOLD_CASES = {
    "闭合符前中文右引号后汉字": ("**原版「特效」**已做。", "原版「特效」"),
    "开闭符均贴中文引号": ("已经**「加粗」**完成", "「加粗」"),
    "闭合符前中文括号后汉字": ("**配置（含注释）**说明如下", "配置（含注释）"),
    "闭合符前顿号后汉字": ("**要点一、要点二**若干", "要点一、要点二"),
    "闭合符前句号后汉字": ("**结束。**接续文字", "结束。"),
    "内容全为中文标点": ("**、**已有。", "、"),
}


class TestCjkSoftPunctBold:
    def test_bold_rendered(self):
        for name, (src, want) in BOLD_CASES.items():
            assert _bold_texts(src) == [want], (name, _plain(src))

    def test_no_literal_markers(self):
        for name, (src, _want) in BOLD_CASES.items():
            assert "**" not in _plain(src), (name, _plain(src))

    def test_inline_path_agrees(self):
        for name, (src, want) in BOLD_CASES.items():
            assert _inline_bold_texts(src) == [want], name

    def test_chunked_equivalence(self):
        for name, (src, _want) in BOLD_CASES.items():
            base = _plain(src)
            for size in (1, 2, 3, 7):
                assert _chunked_plain(src, size=size) == base, (name, size)


class TestCjkSoftPunctSpan:
    def test_highlight_still_applied(self):
        from src.renderer.ansi.inline import _STYLE_HIGHLIGHT

        src = "==高亮」==说明"
        assert _plain(src) == "高亮」说明"
        assert _styled_texts(src, lambda st: st.bg == _STYLE_HIGHLIGHT.bg) == ["高亮」"]

    def test_strikethrough_still_applied(self):
        from src.renderer.ansi.inline import _STYLE_STRIKE

        src = "~~删除」~~说明"
        assert _plain(src) == "删除」说明"
        assert _styled_texts(src, lambda st: st.dim == _STYLE_STRIKE.dim) == ["删除」"]

    def test_underline_still_applied(self):
        from src.renderer.ansi.inline import _STYLE_UNDERLINE

        src = "++下划线」++说明"
        assert _plain(src) == "下划线」说明"
        assert _styled_texts(
            src, lambda st: st.underline == _STYLE_UNDERLINE.underline) == ["下划线」"]

    def test_spoiler_still_masked(self):
        src = "||剧透」||说明"
        got = _plain(src)
        assert "剧透" not in got and "||" not in got, got
        assert got.endswith("说明")


# ══════════════════════════════════════════════════════════
# 3. 反例保护（纯 ASCII 技术文本判定逐字不变）
# ══════════════════════════════════════════════════════════

ASCII_LITERAL = (
    "foo*(bar)*baz",
    "**(a)**b",
    "a * foo bar*",
    "** foo bar**",
    "x==y",
    'mode=="change"',
    "a == b == c",
    "a ++ b ++ c",
    "a || b || c",
    "i++; j++;",
    "a~~b, c~~d",
    "foo**(a)**baz",
)


class TestAsciiUnchanged:
    def test_ascii_forms_keep_literal_markers(self):
        for src in ASCII_LITERAL:
            assert _plain(src) == src, (src, _plain(src))

    def test_ascii_bold_style_absent(self):
        for src in ASCII_LITERAL:
            assert _bold_texts(src) == [], (src, _bold_texts(src))

    def test_ascii_punct_before_cjk_not_softened(self):
        """软化要求「两侧同为非 ASCII」——ASCII 标点紧邻 CJK 时不生效。"""
        assert _bold_texts('foo**"中文"**bar') == []
        assert _plain('foo**"中文"**bar') == 'foo**"中文"**bar'

    def test_cjk_open_after_ascii_not_softened(self):
        """ASCII 词字符 + 非 ASCII 标点开头：开定界符仍按 CommonMark 判否。"""
        assert _bold_texts("x**「中文」**y") == []

    def test_space_edges_still_rejected(self):
        """空白相邻仍不构成定界符（软化不绕过空白规则）。"""
        assert _bold_texts("** 中文」** 已。") == []
        assert _plain("** 中文」** 已。") == "** 中文」** 已。"

    def test_normal_bold_unaffected(self):
        assert _bold_texts("**bold**text") == ["bold"]
        assert _bold_texts("a**bold**b") == ["bold"]
        assert _bold_texts("**bold**已") == ["bold"]
        assert _bold_texts("已**bold**") == ["bold"]
