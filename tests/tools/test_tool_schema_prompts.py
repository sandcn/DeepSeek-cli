"""工具 schema 提词（模型可见 description）回归测试。

覆盖：
- 全部内置工具 schema 可构建且 JSON 可序列化；
- 提词长度预算（功能描述 + 参数短语，防止回涨）；
- 每个工具给出 1-2 条单行 JSON 调用示例，示例参数名与 schema 一致；
- 关键调用约定（参数集合、必填项、enum、默认值）不因精简丢失；
- ``user_select`` 的 1 分钟空闲超时与 header 12 字符限制文案保留；
- ``bash_opt`` 的 op 清单在功能描述中给出；
- ``bash_opt`` 每个 op 的用法例 / 反例都渲染进模型可见描述（``OP_GUIDE``）。
"""

from __future__ import annotations

import json
import re

from src.tools._bash_opt_schema import OP_GUIDE, build_bash_opt_schema
from src.tools.registry import discover_builtin_tools

#: 当前内置工具数量（新增/删除内置工具时同步更新）
_EXPECTED_TOOL_COUNT = 19

#: 单工具 function.description 字符上限（含 1-2 条调用示例）
_DESC_LIMIT = 550

#: 个别工具的 description 预算（其余工具仍用 :data:`_DESC_LIMIT`）。
#: ``bash_opt`` 需逐 op 给出「用法例 + 反例」（15 个 op），且 GUI 窗口输入
#: op 的用法例统一演示「操作完成后按 path 自动截图」，正文自然更长；这是
#: 模型最容易用错 op 的工具，提词收益高于同等长度的其它描述。
_DESC_LIMITS = {"bash_opt": 2200}

#: 全部工具 function.description 总字符上限（示例化后留余量）
_TOTAL_DESC_LIMIT = 6800

#: 参数 description 字符上限（功能短语 + 默认值/边界/示例值）
_PARAM_DESC_LIMIT = 80


def _schemas():
    return {name: cls.to_tool_schema() for name, cls in discover_builtin_tools().items()}


def _param_props(schema):
    return schema["function"]["parameters"]["properties"]


def test_all_builtin_tool_schemas_build() -> None:
    schemas = _schemas()
    assert len(schemas) == _EXPECTED_TOOL_COUNT
    for name, schema in schemas.items():
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] == name
        assert function["description"].strip()
        assert function["parameters"]["type"] == "object"
        json.dumps(schema, ensure_ascii=False)


def test_description_length_budget() -> None:
    schemas = _schemas()
    total = 0
    for name, schema in schemas.items():
        length = len(schema["function"]["description"])
        limit = _DESC_LIMITS.get(name, _DESC_LIMIT)
        assert length <= limit, f"{name} 功能描述过长: {length} > {limit}"
        total += length
        for param, spec in _param_props(schema).items():
            desc = spec.get("description", "")
            assert len(desc) <= _PARAM_DESC_LIMIT, f"{name}.{param} 参数描述过长: {desc!r}"
    assert total <= _TOTAL_DESC_LIMIT, f"功能描述总长 {total} 超预算"


def test_every_tool_description_has_example() -> None:
    """每个工具 1-2 条单行 JSON 示例，且示例参数名存在于 schema。"""
    schemas = _schemas()
    for name, schema in schemas.items():
        function = schema["function"]
        description = function["description"]
        count = description.count('({"')
        assert 1 <= count <= 2, f"{name} 示例数量异常: {count}"
        assert "例：" in description, f"{name} 缺少「例：」引导"
        props = set(function["parameters"]["properties"])
        # 示例内嵌对象（如 user_select.options[]）允许出现被嵌套对象的键
        allowed = props | {"label", "description"}
        if name == "bash_opt":
            # GUI 流程示例跨工具：先用 bash(background=true) 启动程序，再操作 GUI。
            allowed |= {"command", "background"}
        keys = set(re.findall(r'"([a-z_]+)"\s*:', description))
        assert keys <= allowed, f"{name} 示例含未知参数: {sorted(keys - allowed)}"


