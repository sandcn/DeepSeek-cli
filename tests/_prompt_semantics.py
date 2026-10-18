"""提示词语义测试的共享读取助手。

提示词回归测试只断言语义关键词与结构，不再逐字锁定整段文案，
以便在不丢失约束意图的前提下调整措辞。
"""

from __future__ import annotations

from tests._paths import prompts_dir

_PROMPT = prompts_dir() / "prompts_export_main_empty.md"


def read_prompt() -> str:
    """读取空模式系统提词（缺失时直接失败）。"""
    assert _PROMPT.is_file(), f"缺少提示词文件: {_PROMPT}"
    return _PROMPT.read_text(encoding="utf-8")


def prompt_bullets() -> list[str]:
    """提词中的条目行（以 ``- `` 开头）。"""
    return [line for line in read_prompt().splitlines() if line.startswith("- ")]


def assert_contains(text: str, *tokens: str) -> None:
    """断言文本包含全部关键词（一次给出完整缺失列表）。"""
    missing = [token for token in tokens if token not in text]
    assert not missing, f"提示词缺少关键词: {missing}"
