"""测试 ReadFileFunc

测试策略
--------
- 使用 tmp_path 隔离文件系统操作
- 对编码检测（async_detect_encoding）做 mock，确保行为确定性
- _try_decode 是实例方法但不依赖 self，直接在实例上调用
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖单一场景
"""

import os
from unittest.mock import patch, AsyncMock

import pytest

from src.tools.read_file import ReadFileFunc, _resolve_lexer_name, LARGE_FILE_THRESHOLD
from src.tools.file_base import FileToolError


# ═══════════════════════════════════════════════════════════════════════════
# 1. _resolve_lexer_name
# ═══════════════════════════════════════════════════════════════════════════

class TestResolveLexerName:
    """_resolve_lexer_name 将扩展名映射为 Pygments lexer 名称。"""

    def test_empty_ext_returns_text(self):
        assert _resolve_lexer_name("") == "text"

    def test_known_unsupported_returns_text(self):
        assert _resolve_lexer_name("txt") == "text"
        assert _resolve_lexer_name("text") == "text"

    def test_py_returns_py(self):
        assert _resolve_lexer_name("py") == "py"

    def test_md_returns_md(self):
        assert _resolve_lexer_name("md") == "md"

    def test_uppercase_ext_not_lowered(self):
        """函数不主动小写扩展名，按原样返回（仅用于 _UNSUPPORTED 检查）"""
        assert _resolve_lexer_name("PY") == "PY"


# ═══════════════════════════════════════════════════════════════════════════
# 2. _validate_line_number
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateLineNumber:
    """_validate_line_number 行号验证和规范化。"""

    def test_none_returns_none(self):
        assert ReadFileFunc._validate_line_number(None, "start_line") is None

    def test_valid_positive_int(self):
        assert ReadFileFunc._validate_line_number(5, "start_line") == 5

    def test_zero_adjusts_to_one(self):
        result = ReadFileFunc._validate_line_number(0, "start_line")
        assert result == 1

    def test_negative_adjusts_to_one(self):
        result = ReadFileFunc._validate_line_number(-3, "start_line")
        assert result == 1

    def test_non_integer_returns_none(self):
        result = ReadFileFunc._validate_line_number("abc", "end_line")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. __init__
# ═══════════════════════════════════════════════════════════════════════════

class TestInit:
    """__init__ 路径安全校验和基本属性设置。"""

    def test_valid_path(self, tmp_path):
        f = tmp_path / "test.txt"
        rf = ReadFileFunc(str(f))
        assert rf.path == str(f)
        assert rf.encoding == "utf-8"
        assert rf.errors == "strict"
        assert rf.start_line is None
        assert rf.end_line is None

    def test_dangerous_path_raises(self):
        with pytest.raises((ValueError, FileToolError)) as exc:
            ReadFileFunc("/etc/passwd")
        # validate_path_security 对此路径直接抛 ValueError（非 FileToolBase 子类）
        # 检查错误消息中含有安全校验关键词即可
        assert any(kw in str(exc.value).lower() for kw in ("不允许", "安全", "system", "critical"))

    def test_start_end_line_stored(self, tmp_path):
        f = tmp_path / "test.txt"
        rf = ReadFileFunc(str(f), start_line=3, end_line=10)
        assert rf.start_line == 3
        assert rf.end_line == 10


# ═══════════════════════════════════════════════════════════════════════════
# 4. from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestFromArgs:
    """from_args 参数解析"""

    def test_basic_path(self, tmp_path):
        f = tmp_path / "test.txt"
        rf = ReadFileFunc.from_args({"path": str(f)})
        assert rf.path == str(f)
        assert rf.start_line is None
        assert rf.end_line is None

    def test_paths_array_format(self, tmp_path):
        """兼容旧的 paths 数组格式"""
        f = tmp_path / "test.txt"
        rf = ReadFileFunc.from_args({"paths": [str(f)]})
        assert rf.path == str(f)

    def test_paths_array_empty(self, tmp_path):
        """空的 paths 数组"""
        rf = ReadFileFunc.from_args({"paths": []})
        assert rf.path == ""

    def test_start_end_line(self, tmp_path):
        f = tmp_path / "test.txt"
        rf = ReadFileFunc.from_args({
            "path": str(f), "start_line": 3, "end_line": 10
        })
        assert rf.start_line == 3
        assert rf.end_line == 10

    def test_start_line_auto_swap(self, tmp_path):
        """start_line > end_line 自动交换"""
        f = tmp_path / "test.txt"
        rf = ReadFileFunc.from_args({
            "path": str(f), "start_line": 10, "end_line": 5
        })
        assert rf.start_line == 5
        assert rf.end_line == 10

    def test_invalid_start_line_ignored(self, tmp_path):
        f = tmp_path / "test.txt"
        rf = ReadFileFunc.from_args({
            "path": str(f), "start_line": "not_a_number"
        })
        assert rf.start_line is None

    def test_from_args_empty_dict_raises(self):
        """空字典 args 应抛出 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            ReadFileFunc.from_args({})

    def test_from_args_missing_path_raises(self):
        """缺失 path 参数的 args 应抛出 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            ReadFileFunc.from_args({"start_line": 1})


