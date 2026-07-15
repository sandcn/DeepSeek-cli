"""
Diff 渲染器美化（256 色）单元测试。

验证 diff_renderer.py 中的背景色和颜色前缀已升级为 256 色。
"""

from __future__ import annotations

import difflib
import os

import pytest

from src.tui.consumer.diff_renderer import (
    _BG_RED,
    _BG_GREEN,
    _BG_OFF,
    render_diff_to_ansi,
)


# ── 辅助函数 ─────────────────────────────────────────────

_256_FG_RE = "\033[38;5;"
_256_BG_RE = "\033[48;5;"


def _has_256_color(text: str) -> bool:
    """判断字符串中是否包含 256 色 ANSI 序列。"""
    return _256_FG_RE in text or _256_BG_RE in text


# ── 背景色常量测试 ──────────────────────────────────────


class TestBackgroundColors:
    """验证 _BG_RED / _BG_GREEN 已升级为 256 色。"""

    def test_bg_red_is_256_color(self):
        """_BG_RED 应以 48;5; 开头（256 色背景）。"""
        assert _BG_RED.startswith("\033[48;5;"), (
            f"_BG_RED = {_BG_RED!r} 不是 256 色背景码"
        )

    def test_bg_green_is_256_color(self):
        """_BG_GREEN 应以 48;5; 开头（256 色背景）。"""
        assert _BG_GREEN.startswith("\033[48;5;"), (
            f"_BG_GREEN = {_BG_GREEN!r} 不是 256 色背景码"
        )

    def test_bg_off_unchanged(self):
        """_BG_OFF 保持为默认背景重置码不变。"""
        assert _BG_OFF == "\033[49m", (
            f"_BG_OFF 应为 \\033[49m，实际 = {_BG_OFF!r}"
        )

    def test_bg_red_specific_color(self):
        """_BG_RED 使用暗红背景色号 124（或等价的柔和色号）。"""
        # 124 = 暗红，52 = 深红（均可接受）
        code = _BG_RED.replace("\033[48;5;", "").rstrip("m")
        color_num = int(code)
        assert color_num in (52, 88, 124), (
            f"_BG_RED 色号 {color_num} 不在预期集合 {{52, 88, 124}} 中"
        )

    def test_bg_green_specific_color(self):
        """_BG_GREEN 使用柔和绿背景色号 28（或等价的深绿色号）。"""
        code = _BG_GREEN.replace("\033[48;5;", "").rstrip("m")
        color_num = int(code)
        assert color_num in (22, 28, 34), (
            f"_BG_GREEN 色号 {color_num} 不在预期集合 {{22, 28, 34}} 中"
        )


# ── render_diff_to_ansi 输出测试 ────────────────────────


class TestRenderDiffToAnsi:
    """验证 render_diff_to_ansi 输出包含 256 色序列。"""

    def test_simple_diff_contains_256_color(self):
        """简单差异渲染输出应包含 256 色序列。"""
        old_content = "line1\nline2\nline3\n"
        new_content = "line1\nline2_modified\nline3\n"
        result = render_diff_to_ansi("test.py", old_content, new_content)
        assert result, "差异渲染结果不应为空"
        assert _has_256_color(result), (
            f"渲染输出应包含 256 色序列\n输出: {result[:200]}"
        )

    def test_diff_color_bar_present(self):
        """差异渲染应包含 ▐ 颜色条前缀。"""
        old_content = "hello\nworld\nfoo\n"
        new_content = "hello\nworld\nbar\n"
        result = render_diff_to_ansi("test.txt", old_content, new_content)
        assert "▐" in result, (
            "差异渲染应包含 ▐ 颜色条"
        )

    def test_diff_empty_content(self):
        """空旧内容 + 有新内容的差异应正常渲染。"""
        old_content = ""
        new_content = "new_line_1\nnew_line_2\n"
        result = render_diff_to_ansi("new_file.py", old_content, new_content)
        assert result, "新增文件差异不应为空"
        assert _has_256_color(result)

    def test_diff_identical_content(self):
        """相同内容应返回空字符串。"""
        content = "same\ncontent\n"
        result = render_diff_to_ansi("same.txt", content, content)
        assert result == "", "相同内容应返回空字符串"

    def test_diff_with_multiple_hunks(self):
        """多处修改的差异应包含多个 hunk 且全部含 256 色。"""
        old_lines = [f"line_{i}" for i in range(20)]
        new_lines = list(old_lines)
        new_lines[3] = "modified_3"
        new_lines[14] = "modified_14"
        old_content = "\n".join(old_lines)
        new_content = "\n".join(new_lines)
        result = render_diff_to_ansi("multi_hunk.py", old_content, new_content)
        assert "@@" in result, "应包含 hunk 头"
        assert _has_256_color(result)

    def test_compatible_signature(self):
        """render_diff_to_ansi 签名保持不变（向后兼容）。"""
        # 调用方式应与旧版本一致：path, old, new → str
        result = render_diff_to_ansi("compat.py", "a\nb\n", "a\nb\nc\n")
        assert isinstance(result, str), "返回值类型应为 str"


# ── 完整 diff 流程测试 ──────────────────────────────────


class TestDiffRendererColors:
    """验证完整 diff 渲染流程中各颜色组件的 256 色升级。"""

    def test_added_lines_use_green_256(self):
        """新增行应使用 GREEN_256 (41) 色码。"""
        old = "keep\n"
        new = "keep\nadded\n"
        result = render_diff_to_ansi("add_test.py", old, new)
        # 新增行使用 GREEN_256 = \033[38;5;41m
        assert "\033[38;5;41m" in result, (
            "新增行应包含 GREEN_256 (38;5;41) 色码"
        )

    def test_deleted_lines_use_red_256(self):
        """删除行应使用 RED_256 (196) 色码。"""
        old = "removed\nkeep\n"
        new = "keep\n"
        result = render_diff_to_ansi("del_test.py", old, new)
        # 删除行使用 RED_256 = \033[38;5;196m
        assert "\033[38;5;196m" in result, (
            "删除行应包含 RED_256 (38;5;196) 色码"
        )

    def test_hunk_header_uses_cyan_256(self):
        """hunk 头应使用 CYAN_256 (45) + BOLD 色码。"""
        old = "a\nb\nc\n"
        new = "a\nB\nc\n"
        result = render_diff_to_ansi("hunk_test.py", old, new)
        # hunk 头使用 CYAN_256 = \033[38;5;45m + BOLD = \033[1m
        assert "\033[1m" in result, "hunk 头应包含 BOLD"
        assert "\033[38;5;45m" in result, (
            "hunk 头应包含 CYAN_256 (38;5;45) 色码"
        )
