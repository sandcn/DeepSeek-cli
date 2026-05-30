"""Tests for src/core/message_queue.py — MessageQueue 和 Message（异步版）"""

import asyncio
import time

import pytest

from src.core.message_queue import Message, MessageQueue


# ═══════════════════════════════════════════════════════════════
# Message 测试
# ═══════════════════════════════════════════════════════════════

class TestMessage:
    """Message dataclass 基础测试"""

    def test_create_message(self):
        msg = Message(content="hello", timestamp=1.0, id=42)
        assert msg.content == "hello"
        assert msg.timestamp == 1.0
        assert msg.id == 42

    def test_message_id_auto_increment(self):
        """连续创建的 Message id 应自增"""
        msg1 = Message(content="first")
        msg2 = Message(content="second")
        assert msg2.id == msg1.id + 1

    def test_message_default_timestamp(self):
        """不传 timestamp 时使用当前时间"""
        before = time.time()
        msg = Message(content="test")
        after = time.time()
        assert before <= msg.timestamp <= after

    def test_message_content_types(self):
        """Message.content 支持字符串和其他对象"""
        msg_str = Message(content="text")
        assert msg_str.content == "text"
        msg_dict = Message(content={"key": "value"})
        assert msg_dict.content == {"key": "value"}
        msg_int = Message(content=42)
        assert msg_int.content == 42


# ═══════════════════════════════════════════════════════════════
# MessageQueue 测试（异步）
# ═══════════════════════════════════════════════════════════════

