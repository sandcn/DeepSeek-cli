"""测试模块 B — SubAgent 网络错误重试逻辑

测试 SubAgent.run() 中网络错误检测和重试行为。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.core.subagent import SubAgent
from src.core.exceptions import is_network_error


# ── 辅助函数 ─────────────────────────────────────────────

def _make_mock_parent():
    """创建一个最小 mock 父 Agent。"""
    parent = MagicMock()
    parent.model = "test-model"
    parent.get_tool_registry.return_value = MagicMock()
    parent.get_tool_registry.return_value.get_schemas.return_value = []
    parent.get_prompt_builder_port.return_value = MagicMock()
    parent.get_prompt_builder_port.return_value.build_execute_agent_system_prompt.return_value = []
    parent.get_prompt_builder_port.return_value.build_subagent_prompt.return_value = []
    parent._async_model_port = None
    parent._event_port = MagicMock()
    return parent


def _make_subagent(parent=None):
    """创建一个最小 SubAgent 实例（不真实调用模型）。"""
    if parent is None:
        parent = _make_mock_parent()
    sa = SubAgent(
        label="test-agent",
        description="Test SubAgent",
        prompt="test prompt",
        parent_agent=parent,
        agent_type="execute",
    )
    return sa


# ── 测试类 ───────────────────────────────────────────────

class TestSubAgentNetworkRetry:
    """测试 SubAgent.run() 网络错误重试逻辑"""

    @pytest.mark.asyncio
    async def test_normal_call_success(self):
        """正常调用不触发重试，直接返回 content"""
        sa = _make_subagent()
        normal_content = "这是正常的回复内容"

        # Mock _call_model_impl 直接返回正常结果
        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ("", normal_content, {"input": 0, "output": 0}, [])

            result = await sa.run()

            assert result == normal_content
            assert mock_call.call_count == 1  # 只调用一次

    @pytest.mark.asyncio
    async def test_network_error_content_retry_then_success(self):
        """第1次返回网络错误内容，第2次返回正常 → 最终返回正常"""
        sa = _make_subagent()
        normal_content = "重试后的正常回复"
        network_error_content = "抱歉，API 调用出错: 连接超时"

        call_count = 0

        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ("", network_error_content, {"input": 0, "output": 0}, [])
            return ("", normal_content, {"input": 0, "output": 0}, [])

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.side_effect = mock_call

            result = await sa.run()

            assert result == normal_content
            assert mock_fn.call_count == 2  # 第1次失败 + 第2次重试成功

        # 验证重试消息已追加到 messages
        retry_msg = sa.messages[-1]
        assert retry_msg["role"] == "user"
        assert "【继续】网络错误已恢复" in retry_msg["content"]

    @pytest.mark.asyncio
    async def test_network_error_content_all_retries_exhausted(self):
        """连续3次网络错误内容 → 重试用尽，返回错误"""
        sa = _make_subagent()
        error_content = "连接错误: 网络不可达"

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = ("", error_content, {"input": 0, "output": 0}, [])

            result = await sa.run()

            assert "错误:" in result
            assert error_content in result
            assert mock_fn.call_count == 3  # 重试3次用尽

    @pytest.mark.asyncio
    async def test_network_error_exception_retry_then_success(self):
        """第1次抛出网络异常，第2次正常 → 最终返回正常"""
        sa = _make_subagent()
        normal_content = "重试后的正常回复"

        call_count = 0

        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("连接被拒绝")
            return ("", normal_content, {"input": 0, "output": 0}, [])

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.side_effect = mock_call

            result = await sa.run()

            assert result == normal_content
            assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_non_network_exception_no_retry(self):
        """非网络异常（ValueError）不重试，直接返回错误"""
        sa = _make_subagent()

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.side_effect = ValueError("参数错误")

            result = await sa.run()

            assert "错误:" in result
            assert "ValueError" in result or "参数错误" in result
            assert mock_fn.call_count == 1  # 不重试

    @pytest.mark.asyncio
    async def test_normal_content_no_retry(self):
        """正常文本内容不触发重试"""
        sa = _make_subagent()

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = ("", "今天天气很好", {"input": 0, "output": 0}, [])

            result = await sa.run()

            assert result == "今天天气很好"
            assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_message_appended_correctly(self):
        """重试消息格式验证"""
        sa = _make_subagent()
        error_content = "连接错误"

        call_count = 0

        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ("", error_content, {"input": 0, "output": 0}, [])
            return ("", "成功回复", {"input": 0, "output": 0}, [])

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.side_effect = mock_call

            await sa.run()

            # 检查重试消息格式
            retry_msg = sa.messages[-1]
            assert retry_msg["role"] == "user"
            assert "【继续】" in retry_msg["content"]

    @pytest.mark.asyncio
    async def test_cancelled_error_propagated(self):
        """CancelledError 透传不消化"""
        sa = _make_subagent()

        with patch.object(sa, '_call_model_impl', new_callable=AsyncMock) as mock_fn:
            mock_fn.side_effect = asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await sa.run()

            assert mock_fn.call_count == 1