# ═══════════════════════════════════════════════════════════════════════════
# 5. _try_decode
# ═══════════════════════════════════════════════════════════════════════════

class TestTryDecode:
    """_try_decode 编码回退解码"""

    def _make_rf(self, tmp_path):
        """创建 ReadFileFunc 实例（路径在白名单内的 tmp_path）"""
        return ReadFileFunc(str(tmp_path / "_dummy_.txt"))

    def test_perfect_utf8_decode(self, tmp_path):
        """UTF-8 字节完美解码"""
        rf = self._make_rf(tmp_path)
        raw = "hello world".encode("utf-8")
        enc, content = rf._try_decode(raw, ["utf-8"])
        assert enc == "utf-8"
        assert content == "hello world"

    def test_perfect_gbk_decode(self, tmp_path):
        """GBK 编码字节完美解码"""
        rf = self._make_rf(tmp_path)
        raw = "中文测试".encode("gbk")
        enc, content = rf._try_decode(raw, ["gbk"])
        assert enc == "gbk"
        assert content == "中文测试"

    def test_fallback_to_second_candidate(self, tmp_path):
        """首候选编码失败时回退到下一候选"""
        rf = self._make_rf(tmp_path)
        # GBK 字节用 utf-8 解码会报错或出现替代字符
        raw = "中文".encode("gbk")
        enc, content = rf._try_decode(raw, ["utf-8", "gbk"])
        assert enc == "gbk"
        assert content == "中文"

    def test_replace_mode_fallback(self, tmp_path):
        """所有候选 strict 解码失败时降级为 replace，非通吃编码优先"""
        rf = self._make_rf(tmp_path)
        # 随机二进制字节
        raw = b"\xff\xfe\x00\x01\x02\x03"
        # 只用 latin-1 确保能解码（不会抛异常）
        enc, content = rf._try_decode(raw, ["utf-8", "gbk", "latin-1"])
        # 通吃编码评分 60，非通吃编码 utf-8/gbk 各有 2 替代字符评分 68
        # utf-8 （列表中靠前）胜出
        assert enc in ("utf-8", "gbk"), f"预期非通吃编码, 实际 {enc}"
        assert isinstance(content, str)

    def test_empty_bytes(self, tmp_path):
        """空字节"""
        rf = self._make_rf(tmp_path)
        raw = b""
        enc, content = rf._try_decode(raw, ["utf-8"])
        assert enc == "utf-8"
        assert content == ""

    def test_utf8_with_replacement_char(self, tmp_path):
        """UTF-8 字节中有 \ufffd 时选择替代字符少的非通吃编码"""
        rf = self._make_rf(tmp_path)
        # 创建一个在 gbk 下比 utf-8 下替代字符少的数据
        # GBK 能解码的混合字节
        raw = "ABC\x80\x81test".encode("latin-1")
        enc, content = rf._try_decode(raw, ["utf-8", "gbk", "latin-1"])
        # 通吃编码评分 60，非通吃编码 gbk(1替代=69) > utf-8(2替代=68)
        # gbk 胜出——满足「替代字符最少的非通吃编码优先」
        assert enc in ("gbk", "utf-8"), f"预期非通吃编码, 实际 {enc}"
        assert "ABC" in content

    # ── 乱码回归测试 ────────────────────────────────────────────

    def test_catchall_encoding_does_not_override_gbk_regression(self, tmp_path):
        """通吃编码 ISO-8859-9 不会压制正确的 GBK 解码

        chardet 对短 GBK 文本可能误检测为 ISO-8859-9/TIS-620 等
        通吃编码，_try_decode 必须继续尝试 FALLBACK 中的 GBK。
        """
        rf = self._make_rf(tmp_path)
        raw = "你好".encode("gbk")
        # chardet 报告 ISO-8859-9 (置信度低)
        enc, content = rf._try_decode(raw, ["ISO-8859-9"])
        assert enc == "gbk", f"期望 gbk, 实际 {enc}"
        assert content == "你好"

    def test_catchall_encoding_does_not_override_gbk_mixed_regression(self, tmp_path):
        """通吃编码 TIS-620 不会压制混合 GBK 正确解码"""
        rf = self._make_rf(tmp_path)
        raw = "abc你好123".encode("gbk")
        # chardet 报告 TIS-620 (置信度低)
        enc, content = rf._try_decode(raw, ["TIS-620"])
        assert enc == "gbk", f"期望 gbk, 实际 {enc}"
        assert content == "abc你好123"

    def test_utf8_remains_unchanged_regression(self, tmp_path):
        """纯 UTF-8/ASCII 文本不受影响"""
        rf = self._make_rf(tmp_path)
        raw = "Hello World!".encode("utf-8")
        enc, content = rf._try_decode(raw, ["ascii"])
        assert enc == "ascii"
        assert content == "Hello World!"

    def test_utf8_chinese_remains_unchanged_regression(self, tmp_path):
        """UTF-8 中文不受影响"""
        rf = self._make_rf(tmp_path)
        raw = "中文测试".encode("utf-8")
        enc, content = rf._try_decode(raw, ["utf-8"])
        assert enc == "utf-8"
        assert content == "中文测试"

    def test_catchall_lowercase_check_regression(self, tmp_path):
        """验证大写编码名也能正确匹配 _CATCHALL_ENCODINGS"""
        rf = self._make_rf(tmp_path)
        raw = "你好世界".encode("gbk")
        # ISO-8859-9 大写，需 lower() 后匹配
        enc, content = rf._try_decode(raw, ["ISO-8859-9"])
        assert enc == "gbk", f"期望 gbk, 实际 {enc}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. _determine_encoding
