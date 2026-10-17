"""prompts_export_main_empty.md「强制禁止用 bash 加 python 修改代码文件」回归测试。

需求（2026-10-18）：全局约束新增规则，禁止以 bash 为入口调用 Python
解释器（含 -c/heredoc/脚本文件/-m 等形式）修改任何代码文件，修改代码
只能使用内部工具 update_file 与 write_file。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainNoBashPythonModifyRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_forbids_bash_python_modify(self, main_prompt_text: str):
        """提词须含「强制禁止用 bash 加 python 修改代码文件」。"""
        assert "强制禁止用 bash 加 python 修改代码文件" in main_prompt_text

    def test_prompt_marks_rule_as_red_line(self, main_prompt_text: str):
        """该规则须为「红线 · 一票否决」级别。"""
        line = next(
            (ln for ln in main_prompt_text.splitlines() if "强制禁止用 bash 加 python 修改代码文件" in ln),
            "",
        )
        assert "（红线 · 一票否决）" in line

    def test_prompt_bans_inline_python(self, main_prompt_text: str):
        """须点名禁止内联执行形式。"""
        for expr in ("python -c", "python3 -c", "py -c", "python -m"):
            assert expr in main_prompt_text, f"应禁止 {expr}"

    def test_prompt_bans_heredoc_and_script_file(self, main_prompt_text: str):
        """须点名禁止 heredoc 与脚本文件形式。"""
        assert "heredoc" in main_prompt_text
        assert "python script.py" in main_prompt_text
        assert "python3 script.py" in main_prompt_text

    def test_prompt_bans_bash_entry(self, main_prompt_text: str):
        """须点名禁止以 bash 为入口调用 Python。"""
        assert "bash -c 'python" in main_prompt_text
        assert "重定向" in main_prompt_text

    def test_prompt_bans_python_file_ops(self, main_prompt_text: str):
        """须点名禁止 Python 文件写入操作。"""
        for expr in ("open()", "write()", "write_text()", "shutil", "os.rename", "os.remove"):
            assert expr in main_prompt_text, f"应禁止 Python 文件操作 {expr}"

    def test_prompt_bans_mutation_targets(self, main_prompt_text: str):
        """须覆盖改写、追加、覆盖、删除、重命名、移动等改动形式。"""
        for word in ("追加", "覆盖", "删除", "重命名", "移动", "批量替换"):
            assert word in main_prompt_text, f"应禁止对代码文件的「{word}」操作"

    def test_prompt_requires_internal_tools(self, main_prompt_text: str):
        """须指明修改代码只能使用 update_file 与 write_file。"""
        assert "修改代码文件只能使用 `update_file`" in main_prompt_text
        assert "`write_file`" in main_prompt_text

    def test_prompt_allows_non_mutating_python(self, main_prompt_text: str):
        """须说明 Python 仅可用于运行、测试、构建与只读分析。"""
        assert "Python 仅允许用于运行程序、执行测试、构建与只读分析" in main_prompt_text

    def test_prompt_requires_write_file_for_generated_code(self, main_prompt_text: str):
        """须说明 Python 生成的代码内容只能交给 write_file 落盘。"""
        assert "只能把生成结果交给 `write_file` 落盘" in main_prompt_text

    def test_prompt_veto_on_violation(self, main_prompt_text: str):
        """须声明通过 bash + Python 改代码一律一票否决。"""
        assert "一律视为一票否决" in main_prompt_text

    def test_prompt_keeps_neighbor_rules(self, main_prompt_text: str):
        """不得破坏相邻原有约束。"""
        assert "强制用内部工具实现所有（红线 · 一票否决）" in main_prompt_text
        assert "强制只能用 update_file 和 write_file 修改代码" in main_prompt_text
        assert "强制禁止用 `sed -n` 替代 `read_file`（红线 · 一票否决）" in main_prompt_text


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
