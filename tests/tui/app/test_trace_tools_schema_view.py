"""轨迹工具列表详情视图 — 工具 schema 描述渲染回归测试。

覆盖（2026-10-23 用户需求：tool 系统提词详细化后）：
- ``_tools_schema_list()`` 与各工具 ``to_tool_schema()`` 的描述一致；
- 每个工具的描述都能经 ``_tools_inspector_content_rows`` 按右栏宽度换行渲染，
  不抛异常、不丢字符、不触发「内容过长」截断；
- 描述变长后仍能正确换行（最长描述至少拆成多行）；
- 描述内的 ``\\n`` 由渲染层拆成独立行（``bash_opt`` 逐 op 用法例 / 反例），
  除结构化换行外不得丢字符。
"""

from __future__ import annotations

from src.tools.registry import discover_builtin_tools
from src.tui.app.trace import _tools_schema_list
from src.tui.app.trace_tools_view import _tools_inspector_content_rows


def test_tools_schema_list_matches_tool_schemas() -> None:
    classes = discover_builtin_tools()
    listed = {name: (props, required, desc) for name, props, required, desc in _tools_schema_list()}

    assert len(listed) == len(classes)
    for name, cls in classes.items():
        assert name in listed
        schema = cls.to_tool_schema()["function"]
        props, required, desc = listed[name]
        assert desc == schema["description"]
        assert props == schema["parameters"]["properties"]
        assert required == list(schema["parameters"].get("required") or [])


def test_every_description_renders_and_keeps_all_text() -> None:
    right_w = 60
    for name, props, required, desc in _tools_schema_list():
        rows, keys = _tools_inspector_content_rows(
            name, props, required, desc, right_w,
        )
        assert rows, f"{name}: 检查器内容为空"
        assert len(rows) == len(keys)

        desc_rows = []
        for row in rows:
            if isinstance(row, str):
                desc_rows.append(row)
            else:
                break
        rendered = "".join(desc_rows)
        # 描述内的 \n 是渲染层的强制换行（拆成多行 str），比较时排除该
        # 结构化字符；其余字符一个都不能少。
        assert rendered == desc.replace("\n", ""), f"{name}: 描述渲染丢字符"
        assert len(desc_rows) >= desc.count("\n") + 1, f"{name}: 换行未拆行"
        assert all("内容过长" not in row for row in desc_rows)


def test_long_description_wraps_for_narrow_pane() -> None:
    listed = {name: desc for name, _props, _required, desc in _tools_schema_list()}
    name = max(listed, key=lambda key: len(listed[key]))
    props, required, desc = next(
        (p, r, d) for n, p, r, d in _tools_schema_list() if n == name
    )
    rows, _keys = _tools_inspector_content_rows(name, props, required, desc, 40)
    desc_lines = [row for row in rows if isinstance(row, str)]
    assert len(desc_lines) > 1, f"{name}: 窄栏下描述未换行"