# ═══════════════════════════════════════════════════════════════════════════

class TestDetermineEncoding:
    """_determine_encoding 编码检测"""

    @pytest.mark.asyncio
    async def test_determine_returns_mocked_encoding(self, tmp_path):
        """mock async_detect_encoding 验证读取字节和返回编码"""
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world\n")

        rf = ReadFileFunc(str(f))
        with patch(
            "src.tools.read_file.async_detect_encoding",
            return_value="utf-8",
        ):
            encoding, raw_bytes = await rf._determine_encoding(str(f))
        assert encoding == "utf-8"
        assert raw_bytes == b"hello world\n"


# ═══════════════════════════════════════════════════════════════════════════
# 7. execute
# ═══════════════════════════════════════════════════════════════════════════

class TestExecute:
    """execute 读取文件内容"""

    @pytest.mark.asyncio
    async def test_read_normal_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\nworld\n")

        rf = ReadFileFunc(str(f))
        result = await rf.execute()
        assert "文件: " in result
        assert "hello" in result
        assert "world" in result

    @pytest.mark.asyncio
    async def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")

        rf = ReadFileFunc(str(f))
        result = await rf.execute()
        assert "文件为空" in result

    @pytest.mark.asyncio
    async def test_nonexistent_file(self):
        rf = ReadFileFunc("/tmp/nonexistent_file_xyz_123.txt")
        result = await rf.execute()
        assert "文件不存在" in result

    @pytest.mark.asyncio
    async def test_start_line_clips_content(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")

        rf = ReadFileFunc(str(f), start_line=2, end_line=4)
        result = await rf.execute()
        assert "line2" in result
        assert "line3" in result
        assert "line4" in result
        assert "line1" not in result
        assert "line5" not in result

    @pytest.mark.asyncio
    async def test_start_line_beyond_file(self, tmp_path):
        """start_line 超出文件行数范围"""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n")

        rf = ReadFileFunc(str(f), start_line=10, end_line=20)
        result = await rf.execute()
        assert "文件为空" in result

    @pytest.mark.asyncio
    async def test_end_line_clips_to_file_end(self, tmp_path):
        """end_line 超出文件末尾时自动裁剪"""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n")

        rf = ReadFileFunc(str(f), start_line=1, end_line=100)
        result = await rf.execute()
        assert "line1" in result
        assert "line2" in result

    @pytest.mark.asyncio
    async def test_start_line_alone(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")

        rf = ReadFileFunc(str(f), start_line=2)
        result = await rf.execute()
        assert "line1" not in result
        assert "line2" in result
        assert "line3" in result

    @pytest.mark.asyncio
    async def test_end_line_alone(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")

        rf = ReadFileFunc(str(f), end_line=2)
        result = await rf.execute()
        assert "line1" in result
        assert "line2" in result
        assert "line3" not in result

    @pytest.mark.asyncio
    async def test_carriage_return_cleaned(self, tmp_path):
        """\\r 被清理"""
        f = tmp_path / "test.txt"
        f.write_text("line1\r\nline2\r\n")

        rf = ReadFileFunc(str(f))
        result = await rf.execute()
        assert "\r" not in result

    @pytest.mark.asyncio
    async def test_encoding_detection_fallback(self, tmp_path):
        """GBK 编码文件能正常读取"""
        f = tmp_path / "gbk.txt"
        content = "中文测试\n第二行\n"
        f.write_bytes(content.encode("gbk"))

        rf = ReadFileFunc(str(f))
        result = await rf.execute()
        assert "中文测试" in result
        assert "第二行" in result


# ═══════════════════════════════════════════════════════════════════════════
# 8. display
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplay:
    """display 显示并返回文件内容"""

    @pytest.mark.asyncio
    async def test_display_returns_same_as_execute(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\nworld\n")

        rf = ReadFileFunc(str(f))
        exec_result = await rf.execute()
        display_result = await rf.display()
        assert display_result == exec_result

    @pytest.mark.asyncio
    async def test_display_failed_file(self):
        rf = ReadFileFunc("/tmp/nonexistent_file_xyz_456.txt")
        result = await rf.display()
        assert "文件不存在" in result


# ═══════════════════════════════════════════════════════════════════════════
# 9. web_display
# ═══════════════════════════════════════════════════════════════════════════

class TestWebDisplay:
    """web_display 返回带行号范围的文件内容"""

    @pytest.mark.asyncio
    async def test_web_display_returns_line_range(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")

        rf = ReadFileFunc(str(f))
        result = await rf.web_display()
        assert "文件:" in result
        assert "(L1-3)" in result or "(L1-" in result
        assert "line1" in result

    @pytest.mark.asyncio
    async def test_web_display_with_line_range(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\nline4\n")

        rf = ReadFileFunc(str(f), start_line=2, end_line=3)
        result = await rf.web_display()
        assert "L2-3" in result
        assert "line2" in result
        assert "line3" in result
        assert "line1" not in result

    @pytest.mark.asyncio
    async def test_web_display_nonexistent(self):
        rf = ReadFileFunc("/tmp/nonexistent_file_xyz_789.txt")
        result = await rf.web_display()
        assert "文件不存在" in result


# ═══════════════════════════════════════════════════════════════════════════
# 10. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params 参数摘要显示"""

    def test_basic_path(self):
        result = ReadFileFunc.display_params({"path": "/tmp/test.txt"})
        assert "/tmp/test.txt" in result

    def test_with_start_line(self):
        result = ReadFileFunc.display_params({
            "path": "/tmp/test.txt", "start_line": 10
        })
        assert "offset:10" in result

    def test_with_end_line(self):
        result = ReadFileFunc.display_params({
            "path": "/tmp/test.txt", "end_line": 20
        })
        assert "limit:20" in result

    def test_with_both(self):
        result = ReadFileFunc.display_params({
            "path": "/tmp/test.txt", "start_line": 5, "end_line": 15
        })
        assert "offset:5" in result
        assert "limit:15" in result

    def test_empty_path(self):
        result = ReadFileFunc.display_params({})
        assert result == ""

    def test_paths_array(self):
        result = ReadFileFunc.display_params({"paths": ["/tmp/test.txt"]})
        assert "/tmp/test.txt" in result

    def test_long_path_not_truncated(self):
        """长路径不再被截断，返回完整内容。"""
        long_path = "/" + "a" * 100
        result = ReadFileFunc.display_params({"path": long_path}, max_len=20)
        assert "a" * 100 in result


# ═══════════════════════════════════════════════════════════════════════════
# 11. to_tool_schema
# ═══════════════════════════════════════════════════════════════════════════

class TestToToolSchema:
    """to_tool_schema 返回正确的 schema 格式"""

    def test_schema_structure(self):
        schema = ReadFileFunc.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "read_file"
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props
        assert "start_line" in props
        assert "end_line" in props
        assert schema["function"]["parameters"]["required"] == ["path"]
