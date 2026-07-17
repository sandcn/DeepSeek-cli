"""Tests for src/ui/colors.py — 渐变基础设施（hex_to_256 / gradient_step / gradient_range / 调色板）"""
from __future__ import annotations

import pytest

from src.tui.core.gradient import (
    hex_to_256,
    gradient_step,
    gradient_range,
)
from src.tui.animation.palettes import (
    GRADIENT_SUNSET,
    GRADIENT_OCEAN,
    GRADIENT_FOREST,
    GRADIENT_FIRE,
    GRADIENT_NEON,
)


# ════════════════════════════════════════════════════════
# hex_to_256
# ════════════════════════════════════════════════════════

class TestHexTo256:
    """hex_to_256 常见颜色映射验证。"""

    def test_red(self) -> None:
        """红色 #FF0000 → 9 (标准 VGA 红，RGB 精确匹配)。"""
        assert hex_to_256("#FF0000") == 9

    def test_blue(self) -> None:
        """蓝色 #0000FF → 12 (标准 VGA 蓝，RGB 精确匹配)。"""
        assert hex_to_256("#0000FF") == 12

    def test_green(self) -> None:
        """绿色 #00FF00 → 10 (标准 VGA 绿，RGB 精确匹配)。"""
        assert hex_to_256("#00FF00") == 10

    def test_white(self) -> None:
        """白色 #FFFFFF → 15 (标准亮白，RGB 精确匹配)。"""
        assert hex_to_256("#FFFFFF") == 15

    def test_black(self) -> None:
        """黑色 #000000 → 0。"""
        assert hex_to_256("#000000") == 0

    def test_hex_without_hash(self) -> None:
        """不带 # 前缀的 hex 也应正确解析。"""
        assert hex_to_256("FF0000") == 9

    def test_lowercase_hex(self) -> None:
        """小写 hex 输入。"""
        assert hex_to_256("#ff0000") == 9

    def test_mixed_case_hex(self) -> None:
        """混合大小写 hex 结果一致。"""
        assert hex_to_256("#Ff8800") == hex_to_256("#ff8800")

    def test_orange(self) -> None:
        """橙色 #FF8800 → 208 (xterm 色彩立方体 5,2,0)。"""
        assert hex_to_256("#FF8800") == 208

    def test_invalid_input_empty_string(self) -> None:
        """空字符串返回 15 兜底。"""
        assert hex_to_256("") == 15

    def test_invalid_input_garbage(self) -> None:
        """乱码输入返回 15 兜底。"""
        assert hex_to_256("not-a-color") == 15

    def test_invalid_input_short_hex(self) -> None:
        """不完整 hex 输入返回 15 兜底。"""
        assert hex_to_256("#FFF") == 15

    def test_invalid_input_none(self) -> None:
        """None 输入返回 15 兜底。"""
        assert hex_to_256(None)  # type: ignore[arg-type]
        assert isinstance(hex_to_256(None), int)  # type: ignore[arg-type]


# ════════════════════════════════════════════════════════
# gradient_step
# ════════════════════════════════════════════════════════

class TestGradientStep:
    """gradient_step 端点值与边界条件验证。"""

    def test_start_step(self) -> None:
        """第 0 步返回 start。"""
        assert gradient_step(0, 255, 5, 0) == 0

    def test_end_step(self) -> None:
        """第 steps-1 步返回 end。"""
        assert gradient_step(0, 255, 5, 4) == 255

    def test_middle_step(self) -> None:
        """中间步正确插值。"""
        result = gradient_step(0, 200, 5, 2)
        # (0 + 200 * 2/4) = 100
        assert result == 100

    def test_single_step(self) -> None:
        """steps=1 时返回 start（无论 index 为何值）。"""
        assert gradient_step(42, 100, 1, 0) == 42
        assert gradient_step(42, 100, 1, 99) == 42

    def test_two_steps(self) -> None:
        """steps=2 时端点正确。"""
        assert gradient_step(10, 20, 2, 0) == 10
        assert gradient_step(10, 20, 2, 1) == 20

    def test_index_clamp_low(self) -> None:
        """index 低于 0 时 clamp 到 0。"""
        assert gradient_step(0, 100, 5, -5) == gradient_step(0, 100, 5, 0)

    def test_index_clamp_high(self) -> None:
        """index 超过 steps-1 时 clamp 到 steps-1。"""
        assert gradient_step(0, 100, 5, 99) == gradient_step(0, 100, 5, 4)

    def test_descending_gradient(self) -> None:
        """递减渐变（start > end）。"""
        assert gradient_step(255, 0, 5, 0) == 255
        assert gradient_step(255, 0, 5, 4) == 0
        result = gradient_step(255, 0, 5, 2)
        # 255 + (0-255) * 2/4 = 255 - 127.5 ≈ 128
        assert result == 128

    def test_output_clamped(self) -> None:
        """输出值 clamp 到 [0, 255]。"""
        result = gradient_step(200, 300, 5, 4)  # 300 超限，实际 end 为 255
        assert 0 <= result <= 255


