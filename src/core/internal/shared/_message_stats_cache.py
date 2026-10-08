#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量消息统计缓存

MessageStatsCache 维护与 messages 列表对应的每消息字符/token 数缓存，
支持增量更新（append/insert/remove/replace），避免全量遍历。

从 context_selector.py 提取为独立模块，供 context_selector 和 context_manager 共用。

★ 2026-10 同步检测增强（用户反馈「main 上下文百分比统计不准」之一）：
仅比对**长度**无法发现「条数不变、内容被替换」的变更（如
``LoggedMessageList.__setitem__`` / 会话投影 REPLACE / 插件原地替换消息），
缓存会滞留旧统计 → 百分比不更新。新增 ``is_synced(messages)``：长度 + 逐条
消息对象身份（``id``）双重校验，替换后自动判定失步并触发 ``resync``。
"""

from dataclasses import field
from src._compat import dataclass
from ...tokens import estimate_tokens


@dataclass(slots=True)
class MessageStatsCache:
    """增量消息统计缓存。

    维护与 messages 列表对应的每消息字符/token 数缓存，
    支持增量更新（append/insert/remove/replace），避免全量遍历。

    使用方式：
        cache = MessageStatsCache()
        cache.resync(messages)          # 首次全量同步
        cache.is_synced(messages)       # 校验缓存是否仍与列表一致
        cache.on_append(msg)            # 追加消息时
        cache.on_insert(idx, msg)       # 插入消息时
        cache.on_remove(indices)        # 删除消息时（原始索引列表）
        cache.on_replace(idx, msg)      # 替换消息时
    """

    _chars: int = 0
    _tokens: int = 0
    _per_msg: list = field(default_factory=list)
    #: 与 ``_per_msg`` 一一对应的消息对象身份列表（``id(msg)``）——
    #: 用于 ``is_synced`` 检测「条数不变、对象被替换」的隐性变更。
    _msg_ids: list = field(default_factory=list)
    _valid: bool = False

    # ── 全量同步 ──

    def resync(self, messages):
        """全量同步：遍历所有消息重建缓存。"""
        from ._message_text import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        total_chars = 0
        total_tokens = 0
        per_msg = []
        msg_ids = []
        for m in messages:
            text = message_to_text(m)
            c = len(text)
            t = estimate_tokens(text)
            total_chars += c
            total_tokens += t
            per_msg.append((c, t))
            msg_ids.append(id(m))
        self._chars = total_chars
        self._tokens = total_tokens
        self._per_msg = per_msg
        self._msg_ids = msg_ids
        self._valid = True

    def invalidate(self):
        """标记缓存无效，下次访问将自动重建。"""
        self._valid = False

    # ── 同步校验 ──

    def is_synced(self, messages) -> bool:
        """缓存是否与 ``messages`` 一致（长度 + 逐条对象身份）。

        修复前调用方只比对 ``len(cache) != len(messages)``：条数相同但
        消息对象被整体替换（``messages[i] = new_msg``）时判定为「已同步」，
        统计滞留旧值。本方法额外校验每条消息对象身份，替换即判定失步。

        成本：O(n) 只读比较（无分配），仅在刷新上下文使用率时调用，
        与既有 ``_image_fp`` 扫描同数量级。
        """
        if not self._valid:
            return False
        n = len(messages)
        per_msg = self._per_msg
        msg_ids = self._msg_ids
        if len(per_msg) != n or len(msg_ids) != n:
            return False
        for idx in range(n):
            if id(messages[idx]) != msg_ids[idx]:
                return False
        return True

    # ── 增量操作 ──

    def on_append(self, msg):
        """追加一条消息的统计。"""
        from ._message_text import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        text = message_to_text(msg)
        c, t = len(text), estimate_tokens(text)
        self._chars += c
        self._tokens += t
        self._per_msg.append((c, t))
        self._msg_ids.append(id(msg))

    def on_insert(self, idx, msg):
        """在指定索引插入一条消息的统计。"""
        from ._message_text import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        text = message_to_text(msg)
        c, t = len(text), estimate_tokens(text)
        self._per_msg.insert(idx, (c, t))
        self._msg_ids.insert(idx, id(msg))
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
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self._msg_ids):
                self._msg_ids.pop(idx)
        self._chars -= chars_removed
        self._tokens -= tokens_removed

    def on_replace(self, idx, msg):
        """替换指定索引的消息统计（保留位置，更新值）。"""
        from ._message_text import message_to_text  # noqa: PLC0415 — 懒加载避免循环导入

        text = message_to_text(msg)
        c, t = len(text), estimate_tokens(text)
        old_c, old_t = self._per_msg[idx]
        self._per_msg[idx] = (c, t)
        if 0 <= idx < len(self._msg_ids):
            self._msg_ids[idx] = id(msg)
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
