#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/paths.py — 项目路径常量管理

覆盖内容：
  1. CHAT_DIR — Path 类型且路径正确
  2. CHAT_MSGS_DIR — Path 类型且路径正确
  3. ensure_chat_msgs_dir() — 创建目录功能
  4. ensure_chat_msgs_dir() — 幂等性（目录已存在时不抛异常）
"""

from pathlib import Path

import pytest

import src.paths


# ═══════════════════════════════════════════════════════════════
# 路径常量 — 类型检查
# ═══════════════════════════════════════════════════════════════

class TestPathConstantsType:
    """验证 CHAT_DIR / CHAT_MSGS_DIR 为 Path 实例"""

    def test_chat_dir_is_path(self):
        """CHAT_DIR 应为 pathlib.Path 类型"""
        assert isinstance(src.paths.CHAT_DIR, Path)

    def test_chat_msgs_dir_is_path(self):
        """CHAT_MSGS_DIR 应为 pathlib.Path 类型"""
        assert isinstance(src.paths.CHAT_MSGS_DIR, Path)


# ═══════════════════════════════════════════════════════════════
# 路径常量 — 相对位置正确性
# ═══════════════════════════════════════════════════════════════

class TestPathConstantsCorrectness:
    """验证 CHAT_DIR / CHAT_MSGS_DIR 相对于 paths.py 的位置正确"""

    @staticmethod
    def _expected_root() -> Path:
        """根据 src/paths.py 的 __file__ 推算 _PROJECT_ROOT"""
        return Path(src.paths.__file__).resolve().parent.parent

    def test_chat_dir_relative_to_project_root(self):
        """CHAT_DIR 应为 _PROJECT_ROOT / '.chat'"""
        expected = self._expected_root() / ".chat"
        assert src.paths.CHAT_DIR == expected

    def test_chat_msgs_dir_relative_to_chat_dir(self):
        """CHAT_MSGS_DIR 应为 _PROJECT_ROOT / '.chat' / 'msg_list'"""
        expected = self._expected_root() / ".chat" / "msg_list"
        assert src.paths.CHAT_MSGS_DIR == expected

    def test_chat_msgs_dir_is_subdir_of_chat_dir(self):
        """CHAT_MSGS_DIR 的父目录应为 CHAT_DIR"""
        assert src.paths.CHAT_MSGS_DIR.parent == src.paths.CHAT_DIR


# ═══════════════════════════════════════════════════════════════
# ensure_chat_msgs_dir — 创建目录
# ═══════════════════════════════════════════════════════════════

class TestEnsureChatMsgsDir:
    """验证 ensure_chat_msgs_dir() 能正常创建目录且幂等"""

    # ── 夹具：将路径常量重定向到 tmp_path ──────────────
    @pytest.fixture
    def redirect_paths(self, tmp_path, monkeypatch):
        """将 _PROJECT_ROOT / CHAT_DIR / CHAT_MSGS_DIR 重定向到 tmp_path"""
        monkeypatch.setattr(src.paths, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(src.paths, "CHAT_DIR", tmp_path / ".chat")
        monkeypatch.setattr(src.paths, "CHAT_MSGS_DIR", tmp_path / ".chat" / "msg_list")
        return tmp_path

    # ── 创建目录 ──────────────────────────────────────

    def test_creates_directory(self, redirect_paths):
        """ensure_chat_msgs_dir() 应在 CHAT_MSGS_DIR 处创建目录"""
        tmp_root = redirect_paths

        src.paths.ensure_chat_msgs_dir()

        target = tmp_root / ".chat" / "msg_list"
        assert target.is_dir(), f"期望目录 {target} 存在，但实际不存在"

    def test_creates_parent_directories(self, redirect_paths):
        """ensure_chat_msgs_dir() 应连带创建父级 .chat 目录"""
        tmp_root = redirect_paths

        # 确保父级目录也不存在
        assert not (tmp_root / ".chat").exists()

        src.paths.ensure_chat_msgs_dir()

        assert (tmp_root / ".chat").is_dir(), ".chat 父目录应被连带创建"

    # ── 幂等性 ────────────────────────────────────────

    def test_idempotent_when_directory_exists(self, redirect_paths):
        """ensure_chat_msgs_dir() 在目录已存在时应不抛异常"""
        tmp_root = redirect_paths
        target = tmp_root / ".chat" / "msg_list"
        target.mkdir(parents=True, exist_ok=True)

        # 再次调用，不应抛出任何异常
        src.paths.ensure_chat_msgs_dir()

        assert target.is_dir()

    def test_idempotent_called_twice(self, redirect_paths):
        """连续两次调用 ensure_chat_msgs_dir() 应均不抛异常"""
        src.paths.ensure_chat_msgs_dir()
        src.paths.ensure_chat_msgs_dir()  # 第二次不应抛异常

    def test_idempotent_directory_content_unchanged(self, redirect_paths):
        """ensure_chat_msgs_dir() 已存在目录中的内容不应被清空"""
        tmp_root = redirect_paths
        target = tmp_root / ".chat" / "msg_list"
        target.mkdir(parents=True, exist_ok=True)

        # 在目录中放一个文件
        dummy = target / "dummy.txt"
        dummy.write_text("hello")

        # 再次调用
        src.paths.ensure_chat_msgs_dir()

        assert dummy.read_text() == "hello", "已存在文件内容不应被修改"
