#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量消息统计缓存

MessageStatsCache 维护与 messages 列表对应的每消息字符/token 数缓存，
支持增量更新（append/insert/remove/replace），避免全量遍历。

从 context_selector.py 提取为独立模块，供 context_selector 和 context_manager 共用。
"""

from dataclasses import field
from src._compat import dataclass
from ....api.tokens import estimate_tokens


@dataclass(slots=True)
class MessageStatsCache:
    """增量消息统计缓存。

    维护与 messages 列表对应的每消息字符/token 数缓存，
    支持增量更新（append/insert/remove/replace），避免全量遍历。

    使用方式：
        cache = MessageStatsCache()
        cache.resync(messages)          # 首次全量同步
        cache.on_append(msg)            # 追加消息时
        cache.on_insert(idx, msg)       # 插入消息时
        cache.on_remove(indices)        # 删除消息时（原始索引列表）
        cache.on_replace(idx, msg)      # 替换消息时
    """

    _chars: int = 0
    _tokens: int = 0
    _per_msg: list = field(default_factory=list)
    _valid: bool = False

    # ── 全量同步 ──

    def resync(self, messages):
        """全量同步：遍历所有消息重建缓存。"""
        from ...context_selector import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        total_chars = 0
        total_tokens = 0
        per_msg = []
        for m in messages:
            text = message_to_text(m)
            c = len(text)
            t = estimate_tokens(text)
            total_chars += c
            total_tokens += t
            per_msg.append((c, t))
        self._chars = total_chars
        self._tokens = total_tokens
        self._per_msg = per_msg
        self._valid = True

    def invalidate(self):
        """标记缓存无效，下次访问将自动重建。"""
        self._valid = False

    # ── 增量操作 ──

    def on_append(self, msg):
        """追加一条消息的统计。"""
        from ...context_selector import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        text = message_to_text(msg)
        c, t = len(text), estimate_tokens(text)
        self._chars += c
        self._tokens += t
        self._per_msg.append((c, t))

    def on_insert(self, idx, msg):
        """在指定索引插入一条消息的统计。"""
        from ...context_selector import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        text = message_to_text(msg)
        c, t = len(text), estimate_tokens(text)
        self._per_msg.insert(idx, (c, t))
        self._chars += c
        self._tokens += t

    def on_remove(self, indices):
        """删除指定索引的消息统计（索引为原始位置，从高到低处理）。"""
        if not indices:
            return
        chars_removed = 0
        tokens_removed = 0
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self._per_msg):
                c, t = self._per_msg.pop(idx)
                chars_removed += c
                tokens_removed += t
        self._chars -= chars_removed
        self._tokens -= tokens_removed

    def on_replace(self, idx, msg):
        """替换指定索引的消息统计（保留位置，更新值）。"""
        from ...context_selector import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        text = message_to_text(msg)
        c, t = len(text), estimate_tokens(text)
        old_c, old_t = self._per_msg[idx]
        self._per_msg[idx] = (c, t)
        self._chars += c - old_c
        self._tokens += t - old_t

    # ── 查询 ──

    @property
    def total_chars(self) -> int:
        return self._chars

    @property
    def total_tokens(self) -> int:
        return self._tokens

    @property
    def is_valid(self) -> bool:
        return self._valid

    def get_per_msg(self, idx: int):
        """获取指定索引的消息统计 (chars, tokens)。"""
        if 0 <= idx < len(self._per_msg):
            return self._per_msg[idx]
        return (0, 0)

    def __len__(self) -> int:
        return len(self._per_msg)
