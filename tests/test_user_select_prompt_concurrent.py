"""user_select 并发提问提词测试（2026-08-19 用户需求）。

需求：``src/tools/user_select.py`` 增加「可以并发向用户提问」的提词——
``to_tool_schema()`` 的 description（模型可见的工具提示词）显式声明并发
提问能力：有多个需要用户决策/确认/澄清的问题时，应同一轮**同时**发起多个
user_select 调用（并行提问、一次问完），弹窗以 tab 形式一起显示、用户一次
答完。

验证点：
  1. description 含并发提问声明（关键词：并发向用户提问 / 同时发起多个 /
     一次问完 / tab）；
  2. description 保留原有核心说明（单选/多选、超时/非交互回退
     default_options、返回 JSON、action 枚举）；
  3. schema 整体可 JSON 序列化（注入系统提示词前必经序列化）；
  4. 经 ``registry.get_schemas()`` 收集后（实际注入路径）仍含并发提词。
"""

from __future__ import annotations

import json

from src.tools.registry import ToolRegistry
from src.tools.user_select import UserSelectFunc


_CONCURRENT_KEYWORDS = (
    "并发向用户提问",
    "同时发起多个",
    "一次答完",
    "tab",
    "并行提问",
)


def _schema_desc() -> str:
    """读取 user_select 工具 schema 的 description（模型可见提词）。"""
    schema = UserSelectFunc.to_tool_schema()
    return schema["function"]["description"]


def test_schema_description_declares_concurrent_asking():
    """提词：description 显式声明「支持并发向用户提问」。"""
    desc = _schema_desc()
    for kw in _CONCURRENT_KEYWORDS:
        assert kw in desc, f"提词缺少并发提问关键词: {kw!r}\ndescription={desc}"


def test_schema_description_keeps_core_semantics():
    """提词：保留原有核心说明（单选/多选、超时回退、返回 JSON、action 枚举）。"""
    desc = _schema_desc()
    core_keywords = (
        "单选/多选",
        "multi_select",
        "超时/非交互自动回退 default_options",
        "selected",
        "confirmed/cancel/timeout/non_interactive/empty/error",
    )
    for kw in core_keywords:
        assert kw in desc, f"提词丢失原有核心说明: {kw!r}\ndescription={desc}"


def test_schema_json_serializable():
    """提词所在 schema 可 JSON 序列化（系统提示词注入路径前提）。"""
    schema = UserSelectFunc.to_tool_schema()
    dumped = json.dumps(schema, ensure_ascii=False)
    assert "并发向用户提问" in dumped
    loaded = json.loads(dumped)
    assert loaded["function"]["name"] == "user_select"


def test_registry_schema_includes_concurrent_prompt():
    """经 registry.get_schemas() 收集（实际注入路径）后仍含并发提词。"""
    registry = ToolRegistry()
    schemas = registry.get_schemas()
    us_schemas = [
        s for s in schemas
        if s.get("function", {}).get("name") == "user_select"
    ]
    assert us_schemas, "registry 未收集 user_select schema"
    desc = us_schemas[0]["function"]["description"]
    assert "并发向用户提问" in desc
    assert "同时发起多个" in desc
