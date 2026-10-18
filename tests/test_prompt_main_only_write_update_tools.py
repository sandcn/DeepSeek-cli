"""空模式提词：代码写入只走 write_file/update_file。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_write_tools_are_mandatory():
    text = read_prompt()
    assert_contains(
        text,
        "写入或修改用 `write_file`/`update_file`",
        "不要用 bash 直接改写代码文件",
    )
