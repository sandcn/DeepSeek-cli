"""Code 组件单元测试。

覆盖 Code 组件的带行号/无行号渲染、语言标签、
空代码、多行代码行号对齐、超长行处理、update() props 变更。

测试策略：构造 Code 实例，调用 render() 获取纯文本输出，
验证边框结构、行号前缀、语言标签和内容正确性。
"""

from __future__ import annotations

import re
import pytest

from src.chat_ui.components.code import Code, _visual_width
from src.chat_ui.components.base import TuiComponent
from src.chat_ui.vdom.vnode import VNode


# ═══════════════════════════════════════════════════════════
# 测试辅助
# ═══════════════════════════════════════════════════════════

def _lines(output: str) -> list[str]:
    """按 \\n 分割输出，去除空尾行。"""
    return output.rstrip('\n').split('\n')


# ═══════════════════════════════════════════════════════════
# TestCodeRendering
# ═══════════════════════════════════════════════════════════

class TestCodeRendering:
    """Code 渲染测试。"""

    # ── 1. 带行号代码渲染 ──────────────────────────────

    def test_numbered_lines_prefix(self):
        """带行号渲染时，每行前缀格式为 '{N:>{w}} │ '。"""
        code = Code(code="print('hello')", numbered_lines=True)
        output = code.render()
        lines_list = _lines(output)

        # 应有顶边、内容行、底边
        assert len(lines_list) == 3  # ┌──┐ + │...│ + └──┘
        content_line = lines_list[1]
        # 单行时 num_width=1，行号前缀 "1 │ "
        assert "1 │ " in content_line
        assert "print('hello')" in content_line

    def test_numbered_lines_multiple(self):
        """多行带行号，验证每行行号递增。"""
        code = Code(code="line1\nline2\nline3", numbered_lines=True)
        output = code.render()
        lines_list = _lines(output)

        # 顶边 + 3 内容行 + 底边 = 5 行
        assert len(lines_list) == 5
        # num_width=1（3 行，最大行号 "3"，1 位）
        assert "1 │ " in lines_list[1]
        assert "line1" in lines_list[1]
        assert "2 │ " in lines_list[2]
        assert "line2" in lines_list[2]
        assert "3 │ " in lines_list[3]
        assert "line3" in lines_list[3]

    # ── 2. 无行号渲染 ──────────────────────────────────

    def test_no_numbered_lines(self):
        """numbered_lines=False 时仅竖线前缀 '  │ '，无行号。"""
        code = Code(code="print('hello')", numbered_lines=False)
        output = code.render()
        lines_list = _lines(output)

        content_line = lines_list[1]
        # 前缀应为 "  │ " 而非含数字的
        assert "  │ " in content_line
        # 不应含行号数字
        assert not re.search(r'\d │', content_line)
        assert "print('hello')" in content_line

    def test_no_numbered_lines_multiple(self):
        """多行无行号，每行均是 '  │ ' 前缀。"""
        code = Code(code="a\nb\nc", numbered_lines=False)
        output = code.render()
        lines_list = _lines(output)

        assert len(lines_list) == 5  # top + 3 + bottom
        for i in range(1, 4):
            assert "  │ " in lines_list[i]
            assert not re.search(r'\d │', lines_list[i])

    # ── 3. 含语言标签渲染 ──────────────────────────────

    def test_language_label_in_top_border(self):
        """language 非 None 时顶边含 '┌─ {lang} ' 标签。"""
        code = Code(code="print('hello')", language="python")
        output = code.render()
        top_line = _lines(output)[0]

        assert top_line.startswith("┌─ python")
        assert top_line.endswith("┐")

    def test_language_label_different_langs(self):
        """不同语言标签正确嵌入顶边。"""
        for lang in ("bash", "javascript", "rust"):
            code = Code(code="x", language=lang)
            top_line = _lines(code.render())[0]
            assert f"┌─ {lang}" in top_line, (
                f"语言 '{lang}' 未出现在顶边: {top_line}"
            )

    def test_no_language_label(self):
        """language=None 时顶边仅 '┌──...──┐'，无语言名称。"""
        code = Code(code="x", language=None)
        top_line = _lines(code.render())[0]
        # 应只有边框字符，无语言名
        assert top_line.startswith("┌─")
        assert "┐" in top_line
        # 不含任何额外文本（仅横线和角）
        inner = top_line[2:-1]  # 去掉 ┌ 和 ┐
        assert all(c == '─' for c in inner), (
            f"无语言标签时顶边内部应全为 ─，实际: {inner!r}"
        )

    # ── 4. 空代码行为 ──────────────────────────────────

    def test_empty_code(self):
        """code='' 仍渲染完整边框结构。"""
        code = Code(code="", numbered_lines=True)
        output = code.render()
        lines_list = _lines(output)

        # 顶边 + 空内容行 + 底边
        assert len(lines_list) == 3
        # 空代码内容行为空（仅前缀和竖线）
        content_line = lines_list[1]
        assert "│" in content_line
        assert "│" == content_line[0] or "│" in content_line

    def test_empty_code_no_numbers(self):
        """空代码 + 无行号，正确渲染。"""
        code = Code(code="", numbered_lines=False)
        output = code.render()
        lines_list = _lines(output)
        assert len(lines_list) == 3
        assert "│" in lines_list[1]

    def test_empty_code_with_language(self):
        """空代码 + 语言标签。"""
        code = Code(code="", language="python")
        output = code.render()
        lines_list = _lines(output)
        assert "python" in lines_list[0]

    # ── 5. 多行代码行号对齐 ────────────────────────────

    def test_line_number_alignment(self):
        """≥10 行时行号右对齐。"""
        lines_code = "\n".join(f"line{i}" for i in range(1, 13))  # 12 行
        code = Code(code=lines_code, numbered_lines=True)
        output = code.render()
        lines_list = _lines(output)

        # 行 1 → "  1 │ "，行 10 → " 10 │ "，行 12 → " 12 │ "
        # 单数字行号前有 1 空格 padding（num_width=2）
        line1 = lines_list[1]
        line10 = lines_list[10]
        line12 = lines_list[12]

        assert " 1 │ " in line1
        assert "10 │ " in line10
        assert "12 │ " in line12

    def test_line_number_alignment_hundreds(self):
        """≥100 行时三位数行号对齐。"""
        lines_code = "\n".join(f"L{i}" for i in range(1, 101))
        code = Code(code=lines_code, numbered_lines=True)
        output = code.render()
        lines_list = _lines(output)

        # 100 行 → num_width=3 → "  1 │ ", "100 │ "
        assert "  1 │ " in lines_list[1]
        assert "100 │ " in lines_list[100]

    # ── 6. 超长行处理 ──────────────────────────────────

    def test_long_line_no_crash(self):
        """超长行不引发异常，边框正常闭合。"""
        long_line = "x" * 500
        code = Code(code=long_line, numbered_lines=True)
        output = code.render()
        lines_list = _lines(output)

        assert len(lines_list) == 3
        # 边框正常
        assert lines_list[0].startswith("┌")
        assert lines_list[0].endswith("┐")
        assert lines_list[2].startswith("└")
        assert lines_list[2].endswith("┘")
        # 内容含超长行
        assert "x" * 500 in lines_list[1]

    def test_long_line_with_language(self):
        """超长行 + 语言标签，顶边宽度足够容纳。"""
        long_line = "y" * 300
        code = Code(code=long_line, language="python")
        output = code.render()
        top_line = _lines(output)[0]

        assert "python" in top_line
        assert len(top_line) >= len("┌─ python ─┐")

    def test_longest_line_determines_width(self):
        """不同长度行时边框宽度匹配最长行。"""
        code = Code(
            code="short\nthis is a much longer line\nok",
            numbered_lines=True,
        )
        output = code.render()
        lines_list = _lines(output)

        # 所有边框行长度应一致（等宽）
        widths = [len(l.rstrip()) for l in lines_list]
        # 去除 ANSI 序列影响 — Code.render() 返回纯 str，无 ANSI
        assert len(set(widths)) == 1, (
            f"所有行应等宽，实际宽度: {widths}"
        )

    # ── 边框结构完整性 ──────────────────────────────────

    def test_border_structure(self):
        """验证边框结构：主线 '│' 左右竖线。"""
        code = Code(code="hello\nworld")
        output = code.render()
        lines_list = _lines(output)

        # 顶边 ┌──┐
        assert lines_list[0].startswith("┌") and lines_list[0].endswith("┐")
        # 内容行 │...│
        for line in lines_list[1:-1]:
            assert line.startswith("│") and line.endswith("│")
        # 底边 └──┘
        assert lines_list[-1].startswith("└") and lines_list[-1].endswith("┘")

    def test_single_line_border(self):
        """单行代码边框正确。"""
        code = Code(code="one line")
        output = code.render()
        lines_list = _lines(output)
        assert len(lines_list) == 3  # top + content + bottom

    # ── CJK 字符处理 ───────────────────────────────────

    def test_cjk_characters(self):
        """CJK 字符正确渲染（验证 _visual_width 在渲染中被调用）。"""
        code = Code(code="你好世界")
        output = code.render()
        lines_list = _lines(output)

        assert "你好世界" in lines_list[1]
        # 边框正常闭合
        assert lines_list[0].endswith("┐")
        assert lines_list[-1].endswith("┘")


