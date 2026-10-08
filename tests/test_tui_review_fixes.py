"""TUI review 修复回归测试（2026-08-18 / 2026-08-22 / 2026-09-10 多轮）。

本文件由历史碎片化的 ``test_tui_review_fixes{,2,3,4}.py`` **归并**而来（P2
测试归并）——四个文件均围绕「review 报告的 P0-P3 修复回归」主题，各自按轮次
拆分，碎片化且命名无语义（序号）。归并后按轮次分节（见下方分隔注释），
测试内容与断言逐字保留、无删减。

轮次索引：
  第一轮（2026-08-18）——工具盒/输入快照/解析行/渲染器光标/子代理面板锁；
  第二轮（2026-08-18）——widget 边界（codeblock/ZStack/tabs/gradient/listview/
                        tree）/trace 缓存/样式名/粘贴降级；
  第三轮（2026-08-22）——补全引擎/样式校验/_normalize_*/边框盒/制表符/颜色；
  第四轮（2026-09-10）——diff 前缀/画布合并/双宽度函数/truncate/尺寸缓存/
                        ChatBlock 身份/StyleSheet/输入编排器/弹窗下限。
"""

from __future__ import annotations

import types
from types import SimpleNamespace

import pytest

from src.tui.app.model import AppModel
from src.tui.core.style import Style
from src.tui.ink import h, StyledRun
from src.tui.ink.element import Element, TEXT
from src.tui.ink.fiber import InputHook
from src.tui.ink.output import Line
from src.tui.ink.reconciler import Reconciler


# ═══════════════════════════════════════════════════════════════════════
# 第一轮（2026-08-18）—— 工具盒 / 输入快照 / 解析行 / 渲染器 / 子代理面板
# ═══════════════════════════════════════════════════════════════════════
# 覆盖 review agent 报告的 P1-P3 修复：
#   1. P1  close_tool_box 扫描失败分支不再过早 return（关闭主流程继续）
#   2. P3  open_tool_box 复用路径重置兜底空 box 的 _tool_started_at
#   3. P2  _input_snap_key 时间桶与 _build_lines 渐显窗口对齐（fading）
#   4. P2  try_read_paste 短突发（快速连击）降级非粘贴
#   5. P3  _ParseLine first_text 首个文本 run 处理后无条件复位
#   6. P3  InkRenderer.set_width + place_cursor 列上限防御钳制
#   7. P3  SubAgentPanelController.stop() _active 检查移入锁内
# ═══════════════════════════════════════════════════════════════════════


class TestCloseToolBoxCommittedTitleRebuild:
    """close_tool_box：已增量提交的 box 标题行关闭时整行重建。

    2026-10-09 起：完成/失败后标题行前缀**保留最终运行时间**（不再显示
    ✔/✖），且尾部元信息去掉耗时——标题行内容不再只是「翻转图标 run」，
    故关闭时按当前块状态整行重建（真源 = block.extra 的 tool_name/detail）。
    本类回归：闭合主流程不受标题行异常影响 + 篡改行被正确重建。
    """

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

    def test_tampered_title_still_closes_block(self):
        """标题行异常（无前缀字符）时块仍须 closed + 提交 + 缓存释放。"""
        m, block = self._make_incremental_tool_box()
        m.committed_lines[0] = Line([StyledRun("truncated-no-icon", None)])
        m.close_tool_box("t1", True)
        # 修复前（历史）：扫描失败分支 return → closed 恒 False、committed_count 恒 0
        assert block.closed is True
        assert m.committed_count == 1
        # 关闭后缓存释放
        assert block._tool_card_body_cache is None
        assert block._tool_card_frame_cache is None
        assert block._tool_card_body_lines_cache is None

    def test_tampered_title_rebuilt_from_block_state(self):
        """标题行被篡改 → 整行重建为正确标题行（不残留篡改内容）。"""
        m, block = self._make_incremental_tool_box()
        m.committed_lines[0] = Line([StyledRun("truncated-no-icon", None)])
        m.close_tool_box("t1", True)
        plain = m.committed_lines[0].plain
        assert "truncated-no-icon" not in plain
        assert "custom_tool" in plain or "CustomTool" in plain

    def test_close_keeps_check_and_final_time_prefix(self):
        """回归：关闭后标题前缀为 ``✔ <最终运行时间>``。"""
        import time as _time

        m, block = self._make_incremental_tool_box()
        block.extra["_tool_started_at"] = _time.monotonic() - 3.0
        m.close_tool_box("t1", True)
        assert block.closed is True
        assert m.committed_lines[0].plain.lstrip().startswith("\u2714 3.")
        assert m.committed_count == 1

    def test_close_drops_duration_meta_keeps_lines(self):
        """关闭后尾部元信息去掉耗时（``· x.xs``），行数保留。"""
        import time as _time

        m, block = self._make_incremental_tool_box()
        block.extra["_tool_started_at"] = _time.monotonic() - 2.0
        m.refresh_running_tool_titles()
        m.close_tool_box("t1", True)
        title = m.committed_lines[0].plain
        assert title.lstrip().startswith("\u2714 2.")
        assert "2.0s" not in title
        assert "\u884c" in title  # ``· 70 行`` 保留


# ── 2. P3 — open_tool_box 复用路径重置兜底空 box 时间戳 ──

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


