"""空模式提词：截图与 GUI 操作走 bash_opt。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_screenshot_via_bash_opt():
    text = read_prompt()
    assert_contains(text, "`bash_opt`", "screenshot", "窗口截图左上角", "截图后用 `read_image` 查看")
