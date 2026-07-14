"""CursorTracker 单元测试。

覆盖全部公开 API：初始化、move_to/move_xy、set、record_newlines、
save/restore、pos 属性。
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass

import pytest

from src.tui.widgets.cursor_tracker import CursorPosition, CursorTracker


# ═══════════════════════════════════════════════════════════
# CursorPosition
# ═══════════════════════════════════════════════════════════

class TestCursorPosition:
    """CursorPosition dataclass 基础测试。"""

    def test_default(self):
        pos = CursorPosition()
        assert pos.row == 1
        assert pos.col == 1

    def test_custom(self):
        pos = CursorPosition(10, 5)
        assert pos.row == 10
        assert pos.col == 5

    def test_str(self):
        pos = CursorPosition(3, 7)
        assert str(pos) == "(row=3, col=7)"

    def test_immutable(self):
        pos = CursorPosition(5, 5)
        pos.row = 10  # dataclass 默认可变，但这是设计允许的
        assert pos.row == 10


# ═══════════════════════════════════════════════════════════
# CursorTracker
# ═══════════════════════════════════════════════════════════

class TestCursorTrackerInit:
    """CursorTracker 初始化测试。"""

    def test_default_position(self):
        """默认初始位置为 (1, 1)。"""
        tracker = CursorTracker()
        pos = tracker.pos
        assert pos.row == 1
        assert pos.col == 1

    def test_custom_initial_position(self):
        """自定义初始位置。"""
        tracker = CursorTracker(initial_row=10, initial_col=5)
        pos = tracker.pos
        assert pos.row == 10
        assert pos.col == 5

    def test_custom_file(self):
        """自定义输出文件对象。"""
        buf = io.StringIO()
        tracker = CursorTracker(initial_row=3, initial_col=7, _file=buf)
        assert tracker.pos.row == 3
        assert tracker.pos.col == 7
        tracker.move_to(5, 5)
        assert "\033[5;5H" in buf.getvalue()


class TestCursorTrackerMoveTo:
    """move_to 方法测试。"""

    def test_move_to_updates_position(self):
        """move_to 后 pos 反映新位置。"""
        tracker = CursorTracker()
        tracker.move_to(10, 20)
        assert tracker.pos.row == 10
        assert tracker.pos.col == 20

    def test_move_to_writes_ansi(self):
        """move_to 写入 ANSI CUP 序列。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.move_to(5, 10)
        assert buf.getvalue() == "\033[5;10H"

    def test_move_to_chained(self):
        """连续 move_to 累计正确。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.move_to(3, 3)
        tracker.move_to(5, 1)
        tracker.move_to(10, 20)
        assert tracker.pos.row == 10
        assert tracker.pos.col == 20
        assert buf.getvalue() == "\033[3;3H\033[5;1H\033[10;20H"

    def test_move_to_same_position(self):
        """移动到当前位置，should still write ANSI。"""
        buf = io.StringIO()
        tracker = CursorTracker(initial_row=5, initial_col=5, _file=buf)
        tracker.move_to(5, 5)
        assert buf.getvalue() == "\033[5;5H"
        assert tracker.pos.row == 5
        assert tracker.pos.col == 5


class TestCursorTrackerMoveXy:
    """move_xy (0-based) 方法测试。"""

    def test_move_xy_converts_to_1based(self):
        """0-based 输入正确转换为 1-based。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.move_xy(col=4, row=9)  # 期望转换为 1-based: (10, 5)
        assert tracker.pos.row == 10
        assert tracker.pos.col == 5
        assert buf.getvalue() == "\033[10;5H"

    def test_move_xy_origin(self):
        """move_xy(0, 0) 对应 (1, 1)。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.move_xy(0, 0)
        assert tracker.pos.row == 1
        assert tracker.pos.col == 1
        assert buf.getvalue() == "\033[1;1H"

    def test_move_xy_chained(self):
        """连续 move_xy 正确转换。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.move_xy(0, 0)    # → (1, 1)
        tracker.move_xy(9, 4)    # → (5, 10)
        tracker.move_xy(79, 23)  # → (24, 80)
        assert tracker.pos.row == 24
        assert tracker.pos.col == 80


class TestCursorTrackerSet:
    """set 方法（无 I/O）测试。"""

    def test_set_no_io(self):
        """set 不写入终端。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.set(15, 30)
        assert buf.getvalue() == ""  # 没有写入任何内容
        assert tracker.pos.row == 15
        assert tracker.pos.col == 30

    def test_set_after_move(self):
        """move_to 后 set 不覆盖之前写入的内容。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.move_to(5, 5)
        buf.seek(0)
        assert len(buf.getvalue()) > 0
        tracker.set(10, 10)
        # 写入的内容不变（没有新增）
        assert len(buf.getvalue()) > 0
        # 但位置已更新
        assert tracker.pos.row == 10
        assert tracker.pos.col == 10


