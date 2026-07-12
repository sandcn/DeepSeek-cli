"""Tests for src/core/constants.py — 共享常量模块"""
from __future__ import annotations

import pytest

from src.core.constants import (
    # 8-bit 常量
    GRAY, WHITE, CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA,
    BOLD, DIM, RESET, ITALIC, UNDERLINE,
    BRIGHT_CYAN, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE,
    BRIGHT_MAGENTA, BRIGHT_RED, BRIGHT_WHITE, BRIGHT_BLACK,
    BG_BLUE, BG_CYAN, BG_GREEN, BG_YELLOW,
    ORANGE, TEAL, PINK, LAVENDER,
    SOFT_GREEN, SOFT_BLUE, SOFT_YELLOW, DARK_GRAY,
    # 256 色常量
    GRAY_256, WHITE_256, CYAN_256, GREEN_256, YELLOW_256,
    RED_256, BLUE_256, MAGENTA_256,
    BRIGHT_CYAN_256, BRIGHT_GREEN_256, BRIGHT_YELLOW_256,
    BRIGHT_BLUE_256, BRIGHT_MAGENTA_256, BRIGHT_RED_256,
    BRIGHT_WHITE_256, BRIGHT_BLACK_256,
    BG_BLUE_256, BG_CYAN_256, BG_GREEN_256, BG_YELLOW_256,
    ORANGE_256, TEAL_256, PINK_256, LAVENDER_256,
    SOFT_GREEN_256, SOFT_BLUE_256, SOFT_YELLOW_256, DARK_GRAY_256,
)


