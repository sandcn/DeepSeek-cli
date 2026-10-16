"""src/api/protocols — LLMProtocol 运行时协议校验单元测试。

验证 runtime_checkable 协议：真实适配器满足协议、缺失方法/属性被拒绝、
isinstance 与接口行为一致性。
"""

from __future__ import annotations

import pytest

from src.api.protocols import LLMProtocol


class _GoodAdapter:
    """实现协议全部成员。"""

    provider_name = "good"

    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None) -> dict:
        return {}

    def parse_response(self, response: dict) -> dict:
        return {"content": "", "reasoning_content": "", "usage": {}, "tool_calls": []}

    def parse_stream_chunk(self, chunk: dict) -> dict:
        return {"content": "", "reasoning_content": "", "tool_calls": [], "usage": None}


class _MissingMethod:
    provider_name = "bad"

    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None) -> dict:
        return {}

    # parse_response / parse_stream_chunk 缺失


class _MissingAttribute:
    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None) -> dict:
        return {}

    def parse_response(self, response: dict) -> dict:
        return {}

    def parse_stream_chunk(self, chunk: dict) -> dict:
        return {}


def test_runtime_checkable_isinstance_good():
    assert isinstance(_GoodAdapter(), LLMProtocol)


def test_runtime_checkable_rejects_missing_method():
    assert not isinstance(_MissingMethod(), LLMProtocol)


def test_runtime_checkable_rejects_missing_attribute():
    assert not isinstance(_MissingAttribute(), LLMProtocol)


def test_real_adapters_satisfy_protocol():
    """内置适配器均满足 LLMProtocol（协议与实现一致性的回归护栏）。"""
    from src.api.adapters import (
        AnthropicAdapter, DeepSeekAdapter, OllamaAdapter, OpenAICompatAdapter,
    )

    for adapter_cls in (
        DeepSeekAdapter, AnthropicAdapter, OllamaAdapter, OpenAICompatAdapter,
    ):
        assert isinstance(adapter_cls(), LLMProtocol), adapter_cls.__name__


def test_protocol_member_signatures_exist():
    """协议接口可被内省（成员名齐全）。"""
    members = set(getattr(LLMProtocol, "__annotations__", {}))
    expected = {"provider_name"}
    assert expected <= members or hasattr(LLMProtocol, "provider_name")
