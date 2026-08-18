"""TUI review 修复（2026-08-18）回归测试。

覆盖 review agent 报告的 P1-P3 修复：
  1. P1  close_tool_box 扫描失败分支不再过早 return（关闭主流程继续）
  2. P3  open_tool_box 复用路径重置兜底空 box 的 _tool_started_at
  3. P2  _input_snap_key 时间桶与 _build_lines 渐显窗口对齐（fading）
  4. P2  try_read_paste 短突发（快速连击）降级非粘贴
  5. P3  _ParseLine first_text 首个文本 run 处理后无条件复位
  6. P3  InkRenderer.set_width + place_cursor 列上限防御钳制
  7. P3  SubAgentPanelController.stop() _active 检查移入锁内
"""

from __future__ import annotations

import types

import pytest

from src.tui.app.model import AppModel
from src.tui.ink.output import Line, StyledRun
from src.tui.core.style import Style


# ═══════════════════════════════════════════════════════════
# 1. P1 — close_tool_box 扫描失败分支不再 return
# ═══════════════════════════════════════════════════════════

class TestCloseToolBoxScanFallback:
    """close_tool_box：已增量提交的 box 标题行图标扫描失败时仍须完成关闭。"""

    def _make_incremental_tool_box(self) -> tuple[AppModel, object]:
        """构造已触发增量提交的工具 box（标题行已在 committed_lines）。"""
        m = AppModel()
        m.width = 80
        m.open_tool_box("t1", "custom_tool", "detail")
        # 70 行输出（custom_tool 不在 bash-tail / head 名单 → 不 trim）
        # 超过 _TOOL_INCREMENTAL_THRESHOLD=64 → 触发增量提交
        m.append_tool_output(
            "t1", "\n".join(f"line-{i}" for i in range(70)),
        )
        block = m.tool_boxes["t1"]
        assert block.committed_line_count > 0
        assert block.extra.get("_first_committed_offset") == 0
        return m, block

    def test_scan_failure_still_closes_block(self):
        """图标扫描失败（标题行无图标字符）时块仍须 closed + 提交。"""
        m, block = self._make_incremental_tool_box()
        # 篡改已提交标题行：runs 无图标字符（模拟超窄截断致图标丢失）
        m.committed_lines[0] = Line([StyledRun("truncated-no-icon", None)])
        m.close_tool_box("t1", True)
        # 修复前：扫描失败分支 return → closed 恒 False、committed_count 恒 0
        assert block.closed is True
        assert m.committed_count == 1
        # 关闭后缓存释放
        assert block._tool_card_body_cache is None
        assert block._tool_card_frame_cache is None
        assert block._tool_card_body_lines_cache is None

    def test_scan_failure_head_inserts_icon_keeps_content(self):
        """扫描失败分支头部插入图标（保留全部原标题内容，不丢首 run）。"""
        m, block = self._make_incremental_tool_box()
        m.committed_lines[0] = Line([StyledRun("truncated-no-icon", None)])
        m.close_tool_box("t1", True)
        new_line = m.committed_lines[0]
        # 头部插入 ✔ 图标 run，原标题内容保留
        assert new_line.runs[0].text.startswith("\u2714")
        assert new_line.plain.endswith("truncated-no-icon")

    def test_normal_icon_flip_still_works(self):
        """回归：标题行含图标（正常结构）时原位翻转 ✔ 不受重构影响。"""
        m, block = self._make_incremental_tool_box()
        # committed_lines[0] 为 tool_card_lines 产出（runs[0] = ● 图标）
        m.close_tool_box("t1", True)
        assert block.closed is True
        assert m.committed_lines[0].runs[0].text.strip() == "\u2714"
        assert m.committed_count == 1

    def test_mid_scan_icon_replaced_in_place(self):
        """回归：图标不在首位但在行中（扫描命中）时原位替换。"""
        m, block = self._make_incremental_tool_box()
        m.committed_lines[0] = Line([
            StyledRun("  ", None),
            StyledRun("\u25cf ", Style(fg=214)),
            StyledRun("title", None),
        ])
        m.close_tool_box("t1", True)
        assert block.closed is True
        runs = m.committed_lines[0].runs
        assert runs[0].text == "  "
        assert runs[1].text.startswith("\u2714")
        assert runs[2].text == "title"


