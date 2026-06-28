"""测试 src.tools.user_select：UserSelectFunc 工具。

测试策略
--------
- 测试 UserSelectFunc 构造函数参数初始化
- 测试非 TTY 环境下的自动回退行为
- 测试 display_params 格式化功能
- 测试空选项的边界情况
- 所有测试可独立运行，不依赖真实 TTY 终端
"""

import json
import asyncio
import pytest
from src.tools.user_select import UserSelectFunc


class TestUserSelectApi:
    """测试 UserSelectFunc 的基础 API（不依赖 TTY/终端交互）。"""

    def test_init_params(self):
        """验证构造函数参数正确初始化。"""
        tool = UserSelectFunc(
            title="测试标题",
            options=["A", "B", "C"],
            multi_select=True,
            default_options=["B"],
            timeout=30,
        )
        assert tool.title == "测试标题"
        assert tool.options == ["A", "B", "C"]
        assert tool.multi_select is True
        assert tool.default_options == ["B"]
        assert tool.timeout == 30

    def test_init_default_options_none(self):
        """验证 default_options 未传入时为空列表。"""
        tool = UserSelectFunc(title="t", options=["a", "b"])
        assert tool.default_options == []

    def test_empty_options_returns_empty(self):
        """验证空选项返回 {"action": "empty"}。"""
        tool = UserSelectFunc(title="test", options=[])
        result_json = asyncio.run(tool.execute())
        result = json.loads(result_json)
        assert result == {"selected": [], "action": "empty"}

    def test_execute_non_interactive_fallback(self, monkeypatch):
        """验证非 TTY 环境自动回退默认选项。"""
        # mock sys.stdin.fileno（pytest capture 无 fileno）+ os.isatty 返回 False
        monkeypatch.setattr("sys.stdin.fileno", lambda: 999)
        monkeypatch.setattr("os.isatty", lambda fd: False)
        tool = UserSelectFunc(
            title="test",
            options=["A", "B", "C"],
            default_options=["A"],
        )
        result_json = asyncio.run(tool.execute())
        result = json.loads(result_json)
        assert result["action"] == "non_interactive"
        assert result["selected"] == ["A"]

    def test_execute_non_interactive_empty_default(self, monkeypatch):
        """验证非 TTY 环境且无默认选项时返回空列表。"""
        monkeypatch.setattr("sys.stdin.fileno", lambda: 999)
        monkeypatch.setattr("os.isatty", lambda fd: False)
        tool = UserSelectFunc(title="test", options=["A", "B", "C"])
        result_json = asyncio.run(tool.execute())
        result = json.loads(result_json)
        assert result["action"] == "non_interactive"
        assert result["selected"] == []

    def test_display_params_with_title(self):
        """验证 display_params 基础功能。"""
        result = UserSelectFunc.display_params({"title": "请选择方案"})
        assert "'请选择方案'" in result

    def test_display_params_empty_title(self):
        """参数无 title 时返回空字符串。"""
        result = UserSelectFunc.display_params({})
        assert result == ""

    def test_display_params_truncation(self):
        """标题过长时截断。"""
        long_title = "A" * 100
        result = UserSelectFunc.display_params({"title": long_title})
        assert len(result) <= 82  # max_len=80 + 2引号
        assert result.endswith("...'")
