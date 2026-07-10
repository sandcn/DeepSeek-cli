"""测试消息编辑工具函数 truncate_messages / clear_all_messages

覆盖内容：
  1. truncate_messages 基本功能
  2. 边界条件（空列表、纯 system、keep_from_start=0 等）
  3. 多 system 消息
  4. clear_all_messages 清空重建
"""

import pytest
from src.core.message_edit import truncate_messages, clear_all_messages


# ===============================================================
# 1. truncate_messages 基本功能
# ===============================================================

class TestTruncateBasic:
    """保留 system 消息 + 指定数量的非 system 消息"""

    # ── 辅助构造 ──────────────────────────────

    @staticmethod
    def _make(roles: list[str]) -> list[dict]:
        return [{"role": r, "content": f"{r}_{i}"} for i, r in enumerate(roles)]

    # ── 测试用例 ──────────────────────────────

    def test_keep_system_and_non_system(self):
        """保留 system 消息 + keep_from_start 条非 system 消息"""
        msgs = self._make(["system", "user", "assistant", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=2)
        # 保留: system + user0 + assistant0
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
        # 删除: user1 + assistant1
        assert len(deleted) == 2
        assert deleted[0]["role"] == "user"
        assert deleted[1]["role"] == "assistant"

    def test_return_deleted_messages(self):
        """返回被删除的消息列表"""
        msgs = self._make(["system", "user", "assistant", "user"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        # system_end=1, 从索引1开始数非system：user_1(cnt=1)→保留, assistant_2(cnt=2>1)→截断
        assert deleted == [
            {"role": "assistant", "content": "assistant_2"},
            {"role": "user", "content": "user_3"},
        ]

    def test_messages_modified_in_place(self):
        """原始列表被原地修改"""
        original = self._make(["system", "user", "assistant", "user"])
        original_copy = list(original)
        truncate_messages(original, keep_from_start=1)
        assert original != original_copy
        assert len(original) == 2  # system + user0
        assert len(original_copy) == 4

    def test_exactly_keep_from_start_messages(self):
        """非 system 消息数量恰好等于 keep_from_start，不删除"""
        msgs = self._make(["system", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=2)
        assert len(msgs) == 3
        assert deleted == []

    def test_keep_only_non_system_after_system(self):
        """保留 system 之后的前 N 条非 system 消息，删除后续"""
        msgs = self._make(["system", "user", "assistant", "user", "assistant", "user"])
        deleted = truncate_messages(msgs, keep_from_start=3)
        assert len(msgs) == 1 + 3  # system + 3 non-system
        assert len(deleted) == 2

    def test_no_system_message_at_all(self):
        """没有 system 消息时，从第 0 条开始计数"""
        msgs = self._make(["user", "assistant", "user"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert len(deleted) == 2
        assert deleted[0]["role"] == "assistant"
        assert deleted[1]["role"] == "user"


# ===============================================================
# 2. 边界条件
# ===============================================================

class TestTruncateEdgeCases:
    """空列表、纯 system、keep_from_start=0、超大 keep_from_start 等"""

    @staticmethod
    def _make(roles: list[str]) -> list[dict]:
        return [{"role": r, "content": f"{r}_{i}"} for i, r in enumerate(roles)]

    def test_empty_list(self):
        """空列表：返回空列表，messages 不变"""
        msgs: list[dict] = []
        deleted = truncate_messages(msgs, keep_from_start=1)
        assert deleted == []
        assert msgs == []

    def test_only_system_messages(self):
        """只有 system 消息：不删除任何消息"""
        msgs = self._make(["system", "system"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        assert deleted == []
        assert len(msgs) == 2

    def test_keep_from_start_zero(self):
        """keep_from_start=0：只保留 system 消息，删除所有非 system 消息"""
        msgs = self._make(["system", "user", "assistant", "user"])
        deleted = truncate_messages(msgs, keep_from_start=0)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert len(deleted) == 3

    def test_keep_from_start_zero_no_system(self):
        """keep_from_start=0 且没有 system：删除所有消息"""
        msgs = self._make(["user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=0)
        assert msgs == []
        assert len(deleted) == 2

    def test_keep_from_start_larger_than_non_system(self):
        """keep_from_start 大于非 system 消息总数：不删除任何非 system 消息"""
        msgs = self._make(["system", "user"])
        deleted = truncate_messages(msgs, keep_from_start=10)
        assert len(msgs) == 2
        assert deleted == []

    def test_keep_from_start_larger_no_system(self):
        """无 system 且 keep_from_start 大于总数：不删除任何消息"""
        msgs = self._make(["user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=10)
        assert len(msgs) == 2
        assert deleted == []

    def test_single_message_system(self):
        """仅 1 条 system 消息：不删除"""
        msgs = self._make(["system"])
        deleted = truncate_messages(msgs, keep_from_start=5)
        assert len(msgs) == 1
        assert deleted == []

    def test_single_message_non_system(self):
        """仅 1 条非 system 消息，keep_from_start=1：保留"""
        msgs = self._make(["user"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        assert len(msgs) == 1
        assert deleted == []

    def test_single_message_non_system_keep_zero(self):
        """仅 1 条非 system 消息，keep_from_start=0：删除"""
        msgs = self._make(["user"])
        deleted = truncate_messages(msgs, keep_from_start=0)
        assert msgs == []
        assert deleted == [{"role": "user", "content": "user_0"}]

    def test_negative_keep_from_start(self):
        """keep_from_start 为负数：行为如同 0（仅保留 system）"""
        msgs = self._make(["system", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=-1)
        # keep_from_start=-1 → non_system_count=1, 1 > -1 → True → truncate at index 1
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert len(deleted) == 2


# ===============================================================
# 3. 多 system 消息
# ===============================================================

class TestTruncateMultiSystem:
    """多个 system 消息在开头时全部保留"""

    @staticmethod
    def _make(roles: list[str]) -> list[dict]:
        return [{"role": r, "content": f"{r}_{i}"} for i, r in enumerate(roles)]

    def test_multiple_system_at_start(self):
        """多个 system 消息都在前面时全部保留"""
        msgs = self._make(["system", "system", "user", "assistant", "user"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        # 保留: system + system + user0
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "system"
        assert msgs[2]["role"] == "user"
        # 删除: assistant0 + user1
        assert len(deleted) == 2

    def test_many_system_messages_all_preserved(self):
        """大量 system 消息（3个）全部保留"""
        msgs = self._make(["system", "system", "system", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        assert len(msgs) == 4  # 3 system + 1 user
        assert all(m["role"] == "system" for m in msgs[:3])
        assert msgs[3]["role"] == "user"
        assert len(deleted) == 1

    def test_system_in_middle_does_not_extend_system_end(self):
        """system 消息不在开头不影响 system_end 计算，且不计入非 system 计数"""
        msgs = self._make(["user", "system", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        # system_end=0（首条为 user→break），从索引0开始计数非 system
        # i=0 user→cnt=1(≤1保留); i=1 system→跳过(不占槽位);
        # i=2 user→cnt=2(>1)→截断
        # 保留: [user_0, system_1], 删除: [user_2, assistant_3]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "system"
        assert len(deleted) == 2
        assert deleted[0]["role"] == "user"
        assert deleted[1]["role"] == "assistant"

    def test_system_then_user_then_system(self):
        """system 开头后跟非 system 后又有 system：后面的 system 被视为非 system 保留区域内的普通消息"""
        msgs = self._make(["system", "user", "system", "user"])
        deleted = truncate_messages(msgs, keep_from_start=2)
        # system_end=1
        # range(1,4): i=1 user→cnt=1, i=2 system→skip, i=3 user→cnt=2, 2>2? no
        # truncate_idx stays at 4
        # 保留全部 4 条
        assert len(msgs) == 4
        assert deleted == []

    def test_system_then_user_then_system_truncated(self):
        """system 开头后跟非 system 再出现 system，截断位置正确"""
        msgs = self._make(["system", "user", "system", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        # system_end=1
        # range(1,5): i=1 user→cnt=1, 1>1? no; i=2 system→skip; i=3 user→cnt=2, 2>1? yes→truncate_idx=3
        # 保留: system + user0 + system(中间)
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "system"
        assert len(deleted) == 2
        assert deleted[0]["role"] == "user"  # user1 at idx 3
        assert deleted[1]["role"] == "assistant"

    def test_all_system_and_then_non_system(self):
        """全是 system 后接非 system：system 全部保留"""
        msgs = self._make(["system", "system", "system", "user", "assistant"])
        deleted = truncate_messages(msgs, keep_from_start=1)
        assert len(msgs) == 4  # 3 system + 1 user
        assert all(m["role"] == "system" for m in msgs[:3])
        assert msgs[3]["role"] == "user"
        assert len(deleted) == 1


# ===============================================================
# 4. clear_all_messages
# ===============================================================

class TestClearAllMessages:
    """清空所有消息后重建 system prompt"""

    def test_clear_and_rebuild(self):
        """清空后调用 build_system_prompt 重建 system 消息"""
        messages = [
            {"role": "system", "content": "old prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        def build():
            yield "new system part 1"
            yield "new system part 2"

        clear_all_messages(messages, build)

        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "new system part 1"}
        assert messages[1] == {"role": "system", "content": "new system part 2"}

    def test_build_system_prompt_called(self):
        """验证 build_system_prompt 被调用"""
        messages = [
            {"role": "user", "content": "hello"},
        ]
        call_count = 0

        def build():
            nonlocal call_count
            call_count += 1
            yield "system prompt"
            yield ""

        clear_all_messages(messages, build)
        assert call_count == 1

    def test_clear_multiple_messages(self):
        """清空多条消息后重建"""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]

        def build():
            yield "fresh prompt"

        clear_all_messages(messages, build)
        assert len(messages) == 1
        assert messages[0] == {"role": "system", "content": "fresh prompt"}

    def test_build_returns_empty(self):
        """build_system_prompt 返回空 iterable：消息列表变空"""
        messages = [{"role": "user", "content": "hello"}]

        def build():
            return []
            yield  # make it a generator

        clear_all_messages(messages, build)
        assert messages == []

    def test_build_returns_multiple_parts(self):
        """build_system_prompt 返回多个 part，全部插入"""
        messages = [{"role": "user", "content": "hello"}]

        def build():
            yield "part1"
            yield "part2"
            yield "part3"

        clear_all_messages(messages, build)
        assert len(messages) == 3
        for i, m in enumerate(messages):
            assert m["role"] == "system"
            assert m["content"] == f"part{i + 1}"

    def test_empty_messages_with_build(self):
        """原本没有消息时，调用 clear_all_messages 后重建"""
        messages: list[dict] = []

        def build():
            yield "prompt from scratch"

        clear_all_messages(messages, build)
        assert len(messages) == 1
        assert messages[0] == {"role": "system", "content": "prompt from scratch"}

    def test_original_messages_fully_replaced(self):
        """原列表内容被完全替换，非追加"""
        messages = [
            {"role": "system", "content": "old"},
            {"role": "user", "content": "keep me?"},
        ]

        def build():
            yield "brand new"

        clear_all_messages(messages, build)
        assert len(messages) == 1
        assert messages[0]["content"] == "brand new"
        # 确认旧内容不存在
        assert all(m["role"] == "system" for m in messages)

    def test_build_system_prompt_with_lambda(self):
        """使用 lambda 作为 build_system_prompt"""
        messages = [{"role": "user", "content": "hello"}]
        clear_all_messages(messages, lambda: ["lambda prompt"])
        assert len(messages) == 1
        assert messages[0] == {"role": "system", "content": "lambda prompt"}
