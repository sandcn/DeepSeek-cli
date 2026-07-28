"""test_read_stdin_once — Input.read_stdin_once() 方法单元测试。

覆盖无数据、单字符、Enter、中断、暂停状态等核心场景。
使用 os.pipe() 模拟 stdin fd。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.tui.input import Input


def _create_input(pipe_fd: int, tmp_path: Path) -> Input:
    """创建使用指定 pipe fd 的 Input 实例。"""
    return Input(fd=pipe_fd, history_file=tmp_path / "test_history")


class TestReadStdinOnce:
    """read_stdin_once() 方法测试。"""

    def test_read_stdin_once_no_data_regression(self, tmp_path: Path) -> None:
        """无数据时返回 False，不阻塞。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            result = inp.read_stdin_once()
            assert result is False
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_char_regression(self, tmp_path: Path) -> None:
        """写入字符 'a' 后正确分发到缓冲区。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            os.write(w_fd, b"a")
            time.sleep(0.05)  # 确保数据到达 pipe
            result = inp.read_stdin_once()
            assert result is True
            assert inp.get_current_text() == "a"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_enter_regression(self, tmp_path: Path) -> None:
        """Enter 键写入后 has_queued_input() 返回 True。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            inp.handle_chars("test")
            os.write(w_fd, b"\r")
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is True
            assert inp.has_queued_input()
            assert inp.get_queued_input() == "test"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_interrupt_regression(self, tmp_path: Path) -> None:
        """Ctrl+C 触发 _do_interrupt()，缓冲区被清空并回显空字符串。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            inp.handle_chars("hello")
            os.write(w_fd, b"\x03")  # Ctrl+C
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is True
            # 中断后缓冲区被 reset，_interrupted 标志被设置
            assert inp.get_current_text() == ""
            assert inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_paused_regression(self, tmp_path: Path) -> None:
        """暂停状态下不读取数据，返回 False。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            inp.pause_io()
            os.write(w_fd, b"a")
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is False
            assert inp.get_current_text() == ""
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_stopped_regression(self, tmp_path: Path) -> None:
        """已停止状态下不读取数据，返回 False。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            inp.stop_io()
            os.write(w_fd, b"a")
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is False
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_paste_detection(self, tmp_path: Path) -> None:
        """粘贴检测：快速连续写入多个字符应被识别为粘贴。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            # 写入多个字符（快速粘贴模拟）
            os.write(w_fd, b"hello")
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is True
            # 粘贴检测会将多个字符合并处理
            assert inp.get_current_text() == "hello"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_backspace(self, tmp_path: Path) -> None:
        """退格键正确删除字符。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            inp.handle_chars("abc")
            os.write(w_fd, b"\x7f")  # DEL / Backspace
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is True
            assert inp.get_current_text() == "ab"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_tab(self, tmp_path: Path) -> None:
        """Tab 键插入制表符。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            os.write(w_fd, b"\t")
            time.sleep(0.05)
            result = inp.read_stdin_once()
            assert result is True
            assert inp.get_current_text() == "\t"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_eof_no_crash(self, tmp_path: Path) -> None:
        """pipe 写入端关闭后不崩溃，返回 False。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = _create_input(r_fd, tmp_path)
            os.close(w_fd)
            time.sleep(0.05)
            result = inp.read_stdin_once()
            # EOF 时返回 False，不崩溃
            assert result is False
        finally:
            os.close(r_fd)
