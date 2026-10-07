"""上下文窗口默认配置（60k → 1M tokens）单元测试。

覆盖：
  - DEFAULTS 上下文阈值为 1M token 窗口（token / 字符 / 强压阈值 / 窗口分母）；
  - CONFIG_KEYS 的 default 引用 DEFAULTS（单一事实源，无 60000 双源漂移）；
  - MockConfigAdapter 缺省回退与 DEFAULTS 一致、DefaultConfigAdapter 与配置同源；
  - 阈值口径：字符阈值覆盖纯 ASCII 会话 ≥80% 的 1M 窗口（不被提前截断），
    且对中文（≈2.5 token/字符）远大于窗口、不会先于 token 阈值触发；
  - schema 校验：负数字符阈值回退到新默认值；
  - 欢迎页上下文容量显示（1M / 1.5M / 60k / 500 / 0）。
"""

from __future__ import annotations

import pytest

from src.config.defaults import CONFIG_KEYS, DEFAULTS
from src.core.adapters.config import DefaultConfigAdapter, MockConfigAdapter
from src.core.tokens import estimate_tokens


class TestContextWindowDefaults:
    def test_token_threshold_is_one_million(self):
        assert DEFAULTS["max_context_tokens"] == 1_000_000
        assert DEFAULTS["model_context_tokens"] == 1_000_000

    def test_char_threshold_is_three_million(self):
        assert DEFAULTS["max_context_chars"] == 3_000_000
        assert DEFAULTS["auto_force_compress_threshold"] == 3_000_000

    def test_config_keys_reference_defaults(self):
        """CONFIG_KEYS 的 default 必须与 DEFAULTS 同源（防双源漂移）。"""
        assert CONFIG_KEYS["MAX_CONTEXT_TOKENS"]["default"] == DEFAULTS["max_context_tokens"]
        assert CONFIG_KEYS["MAX_CONTEXT_CHARS"]["default"] == DEFAULTS["max_context_chars"]
        assert (
            CONFIG_KEYS["AUTO_FORCE_COMPRESS_THRESHOLD"]["default"]
            == DEFAULTS["auto_force_compress_threshold"]
        )
        assert CONFIG_KEYS["MODEL_CONTEXT_TOKENS"]["default"] == DEFAULTS["model_context_tokens"]

    def test_char_threshold_covers_ascii_window(self):
        """纯 ASCII（≈0.3 token/字符）下字符阈值覆盖 ≥80% 的 1M 窗口。"""
        tokens_at_char_limit = estimate_tokens("a" * DEFAULTS["max_context_chars"])
        assert tokens_at_char_limit <= DEFAULTS["max_context_tokens"]
        assert tokens_at_char_limit >= DEFAULTS["max_context_tokens"] * 0.8

    def test_char_threshold_never_limits_cjk(self):
        """中文（≈2.5 token/字符）下字符阈值对应 token 数远超窗口，不会提前触发。"""
        cjk_ratio = estimate_tokens("中" * 1000) / 1000
        assert cjk_ratio > 2.0
        assert cjk_ratio * DEFAULTS["max_context_chars"] > DEFAULTS["max_context_tokens"] * 5


class TestAdapterFallbacks:
    def test_mock_adapter_defaults(self):
        adapter = MockConfigAdapter()
        assert adapter.get_max_context_tokens() == DEFAULTS["max_context_tokens"]
        assert adapter.get_max_context_chars() == DEFAULTS["max_context_chars"]
        assert (
            adapter.get_auto_force_compress_threshold()
            == DEFAULTS["auto_force_compress_threshold"]
        )
        assert adapter.get_model_context_tokens() == DEFAULTS["model_context_tokens"]

    def test_mock_adapter_explicit_data_wins(self):
        adapter = MockConfigAdapter({"max_context_tokens": 123, "max_context_chars": 456})
        assert adapter.get_max_context_tokens() == 123
        assert adapter.get_max_context_chars() == 456

    def test_default_adapter_matches_config(self):
        from src.config.proxy import config

        expected = config.get("max_context_tokens") or DEFAULTS["max_context_tokens"]
        assert DefaultConfigAdapter().get_max_context_tokens() == expected


class TestSchemaValidation:
    def test_negative_context_chars_falls_back_to_new_default(self):
        from src.config.schema import _validate_rc

        rc = _validate_rc({"max_context_chars": -1})
        assert rc["max_context_chars"] == DEFAULTS["max_context_chars"]


class TestWelcomeCapacityDisplay:
    @staticmethod
    def _patch_tokens(monkeypatch, tokens):
        from src.config.proxy import ConfigProxy

        def fake_get(self, key, default=None):
            return tokens if key == "max_context_tokens" else default

        monkeypatch.setattr(ConfigProxy, "get", fake_get)

    @pytest.mark.parametrize("tokens,expected", [
        (1_000_000, "1M tokens"),
        (1_500_000, "1.5M tokens"),
        (2_000_000, "2M tokens"),
        (60_000, "60k tokens"),
        (500, "500 tokens"),
        (0, ""),
        (-1, ""),
    ])
    def test_context_capacity_display(self, monkeypatch, tokens, expected):
        from src.tui.app._welcome import _context_capacity

        self._patch_tokens(monkeypatch, tokens)
        assert _context_capacity() == expected
