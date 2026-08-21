"""ToolCallbackChain.handle_tool_calls 汇总路径测试。

修复背景：handle_tool_calls 主体在汇总 failed_tools 时调用 to_tool_text，
但该名称原先仅在 _on_after_tool 方法内部局部导入，工具失败路径触发
``NameError: name 'to_tool_text' is not defined``（pipeline 捕获异常后
round 以 interrupted 结束）。修复为模块级导入，本文件验证：
  1. 失败工具路径不再抛 NameError，failed_tools 汇总正确（核心回归）；
  2. 成功工具路径无回归，successful_tools 汇总正确；
  3. _on_after_tool 经模块级导入归一化 ToolResult（与顶部导入一致）。
"""

from __future__ import annotations


class _FakeEventPort:
    """捕获 tool_summary 事件的桩。"""

    def __init__(self):
        self.summary_data = None

    def publish(self, event, data=None, source=None):
        if event == "tool_summary":
            self.summary_data = data

    def publish_event(self, *args, **kwargs):
        pass


class _FakeDisplay:
    """display 桩：记录 tool_done 的 output_preview。"""

    def __init__(self):
        self.last_done_output = None

    def tool_parsing(self, *args, **kwargs):
        pass

    def tool_start(self, *args, **kwargs):
        pass

    def tool_done(self, tool_label, tool_name, success, metadata=None):
        if metadata:
            self.last_done_output = metadata.get("output_preview")


class _FakeToolAgent:
    """最小 agent 桩：仅暴露 handle_tool_calls 依赖的接口。"""

    def __init__(self):
        self._event_port = _FakeEventPort()
        self.display = _FakeDisplay()
        self._on_tool_completed_callbacks: list = []

    def _append_assistant_message(self, *args, **kwargs):
        pass

    def _append_tool_result(self, *args, **kwargs):
        pass


def _patch_scheduler(monkeypatch, results):
    """替换 ToolScheduler.default() 为返回预置结果的桩（保持 classmethod 语义）。"""

    class _FakeScheduler:
        def __init__(self, r):
            self._r = r

        async def schedule(self, tool_calls, **kwargs):
            return self._r

        async def wait_background_dispatch(self):
            return None

    monkeypatch.setattr(
        "src.core.internal.agent._tool_callbacks.ToolScheduler.default",
        classmethod(lambda cls, r=results: _FakeScheduler(r)),
    )


async def test_handle_tool_calls_failed_tool_no_nameerror(monkeypatch):
    """失败工具路径：不再抛 NameError，failed_tools 汇总正确。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain

    agent = _FakeToolAgent()
    _patch_scheduler(monkeypatch, [("tc-1", "工具执行失败: boom", False)])

    chain = ToolCallbackChain(agent)
    await chain.handle_tool_calls(
        "content",
        [{"id": "tc-1", "name": "read_file", "arguments": {"path": "x.py"}}],
    )

    assert agent._event_port.summary_data is not None
    assert agent._event_port.summary_data["successful_tools"] == []
    failed = agent._event_port.summary_data["failed_tools"]
    assert len(failed) == 1
    assert failed[0][1] == "工具执行失败: boom"


async def test_handle_tool_calls_success_tool(monkeypatch):
    """成功工具路径：successful_tools 汇总正确，无回归。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain

    agent = _FakeToolAgent()
    _patch_scheduler(monkeypatch, [("tc-1", "ok", True)])

    chain = ToolCallbackChain(agent)
    await chain.handle_tool_calls(
        "content",
        [{"id": "tc-1", "name": "read_file", "arguments": {"path": "x.py"}}],
    )

    assert agent._event_port.summary_data is not None
    assert agent._event_port.summary_data["successful_tools"] == ["Read"]
    assert agent._event_port.summary_data["failed_tools"] == []


def test_on_after_tool_normalizes_tool_result():
    """_on_after_tool 经模块级导入将 ToolResult 归一化为纯文本。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain
    from src.tools.base import ToolResult

    agent = _FakeToolAgent()
    chain = ToolCallbackChain(agent)

    tc = {"id": "tc-1", "name": "read_file", "arguments": {"path": "x.py"}}
    output = ToolResult(text="内容已读取", blocks=[{"type": "text"}])
    chain._on_after_tool(tc, output, success=True)

    # tool_done 收到的 output_preview 应为纯文本（ToolResult → text）
    assert agent.display.last_done_output == "内容已读取"


def test_on_after_tool_normalizes_read_image_blocks():
    """read_image 多模态 ToolResult（image_url blocks）→ tool_done 纯文本预览。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain
    from src.tools.base import ToolResult

    agent = _FakeToolAgent()
    chain = ToolCallbackChain(agent)

    tc = {"id": "tc-1", "name": "read_image", "arguments": {"path": "a.png"}}
    output = ToolResult(
        text="图片已读取",
        blocks=[{"type": "text", "text": "图片: a.png"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}],
    )
    chain._on_after_tool(tc, output, success=True)

    assert agent.display.last_done_output == "图片已读取"
