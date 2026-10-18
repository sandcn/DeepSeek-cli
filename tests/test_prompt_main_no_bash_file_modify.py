"""空模式提词：禁止用 bash 直接改写代码文件。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_bash_cannot_modify_code_files():
    text = read_prompt()
    assert_contains(
        text,
        "写入或修改用 `write`/`edit`",
        "不要用 bash 直接改写代码文件",
        "确需例外时说明原因",
    )