class Test256ColorConstants:
    """256 色扩展常量的完整性、格式和别名关系验证。"""

    # ── 所有非别名的 256 色前景常量 ──
    FOREGROUND_256 = [
        GRAY_256, WHITE_256, CYAN_256, GREEN_256, YELLOW_256,
        RED_256, BLUE_256, MAGENTA_256,
        BRIGHT_CYAN_256, BRIGHT_GREEN_256, BRIGHT_YELLOW_256,
        BRIGHT_BLUE_256, BRIGHT_MAGENTA_256, BRIGHT_RED_256,
        BRIGHT_WHITE_256, BRIGHT_BLACK_256,
        SOFT_GREEN_256, SOFT_BLUE_256, SOFT_YELLOW_256, DARK_GRAY_256,
    ]

    # ── 所有非别名的 256 色背景常量 ──
    BACKGROUND_256 = [
        BG_BLUE_256, BG_CYAN_256, BG_GREEN_256, BG_YELLOW_256,
    ]

    def test_foreground_256_format(self) -> None:
        """所有 256 色前景常量以 \033[38;5; 开头。"""
        for const in self.FOREGROUND_256:
            assert const.startswith("\033[38;5;"), (
                f"预期前景 256 色格式 \\033[38;5;N，实际: {repr(const[:12])}"
            )

    def test_background_256_format(self) -> None:
        """所有 256 色背景常量以 \033[48;5; 开头。"""
        for const in self.BACKGROUND_256:
            assert const.startswith("\033[48;5;"), (
                f"预期背景 256 色格式 \\033[48;5;N，实际: {repr(const[:12])}"
            )

    def test_eight_bit_constants_unchanged(self) -> None:
        """所有原始 8-bit 常量保持原值不变。"""
        assert GRAY == "\033[90m"
        assert WHITE == "\033[37m"
        assert CYAN == "\033[36m"
        assert GREEN == "\033[32m"
        assert YELLOW == "\033[33m"
        assert RED == "\033[31m"
        assert BLUE == "\033[34m"
        assert MAGENTA == "\033[35m"
        assert BOLD == "\033[1m"
        assert DIM == "\033[2m"
        assert RESET == "\033[0m"
        assert ITALIC == "\033[3m"
        assert UNDERLINE == "\033[4m"
        assert BRIGHT_CYAN == "\033[96m"
        assert BRIGHT_GREEN == "\033[92m"
        assert BRIGHT_YELLOW == "\033[93m"
        assert BRIGHT_BLUE == "\033[94m"
        assert BRIGHT_MAGENTA == "\033[95m"
        assert BRIGHT_WHITE == "\033[97m"
        assert BRIGHT_RED == "\033[91m"
        assert BRIGHT_BLACK == GRAY
        assert BG_BLUE == "\033[44m"
        assert BG_CYAN == "\033[46m"
        assert BG_GREEN == "\033[42m"
        assert BG_YELLOW == "\033[43m"
        assert SOFT_GREEN == "\033[92m"
        assert SOFT_BLUE == "\033[94m"
        assert SOFT_YELLOW == "\033[93m"

    def test_alias_relationships(self) -> None:
        """256 色别名关系与原始 8-bit 保持一致。"""
        # ORANGE_256 是 YELLOW_256 的别名
        assert ORANGE_256 is YELLOW_256, "ORANGE_256 应为 YELLOW_256 的别名"
        # TEAL_256 是 CYAN_256 的别名
        assert TEAL_256 is CYAN_256, "TEAL_256 应为 CYAN_256 的别名"
        # PINK_256 是 MAGENTA_256 的别名
        assert PINK_256 is MAGENTA_256, "PINK_256 应为 MAGENTA_256 的别名"
        # LAVENDER_256 是 BRIGHT_MAGENTA_256 的别名
        assert LAVENDER_256 is BRIGHT_MAGENTA_256, (
            "LAVENDER_256 应为 BRIGHT_MAGENTA_256 的别名"
        )

    def test_color_number_in_range(self) -> None:
        """所有 256 色号在有效范围 [0, 255] 内。"""
        import re
        for const in self.FOREGROUND_256 + self.BACKGROUND_256:
            match = re.search(r"38;5;(\d+)|48;5;(\d+)", const)
            assert match is not None, f"无法从 {repr(const)} 中提取色号"
            num = int(match.group(1) or match.group(2))
            assert 0 <= num <= 255, f"色号 {num} 超出 [0, 255]"

    def test_dark_gray_256_distinct(self) -> None:
        """DARK_GRAY_256 拥有独立的 256 色值，不再是 GRAY_256 的别名。"""
        assert DARK_GRAY_256 is not GRAY_256
        assert DARK_GRAY_256 == "\033[38;5;237m"

    def test_bright_black_256_distinct(self) -> None:
        """BRIGHT_BLACK_256 拥有独立的 256 色值，不再是 GRAY_256 的别名。"""
        assert BRIGHT_BLACK_256 is not GRAY_256
        assert BRIGHT_BLACK_256 == "\033[38;5;239m"

    def test_import_all_256_constants(self) -> None:
        """验证所有 28 个 256 色常量均可导入。"""
        _256_constants = [
            GRAY_256, WHITE_256, CYAN_256, GREEN_256, YELLOW_256,
            RED_256, BLUE_256, MAGENTA_256,
            BRIGHT_CYAN_256, BRIGHT_GREEN_256, BRIGHT_YELLOW_256,
            BRIGHT_BLUE_256, BRIGHT_MAGENTA_256, BRIGHT_RED_256,
            BRIGHT_WHITE_256, BRIGHT_BLACK_256,
            BG_BLUE_256, BG_CYAN_256, BG_GREEN_256, BG_YELLOW_256,
            ORANGE_256, TEAL_256, PINK_256, LAVENDER_256,
            SOFT_GREEN_256, SOFT_BLUE_256, SOFT_YELLOW_256, DARK_GRAY_256,
        ]
        assert len(_256_constants) == 28
        for c in _256_constants:
            assert isinstance(c, str)


class TestEightBitConstantsPreserved:
    """确保原始 8-bit 常量未因新增 256 色常量而受影响。"""

    def test_bright_black_alias(self) -> None:
        """BRIGHT_BLACK 仍是 GRAY 的别名。"""
        assert BRIGHT_BLACK is GRAY

    def test_dark_gray_alias(self) -> None:
        """DARK_GRAY 仍是 GRAY 的别名。"""
        assert DARK_GRAY is GRAY

    def test_orange_alias(self) -> None:
        """ORANGE 仍是 YELLOW 的别名。"""
        assert ORANGE is YELLOW

    def test_teal_alias(self) -> None:
        """TEAL 仍是 CYAN 的别名。"""
        assert TEAL is CYAN

    def test_pink_alias(self) -> None:
        """PINK 仍是 MAGENTA 的别名。"""
        assert PINK is MAGENTA

    def test_lavender_alias(self) -> None:
        """LAVENDER 仍是 BRIGHT_MAGENTA 的别名。"""
        assert LAVENDER is BRIGHT_MAGENTA
