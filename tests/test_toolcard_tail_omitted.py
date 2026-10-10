"""工具卡省略方向（2026-10-10 用户需求）回归测试。

需求：Search / Find 工具的 toolcard 省略提示由「后置『… 后 N 行省略』」改为
「前置『… 前 N 行省略』」——即两者由**头显示**（保留前 N 行）切换为
**尾显示**（保留后 N 行）。

链路：
  ``_TOOL_TAIL_TOOLS`` / ``ui_default("tool_tail_tools")`` 含 ``search`` / ``find``
  → ``append_tool_output`` 命中尾显示分支 → ``_trim_tool_output_tail``
  删除前置行并累计 ``block.extra["_bash_omitted_lines"]``
  → ``toolcard.tool_card_lines`` 前置「… 前 N 行省略」提示行（不含后置提示）。

同批固化：ls/read_file 仍为头显示（保留前 N 行 + 后置「… 后 N 行省略」），
bash 仍为尾显示，防本次改动误伤其它工具。
"""

from __future__ import annotations

from src.presentation_data import ui_default
from src.tui.app._model_helpers import (
    _TOOL_HEAD_TOOLS,
    _TOOL_TAIL_TOOLS,
    _tool_head_tools,
    _tool_tail_tools,
)
from src.tui.app.model import AppModel
from src.tui.app.toolcard import tool_card_lines

_WIDTH = 80


def _plain(rows) -> str:
    """把 tool_card_lines 的行结果拍平为纯文本（便于断言提示行）。"""
    return "\n".join("".join(r.text for r in row) for row in rows)


def _fill(model: AppModel, tool_id: str, segments: list[str]) -> None:
    """一次追加多行工具输出（按 ``\\n`` 连接，``append_tool_output`` 逐行处理）。"""
    model.append_tool_output(tool_id, "\n".join(segments))


# ── 配置层：search / find 归入尾显示工具集合 ───────────────


def test_search_find_in_tail_tools_not_head_tools():
    assert "search" in _TOOL_TAIL_TOOLS
    assert "find" in _TOOL_TAIL_TOOLS
    assert "search" not in _TOOL_HEAD_TOOLS
    assert "find" not in _TOOL_HEAD_TOOLS
    assert "search" in _tool_tail_tools()
    assert "find" in _tool_tail_tools()
    assert "search" not in _tool_head_tools()
    assert "find" not in _tool_head_tools()


def test_defaults_registry_matches_constants():
    assert tuple(ui_default("tool_tail_tools")) == _TOOL_TAIL_TOOLS
    assert tuple(ui_default("tool_head_tools")) == _TOOL_HEAD_TOOLS
    assert "bash" in _TOOL_TAIL_TOOLS
    assert "execute_command" in _TOOL_TAIL_TOOLS
    assert "find" in _TOOL_TAIL_TOOLS
    assert "ls" in _TOOL_HEAD_TOOLS
    assert "read_file" in _TOOL_HEAD_TOOLS


def test_tool_tail_tools_configurable(monkeypatch):
    """数据注册表可覆盖尾显示工具集合（「一切皆插件」）。"""
    import src.presentation_data as pd

    def _fake_ui_default(key, default=None):
        if key == "tool_tail_tools":
            return ["custom_tool"]
        return default

    monkeypatch.setattr(pd, "ui_default", _fake_ui_default)
    assert _tool_tail_tools() == ("custom_tool",)


# ── 行为层：search / find 输出保留尾行 + 累计前省略计数 ──────


def _tool_block(tool_name: str, detail: str, segments: list[str]):
    m = AppModel()
    m.width = _WIDTH
    m.open_tool_box("t1", tool_name, detail)
    _fill(m, "t1", segments)
    return m, m.tool_boxes["t1"]


def test_search_tail_trim_keeps_last_lines():
    segments = [f"row-{i}" for i in range(12)]
    _, block = _tool_block("search", "foo in:src", segments)
    # 标题行 + 保留最后 3 行
    assert len(block.lines) == 1 + 3
    assert block.extra.get("_bash_omitted_lines") == 12 - 3
    assert block.extra.get("_head_omitted_lines", 0) == 0
    kept = [line.plain.strip() for line in block.lines[1:]]
    assert kept == ["row-9", "row-10", "row-11"]


