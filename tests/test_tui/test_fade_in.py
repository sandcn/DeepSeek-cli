"""FadeIn 功能统一测试 — 验证 apply_fade_in 和 build_fade_in_ansi 合并后行为。

覆盖场景：
  - apply_fade_in 基本参数行为（帧号、缓动、色号范围）
  - build_fade_in_ansi 向后兼容性（色号常量不变）
  - 窄屏跳过行为
  - text_utils 与 _base 导入同源验证
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tui.core.style import FADE_COLOR_DARK
from src.tui.core.text_utils import apply_fade_in, build_fade_in_ansi


# ═══════════════════════════════════════════════════════════
# apply_fade_in 基本行为
# ═══════════════════════════════════════════════════════════

class TestApplyFadeIn:
    """apply_fade_in() 基本行为验证。"""

    def test_frame_0_returns_text_unchanged(self):
        """帧号 ≤ 0 时返回原文本。"""
        result = apply_fade_in("hello", 0)
        assert result == "hello"

    def test_negative_frame_returns_text_unchanged(self):
        """负帧号返回原文本。"""
        result = apply_fade_in("world", -1)
        assert result == "world"

    def test_empty_text_returns_empty(self):
        """空文本返回空字符串。"""
        result = apply_fade_in("", 5)
        assert result == ""

    def test_fade_prefix_wraps_text(self):
        """帧号 > 0 时文本被 FadeIn ANSI 前缀包裹。"""
        # frame > 0, total_frames=6 应该产生 ANSI 前缀
        result = apply_fade_in("test", 3, easing="linear", total_frames=6,
                               start_color=238, end_color=255)
        # 应包含 ANSI 转义前缀 + "test" + RESET
        assert result.startswith("\033[")
        assert "test" in result
        assert result.endswith("\033[0m")
        # 前缀不应为空
        prefix = result[:-7]  # 去掉 "test\033[0m"
        assert prefix.startswith("\033[38;5;")

    def test_fade_prefix_empty_for_last_frame(self):
        """最后一帧（frame == total_frames）FadeIn 返回空前缀。"""
        result = apply_fade_in("test", 6, easing="linear", total_frames=6,
                               start_color=238, end_color=255)
        # frame >= total_frames 时 FadeIn.render() 返回空字符串
        # 此时 apply_fade_in 返回原文本
        assert result == "test"

    def test_different_easing_produces_different_colors(self):
        """不同 easing 在同一帧产生不同色号（smooth vs linear 差异）。"""
        smooth = apply_fade_in("x", 3, easing="smooth", total_frames=6,
                               start_color=238, end_color=255)
        linear = apply_fade_in("x", 3, easing="linear", total_frames=6,
                               start_color=238, end_color=255)
        # smooth 和 linear 在中间帧色号不同
        assert smooth != linear, "不同 easing 应产生不同结果"

    def test_color_range_clamped(self):
        """色号范围在 0-255 内。"""
        result = apply_fade_in("test", 3, easing="linear", total_frames=6,
                               start_color=0, end_color=255)
        prefix = result[:-7]
        # 提取色号数字
        import re
        match = re.search(r"38;5;(\d+)", prefix)
        assert match is not None, "应包含 256 色号"
        color = int(match.group(1))
        assert 0 <= color <= 255, f"色号应在 0-255 之间，实际: {color}"


# ═══════════════════════════════════════════════════════════
# build_fade_in_ansi 向后兼容性
# ═══════════════════════════════════════════════════════════

class TestBuildFadeInAnsiBackwardCompat:
    """build_fade_in_ansi() 向后兼容性验证。"""

    def test_frame_0_uses_fade_color_dark(self):
        """第 0 帧使用 FADE_COLOR_DARK(238) 色号。"""
        result = build_fade_in_ansi(0, total_frames=3)
        expected = f"\033[38;5;{FADE_COLOR_DARK}m"
        assert result == expected, (
            f"fade_frame=0 应输出色号 {FADE_COLOR_DARK}, 实际: {repr(result)}"
        )

    def test_frame_1_produces_ansi(self):
        """第 1 帧产生有效的 ANSI 颜色序列。"""
        result = build_fade_in_ansi(1, total_frames=3)
        assert result.startswith("\033[38;5;"), (
            f"fade_frame=1 应产生 ANSI 颜色序列, 实际: {repr(result)}"
        )
        assert result.endswith("m"), (
            f"fade_frame=1 应以 'm' 结尾, 实际: {repr(result)}"
        )

    def test_frame_2_produces_ansi(self):
        """第 2 帧产生有效的 ANSI 颜色序列（FadeIn 连续插值）。"""
        result = build_fade_in_ansi(2, total_frames=3)
        assert result.startswith("\033[38;5;"), (
            f"fade_frame=2 应产生 ANSI 颜色序列, 实际: {repr(result)}"
        )

    def test_frame_exceeds_total_returns_empty(self):
        """第 3 帧（>= total_frames）返回空字符串。"""
        result = build_fade_in_ansi(3, total_frames=3)
        assert result == "", f"fade_frame=3 应返回空字符串, 实际: {repr(result)}"

    def test_custom_total_frames(self):
        """自定义 total_frames 参数。"""
        result = build_fade_in_ansi(0, total_frames=5)
        expected = f"\033[38;5;{FADE_COLOR_DARK}m"
        assert result == expected, "total_frames=5 时第 0 帧仍使用 FADE_COLOR_DARK"


# ═══════════════════════════════════════════════════════════
# 窄屏跳过行为
# ═══════════════════════════════════════════════════════════

class TestNarrowScreen:
    """窄屏时跳过渐显行为。"""

    @patch("src.tui.animation.transitions.is_narrow", return_value=True)
    def test_apply_fade_in_skips_on_narrow(self, mock_narrow):
        """窄屏时 apply_fade_in 返回原文本（FadeIn 窄屏跳过后返回空前缀）。"""
        result = apply_fade_in("hello", 3, easing="linear", total_frames=6,
                               start_color=238, end_color=255)
        # apply_fade_in 委托 FadeIn.render()，FadeIn 内部调用来自
        # animation/transitions 模块的 is_narrow 函数
        # 窄屏时 FadeIn.render() 返回空字符串，apply_fade_in 返回原文本
        assert result == "hello"

    @patch("src.tui.terminal.terminal.is_narrow", return_value=True)
    def test_build_fade_in_ansi_skips_on_narrow(self, mock_narrow):
        """窄屏时 build_fade_in_ansi 返回空字符串。"""
        result = build_fade_in_ansi(0, total_frames=3)
        assert result == "", "窄屏时应返回空字符串"


# ═══════════════════════════════════════════════════════════
# 导入同源验证
# ═══════════════════════════════════════════════════════════

class TestImportConsistency:
    """验证从 _base 导入的 apply_fade_in 与从 text_utils 导入的是同一函数。"""

    def test_import_from_base_is_same_as_text_utils(self):
        """从 _base 和 text_utils 导入的 apply_fade_in 是同一对象。"""
        from src.tui.components._base import apply_fade_in as base_apply
        assert base_apply is apply_fade_in, (
            "从 _base 和 text_utils 导入的 apply_fade_in 应为同一对象"
        )

    def test_import_from_tui_init_is_same_as_text_utils(self):
        """从 __init__ 和 text_utils 导入的 apply_fade_in 是同一对象。"""
        from src.tui import apply_fade_in as init_apply
        assert init_apply is apply_fade_in, (
            "从 __init__ 和 text_utils 导入的 apply_fade_in 应为同一对象"
        )
