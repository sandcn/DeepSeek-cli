"""轨迹 Trace vim 风格搜索测试（2026-08-19 用户需求）。

需求：输入 "/" 和内容然后回车可以查找（这里要显示光标回车后不显示），
查找到要把行定位到那里并且高亮背景，n 和 p 切换下上个行定位到那里，
左右都能用，像 vim 一样，要支持正则。

实现固化项（用户确认）：
  1. 搜索范围 = 当前焦点面板（A 方案）：台账搜记录全文 / 检查器搜内容行；
     "/" 在左台账/右检查器焦点均可进入搜索输入模式；
  2. 搜索输入模式：底部显示 ``/${query}`` + 光标（"这里要显示光标"）；
     回车执行后输入行消失（"回车后不显示"）；字符累积、退格删除、
     Esc 取消输入（保留已执行搜索）；
  3. 匹配：正则 ``re.search``（子串匹配；非法正则 → 无匹配不崩溃）；
     回车定位到首个匹配（台账→选中记录并滚动 / 检查器→光标行并滚动）；
  4. 高亮：**所有匹配行**背景高亮（vim hlsearch 风格，_S_SEARCH_BG 236）、
     当前匹配行亮蓝（_S_SEARCH_CUR_BG 25）——左右两栏都生效；
  5. 键位：n = 下一个 / N = 上一个（vim 标准；p 为用户原话 prev 兼容
     别名），环绕切换并定位；
  6. 状态清理：切换记录（检查器搜索失效）/ 折叠树（检查器搜索失效）/
     进入 subagent 轨迹 / 进入工具列表视图 / 关闭视图 / 清屏 → 清除搜索。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app.trace import TraceRecord
from src.tui.ink import h
from src.tui.ink.fiber import InputHook
from src.tui.ink.reconciler import Reconciler


def _render_root(component, props, width=100, height=24):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(component, props), width, height)
    return rec, root


def _find_input_handler(fiber):
    """查找 fiber 树中第一个活跃 use_input handler。"""
    if fiber is None:
        return None
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    r = _find_input_handler(fiber.child)
    if r is not None:
        return r
    return _find_input_handler(fiber.sibling)


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _plain_rec(index, summary, **kw):
    base = dict(
        index=index, kind="user", summary=summary, status="", time_seconds=None,
        time_started=None, time_started_monotonic=True, tokens={}, result="",
        lines=[], source_block=None, subagent_label="", tool_call_id="",
        tool_args=None, tool_result="",
    )
    base.update(kw)
    return TraceRecord(**base)


# ═══════════════════════════════════════════════════════════
# 1. 纯函数：匹配计算
# ═══════════════════════════════════════════════════════════

class TestTraceSearchMatches:

    def test_ledger_search(self):
        """台账搜索：记录全文正则匹配（summary/lines/result/工具参数）。"""
        from src.tui.app.trace_view import _trace_search_matches
        recs = [
            _plain_rec(1, "hello world"),
            _plain_rec(2, "foo bar"),
            _plain_rec(3, "hello again"),
        ]
        assert _trace_search_matches("hello", "ledger", recs) == [0, 2]
        assert _trace_search_matches("bar", "ledger", recs) == [1]
        assert _trace_search_matches("zzz", "ledger", recs) == []

    def test_ledger_search_full_text(self):
        """台账搜索覆盖返回预览/详情行/工具参数。"""
        from src.tui.app.trace_view import _trace_search_matches
        recs = [
            _plain_rec(1, "s", result="RESULT-xyz", lines=["line-abc"],
                       kind="tool", tool_args='{"cmd": "run-123"}',
                       tool_result="out-qwe"),
        ]
        assert _trace_search_matches("RESULT-xyz", "ledger", recs) == [0]
        assert _trace_search_matches("line-abc", "ledger", recs) == [0]
        assert _trace_search_matches("run-123", "ledger", recs) == [0]
        assert _trace_search_matches("out-qwe", "ledger", recs) == [0]

    def test_inspector_search(self):
        """检查器搜索：内容行正则匹配（StyledRun 行/纯文本行）。"""
        from src.tui.app.trace_view import (
            _trace_search_matches,
        )
        from src.tui.ink import StyledRun
        rows = ["alpha beta", [StyledRun("gamma delta", None)], "hello"]
        assert _trace_search_matches("beta", "inspector", [], rows) == [0]
        assert _trace_search_matches("delta", "inspector", [], rows) == [1]
        assert _trace_search_matches("hello", "inspector", [], rows) == [2]

    def test_regex_support(self):
        """支持正则（行首/行尾/字符类/分组）。"""
        from src.tui.app.trace_view import _trace_search_matches
        recs = [_plain_rec(1, "abc123"), _plain_rec(2, "xyz")]
        assert _trace_search_matches(r"^\d+$", "ledger", recs) == []
        assert _trace_search_matches(r"\d+", "ledger", recs) == [0]
        assert _trace_search_matches(r"a.c", "ledger", recs) == [0]
        assert _trace_search_matches(r"(?:xyz)$", "ledger", recs) == [1]

    def test_invalid_regex_no_crash(self):
        """非法正则 → 空匹配（不崩溃）。"""
        from src.tui.app.trace_view import _trace_search_matches
        recs = [_plain_rec(1, "abc")]
        assert _trace_search_matches("[", "ledger", recs) == []
        assert _trace_search_matches("(", "ledger", recs) == []

    def test_empty_pattern(self):
        """空 pattern → 空匹配。"""
        from src.tui.app.trace_view import _trace_search_matches
        recs = [_plain_rec(1, "abc")]
        assert _trace_search_matches("", "ledger", recs) == []
        assert _trace_search_matches("  ", "ledger", recs) == []


# ═══════════════════════════════════════════════════════════
# 2. model 字段
# ═══════════════════════════════════════════════════════════

class TestModelSearchFields:

    def test_defaults(self):
        """默认：无搜索（mode=False、pattern=""、matches=[]、idx=-1）。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        assert model.trace_search_mode is False
        assert model.trace_search_query == ""
        assert model.trace_search_pattern == ""
        assert model.trace_search_side == ""
        assert model.trace_search_matches == []
        assert model.trace_search_idx == -1

    def test_reset_display_resets(self):
        """reset_display 复位全部搜索字段。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.trace_search_mode = True
        model.trace_search_query = "abc"
        model.trace_search_pattern = "abc"
        model.trace_search_side = "ledger"
        model.trace_search_matches = [1, 3]
        model.trace_search_idx = 1
        model.reset_display()
        assert model.trace_search_mode is False
        assert model.trace_search_query == ""
        assert model.trace_search_pattern == ""
        assert model.trace_search_side == ""
        assert model.trace_search_matches == []
        assert model.trace_search_idx == -1


# ═══════════════════════════════════════════════════════════
# 3. TraceView 组件：/ 输入 → 回车执行 → n/N/p 切换
# ═══════════════════════════════════════════════════════════

class TestTraceViewSearch:

    @pytest.fixture(autouse=True)
    def _pin_records(self, monkeypatch):
        from src.tui.app import trace_view as tv
        self.recs = [
            _plain_rec(1, "hello world"),
            _plain_rec(2, "foo bar"),
            _plain_rec(3, "hello again"),
        ]
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda model: (self.recs, self.recs))

    def _setup(self):
        from src.tui.app.model import AppModel
        from src.tui.app.trace_view import TraceView
        model = AppModel()
        model.fullscreen = "trace"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        return model, _find_input_handler(root)

    def _type(self, handler, text):
        for ch in text:
            assert handler(_ev("char", ch)) is True

    def test_slash_enters_search_mode(self):
        """"/" 进入搜索输入模式（query 预填上次 pattern，光标输入行显示）。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        assert model.trace_search_mode is True
        assert model.trace_search_query == ""
        # 再次 "/" 预填上次 pattern（vim 语义）
        self._type(handler, "hello")
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == "hello"
        assert handler(_ev("char", "/")) is True
        assert model.trace_search_query == "hello"

    def test_enter_executes_and_hides_input(self):
        """回车执行搜索：输入行消失（mode=False）、定位到首个匹配。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "hello")
        assert model.trace_search_mode is True
        assert handler(_ev("enter")) is True
        assert model.trace_search_mode is False  # 回车后不显示
        assert model.trace_search_pattern == "hello"
        assert model.trace_search_side == "ledger"
        assert model.trace_search_matches == [0, 2]
        assert model.trace_search_idx == 0
        assert model.trace_selected == 0  # 定位到首个匹配

    def test_n_next_N_prev_p_alias(self):
        """n 下一个 / N、p 上一个（环绕切换并定位）。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "hello")
        assert handler(_ev("enter")) is True
        # n → 下一个匹配（1 → 记录 2）
        assert handler(_ev("char", "n")) is True
        assert model.trace_search_idx == 1
        assert model.trace_selected == 2
        # n 环绕（末 → 首）
        assert handler(_ev("char", "n")) is True
        assert model.trace_search_idx == 0
        assert model.trace_selected == 0
        # N 上一个（首 → 末）
        assert handler(_ev("char", "N")) is True
        assert model.trace_search_idx == 1
        assert model.trace_selected == 2
        # p 兼容别名（用户原话 prev）
        assert handler(_ev("char", "p")) is True
        assert model.trace_search_idx == 0
        assert model.trace_selected == 0

    def test_backspace_edits_query(self):
        """退格删除输入字符（空 query 退格也吞掉——搜索输入模式全事件消费）。

        ★ 2026-08-20（review P3）：空 query 退格由返回 False（放行）改为
        返回 True（搜索输入模式吞掉所有未识别事件——vim 搜索输入模式不
        导航）。
        """
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "he")
        assert handler(_ev("backspace")) is True
        assert model.trace_search_query == "h"
        assert handler(_ev("backspace")) is True
        assert model.trace_search_query == ""
        assert handler(_ev("backspace")) is True  # 空 query 仍吞掉（不导航）

    def test_escape_cancels_input_keeps_search(self):
        """Esc 取消输入模式（保留已执行搜索）。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "hello")
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == "hello"
        # 再次进入输入，Esc 取消
        assert handler(_ev("char", "/")) is True
        model.trace_search_query = "zzz"
        assert handler(_ev("escape")) is True
        assert model.trace_search_mode is False
        assert model.trace_search_pattern == "hello"  # 保留上次搜索
        assert model.trace_search_matches == [0, 2]

    def test_empty_query_clears_search(self):
        """空查询回车 → 清除搜索（无高亮）。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "hello")
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == "hello"
        # 空查询执行
        assert handler(_ev("char", "/")) is True
        while model.trace_search_query:
            handler(_ev("backspace"))
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == ""
        assert model.trace_search_matches == []
        assert model.trace_search_idx == -1

    def test_no_match(self):
        """无匹配 → matches 空、idx=-1（不定位、不高亮）。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "zzz")
        assert handler(_ev("enter")) is True
        assert model.trace_search_matches == []
        assert model.trace_search_idx == -1
        assert model.trace_search_pattern == "zzz"

    def test_regex_search(self):
        """正则搜索（行尾锚定）→ 正确匹配并定位。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "again$")
        assert handler(_ev("enter")) is True
        assert model.trace_search_matches == [2]
        assert model.trace_selected == 2

    def test_invalid_regex_no_crash(self):
        """非法正则 → 无匹配（不崩溃）。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "[")
        assert handler(_ev("enter")) is True
        assert model.trace_search_matches == []
        assert model.trace_search_idx == -1

    def test_whitespace_pattern_kept(self):
        """首尾空格是 pattern 一部分（不 strip——vim 语义）。

        ★ 2026-08-20（review P3）：修复前 ``_exec_search`` 里
        ``pattern.strip()`` 去除首尾空格——无法搜索含首尾空格的 pattern，
        纯空格 pattern 被当作空清除。现保留原始输入——空串（直接回车）
        才清除搜索。
        """
        model, handler = self._setup()
        # 含首尾空格 pattern：精确匹配含空格的文本
        assert handler(_ev("char", "/")) is True
        self._type(handler, "hello ")
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == "hello "
        assert model.trace_search_matches == [0, 2]
        # 纯空格 pattern 合法（搜索空格——三条记录摘要均含空格）
        assert handler(_ev("char", "/")) is True
        while model.trace_search_query:
            handler(_ev("backspace"))
        self._type(handler, " ")
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == " "
        assert model.trace_search_matches == [0, 1, 2]

    def test_search_mode_swallows_navigation(self):
        """搜索输入模式未识别事件全部吞掉（方向键不导航台账）。

        ★ 2026-08-20（review P3）：修复前搜索模式对未识别事件 return False
        放行——台账焦点时 ListView 仍激活消费方向键/翻页 → 搜索输入中按
        ↑↓ 意外导航选中记录（vim 中搜索输入模式不导航）。
        """
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_up")) is True
        assert handler(_ev("page_down")) is True
        assert handler(_ev("home")) is True
        assert handler(_ev("end")) is True
        assert model.trace_selected == -1  # 尾部跟随未变（未导航）
        # 字符仍累积进 query（vim 语义：搜索输入模式接受字符）
        assert handler(_ev("char", "j")) is True
        assert model.trace_search_query == "j"

    def test_query_length_cap(self):
        """query 长度上限——超长输入截断丢弃。

        ★ 2026-08-20（review P3）：底部 ``/${query}`` 渲染行按栏宽截断，
        无上限累积只浪费内存（输入侧 ``_SEARCH_QUERY_MAX`` 限制 + 渲染侧
        截断双保险）。
        """
        from src.tui.app.trace_view import _SEARCH_QUERY_MAX
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        for _ in range(_SEARCH_QUERY_MAX + 50):
            assert handler(_ev("char", "a")) is True
        assert len(model.trace_search_query) == _SEARCH_QUERY_MAX
        # 退格仍可删除
        assert handler(_ev("backspace")) is True
        assert len(model.trace_search_query) == _SEARCH_QUERY_MAX - 1

    def test_inspector_search_locate_cursor(self, monkeypatch):
        """检查器焦点搜索内容行 → 定位光标到匹配行（side=inspector）。

        ★ 2026-08-20（review P3）：``tv.build_trace_records = lambda`` 直接
        赋值污染模块属性 → 改 ``monkeypatch.setattr``（测试隔离）。
        """
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        rec = _plain_rec(1, "content", kind="content", lines=["alpha", "beta"])
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec], [rec]))
        from src.tui.app.model import AppModel
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec_, root = _render_root(TraceView, {"model": model, "width": 100})
        handler = _find_input_handler(root)
        assert handler(_ev("char", "/")) is True
        for ch in "beta":
            assert handler(_ev("char", ch)) is True
        assert handler(_ev("enter")) is True
        assert model.trace_search_side == "inspector"
        assert model.trace_search_matches  # 至少一个匹配行
        assert model.trace_search_idx == 0
        # 光标定位到匹配行
        assert model.trace_inspector_cursor == model.trace_search_matches[0]
        # n 下一个（多个匹配时切换）
        if len(model.trace_search_matches) > 1:
            assert handler(_ev("char", "n")) is True
            assert model.trace_search_idx == 1
            assert model.trace_inspector_cursor == model.trace_search_matches[1]

    def test_search_line_rendered_in_mode(self):
        """搜索输入模式 → 底部渲染 ``/${query}`` 行（含光标 ▏）；回车后消失。"""
        model, handler = self._setup()
        # 渲染期 search_mode=False → 无搜索行
        assert handler(_ev("char", "/")) is True
        model.trace_search_query = "hello"
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        # 查找底部搜索行（遍历 fiber 找 children 含 "/hello" 的 TEXT）
        found = []
        def walk(f):
            if f is None:
                return
            props = getattr(f, "props", None) or {}
            ch = props.get("children")
            if isinstance(ch, str) and ch.startswith("/hello"):
                found.append(ch)
            walk(getattr(f, "child", None))
            walk(getattr(f, "sibling", None))
        walk(root)
        assert found, "搜索输入行应渲染 /hello"
        # 回车后 mode=False → 重新渲染无搜索行
        handler = _find_input_handler(root)
        assert handler(_ev("enter")) is True
        assert model.trace_search_mode is False

    def test_navigate_resets_inspector_search(self, monkeypatch):
        """切换记录 → 检查器搜索清除（台账搜索保留）。

        ★ 2026-08-20（review P3）：直接赋值改 ``monkeypatch.setattr``。
        """
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        recs = [self.recs[0], self.recs[1]]
        monkeypatch.setattr(tv, "build_trace_records", lambda model: (recs, recs))
        from src.tui.app.model import AppModel
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_selected = 0
        model.trace_search_side = "inspector"
        model.trace_search_pattern = "beta"
        model.trace_search_matches = [0]
        model.trace_search_idx = 0
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True  # 台账导航
        assert model.trace_search_pattern == ""
        # 台账搜索保留
        model.trace_search_side = "ledger"
        model.trace_search_pattern = "hello"
        model.trace_search_matches = [0]
        model.trace_search_idx = 0
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True
        assert model.trace_search_pattern == "hello"

    def test_escape_closes_clears_search(self):
        """关闭视图（Esc）→ 搜索状态清除。"""
        model, handler = self._setup()
        assert handler(_ev("char", "/")) is True
        self._type(handler, "hello")
        assert handler(_ev("enter")) is True
        assert model.trace_search_pattern == "hello"
        assert handler(_ev("escape")) is True
        assert model.trace_open is False
        assert model.trace_search_pattern == ""


# ═══════════════════════════════════════════════════════════
# 4. 匹配高亮渲染
# ═══════════════════════════════════════════════════════════

class TestSearchHighlight:

    def test_ledger_row_runs_highlight(self):
        """台账行：匹配行背景 236、当前匹配行亮蓝 25、无搜索不高亮。"""
        from src.tui.app.trace_view import _ledger_row_runs
        rec = _plain_rec(1, "hello")
        plain = _ledger_row_runs(rec, False, 40)
        matched = _ledger_row_runs(rec, False, 40, matched=True)
        cur = _ledger_row_runs(rec, True, 40, matched=True, cur_match=True)
        assert not any(r.style is not None and r.style.bg == 236 for r in plain)
        assert any(r.style is not None and r.style.bg == 236 for r in matched)
        assert any(r.style is not None and r.style.bg == 25 for r in cur)

    def test_inspector_children_highlight(self):
        """检查器内容行：匹配行背景高亮、当前匹配亮蓝、非匹配不高亮。"""
        from src.tui.app.trace_view import _inspector_children
        rec = _plain_rec(1, "s", kind="content", lines=["alpha", "beta"])
        children = _inspector_children(
            rec, 40, 20, scroll=0, content_rows=["alpha", "beta"],
            cursor=-1, search_matches=[1], search_cur=1,
        )
        texts = [str(c.props.get("children", "")) for c in children]
        beta_el = children[texts.index("beta")]
        alpha_el = children[texts.index("alpha")]
        assert beta_el.props.get("style") is not None
        assert beta_el.props["style"].bg == 25  # 当前匹配亮蓝
        st_a = alpha_el.props.get("style")
        assert st_a is None or st_a.bg not in (25, 236)

    def test_ledger_renderer_passes_match(self):
        """_ledger_renderer 匹配信息传递（匹配行 styled 含搜索背景）。"""
        from src.tui.app.trace_view import _ledger_renderer
        rec = _plain_rec(1, "hello")
        render_item = _ledger_renderer([rec], 40, {id(rec)}, id(rec))
        el = render_item(rec, 0, True)
        styled = el.props["styled"]
        assert any(r.style is not None and r.style.bg == 25 for r in styled)
