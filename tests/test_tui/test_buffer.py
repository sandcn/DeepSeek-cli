"""test_buffer — RenderBuffer（精简版）单元测试。

测试范围：
  - 构造器/属性/边界条件
  - write() — 基础写入、换行、越界、style 前缀
  - write_char() — 单字符写入
  - merge() — 叠加、透明字符
  - fill() — 区域填充
  - clear() / clear_row() / clear_col()
  - sub_buffer() — 子缓冲区独立副本
  - render() / render_raw() — 输出格式
  - measure_text() — 文本测量
  - hcenter() / hline() — 便捷操作
"""

from __future__ import annotations

import pytest
from src.tui._buffer import RenderBuffer


# ═══════════════════════════════════════════════════════════
# 构造器与属性
# ═══════════════════════════════════════════════════════════

class TestConstructor:
    """构造器与基础属性测试。"""

    def test_default_constructor(self):
        buf = RenderBuffer(10, 3)
        assert buf.width == 10
        assert buf.height == 3
        assert not buf.is_empty()

    def test_zero_dimensions(self):
        buf = RenderBuffer(0, 5)
        assert buf.is_empty()

        buf2 = RenderBuffer(5, 0)
        assert buf2.is_empty()

    def test_negative_dimensions_clamped(self):
        buf = RenderBuffer(-5, -3)
        assert buf.width == 0
        assert buf.height == 0
        assert buf.is_empty()

    def test_custom_default_char(self):
        buf = RenderBuffer(3, 2, default_char=".")
        result = buf.render()
        # 两行，每行 "." * 3，去行尾空格后为 "." * 3
        assert "..." in result


# ═══════════════════════════════════════════════════════════
# write() — 基础写入
# ═══════════════════════════════════════════════════════════

