"""测试 src/tui/_format.py — 公共格式化模块（方向C 步骤4 收敛）。

覆盖 format_duration / format_tokens / format_speed 的边界与规范格式。
"""

from __future__ import annotations

from src.tui._format import format_duration, format_tokens, format_speed


class TestFormatDuration:
    """format_duration 边界：0s / 59s / 60s / 3600s / 负数。"""

    def test_zero(self):
        assert format_duration(0) == "0.0s"

    def test_sub_minute(self):
        assert format_duration(0.5) == "0.5s"
        assert format_duration(59.4) == "59.4s"
        assert format_duration(59.9) == "59.9s"

    def test_exact_minute(self):
        assert format_duration(60) == "1:00"

    def test_minutes_seconds(self):
        assert format_duration(75) == "1:15"
        assert format_duration(3599) == "59:59"

    def test_hour(self):
        assert format_duration(3600) == "1:00:00"
        assert format_duration(3661) == "1:01:01"
        assert format_duration(7200) == "2:00:00"

    def test_negative(self):
        # 负数按 <60s 分支：x.xs（与旧 status_bar 实现一致）
        assert format_duration(-5) == "-5.0s"


class TestFormatTokens:
    """format_tokens：原样数字 / k / M。"""

    def test_plain(self):
        assert format_tokens(0) == "0"
        assert format_tokens(999) == "999"

    def test_k(self):
        assert format_tokens(1000) == "1.0k"
        assert format_tokens(12500) == "12.5k"

    def test_m(self):
        assert format_tokens(1_000_000) == "1.0M"
        assert format_tokens(2_300_000) == "2.3M"


class TestFormatSpeed:
    """format_speed：≤0 / 慢速 / 各级单位。"""

    def test_non_positive(self):
        assert format_speed(0) == "-"
        assert format_speed(-1) == "-"

    def test_slow(self):
        assert format_speed(0.25) == "0.25/s"

    def test_units(self):
        assert format_speed(1) == "1.0/s"
        assert format_speed(50.5) == "50.5/s"
        assert format_speed(100) == "100/s"
        assert format_speed(1500) == "2k/s"
        assert format_speed(1_000_000) == "1.0M/s"
