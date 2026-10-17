"""prompts_export_main_empty.md「强制禁止用 sed -n 替代 read_file」规则回归测试。

需求（2026-10-18）：读取文件内容一律使用内部工具 read_file，
强制禁止用 bash 的 sed -n 行号/模式范围读取替代 read_file。
"""

from __future__ import annotations

import pytest

from tests._paths import prompts_dir

MAIN_PROMPT = prompts_dir() / "prompts_export_main_empty.md"

RULE_KEY = "强制禁止用 `sed -n` 替代 `read_file`"
INTERNAL_TOOL_KEY = "强制用内部工具实现所有"
FULL_READ_KEY = "强制首次读取文件须读完整全文"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_line(main_prompt_text: str) -> str:
    line = next(
        (ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and RULE_KEY in ln),
        "",
    )
    assert line, f"缺少约束条目: {RULE_KEY}"
    return line


@pytest.fixture(scope="module")
def internal_tool_line(main_prompt_text: str) -> str:
    line = next(
        (ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and INTERNAL_TOOL_KEY in ln),
        "",
    )
    assert line, f"缺少约束条目: {INTERNAL_TOOL_KEY}"
    return line


@pytest.fixture(scope="module")
def full_read_line(main_prompt_text: str) -> str:
    line = next(
        (ln for ln in main_prompt_text.splitlines() if ln.startswith("- ") and FULL_READ_KEY in ln),
        "",
    )
    assert line, f"缺少约束条目: {FULL_READ_KEY}"
    return line


class TestMainNoSedNReplaceReadFile:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_rule_present(self, main_prompt_text: str):
        """独立约束条目须存在。"""
        assert RULE_KEY in main_prompt_text, f"缺少约束: {RULE_KEY}"

    def test_rule_is_red_line(self, rule_line: str):
        """约束须标注「红线 · 一票否决」。"""
        assert "（红线 · 一票否决）" in rule_line

    def test_rule_names_both_sides(self, rule_line: str):
        """约束须同时点名 sed -n 与 read_file。"""
        assert "`sed -n`" in rule_line
        assert "`read_file`" in rule_line

    def test_rule_bans_replacement(self, rule_line: str):
        """约束须明确禁止用 sed -n 替代 read_file。"""
        assert "强制禁止用 bash 的 `sed -n`" in rule_line
        assert "替代 `read_file`" in rule_line

    def test_rule_names_range_forms(self, rule_line: str):
        """约束须点名行号范围与模式范围两种常见写法。"""
        for expr in ("sed -n '1,100p'", "sed -n '10,20p'", "sed -n '/关键字/p'"):
            assert expr in rule_line, f"应点名禁止写法 {expr}"

    def test_rule_blocks_excuses(self, rule_line: str):
        """约束须封堵以片段/上下文/文件大小等理由绕过 read_file。"""
        for word in ("只读片段", "节省上下文", "文件太大", "日志文件", "只确认一下"):
            assert word in rule_line, f"应禁止以「{word}」为借口使用 sed -n"

    def test_rule_requires_ranged_read_file(self, rule_line: str):
        """大文件与日志文件限行读取也必须使用 read_file 的 start_line/end_line。"""
        assert "大文件" in rule_line
        assert "日志文件" in rule_line
        assert "`start_line`/`end_line`" in rule_line
        assert "不得改用 `sed -n`" in rule_line

    def test_rule_gives_reason_and_veto(self, rule_line: str):
        """约束须说明无沙盒/绕过完整读取的理由并声明一票否决。"""
        assert "无文件沙盒保护" in rule_line
        assert "绕过 `read_file` 的完整读取" in rule_line
        assert "一律视为一票否决" in rule_line

    def test_internal_tool_rule_mentions_sed_n(self, internal_tool_line: str):
        """内部工具条目须同步点名按行号范围读取禁止用 sed -n。"""
        assert "按行号范围读取同样必须用 `read_file`" in internal_tool_line
        assert "强制禁止用 `sed -n` 等 bash 方式替代" in internal_tool_line

    def test_full_read_rule_mentions_sed_n(self, full_read_line: str):
        """首次读取条目的大文件例外须禁止改用 sed -n。"""
        assert "`start_line`/`end_line`" in full_read_line
        assert "强制禁止改用 `sed -n` 等 bash 命令替代" in full_read_line

    def test_rule_placed_before_full_read_rule(self, main_prompt_text: str):
        """约束须紧邻读取类规则，位于首次完整读取条目之前。"""
        assert main_prompt_text.index(RULE_KEY) < main_prompt_text.index(FULL_READ_KEY)

    def test_existing_rules_untouched(self, main_prompt_text: str):
        """其余关键约束未被误删或改写。"""
        assert main_prompt_text.startswith("你是一位乐于助人的软件工程师助手。")
        assert "# 全局约束" in main_prompt_text
        assert INTERNAL_TOOL_KEY in main_prompt_text
        assert FULL_READ_KEY in main_prompt_text
        assert "图片强制用 `read_image` 读取" in main_prompt_text
        assert "只能使用相对路径（红线 · 一票否决）" in main_prompt_text

    def test_no_duplicate_entries(self, main_prompt_text: str):
        """新增条目后仍无重复条目行。"""
        entries = [ln for ln in main_prompt_text.splitlines() if ln.startswith("- ")]
        assert len(entries) == len(set(entries)), "存在重复条目行"

    def test_only_empty_version_changed(self):
        """本次仅修改 empty 版本，其它提示词文件不受影响。"""
        base = MAIN_PROMPT.parent
        for other in ("prompts_export_main.md", "prompts_export_main_simple.md"):
            content = (base / other).read_text(encoding="utf-8")
            assert RULE_KEY not in content, f"{other} 不应包含本次新增约束"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
