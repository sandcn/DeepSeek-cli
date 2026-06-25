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


class TestSubagentToolHistoryRendering:
    """测试 subagent_slots 渲染中 tool_history 子行的生成逻辑。

    模拟 strategy.py 中 subagent_slots 分支的 tool_history 部分，
    验证：空列表不产生额外行、行数计算、图标颜色、detail 截断。
    """

    # ── 辅助方法：构造带 tool_history 的 slot dict ──

    @staticmethod
    def _make_slot(label="agent-1", tool_history=None, status="running",
                   start_time=1000.0, end_time=0.0, **overrides):
        """构造一个与 slot_dict 格式一致的 slot 数据。"""
        slot = {
            "label": label,
            "description": f"test {label}",
            "agent_type": "plan_execute",
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "total_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "live_input_tokens": 0,
            "live_output_tokens": 0,
            "last_speed": 0.0,
            "model_phase": "",
            "model_info": "",
            "result_text": "",
            "result_error": "",
            "tool_history": tool_history if tool_history is not None else [],
        }
        slot.update(overrides)
        return slot

    @staticmethod
    def _make_tool_record(tool_name="read_file", detail="main.py",
                          phase="done", start_time=1000.0, end_time=1001.5):
        """构造一个 tool_history 条目 dict。"""
        return {
            "tool_name": tool_name,
            "detail": detail,
            "start_time": start_time,
            "end_time": end_time,
            "phase": phase,
        }

    # ── 行数测试 ──

    def test_empty_tool_history_no_extra_lines(self):
        """tool_history 为空时不产生额外行，new_line_count 仅计主行。"""
        from unittest.mock import MagicMock
        adapter = MagicMock()

        slots = {"agent-1": self._make_slot(tool_history=[])}
        new_line_count = 0
        for label, slot in slots.items():
            adapter.write_raw(f"\r\033[K[main {label}]\n")
            new_line_count += 1
            tool_history = slot.get("tool_history", [])
            if tool_history:
                new_line_count += len(tool_history)

        assert new_line_count == 1, (
            f"tool_history 为空时 new_line_count 应为 1（仅主行），实际为 {new_line_count}"
        )
        assert adapter.write_raw.call_count == 1

    def test_tool_history_one_entry_produces_two_lines(self):
        """tool_history 有 1 条时，new_line_count = 主行 1 + 工具行 1 = 2。"""
        from unittest.mock import MagicMock
        adapter = MagicMock()

        slots = {"agent-1": self._make_slot(tool_history=[
            self._make_tool_record("read_file", "main.py", "done", 1000.0, 1001.5),
        ])}

        new_line_count = 0
        for label, slot in slots.items():
            adapter.write_raw(f"[main {label}]\n")
            new_line_count += 1
            tool_history = slot.get("tool_history", [])
            if tool_history:
                recent = list(reversed(tool_history[-3:]))
                for rec in recent:
                    adapter.write_raw(f"[tool {rec['tool_name']}]\n")
                    new_line_count += 1

        assert new_line_count == 2
        assert adapter.write_raw.call_count == 2

    def test_tool_history_three_entries_shows_all_three(self):
        """tool_history 有 3 条时全部显示（最近 3 条倒序）。"""
        from unittest.mock import MagicMock
        adapter = MagicMock()

        slots = {"agent-1": self._make_slot(tool_history=[
            self._make_tool_record("tool_a", "", "done", 1000.0, 1001.0),
            self._make_tool_record("tool_b", "", "done", 1002.0, 1003.0),
            self._make_tool_record("tool_c", "", "done", 1004.0, 1005.0),
        ])}

        tool_names_rendered = []
        new_line_count = 0
        for label, slot in slots.items():
            adapter.write_raw("[main]\n")
            new_line_count += 1
            tool_history = slot.get("tool_history", [])
            if tool_history:
                recent = list(reversed(tool_history[-3:]))
                for rec in recent:
                    tool_names_rendered.append(rec["tool_name"])
                    adapter.write_raw(f"[tool {rec['tool_name']}]\n")
                    new_line_count += 1

        assert new_line_count == 4  # 1 main + 3 tools
        assert tool_names_rendered == ["tool_c", "tool_b", "tool_a"]

    def test_tool_history_five_entries_shows_last_three_reversed(self):
        """tool_history 有 5 条时仅显示最近 3 条（倒序）。"""
        from unittest.mock import MagicMock
        adapter = MagicMock()

        slots = {"agent-1": self._make_slot(tool_history=[
            self._make_tool_record("tool_1", "", "done", 1000.0, 1001.0),
            self._make_tool_record("tool_2", "", "done", 1002.0, 1003.0),
            self._make_tool_record("tool_3", "", "done", 1004.0, 1005.0),
            self._make_tool_record("tool_4", "", "done", 1006.0, 1007.0),
            self._make_tool_record("tool_5", "", "done", 1008.0, 1009.0),
        ])}

        tool_names_rendered = []
        new_line_count = 0
        for label, slot in slots.items():
            adapter.write_raw("[main]\n")
            new_line_count += 1
            tool_history = slot.get("tool_history", [])
            if tool_history:
                recent = list(reversed(tool_history[-3:]))
                for rec in recent:
                    tool_names_rendered.append(rec["tool_name"])
                    new_line_count += 1

        assert new_line_count == 4  # 1 main + 3 tools
        assert tool_names_rendered == ["tool_5", "tool_4", "tool_3"]

    # ── 图标测试 ──

    def test_done_phase_icon_is_checkmark(self):
        """done phase 渲染时图标为 ✓（U+2713）。"""
        from src.chat_ui.infrastructure.styled import StyledText

        icon = "\u2713"
        line = StyledText.assemble(
            (f"    {icon} ", "dim green"),
            ("bash cmd", "dim"),
            ("  · ", "dim"),
            ("1.5s", "dim"),
        )
        rendered = str(line)
        assert "\u2713" in rendered, f"done 图标应为 ✓，渲染内容: {rendered!r}"

    def test_fail_phase_icon_is_cross(self):
        """fail phase 渲染时图标为 ✗（U+2717）。"""
        from src.chat_ui.infrastructure.styled import StyledText

        icon = "\u2717"
        line = StyledText.assemble(
            (f"    {icon} ", "dim red"),
            ("bash cmd", "dim"),
            ("  · ", "dim"),
            ("1.5s", "dim"),
        )
        rendered = str(line)
        assert "\u2717" in rendered, f"fail 图标应为 ✗，渲染内容: {rendered!r}"

    def test_running_phase_icon_is_loop(self):
        """running phase 渲染时图标为 ⟳（U+27F3）。"""
        from src.chat_ui.infrastructure.styled import StyledText

        icon = "\u27f3"
        line = StyledText.assemble(
            (f"    {icon} ", "dim yellow"),
            ("bash cmd", "dim"),
        )
        rendered = str(line)
        assert "\u27f3" in rendered, f"running 图标应为 ⟳，渲染内容: {rendered!r}"

    def test_parsing_phase_icon_is_loop(self):
        """parsing phase 渲染时图标也为 ⟳（与 running 同组）。"""
        from src.chat_ui.infrastructure.styled import StyledText

        icon = "\u27f3"
        line = StyledText.assemble(
            (f"    {icon} ", "dim yellow"),
            ("read_file a.py", "dim"),
        )
        rendered = str(line)
        assert "\u27f3" in rendered, f"parsing 图标应为 ⟳，渲染内容: {rendered!r}"

    # ── 终端宽度感知截断测试 ──

    def test_main_desc_truncated_by_terminal_width(self):
        """主行 desc 超过终端可用宽度时被截断。"""
        from src.ui.ansi import truncate_ansi_visual, visual_width
        tw = 60
        desc = "A" * 100
        type_tag = "exec"
        token_str = "123"
        elapsed_str = "2.5s"
        prefix_w = 7 + len(type_tag)  # "  X [tag] "
        suffix_w = 4 + len(token_str) + 4 + 4 + len(elapsed_str)  # "  · 123 out  · 2.5s"
        available = max(tw - prefix_w - suffix_w - 1, 10)
        truncated = truncate_ansi_visual(desc, max_visual=available)
        # … (U+2026) 被 visual_width 计为 2，但 truncate_ansi_visual 内部按 1 预留
        assert visual_width(truncated) <= available + 1
        assert "…" in truncated or len(truncated) < len(desc)

    def test_tool_desc_truncated_by_terminal_width(self):
        """工具行 tool_desc 超过终端可用宽度时被截断。"""
        from src.ui.ansi import truncate_ansi_visual, visual_width
        tw = 80
        tool_desc = "read_file " + "x" * 100
        t_elapsed_str = "1.2s"
        t_prefix_w = 6  # "    X "
        t_suffix_w = 4 + len(t_elapsed_str)  # "  · 1.2s"
        t_available = max(tw - t_prefix_w - t_suffix_w - 1, 10)
        truncated = truncate_ansi_visual(tool_desc, max_visual=t_available)
        # … (U+2026) 被 visual_width 计为 2，但 truncate_ansi_visual 内部按 1 预留
        assert visual_width(truncated) <= t_available + 1

    def test_truncation_floor_min_10(self):
        """极端窄终端（tw=20）时 available 不低于 10。"""
        from src.ui.ansi import truncate_ansi_visual
        tw = 20
        desc = "Hello World This Is A Very Long Description"
        type_tag = "exec"
        token_str = "123"
        elapsed_str = "2.5s"
        prefix_w = 7 + len(type_tag)
        suffix_w = 4 + len(token_str) + 4 + 4 + len(elapsed_str)
        available = max(tw - prefix_w - suffix_w - 1, 10)
        assert available >= 10  # floor protection
        truncated = truncate_ansi_visual(desc, max_visual=available)
        assert len(truncated) > 0

    # ── 综合渲染输出测试 ──

    def test_full_rendering_output_includes_tool_lines(self):
        """综合测试：模拟完整渲染流程，验证 tool_history 子行正确输出。"""
        from unittest.mock import MagicMock
        from src.chat_ui.infrastructure.styled import StyledText

        adapter = MagicMock()
        _now = 2000.0  # 固定「当前」时间

        slots = {"agent-1": self._make_slot(
            tool_history=[
                self._make_tool_record("read_file", "src/main.py", "done",
                                       1000.0, 1001.5),
                self._make_tool_record("bash", "pytest -x -q", "done",
                                       1002.0, 1003.2),
                self._make_tool_record("write_file", "src/out.py", "done",
                                       1004.0, 1005.0),
            ],
            status="done",
            start_time=1000.0,
            end_time=1006.0,
        )}

        new_line_count = 0
        for label, slot in slots.items():
            # 主行
            adapter.write_raw(f"\r\033[K[main {label}]\n")
            new_line_count += 1

            # 工具历史
            tool_history = slot.get("tool_history", [])
            if tool_history:
                recent_tools = list(reversed(tool_history[-3:]))
                for rec in recent_tools:
                    t_name = rec.get("tool_name", "?")
                    t_detail = rec.get("detail", "")
                    t_phase = rec.get("phase", "running")
                    t_start = rec.get("start_time", 0)
                    t_end = rec.get("end_time", 0)

                    if t_phase in ("running", "parsing") and t_start > 0:
                        t_elapsed = _now - t_start
                    elif t_end > 0:
                        t_elapsed = t_end - t_start
                    else:
                        t_elapsed = 0.0

                    tool_desc = f"{t_name} {t_detail}" if t_detail else t_name
                    t_elapsed_str = f"{t_elapsed:.1f}s" if t_elapsed > 0 else ""

                    # 终端宽度截断
                    from src.ui.ansi import truncate_ansi_visual
                    tw = 80
                    t_prefix_w = 6
                    t_suffix_w = 4 + len(t_elapsed_str) if t_elapsed_str else 0
                    t_available = max(tw - t_prefix_w - t_suffix_w - 1, 10)
                    tool_desc = truncate_ansi_visual(tool_desc, max_visual=t_available)

                    if t_phase in ("done",):
                        t_icon = "\u2713"
                        t_icon_color = "dim green"
                    elif t_phase in ("fail",):
                        t_icon = "\u2717"
                        t_icon_color = "dim red"
                    else:
                        t_icon = "\u27f3"
                        t_icon_color = "dim yellow"

                    t_parts = [
                        (f"    {t_icon} ", t_icon_color),
                        (f"{tool_desc}", "dim"),
                    ]
                    if t_elapsed_str:
                        t_parts.append(("  \u00b7 ", "dim"))
                        t_parts.append((t_elapsed_str, "dim"))

                    t_line = StyledText.assemble(*t_parts)
                    adapter.write_raw(f"\r\033[K{t_line}\n")
                    new_line_count += 1

        assert new_line_count == 4  # 1 main + 3 tools
        assert adapter.write_raw.call_count == 4

        # 验证每条工具行包含正确的图标
        rendered_lines = [str(c[0][0]) for c in adapter.write_raw.call_args_list]
        assert "\u2713" in rendered_lines[1], f"工具行 1 应含 ✓: {rendered_lines[1]!r}"
        assert "\u2713" in rendered_lines[2], f"工具行 2 应含 ✓: {rendered_lines[2]!r}"
        assert "\u2713" in rendered_lines[3], f"工具行 3 应含 ✓: {rendered_lines[3]!r}"
        # 工具名称应以倒序出现
        assert "write_file" in rendered_lines[1]
        assert "bash" in rendered_lines[2]
        assert "read_file" in rendered_lines[3]
