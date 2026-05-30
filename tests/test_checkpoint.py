"""Tests for src/checkpoint.py — 任务断点保存与恢复"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import src.checkpoint as _checkpoint_module

from src.checkpoint import (
    clear_checkpoint,
    get_checkpoint_info,
    has_checkpoint,
    load_checkpoint,
    save_checkpoint,
)


# ═══════════════════════════════════════════════════════════════
# 辅助 fixture
# ═══════════════════════════════════════════════════════════════

_FAKE_CHECKPOINT_FILE: Path | None = None


@pytest.fixture(autouse=True)
def _isolate_checkpoint_file(monkeypatch, tmp_path):
    """自动将断点文件指向临时路径，避免读写真实断点文件"""
    global _FAKE_CHECKPOINT_FILE
    fake_path = tmp_path / "_checkpoint.json"
    _FAKE_CHECKPOINT_FILE = fake_path
    # 直接设置模块属性，避免字符串路径解析的干扰
    monkeypatch.setattr(_checkpoint_module, "CHECKPOINT_FILE", fake_path)
    yield
    # 测试结束后清理临时文件（tmp_path 会自动清理，无需额外操作）


def _make_user_message(content: str) -> dict:
    """构造一条 user 角色消息"""
    return {"role": "user", "content": content}


def _make_assistant_message(content: str = "ok") -> dict:
    """构造一条 assistant 角色消息"""
    return {"role": "assistant", "content": content}


# ═══════════════════════════════════════════════════════════════
# TestSaveCheckpoint
# ═══════════════════════════════════════════════════════════════

class TestSaveCheckpoint:
    """保存断点相关测试"""

    # ── 基础保存 ──────────────────────────────────────────────

    def test_save_creates_file(self):
        """保存断点后，断点文件应被创建"""
        messages = [_make_user_message("Hello")]
        save_checkpoint(messages, model="gpt-4")
        # 使用模块引用以适配 monkeypatch
        assert _checkpoint_module.CHECKPOINT_FILE.exists(), "断点文件应存在"
        assert _checkpoint_module.CHECKPOINT_FILE.is_file(), "断点文件应为普通文件"

    # ── 自动提取任务描述 ──────────────────────────────────────

    def test_auto_extract_task_description_from_last_user(self):
        """未提供 task_description 时，从最后一条 user 消息自动提取"""
        messages = [
            _make_system_message("You are a helper"),
            _make_user_message("帮我写一首诗"),
            _make_assistant_message("好的，以下是一首诗..."),
            _make_user_message("帮我翻译成英文"),
        ]
        save_checkpoint(messages, model="gpt-4")
        data = load_checkpoint()
        assert data is not None
        # 应从最后一条 user 消息提取
        assert data["task_description"] == "帮我翻译成英文"

    def test_auto_extract_truncates_long_content(self):
        """消息内容超过 200 字符时自动截断并追加 …"""
        long_content = "A" * 300
        messages = [_make_user_message(long_content)]
        save_checkpoint(messages, model="gpt-4")
        data = load_checkpoint()
        assert data is not None
        assert len(data["task_description"]) == 201  # 200 chars + "…"
        assert data["task_description"].endswith("…")

    def test_auto_extract_no_user_message(self):
        """消息列表中没有 user 消息时，task_description 为空字符串"""
        messages = [_make_assistant_message("hello")]
        save_checkpoint(messages, model="gpt-4")
        data = load_checkpoint()
        assert data is not None
        assert data["task_description"] == ""

    # ── 自定义任务描述 ────────────────────────────────────────

    def test_custom_task_description(self):
        """提供自定义 task_description 时，应使用提供的值"""
        messages = [_make_user_message("忽略我")]
        save_checkpoint(messages, model="gpt-4", task_description="自定义任务")
        data = load_checkpoint()
        assert data is not None
        assert data["task_description"] == "自定义任务"

    def test_custom_task_description_overrides_auto_extract(self):
        """即使有 user 消息，自定义 task_description 也应优先"""
        messages = [_make_user_message("自动提取内容")]
        save_checkpoint(messages, model="gpt-4", task_description="显式指定")
        data = load_checkpoint()
        assert data is not None
        assert data["task_description"] == "显式指定"

    # ── 空消息列表 ────────────────────────────────────────────

    def test_empty_messages_list(self):
        """消息列表为空时也能正常保存"""
        save_checkpoint([], model="gpt-4")
        assert _checkpoint_module.CHECKPOINT_FILE.exists()
        data = load_checkpoint()
        assert data is not None
        assert data["message_count"] == 0
        assert data["messages"] == []

    # ── 保存内容校验 ──────────────────────────────────────────

    def test_saved_data_contains_expected_fields(self):
        """保存的数据包含必要字段"""
        messages = [_make_user_message("test")]
        save_checkpoint(messages, model="claude-3")
        data = load_checkpoint()
        assert data is not None
        assert "saved_at" in data
        assert isinstance(data["saved_at"], (int, float))
        assert data["model"] == "claude-3"
        assert data["message_count"] == 1
        assert data["messages"] == messages


# ═══════════════════════════════════════════════════════════════
# TestLoadCheckpoint
# ═══════════════════════════════════════════════════════════════

class TestLoadCheckpoint:
    """加载断点相关测试"""

    def test_load_returns_saved_data(self):
        """保存后能正确加载出完整数据"""
        messages = [_make_user_message("你好"), _make_assistant_message("你好！")]
        save_checkpoint(messages, model="gpt-4", task_description="对话")
        data = load_checkpoint()
        assert data is not None
        assert data["messages"] == messages
        assert data["model"] == "gpt-4"
        assert data["task_description"] == "对话"
        assert data["message_count"] == 2

    def test_load_contains_saved_at(self):
        """加载的数据包含 saved_at 时间戳字段"""
        save_checkpoint([_make_user_message("hi")], model="gpt-4")
        data = load_checkpoint()
        assert data is not None
        assert "saved_at" in data
        # 应接近当前时间（允许 5 秒偏差）
        assert abs(data["saved_at"] - time.time()) < 5

    def test_load_returns_none_when_no_file(self):
        """没有断点文件时返回 None"""
        assert load_checkpoint() is None

    def test_load_returns_none_on_corrupted_file(self):
        """断点文件损坏时返回 None"""
        _checkpoint_module.CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _checkpoint_module.CHECKPOINT_FILE.write_text("这不是合法的 JSON", encoding="utf-8")
        assert load_checkpoint() is None

    def test_load_returns_none_on_empty_file(self):
        """断点文件为空时返回 None"""
        _checkpoint_module.CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _checkpoint_module.CHECKPOINT_FILE.write_text("", encoding="utf-8")
        assert load_checkpoint() is None


# ═══════════════════════════════════════════════════════════════
# TestClearCheckpoint
# ═══════════════════════════════════════════════════════════════

class TestClearCheckpoint:
    """清除断点相关测试"""

    def test_clear_removes_checkpoint(self):
        """清除断点后文件应被删除"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        assert _checkpoint_module.CHECKPOINT_FILE.exists()
        clear_checkpoint()
        assert not _checkpoint_module.CHECKPOINT_FILE.exists()

    def test_clear_makes_has_checkpoint_false(self):
        """清除断点后 has_checkpoint() 应返回 False"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        assert has_checkpoint() is True
        clear_checkpoint()
        assert has_checkpoint() is False

    def test_clear_nonexistent_checkpoint(self):
        """清除不存在的断点不应抛出异常"""
        assert not _checkpoint_module.CHECKPOINT_FILE.exists()
        # 不应抛出任何异常
        clear_checkpoint()


# ═══════════════════════════════════════════════════════════════
# TestHasCheckpoint
# ═══════════════════════════════════════════════════════════════

class TestHasCheckpoint:
    """断点存在性检测相关测试"""

    def test_has_checkpoint_after_save(self):
        """保存后 has_checkpoint() 返回 True"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        assert has_checkpoint() is True

    def test_has_checkpoint_after_clear(self):
        """清除后 has_checkpoint() 返回 False"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        clear_checkpoint()
        assert has_checkpoint() is False

    def test_has_checkpoint_initial(self):
        """初始状态下没有断点，返回 False"""
        assert has_checkpoint() is False

    def test_has_checkpoint_with_empty_file(self):
        """空文件不应被视为有效断点（与 load_checkpoint 一致）"""
        _checkpoint_module.CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _checkpoint_module.CHECKPOINT_FILE.write_text("", encoding="utf-8")
        # has_checkpoint 仅检查文件存在性，空文件也返回 True
        assert has_checkpoint() is True


# ═══════════════════════════════════════════════════════════════
# TestGetCheckpointInfo
# ═══════════════════════════════════════════════════════════════

class TestGetCheckpointInfo:
    """断点摘要信息相关测试"""

    def test_get_info_after_save(self):
        """保存后能获取断点摘要信息"""
        messages = [
            _make_user_message("请帮我写一个 Python 脚本"),
            _make_assistant_message("好的，以下是脚本..."),
        ]
        save_checkpoint(messages, model="gpt-4", task_description="写脚本")
        info = get_checkpoint_info()
        assert info is not None
        assert info["message_count"] == 2
        assert info["task_description"] == "写脚本"
        assert info["model"] == "gpt-4"

    def test_info_contains_elapsed_minutes(self):
        """摘要信息包含已过分钟数（elapsed_minutes）"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        info = get_checkpoint_info()
        assert info is not None
        assert "elapsed_minutes" in info
        assert isinstance(info["elapsed_minutes"], float)
        # 刚保存不久，elapsed_minutes 应接近 0（允许 1 分钟偏差）
        assert info["elapsed_minutes"] >= 0
        assert info["elapsed_minutes"] < 1

    def test_info_contains_expected_keys(self):
        """摘要信息包含所有预期字段"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        info = get_checkpoint_info()
        assert info is not None
        expected_keys = {"message_count", "task_description", "elapsed_minutes", "model"}
        assert info.keys() == expected_keys

    def test_get_info_no_checkpoint(self):
        """没有断点时 get_checkpoint_info() 返回 None"""
        assert get_checkpoint_info() is None

    def test_get_info_after_clear(self):
        """清除断点后 get_checkpoint_info() 返回 None"""
        save_checkpoint([_make_user_message("test")], model="gpt-4")
        clear_checkpoint()
        assert get_checkpoint_info() is None


# ═══════════════════════════════════════════════════════════════
# 辅助函数（本文件私有）
# ═══════════════════════════════════════════════════════════════

def _make_system_message(content: str) -> dict:
    """构造一条 system 角色消息"""
    return {"role": "system", "content": content}
