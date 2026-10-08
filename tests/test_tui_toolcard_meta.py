"""工具卡标题元信息单元测试（2026-10-07 工具卡显示增强）。

覆盖：
  - ``_tool_result_line_count``（body 数据行 + 省略行，不含标题/状态行）；
  - ``_tool_meta_runs``：完成显示耗时/行数、失败显示 ``· 失败``、运行中为空；
  - ``tool_card_lines`` 标题行包含元信息（且运行中保持极简）。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.toolcard import (
    _tool_meta_runs,
    _tool_result_line_count,
    tool_card_lines,
)


def _closed_tool(success: bool = True, lines: int = 5, duration: float = 1.25):
    m = AppModel()
    m.open_tool_box("t1", "bash", "ls -la")
    if lines:
        m.append_tool_output("t1", "\n".join(f"line {i}" for i in range(lines)))
    m.close_tool_box("t1", success)
    block = m.blocks[-1]
    block.extra["_tool_duration"] = duration
    return block


def _text(runs) -> str:
    return "".join(r.text for r in runs)


def test_result_line_count_excludes_title_and_status():
    block = _closed_tool(lines=5)
    count = _tool_result_line_count(block)
    # 5 行 body（标题数据行与 ✔ 状态行不计）
    assert count == 5


def test_result_line_count_zero_for_bodyless():
    block = _closed_tool(lines=0)
    assert _tool_result_line_count(block) == 0


def test_meta_runs_running_is_empty():
    m = AppModel()
    m.open_tool_box("t1", "bash", "ls -la")
    block = m.blocks[-1]
    assert _tool_meta_runs(block, True) == []


def test_meta_runs_done_shows_lines_without_duration():
    """2026-10-09 用户需求：完成后元信息不再显示耗时（``· 13.4s``）。

    最终运行时间已由标题前缀保留（``_tool_finished_prefix_text``）。
    """
    block = _closed_tool(success=True, lines=5, duration=1.25)
    text = _text(_tool_meta_runs(block, False))
    assert "1.2s" not in text
    assert "1.2" not in text
    assert "5 \u884c" in text


def test_meta_runs_single_line_has_no_meta():
    """单行结果无行数元信息 + 无耗时元信息 → 元信息为空。"""
    block = _closed_tool(success=True, lines=1, duration=0.01)
    assert _text(_tool_meta_runs(block, False)) == ""


def test_meta_runs_fail_marker():
    block = _closed_tool(success=False, lines=1, duration=0.5)
    text = _text(_tool_meta_runs(block, False))
    assert "\u5931\u8d25" in text


def test_tool_card_title_contains_meta_after_close():
    block = _closed_tool(success=True, lines=5, duration=2.5)
    lines = tool_card_lines(block, 80)
    title = _text(lines[0])
    # ★ 2026-10-09 用户需求：完成后 = ``✔ <最终运行时间> …``（前面的时间保留）
    assert title.startswith("\u2714 2.5")
    assert "2.5s" not in title          # 尾部不再重复耗时元信息
    assert "5 \u884c" in title
    assert "ls -la" in title


def test_tool_card_title_minimal_while_running():
    m = AppModel()
    m.open_tool_box("t1", "bash", "ls -la")
    m.append_tool_output("t1", "out")
    block = m.blocks[-1]
    title = _text(tool_card_lines(block, 80)[0])
    # ★ 2026-10-09 用户需求：运行中前缀 = 实时运行时间（替代 ● 图标）
    assert "\u25cf" not in title
    assert title.split(" ")[0].replace(".", "").isdigit()  # 如 ``0.0``
    assert "s" not in title.split("ls -la")[-1]  # 运行中无元信息