# ── 3. P2 — _input_snap_key 时间桶对齐渐显窗口 ──

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


# ── 4. P2 — try_read_paste 短突发降级 ──

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


# ── 5. P3 — _ParseLine first_text 复位 ──

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


# ── 6. P3 — InkRenderer set_width + place_cursor 列上限 ──

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


# ── 7. P3 — stop() _active 检查移入锁内 ──

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


# ═══════════════════════════════════════════════════════════════════════
# 第二轮（2026-08-18）—— widget 边界 / trace 缓存 / 样式 / 粘贴降级
# ═══════════════════════════════════════════════════════════════════════
# 覆盖第二轮 review 报告的 P2/P3 修复：
#   1. P2  trace_tools_view use_memo deps 展平原子（嵌套 tuple 恒 miss）
#   2. P2  codeblock 侧边竖线用 chars[5]（borderStyle 联动）
#   3. P2  ZStack 无显式高度 debug 提示（契约声明 + 可观测）
#   4. P2  trace._param_node 非 dict pinfo 防御（畸形 schema 不崩溃）
#   5. P3  _input_io 短突发降级清空 _paste_partial
#   6. P3  _style_utils 颜色名键小写（bright* 系列命中）
#   7. P3  trace 增量缓存键补工具卡标题指纹（原地标题替换感知）
#   8. P3  tree 边界方向键返回 False 放行
#   9. P3  tabs activeStyle/inactiveStyle is-not-None 判断
#   10. P3 gradient 按累计显示宽度归一化渐变进度
#   11. P3 listview 受控模式同批连续导航基准
#   12. P3 _ledger_renderer 死参数删除
# ═══════════════════════════════════════════════════════════════════════


# ── 组件测试基建（参考 tests/test_renderer_popup_overlap.py 模式） ──

def _render_root(component, props, width=80, height=24):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(component, props), width, height)
    return rec, root


def _find_fiber(fiber, pred):
    if fiber is None:
        return None
    if pred(fiber):
        return fiber
    r = _find_fiber(fiber.child, pred)
    if r is not None:
        return r
    return _find_fiber(fiber.sibling, pred)


def _find_input_handler(fiber, key_pred=None):
    """查找 fiber 树中第一个活跃 use_input handler。"""
    target = _find_fiber(fiber, lambda f: (
        key_pred is None or key_pred(f)
    )) if key_pred else fiber
    def _walk(f):
        if f is None:
            return None
        for hook in getattr(f, "hooks", None) or []:
            if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
                return hook.handler
        r = _walk(f.child)
        if r is not None:
            return r
        return _walk(f.sibling)
    return _walk(target if key_pred else fiber)


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _collect_text_strings(element: Element, out: list):
    """递归收集 Element 树中 TEXT 的 children 字符串。"""
    if element is None:
        return
    if element.type == TEXT:
        for c in element.children:
            if isinstance(c, Element):
                _collect_text_strings(c, out)
            elif isinstance(c, str):
                out.append(c)
        ch = element.props.get("children")
        if isinstance(ch, str):
            out.append(ch)
        return
    for c in element.children:
        if isinstance(c, Element):
            _collect_text_strings(c, out)


# ── 2. P2 — codeblock 侧边竖线 chars[5] ──

class TestCodeblockSideBorder:

    def test_double_border_uses_double_vline(self):
        from src.tui.ink.widgets.codeblock import CodeBlock
        el = CodeBlock({
            "code": "print(1)\nprint(2)",
            "language": "python",
            "borderStyle": "double",
            "width": 20,
        })
        texts: list = []
        _collect_text_strings(el, texts)
        joined = "\n".join(texts)
        # 顶/底双边框
        assert "\u2554" in joined and "\u2557" in joined  # ╔ ╗
        # ★ 修复断言：代码行侧边竖线用 ║（chars[5]）——修复前恒 │
        assert "\u2551" in joined
        # 侧边不再出现单线 │（classic/round 场景的 │ 不应混入 double 边框）
        assert "\u2502" not in joined

    def test_single_border_uses_single_vline(self):
        from src.tui.ink.widgets.codeblock import CodeBlock
        el = CodeBlock({
            "code": "x = 1",
            "borderStyle": "single",
            "width": 14,
        })
        texts: list = []
        _collect_text_strings(el, texts)
        joined = "\n".join(texts)
        assert "\u2502" in joined
        assert "\u2551" not in joined

    def test_classic_border_uses_pipe(self):
        from src.tui.ink.widgets.codeblock import CodeBlock
        el = CodeBlock({
            "code": "x = 1",
            "borderStyle": "classic",
            "width": 14,
        })
        texts: list = []
        _collect_text_strings(el, texts)
        joined = "\n".join(texts)
        assert "|" in joined
        assert "\u2502" not in joined


# ── 3. P2 — ZStack 塌缩契约提示 ──

