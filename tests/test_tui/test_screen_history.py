"""测试 ScreenHistoryManager 上屏历史管理器（已屏蔽 No-op 版本）。

覆盖：
  - 所有记录方法 No-op 返回空历史
  - clear No-op 行为
  - on_display_messages 回调传递
  - replay No-op 不抛异常
"""

from __future__ import annotations

from src.chat_ui._screen_history import ScreenHistoryManager


class TestScreenHistoryManager:
    """ScreenHistoryManager No-op 行为测试。"""

    def test_screen_history_always_empty(self):
        """screen_history 始终返回空列表（所有记录方法均为 No-op）。"""
        shm = ScreenHistoryManager()
        assert shm.screen_history == []
        shm.append_reasoning("hello ")
        shm.append_reasoning("world")
        assert shm.screen_history == []
        shm.flush_reasoning()
        assert shm.screen_history == []

    def test_record_noop(self):
        """record No-op，历史始终为空。"""
        shm = ScreenHistoryManager()
        shm.record("tool_output", "out1")
        shm.record("user_msg", "hello")
        shm.record("error", "err!")
        assert shm.screen_history == []

    def test_clear_noop(self):
        """clear No-op，历史始终为空。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("r")
        shm.record("tool_output", "o")
        shm.clear()
        assert shm.screen_history == []

    def test_flush_all_noop(self):
        """flush_all No-op，历史始终为空。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("r1")
        shm.append_content("c1")
        shm.flush_all()
        assert shm.screen_history == []
        shm.flush_all()  # 幂等
        assert shm.screen_history == []

    def test_on_display_messages_passthrough(self):
        """on_display_messages 回调可正常读写（不做 replay 分发）。"""
        captured: list = []

        def callback(data, speed):
            captured.append((data, speed))

        shm = ScreenHistoryManager(on_display_messages=callback)
        # record No-op 不记录，但回调对象仍可访问
        assert shm.on_display_messages is callback
        assert shm.screen_history == []

    def test_replay_noop_does_not_raise(self):
        """replay No-op，不抛异常（无需传参）。"""
        shm = ScreenHistoryManager()
        shm.replay(None, None)  # type: ignore[arg-type]  # No-op 不使用参数
        assert True  # 不抛异常即通过

    def test_flush_empty_noop(self):
        """空缓冲区的 flush 方法 No-op。"""
        shm = ScreenHistoryManager()
        shm.flush_reasoning()
        shm.flush_content()
        shm.flush_all()
        assert shm.screen_history == []