# ═══════════════════════════════════════════════════════════
# 2. P3 — open_tool_box 复用路径重置兜底空 box 时间戳
# ═══════════════════════════════════════════════════════════

class TestOpenToolBoxReuseTimestamp:

    def test_fallback_empty_box_timestamp_reset_on_real_start(self):
        """兜底空 box（append 输出兜底建，tool_name 空）+ 后到真实 start
        → 开始时间重置为真实执行开始。"""
        import time as _time
        m = AppModel()
        m.width = 80
        # 模拟 append_tool_output 兜底：open_tool_box(tool_id, "")
        block = m.open_tool_box("t1", "")
        # 哨兵取「过去」时间戳（相对 monotonic 基准——防 uptime 不足的
        # 环境 flaky，P3 review 2026-08-18）
        old_ts = _time.monotonic() - 1000.0
        block.extra["_tool_started_at"] = old_ts
        # 后到真实 ToolStartedEvent（tool_name 补全）
        reused = m.open_tool_box("t1", "bash", "ls")
        assert reused is block
        assert block.extra["_tool_started_at"] != old_ts
        assert block.extra["_tool_started_at"] > old_ts
        assert block.extra["tool_name"] == "bash"

    def test_duplicate_real_start_keeps_first_timestamp(self):
        """真实重复投递（原 tool_name 非空）→ 保持首次开始时间。"""
        m = AppModel()
        m.width = 80
        block = m.open_tool_box("t2", "bash", "x")
        old_ts = 100.0
        block.extra["_tool_started_at"] = old_ts
        reused = m.open_tool_box("t2", "bash", "retry")
        assert reused is block
        assert block.extra["_tool_started_at"] == old_ts

    def test_fallback_box_with_body_not_reset(self):
        """兜底语义 box 但已有主体输出（append 已到达）→ 不重置（保守）。"""
        m = AppModel()
        m.width = 80
        block = m.open_tool_box("t3", "")
        old_ts = 100.0
        block.extra["_tool_started_at"] = old_ts
        # 追加主体输出（行数 > 1）
        m.append_tool_output("t3", "some output")
        assert len(block.lines) > 1
        m.open_tool_box("t3", "search", "q")
        assert block.extra["_tool_started_at"] == old_ts


# ═══════════════════════════════════════════════════════════
# 3. P2 — _input_snap_key 时间桶对齐渐显窗口
# ═══════════════════════════════════════════════════════════

class TestInputSnapKeyFadingBucket:

    def test_idle_uses_quarter_bucket(self):
        from src.tui.app.input_area import _input_snap_key
        now = 123.456
        key = _input_snap_key({"status_active": False}, 80, now, False)
        assert key[-1] == int(now / 0.25)

    def test_fading_uses_tenth_bucket(self):
        from src.tui.app.input_area import _input_snap_key
        now = 123.456
        key = _input_snap_key({"status_active": False}, 80, now, True)
        assert key[-1] == int(now / 0.1)

    def test_status_active_uses_tenth_bucket(self):
        from src.tui.app.input_area import _input_snap_key
        now = 123.456
        key = _input_snap_key({"status_active": True}, 80, now, False)
        assert key[-1] == int(now / 0.1)

    def test_fading_distinct_from_idle(self):
        """渐显期桶粒度必须比空闲细（0.1s vs 0.25s）——同 now 下可区分。"""
        from src.tui.app.input_area import _input_snap_key
        now = 123.456
        idle = _input_snap_key({"status_active": False}, 80, now, False)
        fading = _input_snap_key({"status_active": False}, 80, now, True)
        assert idle[-1] != fading[-1]

    def test_default_fading_param_backward_compatible(self):
        """缺省 fading=False 保持既有调用方兼容（3 参调用）。"""
        from src.tui.app.input_area import _input_snap_key
        now = 123.456
        key = _input_snap_key({"status_active": False}, 80, now)
        assert key[-1] == int(now / 0.25)


# ═══════════════════════════════════════════════════════════
# 4. P2 — try_read_paste 短突发降级
# ═══════════════════════════════════════════════════════════

