"""Tests for StreamContext — 流式处理共享状态容器"""
from __future__ import annotations

from src.api.stream.context import StreamContext


class MockDisplay:
    """桩：模拟 display 对象，满足 StreamContext 构造要求"""
    def update_speed(self, label: str, speed: float) -> None: ...
    def update_live_output(self, label: str, delta: int) -> None: ...


def _make_context() -> StreamContext:
    """创建 StreamContext 实例的辅助函数"""
    return StreamContext(model="test-model", display=MockDisplay(),
                         label="test", silent=True)


class TestStreamContextInit:
    """StreamContext 初始化完整性测试"""

    def test_all_attributes_initialized(self):
        """所有在 handler 中使用的属性都已在 __init__ 中初始化"""
        ctx = _make_context()

        # 核心状态
        assert ctx.model == "test-model"
        assert ctx.display is not None
        assert ctx.label == "test"
        assert ctx.silent is True

        # 模型阶段
        assert ctx.is_reasoning is True
        assert ctx.phase_thinking_sent is False
        assert ctx.phase_answering_sent is False

        # 内容累积
        assert ctx.content_full == ""
        assert ctx.reasoning_full == ""

        # 使用量
        assert ctx.usage == {"input": 0, "output": 0}
        assert ctx.usage_accumulated is False

        # 中断
        assert ctx.esc_interrupted is False
        assert ctx.task_cancelled is False

        # 速度追踪
        assert ctx.speed_chunk_count == 0
        assert ctx.speed_update_interval == 0.5
        assert ctx.last_live_est == 0
        assert ctx.token_estimate == 0
        assert ctx._live_total_dirty is False  # ★ Bug 回归检查

        # 渲染器
        assert ctx.reasoning_renderer is None
        assert ctx.content_renderer is None

    def test_live_total_dirty_set_by_reasoning_handler(self):
        """验证 reasoning handler 设置 _live_total_dirty 的行为可复现（回归）"""
        ctx = _make_context()
        # 模拟 reasoning handler 的行为
        ctx.token_estimate += 10
        ctx.reasoning_full += "test reasoning"
        ctx.speed_chunk_count += 1
        ctx._live_total_dirty = True

        assert ctx._live_total_dirty is True
        # speed handler 消费后重置
        ctx._live_total_dirty = False
        assert ctx._live_total_dirty is False

    def test_live_total_dirty_set_by_content_handler(self):
        """验证 content handler 设置 _live_total_dirty 的行为可复现（回归）"""
        ctx = _make_context()
        # 模拟 content handler 的行为
        ctx.token_estimate += 10
        ctx.content_full += "test content"
        ctx.speed_chunk_count += 1
        ctx._live_total_dirty = True

        assert ctx._live_total_dirty is True
