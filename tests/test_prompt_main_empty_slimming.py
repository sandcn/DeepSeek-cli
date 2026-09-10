"""prompts_export_main_empty.md 精简后规则完整性回归测试。

需求（2026-09-11）：精简该提示词（压缩表述、合并同类条目），
但一条全局约束都不得丢失；本测试逐条断言各约束的关键短语仍在。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"

REQUIRED_SNIPPETS = [
    "强势禁止因为难就不做",
    "一次性输出全部计划",
    "纯中文输出",
    "生成代码少注释",
    "禁止采信代码的任何注释",
    "红线 · 零豁免",
    "禁止遗漏、简化、打折、只做部分或选择性实现",
    "验收",
    "不行再换方法",
    "任务宏大",
    "user_select",
    "默认全选",
    "禁止调用 subagent",
    "git/svn",
    "plan 与 execute agent 强制串行",
    "先读够/查够代码再动手",
    "强制改底层",
    "全部引用、调用方、被调用方及依赖",
    "递归获取完整目录结构",
    "完整调用链",
    "工作量太大",
    "./tests",
    "临时修改且立即使用",
    "禁止未完成就提前检查",
    "P0~P3",
    "read_file",
    "read_image",
    "强制用内部工具实现所有（红线 · 一票否决）",
    "只能使用相对路径（红线 · 一票否决）",
    "元文件保护",
    "rm -rf",
    "mkfs",
    "chmod 777",
    "sudo",
    "chown",
    "skill",
]

METAFILES = ["global.md", "main.md", "plan.md", "think.md", "map.md", "review.md", "execute.md"]


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestMainEmptyPromptSlimmedRules:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    @pytest.mark.parametrize("snippet", REQUIRED_SNIPPETS)
    def test_rule_snippet_kept(self, main_prompt_text: str, snippet: str):
        """精简后每条全局约束的关键短语仍须保留。"""
        assert snippet in main_prompt_text, f"精简后丢失规则关键短语: {snippet}"

    @pytest.mark.parametrize("meta", METAFILES)
    def test_meta_files_protected(self, main_prompt_text: str, meta: str):
        """元文件保护清单须完整列出 7 个运行时元文件。"""
        assert meta in main_prompt_text, f"元文件保护清单缺少 {meta}"

    def test_no_duplicated_lines(self, main_prompt_text: str):
        """精简后不应出现重复条目。"""
        lines = [ln.strip() for ln in main_prompt_text.splitlines() if ln.strip()]
        assert lines, "提示词不应为空"
        assert len(lines) == len(set(lines)), "存在重复条目"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