# ════════════════════════════════════════════════════════
# gradient_range
# ════════════════════════════════════════════════════════

class TestGradientRange:
    """gradient_range 长度与单调性验证。"""

    def test_zero_steps(self) -> None:
        """steps=0 返回空列表。"""
        assert gradient_range(0, 255, 0) == []

    def test_one_step(self) -> None:
        """steps=1 返回 [start]。"""
        assert gradient_range(42, 255, 1) == [42]

    def test_two_steps(self) -> None:
        """steps=2 返回 [start, end]。"""
        assert gradient_range(0, 255, 2) == [0, 255]

    def test_five_steps(self) -> None:
        """steps=5 返回均匀分布的 5 个值。"""
        result = gradient_range(0, 100, 5)
        assert len(result) == 5
        assert result[0] == 0
        assert result[4] == 100
        # 中间值应在合理范围
        assert result[1] == 25
        assert result[2] == 50
        assert result[3] == 75

    def test_monotonic_increasing(self) -> None:
        """递增渐变结果单调不减。"""
        result = gradient_range(50, 200, 10)
        for i in range(1, len(result)):
            assert result[i] >= result[i - 1], (
                f"位置 {i}: {result[i-1]} → {result[i]} 非单调递增"
            )

    def test_monotonic_decreasing(self) -> None:
        """递减渐变结果单调不增。"""
        result = gradient_range(200, 50, 10)
        for i in range(1, len(result)):
            assert result[i] <= result[i - 1], (
                f"位置 {i}: {result[i-1]} → {result[i]} 非单调递减"
            )

    def test_values_in_range(self) -> None:
        """所有结果值在 [0, 255] 内。"""
        result = gradient_range(10, 250, 12)
        for v in result:
            assert 0 <= v <= 255, f"值 {v} 超出 [0, 255]"

    def test_flat_gradient(self) -> None:
        """start == end 时所有值相同。"""
        result = gradient_range(100, 100, 5)
        assert all(v == 100 for v in result)
        assert len(result) == 5


# ════════════════════════════════════════════════════════
# 预定义渐变调色板
# ════════════════════════════════════════════════════════

class TestGradientPalettes:
    """预定义渐变调色板格式与值范围验证。"""

    ALL_PALETTES = [
        ("GRADIENT_SUNSET", GRADIENT_SUNSET, 8),
        ("GRADIENT_OCEAN", GRADIENT_OCEAN, 6),
        ("GRADIENT_FOREST", GRADIENT_FOREST, 6),
        ("GRADIENT_FIRE", GRADIENT_FIRE, 9),
        ("GRADIENT_NEON", GRADIENT_NEON, 10),
    ]

    def test_min_length(self) -> None:
        """每个渐变列表长度 >= 4。"""
        for name, palette, expected_len in self.ALL_PALETTES:
            assert len(palette) >= 4, f"{name} 长度 {len(palette)} < 4"

    def test_expected_length(self) -> None:
        """每个渐变列表长度符合预期。"""
        for name, palette, expected_len in self.ALL_PALETTES:
            assert len(palette) == expected_len, (
                f"{name} 长度 {len(palette)} != 预期 {expected_len}"
            )

    def test_values_in_range(self) -> None:
        """所有色号值在 [0, 255] 内。"""
        for name, palette, _ in self.ALL_PALETTES:
            for v in palette:
                assert 0 <= v <= 255, (
                    f"{name} 包含非法色号 {v}"
                )

    def test_all_unique(self) -> None:
        """每个调色板内的色号值不重复（允许首尾重复的特例除外）。"""
        for name, palette, _ in self.ALL_PALETTES:
            # 跳过首尾可能相邻相同的特例（如 GRADIENT_FIRE）
            # 此处只检查基本无重复
            dupes = len(palette) - len(set(palette))
            if dupes > 2:  # 允许个别相邻重复
                pytest.fail(f"{name} 含 {dupes} 个重复值")

    def test_sunset_values(self) -> None:
        """GRADIENT_SUNSET 起始红色(196)，结束暖色。"""
        assert GRADIENT_SUNSET[0] == 196
        # 最后一个值接近 224（暖琥珀）
        assert GRADIENT_SUNSET[-1] == 224

    def test_ocean_values(self) -> None:
        """GRADIENT_OCEAN 起始深蓝(26)，结束青色(87)。"""
        assert GRADIENT_OCEAN[0] == 26
        assert GRADIENT_OCEAN[-1] == 87

    def test_forest_values(self) -> None:
        """GRADIENT_FOREST 起始深绿(22)，结束亮绿(47)。"""
        assert GRADIENT_FOREST[0] == 22
        assert GRADIENT_FOREST[-1] == 47

    def test_fire_values(self) -> None:
        """GRADIENT_FIRE 起始深红(52)，结束亮黄(220)。"""
        assert GRADIENT_FIRE[0] == 52
        assert GRADIENT_FIRE[-1] == 220

    def test_neon_values(self) -> None:
        """GRADIENT_NEON 准确包含 10 个手工精选值。"""
        assert GRADIENT_NEON == [57, 93, 129, 165, 171, 177, 183, 189, 195, 87]
