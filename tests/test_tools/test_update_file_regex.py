r"""update_file 正则替换功能测试。

需求（2026-08-06）：update_file 支持 use_regex=true 正则替换，
old_string 按正则表达式编译匹配，new_string 支持反向引用 \1、\g<name>。
"""

from __future__ import annotations

import re

import pytest

from src.tools.update_file import (
    UpdateFileFunc,
    StringNotFoundError,
    AmbiguousMatchError,
    RegexCompileError,
    _parse_regex_flags,
)


async def _run(path, old_string, new_string, **kwargs):
    """执行 update_file 并返回 (result, 文件最新内容)。"""
    func = UpdateFileFunc(str(path), old_string, new_string, **kwargs)
    result = await func.execute()
    return result, path.read_text(encoding="utf-8")


# ── 正则标志解析单元测试 ─────────────────────────────────

class TestParseRegexFlags:
    def test_empty_returns_zero(self):
        assert _parse_regex_flags("") == 0

    def test_single_flag(self):
        assert _parse_regex_flags("i") == re.IGNORECASE
        assert _parse_regex_flags("m") == re.MULTILINE
        assert _parse_regex_flags("s") == re.DOTALL
        assert _parse_regex_flags("a") == re.ASCII
        assert _parse_regex_flags("x") == re.VERBOSE

    def test_combined_flags(self):
        assert _parse_regex_flags("im") == (re.IGNORECASE | re.MULTILINE)

    def test_case_insensitive_letters(self):
        assert _parse_regex_flags("IM") == (re.IGNORECASE | re.MULTILINE)

    def test_invalid_flag_raises(self):
        with pytest.raises(RegexCompileError):
            _parse_regex_flags("z")


# ── 基础正则替换 ─────────────────────────────────────────

