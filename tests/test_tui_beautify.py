"""TUI 全界面美化（BEAUTY-36，2026-08-19）单元测试。

覆盖 9 项美化改动：
  1. 欢迎屏多行化（chat_view._welcome_rows / _welcome_elements）
  2. Splash 启动屏品牌化（apply._do_splash：✦ + 模型名 + · 版本）
  3. 错误行 ✖ 图标（apply._do_error）
  4. 状态栏图标化（status_bar._build_status_runs：⏱/◆/»）
  5. 输入区时间戳 ◷ 图标（input_area._build_lines）
  6. 角色头样式分层（_model_helpers._role_header_runs：▎ 提亮，文本不变）
  7. TraceView 头部行尾分隔线填充
  8. TraceToolsView 头部行尾分隔线填充
  9. 弹窗标题色统一 45（_popup_builder._build_popup_lines / input_area.CompletionPopup）
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.tui._const import ErrorCmd, SplashCmd
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.app import chat_view
from src.tui.app.chat_view import _welcome_rows, _welcome_elements
from src.tui.app.status_bar import _build_status_runs
from src.tui.app.input_area import _build_lines


# ═══════════════════════════════════════════════════════════
# 1. 欢迎屏多行化
# ═══════════════════════════════════════════════════════════

class TestWelcomeScreen:
    """空状态欢迎屏（多行欢迎卡）。"""

    def test_rows_structure_five_lines(self):
        """欢迎屏为 5 行：品牌行 + 空行 + 3 行引导。"""
        rows = _welcome_rows(False, 80)
        assert len(rows) == 5

    def test_brand_line_contains_dot_and_gradient_title(self):
        """品牌行：✦ 前缀 + 渐变逐字符 "DeepSeek CLI" + 版本 dim。"""
        rows = _welcome_rows(False, 80)
        brand = rows[0]
        assert brand[0].text == "\u2726 "   # ✦
        full = "".join(r.text for r in brand)
        assert "DeepSeek CLI" in full       # 渐变标题（逐字符 runs）
        assert brand[0].style is not None and brand[0].style.bold
        # 渐变逐字符：标题部分每字符一个 run（fg 在色标区间内单调渐变）
        title_runs = [r for r in brand[1:] if r.text and set(r.text) != {"\u00b7", " "}]
        assert len(title_runs) > 4, "渐变标题应逐字符成 run"
        fgs = [r.style.fg for r in title_runs if r.style is not None]
        assert len(set(fgs)) > 1, "渐变应产生多个不同色号"

    def test_guide_lines_bullet_prefix(self):
        """3 行引导行以 › 前缀开头（› 强调青）。"""
        rows = _welcome_rows(False, 80)
        for row in rows[2:]:
            texts = "".join(r.text for r in row)
            assert "\u203a" in texts, "引导行应含 › 前缀"

    def test_static_cache_reference_stable(self):
        """空闲态同 (False, width) 快照命中缓存——同引用跨帧复用。"""
        # 重置缓存后连续两次调用返回同一列表对象
        chat_view._WELCOME_STATIC_CACHE[0] = None
        chat_view._WELCOME_STATIC_CACHE[1] = None
        a = _welcome_rows(False, 80)
        b = _welcome_rows(False, 80)
        assert a is b
        assert chat_view._WELCOME_STATIC_CACHE[0] == (False, 80)

    def test_active_dot_glow_range(self):
        """活跃期 ✦ 呼吸色号落在 [45, 61] 区间。"""
        rows = _welcome_rows(True, 80)
        fg = rows[0][0].style.fg
        assert 45 <= fg <= 61
        # 活跃期不写静态缓存（每帧呼吸色变化）
        assert chat_view._WELCOME_STATIC_CACHE[0] != (True, 80)

    def test_narrow_width_truncated(self):
        """窄屏（width=10）每行显示宽度 <= width（行级 diff 宽度不变量）。"""
        rows = _welcome_rows(False, 10)
        for i, row in enumerate(rows):
            w = sum(getattr(r, "width", len(r.text)) for r in row)
            assert w <= 10, f"行 {i} 宽 {w} 超出 width=10"

    def test_elements_keys(self):
        """_welcome_elements 返回带索引 key 的 TEXT 元素列表。"""
        model = AppModel()
        els = _welcome_elements(model, 80)
        assert len(els) == 5
        for i, el in enumerate(els):
            assert el.props.get("key") == f"welcome-{i}"

    def test_active_model_detection(self):
        """status_active=True 时走活跃呼吸路径（✦ fg 动态计算）。"""
        model = AppModel()
        model.status.status_active = True
        els = _welcome_elements(model, 80)
        assert len(els) == 5


# ═══════════════════════════════════════════════════════════
# 2. Splash 启动屏品牌化
# ═══════════════════════════════════════════════════════════

class TestSplashBrand:
    """启动品牌屏（✦ + 模型名 + · 版本）。"""

    def test_splash_with_model_name(self):
        model = AppModel()
        model.status.model_name = "deepseek-chat"
        apply_cmd(model, SplashCmd())
        assert len(model.blocks) == 1
        block = model.blocks[0]
        assert block.kind == "splash"
        plain = block.lines[0].plain
        assert "\u2726" in plain          # ✦ 品牌符号
        assert "deepseek-chat" in plain   # 模型名
        assert "\u00b7" in plain          # · 分隔
        assert "v" in plain               # 版本号（v2.x.x）

    def test_splash_without_model_falls_back_version(self):
        model = AppModel()
        apply_cmd(model, SplashCmd())
        plain = model.blocks[0].lines[0].plain
        assert "\u2726" in plain
        # 无模型名：仅 ✦ + 版本（仍非空屏）
        assert plain.strip().startswith("\u2726")


# ═══════════════════════════════════════════════════════════
# 3. 错误行 ✖ 图标
# ═══════════════════════════════════════════════════════════

class TestErrorIcon:
    """错误消息行前缀 ✖（替代 !）。"""

    def test_error_prefix_cross_mark(self):
        model = AppModel()
        apply_cmd(model, ErrorCmd(message="boom"))
        block = model.blocks[0]
        assert block.kind == "error"
        assert block.lines[0].plain.startswith("  \u2716 ")
        assert "boom" in block.lines[0].plain

    def test_error_multiline_each_prefixed(self):
        model = AppModel()
        apply_cmd(model, ErrorCmd(message="line1\nline2"))
        lines = model.blocks[0].lines
        assert len(lines) == 2
        for ln in lines:
            assert ln.plain.startswith("  \u2716 ")


# ═══════════════════════════════════════════════════════════
# 4. 状态栏图标化
# ═══════════════════════════════════════════════════════════

class TestStatusBarIcons:
    """状态栏耗时/token/速度图标前缀（⏱/◆/»）。"""

    def _model_with_snapshot(self, active: bool) -> AppModel:
        model = AppModel()
        model.status.status_active = active
        model.status.model_name = "test-model"
        model.status.tool_total = 5
        model.status.tool_count = 2
        model._status_snapshot_cache = (
            time.monotonic(),
            {"total_tokens": 1500, "elapsed_seconds": 65.0, "per_second_speed": 42.0},
        )
        return model

    def test_active_icons_present(self):
        model = self._model_with_snapshot(True)
        runs = _build_status_runs(model, 0.0, "\u00b7", "")
        text = "".join(r.text for r in runs)
        assert "\u23f1 1:05" in text      # ⏱ + m:ss 耗时
        assert "\u25c6 1.5kt" in text     # ◆ + token
        assert "\u00bb 42.0t/s" in text   # » + 速度（42.0 → x.x t/s）

    def test_idle_icons_present(self):
        model = self._model_with_snapshot(False)
        model.status.tool_total = 0
        model._status_snapshot_cache = (
            time.monotonic(),
            {"total_tokens": 300, "elapsed_seconds": 5.0, "per_second_speed": 10.0},
        )
        # 空闲时不渲染统计区（status_active=False → model_part only）
        runs = _build_status_runs(model, 0.0, "\u00b7", "")
        text = "".join(r.text for r in runs)
        assert "test-model" in text


# ═══════════════════════════════════════════════════════════
# 5. 输入区时间戳 ◷ 图标
# ═══════════════════════════════════════════════════════════

class TestTimestampClockIcon:
    """下分隔线时间戳带 ◷ 时钟图标。"""

    def _fiber(self, width: int = 80):
        props = {
            "text": "",
            "cursor_pos": 0,
            "completion": None,
            "status_active": False,
            "cpu": 3,
            "mem": 12,
            "width": width,
        }
        return SimpleNamespace(
            props=props,
            layout_box=SimpleNamespace(w=width, x=0, y=0),
        )

    def test_timestamp_line_has_clock_icon(self):
        fiber = self._fiber(80)
        lines = _build_lines(fiber)
        texts = ["".join(r.text for r in ln.runs) for ln in lines]
        # 下分隔线（时间戳行）在输入行之后——含 ◷ 图标
        ts_lines = [t for t in texts if "\u25f7" in t]
        assert ts_lines, "时间戳行应含 ◷ 图标"
        assert any(":" in t for t in ts_lines)  # 时间 HH:MM:SS

    def test_mode_line_still_present(self):
        fiber = self._fiber(80)
        lines = _build_lines(fiber)
        texts = ["".join(r.text for r in ln.runs) for ln in lines]
        assert any("\u6807\u51c6\u6a21\u5f0f" in t for t in texts)  # 标准模式


# ═══════════════════════════════════════════════════════════
# 6. 角色头样式分层（文本不变）
# ═══════════════════════════════════════════════════════════

class TestRoleHeaderStyles:
    """通知/子代理角色头 ▎ 提亮（文本不变——▎通知 测试锁定）。"""

    def test_notification_text_unchanged(self):
        from src.tui.app._model_helpers import _role_header_runs
        block = SimpleNamespace(kind="notification", closed=True, extra={})
        runs = _role_header_runs(block, live=False)
        assert "".join(r.text for r in runs) == "\u258e\u901a\u77e5"  # ▎通知

    def test_notification_bar_highlighted(self):
        from src.tui.app._model_helpers import _role_header_runs
        block = SimpleNamespace(kind="notification", closed=True, extra={})
        runs = _role_header_runs(block, live=False)
        assert runs[0].text == "\u258e"
        assert runs[0].style is not None and runs[0].style.fg == 110  # ▎ 浅蓝提亮

    def test_subagent_bar_highlighted(self):
        from src.tui.app._model_helpers import _role_header_runs
        block = SimpleNamespace(kind="subagent", closed=True, extra={})
        runs = _role_header_runs(block, live=False)
        assert "".join(r.text for r in runs) == "\u258e\u5b50\u4ee3\u7406"  # ▎子代理
        assert runs[0].style is not None and runs[0].style.fg == 75   # ▎ 紫蓝提亮

    def test_content_reasoning_headers_unchanged(self):
        from src.tui.app._model_helpers import _role_header_runs
        content = SimpleNamespace(kind="content", closed=True, extra={})
        runs = _role_header_runs(content, live=False)
        assert "".join(r.text for r in runs) == "\u258d\U0001f4ac \u56de\u7b54"  # ▍💬 回答


# ═══════════════════════════════════════════════════════════
# 7/8. 轨迹视图头部行尾分隔线填充（真实组件渲染）
# ═══════════════════════════════════════════════════════════

class TestTraceHeaderSeparator:
    """TraceView / TraceToolsView 头部行尾 ─ 填充至满宽（真实渲染验证）。"""

    def _render_component(self, el, width: int = 80):
        from src.tui.ink.element import h  # noqa: F401
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.layout import layout_tree
        from src.tui.ink import components as _components
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        rec.render(root, el, width, 40)
        layout_tree(root, width)
        return _components.render_frame(root, width)

    def test_trace_view_header_fills_width(self):
        """TraceView 头部行行宽 == width（行尾 ─ 填充真实生效）。"""
        from src.tui.ink.element import h
        from src.tui.app.trace_view import TraceView
        model = AppModel()
        model.fullscreen = "trace"
        frame = self._render_component(h(TraceView, {"model": model, "width": 80}))
        assert frame.lines, "TraceView 应渲染头部行"
        head = frame.lines[0]
        assert sum(getattr(r, "width", 1) for r in head.runs) == 80
        assert head.runs[-1].text.startswith("\u2500"), "行尾应为 ─ 填充"

    def test_trace_tools_view_header_fills_width(self):
        """TraceToolsView 头部行行宽 == width（行尾 ─ 填充真实生效）。"""
        from src.tui.ink.element import h
        from src.tui.app.trace_tools_view import TraceToolsView
        model = AppModel()
        model.fullscreen = "trace_tools"
        frame = self._render_component(h(TraceToolsView, {"model": model, "width": 80}))
        assert frame.lines, "TraceToolsView 应渲染头部行"
        head = frame.lines[0]
        assert sum(getattr(r, "width", 1) for r in head.runs) == 80
        assert head.runs[-1].text.startswith("\u2500"), "行尾应为 ─ 填充"


# ═══════════════════════════════════════════════════════════
# 9. 弹窗标题色统一 45
# ═══════════════════════════════════════════════════════════

class TestPopupTitleColor:
    """三处弹窗标题统一亮青 45 加粗。"""

    def test_build_popup_lines_title_color(self):
        from src.tui.app._popup_builder import _build_popup_lines
        from src.tui.app.model import CompletionState
        c = CompletionState()
        c.visible = True
        c.items = ["/help", "/model"]
        c.texts = list(c.items)
        c.types = ["command", "command"]
        c.title = "命令"
        lines = _build_popup_lines(c, 80, time.monotonic())
        head = lines[0]
        # 标题行 ▍ 前缀色 45
        assert head.runs[0].style is not None and head.runs[0].style.fg == 45
        assert head.runs[0].style.bold

    def test_completion_popup_title_color(self):
        """CompletionPopup 组件标题行色 45（行为断言——真实渲染）。"""
        from src.tui.ink.element import h
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.layout import layout_tree
        from src.tui.ink import components as _components
        from src.tui.app.input_area import CompletionPopup
        from src.tui.app.model import AppModel
        c = AppModel().completion
        c.visible = True
        c.items = ["/help", "/model"]
        c.texts = list(c.items)
        c.types = ["command", "command"]
        c.title = "命令"
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        rec.render(root, h(CompletionPopup, {"completion": c, "width": 80}), 80, 40)
        layout_tree(root, 80)
        frame = _components.render_frame(root, 80)
        head = frame.lines[0]
        assert head.runs[0].style is not None and head.runs[0].style.fg == 45
        assert head.runs[0].style.bold


# ═══════════════════════════════════════════════════════════
# 10. review 修复项回归测试（P0/P1/P2/P3 清零）
# ═══════════════════════════════════════════════════════════

def _render_component(component, props, fiber=None):
    """在手动 fiber 上下文渲染控件（复用/新建 fiber——hook 状态保持语义）。"""
    from src.tui.ink import hooks
    from src.tui.ink.fiber import Fiber, TAG_FUNCTION
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    else:
        fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        el = component(dict(props))
    finally:
        hooks._pop_current()
    return fiber, el


def _get_input_handler(fiber):
    """取 fiber 上注册的 use_input handler。"""
    for hook in fiber.hooks:
        if getattr(hook, "handler", None) is not None:
            return hook.handler
    raise AssertionError("fiber 上未找到 use_input handler")


class TestSelectInputVimNavGating:
    """P0 修复：单字符 vim 导航门控 consume_all。"""

    def _event(self, ch: str):
        from src.tui._input_parser import KeyEvent
        return KeyEvent(kind="char", char=ch, raw=ch.encode())

    def test_single_char_not_consumed_without_consume_all(self):
        """consumeAll=False（补全弹窗）：单字符 j 不消费不导航——放行进输入缓冲。"""
        from src.tui.ink.widgets import SelectInput
        highlights = []
        fiber, _ = _render_component(SelectInput, {
            "items": ["a", "b", "c"], "initialIndex": 0,
            "consumeAll": False, "onHighlight": highlights.append,
        })
        handler = _get_input_handler(fiber)
        assert handler(self._event("j")) is False   # 不消费（修复前 True 吞掉）
        assert highlights == []

    def test_single_char_consumed_with_consume_all(self):
        """consumeAll=True（模态弹窗）：单字符 j/k 导航保持（零回归）。"""
        from src.tui.ink.widgets import SelectInput
        highlights = []
        fiber, _ = _render_component(SelectInput, {
            "items": ["a", "b", "c"], "initialIndex": 0,
            "consumeAll": True, "onHighlight": highlights.append,
        })
        handler = _get_input_handler(fiber)
        assert handler(self._event("j")) is True
        assert highlights == [1]


class TestSelectControlledIndex:
    """P1 修复：SelectInput 受控 index prop——外部选中同步内部高亮。"""

    def _event(self):
        from src.tui._input_parser import KeyEvent
        return KeyEvent(kind="enter", char="", raw=b"\r")

    def test_controlled_index_syncs_internal_state(self):
        """外部 index=2 写回后：enter 选择 items[2]（高亮与外部选中一致）。"""
        from src.tui.ink.widgets import SelectInput
        selected_items = []
        props = {
            "items": ["a", "b", "c", "d"],
            "initialIndex": 0,
            "consumeAll": False,
            "onSelect": selected_items.append,
            "index": 0,
        }
        fiber, _ = _render_component(SelectInput, props)
        handler = _get_input_handler(fiber)
        # 外部权威源改写 index=2（如 InputDispatcher 旧路径 PgUp/PgDn）——
        # 复用同一 fiber 重渲染（模拟下一帧）
        props2 = dict(props, index=2)
        fiber, _ = _render_component(SelectInput, props2, fiber)
        handler = _get_input_handler(fiber)
        assert handler(self._event()) is True
        assert selected_items and selected_items[-1]["value"] == "c"

    def test_uncontrolled_mode_unchanged(self):
        """不传 index（缺省 None）：纯非受控——enter 选择内部导航后的项。"""
        from src.tui.ink.widgets import SelectInput
        selected_items = []
        fiber, _ = _render_component(SelectInput, {
            "items": ["a", "b", "c"], "initialIndex": 1,
            "consumeAll": False, "onSelect": selected_items.append,
        })
        handler = _get_input_handler(fiber)
        assert handler(self._event()) is True
        assert selected_items[-1]["value"] == "b"


class TestTreeKeyValueColoring:
    """BEAUTY-36 + P2-2 修复：树节点键/值分色（死常量落实）。"""

    def test_key_value_split_coloring(self):
        from src.tui.app.trace_view import _tree_node_rows, _S_TREE_KEY, _S_TREE_VAL, _S_TEXT
        nodes = [
            {"label": "command: ls -la", "children": []},
            {"label": "嵌套 (2 项)", "children": [
                {"label": "[0]: a.txt", "children": []},
            ]},
        ]
        out = []
        _tree_node_rows(nodes, 60, out)
        assert len(out) == 3
        # "command: ls -la" → 键（含指示符）TREE_KEY + 值 TREE_VAL
        r0 = out[0]
        assert r0[0].style is _S_TREE_KEY and r0[0].text.endswith("command: ")
        assert r0[1].style is _S_TREE_VAL and r0[1].text == "ls -la"
        # 容器行（无 ": "）整行 _S_TEXT
        r1 = out[1]
        assert len(r1) == 1 and r1[0].style is _S_TEXT


class TestInspectorDepsSafeInt:
    """P3 修复：_inspector_deps 异常注入值（str/NaN/inf）不中断。"""

    def test_safe_int_defensive(self):
        from src.tui.app.trace_view import _safe_int
        assert _safe_int("abc") == 0
        assert _safe_int(float("nan")) == 0
        assert _safe_int(float("inf")) == 0
        assert _safe_int(None) == 0
        assert _safe_int(42) == 42
        assert _safe_int(3.9) == 3

    def test_inspector_deps_with_invalid_values(self):
        from src.tui.app.trace_view import _inspector_deps
        rec = SimpleNamespace(
            kind="content", index=0, status="", source_block=None, lines=None,
            time_seconds="bad", tokens={"input": float("nan"), "output": "x"},
        )
        deps = _inspector_deps(rec, 40, 24)   # 不抛异常
        assert 0 in deps                       # 归一化回退 0


class TestApplyDefensiveFixes:
    """P3 修复：splash 版本防御 / bg_bash_count Overflow / tool_summary 状态。"""

    def test_splash_version_import_failure_fallback(self, monkeypatch):
        """VERSION 导入失败（模块缺属性）→ 仍渲染 ✦ + 模型名（不丢启动屏）。"""
        import sys as _sys
        monkeypatch.setitem(_sys.modules, "src.app_init._args", SimpleNamespace())
        model = AppModel()
        model.status.model_name = "m1"
        apply_cmd(model, SplashCmd())
        plain = model.blocks[0].lines[0].plain
        assert "\u2726" in plain and "m1" in plain

    def test_bg_bash_count_overflow_no_crash(self):
        """count=inf → OverflowError 捕获回退 0（不更新异常值）。"""
        from src.tui._const import BgBashCountCmd
        model = AppModel()
        apply_cmd(model, BgBashCountCmd(count=float("inf")))
        assert model.status.bg_bash_count == 0

    def test_tool_summary_respects_fail_status(self):
        """残留 fail 状态 box 防御关闭时保持失败位（✖ 而非 ✔）。"""
        from src.tui._const import RenderCommand
        from src.tui.app.apply import _HANDLERS
        model = AppModel()
        model.open_tool_box("t1", "bash", "pwd")
        model.tool_boxes["t1"].extra["tool_status"] = "fail"
        _HANDLERS[RenderCommand.TOOL_SUMMARY](model, None)
        assert "t1" not in model.tool_boxes
        block = model.blocks[0]
        assert block.extra.get("tool_status") == "fail"

    def test_error_role_header_live_false_static(self):
        """P3 修复：error 头 live=False 时不呼吸（静态红，防冻结随机帧）。"""
        from src.tui.app._theme import time_glow
        from src.tui.app._model_helpers import _role_header_runs
        # 未关闭 error 块 + live=False（提交/冻结路径）→ 静态 196
        block = SimpleNamespace(kind="error", closed=False, extra={})
        runs = _role_header_runs(block, live=False)
        assert runs[0].style.fg == 196
        # live=True（每帧渲染路径）→ 呼吸色
        runs2 = _role_header_runs(block, live=True)
        assert 196 <= runs2[0].style.fg <= 208


class TestTraceToolsBudgetGuard:
    """P3 修复：检查器分割线/小节标题追加前检查预算（不超视口）。"""

    def test_budget_exhausted_no_separator_append(self):
        from src.tui.app.trace_tools_view import _inspector_children
        desc = "描述行" * 30     # 长描述（wrap 后 ≥4 行填满 budget=4）
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"], desc,
            right_w=30, vh=5,   # budget = max(4, 5-2) = 4
        )
        texts = [str(c.props.get("children", "")) for c in children]
        # desc 行填满预算后：分割线/小节标题不追加（修复前 +2 行超预算）
        assert not any(t.startswith("\u2500") for t in texts[2:]), "预算外不应追加分割线"
        assert not any(t.strip() == "\u25b8 \u53c2\u6570" for t in texts[2:]), "预算外不应追加小节标题"
        # 常规预算（vh 充足）：分割线 + 小节标题正常追加（零回归）
        children2 = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"], "短描述",
            right_w=30, vh=30,
        )
        texts2 = [str(c.props.get("children", "")) for c in children2]
        assert any(t.startswith("\u2500") for t in texts2)
        assert any(t.strip() == "\u25b8 \u53c2\u6570" for t in texts2)
