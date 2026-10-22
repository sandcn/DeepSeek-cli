"""历史回放工具卡：结构化 JSON 结果转为可读文本（bash / bash_opt 等）。"""

from __future__ import annotations

import json

from src.tui._const import DisplayMsgsCmd
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.pipeline.tool_output_display import format_tool_message_content


def _bash_json(stdout: str = "", stderr: str = "", returncode: int = 0) -> str:
    return json.dumps(
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
        ensure_ascii=False,
    )


# ── 纯函数：bash / bash_opt ─────────────────────────────


def test_bash_json_shows_stdout_then_stderr() -> None:
    content = _bash_json("hello\nworld\n", "warn\n")
    assert format_tool_message_content("bash", content) == "hello\nworld\nwarn"


def test_bash_empty_output_returns_empty_text() -> None:
    assert format_tool_message_content("bash", _bash_json()) == ""


def test_bash_legacy_plain_text_unchanged() -> None:
    assert format_tool_message_content("bash", "旧格式纯文本") == "旧格式纯文本"


def test_bash_background_json_shows_started_line() -> None:
    content = json.dumps({"task_id": "bg-1", "status": "running"})
    assert (
        format_tool_message_content("bash", content, detail="sleep 10")
        == "[后台任务 bg-1 已启动: sleep 10]"
    )


def test_bash_opt_prefers_stdio_and_falls_back_to_output() -> None:
    wait_content = json.dumps({
        "task_id": "bg-1",
        "status": "completed",
        "stdout": "out\n",
        "stderr": "err\n",
        "returncode": 0,
    })
    assert format_tool_message_content("bash_opt", wait_content) == "out\nerr"

    read_content = json.dumps({
        "task_id": "bg-1",
        "status": "running",
        "output": "line1\nline2\n",
    })
    assert (
        format_tool_message_content("bash_opt", read_content)
        == "line1\nline2\n"
    )


def test_bash_opt_structured_fallback_has_no_json_braces() -> None:
    content = json.dumps({
        "task_id": "bg-1",
        "op": "screenshot",
        "path": "/tmp/a.png",
        "width": 10,
    })
    text = format_tool_message_content("bash_opt", content)
    assert "path: /tmp/a.png" in text
    assert "width: 10" in text
    assert "{" not in text


# ── 纯函数：subagent / subagent_opt / user_select ───────


def test_subagent_start_json_shows_started_line() -> None:
    content = json.dumps({
        "task_id": "sa-1",
        "status": "running",
        "description": "分析代码",
        "type": "execute",
    })
    assert (
        format_tool_message_content("subagent", content)
        == "[后台 subagent 任务 sa-1 已启动: 分析代码]"
    )


def test_subagent_opt_uses_output_and_formats_wait_all() -> None:
    wait_content = json.dumps({
        "task_id": "sa-1",
        "description": "分析代码",
        "agent_type": "execute",
        "status": "completed",
        "output": "done",
    })
    assert format_tool_message_content("subagent_opt", wait_content) == "done"

    wait_all = json.dumps({
        "count": 1,
        "completed": 1,
        "running": 0,
        "timed_out": False,
        "tasks": [{
            "task_id": "sa-1",
            "status": "completed",
            "output": "a",
        }],
    })
    text = format_tool_message_content("subagent_opt", wait_all)
    assert "tasks:" in text
    assert "output: a" in text
    assert "{" not in text


def test_user_select_summary_matches_live_path() -> None:
    one = json.dumps({"selected": ["唯一项"], "action": "confirmed"})
    assert format_tool_message_content("user_select", one) == "已选择: 唯一项"

    many = json.dumps({"selected": ["a", "b", "c", "d"], "action": "confirmed"})
    assert (
        format_tool_message_content("user_select", many)
        == "已选择 4 项: a, b, c ... 还有 1 项"
    )

    assert format_tool_message_content(
        "user_select", json.dumps({"selected": [], "action": "cancel"}),
    ) == ""


def test_unknown_tool_and_non_json_content_unchanged() -> None:
    payload = _bash_json("hello\n")
    assert format_tool_message_content("mcp_demo", payload) == payload
    assert format_tool_message_content("bash", "not-json") == "not-json"


# ── 回放集成：工具卡正文不再是 JSON ─────────────────────


def _tool_call(tool_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": tool_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _replay(messages: list[dict]) -> AppModel:
    model = AppModel()
    apply_cmd(model, DisplayMsgsCmd(messages=messages))
    return model


def _find_block(model: AppModel, tool_id: str):
    for block in model.blocks:
        if block.extra.get("tool_id") == tool_id:
            return block
    raise AssertionError(f"未找到工具块: {tool_id}")


def _block_text(model: AppModel, tool_id: str) -> str:
    return "\n".join(line.plain for line in _find_block(model, tool_id).lines)


def test_replay_bash_renders_stdio_not_json() -> None:
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call_bash", "bash", {"command": "echo hi"}),
        ]},
        {
            "role": "tool",
            "tool_call_id": "call_bash",
            "content": _bash_json("hello\n", "warn\n"),
        },
    ]
    model = _replay(messages)
    text = _block_text(model, "call_bash")
    assert "hello" in text
    assert "warn" in text
    assert '"stdout"' not in text
    assert "{" not in text


def test_replay_bash_opt_renders_output_not_json() -> None:
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call_opt", "bash_opt", {"task_id": "bg-1", "op": "read"}),
        ]},
        {
            "role": "tool",
            "tool_call_id": "call_opt",
            "content": json.dumps({
                "task_id": "bg-1",
                "status": "running",
                "output": "编译中",
            }),
        },
    ]
    text = _block_text(_replay(messages), "call_opt")
    assert "编译中" in text
    assert '"output"' not in text


def test_replay_user_select_renders_selection_not_json() -> None:
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call_select", "user_select", {"title": "选择", "options": ["A"]}),
        ]},
        {
            "role": "tool",
            "tool_call_id": "call_select",
            "content": json.dumps({"selected": ["A"], "action": "confirmed"}),
        },
    ]
    text = _block_text(_replay(messages), "call_select")
    assert "已选择: A" in text
    assert '"selected"' not in text


def test_replay_read_hidden_still_uses_raw_content() -> None:
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call_read", "read", {"file_path": "/tmp/demo.txt"}),
        ]},
        {
            "role": "tool",
            "tool_call_id": "call_read",
            "content": "文件: /tmp/demo.txt\nsecret",
        },
    ]
    model = _replay(messages)
    block = _find_block(model, "call_read")
    assert "secret" in _block_text(model, "call_read")
    assert block.extra["_chat_hidden_lines"], "read 内容行仍应登记为聊天卡隐藏"


def test_replay_unknown_tool_json_unchanged() -> None:
    payload = _bash_json("hello\n")
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call_ext", "mcp_demo", {}),
        ]},
        {"role": "tool", "tool_call_id": "call_ext", "content": payload},
    ]
    text = _block_text(_replay(messages), "call_ext")
    assert '"stdout"' in text


def test_replay_markdown_tool_keeps_existing_path() -> None:
    content = "## 搜索结果\n\n正文"
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            _tool_call("call_web", "web_search", {"query": "x"}),
        ]},
        {"role": "tool", "tool_call_id": "call_web", "content": content},
    ]
    block = _find_block(_replay(messages), "call_web")
    assert block.extra["_tool_md_source"] == content
