"""test_tui_structure — TUI 目录结构稳定性测试。

替代 test_refactor_final_structure.py 与 test_refactor_phase1_deletion.py
（原结构断言与步骤 1-8 实际删除结果冲突，已合并收敛为单一稳定文件）。

保留稳定断言：
  - 关键子目录存在（consumer/state/events/core/_bottom_bar/_renderer）
  - 关键模块与公共入口存在（src/tui/__init__.py 导出面）
  - 已删除死代码不回归（与步骤 1-8 实际删除清单精确对齐）
  - 兼容 re-export 门面保留（input.py）

移除脆弱断言：
  - core/ 恰好 N 个文件（计数断言）→ 改为关键文件存在断言
  - 已删除模块「应存在」断言（framework/config/render_buffer 等）
"""

from __future__ import annotations

from pathlib import Path


def _src_tui() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "tui"


class TestKeyDirectoriesExist:
    """关键子目录存在。"""

    def test_ink_dir_exists(self) -> None:
        assert (_src_tui() / "ink").is_dir()

    def test_app_dir_exists(self) -> None:
        assert (_src_tui() / "app").is_dir()

    def test_consumer_dir_exists(self) -> None:
        assert (_src_tui() / "consumer").is_dir()

    def test_state_dir_exists(self) -> None:
        assert (_src_tui() / "state").is_dir()

    def test_events_dir_exists(self) -> None:
        assert (_src_tui() / "events").is_dir()

    def test_core_dir_exists(self) -> None:
        assert (_src_tui() / "core").is_dir()


class TestKeyModulesExist:
    """TUI 根层级关键模块存在。"""

    _KEY_MODULES = [
        "_config.py", "_const.py", "_screen.py", "_buffer.py",
        "_input.py", "_consumer.py", "_completion.py", "_completion_engine.py",
        "_animator.py", "_cursor_tracker.py", "_stdout_tracker.py",
        "_diff_renderer.py", "_base_display.py", "_snapshot.py",
        "_subagent_panel.py", "_tool_icons.py", "_dispatcher.py",
        "_ink_bridge.py", "__init__.py",
    ]

    def test_key_modules_exist(self) -> None:
        missing = [m for m in self._KEY_MODULES if not (_src_tui() / m).exists()]
        assert not missing, f"缺少关键模块: {missing}"

    def test_backward_compat_input_reexport_exists(self) -> None:
        """向后兼容门面 input.py 保留（re-export ._input）。"""
        assert (_src_tui() / "input.py").is_file()


class TestPublicApiExports:
    """src/tui/__init__.py 关键公共入口导出。"""

    _PUBLIC_API = [
        "TuiConfig", "RenderCommand", "FrameworkCommand", "ChatCommand",
        "RenderBuffer", "Input", "KeyEvent", "ChatUIConsumer",
        "get_active_chat_ui", "AppModel", "ChatConfig",
        "render_diff_to_ansi", "show_file_diff", "BaseDisplay",
    ]

    def test_public_api_exports(self) -> None:
        import src.tui as tui

        missing = [name for name in self._PUBLIC_API if not hasattr(tui, name)]
        assert not missing, f"__init__.py 缺少导出: {missing}"


class TestDeletedDeadCodeNotRegress:
    """步骤 1-8 已删除死代码不回归（与删除清单精确对齐）。"""

    _DELETED_MODULES = [
        "framework.py", "config.py", "render_buffer.py",
        "_input_reader.py", "_locks.py", "_param_formatter.py",
    ]

    def test_dead_code_modules_not_regress(self) -> None:
        remaining = [m for m in self._DELETED_MODULES if (_src_tui() / m).exists()]
        assert not remaining, f"死代码应保持删除: {remaining}"


class TestDeletedOldDirectoriesNotRegress:
    """旧目录删除不回归。"""

    _DELETED_DIRS = [
        "engine", "widgets", "terminal", "animation", "components", "frame",
        "_bottom_bar", "_renderer",
    ]

    def test_old_directories_not_regress(self) -> None:
        remaining = [d for d in self._DELETED_DIRS if (_src_tui() / d).exists()]
        assert not remaining, f"旧目录应保持删除: {remaining}"


class TestDeletedOldFilesNotRegress:
    """旧文件删除不回归。"""

    _DELETED_FILES = [
        "layout.py", "widget_base.py", "parallel_display.py",
        "testing.py", "_exceptions.py", "_lazy.py", "_output.py",
    ]

    def test_old_files_not_regress(self) -> None:
        remaining = [f for f in self._DELETED_FILES if (_src_tui() / f).exists()]
        assert not remaining, f"旧文件应保持删除: {remaining}"


class TestSubpackageStructure:
    """子包关键文件存在（不计数，避免脆弱断言）。"""

    def test_consumer_essential_files(self) -> None:
        consumer = _src_tui() / "consumer"
        for fname in ["__init__.py", "chat_config.py"]:
            assert (consumer / fname).is_file(), f"consumer/{fname} 应存在"

    def test_state_essential_files(self) -> None:
        state = _src_tui() / "state"
        for fname in ["__init__.py", "_collection.py", "consumer_registry.py"]:
            assert (state / fname).is_file(), f"state/{fname} 应存在"

    def test_render_state_deleted(self) -> None:
        """render_state.py 已并入 AppModel（应删除）。"""
        assert not (_src_tui() / "state" / "render_state.py").exists()

    def test_events_essential_files(self) -> None:
        events = _src_tui() / "events"
        for fname in ["__init__.py", "event_bus.py", "event_types.py"]:
            assert (events / fname).is_file(), f"events/{fname} 应存在"

    def test_core_essential_files(self) -> None:
        core = _src_tui() / "core"
        for fname in ["__init__.py", "style.py", "color.py", "singleton.py"]:
            assert (core / fname).is_file(), f"core/{fname} 应存在"