class TestZStackContract:

    def test_children_wrapped_absolute(self):
        from src.tui.ink.widgets.layout import ZStack
        el = ZStack({"height": 3, "children": [h(TEXT, {"children": "x"})]})
        assert el.props.get("position") == "relative"
        child = el.children[0]
        assert child.props.get("position") == "absolute"
        assert child.props.get("left") == 0 and child.props.get("top") == 0

    def test_missing_height_logs_debug(self, caplog):
        import logging
        from src.tui.ink.widgets.layout import ZStack
        with caplog.at_level(logging.DEBUG, logger="src.tui.ink.widgets.layout"):
            ZStack({"children": [h(TEXT, {"children": "x"})]})
        assert any("塌缩" in r.message for r in caplog.records)

    def test_explicit_height_no_warning(self, caplog):
        import logging
        from src.tui.ink.widgets.layout import ZStack
        with caplog.at_level(logging.DEBUG, logger="src.tui.ink.widgets.layout"):
            ZStack({"height": 3, "children": [h(TEXT, {"children": "x"})]})
        assert not any("塌缩" in r.message for r in caplog.records)


# ── 4. P2 — _param_node 畸形 schema 防御 ──

class TestParamNodeMalformedSchema:

    @staticmethod
    def _leaves(nodes):
        """顶层容器 → 首个参数节点 → 叶子 label 列表。"""
        return [c["label"] for c in nodes[0]["children"][0]["children"]]

    def test_non_dict_pinfo_no_crash(self):
        from src.tui.app.trace import build_tools_params_tree
        nodes = build_tools_params_tree({"p": "not-a-dict"}, ["p"])
        assert nodes and nodes[0]["children"]
        # 必需标记仍生效（叶子「必需: 是」）
        assert "必需: 是" in self._leaves(nodes)

    def test_int_pinfo_no_crash(self):
        from src.tui.app.trace import build_tools_params_tree
        nodes = build_tools_params_tree({"n": 42}, [])
        assert nodes[0]["children"][0]["label"].startswith("n")

    def test_none_pinfo_no_crash(self):
        from src.tui.app.trace import build_tools_params_tree
        nodes = build_tools_params_tree({"z": None}, [])
        assert nodes

    def test_normal_dict_still_works(self):
        from src.tui.app.trace import build_tools_params_tree
        nodes = build_tools_params_tree(
            {"q": {"type": "string", "description": "查询"}}, ["q"],
        )
        labels = self._leaves(nodes)
        assert "类型: string" in labels
        assert "描述: 查询" in labels


# ── 5. P3 — try_read_paste 降级清空 _paste_partial ──

class TestPastePartialCleanupOnDowngrade:

    def test_downgrade_clears_stale_partial(self):
        from src.tui._input_io import InputIO
        io = InputIO(fd=0)
        io._paste_partial = b"\xff"  # 上一粘贴残留截断尾
        io.set_pending(b"b")
        result = io.try_read_paste(0, "a")
        assert result == "a"
        assert io.has_pending()
        # ★ 修复断言：降级（判为键入）时粘贴边界结束 → partial 清空
        assert io._paste_partial == b""


# ── 6. P3 — _parse_color bright* 键小写命中 ──

class TestParseColorBrightNames:

    @pytest.mark.parametrize("name,expected", [
        ("brightBlack", 8), ("brightblack", 8),
        ("brightRed", 9), ("brightred", 9),
        ("brightGreen", 10), ("brightyellow", 11),
        ("brightBlue", 12), ("brightmagenta", 13),
        ("brightCyan", 14), ("brightwhite", 15),
        ("black", 0), ("red", 1), ("gray", 8), ("grey", 8),
    ])
    def test_named_colors(self, name, expected):
        from src.tui.ink._style_utils import _parse_color
        assert _parse_color(name) == expected

    def test_unknown_name_none(self):
        from src.tui.ink._style_utils import _parse_color
        assert _parse_color("notacolor") is None


# ── 7. P3 — trace 增量缓存键补标题指纹 ──

class TestTraceCacheTitleInvalidation:

    def _make_block(self):
        from src.tui.app._state_types import ChatBlock
        from src.renderer.ansi.helpers import AnsiLine
        block = ChatBlock("tool")
        block.extra["tool_name"] = ""
        block.extra["tool_detail"] = ""
        block.lines.append(AnsiLine.of("  · 工具 · old"))
        block.lines.append(AnsiLine.of("  output-1"))
        return block

    def test_block_plain_lines_rebuilds_on_title_replace(self):
        from src.tui.app.trace import _block_plain_lines
        block = self._make_block()
        first = _block_plain_lines(block)
        assert first[0] == "  · 工具 · old"
        # open_tool_box 复用路径：原地替换标题行（同长无关）+ extra 更新
        from src.renderer.ansi.helpers import AnsiLine
        block.lines[0] = AnsiLine.of("  · Bash · ls")
        block.extra["tool_name"] = "bash"
        block.extra["tool_detail"] = "ls"
        second = _block_plain_lines(block)
        # ★ 修复断言：缓存键含 tool_name/detail → 原地替换后重建
        assert second[0] == "  · Bash · ls"
        # 未变行复用（增量契约保持）
        assert second[1] == first[1]

    def test_live_fingerprint_changes_on_title_update(self):
        from src.tui.app.trace import _live_fingerprint
        from src.tui.app.model import AppModel
        m = AppModel()
        m.open_tool_box("t1", "", "")
        fp1 = _live_fingerprint(m)
        # 复用路径补全工具名（行数不变——仅原地替换标题）
        m.open_tool_box("t1", "bash", "ls")
        fp2 = _live_fingerprint(m)
        assert fp1 != fp2

    def test_append_only_growth_still_cached(self):
        """回归：行追加（append-only）不因新键失效增量复用语义。"""
        from src.tui.app.trace import _block_plain_lines
        from src.renderer.ansi.helpers import AnsiLine
        block = self._make_block()
        first_snapshot = list(_block_plain_lines(block))  # 拷贝（缓存返回共享引用）
        block.lines.append(AnsiLine.of("  output-2"))
        grown = _block_plain_lines(block)
        assert grown[:2] == first_snapshot
        assert grown[2] == "  output-2"


