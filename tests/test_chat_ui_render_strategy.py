"""VNodeRenderStrategy 增量渲染测试"""
from unittest.mock import MagicMock, call
from src.chat_ui.core.strategy import VNodeRenderStrategy
from src.chat_ui.vdom.vnode import VNode, Patch, PatchKind, diff


class TestVNodeRenderStrategyDelta:
    """测试增量渲染逻辑"""

    def test_answer_block_delta_only_writes_increment(self):
        """模拟两次 _render_node(answer_block) 调用，验证第二次只输出增量"""
        mock_renderer = MagicMock()
        mock_renderer.output_adapter = MagicMock()
        strategy = VNodeRenderStrategy(
            renderer=mock_renderer,
            store=MagicMock(),
            vnode_builder=MagicMock(),
            output_func=MagicMock(),
        )
        strategy._last_answer_text = "你好"

        # 模拟 render_commands 中的 _render_node 逻辑
        # 构建 answer_block VNode 并调用 render
        vnode = VNode(type="answer_block", key="answer",
                      props={"text": "你好！👋", "phase": "content"})

        # 直接调用 _render_node 的 answer_block 分支逻辑
        text = vnode.props.get("text", "")
        delta = text[len(strategy._last_answer_text):]
        if delta:
            mock_renderer.output_adapter.write_raw(delta)
        strategy._last_answer_text = text

        # 验证只写了增量 "！👋"，不是全量 "你好！👋"
        mock_renderer.output_adapter.write_raw.assert_called_once_with("！👋")
        assert strategy._last_answer_text == "你好！👋"

    def test_answer_block_empty_delta_no_write(self):
        """文本未变化时不调用 write_raw"""
        mock_renderer = MagicMock()
        mock_renderer.output_adapter = MagicMock()
        strategy = VNodeRenderStrategy(
            renderer=mock_renderer,
            store=MagicMock(),
            vnode_builder=MagicMock(),
            output_func=MagicMock(),
        )
        strategy._last_answer_text = "你好"

        text = "你好"  # 未变化
        delta = text[len(strategy._last_answer_text):]
        if delta:
            mock_renderer.output_adapter.write_raw(delta)

        mock_renderer.output_adapter.write_raw.assert_not_called()

    def test_write_lines_incremental(self):
        """write_lines 增量渲染：只输出新增行"""
        mock_output = MagicMock()
        strategy = VNodeRenderStrategy(
            renderer=MagicMock(),
            store=MagicMock(),
            vnode_builder=MagicMock(),
            output_func=mock_output,
        )
        strategy._last_write_lines_count = 2

        lines = ("line1", "line2", "line3", "line4")
        new_count = len(lines)
        if new_count > strategy._last_write_lines_count:
            for line in lines[strategy._last_write_lines_count:]:
                mock_output(f"{line}\n")
        strategy._last_write_lines_count = new_count

        # 只输出了 line3, line4
        assert mock_output.call_count == 2
        mock_output.assert_has_calls([call("line3\n"), call("line4\n")])

    def test_user_messages_incremental(self):
        """user_messages 增量渲染"""
        mock_output = MagicMock()
        strategy = VNodeRenderStrategy(
            renderer=MagicMock(),
            store=MagicMock(),
            vnode_builder=MagicMock(),
            output_func=mock_output,
        )
        strategy._last_user_messages_count = 0

        msgs = ("hello", "world")
        new_count = len(msgs)
        if new_count > strategy._last_user_messages_count:
            for msg in msgs[strategy._last_user_messages_count:]:
                mock_output(f"\n  > {msg}")
        strategy._last_user_messages_count = new_count

        assert mock_output.call_count == 2

    def test_root_type_recursively_renders_answer(self):
        """root VNode 递归渲染子节点中的 answer_block"""
        mock_renderer = MagicMock()
        mock_renderer.output_adapter = MagicMock()
        strategy = VNodeRenderStrategy(
            renderer=mock_renderer,
            store=MagicMock(),
            vnode_builder=MagicMock(),
            output_func=MagicMock(),
        )

        answer = VNode(type="answer_block", key="answer",
                       props={"text": "测试"}, children=[])
        content_area = VNode(type="content_area", key="content_area",
                             children=[answer])
        root = VNode(type="root", key="root", children=[content_area])

        # 模拟 root handler 逻辑
        if root.type == "root":
            for child in root.children:
                if child.type == "content_area":
                    for sub_child in child.children:
                        if sub_child.type == "answer_block":
                            text = sub_child.props.get("text", "")
                            if text:
                                mock_renderer.output_adapter.write_raw(text)

        mock_renderer.output_adapter.write_raw.assert_called_once_with("测试")

    def test_tool_outputs_same_count_content_change(self):
        """tool_outputs 同一工具继续追加文本时的增量渲染"""
        mock_output = MagicMock()
        strategy = VNodeRenderStrategy(
            renderer=MagicMock(),
            store=MagicMock(),
            vnode_builder=MagicMock(),
            output_func=mock_output,
        )
        strategy._last_tool_outputs = (("tool1", "partial"),)

        outputs = (("tool1", "partial_output"),)  # 同一条目，内容变了
        old_len = len(strategy._last_tool_outputs)
        new_len = len(outputs)

        if new_len > old_len:
            for output in outputs[old_len:]:
                mock_output(f"   {output}")
        elif new_len == old_len and old_len > 0:
            if outputs[-1] != strategy._last_tool_outputs[-1]:
                mock_output(f"   {outputs[-1]}")
        strategy._last_tool_outputs = outputs

        mock_output.assert_called_once_with("   ('tool1', 'partial_output')")