# ═══════════════════════════════════════════════════════════
# TestCodeUpdate
# ═══════════════════════════════════════════════════════════

class TestCodeUpdate:
    """Code update() props 变更测试。"""

    def test_update_code_changed(self):
        """code 变更返回 True 且内部状态更新。"""
        code = Code(code="old")
        changed = code.update({"code": "new"})
        assert changed is True
        assert "new" in code.render()

    def test_update_code_unchanged(self):
        """code 未变更返回 False。"""
        code = Code(code="same")
        changed = code.update({"code": "same"})
        assert changed is False

    def test_update_language_changed(self):
        """language 变更返回 True。"""
        code = Code(code="x", language=None)
        changed = code.update({"language": "python"})
        assert changed is True
        assert "python" in code.render()

    def test_update_language_unchanged(self):
        """language 未变更返回 False。"""
        code = Code(code="x", language="python")
        changed = code.update({"language": "python"})
        assert changed is False

    def test_update_numbered_lines_changed(self):
        """numbered_lines 变更返回 True。"""
        code = Code(code="x", numbered_lines=True)
        changed = code.update({"numbered_lines": False})
        assert changed is True
        # 无行号前缀
        output = code.render()
        assert "  │ " in output
        assert not re.search(r'\d │', output)

    def test_update_numbered_lines_unchanged(self):
        """numbered_lines 未变更返回 False。"""
        code = Code(code="x", numbered_lines=True)
        changed = code.update({"numbered_lines": True})
        assert changed is False

    def test_update_multiple_props(self):
        """同时更新多个 props。"""
        code = Code(code="a", language=None, numbered_lines=True)
        changed = code.update({
            "code": "b",
            "language": "bash",
            "numbered_lines": False,
        })
        assert changed is True
        output = code.render()
        assert "b" in output
        assert "bash" in output
        assert "  │ " in output

    def test_update_no_relevant_keys(self):
        """update dict 无相关 key 时返回 False。"""
        code = Code(code="x")
        changed = code.update({"other": "value"})
        assert changed is False