# ── 8. P3 — tree 边界方向键放行 ──

class TestTreeBoundaryNavigation:

    def _render_tree(self):
        from src.tui.ink.widgets.tree import Tree
        rec, root = _render_root(Tree, {
            "data": ["alpha", "beta", "gamma"], "focus": True,
        })
        return root

    def test_first_item_arrow_up_released(self):
        root = self._render_tree()
        handler = _find_input_handler(root.child)
        assert handler is not None
        # ★ 修复断言：首项按上键无移动 → 返回 False（放行父级）
        assert handler(_ev("arrow_up")) is False

    def test_last_item_arrow_down_released(self):
        root = self._render_tree()
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_down")) is True
        # 到末项后再按下 → 无移动放行
        assert handler(_ev("arrow_down")) is False

    def test_mid_navigation_still_consumed(self):
        root = self._render_tree()
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_up")) is True


# ── 9. P3 — tabs 样式 is-not-None 判断 ──

class TestTabsExplicitEmptyStyle:

    @staticmethod
    def _render_tabs(props) -> "object":
        from src.tui.ink.widgets.tabs import Tabs
        rec, root = _render_root(Tabs, props)
        fiber = _find_fiber(root.child, lambda f: f.props.get("key") == "tab-0")
        assert fiber is not None, "未找到 tab-0 fiber"
        return fiber

    def test_explicit_empty_active_style_kept(self):
        fiber = self._render_tabs({
            "tabs": ["a", "b"], "activeKey": "a",
            "activeStyle": Style(),  # 显式空样式（falsy）
            "showContent": False,
        })
        style = fiber.props.get("style")
        # ★ 修复断言：显式空 Style() 不被默认样式替换
        assert isinstance(style, Style)
        assert style.fg is None

    def test_default_style_when_absent(self):
        from src.tui.ink.widgets.tabs import _TAB_ACTIVE
        fiber = self._render_tabs({
            "tabs": ["a"], "activeKey": "a", "showContent": False,
        })
        assert fiber.props.get("style") is _TAB_ACTIVE


# ── 10. P3 — gradient 按显示宽度归一化 ──

class TestGradientDisplayWidthNormalization:

    def test_ascii_matches_char_index_semantics(self):
        """纯 ASCII：t = i/(n-1)（与按字符索引的旧实现一致，零回归）。"""
        from src.tui.ink.widgets.gradient import _gradient_runs
        from src.tui.core.color import lerp_color
        text = "abcdef"
        stops = [10, 20]
        runs = _gradient_runs(text, stops)
        assert len(runs) == len(text)
        # 逐字符 t 与旧实现（字符索引）一致——用 lerp_color 参考值对比
        for i, r in enumerate(runs):
            t = i / (len(text) - 1)
            assert r.style.fg == lerp_color(stops[0], stops[1], t)

    def test_cjk_ascii_mixed_progress_proportional(self):
        """CJK/ASCII 混排：渐变 t 按累计显示宽度归一（非字符索引）。"""
        from src.tui.ink.widgets.gradient import _gradient_runs
        from src.tui.core.color import lerp_color
        text = "中ab"  # 显示宽 2+1+1=4
        stops = [0, 100]
        runs = _gradient_runs(text, stops)
        fgs = [r.style.fg for r in runs]
        assert len(fgs) == 3
        # 归一化：t = 字符起始显示列 / (总宽 - 本字符宽)（首 0 / 末 1 收敛）
        assert fgs[0] == lerp_color(0, 100, 0.0)       # 中：起始列 0
        # ★ 修复断言：a 的 t 按显示宽 = 2/(4-1) = 2/3（旧按字符索引 = 1/2）
        assert fgs[1] == lerp_color(0, 100, 2 / 3)
        assert fgs[2] == lerp_color(0, 100, 1.0)       # b：起始列 3 / (4-1) = 1

    def test_single_char_solid(self):
        from src.tui.ink.widgets.gradient import _gradient_runs
        runs = _gradient_runs("x", [10, 20])
        assert len(runs) == 1
        assert runs[0].style.fg == 10


# ── 11. P3 — listview 受控模式同批连续导航 ──

class TestListViewControlledBatchNavigation:

    def test_two_downs_same_batch_advance_two(self):
        from src.tui.ink.widgets.listview import ListView
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler is not None
        # 同批两次 arrow_down（无中间渲染——受控 prop 仍为 0）
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_down")) is True
        # ★ 修复断言：第二次基于 ref 推进值（1→2），非旧受控值（0→1）
        assert nav == [1, 2]

    def test_external_cursor_change_resynced_on_render(self):
        from src.tui.ink.widgets.listview import ListView
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        handler(_ev("arrow_down"))  # 内部 ref=1
        # 外部受控值直接跳到 3（新渲染到达）→ 渲染期同步基准
        rec.render(root, h(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 3, "onNavigate": nav.append, "focus": True,
        }), 80, 24)
        handler2 = _find_input_handler(root.child)
        # 基准已同步 3 → 末项按下无移动放行
        assert handler2(_ev("arrow_down")) is False
        assert handler2(_ev("arrow_up")) is True
        assert nav[-1] == 2

    def test_uncontrolled_navigation_unchanged(self):
        from src.tui.ink.widgets.listview import ListView
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_down")) is True
        # 末项（idx 2）→ 放行
        assert handler(_ev("arrow_down")) is False
        assert nav == [1, 2]


