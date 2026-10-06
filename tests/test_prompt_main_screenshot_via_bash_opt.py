"""prompts_export_main_empty.md「截图用 bash_opt 实现」规则回归测试。

需求（2026-10-07）：在空模式提词中明确截图可以用 bash_opt 实现
（op=screenshot 截取后台命令进程树的窗口并保存为 PNG，需 path 参数），
截图产出后仍用 read_image 读取查看，与「图片强制用 read_image 读取」
的读图约束不冲突。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "prompts_export_main_empty.md"


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    assert MAIN_PROMPT.exists(), f"缺少文件: {MAIN_PROMPT}"
    return MAIN_PROMPT.read_text(encoding="utf-8")


class TestScreenshotViaBashOptRule:

    def test_prompt_exists(self):
        """提示词文件存在。"""
        assert MAIN_PROMPT.exists()

    def test_prompt_mentions_screenshot(self, main_prompt_text: str):
        """提词须提及截图。"""
        assert "截图" in main_prompt_text

    def test_prompt_mentions_bash_opt(self, main_prompt_text: str):
        """提词须点名 bash_opt 工具。"""
        assert "bash_opt" in main_prompt_text

    def test_prompt_mentions_screenshot_op(self, main_prompt_text: str):
        """提词须点名 op=screenshot 操作。"""
        assert "op=screenshot" in main_prompt_text

    def test_prompt_mentions_png_and_path(self, main_prompt_text: str):
        """提词须说明截图保存为 PNG 且需要 path 参数。"""
        assert "PNG" in main_prompt_text
        assert "path 参数" in main_prompt_text

    def test_screenshot_clause_on_single_line(self, main_prompt_text: str):
        """截图用 bash_opt 的约定须落在同一行，避免语义脱离。"""
        line = next(
            (ln for ln in main_prompt_text.splitlines() if "bash_opt" in ln and "截图" in ln),
            "",
        )
        assert "op=screenshot" in line

    def test_read_image_rule_retained(self, main_prompt_text: str):
        """读图仍须用 read_image，与截图实现方式不冲突。"""
        assert "必须强制用 `read_image` 工具读取并验证正确性" in main_prompt_text
        assert "强制禁止用 bash 读取/查看/解析图片" in main_prompt_text

    def test_screenshot_clause_references_read_image(self, main_prompt_text: str):
        """截图条目须点明产出后用 read_image 读取查看。"""
        line = next(
            (ln for ln in main_prompt_text.splitlines() if "截图用 `bash_opt` 实现" in ln),
            "",
        )
        assert "read_image" in line

    def test_screenshot_clause_documents_crop(self, main_prompt_text: str):
        """截图条目须说明 crop 参数可指定只截取的像素区域（指定大小）。"""
        line = next(
            (ln for ln in main_prompt_text.splitlines() if "截图用 `bash_opt` 实现" in ln),
            "",
        )
        assert "crop" in line
        assert "x,y,width,height" in line

    def test_kept_constraints_untouched(self, main_prompt_text: str):
        """其余全局约束不受影响，未被误删。"""
        assert "你是一位乐于助人的软件工程师助手。" in main_prompt_text
        assert "# 全局约束" in main_prompt_text
        assert "思考跟回答强制简体中文输出" in main_prompt_text
        assert "禁止采信代码的任何注释" in main_prompt_text
        assert "plan execute agent 强制串行" in main_prompt_text

    def test_simple_prompt_not_touched(self):
        """本次仅修改 empty 版本，simple 版本不受影响。"""
        simple = MAIN_PROMPT.parent / "prompts_export_main_simple.md"
        assert simple.exists()
        assert "op=screenshot" not in simple.read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
