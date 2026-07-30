"""测试步骤1：验证旧TUI模块删除结果。

确认已删除的目录和文件确实不存在，保留的文件仍然存在。
"""
import os


def _tui_path(relative: str) -> str:
    """获取 src/tui/ 下的绝对路径。"""
    return os.path.join(os.path.dirname(__file__), "..", "..", "src", "tui", relative)


class TestDeletedDirectories:
    """验证已删除的目录。"""

    def test_terminal_deleted(self):
        assert not os.path.exists(_tui_path("terminal"))

    def test_animation_deleted(self):
        assert not os.path.exists(_tui_path("animation"))

    def test_components_deleted(self):
        assert not os.path.exists(_tui_path("components"))

    def test_frame_deleted(self):
        assert not os.path.exists(_tui_path("frame"))


class TestDeletedFiles:
    """验证已删除的单文件。"""

    def test_layout_py_deleted(self):
        assert not os.path.exists(_tui_path("layout.py"))

    def test_widget_base_py_deleted(self):
        assert not os.path.exists(_tui_path("widget_base.py"))

    def test_parallel_display_py_deleted(self):
        assert not os.path.exists(_tui_path("parallel_display.py"))

    def test_testing_py_deleted(self):
        assert not os.path.exists(_tui_path("testing.py"))

    def test_exceptions_py_deleted(self):
        assert not os.path.exists(_tui_path("_exceptions.py"))

    def test_lazy_py_deleted(self):
        assert not os.path.exists(_tui_path("_lazy.py"))


class TestPreservedFiles:
    """验证 core/ 目录保留文件。"""

    def test_core_style_preserved(self):
        assert os.path.isfile(_tui_path("core/style.py"))

    def test_core_color_preserved(self):
        assert os.path.isfile(_tui_path("core/color.py"))

    def test_core_singleton_preserved(self):
        assert os.path.isfile(_tui_path("core/singleton.py"))

    def test_core_init_preserved(self):
        assert os.path.isfile(_tui_path("core/__init__.py"))

    def test_core_only_four_files(self):
        """验证 core/ 目录仅包含 4 个文件。"""
        core_dir = _tui_path("core")
        files = [f for f in os.listdir(core_dir) if f.endswith(".py")]
        assert len(files) == 4, f"Expected 4 files, got {len(files)}: {files}"
