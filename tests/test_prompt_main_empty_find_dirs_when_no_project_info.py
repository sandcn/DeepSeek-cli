"""空模式提词：陌生项目先建立目录与调用链认知。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_unknown_project_explores_directories_first():
    text = read_prompt()
    assert_contains(
        text,
        "陌生项目先用 `find` 了解目录结构",
        "`search`/`find`",
        "影响范围",
        "禁止凭猜测修改",
    )