class TestRegexReplace:
    @pytest.mark.asyncio
    async def test_regex_replace_all(self, tmp_path):
        """正则全局替换：匹配所有数字。"""
        path = tmp_path / "f.txt"
        path.write_text("price: 100\nprice: 200\n", encoding="utf-8")
        result, content = await _run(
            path, r"price: \d+", "price: 0", use_regex=True, replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "price: 0\nprice: 0\n"

    @pytest.mark.asyncio
    async def test_regex_single_match(self, tmp_path):
        """正则单处匹配：replace_all=false 且仅一处匹配时正常替换。"""
        path = tmp_path / "f.txt"
        path.write_text("a 1\nb c\n", encoding="utf-8")
        result, content = await _run(path, r"\d+", "X", use_regex=True)
        assert result.startswith("更新成功")
        assert content == "a X\nb c\n"

    @pytest.mark.asyncio
    async def test_regex_delete(self, tmp_path):
        """正则替换为空字符串：删除所有数字。"""
        path = tmp_path / "f.txt"
        path.write_text("a=1\nb=2\n", encoding="utf-8")
        result, content = await _run(
            path, r"\d+", "", use_regex=True, replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "a=\nb=\n"

    @pytest.mark.asyncio
    async def test_regex_backreference(self, tmp_path):
        """反向引用 \\1 \\2：交换捕获组位置。"""
        path = tmp_path / "f.txt"
        path.write_text("foo123\nbar456\n", encoding="utf-8")
        result, content = await _run(
            path, r"([a-z]+)(\d+)", r"\2-\1", use_regex=True, replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "123-foo\n456-bar\n"

    @pytest.mark.asyncio
    async def test_regex_named_group_backreference(self, tmp_path):
        """命名分组反向引用 \\g<name>。"""
        path = tmp_path / "f.txt"
        path.write_text("key=value\n", encoding="utf-8")
        result, content = await _run(
            path, r"(?P<k>\w+)=(?P<v>\w+)", r"\g<v>:\g<k>",
            use_regex=True, replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "value:key\n"

    @pytest.mark.asyncio
    async def test_regex_line_anchor(self, tmp_path):
        """行首锚点：^ 匹配每行行首（配合多行标志）。"""
        path = tmp_path / "f.txt"
        path.write_text("  x\n  y\n", encoding="utf-8")
        result, content = await _run(
            path, r"^  ", "    ", use_regex=True, replace_all=True, regex_flags="m",
        )
        assert result.startswith("更新成功")
        assert content == "    x\n    y\n"


# ── 正则标志 ─────────────────────────────────────────────

class TestRegexFlags:
    @pytest.mark.asyncio
    async def test_flags_ignorecase(self, tmp_path):
        """regex_flags='i' 忽略大小写。"""
        path = tmp_path / "f.txt"
        path.write_text("Hello\nworld\n", encoding="utf-8")
        result, content = await _run(
            path, "WORLD", "X", use_regex=True, regex_flags="i",
        )
        assert result.startswith("更新成功")
        assert content == "Hello\nX\n"

    @pytest.mark.asyncio
    async def test_flags_multiline(self, tmp_path):
        """regex_flags='m' 多行模式：^ $ 匹配每行行首尾。"""
        path = tmp_path / "f.txt"
        path.write_text("a\nb\na\n", encoding="utf-8")
        result, content = await _run(
            path, r"^a$", "X", use_regex=True, regex_flags="m", replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "X\nb\nX\n"

    @pytest.mark.asyncio
    async def test_flags_combined(self, tmp_path):
        """regex_flags='im' 组合：忽略大小写 + 多行。"""
        path = tmp_path / "f.txt"
        path.write_text("A\nb\nA\n", encoding="utf-8")
        result, content = await _run(
            path, r"^a$", "X", use_regex=True, regex_flags="im", replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "X\nb\nX\n"

    @pytest.mark.asyncio
    async def test_flags_verbose(self, tmp_path):
        """regex_flags='x' 详细模式：忽略模式内空白。"""
        path = tmp_path / "f.txt"
        path.write_text("a=1\n", encoding="utf-8")
        result, content = await _run(
            path, "a = \\d+", "b=2", use_regex=True, regex_flags="x", replace_all=True,
        )
        assert result.startswith("更新成功")
        assert content == "b=2\n"


# ── 错误路径（execute 返回 "(更新失败: ...)" 字符串）──────

class TestRegexErrors:
    @pytest.mark.asyncio
    async def test_regex_no_match(self, tmp_path):
        """正则未匹配 → 返回错误，文件保持不变。"""
        path = tmp_path / "f.txt"
        path.write_text("abc\n", encoding="utf-8")
        result, _ = await _run(path, r"\d+", "X", use_regex=True)
        assert result.startswith("(更新失败")
        assert "正则未匹配" in result
        assert path.read_text(encoding="utf-8") == "abc\n"

    @pytest.mark.asyncio
    async def test_regex_compile_error(self, tmp_path):
        """正则语法错误 → 返回编译错误，文件保持不变。"""
        path = tmp_path / "f.txt"
        path.write_text("abc\n", encoding="utf-8")
        result, _ = await _run(path, "(", "X", use_regex=True)
        assert result.startswith("(更新失败")
        assert "正则表达式编译失败" in result
        assert path.read_text(encoding="utf-8") == "abc\n"

    @pytest.mark.asyncio
    async def test_regex_invalid_flag(self, tmp_path):
        """非法 regex_flags → 返回错误。"""
        path = tmp_path / "f.txt"
        path.write_text("abc\n", encoding="utf-8")
        result, _ = await _run(path, "a", "X", use_regex=True, regex_flags="z")
        assert result.startswith("(更新失败")
        assert "不支持的 regex_flags" in result

    @pytest.mark.asyncio
    async def test_regex_ambiguous(self, tmp_path):
        """多匹配且 replace_all=false → 返回歧义错误，文件保持不变。"""
        path = tmp_path / "f.txt"
        path.write_text("a 1\nb 2\n", encoding="utf-8")
        result, _ = await _run(path, r"\d+", "X", use_regex=True)
        assert result.startswith("(更新失败")
        assert "匹配了2处" in result
        assert path.read_text(encoding="utf-8") == "a 1\nb 2\n"

    def test_regex_exceptions_are_file_tool_errors(self):
        """正则相关异常都是 FileToolError 子类（被 execute 统一捕获）。"""
        assert issubclass(RegexCompileError, StringNotFoundError.__mro__[1])
        assert issubclass(StringNotFoundError, StringNotFoundError.__mro__[1])
        assert issubclass(AmbiguousMatchError, StringNotFoundError.__mro__[1])


# ── 兼容性：原有行为不变 ─────────────────────────────────

class TestBackwardCompat:
    @pytest.mark.asyncio
    async def test_string_mode_unchanged(self, tmp_path):
        """默认 use_regex=false：字符串精确替换行为不变。"""
        path = tmp_path / "f.txt"
        path.write_text("a.b\n", encoding="utf-8")
        result, content = await _run(path, "a.b", "X")
        assert result.startswith("更新成功")
        assert content == "X\n"

    @pytest.mark.asyncio
    async def test_string_mode_literal_dot(self, tmp_path):
        """默认模式下 '.' 是字面量：不匹配 "a1" 中的任意字符。"""
        path = tmp_path / "f.txt"
        path.write_text("a1\n", encoding="utf-8")
        result, _ = await _run(path, "a.b", "X")
        assert result.startswith("(更新失败")
        assert "未找到匹配内容" in result

    @pytest.mark.asyncio
    async def test_append_mode_ignores_regex(self, tmp_path):
        """追加模式（old_string=''）不受 use_regex 影响，始终追加。"""
        path = tmp_path / "f.txt"
        path.write_text("abc\n", encoding="utf-8")
        result, content = await _run(path, "", "end\n", use_regex=True)
        assert result.startswith("更新成功")
        assert content == "abc\nend\n"


# ── 展示与参数构造 ───────────────────────────────────────

class TestDisplayAndArgs:
    def test_display_params_marks_regex(self):
        """display_params 显示 [正则] 标记。"""
        text = UpdateFileFunc.display_params(
            {"path": "f.txt", "use_regex": True, "replace_all": True},
        )
        assert "[正则]" in text
        assert "[全局替换]" in text

    def test_display_params_no_regex_mark(self):
        """未开启 use_regex 时不显示 [正则]。"""
        text = UpdateFileFunc.display_params({"path": "f.txt", "replace_all": True})
        assert "[正则]" not in text
        assert "[全局替换]" in text

    def test_from_args_with_regex_params(self):
        """from_args 能构造带正则参数的工具实例。"""
        func = UpdateFileFunc.from_args({
            "path": "f.txt",
            "old_string": r"\d+",
            "new_string": "X",
            "use_regex": True,
            "regex_flags": "im",
        })
        assert isinstance(func, UpdateFileFunc)
        assert func.use_regex is True
        assert func.regex_flags == "im"

    def test_from_args_defaults(self):
        """from_args 缺省参数回退默认值（向后兼容）。"""
        func = UpdateFileFunc.from_args({
            "path": "f.txt",
            "old_string": "old",
            "new_string": "new",
        })
        assert func.use_regex is False
        assert func.regex_flags == ""
        assert func.replace_all is False

    def test_schema_contains_regex_params(self):
        """tool schema 包含 use_regex / regex_flags 参数。"""
        schema = UpdateFileFunc.to_tool_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "use_regex" in props
        assert "regex_flags" in props
        # 必需参数不变（向后兼容）
        assert schema["function"]["parameters"]["required"] == ["path", "old_string", "new_string"]
