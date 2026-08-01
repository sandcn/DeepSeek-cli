"""test_state_collection — 零覆盖模块最小测试（方向5 步骤5.5）。

覆盖 ``state/_collection.py``：ThreadSafeList 增删查/迭代/边界/并发安全。
"""

from __future__ import annotations

import threading

from src.tui.state._collection import ThreadSafeList


class TestThreadSafeList:
    """ThreadSafeList 线程安全集合最小测试。"""

    def test_append_len_getitem(self):
        """append 后 len/__getitem__ 正确。"""
        l = ThreadSafeList()
        l.append("a")
        l.append("b")
        assert len(l) == 2
        assert l[0] == "a"
        assert l[1] == "b"

    def test_initial_values(self):
        """initial 列表注入。"""
        l = ThreadSafeList(["x", "y"])
        assert len(l) == 2
        assert l[1] == "y"

    def test_clear(self):
        """clear 清空。"""
        l = ThreadSafeList(["a"])
        l.clear()
        assert len(l) == 0
        assert bool(l) is False

    def test_iter_and_bool(self):
        """迭代与布尔判定（基于 __len__）。"""
        l = ThreadSafeList(["a", "b"])
        assert list(l) == ["a", "b"]
        assert bool(l) is True
        assert bool(ThreadSafeList()) is False

    def test_extend_and_to_list(self):
        """extend 批量追加；to_list 快照。"""
        l = ThreadSafeList(["a"])
        l.extend(["b", "c"])
        assert l.to_list() == ["a", "b", "c"]
        # to_list 返回快照（外部修改不影响内部）
        snap = l.to_list()
        snap.append("z")
        assert len(l) == 3

    def test_getitem_out_of_range(self):
        """越界 __getitem__ 抛 IndexError（list 语义）。"""
        l = ThreadSafeList(["a"])
        try:
            l[5]
        except IndexError:
            pass
        else:
            raise AssertionError("越界 __getitem__ 应抛 IndexError")

    def test_repr(self):
        """repr 委托内部列表。"""
        l = ThreadSafeList(["a"])
        assert "a" in repr(l)

    def test_thread_safety_append_concurrent(self):
        """并发 append 不丢元素（锁保护）。"""
        l = ThreadSafeList()

        def worker():
            for i in range(100):
                l.append(i)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(l) == 400
