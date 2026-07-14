"""测试 _state_tree — TUIStateTree 统一状态容器。

覆盖内容：
  1. TUIStateTree 默认值
  2. SessionState 不可变（frozen dataclass）
  3. update_session 批量更新
  4. input Esc 双击检测
  5. streaming start/stop/elapsed
  6. snapshot 快照
"""

from __future__ import annotations

import time
import dataclasses

from src.tui.core.state import (
    TUIStateTree,
    UISessionState,
    InputState,
    StreamingState,
)


class TestTUIStateTreeDefaults:
    """TUIStateTree 默认值测试"""

    def test_session_defaults(self):
        tree = TUIStateTree()
        s = tree.session
        assert s.model == ""
        assert s.message_count == 0
        assert s.input_tokens == 0
        assert s.output_tokens == 0
        assert s.status_text == ""
        assert s.session_title == ""
        assert s.show_time is True
        assert s.show_tokens is True
        assert s.show_duration is False

    def test_input_defaults(self):
        tree = TUIStateTree()
        # current_model 已从 InputState 移除，统一使用 UISessionState.model
        assert tree.session.model == ""

    def test_streaming_defaults(self):
        tree = TUIStateTree()
        assert tree.streaming.active is False
        assert tree.streaming.output_tokens == 0
        assert tree.streaming.speed == 0.0
        assert tree.streaming.elapsed == 0.0


class TestTUIStateTreeSession:
    """SessionState / update_session 测试"""

    def test_update_session(self):
        tree = TUIStateTree()
        tree.update_session(model="gpt-4", message_count=5)
        assert tree.session.model == "gpt-4"
        assert tree.session.message_count == 5

    def test_update_session_preserves_other_fields(self):
        tree = TUIStateTree()
        tree.update_session(model="gpt-4", message_count=5)
        tree.update_session(status_text="thinking...")
        assert tree.session.model == "gpt-4"       # preserved
        assert tree.session.message_count == 5       # preserved
        assert tree.session.status_text == "thinking..."

    def test_session_is_frozen(self):
        tree = TUIStateTree()
        try:
            tree.session.model = "other"
            assert False, "UISessionState should be frozen"
        except dataclasses.FrozenInstanceError:
            pass

    def test_session_replace_via_dataclasses(self):
        tree = TUIStateTree()
        tree.update_session(model="gpt-4")
        tree.update_session(model="claude-3")
        assert tree.session.model == "claude-3"


class TestTUIStateTreeInput:
    """InputState / Esc 双击检测测试"""

    def test_set_current_model(self):
        tree = TUIStateTree()
        # 模型状态统一由 UISessionState.model 管理
        tree.update_session(model="gpt-4")
        assert tree.session.model == "gpt-4"

    def test_esc_first_press_returns_false(self):
        tree = TUIStateTree()
        result = tree.input.record_esc_press()
        assert result is False

    def test_esc_double_press_returns_true(self):
        tree = TUIStateTree()
        tree.input.record_esc_press()       # first press
        result = tree.input.record_esc_press()  # second (within 500ms)
        assert result is True

    def test_esc_reset(self):
        tree = TUIStateTree()
        tree.input.record_esc_press()
        tree.input.reset_esc_state()
        result = tree.input.record_esc_press()  # should be first press again
        assert result is False


class TestTUIStateTreeStreaming:
    """StreamingState / start/stop/elapsed 测试"""

    def test_start_streaming(self):
        tree = TUIStateTree()
        tree.streaming.start()
        assert tree.streaming.active is True
        assert tree.streaming.start_time > 0

    def test_start_streaming_idempotent(self):
        tree = TUIStateTree()
        tree.streaming.start()
        start_time = tree.streaming.start_time
        tree.streaming.start()  # already active
        assert tree.streaming.start_time == start_time  # not reset
        assert tree.streaming.output_tokens == 0        # not reset

    def test_stop_streaming(self):
        tree = TUIStateTree()
        tree.streaming.start()
        tree.streaming.stop()
        assert tree.streaming.active is False

    def test_elapsed_when_not_active(self):
        tree = TUIStateTree()
        assert tree.streaming.elapsed == 0.0

    def test_elapsed_when_active(self):
        tree = TUIStateTree()
        tree.streaming.start()
        time.sleep(0.01)
        assert tree.streaming.elapsed > 0.0

    def test_reset_streaming(self):
        tree = TUIStateTree()
        tree.streaming.start()
        tree.streaming.output_tokens = 100
        tree.streaming.speed = 15.5
        tree.streaming.stop()
        assert tree.streaming.active is False
        assert tree.streaming.output_tokens == 0
        assert tree.streaming.speed == 0.0

    def test_update_tokens_and_speed(self):
        tree = TUIStateTree()
        tree.streaming.output_tokens = 250
        tree.streaming.speed = 12.3
        assert tree.streaming.output_tokens == 250
        assert tree.streaming.speed == 12.3


# snapshot() / reset() 已于 v10 重构移除（未使用的预留 API）