def test_key_parameter_contracts() -> None:
    schemas = _schemas()

    read = schemas["read"]["function"]["parameters"]
    assert read["required"] == ["file_path"]
    assert set(read["properties"]) == {
        "file_path", "start_line", "end_line", "show_line_numbers",
    }

    edit = schemas["edit"]["function"]["parameters"]
    assert edit["required"] == ["file_path", "old_string", "new_string"]
    assert {"replace_all", "use_regex", "regex_flags"} <= set(edit["properties"])

    recursive = schemas["rm"]["function"]["parameters"]["properties"]["recursive"]
    assert recursive["default"] is False

    glob = schemas["glob"]["function"]["parameters"]["properties"]
    assert glob["depth"]["default"] == 0
    assert glob["type"]["enum"] == ["file", "dir"]

    subagent_type = schemas["subagent"]["function"]["parameters"]["properties"]["type"]
    assert subagent_type["enum"] == ["map", "review", "plan", "execute"]

    user_select = schemas["user_select"]["function"]["parameters"]
    assert user_select["required"] == ["title", "options"]
    assert "1 分钟没有任何操作" in schemas["user_select"]["function"]["description"]
    # ★ 2026-10-23（用户需求）：说明并入 options，删除 option_descriptions。
    props = user_select["properties"]
    assert "option_descriptions" not in props
    options_item = props["options"]["items"]
    assert options_item["type"] == "object"
    assert options_item["required"] == ["label"]
    assert "description" in options_item["properties"]


def test_bash_opt_schema_keeps_ops_and_op_list() -> None:
    """GUI 能力只保留光标操作、截图与键盘输入；其余 op 必须消失。"""
    schema = build_bash_opt_schema()
    function = schema["function"]
    assert function["parameters"]["required"] == ["op"]

    props = function["parameters"]["properties"]
    for key in ("task_id", "op", "timeout", "path", "window",
                "crop", "screen", "x", "y", "button", "count",
                "direction", "amount", "from_x", "from_y", "to_x", "to_y"):
        assert key in props
    # 已移除的 GUI 能力 / 调参项不得再暴露参数
    for key in ("allow_hidden", "actions", "element", "window_action",
                "clipboard_action", "template", "query", "macro", "times",
                "wait_for", "diff", "tolerance", "color", "boxes",
                "grid", "shot", "settle", "hold", "interval", "dwell",
                "duration", "steps", "method", "phase", "repeat",
                "relative_event", "hold_keys", "keys", "buttons",
                "dx", "dy", "modifiers"):
        assert key not in props

    enum = props["op"]["enum"]
    for op in (
        "read", "wait", "kill", "stdin", "close_stdin", "keys", "screenshot",
        "release", "click", "move", "hover", "drag", "scroll", "key", "type",
    ):
        assert op in enum
    # 已清理的 GUI op 不得再出现
    for op in ("windows", "window", "elements", "wait_window", "clipboard",
               "locate", "pixel", "annotate", "record", "replay", "sequence"):
        assert op not in enum

    # op 说明收敛为一句，op 清单放在功能描述中
    assert props["op"]["description"].startswith("要执行的操作")
    assert "见功能描述" in props["op"]["description"]
    description = function["description"]
    for op in ("read", "wait", "kill", "release", "click", "type"):
        assert op in description

    json.dumps(schema, ensure_ascii=False)


def test_bash_opt_op_guide_has_usage_and_counterexample_per_op() -> None:
    """每个 op 都要有可照抄的用法例与一条反例，并真正渲染进模型可见描述。"""
    schema = build_bash_opt_schema()
    function = schema["function"]
    enum = function["parameters"]["properties"]["op"]["enum"]
    description = function["description"]

    # 顺序与 op 枚举一致（模型按同一顺序阅读），且不遗漏任何 op
    assert list(OP_GUIDE) == list(enum)
    for op, guide in OP_GUIDE.items():
        assert guide["usage"].strip(), f"{op} 缺少用法例"
        assert guide["counter"].strip(), f"{op} 缺少反例"
        # 用法例必须是可直接照抄的 JSON 调用（含 op 自身）
        assert guide["usage"].lstrip().startswith("{"), f"{op} 用法例不是 JSON 调用"
        assert f'"op":"{op}"' in guide["usage"], f"{op} 用法例未包含自身 op"
        assert f"{op}：✓ {guide['usage']}；✗ {guide['counter']}" in description, op
    # 每个 op 恰好一行（标题里的「反例（✗）」不计入）
    assert description.count("；✗ ") == len(OP_GUIDE)


