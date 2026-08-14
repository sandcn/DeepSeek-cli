"""测试适配器 reasoning_effort 注入。

覆盖：DeepSeekAdapter / OpenAICompatAdapter 在 V4 模型注入 thinking 参数时
携带 /reasoning 命令配置的推理等级（low/medium/high/max）；
reasoner / classic 模型不注入 thinking 参数。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.api.adapters.deepseek import DeepSeekAdapter
from src.api.adapters.openai_compat import OpenAICompatAdapter

_MESSAGES = [{"role": "user", "content": "hi"}]


class TestDeepSeekAdapterReasoningEffort:
    """DeepSeekAdapter.build_request_kwargs 注入。"""

    @pytest.fixture(autouse=True)
    def _restore_effort(self):
        yield
        # 清理 patch 对 src.config 模块属性的影响
        patch.stopall()

    def test_v4_injects_default_max(self):
        adapter = DeepSeekAdapter()
        kwargs = adapter.build_request_kwargs(messages=_MESSAGES, model="deepseek-v4-pro")
        assert kwargs.get("thinking") == {"type": "enabled", "reasoning_effort": "max"}

    @pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
    def test_v4_injects_configured_effort(self, level):
        adapter = DeepSeekAdapter()
        with patch("src.config.REASONING_EFFORT", level):
            kwargs = adapter.build_request_kwargs(
                messages=_MESSAGES, model="deepseek-v4-flash",
            )
            assert kwargs.get("thinking") == {"type": "enabled", "reasoning_effort": level}

    def test_reasoner_no_thinking_param(self):
        adapter = DeepSeekAdapter()
        kwargs = adapter.build_request_kwargs(messages=_MESSAGES, model="deepseek-reasoner")
        assert kwargs.get("thinking") is None

    def test_classic_no_thinking_param(self):
        adapter = DeepSeekAdapter()
        for model in ("deepseek-chat", "deepseek-coder"):
            kwargs = adapter.build_request_kwargs(messages=_MESSAGES, model=model)
            assert kwargs.get("thinking") is None

    def test_stream_kwargs_also_inject(self):
        adapter = DeepSeekAdapter()
        with patch("src.config.REASONING_EFFORT", "low"):
            kwargs = adapter.build_request_kwargs(
                messages=_MESSAGES, model="deepseek-v4-pro",
                stream=True, stream_options={"include_usage": True},
            )
            assert kwargs.get("stream") is True
            assert kwargs.get("thinking") == {"type": "enabled", "reasoning_effort": "low"}


class TestOpenAICompatAdapterReasoningEffort:
    """OpenAICompatAdapter.build_request_kwargs 注入。"""

    def test_v4_injects_configured_effort(self):
        adapter = OpenAICompatAdapter()
        with patch("src.config.REASONING_EFFORT", "high"):
            kwargs = adapter.build_request_kwargs(messages=_MESSAGES, model="deepseek-v4-pro")
            assert kwargs.get("thinking") == {"type": "enabled", "reasoning_effort": "high"}

    def test_non_v4_model_no_thinking(self):
        adapter = OpenAICompatAdapter()
        kwargs = adapter.build_request_kwargs(messages=_MESSAGES, model="glm-5.2")
        assert kwargs.get("thinking") is None

    def test_reasoner_model_no_thinking(self):
        adapter = OpenAICompatAdapter()
        kwargs = adapter.build_request_kwargs(messages=_MESSAGES, model="deepseek-reasoner")
        assert kwargs.get("thinking") is None


class TestReasoningEffortFallback:
    """配置读取异常时回退 max。"""

    def test_get_reasoning_effort_empty_falls_back(self):
        from src.api.adapters.deepseek import _get_reasoning_effort
        with patch("src.config.REASONING_EFFORT", ""):
            assert _get_reasoning_effort() == "max"

    def test_get_reasoning_effort_import_error_falls_back(self):
        """config 读取抛异常时回退 max（防御性）。"""
        from src.api.adapters.deepseek import _get_reasoning_effort
        import src.config as cfg_mod
        with patch.object(cfg_mod, "__getattr__", side_effect=AttributeError("boom")):
            assert _get_reasoning_effort() == "max"
