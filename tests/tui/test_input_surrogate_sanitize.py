"""TUI 输入边界：孤立 UTF-16 代理项清洗回归测试。

背景：孤立代理项（U+D800-U+DFFF）无法以 UTF-8 编码——进入输入缓冲后，
提交成用户消息会让会话自动保存 / 下一次请求序列化抛
``UnicodeEncodeError: surrogates not allowed``。输入侧统一清洗：
  - ``sanitize_paste``（粘贴 / 批量输入）→ 替换为 U+FFFD；
  - ``InputBufferEditor.set_buffer``（预填，如 /editmsg）→ 同样清洗；
  - 相邻的高+低代理对（部分环境以两个码点表示）→ 还原为完整 emoji。
"""

from __future__ import annotations

from pathlib import Path

from src._ansi_text import sanitize_paste
from src._unicode_text import REPLACEMENT_CHARACTER
from src.tui._input_buffer import InputBufferEditor

LONE_HIGH = "\ud83d"
LONE_LOW = "\ude00"
EMOJI = "\U0001F600"


class TestSanitizePaste:
    def test_lone_surrogate_replaced(self):
        assert sanitize_paste(f"粘贴{LONE_HIGH}") == f"粘贴{REPLACEMENT_CHARACTER}"

    def test_surrogate_pair_restored(self):
        assert sanitize_paste(LONE_HIGH + LONE_LOW) == EMOJI

    def test_normal_text_and_controls_unchanged(self):
        assert sanitize_paste("正常\ttab\n换行") == "正常\ttab\n换行"

    def test_ansi_still_stripped(self):
        assert sanitize_paste(f"\x1b[31m红{LONE_LOW}\x1b[0m") == (
            f"红{REPLACEMENT_CHARACTER}"
        )


class TestInputBufferSanitize:
    def test_set_buffer_sanitizes(self):
        editor = InputBufferEditor(Path("unused_history.txt"))
        editor.set_buffer(f"预填{LONE_HIGH}")
        assert editor.get_current_text() == f"预填{REPLACEMENT_CHARACTER}"

    def test_set_buffer_restores_pair(self):
        editor = InputBufferEditor(Path("unused_history.txt"))
        editor.set_buffer(LONE_HIGH + LONE_LOW)
        assert editor.get_current_text() == EMOJI

    def test_handle_chars_sanitizes(self):
        editor = InputBufferEditor(Path("unused_history.txt"))
        editor.handle_chars(f"批量{LONE_LOW}")
        assert editor.get_current_text() == f"批量{REPLACEMENT_CHARACTER}"