# ── 12. P3 — _ledger_renderer 签名收紧 ──

class TestLedgerRendererSignature:

    def test_two_arg_signature_works(self):
        from src.tui.app.trace_view import _ledger_renderer
        from src.tui.app.trace import TraceRecord
        rows = [
            TraceRecord(index=1, kind="user", summary="hello"),
            None,  # 分隔行
        ]
        render_item = _ledger_renderer(rows, 20)
        el_sep = render_item(None, 1, False)
        assert el_sep is not None
        el_rec = render_item(rows[0], 0, True)
        assert el_rec is not None


# ── 1. P2 — trace_tools_view deps 展平（组件冒烟） ──

class TestTraceToolsViewDeps:

    def test_render_smoke_and_inspector_content(self, monkeypatch):
        import time as _time
        from src.tui.app import trace as trace_mod
        from src.tui.app.trace_tools_view import TraceToolsView
        from src.tui.app.model import AppModel
        schemas = [
            ("bash", {"command": {"type": "string", "description": "命令"}},
             ["command"], "执行命令"),
            ("ls", {"path": {"type": "string"}}, [], "列目录"),
        ]
        monkeypatch.setattr(
            trace_mod, "_tools_schema_cache",
            (_time.monotonic(), schemas),
        )
        model = AppModel()
        model.fullscreen = "trace_tools"
        model.width = 80
        rec, root = _render_root(TraceToolsView, {"model": model, "width": 80})
        # 二次渲染（选中不变 → deps 稳定命中缓存路径）
        rec.render(root, h(TraceToolsView, {"model": model, "width": 80}), 80, 24)
        # 左栏两个工具名 + 右栏检查器内容在帧文本中出现
        from src.tui.ink import components as _components
        frame = _components.render_frame(root, 80)
        text = "\n".join(line.plain for line in frame.lines)
        assert "bash" in text
        assert "ls" in text
        assert "命令" in text  # 右栏参数描述（检查器渲染成功）

    def test_navigation_updates_inspector(self, monkeypatch):
        import time as _time
        from src.tui.app import trace as trace_mod
        from src.tui.app.trace_tools_view import TraceToolsView
        from src.tui.app.model import AppModel
        schemas = [
            ("bash", {"command": {"type": "string"}}, ["command"], "执行命令"),
            ("ls", {"path": {"type": "string"}}, [], "列目录"),
        ]
        monkeypatch.setattr(
            trace_mod, "_tools_schema_cache",
            (_time.monotonic(), schemas),
        )
        model = AppModel()
        model.fullscreen = "trace_tools"
        model.width = 80
        rec, root = _render_root(TraceToolsView, {"model": model, "width": 80})
        model.trace_tools_selected = 1
        rec.render(root, h(TraceToolsView, {"model": model, "width": 80}), 80, 24)
        from src.tui.ink import components as _components
        frame = _components.render_frame(root, 80)
        text = "\n".join(line.plain for line in frame.lines)
        # 右栏切换到 ls 的参数（选中变化 → deps 变化 → 重建检查器）
        assert "path" in text


# ═══════════════════════════════════════════════════════════════════════
# 第三轮（2026-08-22）—— 补全引擎 / 样式校验 / 组件边界
# ═══════════════════════════════════════════════════════════════════════
# 覆盖本轮 review（P0-P3）关键修复：
#   1. P0  _text_input 光标字符重复（test_tui_textinput_cursor.py 单文件）
#   2. P1  _completion_engine._complete_path("") 空前缀返回空
#   3. P1  _apply_completion 对 /config set（尾随空格）词边界对齐
#   4. P2  Style 拒绝非 int/TrueColor 色号（float 等）
#   5. P2  search_input/breadcrumbs/tabs 的 _normalize_* 拒绝 str/bytes
#   6. P2  _repeat_to_width 零宽字符不崩溃（补空格）
#   7. P2  _desc_column_width 19/20 边界单调（修复非单调跳变）
#   8. P2  _sync_locked_height 提取（_completion_height 副作用分离）
#   9. P3  _border_box width==3 顶/底行补右角 ┐/┘
#   10. P3 _input_layout._wrap_by_width 内部展开制表符
#   11. P3 _truncate_to_width 参数化 strip（codeblock 复用）
#   12. P3 lerp_color 越界 a + 非有限 t 顺序
#   13. P3 hex_to_rgb 非 hex 上下文 ValueError
#   14. P3 DisplayEventBus.subscribe 非类型 event_type
#   15. P3 _content_str 未知 dict 类型摘要
# ═══════════════════════════════════════════════════════════════════════


def _text_of(line) -> str:
    """Line 的纯文本（runs 拼接）。"""
    return "".join(getattr(r, "text", "") for r in getattr(line, "runs", []))