def test_bash_opt_gui_input_ops_document_auto_screenshot() -> None:
    """GUI 窗口输入 op 的用法例要演示 path（操作完成后自动截图）。"""
    schema = build_bash_opt_schema()
    props = schema["function"]["parameters"]["properties"]
    # path 参数同时服务 screenshot 与 GUI 输入 op，描述须讲清两种语义
    assert "op=screenshot" in props["path"]["description"]
    assert "自动截图" in props["path"]["description"]
    assert "自动截图" in schema["function"]["description"]

    for op in ("click", "move", "hover", "drag", "scroll", "key", "type"):
        assert '"path":"shot.png"' in OP_GUIDE[op]["usage"], f"{op} 用法例缺少 path"
        assert "截图" in OP_GUIDE[op]["usage"], f"{op} 用法例未说明截图"


def test_tool_selection_guidance_present() -> None:
    """每个工具提词都要给出「何时用 / 不要用什么替代」的可执行指引。"""
    schemas = _schemas()

    # 文件读写：禁止 shell 替代内部工具（本项目的核心纪律）
    assert "禁止用 bash cat/sed/head" in schemas["read"]["function"]["description"]
    assert "局部修改一律用本工具" in schemas["edit"]["function"]["description"]
    assert "局部修改用 edit" in schemas["write"]["function"]["description"]
    assert "禁止用 bash 读写或搜索文件" in schemas["bash"]["function"]["description"]
    assert "不要用 bash grep" in schemas["grep"]["function"]["description"]
    assert "不要用 bash find/ls" in schemas["glob"]["function"]["description"]
    assert "递归查找文件用 glob" in schemas["ls"]["function"]["description"]

    # 读图：不能用 shell 解析图片（本项目红线）
    assert "禁止用 bash base64/xxd/file 读图" in schemas["read_image"]["function"]["description"]

    # 破坏性/可撤销语义必须对模型可见
    for name in ("cp", "mv", "rm", "mkdir", "write"):
        assert "沙盒" in schemas[name]["function"]["description"], name

    # 后台任务：read 为增量读取、wait 超时任务继续
    bash_opt_desc = schemas["bash_opt"]["function"]["description"]
    assert "增量读取" in bash_opt_desc
    assert "任务继续运行" in bash_opt_desc
    # GUI 流程示例：bash 后台启动程序 → bash_opt 截图 / 键鼠操作 → wait 或 kill
    assert "GUI 流程例" in bash_opt_desc
    assert '"background": true' in bash_opt_desc
    assert "click/type" in bash_opt_desc
    assert "wait 或 kill" in bash_opt_desc

    # 子代理：prompt 自包含、kill 级联取消
    assert "自包含" in schemas["subagent"]["function"]["description"]
    assert "级联取消" in schemas["subagent_opt"]["function"]["description"]

    # 联网工具分工、技能加载约束、user_select 使用场景
    assert "web_search" in schemas["web_fetch"]["function"]["description"]
    assert "web_fetch" in schemas["web_search"]["function"]["description"]
    assert "auto_load" in schemas["skill"]["function"]["description"]
    assert "互斥方向选择" in schemas["user_select"]["function"]["description"]


def test_bash_opt_param_guide() -> None:
    """bash_opt 的关键参数要说明取值形态与默认值（而非只给一个名词）。"""
    props = _schemas()["bash_opt"]["function"]["parameters"]["properties"]

    assert "#N" in props["window"]["description"]
    assert "center" in props["x"]["description"]
    assert "x,y,width,height" in props["crop"]["description"]
    assert "true" in props["screen"]["description"]
    assert "双击" in props["count"]["description"]
    assert "left" in props["button"]["description"]
    assert "up" in props["direction"]["description"]


def test_key_function_contracts_after_trimming() -> None:
    schemas = _schemas()

    bash_desc = schemas["bash"]["function"]["description"]
    assert "returncode" in bash_desc
    assert "task_id" in bash_desc
    assert "60 秒" in bash_desc

    assert "文件:行号:内容" in schemas["grep"]["function"]["description"]
    assert "image" in schemas["read_image"]["function"]["description"]
    assert "URL" in schemas["web_fetch"]["function"]["description"]

    user_select = schemas["user_select"]
    assert "1 分钟没有任何操作" in user_select["function"]["description"]
    # 未回答（answered=false）与用户取消语义必须对模型可见。
    assert "answered=false" in user_select["function"]["description"]
    assert "未答复" in user_select["function"]["description"]
    header = user_select["function"]["parameters"]["properties"]["header"]
    assert "12" in header["description"]

    assert "read" in schemas["subagent_opt"]["function"]["description"]
    assert "type" in schemas["subagent"]["function"]["description"]
