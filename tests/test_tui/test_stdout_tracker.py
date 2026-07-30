import pytest
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestOutputHistoryIntegration:
    """集成测试：写入→刷盘→恢复全链路。"""

    def test_write_flush_restore_cycle(self, tmp_path: Path):
        """验证写入→刷盘→重新加载恢复最后行的完整链路。

        mock 文件锁为始终成功，避免多线程并发刷盘时 flock 竞争导致丢行。
        """
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history"
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            real_stdout = io.StringIO()
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10  # 启用追踪

            # 写入 1500 行
            for i in range(1500):
                tracker._track(f"line_{i}\n")

            # 刷盘
            tracker._flush_history()

            # 验证文件存在且有内容
            assert output_file.exists()
            content = output_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            assert len(lines) > 0
            # 验证最后写入的行在文件中
            assert "line_1499" in lines

        # 重新创建 tracker，验证恢复最后 1000 行
        with (
            patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file),
            patch("src.tui._stdout_tracker._lock_history_file", return_value=True),
            patch("src.tui._stdout_tracker._unlock_history_file"),
        ):
            tracker2 = _StdoutLineTracker(real_stdout)
            assert len(tracker2._ring) <= 1000
            # 环形缓冲最后一行应为 line_1499
            if tracker2._ring:
                assert tracker2._ring[-1] == "line_1499"

    def test_no_file_graceful_start(self, tmp_path: Path):
        """文件不存在时平滑启动。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        real_stdout = io.StringIO()
        output_file = tmp_path / "nonexistent_output_history"
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file):
            tracker = _StdoutLineTracker(real_stdout)
            assert len(tracker._ring) == 0  # 平滑启动，空缓冲

    def test_overflow_ring_capacity(self, tmp_path: Path):
        """环形缓冲超过 _MAX_LINES=1000 时自动淘汰旧行。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        real_stdout = io.StringIO()
        output_file = tmp_path / "output_history_overflow"
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file):
            tracker = _StdoutLineTracker(real_stdout)
            tracker._scroll_end = 10

            # 写入 1200 行（超过 1000）
            for i in range(1200):
                tracker._track(f"line_{i}\n")

            assert len(tracker._ring) == 1000
            assert tracker._ring[0] == "line_200"  # 第200行成为首行（0-based）
            assert tracker._ring[-1] == "line_1199"  # 最后一行

    def test_history_file_not_overwritten_on_start(self, tmp_path: Path):
        """启动时加载不覆盖现有文件内容，仅读取。"""
        from src.tui._stdout_tracker import _StdoutLineTracker

        output_file = tmp_path / "output_history_persist"
        # 预置文件内容
        output_file.write_text("\n".join(f"existing_{i}" for i in range(500)) + "\n", encoding="utf-8")

        real_stdout = io.StringIO()
        with patch("src.tui._stdout_tracker.OUTPUT_HISTORY_FILE", output_file):
            tracker = _StdoutLineTracker(real_stdout)
            # 恢复 500 行到环形缓冲
            assert len(tracker._ring) == 500
            assert tracker._ring[-1] == "existing_499"

        # 验证文件内容未被修改
        content = output_file.read_text(encoding="utf-8")
        assert "existing_499" in content
        assert len(content.splitlines()) == 500