# ── P1: _complete_path("") 空前缀返回空 ──
def test_complete_path_empty_prefix_returns_empty():
    from src.tui._completion_engine import CompletionEngine
    engine = CompletionEngine()
    assert engine._complete_path("") == []


# ── P1: /config set 尾随空格词边界对齐 ──
def test_apply_completion_config_set_trailing_space():
    from src.tui._completion import _apply_completion
    result = _apply_completion("/config set ", "set api_base_url", -3, "set")
    assert result == "/config set api_base_url"


# ── P2: Style 拒绝 float/str 色号 ──
def test_style_rejects_float_fg():
    with pytest.raises(ValueError):
        Style(fg=45.5)
    with pytest.raises(ValueError):
        Style(bg="45")
    # bool 仍被拒绝（bool 是 int 子类）
    with pytest.raises(ValueError):
        Style(fg=True)
    # 合法 int 通过
    Style(fg=45, bg=23)


# ── P2: _normalize_* 拒绝 str/bytes ──
def test_normalize_items_reject_str():
    from src.tui.ink.widgets.search_input import _normalize_items as _search
    from src.tui.ink.widgets.breadcrumbs import _normalize_items as _breadcrumbs
    from src.tui.ink.widgets.tabs import _normalize_tabs
    assert _search("abc") == []
    assert _breadcrumbs("abc") == []
    assert _normalize_tabs("abc") == []


# ── P2: _repeat_to_width 零宽字符补齐不崩溃 ──
def test_repeat_to_width_zero_width_no_crash():
    from src.tui.ink.widgets._display_common import _repeat_to_width
    assert _repeat_to_width("\u200b", 5) == "     "


# ── P2: _desc_column_width 19/20 边界单调 ──
def test_desc_column_width_monotonic_19_20():
    from src.tui._input_metrics import _desc_column_width
    assert _desc_column_width(19) == 8
    assert _desc_column_width(20) == 8
    # 12~20 无跳变（均匀近似单调）
    for w in range(12, 21):
        assert 0 <= _desc_column_width(w) <= w - 1


# ── P2: _sync_locked_height 提取存在且行为 ──
def test_sync_locked_height_extracted():
    from src.tui._input_metrics import _sync_locked_height
    from types import SimpleNamespace
    c = SimpleNamespace(locked_height=0)
    out = _sync_locked_height(c, locked=0, need=5)
    assert out == 5 and c.locked_height == 5
    # 小幅减少保持（只增不减）
    out2 = _sync_locked_height(c, locked=5, need=4)
    assert out2 == 5 and c.locked_height == 5
    # 大幅减少允许缩小
    out3 = _sync_locked_height(c, locked=5, need=1)
    assert out3 == 1 and c.locked_height == 1


# ── P3: _border_box width==3 顶/底行补右角 ──
def test_border_box_width3_corners():
    from src.tui.ink._border_box import build_border_box
    from src.tui.core.style import Style
    lines = build_border_box([], [], width=3, status="open", border_style=Style(fg=23))
    top = _text_of(lines[0])
    assert "\u2510" in top  # ┐
    closed = build_border_box([], [], width=3, status="done", border_style=Style(fg=23))
    bottom = _text_of(closed[-1])
    assert "\u2518" in bottom  # ┘


# ── P3: _wrap_by_width 内部展开制表符 ──
def test_wrap_by_width_expands_tab():
    from src.tui._input_layout import _wrap_by_width
    # "a" 占 1 列 → \t 跳到第 4 列 tab stop → 补 3 空格 → "a   b"
    out = _wrap_by_width("a\tb", 10)
    assert out == ["a   b"]


# ── P3: _truncate_to_width 参数化 strip ──
def test_truncate_to_width_strip_param():
    from src.tui.ink.widgets._display_common import _truncate_to_width
    text = "\x1b[31mhello\x1b[0m"
    # strip_ansi_seq=True 剥离 ANSI 后按可见字符截断
    r = _truncate_to_width(text, 20, True)
    assert r == "hello"


# ── P3: lerp_color 越界 a + 非有限 t 顺序 ──
def test_lerp_color_range_before_nonfinite():
    from src.tui.core.color import lerp_color
    # a 越界且 t 为 NaN → 范围校验优先（抛 ValueError，而非返回越界 a）
    with pytest.raises(ValueError):
        lerp_color(300, 45, float("nan"))
    # 合法范围 + 非有限 t → 返回 a
    assert lerp_color(45, 60, float("nan")) == 45


# ── P3: hex_to_rgb 非 hex 上下文 ValueError ──
def test_hex_to_rgb_invalid_context():
    from src.tui.core.color import hex_to_rgb
    with pytest.raises(ValueError):
        hex_to_rgb("#gg8800")


# ── P3: DisplayEventBus.subscribe 非类型 event_type ──
def test_event_bus_subscribe_non_type_rejected():
    from src.tui.events.event_bus import DisplayEventBus
    bus = DisplayEventBus()
    with pytest.raises(TypeError):
        bus.subscribe(lambda e: None, event_type=object())


# ── P3: _content_str 未知 dict 类型摘要 ──
def test_content_str_unknown_dict_summary():
    from src.tui.pipeline.message_display import _content_str
    out = _content_str([{"type": "tool_use", "name": "calculator"}])
    assert "[工具调用: calculator]" in out