class TestTryReadPasteShortBurst:

    def _make_io(self) -> "object":
        from src.tui._input_io import InputIO
        return InputIO(fd=0)

    def test_single_printable_pending_not_paste(self):
        """1 字节可打印 pending（快速连击第二键）→ 非粘贴，回写 pending。"""
        io = self._make_io()
        io.set_pending(b"b")
        result = io.try_read_paste(0, "a")
        assert result == "a"
        assert io.has_pending()
        assert io.drain_pending() == b"b"

    def test_two_printable_pending_not_paste(self):
        """2 字节可打印 pending → 非粘贴，回写 pending 保序。"""
        io = self._make_io()
        io.set_pending(b"bc")
        result = io.try_read_paste(0, "a")
        assert result == "a"
        assert io.has_pending()
        assert io.drain_pending() == b"bc"

    def test_multibyte_pending_still_paste(self, monkeypatch):
        """含高位字节的 pending（IME 上屏续字符）→ 仍走粘贴路径消费。"""
        monkeypatch.setattr(
            "src.tui._input_io.select",
            types.SimpleNamespace(select=lambda *a, **k: ([], [], [])),
        )
        io = self._make_io()
        io.set_pending(b"\xe4\xb8")
        result = io.try_read_paste(0, "a")
        # 走粘贴路径：pending 被 drain（回写进 _paste_partial 留待补齐）
        assert result == "a"
        assert not io.has_pending()
        assert io._paste_partial == b"\xe4\xb8"

    def test_three_printable_pending_still_paste(self, monkeypatch):
        """3 字节可打印突发 → 超过连击阈值，走粘贴路径整段返回。"""
        monkeypatch.setattr(
            "src.tui._input_io.select",
            types.SimpleNamespace(select=lambda *a, **k: ([], [], [])),
        )
        io = self._make_io()
        io.set_pending(b"bcd")
        result = io.try_read_paste(0, "a")
        assert result == "abcd"
        assert not io.has_pending()
        assert io._paste_partial == b""

    def test_control_byte_pending_not_downgraded(self, monkeypatch):
        """含控制码（如 ESC 序列）→ 不降级，走既有 ESC 回写分支。"""
        monkeypatch.setattr(
            "src.tui._input_io.select",
            types.SimpleNamespace(select=lambda *a, **k: ([], [], [])),
        )
        io = self._make_io()
        io.set_pending(b"\x1b[A")
        result = io.try_read_paste(0, "a")
        assert result == "a"
        # ESC 分支：整段回写 pending 交解析器消费
        assert io.has_pending()
        assert io.drain_pending() == b"\x1b[A"


# ═══════════════════════════════════════════════════════════
# 5. P3 — _ParseLine first_text 复位
# ═══════════════════════════════════════════════════════════

_SPINNER_CHARS = set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


class TestParseLineFirstTextReset:

    def test_first_run_without_tilde_no_later_replacement(self):
        """首 run 不以 ~ 开头时，后续 run 中的 ~（如路径）不被替换为 spinner。"""
        from types import SimpleNamespace
        from src.tui.app.app import _ParseLine
        line = Line([
            StyledRun("abc", Style(fg=242)),
            StyledRun("~/proj", Style(fg=242)),
        ])
        model = SimpleNamespace(parse_line=line)
        element = _ParseLine({"model": model, "width": 0})
        runs = element.props["styled"]
        texts = "".join(r.text for r in runs)
        # "~" 保留（不被 spinner 替换），且无 spinner 帧字符混入
        assert "~/proj" in texts
        assert not (_SPINNER_CHARS & set(texts))

    def test_leading_tilde_still_replaced(self):
        """回归：首 run 前导空格后的 ~ 仍替换为 spinner。"""
        from types import SimpleNamespace
        from src.tui.app.app import _ParseLine
        line = Line([
            StyledRun("  ~ tool1 12t", Style(fg=242)),
        ])
        model = SimpleNamespace(parse_line=line)
        element = _ParseLine({"model": model, "width": 0})
        runs = element.props["styled"]
        texts = "".join(r.text for r in runs)
        assert "~" not in texts
        assert _SPINNER_CHARS & set(texts)
        assert "tool1 12t" in texts

    def test_first_run_with_embedded_tilde_only_prefix_replaced(self):
        """回归（BUG-40）：仅行首前缀位的 ~ 替换，run 内其他 ~ 保留。"""
        from types import SimpleNamespace
        from src.tui.app.app import _ParseLine
        line = Line([
            StyledRun("  ~ run ~/home", Style(fg=242)),
        ])
        model = SimpleNamespace(parse_line=line)
        element = _ParseLine({"model": model, "width": 0})
        runs = element.props["styled"]
        texts = "".join(r.text for r in runs)
        assert "~/home" in texts
        assert "run" in texts


