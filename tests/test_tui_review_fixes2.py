"""TUI 第二轮 review 修复（2026-08-18）回归测试。

覆盖第二轮 review 报告的 P2/P3 修复：
  1. P2  trace_tools_view use_memo deps 展平原子（嵌套 tuple 恒 miss）
  2. P2  codeblock 侧边竖线用 chars[5]（borderStyle 联动）
  3. P2  ZStack 无显式高度 debug 提示（契约声明 + 可观测）
  4. P2  trace._param_node 非 dict pinfo 防御（畸形 schema 不崩溃）
  5. P3  _input_io 短突发降级清空 _paste_partial
  6. P3  _style_utils 颜色名键小写（bright* 系列命中）
  7. P3  trace 增量缓存键补工具卡标题指纹（原地标题替换感知）
  8. P3  tree 边界方向键返回 False 放行
  9. P3  tabs activeStyle/inactiveStyle is-not-None 判断
  10. P3 gradient 按累计显示宽度归一化渐变进度
  11. P3 listview 受控模式同批连续导航基准
  12. P3 _ledger_renderer 死参数删除
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.core.style import Style
from src.tui.ink import h, StyledRun
from src.tui.ink.element import Element, TEXT
from src.tui.ink.fiber import InputHook
from src.tui.ink.reconciler import Reconciler


# ═══════════════════════════════════════════════════════════
# 组件测试基建（参考 tests/test_renderer_popup_overlap.py 模式）
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 2. P2 — codeblock 侧边竖线 chars[5]
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 3. P2 — ZStack 塌缩契约提示
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 4. P2 — _param_node 畸形 schema 防御
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 5. P3 — try_read_paste 降级清空 _paste_partial
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 6. P3 — _parse_color bright* 键小写命中
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 7. P3 — trace 增量缓存键补标题指纹
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 8. P3 — tree 边界方向键放行
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 9. P3 — tabs 样式 is-not-None 判断
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 10. P3 — gradient 按显示宽度归一化
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 11. P3 — listview 受控模式同批连续导航
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 12. P3 — _ledger_renderer 签名收紧
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# 1. P2 — trace_tools_view deps 展平（组件冒烟）
# ═══════════════════════════════════════════════════════════

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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