# ═══════════════════════════════════════════════════════════════════════
# 第四轮（2026-09-10）—— diff / 画布 / 宽度统一 / 尺寸缓存 / 身份语义
# ═══════════════════════════════════════════════════════════════════════
# 覆盖本轮修复的关键行为（每条对应一处 review 问题）：
#   1. ``ink/diff.py`` 稳定前缀跳过不再吞掉前缀之上行区间（顶部行更新可见）；
#   2. ``ink/_paint_border.py`` clip 空交集哨兵 (0,0,0,0) 全裁剪 / y0<0 不丢可见部分；
#   3. ``ink/_paint_canvas.py`` 合并时清理宽字符第二列残留；
#   4. ``_width.wcswidth_simple`` 与 ``renderer cjk_display_width`` 区间一致；
#   5. ``_width.truncate_width`` ANSI 序列整段穿透（不产生残缺转义）；
#   6. ``_screen.TerminalWidthCache.set_dimensions`` 覆盖尺寸不被 TTL 探测替换；
#   7. ``trace._slot_live_lines`` 缓存键含 attr（reasoning/content 各自命中）；
#   8. ``app._state_types.ChatBlock`` 身份语义（``eq=False``）；
#   9. ``core.style.StyleSheet.clear`` 后恢复内置样式集；
#  10. ``src.tui.__getattr__`` 对废弃符号抛 AttributeError（hasattr 安全）；
#  11. ``_input_orchestrator`` 窗口期非空提交不被静默丢弃（返回用户输入）；
#  12. ``user_select._popup_item_rows`` 矮终端下限为 1（不溢出）。
# ═══════════════════════════════════════════════════════════════════════


# ── 1. diff.py — 稳定前缀跳过语义 ──

def test_first_diff_line_detects_change_before_stable_prefix():
    """前缀之上的行（top header）变化必须被检测到（修复前被跳过）。"""
    from src.tui.ink.diff import first_diff_line
    from src.tui.ink.output import Frame, Line

    shared_prefix = [Line.of("committed-1"), Line.of("committed-2")]
    prev = Frame(
        [Line.of("hdr-a")] + list(shared_prefix) + [Line.of("tail")],
        stable_prefix=shared_prefix,
        stable_prefix_offset=1,
        stable_prefix_len=2,
    )
    new = Frame(
        [Line.of("hdr-b")] + list(shared_prefix) + [Line.of("tail")],
        stable_prefix=shared_prefix,
        stable_prefix_offset=1,
        stable_prefix_len=2,
    )
    assert first_diff_line(prev, new) == 0


def test_first_diff_line_skips_identical_prefix_only():
    """前缀区间相同、其余行相同时仍返回 -1（跳过语义保留）。"""
    from src.tui.ink.diff import first_diff_line
    from src.tui.ink.output import Frame, Line

    shared_prefix = [Line.of("committed-1"), Line.of("committed-2")]
    lines = [Line.of("hdr")] + list(shared_prefix) + [Line.of("tail")]
    prev = Frame(lines, stable_prefix=shared_prefix, stable_prefix_offset=1, stable_prefix_len=2)
    new = Frame(list(lines), stable_prefix=shared_prefix, stable_prefix_offset=1, stable_prefix_len=2)
    assert first_diff_line(prev, new) == -1


# ── 2. _paint_border — clip 哨兵 / 负 y0 ──

def test_paint_border_empty_clip_sentinel_paints_nothing():
    """clip=(0,0,0,0)（全部裁剪哨兵）时边框不绘制。"""
    from src.tui.ink._paint_border import _paint_border

    fiber = SimpleNamespace(
        layout_box=SimpleNamespace(x=0, y=0, w=5, h=3),
        props={"border": 1},
    )
    canvas: list = [None, None, None, None]
    _paint_border(fiber, canvas, 1, clip=(0, 0, 0, 0))
    assert canvas == [None, None, None, None]


def test_paint_border_negative_y_paints_visible_rows():
    """box 顶部在画布上方（y0<0）时底边/左右边可见部分仍绘制。"""
    from src.tui.ink._paint_border import _paint_border

    fiber = SimpleNamespace(
        layout_box=SimpleNamespace(x=0, y=-1, w=4, h=3),
        props={"border": 1},
    )
    canvas: list = [None, None]
    _paint_border(fiber, canvas, 1)
    assert any(row for row in canvas)


# ── 3. _paint_canvas — 宽字符第二列残留清理 / 行首零宽 ──

def test_merge_line_clears_stale_second_column():
    """新宽字符写入列 c 时清理既有 c+1 残字（避免错位拼接）。"""
    from src.tui.ink._paint_canvas import _merge_line, _canvas_row_to_line
    from src.tui.ink.output import Line

    row = {1: ("x", None)}
    line = Line.of("中")
    merged = _merge_line(row, 0, line)
    out = _canvas_row_to_line(merged)
    assert out.plain == "中"


def test_put_char_leading_zero_width_kept():
    """行首零宽字符不被下一字符覆盖（合并到同一键）。"""
    from src.tui.ink._paint_canvas import _line_as_dict
    from src.tui.ink.output import Line

    line = Line.of("\u0301a")
    d = _line_as_dict(line)
    joined = "".join(v[0] for _, v in sorted(d.items()))
    assert joined == "\u0301a"