class TestWrite:
    """write() 方法测试。"""

    def test_basic_write(self):
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Hello")
        assert "Hello" in buf.render()

    def test_write_with_newline(self):
        buf = RenderBuffer(20, 5)
        buf.write(0, 0, "Line1\nLine2")
        result = buf.render()
        assert "Line1" in result
        assert "Line2" in result

    def test_write_out_of_bounds_x(self):
        """超出右边界静默丢弃。"""
        buf = RenderBuffer(5, 3)
        buf.write(3, 0, "Hello")  # "He" 可见 (col 3-4)，"llo" 丢弃
        result = buf.render()
        # 前两个字符 "He" 应出现在 x=3,4 位置
        lines = result.split("\n")
        assert lines[0] == "   He"

    def test_write_out_of_bounds_y(self):
        """超出下边界静默丢弃。"""
        buf = RenderBuffer(10, 2)
        buf.write(0, 3, "Hello")  # y=3 超出 height=2
        result = buf.render()
        assert "Hello" not in result

    def test_write_negative_y(self):
        buf = RenderBuffer(10, 3)
        buf.write(0, -1, "Hello")
        result = buf.render()
        assert "Hello" not in result

    def test_write_empty_text(self):
        buf = RenderBuffer(10, 3)
        buf.fill("X", 0, 0, 10, 3)
        buf.write(0, 0, "")
        result = buf.render()
        assert "X" in result  # 未改动

    def test_write_with_style_prefix(self):
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Red", style="\033[31m")
        result = buf.render()
        assert "\033[31mRed" in result

    def test_write_with_none_style(self):
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Plain", style=None)
        result = buf.render()
        assert "Plain" in result

    def test_write_negative_x(self):
        """x 为负时，跳过前 |x| 字符。"""
        buf = RenderBuffer(10, 3)
        buf.write(-2, 0, "Hello")
        result = buf.render()
        # 跳过前 2 个字符 "He"，写入 "llo" 从 x=0 开始
        assert result.startswith("llo")


# ═══════════════════════════════════════════════════════════
# write_char() — 单字符写入
# ═══════════════════════════════════════════════════════════

class TestWriteChar:
    """write_char() 方法测试。"""

    def test_basic_write_char(self):
        buf = RenderBuffer(10, 3)
        buf.write_char(5, 1, "X")
        lines = buf.render().split("\n")
        assert lines[1][5] == "X"

    def test_write_char_out_of_bounds(self):
        buf = RenderBuffer(10, 3)
        buf.write_char(20, 0, "X")  # 越界 x
        buf.write_char(0, 10, "X")  # 越界 y
        # 不应抛异常
        assert True

    def test_write_char_with_style(self):
        buf = RenderBuffer(10, 3)
        buf.write_char(0, 0, "R", style="\033[31m")
        result = buf.render()
        assert "\033[31mR" in result


# ═══════════════════════════════════════════════════════════
# merge() — 叠加
# ═══════════════════════════════════════════════════════════

class TestMerge:
    """merge() 方法测试。"""

    def test_basic_merge(self):
        src = RenderBuffer(10, 3)
        src.write(0, 0, "HELLO")
        dst = RenderBuffer(10, 3)
        dst.fill(".", 0, 0, 10, 3)
        dst.merge(src, 0, 0)
        result = dst.render()
        assert "HELLO" in result

    def test_merge_with_offset(self):
        src = RenderBuffer(5, 2)
        src.write(0, 0, "WORLD")
        src.write(0, 1, "earth")
        dst = RenderBuffer(15, 4)
        dst.fill(".", 0, 0, 15, 4)
        dst.merge(src, 5, 1)
        result = dst.render()
        lines = result.split("\n")
        # 行 1（y=1）第 5 列开始应为 "WORLD"
        assert "WORLD" in lines[1]

    def test_merge_transparent_char(self):
        src = RenderBuffer(5, 3, default_char=".")
        src.write(0, 1, "MID")
        dst = RenderBuffer(5, 3)
        dst.fill("-", 0, 0, 5, 3)
        dst.merge(src, 0, 0, transparent_char=".")
        result = dst.render()
        lines = result.split("\n")
        # 行 0 保持 "-----"（因为 src 行 0 全是透明 "."）
        assert lines[0] == "-----"
        # 行 1 包含 "MID"
        assert "MID" in lines[1]

    def test_merge_empty_source(self):
        src = RenderBuffer(0, 0)
        dst = RenderBuffer(10, 3)
        dst.fill("X", 0, 0, 10, 3)
        dst.merge(src, 0, 0)  # 空源不改变目标
        result = dst.render()
        assert "X" in result

    def test_merge_partial_overlap(self):
        """部分越界的 merge 被裁剪。"""
        src = RenderBuffer(8, 4)
        src.fill("S", 0, 0, 8, 4)
        dst = RenderBuffer(10, 3)
        dst.fill(".", 0, 0, 10, 3)
        dst.merge(src, 5, 1)  # 从 (5,1) 开始，部分超出右/下边界
        result = dst.render()
        # 不应抛异常
        assert result


# ═══════════════════════════════════════════════════════════
# fill() — 区域填充
# ═══════════════════════════════════════════════════════════

class TestFill:
    """fill() 方法测试。"""

    def test_fill_region(self):
        buf = RenderBuffer(10, 5)
        buf.fill("X", 2, 1, 4, 3)
        lines = buf.render().split("\n")
        # 行 1-3，列 2-5 应为 "X"
        for row in [1, 2, 3]:
            assert lines[row][2:6] == "XXXX"

    def test_fill_partial_overflow(self):
        """填充区域部分越界时自动裁剪。"""
        buf = RenderBuffer(10, 3)
        buf.fill("X", 8, 2, 5, 5)
        # 不应抛异常，仅填充 (8,2) → (9,2)
        lines = buf.render().split("\n")
        assert lines[2][8:10] == "XX"


# ═══════════════════════════════════════════════════════════
# clear() / clear_row() / clear_col()
# ═══════════════════════════════════════════════════════════

class TestClear:
    """清空方法测试。"""

    def test_clear(self):
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Hello World")
        assert "Hello" in buf.render()
        buf.clear()
        result = buf.render()
        assert result == ""

    def test_clear_row(self):
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Row0")
        buf.write(0, 1, "Row1")
        buf.write(0, 2, "Row2")
        buf.clear_row(1)
        lines = buf.render().split("\n")
        assert "Row0" in lines[0]
        assert lines[1] == ""
        assert "Row2" in lines[2]

    def test_clear_row_out_of_bounds(self):
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Test")
        buf.clear_row(10)  # 越界，不抛异常
        assert "Test" in buf.render()

    def test_clear_col(self):
        buf = RenderBuffer(5, 3)
        buf.fill("X", 0, 0, 5, 3)
        buf.clear_col(2)
        lines = buf.render().split("\n")
        for line in lines:
            if len(line) > 2:
                assert line[2] == " "


# ═══════════════════════════════════════════════════════════
# sub_buffer() — 子缓冲区
# ═══════════════════════════════════════════════════════════

class TestSubBuffer:
    """sub_buffer() 方法测试。"""

    def test_sub_buffer(self):
        buf = RenderBuffer(10, 5)
        buf.fill("X", 0, 0, 10, 5)
        buf.write(3, 2, "ABC")
        sub = buf.sub_buffer(2, 1, 6, 3)
        assert sub.width == 6
        assert sub.height == 3
        result = sub.render()
        # 子缓冲区第 2 行（对应源行 3=1+2）第 1 列（对应源列 3=2+1）应为 "A"
        lines = result.split("\n")
        assert "ABC" in lines[1]

    def test_sub_buffer_independence(self):
        """修改子缓冲区不影响源缓冲区。"""
        buf = RenderBuffer(10, 3)
        buf.write(0, 0, "Source")
        sub = buf.sub_buffer(0, 0, 5, 2)
        sub.write(0, 0, "CHANGED")
        # 源缓冲区不变
        assert "Source" in buf.render()
        assert "Source" not in sub.render()

    def test_sub_buffer_clip(self):
        """超出边界自动裁剪。"""
        buf = RenderBuffer(10, 5)
        sub = buf.sub_buffer(8, 4, 10, 10)
        assert sub.width <= 2  # 10-8=2
        assert sub.height <= 1  # 5-4=1

    def test_sub_buffer_zero(self):
        """完全越界返回空缓冲区。"""
        buf = RenderBuffer(10, 5)
        sub = buf.sub_buffer(20, 20, 5, 5)
        assert sub.is_empty()


# ═══════════════════════════════════════════════════════════
# render() / render_raw()
# ═══════════════════════════════════════════════════════════

class TestRender:
    """输出方法测试。"""

    def test_render_empty(self):
        buf = RenderBuffer(0, 0)
        assert buf.render() == ""

    def test_render_trailing_spaces_removed(self):
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Hi")
        result = buf.render()
        lines = result.split("\n")
        # 行尾空格应被移除
        assert lines[0] == "Hi"

    def test_render_raw_preserves_spaces(self):
        buf = RenderBuffer(20, 3)
        buf.write(0, 0, "Hi  ")
        raw = buf.render_raw()
        # render_raw 保留行尾空格但移除纯空白行
        assert "Hi  " in raw

    def test_render_removes_trailing_empty_lines(self):
        buf = RenderBuffer(10, 5)
        buf.write(0, 0, "Only")
        result = buf.render()
        assert not result.endswith("\n")


# ═══════════════════════════════════════════════════════════
# measure_text()
# ═══════════════════════════════════════════════════════════

class TestMeasureText:
    """measure_text() 静态方法测试。"""

    def test_measure_single_line(self):
        w, h = RenderBuffer.measure_text("Hello World")
        assert w == 11
        assert h == 1

    def test_measure_multiline(self):
        w, h = RenderBuffer.measure_text("Short\nLonger line")
        assert h == 2
        assert w == 11  # max(5, 11) = 11

    def test_measure_empty(self):
        w, h = RenderBuffer.measure_text("")
        assert w == 0
        assert h == 0


# ═══════════════════════════════════════════════════════════
# hcenter() / hline()
# ═══════════════════════════════════════════════════════════

class TestConvenience:
    """便捷操作方法测试。"""

    def test_hcenter(self):
        buf = RenderBuffer(20, 3)
        buf.hcenter("CENTER", 1)
        lines = buf.render().split("\n")
        # 文本宽度 6，居中列 = (20-6)//2 = 7
        assert lines[1][7:13] == "CENTER"

    def test_hcenter_with_style(self):
        buf = RenderBuffer(20, 3)
        buf.hcenter("X", 1, style="\033[31m")
        result = buf.render()
        assert "\033[31m" in result

    def test_hline(self):
        buf = RenderBuffer(10, 3)
        buf.hline(1)
        lines = buf.render().split("\n")
        assert lines[1] == "\u2500" * 10

    def test_hline_custom_char(self):
        buf = RenderBuffer(10, 3)
        buf.hline(0, char="=")
        lines = buf.render().split("\n")
        assert lines[0] == "=" * 10

    def test_hline_out_of_bounds(self):
        buf = RenderBuffer(10, 3)
        buf.hline(5)  # 越界不抛异常
        assert True
