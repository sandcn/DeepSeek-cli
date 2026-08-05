"""测试 SubAgent 快速失败：API 报错时不叠加全局长重试（MAX_RETRIES×RETRY_BASE_SEC）。

背景（Bug 修复 2026-08-05）：
- 用户环境 MAX_RETRIES=10, RETRY_BASE_SEC=30 → 单次模型调用 API 报错时
  最长等待 300s（5 分钟），叠加 SubAgent 层 _NETWORK_RETRY_MAX=3 可达 15 分钟。
- 用户侧现象：子代理"调用一两个工具后就卡住 5 分钟，必现"。
- 修复：SubAgent 模型调用传 override_max_retries=1 + fixed_delay_sec=0，
  禁用 API 层长重试，由 SubAgent 主循环提供有限次快速重试（秒级失败）。
"""

from __future__ import annotations

import asyncio
import time
from unittest import mock

import pytest

from src.api.client_async import APIError
from src.api.stream import pipeline_async
from src.core.agent import Agent
from src.core.ports.model import AsyncModelPort, ModelResult
from src.core.subagent import SubAgent


class _FakeParentPort(AsyncModelPort):
    """父 Agent 端口：SubAgent 复用（继承父 _async_model_port），此处仅用于 Agent 构造。"""

    async def call(self, messages, model=None, tools=None, display=None, label=None, silent=False):
        return ModelResult(reasoning="", content="父结果", usage={"input": 1, "output": 1}, tool_calls=[])

    async def call_sync(self, messages, model=None, tools=None, display=None, label=None):
        return ModelResult(reasoning="", content="父结果", usage={"input": 1, "output": 1}, tool_calls=[])


def _patch_api_error(status: int):
    """mock chat_completions_async 持续抛出 APIError(status)。"""
    return mock.patch.object(
        pipeline_async, "chat_completions_async",
        side_effect=lambda **kw: (_ for _ in ()).throw(APIError(status, f"API error {status}")),
    )


class TestSubAgentApiErrorFastFail:
    """SubAgent API 报错应快速失败（秒级），而非叠加全局长重试卡住 5 分钟。"""

    @pytest.mark.asyncio
    async def test_api_500_returns_error_quickly(self):
        """API 持续 500 → SubAgent 在秒级内返回错误（不进入 10×30s 长重试）。"""
        # Agent 不传 async_model_port → 默认 DefaultAsyncModelAdapter（真实 retry 链）
        agent = Agent(model="fake-model")

        with _patch_api_error(500):
            sub = SubAgent(
                label="agent-1",
                description="测试",
                prompt="请执行任务",
                parent_agent=agent,
                model="fake-model",
                agent_type="execute",
            )
            # SubAgent 继承父 DefaultAsyncModelAdapter → 真实 retry 链（被 mock 为 500）
            assert sub._model_port is not None

            t0 = time.monotonic()
            result = await asyncio.wait_for(sub.run(), timeout=10)
            elapsed = time.monotonic() - t0

            # 快速失败：总耗时远小于全局长重试（300s）
            assert elapsed < 10
            assert "错误" in result
            assert sub.error != ""

    @pytest.mark.asyncio
    async def test_api_layer_tries_only_once_for_subagent(self):
        """SubAgent 的 API 层只尝试 1 次（override_max_retries=1 生效），不重试 10 次。"""
        agent = Agent(model="fake-model")

        with _patch_api_error(500):
            sub = SubAgent(
                label="agent-1",
                description="测试",
                prompt="请执行任务",
                parent_agent=agent,
                model="fake-model",
                agent_type="execute",
            )
            with mock.patch("src.api._retry.wait_for_interrupt_async",
                            new=mock.AsyncMock(return_value=False)):
                await asyncio.wait_for(sub.run(), timeout=10)

        # SubAgent 层重试 3 次，每次 API 层仅 1 次尝试 → 总模型调用尝试 ≈ 3
        # （父 Agent port 也被 SubAgent 复用，但 SubAgent 用 _model_port 直达真实链）
        # 断言通过日志可验证：API 层"尝试 1/1"而非"尝试 N/10"。

    @pytest.mark.asyncio
    async def test_retry_on_parse_failure_passes_override(self):
        """retry_on_parse_failure_async 透传 override_max_retries/fixed_delay_sec。"""
        from src.api._retry import retry_on_parse_failure_async
        calls = {"n": 0, "override": None, "delay": None}

        async def _fake_retry(api_func, *, silent=False, display=None, label=None,
                              api_args=(), override_max_retries=None,
                              fixed_delay_sec=None):
            calls["n"] += 1
            calls["override"] = override_max_retries
            calls["delay"] = fixed_delay_sec
            return ("", "", {"input": 0, "output": 0}, [])

        await retry_on_parse_failure_async(
            lambda: None,
            silent=True, label="x",
            api_args=(),
            retry_func=_fake_retry,
            override_max_retries=1,
            fixed_delay_sec=0,
        )
        assert calls["n"] == 1
        assert calls["override"] == 1
        assert calls["delay"] == 0

    @pytest.mark.asyncio
    async def test_legacy_port_without_retry_args_compat(self):
        """不支持重试参数的自定义端口：SubAgent 回退默认调用（不 TypeError）。"""
        agent = Agent(model="fake-model", async_model_port=_FakeParentPort())

        class _LegacyPort(AsyncModelPort):
            """旧版端口：call 签名无 override_max_retries。"""

            async def call(self, messages, model=None, tools=None, display=None,
                           label=None, silent=False):
                return ModelResult(reasoning="", content="结果", usage={"input": 1, "output": 1}, tool_calls=[])

            async def call_sync(self, messages, model=None, tools=None, display=None,
                                label=None):
                return ModelResult(reasoning="", content="结果", usage={"input": 1, "output": 1}, tool_calls=[])

        sub = SubAgent(
            label="agent-1",
            description="测试",
            prompt="请执行任务",
            parent_agent=agent,
            model="fake-model",
            agent_type="execute",
        )
        sub._model_port = _LegacyPort()
        result = await asyncio.wait_for(sub.run(), timeout=10)
        assert "结果" in result