# ═══════════════════════════════════════════════════════════
# 6. P3 — InkRenderer set_width + place_cursor 列上限
# ═══════════════════════════════════════════════════════════

class TestRendererPlaceCursorColClamp:

    def _make_renderer(self):
        import io as _io
        from src.tui.ink.renderer import InkRenderer
        stream = _io.StringIO()
        return InkRenderer(stream=stream), stream

    def test_set_width_clamps_col(self):
        """宽度已知时 place_cursor 列钳制到 [1, width]。"""
        renderer, stream = self._make_renderer()
        renderer.set_width(10)
        renderer.place_cursor(1, 50)
        out = stream.getvalue()
        # col 钳到 10 → cursor_forward(9)
        assert "\033[9C" in out
        assert "\033[49C" not in out

    def test_unknown_width_no_clamp(self):
        """宽度未知（0，缺省）时不钳制，保持既有行为。"""
        renderer, stream = self._make_renderer()
        renderer.place_cursor(1, 50)
        out = stream.getvalue()
        assert "\033[49C" in out

    def test_col_lower_bound_kept(self):
        """回归：col <= 0 仍钳制到 1（P3-1 既有行为）。"""
        renderer, stream = self._make_renderer()
        renderer.set_width(10)
        renderer.place_cursor(1, -5)
        out = stream.getvalue()
        assert "\033[0C" not in out
        assert "C" not in out.replace("\033[1B", "")  # 无前进序列（\r 归位即可）

    def test_set_width_zero_resets_clamp(self):
        """set_width(0) 恢复未知宽度（不钳制）。"""
        renderer, stream = self._make_renderer()
        renderer.set_width(10)
        renderer.set_width(0)
        renderer.place_cursor(1, 50)
        assert "\033[49C" in stream.getvalue()

    def test_width_equal_col_not_clamped_away(self):
        """col == width（边界）合法，钳制后不变。"""
        renderer, stream = self._make_renderer()
        renderer.set_width(10)
        renderer.place_cursor(1, 10)
        assert "\033[9C" in stream.getvalue()


# ═══════════════════════════════════════════════════════════
# 7. P3 — stop() _active 检查移入锁内
# ═══════════════════════════════════════════════════════════

class _FakeEventBus:
    calls: list = []

    @classmethod
    def get_default(cls):
        return cls

    @classmethod
    def unsubscribe(cls, handler, event_type=None):
        cls.calls.append((handler, event_type))


class TestSubagentPanelStopLocked:

    def _make_controller(self):
        from src.tui._subagent_panel import SubAgentPanelController
        return SubAgentPanelController(push_cmd=lambda cmd: None)

    def test_inactive_stop_is_noop(self, monkeypatch):
        monkeypatch.setattr("src.tui.events.DisplayEventBus", _FakeEventBus)
        _FakeEventBus.calls = []
        ctrl = self._make_controller()
        ctrl.stop()  # 未激活：直接返回，不触达总线
        assert _FakeEventBus.calls == []
        assert ctrl._active_refs == 0

    def test_stop_deactivates_and_unsubscribes(self, monkeypatch):
        monkeypatch.setattr("src.tui.events.DisplayEventBus", _FakeEventBus)
        _FakeEventBus.calls = []
        ctrl = self._make_controller()
        ctrl._active = True
        ctrl._active_refs = 1
        ctrl.stop()
        assert ctrl._active is False
        assert ctrl._active_refs == 0
        # 12 类事件全部取消订阅（_SUBSCRIPTIONS 全量）
        assert len(_FakeEventBus.calls) == len(ctrl._SUBSCRIPTIONS)
        # 幂等：再次 stop 为 no-op
        _FakeEventBus.calls = []
        ctrl.stop()
        assert _FakeEventBus.calls == []

    def test_refcount_stop_partial(self, monkeypatch):
        monkeypatch.setattr("src.tui.events.DisplayEventBus", _FakeEventBus)
        _FakeEventBus.calls = []
        ctrl = self._make_controller()
        ctrl._active = True
        ctrl._active_refs = 2
        ctrl.stop()
        # 仍有活跃引用：不清理
        assert ctrl._active is True
        assert ctrl._active_refs == 1
        assert _FakeEventBus.calls == []
        ctrl.stop()
        assert ctrl._active is False
        assert ctrl._active_refs == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
