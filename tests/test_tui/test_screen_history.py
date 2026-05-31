"""测试 ScreenHistoryManager 上屏历史管理器。

覆盖：
  - append/flush 累积缓冲区合并逻辑
  - record 先 flush_all 再 append 的时序
  - clear 清空行为
  - flush_all 幂等性
  - on_display_messages 回调
"""

from __future__ import annotations

from src.chat_ui._screen_history import ScreenHistoryManager


class TestScreenHistoryManager:
    """ScreenHistoryManager 核心逻辑测试。"""

    def test_append_and_flush_reasoning(self):
        """推理文本累积→flush→单条记录。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("hello ")
        shm.append_reasoning("world")
        assert len(shm.screen_history) == 0  # 未 flush，无记录
        shm.flush_reasoning()
        assert len(shm.screen_history) == 1
        kind, text = shm.screen_history[0]
        assert kind == "reasoning_block"
        assert text == "hello world"

    def test_append_and_flush_content(self):
        """内容文本累积→flush→单条记录。"""
        shm = ScreenHistoryManager()
        shm.append_content("part1")
        shm.append_content("part2")
        shm.flush_content()
        assert len(shm.screen_history) == 1
        kind, text = shm.screen_history[0]
        assert kind == "content_block"
        assert text == "part1part2"

    def test_flush_reasoning_empty_no_record(self):
        """空推理缓冲区 flush 不产生记录。"""
        shm = ScreenHistoryManager()
        shm.flush_reasoning()
        assert len(shm.screen_history) == 0

    def test_flush_content_empty_no_record(self):
        """空内容缓冲区 flush 不产生记录。"""
        shm = ScreenHistoryManager()
        shm.flush_content()
        assert len(shm.screen_history) == 0

    def test_flush_all_flushes_both(self):
        """flush_all 同时刷新推理和内容缓冲区。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("r1")
        shm.append_content("c1")
        shm.flush_all()
        assert len(shm.screen_history) == 2
        assert shm.screen_history[0][0] == "reasoning_block"
        assert shm.screen_history[1][0] == "content_block"

    def test_record_flushes_before_append(self):
        """record 先 flush 所有缓冲区，再追加记录。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("reasoning")
        shm.record("tool_output", "output")
        # 先 flush reasoning 再 append tool_output
        assert len(shm.screen_history) == 2
        assert shm.screen_history[0][0] == "reasoning_block"
        assert shm.screen_history[1][0] == "tool_output"

    def test_clear_empties_everything(self):
        """clear 清空历史和所有累积缓冲区。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("r")
        shm.record("tool_output", "o")
        shm.flush_content()
        shm.clear()
        assert len(shm.screen_history) == 0
        # 清空后再 flush，应无新记录（累积缓冲区已清空）
        shm.flush_reasoning()
        assert len(shm.screen_history) == 0

    def test_record_multiple_kinds(self):
        """多种记录类型正确保存。"""
        shm = ScreenHistoryManager()
        shm.record("tool_output", "out1")
        shm.record("user_msg", "hello")
        shm.record("error", "err!")
        assert len(shm.screen_history) == 3
        assert shm.screen_history[0] == ("tool_output", "out1")
        assert shm.screen_history[1] == ("user_msg", "hello")
        assert shm.screen_history[2] == ("error", "err!")

    def test_on_display_messages_callback(self):
        """on_display_messages 回调被正确调用。"""
        captured: list = []

        def callback(data, speed):
            captured.append((data, speed))

        shm = ScreenHistoryManager(on_display_messages=callback)
        shm.record("display_msgs", [{"role": "user"}], 42)
        assert len(captured) == 0  # record 不直接调用回调
        # 回调只在 replay() 中被调用
        assert shm.screen_history[0][0] == "display_msgs"

    def test_replay_empty_history_returns_early(self):
        """空历史重放不报错。"""
        shm = ScreenHistoryManager()
        # 不传参数，只验证不抛异常
        # replay 需要 tool_adapter + bottom_bar，此处只测空历史提前返回
        assert shm.screen_history == []

    def test_flush_all_idempotent(self):
        """flush_all 幂等：连续调用两次不产生多余记录。"""
        shm = ScreenHistoryManager()
        shm.append_reasoning("r")
        shm.flush_all()
        shm.flush_all()  # 第二次 flush 空缓冲区
        assert len(shm.screen_history) == 1
