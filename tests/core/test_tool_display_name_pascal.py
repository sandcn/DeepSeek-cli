"""工具显示名 = 工具注册名 PascalCase（2026-09-18 用户需求）回归测试。

需求：工具卡标题行 / 子代理工具记录行 / 解析进度行显示工具**真实注册名**
（``Read engine/rendering/FrameGraph.cpp``），不再用 Claude Code 风格缩写
（``Task`` / ``RM`` 等）。

链路：工具注册名 → ``TOOL_DISPLAY_NAME``（唯一真源）
  → ``registry.get_tool_display_name`` → 消费方
  （``tui/app/toolcard.py`` 标题行、``_tool_output_mixin.open_tool_box``、
   ``_subagent_render.format_tool_record``、``api/stream_parse.py`` 进度行、
   ``core/internal/agent/_tool_callbacks.py`` 通知汇总）。

覆盖：
  1. 真实工具显示名 == 注册名 PascalCase（参数化全量；read_image 为
     DSH presentCall 对齐的显式例外 ``Read image``）；
  2. 映射表覆盖全部注册工具；
  3. 历史兼容别名按 PascalCase 显示；
  4. 未知工具回退原名；
  5. 端到端：工具卡标题行 / 子代理记录行显示 Read（非旧缩写）。
"""

from __future__ import annotations

import pytest

from src.tools._constants import TOOL_DISPLAY_NAME
from src.tools.registry import get_tool_display_name, get_tools

#: 旧 Claude Code 风格缩写（真实工具不得再使用这些显示名）
_OLD_ABBREVS = {"Task", "RM", "MV", "CP", "LS"}

#: 显式例外：read_image 对齐 DSH presentCall 标题 ``Read image <path>``。
_DISPLAY_EXCEPTIONS = {"read_image": "Read image"}


def _pascal(name: str) -> str:
    """snake_case → PascalCase（工具注册名的展示形态）。"""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def _real_tools() -> list[str]:
    return sorted(get_tools().keys())


def test_tool_registry_not_empty():
    """注册表非空（否则映射校验失去意义）。"""
    assert _real_tools(), "工具注册表为空，无法校验显示名映射"


@pytest.mark.parametrize("tool_name", _real_tools())
def test_real_tool_display_name_is_registered_name_pascal(tool_name):
    """真实工具：显示名 == 工具注册名 PascalCase（read_image 为 DSH 例外）。"""
    expected = _DISPLAY_EXCEPTIONS.get(tool_name, _pascal(tool_name))
    assert get_tool_display_name(tool_name) == expected


def test_mapping_covers_all_registered_tools():
    """映射表覆盖全部注册工具（新增工具漏加映射立即失败）。"""
    missing = sorted(n for n in _real_tools() if n not in TOOL_DISPLAY_NAME)
    assert not missing, f"注册工具缺少显示名映射: {missing}"


@pytest.mark.parametrize("tool_name,expected", [
    ("read", "Read"),
    ("read_image", "Read image"),
    ("write", "Write"),
    ("edit", "Edit"),
    ("bash", "Bash"),
    ("bash_opt", "BashOpt"),
    ("subagent", "Subagent"),
    ("subagent_opt", "SubagentOpt"),
    ("glob", "Glob"),
    ("grep", "Grep"),
    ("cp", "Cp"),
    ("mv", "Mv"),
    ("rm", "Rm"),
    ("mkdir", "Mkdir"),
    ("ls", "Ls"),
    ("user_select", "UserSelect"),
    ("web_search", "WebSearch"),
    ("web_fetch", "WebFetch"),
    ("skill", "Skill"),
])
def test_known_display_names(tool_name, expected):
    """关键工具显示名逐项固定（防回归为 Claude Code 缩写）。"""
    assert TOOL_DISPLAY_NAME[tool_name] == expected
    assert get_tool_display_name(tool_name) == expected


@pytest.mark.parametrize("alias,expected", [
    ("str_replace_editor", "StrReplaceEditor"),
    ("file_editor", "FileEditor"),
    ("execute_command", "ExecuteCommand"),
])
def test_alias_display_names_are_pascal(alias, expected):
    """历史兼容别名（非真实工具）同样按 PascalCase 显示。"""
    assert get_tool_display_name(alias) == expected


def test_real_tools_no_longer_use_old_abbrev():
    """真实工具的显示名中不得残留旧缩写。"""
    shown = {get_tool_display_name(n) for n in _real_tools()}
    overlap = sorted(shown & _OLD_ABBREVS)
    assert not overlap, f"仍在使用旧缩写: {overlap}"


def test_unknown_tool_falls_back_to_raw_name():
    """未知工具无映射 → 原样返回（不抛异常、不臆造名）。"""
    assert get_tool_display_name("todo_write") == "todo_write"


# ── 端到端：工具卡标题行 / 子代理记录行 ──────────────────

def test_tool_card_title_shows_read():
    """工具卡标题行显示 ``● Read <path>``（注册名 PascalCase）。"""
    from src.tui.app.model import AppModel
    from src.tui.app.toolcard import tool_card_lines

    model = AppModel()
    model.width = 80
    model.open_tool_box("t1", "read", "engine/rendering/FrameGraph.cpp")
    block = model.tool_boxes["t1"]
    title = "".join(r.text for r in tool_card_lines(block, 80)[0])
    assert "Read" in title
    assert "engine/rendering/FrameGraph.cpp" in title
    assert "ReadFile" not in title


def test_tool_card_title_shows_read_image():
    """read_image 工具卡标题对齐 DSH：``Read image <path>``。"""
    from src.tui.app.model import AppModel
    from src.tui.app.toolcard import tool_card_lines

    model = AppModel()
    model.width = 80
    model.open_tool_box("t2", "read_image", "test_img/tiny_1x1.png")
    block = model.tool_boxes["t2"]
    title = "".join(r.text for r in tool_card_lines(block, 80)[0])
    assert "Read image test_img/tiny_1x1.png" in title
    assert "ReadImage" not in title


def test_subagent_tool_record_shows_read():
    """子代理面板工具记录行显示 ``Read``。"""
    from src.tui._subagent_state import _ToolRecord
    from src.tui._subagent_render import format_tool_record

    rec = _ToolRecord("read", "engine/rendering/FrameGraph.cpp", "tc-1")
    rec.phase = "done"
    rec.end_time = rec.start_time + 0.3
    text = "".join(r.text for r in format_tool_record(rec, rec.end_time).runs)
    assert "Read" in text
    assert "engine/rendering/FrameGraph.cpp" in text


@pytest.mark.parametrize("tool_name,expected", [
    ("grep", "Grep"),
    ("rm", "Rm"),
    ("subagent", "Subagent"),
    ("write", "Write"),
])
def test_subagent_tool_record_uses_registered_name(tool_name, expected):
    """grep / rm / subagent / write 记录行显示注册名 PascalCase。"""
    from src.tui._subagent_state import _ToolRecord
    from src.tui._subagent_render import format_tool_record

    rec = _ToolRecord(tool_name, "", "tc-x")
    rec.phase = "done"
    rec.end_time = rec.start_time
    text = "".join(r.text for r in format_tool_record(rec, rec.end_time).runs)
    assert expected in text, text