def test_find_tail_trim_keeps_last_lines():
    segments = [f"file-{i}.py" for i in range(10)]
    _, block = _tool_block("find", "*.py", segments)
    assert len(block.lines) == 1 + 3
    assert block.extra.get("_bash_omitted_lines") == 10 - 3
    assert block.extra.get("_head_omitted_lines", 0) == 0
    kept = [line.plain.strip() for line in block.lines[1:]]
    assert kept == ["file-7.py", "file-8.py", "file-9.py"]


def test_tail_trim_no_omit_below_threshold():
    _, block = _tool_block("search", "foo", ["only-1", "only-2"])
    assert len(block.lines) == 1 + 2
    assert block.extra.get("_bash_omitted_lines", 0) == 0
    assert block.extra.get("_head_omitted_lines", 0) == 0


def test_search_card_shows_leading_omitted_marker():
    segments = [f"row-{i}" for i in range(12)]
    _, block = _tool_block("search", "foo in:src", segments)
    rows = tool_card_lines(block, _WIDTH)
    text = _plain(rows)
    assert "… 前 9 行省略" in text
    assert "… 后" not in text
    # 省略提示行是卡片第二行（标题行之后、内容行之前）
    assert "行省略" in _plain(rows[1:2])
    # 保留的尾行仍在卡片中（顺序：标题 → 省略 → row-9/10/11）
    assert text.index("行省略") < text.index("row-9") < text.index("row-11")


def test_find_card_shows_leading_omitted_marker():
    segments = [f"file-{i}.py" for i in range(10)]
    _, block = _tool_block("find", "*.py", segments)
    text = _plain(tool_card_lines(block, _WIDTH))
    assert "… 前 7 行省略" in text
    assert "… 后" not in text
    assert "file-7.py" in text
    assert "file-9.py" in text
    assert "file-6.py" not in text


def test_search_card_shows_tail_content_order():
    segments = [f"row-{i}" for i in range(20)]
    _, block = _tool_block("search", "foo", segments)
    text = _plain(tool_card_lines(block, _WIDTH))
    assert "… 前 17 行省略" in text
    for i in (17, 18, 19):
        assert f"row-{i}" in text
    assert "row-16" not in text


# ── 防回归：其它工具省略方向不变 ─────────────────────────


def test_ls_keeps_head_omitted_marker():
    m = AppModel()
    m.width = _WIDTH
    m.open_tool_box("l1", "ls", "src")
    _fill(m, "l1", [f"entry-{i}" for i in range(8)])
    block = m.tool_boxes["l1"]
    assert block.extra.get("_head_omitted_lines") == 8 - 3
    assert block.extra.get("_bash_omitted_lines", 0) == 0
    text = _plain(tool_card_lines(block, _WIDTH))
    assert "… 后 5 行省略" in text
    assert "… 前" not in text


def test_read_file_keeps_head_omitted_marker():
    m = AppModel()
    m.width = _WIDTH
    m.open_tool_box("r1", "read_file", "src/main.py")
    _fill(m, "r1", [f"line-{i}" for i in range(9)])
    block = m.tool_boxes["r1"]
    assert block.extra.get("_head_omitted_lines") == 9 - 3
    assert block.extra.get("_bash_omitted_lines", 0) == 0
    text = _plain(tool_card_lines(block, _WIDTH))
    assert "… 后 6 行省略" in text
    assert "… 前" not in text
    assert "line-0" in text
    assert "line-8" not in text


def test_bash_still_tail_omitted():
    m = AppModel()
    m.width = _WIDTH
    m.open_tool_box("b1", "bash", "run")
    _fill(m, "b1", [f"out-{i}" for i in range(9)])
    block = m.tool_boxes["b1"]
    assert block.extra.get("_bash_omitted_lines") == 9 - 3
    text = _plain(tool_card_lines(block, _WIDTH))
    assert "… 前 6 行省略" in text
    assert "… 后" not in text