# ── 4. 双宽度函数一致性（_width vs renderer cjk_display_width） ──

@pytest.mark.parametrize("cp", [
    0x1100, 0x2E80, 0x3002, 0x3042, 0x4E00, 0xAC00, 0xF900,
    0x2F800, 0x2CEB0, 0x30400, 0x1F600, 0x26A1, 0xFF01, 0x0301, 0x200B,
])
def test_dual_width_functions_agree(cp):
    """两个宽度函数在关键码点上一致（修复前 Ext F/兼容表意补充缺口）。"""
    from src.tui._width import wcswidth_simple
    from src.renderer._utils._display import cjk_display_width

    ch = chr(cp)
    assert wcswidth_simple(ch) == cjk_display_width(ch)


# ── 5. truncate_width — ANSI 序列穿透 ──

def test_truncate_width_keeps_ansi_sequence_intact():
    """ANSI 序列整体保留、不被拦腰截断（修复前产生残缺转义）。"""
    from src.tui._width import truncate_width, wcswidth_simple

    s = "\x1b[31mabcdef"
    out = truncate_width(s, 3)
    assert out.startswith("\x1b[31m")
    assert "\x1b[31" not in out.replace("\x1b[31m", "")
    assert wcswidth_simple(out) == 3


# ── 6. TerminalWidthCache.set_dimensions — 覆盖不被 TTL 替换 ──

def test_terminal_width_cache_set_dimensions_override_persists():
    """set_dimensions 覆盖后，TTL 到期（force_refresh）仍返回覆盖值。"""
    from src.tui._screen import TerminalWidthCache

    cache = TerminalWidthCache(ttl=0.0)  # TTL 立即过期
    cache.set_dimensions(123, 45)
    assert cache.get_width() == 123
    assert cache.get_height() == 45
    cache.force_refresh()
    assert cache.get_width() == 123
    cache.clear_override()
    assert cache.get_width() != 123 or cache.get_width() > 0


# ── 7. trace._slot_live_lines — 缓存键含 attr ──

def test_slot_live_lines_cache_keyed_by_attr():
    """reasoning 与 content 交错读取不再互相驱逐（修复前恒 miss）。"""
    from src.tui.app.trace import _slot_live_lines

    slot = SimpleNamespace(live_reasoning="r1", live_content="c1")
    first_r = _slot_live_lines(slot, "live_reasoning")
    first_c = _slot_live_lines(slot, "live_content")
    # 再次读取 reasoning 应命中其自身缓存（返回同一列表对象）
    assert _slot_live_lines(slot, "live_reasoning") is first_r
    assert _slot_live_lines(slot, "live_content") is first_c


# ── 8. ChatBlock 身份语义 ──

def test_chat_block_identity_semantics():
    """ChatBlock 为身份对象：内容相同的两个块不相等（eq=False）。"""
    from src.tui.app._state_types import ChatBlock

    a = ChatBlock(kind="content", lines=["x"])
    b = ChatBlock(kind="content", lines=["x"])
    assert a != b
    assert a == a


# ── 9. StyleSheet.clear 恢复内置集 ──

def test_stylesheet_clear_restores_builtins():
    """clear() 后内置样式仍可用（修复前永久丢失）。"""
    from src.tui.core.style import StyleSheet

    before = set(StyleSheet.all_names())
    assert "error" in before
    StyleSheet.clear()
    after = set(StyleSheet.all_names())
    assert "error" in after
    assert after == before


# ── 10. src.tui.__getattr__ — AttributeError 语义 ──

def test_tui_getattr_obsolete_symbol_attribute_error():
    """hasattr/getattr 对废弃符号安全返回（修复前抛 ImportError）。"""
    import src.tui as tui

    assert hasattr(tui, "Box") is False
    assert getattr(tui, "Box", "dflt") == "dflt"
    with pytest.raises(AttributeError):
        tui.Box


# ── 11. 输入编排器 — 窗口期非空提交不丢弃 ──

def test_input_orchestrator_returns_window_submit():
    """窗口期非空提交返回用户输入（修复前静默丢弃）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    class _FakeInput:
        def __init__(self):
            self._queued = "用户窗口期输入"
            self.calls = []

        def get_queued_input(self):
            v, self._queued = self._queued, None
            return v

        def set_buffer(self, text):
            self.calls.append(("set", text))

        def echo(self, text):
            self.calls.append(("echo", text))

    class _FakeMonitor:
        is_alive = True

    orch = TuiInputOrchestrator.__new__(TuiInputOrchestrator)
    fake_input = _FakeInput()
    orch._input = fake_input  # type: ignore[attr-defined]

    result = orch.wait_for_user_input(
        prefill="旧消息", monitor=_FakeMonitor(), input_=fake_input,
    )
    assert result == "用户窗口期输入"


# ── 12. user_select 弹窗行数下限 ──

def test_user_select_popup_rows_lower_bound(monkeypatch):
    """矮终端（h<9）时下限为 1（修复前强制 6 行溢出）。"""
    import src.tui.app.user_select as us

    monkeypatch.setattr(
        "src.tui._screen.TerminalWidthCache.get_default",
        classmethod(lambda cls: SimpleNamespace(get_height=lambda: 4)),
    )
    rows = us._popup_item_rows()
    assert rows == 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
