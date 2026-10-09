"""subagent 提词用 TUI 流式 markdown 渲染（2026-10-09 用户需求）。

覆盖链路：
  1. ``trace._records_from_messages(user_markdown=True)`` —— subagent 轨迹的
     user 记录（提词）标记 ``TraceRecord.markdown``；主轨迹（默认参数）不标记；
  2. ``trace._subagent_fallback_records`` —— 无 messages 回退路径的提词同样标记；
  3. ``trace.build_subagent_trace_records`` —— 嵌套 subagent 轨迹数据源标记；
  4. ``trace_view._inspector_content_rows`` —— 标记的 user 记录走
     ``_md_detail_rows``（AnsiStreamRenderer 流式 markdown 管线）渲染为
     StyledRun 行；未标记 user 记录保持纯文本换行（原文）。
"""

from types import SimpleNamespace

import pytest

from src.tui.app.trace_types import TraceRecord


def _user_rec(lines, **kw):
    base = dict(
        index=1, kind="user", summary="提词", status="", time_seconds=None,
        time_started=None, time_started_monotonic=True, tokens={}, result="",
        lines=list(lines), source_block=None, subagent_label="",
        tool_call_id="", tool_args=None, tool_result="", images=[],
    )
    base.update(kw)
    return TraceRecord(**base)


def _joined(rows):
    """检查器行 → 纯文本（str 行原样，StyledRun 行拼 text）。"""
    out = []
    for row in rows:
        if isinstance(row, str):
            out.append(row)
        else:
            out.append("".join(getattr(r, "text", "") for r in row))
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════
# 1. 记录构建：markdown 标记（trace._records_from_messages）
# ═══════════════════════════════════════════════════════════

class TestRecordsMarkdownFlag:

    def _msgs(self):
        return [
            {"role": "system", "content": "# 系统提词\n规则"},
            {"role": "user", "content": "# 任务\n- 步骤一\n- 步骤二"},
        ]

    def test_main_track_user_not_marked(self):
        """主轨迹（默认参数）user 记录不带 markdown 标记（保持原文）。"""
        from src.tui.app.trace import _records_from_messages
        records, _ = _records_from_messages(self._msgs())
        users = [r for r in records if r.kind == "user"]
        assert users and users[0].markdown is False

    def test_subagent_user_marked(self):
        """subagent 轨迹（user_markdown=True）user 提词记录带 markdown 标记。"""
        from src.tui.app.trace import _records_from_messages
        records, _ = _records_from_messages(self._msgs(), user_markdown=True)
        users = [r for r in records if r.kind == "user"]
        assert users and users[0].markdown is True
        assert users[0].lines[0] == "# 任务"

    def test_system_record_content_intact(self):
        """system 提词记录内容不受标记影响（system 恒走 markdown 渲染）。"""
        from src.tui.app.trace import _records_from_messages
        records, _ = _records_from_messages(self._msgs(), user_markdown=True)
        systems = [r for r in records if r.kind == "system"]
        assert systems and systems[0].lines[0] == "# 系统提词"


# ═══════════════════════════════════════════════════════════
# 2. 回退路径 & 嵌套 subagent 轨迹构建
# ═══════════════════════════════════════════════════════════

class TestSubagentTraceBuild:

    def _slot(self, **kw):
        base = dict(
            label="agent-1", description="解析模块", status="done",
            agent_type="execute", dispatch_label="",
            prompt="# 提词\n- 目标: 解析\n- 输出: 列表",
            messages=[], tool_history=[], result_text="", result_error="",
            model_phase="", live_reasoning="", live_content="",
            parse_info="", input_tokens=0, output_tokens=0,
            live_input_tokens=0, live_output_tokens=0, last_speed=0.0,
            start_time=0.0, end_time=0.0,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_fallback_prompt_marked(self, monkeypatch):
        """回退路径（无 messages）提词记录标记 markdown。"""
        from src.tui.app import trace
        monkeypatch.setattr(trace, "_tools_record", lambda: None)
        records, _ = trace._subagent_fallback_records("agent-1", self._slot())
        users = [r for r in records if r.kind == "user"]
        assert users and users[0].markdown is True
        assert "提词" in users[0].lines[0]

    def test_build_subagent_trace_marks_prompt(self, monkeypatch):
        """嵌套 subagent 轨迹数据源：提词记录标记 markdown。"""
        from src.tui.app import trace
        monkeypatch.setattr(trace, "_tools_record", lambda: None)
        slot = self._slot(messages=[
            {"role": "system", "content": "你是子代理"},
            {"role": "user", "content": "## 派发指令\n1. 读取 a.py"},
            {"role": "assistant", "content": "完成"},
        ])
        monkeypatch.setattr(trace, "_subagent_slot", lambda label: slot)
        records, _ = trace.build_subagent_trace_records("agent-1", None)
        users = [r for r in records if r.kind == "user"]
        assert users and users[0].markdown is True
        assert users[0].lines[0] == "## 派发指令"


# ═══════════════════════════════════════════════════════════
# 3. 检查器渲染（trace_view._inspector_content_rows）
# ═══════════════════════════════════════════════════════════

class TestInspectorMarkdownRender:

    _LINES = ["# 大标题", "", "- 列表项一", "- 列表项二", "", "**加粗**结尾"]

    def test_flagged_user_uses_markdown(self):
        """标记 markdown 的 user 提词记录 → StyledRun 行（markdown 渲染）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _user_rec(self._LINES, markdown=True)
        rows, keys = _inspector_content_rows(rec, 60)
        assert rows, "提词应生成内容行"
        assert all(isinstance(r, list) for r in rows), "markdown 渲染行为 StyledRun 列表"
        assert keys == [None] * len(rows)
        text = _joined(rows)
        # markdown 标题符号被渲染掉（不再是字面 `# `）
        assert "# 大标题" not in text
        assert "大标题" in text
        assert "列表项一" in text

    def test_unflagged_user_plain(self):
        """未标记 user 记录 → 纯文本换行（保留 `# ` 原文）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _user_rec(self._LINES)
        rows, keys = _inspector_content_rows(rec, 60)
        assert rows
        assert all(isinstance(r, str) for r in rows), "未标记记录走纯文本换行"
        assert "# 大标题" in _joined(rows)

    def test_flagged_user_empty_lines(self):
        """标记但无内容 → 空行列表（检查器显示 (无内容)）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _user_rec([], markdown=True)
        rows, keys = _inspector_content_rows(rec, 60)
        assert rows == []
        assert keys == []

    def test_flagged_user_wraps_to_width(self):
        """markdown 渲染行宽度受栏宽约束（不发散）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _user_rec(["长文本 " * 40], markdown=True)
        rows, _ = _inspector_content_rows(rec, 30)
        assert rows
        for row in rows:
            if isinstance(row, list):
                width = sum(len(getattr(r, "text", "")) for r in row)
                assert width <= 30

    def test_render_cached_across_records(self):
        """同内容跨记录重建命中 markdown 渲染缓存（结果稳定）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        a, _ = _inspector_content_rows(_user_rec(self._LINES, markdown=True), 60)
        b, _ = _inspector_content_rows(_user_rec(self._LINES, markdown=True), 60)
        assert _joined(a) == _joined(b)