class TestMessageQueue:
    """MessageQueue 功能测试（asyncio 版）"""

    # ── put 和 get ──────────────────────────────────────────────

    async def test_put_returns_message(self):
        q = MessageQueue()
        msg = await q.put("hello")
        assert isinstance(msg, Message)
        assert msg.content == "hello"

    async def test_put_and_get(self):
        """放一条取一条"""
        q = MessageQueue()
        put_msg = await q.put("world")
        got_msg = await q.get(timeout=1)
        assert got_msg is put_msg
        assert got_msg.content == "world"

    async def test_put_multiple_get_fifo(self):
        """先进先出顺序"""
        q = MessageQueue()
        await q.put("first")
        await q.put("second")
        await q.put("third")
        assert (await q.get(timeout=1)).content == "first"
        assert (await q.get(timeout=1)).content == "second"
        assert (await q.get(timeout=1)).content == "third"

    # ── 非阻塞 get(timeout=0) ──────────────────────────────────

    async def test_get_nonblocking_empty_returns_none(self):
        q = MessageQueue()
        assert await q.get(timeout=0) is None

    async def test_get_nonblocking_with_item(self):
        q = MessageQueue()
        await q.put("item")
        msg = await q.get(timeout=0)
        assert msg is not None
        assert msg.content == "item"

    # ── 阻塞 get（超时返回 None） ──────────────────────────────

    async def test_get_timeout_returns_none(self):
        """空队列上 get(timeout=0.1) 应超时返回 None"""
        q = MessageQueue()
        result = await q.get(timeout=0.1)
        assert result is None

    async def test_get_with_timeout_and_item_arrives(self):
        """等待期间消息到达，应正确返回"""
        q = MessageQueue()

        async def delayed_put():
            await asyncio.sleep(0.05)
            await q.put("delayed")

        task = asyncio.create_task(delayed_put())
        msg = await q.get(timeout=2)
        assert msg is not None
        assert msg.content == "delayed"
        await task

    # ── 无限等待 get(timeout=None) ─────────────────────────────

    async def test_get_infinite_wait(self):
        """timeout=None 应无限等待直到有消息"""
        q = MessageQueue()

        async def delayed_put():
            await asyncio.sleep(0.05)
            await q.put("finally")

        task = asyncio.create_task(delayed_put())
        msg = await q.get(timeout=None)
        assert msg is not None
        assert msg.content == "finally"
        await task

    # ── qsize ──────────────────────────────────────────────────

    async def test_qsize_empty(self):
        q = MessageQueue()
        assert q.qsize == 0

    async def test_qsize_after_put(self):
        q = MessageQueue()
        await q.put("a")
        assert q.qsize == 1
        await q.put("b")
        assert q.qsize == 2

    async def test_qsize_after_get(self):
        q = MessageQueue()
        await q.put("a")
        await q.put("b")
        await q.get(timeout=1)
        assert q.qsize == 1
        await q.get(timeout=1)
        assert q.qsize == 0

    # ── async_consume ──────────────────────────────────────────

    async def test_async_consume_receives_messages(self):
        """async_consume 应串行收到每条消息"""
        q = MessageQueue()
        received = []

        async def callback(msg: Message):
            received.append(msg.content)

        consumer = asyncio.create_task(q.async_consume(callback, poll_interval=0.05))

        await q.put("msg1")
        await q.put("msg2")
        await asyncio.sleep(0.3)

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

        assert received == ["msg1", "msg2"]

    async def test_async_consume_callback_error_does_not_crash(self):
        """消费者回调抛出异常不应终止 async_consume"""
        q = MessageQueue()
        received = []

        async def faulty(msg):
            if msg.content == "crash":
                raise ValueError("模拟异常")
            received.append(msg.content)

        consumer = asyncio.create_task(q.async_consume(faulty, poll_interval=0.05))

        await q.put("crash")
        await q.put("survive")
        await asyncio.sleep(0.3)

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

        assert "survive" in received

    async def test_async_consume_stop_via_sentinel(self):
        """放入哨兵消息应优雅停止 async_consume"""
        q = MessageQueue()
        received = []

        async def callback(msg: Message):
            received.append(msg.content)

        consumer = asyncio.create_task(q.async_consume(callback, poll_interval=0.05))

        await q.put("before")
        await asyncio.sleep(0.1)
        await q.stop()  # 放入哨兵消息
        await consumer  # 等待消费者自然退出（不抛 CancelledError）

        assert received == ["before"]

    async def test_async_consume_cancel_re_enqueues_message(self):
        """取消 async_consume 时正在处理的消息应放回队列"""
        q = MessageQueue()
        blocked = asyncio.Event()

        async def blocking_callback(msg):
            blocked.set()
            # 永远不返回，让 consumer 被取消
            await asyncio.Event().wait()

        consumer = asyncio.create_task(q.async_consume(blocking_callback, poll_interval=0.05))

        await q.put("stuck")
        await blocked.wait()  # 等待回调开始处理
        await asyncio.sleep(0.05)
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

        # 消息应被重新入队
        re_queued = await q.get(timeout=0.1)
        assert re_queued is not None
        assert re_queued.content == "stuck"

    # ── is_running ─────────────────────────────────────────────

    async def test_is_running_default(self):
        q = MessageQueue()
        assert q.is_running is True

    async def test_stop_clears_is_running(self):
        q = MessageQueue()
        await q.stop()
        assert q.is_running is False

    # ── 高并发 ─────────────────────────────────────────────────

    async def test_concurrent_put_and_get(self):
        """多个生产者和消费者同时操作"""
        q = MessageQueue()
        results = []

        async def producer(start, count):
            for i in range(start, start + count):
                await q.put(f"msg_{i}")
                await asyncio.sleep(0.01)

        async def consumer(n):
            for _ in range(n):
                msg = await q.get(timeout=5)
                if msg is not None:
                    results.append(msg.content)

        producers = [asyncio.create_task(producer(0, 5)),
                     asyncio.create_task(producer(5, 5))]
        consumers = [asyncio.create_task(consumer(5)),
                     asyncio.create_task(consumer(5))]

        await asyncio.gather(*producers)
        await asyncio.gather(*consumers)

        assert len(results) == 10
        assert sorted(results) == [f"msg_{i}" for i in range(10)]
