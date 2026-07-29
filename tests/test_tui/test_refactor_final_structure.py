"""test_refactor_final_structure — 验证步骤 9 清理后的目录结构。

验证：
  - engine/ 目录已删除
  - consumer/ 仅保留 chat_config.py 和 __init__.py
  - widgets/ 目录已删除
  - state/ 仅保留 render_state.py / consumer_registry.py / _collection.py / __init__.py
  - TUI 根层级新模块存在
"""

from __future__ import annotations

import os
from pathlib import Path


def _tui_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "tui"


class TestStep9FinalStructure:
    """验证步骤 9 清理后的最终目录结构。"""

    # ── 目录删除验证 ──

    def test_engine_directory_deleted(self) -> None:
        """验证 engine/ 目录已被删除。"""
        assert not (_tui_path() / "engine").exists(), "engine/ 目录应已删除"

    def test_widgets_directory_deleted(self) -> None:
        """验证 widgets/ 目录已被删除。"""
        assert not (_tui_path() / "widgets").exists(), "widgets/ 目录应已删除"

    # ── consumer/ 精简验证 ──

    def test_consumer_only_essential_files(self) -> None:
        """验证 consumer/ 仅保留 chat_config.py 和 __init__.py。"""
        consumer_dir = _tui_path() / "consumer"
        assert consumer_dir.exists(), "consumer/ 目录应存在"
        py_files = sorted(f.name for f in consumer_dir.glob("*.py"))
        assert py_files == ["__init__.py", "chat_config.py"], (
            f"consumer/ 应仅有 __init__.py 和 chat_config.py，实际: {py_files}"
        )

    # ── state/ 精简验证 ──

    def test_state_only_core_files(self) -> None:
        """验证 state/ 仅保留 4 个核心文件。"""
        state_dir = _tui_path() / "state"
        assert state_dir.exists(), "state/ 目录应存在"
        py_files = sorted(f.name for f in state_dir.glob("*.py"))
        expected = ["__init__.py", "_collection.py", "consumer_registry.py", "render_state.py"]
        assert py_files == expected, (
            f"state/ 应仅有 {expected}，实际: {py_files}"
        )

    def test_agent_state_deleted(self) -> None:
        assert not (_tui_path() / "state" / "agent_state.py").exists()

    def test_input_state_deleted(self) -> None:
        assert not (_tui_path() / "state" / "input_state.py").exists()

    def test_session_state_deleted(self) -> None:
        assert not (_tui_path() / "state" / "session_state.py").exists()

    def test_streaming_state_deleted(self) -> None:
        assert not (_tui_path() / "state" / "streaming_state.py").exists()

    def test_tui_state_tree_deleted(self) -> None:
        assert not (_tui_path() / "state" / "tui_state_tree.py").exists()

    # ── events/ 保留验证 ──

    def test_events_directory_preserved(self) -> None:
        """验证 events/ 目录未被删除（被非 TUI 模块引用）。"""
        assert (_tui_path() / "events").exists(), "events/ 目录应保留"

    def test_events_core_files_present(self) -> None:
        events_dir = _tui_path() / "events"
        for fname in ["event_types.py", "event_bus.py", "__init__.py"]:
            assert (events_dir / fname).exists(), f"events/{fname} 应存在"

    # ── core/ 保留验证 ──

    def test_core_style_preserved(self) -> None:
        assert (_tui_path() / "core" / "style.py").exists()

    def test_core_color_preserved(self) -> None:
        assert (_tui_path() / "core" / "color.py").exists()

    def test_core_singleton_preserved(self) -> None:
        assert (_tui_path() / "core" / "singleton.py").exists()

    # ── 新模块存在验证 ──

    _NEW_MODULES = [
        "_config.py", "_const.py", "_screen.py", "_buffer.py",
        "_input.py", "_bottom_bar.py", "_renderer.py", "_consumer.py",
        "_completion.py", "_completion_engine.py", "_locks.py",
        "_cursor_tracker.py", "_stdout_tracker.py",
        "_diff_renderer.py", "_base_display.py", "_snapshot.py",
        "__init__.py",
    ]

    def test_new_modules_exist(self) -> None:
        """验证 TUI 根层级新模块全部存在。"""
        missing = []
        for fname in self._NEW_MODULES:
            if not (_tui_path() / fname).exists():
                missing.append(fname)
        assert not missing, f"缺少新模块: {missing}"

    # ── 旧模块删除验证 ──

    _DELETED_OLD_MODULES = [
        "layout.py", "widget_base.py", "parallel_display.py",
        "testing.py", "_exceptions.py", "_lazy.py",
    ]

    def test_old_modules_deleted(self) -> None:
        """验证旧模块已被删除。"""
        remaining = []
        for fname in self._DELETED_OLD_MODULES:
            if (_tui_path() / fname).exists():
                remaining.append(fname)
        assert not remaining, f"旧模块应已删除: {remaining}"

    # ── 向后兼容模块保留验证 ──

    def test_backward_compat_modules_exist(self) -> None:
        """验证向后兼容的 re-export 模块仍存在。"""
        for fname in ["config.py", "render_buffer.py", "input.py", "framework.py"]:
            assert (_tui_path() / fname).exists(), f"{fname} 应保留为兼容 re-export"
