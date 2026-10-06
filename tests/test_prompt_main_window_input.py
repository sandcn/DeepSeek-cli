"""prompts_export_main_empty.md「操作 GUI 窗口用 bash_opt 输入 op」规则回归测试。

需求（2026-10-07）：提词须告知可用 `bash_opt` 向后台命令的 GUI 窗口注入
鼠标点击（含右击/双击）、拖动、滚动、按键与文本输入，并说明坐标以窗口
截图左上角为原点、可用 screenshot + read_image 对照。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAIN_PROMPT = Path("prompts/prompts_export_main_empty.md")


@pytest.fixture(scope="module")
def main_prompt_text() -> str:
    return MAIN_PROMPT.read_text(encoding="utf-8")


def test_prompt_mentions_window_input_rule(main_prompt_text: str):
    """提词含窗口输入规则行（同一行内点名 bash_opt 与输入 op）。"""
    lines = [
        line for line in main_prompt_text.splitlines()
        if "bash_opt" in line and "op=click" in line
    ]
    assert lines, "提词缺少「操作 GUI 窗口用 bash_opt 输入 op」规则"


def test_prompt_lists_all_input_ops(main_prompt_text: str):
    """提词覆盖全部输入 op（点击/移动/拖动/滚动/按键/文本）。"""
    line = next(ln for ln in main_prompt_text.splitlines()
                if "bash_opt" in ln and "op=click" in ln)
    for token in ("op=click", "op=move", "op=drag", "op=scroll",
                  "op=key", "op=type"):
        assert token in line, f"提词缺少 {token}"


def test_prompt_mentions_mouse_buttons_and_double_click(main_prompt_text: str):
    """提词说明按钮取值与双击（右击能力）。"""
    line = next(ln for ln in main_prompt_text.splitlines()
                if "bash_opt" in ln and "op=click" in ln)
    assert "button=left/right/middle" in line
    assert "双击" in line


def test_prompt_mentions_coordinate_semantics(main_prompt_text: str):
    """提词说明坐标原点与截图一致，且可用 screenshot + read_image 对照。"""
    line = next(ln for ln in main_prompt_text.splitlines()
                if "bash_opt" in ln and "op=click" in ln)
    assert "窗口截图左上角" in line
    assert "screenshot" in line
    assert "read_image" in line
