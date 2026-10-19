"""summary_token_budget 默认值（2000 → 100 → 100k）单元测试。

覆盖：
  - DEFAULTS / CONFIG_KEYS 默认值为 100000（100k tokens）且单一事实源
    （防双源漂移）；
  - MockConfigAdapter 缺省回退 100000、显式配置值优先；
  - build_summary_prompt 未显式传预算时按默认 100000 折算
    （budget_chars=150000），超出部分被截断；
  - 极小预算仍受单条消息 200 字符下限保护；
  - 插件 ContextSummarizerService 透传 summary_token_budget（修复前误把
    max_output_chars 当摘要预算传入）；
  - 配置写入边界：负数被拒绝。
"""

from __future__ import annotations

import pytest

from src.config.defaults import CONFIG_KEYS, DEFAULTS
from src.core.adapters.config import MockConfigAdapter
from src.core import context_summarizer as cs


class TestSummaryTokenBudgetDefaults:
    def test_defaults_is_100000(self):
        assert DEFAULTS["summary_token_budget"] == 100000

    def test_config_key_references_defaults(self):
        """CONFIG_KEYS 的 default 必须与 DEFAULTS 同源。"""
        entry = CONFIG_KEYS["SUMMARY_TOKEN_BUDGET"]
        assert entry["rc_path"] == ("summary_token_budget",)
        assert entry["type"] is int
        assert entry["default"] == DEFAULTS["summary_token_budget"] == 100_000

    def test_mock_adapter_falls_back_to_100000(self):
        assert MockConfigAdapter().get_summary_token_budget() == 100_000

    def test_mock_adapter_explicit_value_wins(self):
        assert MockConfigAdapter({"summary_token_budget": 512}).get_summary_token_budget() == 512


class TestDefaultBudgetUsedByPrompt:
    def test_default_budget_truncates_to_150k_chars(self, monkeypatch):
        """默认 100000 tokens → 150000 字符预算（单条消息全额可用）。"""
        import src.config as config_mod

        monkeypatch.setattr(
            config_mod, "SUMMARY_TOKEN_BUDGET",
            DEFAULTS["summary_token_budget"], raising=False,
        )
        prompt = cs.build_summary_prompt(
            [{"role": "user", "content": "x" * 200000}],
            has_prior_summary=False,
        )
        assert "x" * 150000 in prompt
        assert "x" * 150001 not in prompt

    def test_default_budget_keeps_short_message_intact(self, monkeypatch):
        """100k 预算下 5000 字符的普通消息不会被截断。"""
        import src.config as config_mod

        monkeypatch.setattr(
            config_mod, "SUMMARY_TOKEN_BUDGET",
            DEFAULTS["summary_token_budget"], raising=False,
        )
        prompt = cs.build_summary_prompt(
            [{"role": "user", "content": "z" * 5000}],
            has_prior_summary=False,
        )
        assert "z" * 5000 in prompt

    def test_small_budget_keeps_per_message_floor(self):
        """预算 100 tokens → 150 字符 < 单条消息下限 200 字符。"""
        prompt = cs.build_summary_prompt(
            [{"role": "user", "content": "y" * 5000}],
            has_prior_summary=False,
            summary_token_budget=100,
        )
        assert "y" * 200 in prompt
        assert "y" * 201 not in prompt

    def test_explicit_budget_overrides_default(self):
        prompt = cs.build_summary_prompt(
            [{"role": "user", "content": "w" * 5000}],
            has_prior_summary=False,
            summary_token_budget=1000,
        )
        # 1000 * 1.5 = 1500 字符
        assert "w" * 1500 in prompt
        assert "w" * 1501 not in prompt


class TestPluginServicePassesBudget:
    @staticmethod
    def _service():
        from src.plugins.runtime_data import ContextSummarizerService

        class _Ctx:
            def __init__(self):
                self.provided = {}

            def provide(self, key, value):
                self.provided[key] = value

        return ContextSummarizerService(_Ctx())

    def test_service_passes_summary_token_budget(self):
        prompt = self._service().build_summary_prompt(
            [{"role": "user", "content": "v" * 5000}],
            False,
            summary_token_budget=1000,
        )
        assert "v" * 1500 in prompt
        assert "v" * 1501 not in prompt

    def test_service_defaults_to_config_budget(self, monkeypatch):
        import src.config as config_mod

        monkeypatch.setattr(config_mod, "SUMMARY_TOKEN_BUDGET", 100000, raising=False)
        prompt = self._service().build_summary_prompt(
            [{"role": "user", "content": "u" * 200000}],
            False,
        )
        assert "u" * 150000 in prompt
        assert "u" * 150001 not in prompt


class TestSummaryTokenBudgetBounds:
    def test_negative_budget_rejected_on_write(self):
        from src.config.loader import check_config_bounds

        with pytest.raises(ValueError):
            check_config_bounds("summary_token_budget", -1)

    def test_zero_allowed(self):
        from src.config.loader import check_config_bounds

        check_config_bounds("summary_token_budget", 0)