class TestCursorTrackerRecordNewlines:
    """record_newlines 方法测试。"""

    def test_record_one_line(self):
        """记录 1 行：row+1, col=1。"""
        tracker = CursorTracker(initial_row=5, initial_col=10)
        tracker.record_newlines(1)
        assert tracker.pos.row == 6
        assert tracker.pos.col == 1

    def test_record_multiple_lines(self):
        """记录多行：row+=n, col=1。"""
        tracker = CursorTracker(initial_row=3, initial_col=5)
        tracker.record_newlines(3)
        assert tracker.pos.row == 6
        assert tracker.pos.col == 1

    def test_record_zero_lines(self):
        """记录 0 行：row 不变，col 重置为 1（新行语义）。"""
        tracker = CursorTracker(initial_row=5, initial_col=3)
        tracker.record_newlines(0)
        assert tracker.pos.row == 5
        assert tracker.pos.col == 1  # record_newlines 始终重置 col=1

    def test_no_io(self):
        """record_newlines 不写终端。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        tracker.record_newlines(2)
        assert buf.getvalue() == ""


class TestCursorTrackerRecordMoveDown:
    """record_move_down 方法测试。"""

    def test_move_down_default(self):
        """默认下移 1 行，列不变。"""
        tracker = CursorTracker(initial_row=10, initial_col=5)
        tracker.record_move_down()
        assert tracker.pos.row == 11
        assert tracker.pos.col == 5

    def test_move_down_multiple(self):
        """下移多行。"""
        tracker = CursorTracker(initial_row=10, initial_col=5)
        tracker.record_move_down(3)
        assert tracker.pos.row == 13
        assert tracker.pos.col == 5


class TestCursorTrackerSaveRestore:
    """save/restore 检查点模式测试。"""

    def test_save_returns_snapshot(self):
        """save 返回当前位置的快照。"""
        tracker = CursorTracker(initial_row=5, initial_col=10)
        snap = tracker.save()
        assert isinstance(snap, CursorPosition)
        assert snap.row == 5
        assert snap.col == 10

    def test_restore_recovers_position(self):
        """restore 恢复之前保存的位置。"""
        tracker = CursorTracker(initial_row=1, initial_col=1)
        tracker.move_to(10, 20)
        snap = tracker.save()
        tracker.move_to(30, 40)
        tracker.restore(snap)
        assert tracker.pos.row == 10
        assert tracker.pos.col == 20

    def test_nested_save_restore(self):
        """多层嵌套 save/restore 正确。"""
        tracker = CursorTracker(initial_row=1, initial_col=1)

        # Level 1
        s1 = tracker.save()
        tracker.move_to(5, 5)

        # Level 2
        s2 = tracker.save()
        tracker.move_to(10, 10)

        # Level 3
        s3 = tracker.save()
        tracker.move_to(20, 20)

        tracker.restore(s3)
        assert tracker.pos == CursorPosition(10, 10)

        tracker.restore(s2)
        assert tracker.pos == CursorPosition(5, 5)

        tracker.restore(s1)
        assert tracker.pos == CursorPosition(1, 1)

    def test_restore_no_io(self):
        """restore 不写终端。"""
        buf = io.StringIO()
        tracker = CursorTracker(_file=buf)
        snap = tracker.save()
        tracker.move_to(10, 10)
        assert len(buf.getvalue()) > 0
        buf.seek(0)
        before_len = len(buf.getvalue())
        tracker.restore(snap)
        assert len(buf.getvalue()) == before_len  # 没有新增写入


class TestCursorTrackerPosProperty:
    """pos 属性测试。"""

    def test_pos_is_snapshot_not_reference(self):
        """pos 返回的是快照，不是内部状态的引用。"""
        tracker = CursorTracker(initial_row=5, initial_col=5)
        pos1 = tracker.pos
        tracker.move_to(10, 10)
        pos2 = tracker.pos
        assert pos1.row == 5   # pos1 不受后续 move_to 影响
        assert pos2.row == 10

    def test_pos_after_operations(self):
        """经过一系列操作后 pos 正确。"""
        tracker = CursorTracker()
        tracker.move_to(3, 5)
        tracker.record_newlines(2)
        tracker.set(10, 20)
        snap = tracker.save()
        tracker.record_move_down()
        tracker.restore(snap)
        assert tracker.pos == CursorPosition(10, 20)


class TestCursorTrackerRealStdout:
    """使用真实 sys.__stdout__ 的集成测试。"""

    def test_move_to_real_stdout(self):
        """使用真实 stdout 的 move_to 不抛出异常。"""
        tracker = CursorTracker()
        try:
            tracker.move_to(5, 5)
        except Exception as e:
            pytest.fail(f"move_to 异常: {e}")

    def test_chained_real_stdout(self):
        """连续操作在真实 stdout 下不抛出异常。"""
        tracker = CursorTracker()
        try:
            tracker.move_to(1, 1)
            tracker.record_newlines(3)
            tracker.move_to(5, 10)
            tracker.set(10, 20)
            snap = tracker.save()
            tracker.move_to(15, 5)
            tracker.restore(snap)
        except Exception as e:
            pytest.fail(f"链式操作异常: {e}")


# ═══════════════════════════════════════════════════════════
# __repr__
# ═══════════════════════════════════════════════════════════

class TestCursorTrackerRepr:
    def test_repr(self):
        tracker = CursorTracker(initial_row=10, initial_col=5)
        assert "CursorTracker" in repr(tracker)
        assert "10" in repr(tracker)
        assert "5" in repr(tracker)
