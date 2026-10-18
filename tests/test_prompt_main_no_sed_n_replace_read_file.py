"""空模式提词：读取文件只用 read_file，不用 bash 文本工具替代。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_read_file_is_the_only_reader():
    text = read_prompt()
    assert_contains(
        text,
        "读取用 `read_file`",
        "不要用 bash 的 `cat`/`grep`/`sed`/`echo` 等替代这些工具",
    )


def test_bash_scope_is_explicit():
    assert "bash 仅用于构建、安装依赖、运行测试、git、进程和系统信息查询" in read_prompt()
