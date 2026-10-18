"""空模式提词：GUI 窗口输入使用 bash_opt。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_window_input_ops():
    text = read_prompt()
    assert_contains(
        text,
        "op=windows",
        "op=click",
        "op=type",
        "窗口截图左上角",
    )
