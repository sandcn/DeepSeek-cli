"""Tests for src/core/constants.py — 共享常量模块"""

from src.core.constants import TOOL_OUTPUT_TRUNCATE


# ═══════════════════════════════════════════════════════════════
# TOOL_OUTPUT_TRUNCATE 常量测试
# ═══════════════════════════════════════════════════════════════

class TestToolOutputTruncate:
    """TOOL_OUTPUT_TRUNCATE 常量验证"""

    # ── 存在性与类型 ──────────────────────────────────────────

    def test_exists(self):
        """常量已被定义且不为 None"""
        assert TOOL_OUTPUT_TRUNCATE is not None

    def test_is_integer(self):
        """常量类型为 int"""
        assert isinstance(TOOL_OUTPUT_TRUNCATE, int)

    # ── 值验证 ────────────────────────────────────────────────

    def test_value_is_500(self):
        """常量值应为 500"""
        assert TOOL_OUTPUT_TRUNCATE == 500

    def test_not_negative(self):
        """截断长度为非负数"""
        assert TOOL_OUTPUT_TRUNCATE >= 0