# ═══════════════════════════════════════════════════════════
# TestCodeVNode
# ═══════════════════════════════════════════════════════════

class TestCodeVNode:
    """Code render_vnode 测试。"""

    def test_render_vnode_returns_vnode(self):
        """render_vnode() 返回 VNode 实例。"""
        code = Code(code="test")
        vnode = code.render_vnode()

        assert isinstance(vnode, VNode)
        assert vnode.type == "code"
        assert vnode.key == "code"

    def test_render_vnode_props(self):
        """VNode props 包含 code/language/numbered_lines/text。"""
        code = Code(code="hello", language="python", numbered_lines=True)
        vnode = code.render_vnode()

        assert vnode.props["code"] == "hello"
        assert vnode.props["language"] == "python"
        assert vnode.props["numbered_lines"] is True
        assert "text" in vnode.props
        assert "hello" in vnode.props["text"]

    def test_render_vnode_empty_code(self):
        """空代码的 VNode text 为空字符串。"""
        code = Code(code="")
        vnode = code.render_vnode()
        assert vnode.props["code"] == ""
        assert "text" in vnode.props


# ═══════════════════════════════════════════════════════════
# TestCodeInheritance
# ═══════════════════════════════════════════════════════════

class TestCodeInheritance:
    """Code 组件继承和接口测试。"""

    def test_code_is_tuicomponent(self):
        """Code 是 TuiComponent 子类。"""
        assert issubclass(Code, TuiComponent)

    def test_code_key(self):
        """key 属性返回 'code'。"""
        code = Code(code="x")
        assert code.key == "code"

    def test_code_render_returns_str(self):
        """render() 返回 str。"""
        code = Code(code="x")
        assert isinstance(code.render(), str)

    def test_code_default_values(self):
        """默认构造参数正确。"""
        code = Code()
        assert code._code == ""
        assert code._language is None
        assert code._numbered_lines is True


# ═══════════════════════════════════════════════════════════
# TestVisualWidth
# ═══════════════════════════════════════════════════════════

class TestVisualWidth:
    """_visual_width 辅助函数测试。"""

    def test_ascii_width(self):
        """ASCII 字符宽度为 1。"""
        assert _visual_width("hello") == 5
        assert _visual_width("") == 0

    def test_cjk_width(self):
        """CJK 字符宽度为 2。"""
        assert _visual_width("你好") == 4
        assert _visual_width("测试") == 4

    def test_mixed_width(self):
        """混合 ASCII 和 CJK 字符宽度正确。"""
        assert _visual_width("hello你好") == 9  # 5 + 4
        assert _visual_width("a测试b") == 6  # 1 + 4 + 1
