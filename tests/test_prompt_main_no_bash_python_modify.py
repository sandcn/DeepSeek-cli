"""空模式提词：bash 的职责边界不包含改写代码。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_bash_scope_excludes_code_writes():
    text = read_prompt()
    assert_contains(
        text,
        "bash 仅用于构建、只读安装依赖、运行测试、git、进程和系统信息查询",
        "不要用 bash 直接改写代码文件",
    )
