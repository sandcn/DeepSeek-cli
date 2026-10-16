"""帮助视图搜索过滤 + 分组折叠单元测试（2026-10-07 帮助界面增强）。

覆盖：
  - ``_help_sections`` 结构化行段（类型/分组/纯文本/runs）；
  - ``_help_rows`` 与 ``_help_sections`` 同源；
  - ``_visible_sections`` 折叠（隐藏组内命令，保留组标题）与搜索过滤；
  - ``_highlight_matches`` 关键词高亮（保序、非破坏）。
"""

from __future__ import annotations

import pytest

from src.tui.app.help_view import (
    _help_rows,
    _help_sections,
    _highlight_matches,
    _visible_sections,
)


def _register_all_commands():
    import src.core.commands._config_cmd  # noqa: F401
    import src.core.commands._data_cmd  # noqa: F401
    import src.core.commands._export_cmd  # noqa: F401
    import src.core.commands._plugin_cmd  # noqa: F401
    import src.core.commands._session_cmd  # noqa: F401
    import src.core.commands.plugins.deitmsg_plugin  # noqa: F401
    import src.core.commands.plugins.editmsg_plugin  # noqa: F401
    import src.core.commands.plugins.loop_plugin  # noqa: F401
    import src.core.commands.plugins.model_plugin  # noqa: F401
    import src.core.commands.plugins.skill_plugin  # noqa: F401
    from src.core.commands.base import (
        command_registry_singleton,
        declared_command_names,
        declared_command_plugin,
    )

    reg = command_registry_singleton()
    for name in declared_command_names():
        reg.register(declared_command_plugin(name))
    return reg


@pytest.fixture(autouse=True)
def _commands():
    _register_all_commands()


def test_sections_structure():
    sections = _help_sections(80)
    kinds = {sec["kind"] for sec in sections}
    assert {"spacer", "group_header", "cmd", "shortcut"} <= kinds
    assert any(sec["text"] == "会话" for sec in sections)
    assert all(isinstance(sec["runs"], list) for sec in sections)


def test_help_rows_same_source_as_sections():
    rows = _help_rows(80)
    texts_rows = [[r.text for r in row] for row in rows]
    texts_secs = [[r.text for r in sec["runs"]] for sec in _help_sections(80)]
    assert texts_rows == texts_secs


def test_visible_sections_collapse_hides_commands():
    sections = _help_sections(80)
    target = next(sec for sec in sections if sec["kind"] == "cmd" and sec["group"])
    group = target["group"]
    visible = _visible_sections(sections, frozenset({group}), "")
    assert not any(
        sec["kind"] == "cmd" and sec["group"] == group for sec in visible
    ), "折叠组的命令行应隐藏"
    assert any(
        sec["kind"] == "group_header" and sec["group"] == group for sec in visible
    ), "折叠后组标题仍显示"


def test_visible_sections_search_filters():
    sections = _help_sections(80)
    visible = _visible_sections(sections, frozenset(), "model")
    assert visible
    assert all("model" in sec["text"].lower() for sec in visible)


def test_visible_sections_search_ignores_collapse():
    sections = _help_sections(80)
    visible = _visible_sections(sections, frozenset({"model", "session"}), "help")
    assert any("help" in sec["text"].lower() for sec in visible)


def test_collapsed_header_marker_switches():
    from src.tui.app._welcome import display_width

    sections = _help_sections(80)
    target = next(s for s in sections if s["kind"] == "group_header" and s["group"])
    group = target["group"]
    # 未折叠：▾
    visible = _visible_sections(sections, frozenset(), "")
    header = next(
        s for s in visible if s["kind"] == "group_header" and s["group"] == group
    )
    assert "\u25be" in "".join(r.text for r in header["runs"])
    # 折叠：▸，且行宽不变（行宽不变量）
    visible = _visible_sections(sections, frozenset({group}), "")
    header = next(
        s for s in visible if s["kind"] == "group_header" and s["group"] == group
    )
    text = "".join(r.text for r in header["runs"])
    assert "\u25b8" in text and "\u25be" not in text
    assert sum(display_width(r.text) for r in header["runs"]) <= 80
    # 原 sections 缓存未被污染
    assert "\u25be" in "".join(r.text for r in target["runs"])


def test_highlight_matches_preserves_text():
    from src.tui.core.style import Style
    from src.tui.ink import StyledRun

    runs = [StyledRun("/model 切换模型 · /model", Style(fg=45))]
    out = _highlight_matches(runs, "model")
    assert "".join(r.text for r in out) == "/model 切换模型 · /model"
    assert any(r.style is not None and r.style.bg == 221 for r in out)


def test_highlight_matches_empty_query_same_object():
    from src.tui.ink import StyledRun

    runs = [StyledRun("abc", None)]
    assert _highlight_matches(runs, "") is runs
