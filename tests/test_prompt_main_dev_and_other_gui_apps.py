"""空模式提词：GUI 运行与操作统一走 bash_opt。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_gui_operations_use_bash_opt():
    text = read_prompt()
    assert_contains(
        text,
        "GUI 操作",
        "`bash_opt`",
        "op=windows",
        "op=click",
        "op=type",
        "窗口截图左上角",
    )
