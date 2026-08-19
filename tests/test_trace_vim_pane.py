"""轨迹 Trace vim 面板浏览测试（2026-08-19 用户需求）。

需求：轨迹 Trace 可以**移动到右边查看东西**并且**像 vim 一样**。

实现固化项：
  1. ListView 支持 vim 键 ``j/J``（下）/``k/K``（上）——大小写等效（与
     SelectInput ``_nav_for_char`` 语义一致），台账 / 工具列表自动获得
     vim 上下导航；
  2. TraceView 面板焦点 ``model.trace_pane``（"ledger"=左台账 /
     "inspector"=右检查器）：``l`` 从台账移到检查器（右栏滚动查看详情）、
     ``h`` 返回台账；
  3. 检查器内容**全量生成 + 滚动窗口**（``model.trace_inspector_scroll``）：
     j/k/↑↓ 逐行、PgUp/PgDn 翻页、g/G/Home/End 首末；scroll>0 置顶
     「… 前 N 行省略」、未到尾部后置「… 后 N 行省略」；
  4. 切换记录 / 进入 subagent 轨迹 / 进入工具列表视图复位 pane/scroll；
  5. trace_tools_view（工具列表详情视图）同样 vim 面板浏览
     （``model.trace_tools_pane`` / ``model.trace_tools_scroll``）；
  6. model 字段默认值 + ``reset_display`` 复位。
"""

from __future__ import annotations

import time as _time
from types import SimpleNamespace

import pytest

from src.tui.app.trace import TraceRecord
from src.tui.ink import h
from src.tui.ink.fiber import InputHook
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.widgets.listview import ListView


def _render_root(component, props, width=80, height=24):
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


def _plain_rec(lines, **kw):
    """纯文本记录（检查器内容走 wrap 路径）。"""
    base = dict(
        index=0, kind="user", summary="rec", status="", time_seconds=None,
        time_started=None, time_started_monotonic=True, tokens={}, result="",
        lines=list(lines), source_block=None, subagent_label="",
        tool_call_id="", tool_args=None, tool_result="",
    )
    base.update(kw)
    return TraceRecord(**base)


# ═══════════════════════════════════════════════════════════
# 1. ListView vim 键 j/k（大小写等效、边界、分隔行、批内连续）
# ═══════════════════════════════════════════════════════════

class TestListViewVimKeys:

    def test_j_moves_down(self):
        """j → 下移一项（与 arrow_down 等价）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "j")) is True
        assert nav == [1]

    def test_k_moves_up(self):
        """k → 上移一项（与 arrow_up 等价）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "cursor": 2, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "k")) is True
        assert nav == [1]

    def test_J_K_uppercase_equivalent(self):
        """J/K 大写等效（与 SelectInput _nav_for_char 语义一致）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "cursor": 1, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "J")) is True
        assert handler(_ev("char", "K")) is True
        assert nav == [2, 1]

    def test_j_at_last_releases(self):
        """末项按 j 无移动 → 放行（返回 False，与 arrow_down 一致）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1"], "height": 2,
            "cursor": 1, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "j")) is False
        assert nav == []

    def test_k_at_first_releases(self):
        """首项按 k 无移动 → 放行。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1"], "height": 2,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "k")) is False
        assert nav == []

    def test_j_skips_separator_rows(self):
        """j 跳过分隔行（None 不可选）落到下一个可选项。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", None, "i1", "i2"], "height": 3,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "j")) is True
        assert nav == [2]

    def test_batch_jj_k_continuous(self):
        """批内连续导航（无中间渲染）：jj 下移 2、k 回移 1。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "j")) is True
        assert handler(_ev("char", "j")) is True
        assert handler(_ev("char", "k")) is True
        assert nav == [1, 2, 1]

    def test_multi_char_paste_not_nav(self):
        """多字符 char 事件（粘贴流 "jj"）不导航 → 放行（零回归）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "jj")) is False
        assert nav == []

    def test_focus_false_not_active(self):
        """focus=False → use_input 不激活（检查器焦点时 ListView 放行）。"""
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1"], "height": 2, "focus": False,
        })
        assert _find_input_handler(root.child) is None

    def test_g_G_still_work(self):
        """g/G 首末既有语义零回归。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 3,
            "cursor": 1, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "g")) is True
        assert handler(_ev("char", "G")) is True
        assert nav == [0, 3]


# ═══════════════════════════════════════════════════════════
# 2. 检查器全量内容行（_inspector_content_rows，trace_view）
# ═══════════════════════════════════════════════════════════

class TestInspectorContentRows:

    def test_plain_lines_wrapped(self):
        """纯文本记录 → 按栏宽换行的全量内容行（正序）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _plain_rec(["a" * 100, "b"])
        rows, keys = _inspector_content_rows(rec, 40)
        assert len(rows) >= 4  # 100 宽拆 3 行 + "b" 1 行
        assert rows[0] == "a" * 40
        assert rows[-1] == "b"
        assert keys == [None] * len(rows)  # 非树行无节点 key

    def test_tool_tree_rows(self):
        """tool 记录（带参数/返回值）→ 树内容行（参数 → 分割线 → 返回值）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _plain_rec(
            ["x"], kind="tool", tool_args='{"command": "ls"}',
            tool_result="out1",
        )
        rows, keys = _inspector_content_rows(rec, 40)
        texts = ["".join(r.text for r in row) if isinstance(row, list) else row
                 for row in rows]
        joined = "\n".join(texts)
        assert "参数" in joined
        assert "返回值" in joined
        assert "command" in joined
        assert "out1" in joined
        # 标量参数树无容器节点 → keys 全 None（与行对齐）
        assert len(keys) == len(rows)
        assert all(k is None for k in keys)

    def test_md_rows(self):
        """reasoning/content/system → markdown 渲染行（StyledRun 列表）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _plain_rec(["**bold** text"], kind="content")
        rows, keys = _inspector_content_rows(rec, 40)
        assert rows, "markdown 内容应生成行"
        assert all(isinstance(r, list) for r in rows)
        assert keys == [None] * len(rows)

    def test_max_rows_cap(self):
        """超上限 → 截断 + 追加「内容过长」提示行（滚动到底部可见）。"""
        from src.tui.app.trace_view import (
            _INSPECTOR_MAX_ROWS, _inspector_content_rows,
        )
        rec = _plain_rec([f"line{i}" for i in range(_INSPECTOR_MAX_ROWS + 500)])
        rows, keys = _inspector_content_rows(rec, 40)
        assert len(rows) == _INSPECTOR_MAX_ROWS + 1
        last = rows[-1]
        assert isinstance(last, list)
        assert "内容过长" in "".join(r.text for r in last)
        assert len(keys) == len(rows)

    def test_empty_rec(self):
        """无内容记录 → 空列表（检查器显示 (无内容) 占位）。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = _plain_rec([])
        rows, keys = _inspector_content_rows(rec, 40)
        assert rows == []
        assert keys == []


# ═══════════════════════════════════════════════════════════
# 3. 检查器滚动窗口（_inspector_children scroll，trace_view）
# ═══════════════════════════════════════════════════════════

class TestInspectorScroll:

    def _texts(self, children):
        return [str(c.props.get("children", "")) for c in children]

    def _rec_long(self, n=20):
        return _plain_rec([f"line-{i}" for i in range(n)])

    def test_scroll_zero_shows_head_with_bottom_omitted(self):
        """scroll=0（默认/顶部）：显示头部窗口 + 「… 后 N 行省略」。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(self._rec_long(20), 40, 10)
        texts = self._texts(children)
        assert "line-0" in texts
        assert any(t.startswith("\u2026 后 ") and "行省略" in t for t in texts)
        assert not any(t.startswith("\u2026 前 ") for t in texts)

    def test_scroll_middle_shows_top_and_bottom_omitted(self):
        """scroll 中间：置顶「… 前 N 行省略」+ 窗口 + 后置省略。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(self._rec_long(20), 40, 10, scroll=5)
        texts = self._texts(children)
        assert any(t.startswith("\u2026 前 5 行省略") for t in texts)
        # 窗口从第 5 行开始（line-5）
        content_texts = [t for t in texts if t.startswith("line-")]
        assert content_texts[0] == "line-5"
        assert any(t.startswith("\u2026 后 ") for t in texts)

    def test_scroll_bottom_reaches_end(self):
        """scroll 到底：仅置顶省略提示（无后置省略）。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(self._rec_long(20), 40, 10, scroll=12)
        texts = self._texts(children)
        assert any(t.startswith("\u2026 前 12 行省略") for t in texts)
        assert not any(t.startswith("\u2026 后 ") for t in texts)
        assert "line-19" in texts  # 末行可见

    def test_scroll_clamped_negative_and_excess(self):
        """scroll 越界（负 / 超 max）→ 钳制到合法范围。"""
        from src.tui.app.trace_view import _inspector_children
        neg = _inspector_children(self._rec_long(20), 40, 10, scroll=-5)
        texts_neg = self._texts(neg)
        assert "line-0" in texts_neg  # 钳制到顶部
        assert not any(t.startswith("\u2026 前 ") for t in texts_neg)
        excess = _inspector_children(self._rec_long(20), 40, 10, scroll=999)
        texts_ex = self._texts(excess)
        assert "line-19" in texts_ex  # 钳制到底部
        assert not any(t.startswith("\u2026 后 ") for t in texts_ex)

    def test_scroll_zero_default_backward_compatible(self):
        """省略 scroll 参数（既有调用面）→ 默认 0（向后兼容）。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(self._rec_long(20), 40, 10)
        texts = self._texts(children)
        assert "line-0" in texts
        assert any(t.startswith("\u2026 后 ") for t in texts)

    def test_content_fits_no_omitted(self):
        """内容不足一屏 → 无省略提示、scroll 钳制 0。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(self._rec_long(3), 40, 10, scroll=5)
        texts = self._texts(children)
        assert not any(t.startswith("\u2026") for t in texts)
        assert "line-0" in texts

    def test_subagent_hint_still_appended(self):
        """subagent 记录操作提示「Enter 查看该子代理的轨迹」保留。"""
        from src.tui.app.trace_view import _inspector_children
        rec = _plain_rec(["hi"], subagent_label="sa-1")
        children = _inspector_children(rec, 40, 10)
        texts = self._texts(children)
        assert any("Enter 查看该子代理的轨迹" in t for t in texts)

    def test_empty_shows_no_content(self):
        """无内容记录 → (无内容) 占位。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(_plain_rec([]), 40, 10)
        texts = self._texts(children)
        assert any(t.strip() == "(无内容)" for t in texts)

    # ── 光标行高亮（2026-08-19：右边高亮当前行背景色） ──

    def _bg_of(self, children, text):
        """返回 children 中内容 == text 的行的样式（str 行）。"""
        for c in children:
            if str(c.props.get("children", "")) == text:
                return c.props.get("style")
        raise AssertionError(f"未找到行: {text!r}")

    def test_cursor_row_highlighted(self):
        """cursor 所在行背景高亮（_S_INSP_BG）；其余行不高亮。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(
            self._rec_long(20), 40, 10, scroll=0, cursor=1,
        )
        st_cur = self._bg_of(children, "line-1")
        assert st_cur is not None and st_cur.bg is not None, "光标行应有背景色"
        st_other = self._bg_of(children, "line-0")
        assert st_other is None or st_other.bg is None, "非光标行无背景色"

    def test_cursor_default_no_highlight(self):
        """cursor 缺省（-1）→ 无任何行背景高亮（向后兼容）。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(self._rec_long(5), 40, 10)
        for c in children:
            style = c.props.get("style")
            if style is not None:
                assert style.bg is None, "缺省 cursor 不应有背景高亮"

    def test_cursor_out_of_window_not_highlighted(self):
        """cursor 不在窗口内（scroll 偏移后）→ 无高亮行（窗口外光标）。"""
        from src.tui.app.trace_view import _inspector_children
        children = _inspector_children(
            self._rec_long(20), 40, 10, scroll=5, cursor=0,
        )
        # 窗口从 line-5 开始；cursor=0 在窗口外 → 无行背景高亮
        for c in children:
            style = c.props.get("style")
            if style is not None:
                assert style.bg is None

    def test_cursor_highlight_moves_with_scroll(self):
        """滚动后 cursor 所在窗口内行高亮（滚动+光标协同）。"""
        from src.tui.app.trace_view import _inspector_children
        # scroll=5, cursor=7 → 窗口内第 2 行（line-7）高亮
        children = _inspector_children(
            self._rec_long(20), 40, 10, scroll=5, cursor=7,
        )
        st_cur = self._bg_of(children, "line-7")
        assert st_cur is not None and st_cur.bg is not None
        st_other = self._bg_of(children, "line-5")
        assert st_other is None or st_other.bg is None


# ═══════════════════════════════════════════════════════════
# 4. 检查器 deps 含 scroll（_inspector_deps，trace_view）
# ═══════════════════════════════════════════════════════════

class TestInspectorDepsScroll:

    def test_scroll_change_changes_deps(self):
        """scroll 变化 → deps 变化（滚动触发重建）；cursor 默认 -1（末位）。"""
        from src.tui.app.trace_view import _inspector_deps
        rec = _plain_rec(["hi"])
        d0 = _inspector_deps(rec, 40, 24, 0)
        d1 = _inspector_deps(rec, 40, 24, 3)
        assert d0 != d1
        assert d0[-2] == 0
        assert d1[-2] == 3
        assert d0[-1] == -1  # cursor 默认 -1（不高亮）

    def test_scroll_default_zero_backward_compatible(self):
        """省略 scroll 参数 → 默认 0（既有调用面兼容）。"""
        from src.tui.app.trace_view import _inspector_deps
        rec = _plain_rec(["hi"])
        deps = _inspector_deps(rec, 40, 24)
        assert deps[-2] == 0
        assert deps[-1] == -1

    def test_cursor_change_changes_deps(self):
        """cursor 变化 → deps 变化（高亮行移动触发重建）。"""
        from src.tui.app.trace_view import _inspector_deps
        rec = _plain_rec(["hi"])
        d0 = _inspector_deps(rec, 40, 24, 0, -1)
        d1 = _inspector_deps(rec, 40, 24, 0, 3)
        assert d0 != d1
        assert d0[-1] == -1
        assert d1[-1] == 3

    def test_invalid_scroll_safe(self):
        """异常 scroll/cursor 值（str/None）→ 归一化不中断。"""
        from src.tui.app.trace_view import _inspector_deps
        rec = _plain_rec(["hi"])
        deps_bad = _inspector_deps(rec, 40, 24, "bad")
        assert deps_bad[-2] == 0  # scroll 归一化 0
        assert deps_bad[-1] == -1  # cursor 默认 -1
        deps_none = _inspector_deps(rec, 40, 24, None, None)
        assert deps_none[-2] == 0
        assert deps_none[-1] == -1


# ═══════════════════════════════════════════════════════════
# 5. model 字段（trace_pane / trace_inspector_scroll / trace_tools_*）
# ═══════════════════════════════════════════════════════════

class TestModelTracePaneFields:

    def test_defaults(self):
        """新字段默认值：台账焦点、滚动/光标 0。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        assert model.trace_pane == "ledger"
        assert model.trace_inspector_scroll == 0
        assert model.trace_inspector_cursor == 0
        assert model.trace_tools_pane == "ledger"
        assert model.trace_tools_scroll == 0
        assert model.trace_tools_cursor == 0

    def test_reset_display_resets(self):
        """reset_display 复位全部面板/滚动/光标字段。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.trace_pane = "inspector"
        model.trace_inspector_scroll = 5
        model.trace_inspector_cursor = 7
        model.trace_tools_pane = "inspector"
        model.trace_tools_scroll = 3
        model.trace_tools_cursor = 9
        model.reset_display()
        assert model.trace_pane == "ledger"
        assert model.trace_inspector_scroll == 0
        assert model.trace_inspector_cursor == 0
        assert model.trace_tools_pane == "ledger"
        assert model.trace_tools_scroll == 0
        assert model.trace_tools_cursor == 0


# ═══════════════════════════════════════════════════════════
# 6. TraceView 端到端：l/h 面板切换 + 检查器滚动 + 台账 vim 导航
# ═══════════════════════════════════════════════════════════

class TestTraceViewVimPane:

    @staticmethod
    def _make_model():
        from src.tui.app.model import AppModel
        model = AppModel()
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(5):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        model.message_source = lambda: msgs
        return model

    @pytest.fixture(autouse=True)
    def _pin_tools_record(self, monkeypatch):
        """固定 #0 工具列表记录（解耦全局 ToolRegistry 自动发现）。"""
        from src.tui.app import trace as trace_mod
        from src.tui.app.trace import TraceRecord
        monkeypatch.setattr(
            trace_mod, "_tools_record",
            lambda: TraceRecord(index=0, kind="tools", summary="工具列表"),
        )

    def test_l_moves_pane_to_inspector(self):
        """台账焦点按 l → trace_pane=inspector（消费）。"""
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "l")) is True
        assert model.trace_pane == "inspector"

    def test_h_in_inspector_returns_ledger(self):
        """检查器焦点按 h → trace_pane=ledger（消费）。"""
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "h")) is True
        assert model.trace_pane == "ledger"

    def test_ledger_h_not_consumed(self):
        """台账焦点按 h（已在最左）→ 本组件不消费，被模态吞掉（不落输入缓冲）。

        use_fullscreen 模态激活时未消费事件由 router 吞掉（返回 True）——
        语义与 ListView 首行按 ↑ 放行一致（放行旧路径=模态吞掉），pane 不变。
        """
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "h")) is True
        assert model.trace_pane == "ledger"

    def test_ledger_j_moves_selection(self):
        """台账焦点 j → ListView 消费导航（trace_selected 写回）。"""
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        model.trace_selected = 0
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True
        assert model.trace_selected == 1

    def test_ledger_k_moves_selection(self):
        """台账焦点 k → ListView 消费导航（上移）。"""
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        model.trace_selected = 5
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "k")) is True
        assert model.trace_selected == 4

    def test_inspector_jk_moves_cursor(self, monkeypatch):
        """检查器焦点 j/k → trace_inspector_cursor ±1（光标移动，当前行高亮）。

        vim 视口语义：光标在窗口内移动时视口（scroll）不滚动——仅光标行
        高亮变化；光标到窗口边界后视口才跟随滚动。
        """
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        from src.tui.app.model import AppModel
        rec0 = _plain_rec([f"line-{i}" for i in range(30)])
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True
        assert model.trace_inspector_cursor == 1
        assert model.trace_inspector_scroll == 0  # 光标在窗口内，视口不动
        assert router(_ev("char", "j")) is True
        assert model.trace_inspector_cursor == 2
        assert router(_ev("char", "k")) is True
        assert model.trace_inspector_cursor == 1

    def test_inspector_g_G_moves_cursor(self, monkeypatch):
        """检查器焦点 g → 光标顶部、G → 光标底部（视口跟随）。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import (
            _INSPECTOR_MIN_CONTENT, TraceView, _viewport_rows,
        )
        from src.tui.app.model import AppModel
        rec0 = _plain_rec([f"line-{i}" for i in range(30)])
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        model.trace_inspector_cursor = 5
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        vh = _viewport_rows()
        approx = max(_INSPECTOR_MIN_CONTENT, vh - 3)
        max_scroll = max(0, 30 - approx)
        assert router(_ev("char", "G")) is True
        assert model.trace_inspector_cursor == 29  # 末行
        assert model.trace_inspector_scroll == max_scroll  # 视口跟随到底
        assert router(_ev("char", "g")) is True
        assert model.trace_inspector_cursor == 0
        assert model.trace_inspector_scroll == 0

    def test_inspector_arrow_keys_move_cursor(self, monkeypatch):
        """检查器焦点方向键同样移动光标（↓/↑/PgDn/PgUp/Home/End）。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import (
            _INSPECTOR_MIN_CONTENT, TraceView, _viewport_rows,
        )
        from src.tui.app.model import AppModel
        rec0 = _plain_rec([f"line-{i}" for i in range(30)])
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        vh = _viewport_rows()
        approx = max(_INSPECTOR_MIN_CONTENT, vh - 3)
        assert router(_ev("arrow_down")) is True
        assert model.trace_inspector_cursor == 1
        assert router(_ev("arrow_up")) is True
        assert model.trace_inspector_cursor == 0
        assert router(_ev("page_down")) is True
        assert model.trace_inspector_cursor == approx  # 翻页（光标 + 视口）
        assert router(_ev("page_up")) is True
        assert model.trace_inspector_cursor == 0
        assert router(_ev("end")) is True
        assert model.trace_inspector_cursor == 29  # 末行
        assert router(_ev("home")) is True
        assert model.trace_inspector_cursor == 0

    def test_inspector_cursor_scroll_follows_at_boundary(self, monkeypatch):
        """光标移动到窗口边界 → 视口滚动跟随（光标保持可见）。

        内容 30 行、近似视口 approx（>0）：连续 j 到边界（scroll+approx）
        后视口开始滚动；光标始终可见。
        """
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import (
            _INSPECTOR_MIN_CONTENT, TraceView, _viewport_rows,
        )
        from src.tui.app.model import AppModel
        rec0 = _plain_rec([f"line-{i}" for i in range(30)])
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        vh = _viewport_rows()
        approx = max(_INSPECTOR_MIN_CONTENT, vh - 3)
        # 连续按 j 至窗口下边界（光标 = scroll+approx-1，视口仍不滚动）
        for _ in range(approx - 1):
            router(_ev("char", "j"))
        assert model.trace_inspector_cursor == approx - 1
        assert model.trace_inspector_scroll == 0
        # 再按 j：光标越过边界 → 视口滚动 1 行（光标保持窗口内可见）
        router(_ev("char", "j"))
        assert model.trace_inspector_cursor == approx
        assert model.trace_inspector_scroll == 1

    def test_inspector_arrow_left_returns_ledger(self, monkeypatch):
        """检查器焦点 ← → 返回台账。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        from src.tui.app.model import AppModel
        rec0 = _plain_rec(["hi"])
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("arrow_left")) is True
        assert model.trace_pane == "ledger"

    def test_enter_subagent_in_inspector_resets_pane(self, monkeypatch):
        """检查器焦点 Enter 下钻 subagent → 同时复位 pane/scroll/cursor。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        from src.tui.app.model import AppModel
        rec0 = _plain_rec(["hello"], kind="subagent", subagent_label="sa-1")
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        model.trace_inspector_scroll = 3
        model.trace_inspector_cursor = 5
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("enter")) is True
        assert model.trace_subagent_label == "sa-1"
        assert model.trace_pane == "ledger"
        assert model.trace_inspector_scroll == 0
        assert model.trace_inspector_cursor == 0

    def test_enter_tools_view_resets_pane(self, monkeypatch):
        """台账焦点 Enter 进入工具列表视图 → 复位 trace pane/scroll/cursor
        + 工具视图状态。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        from src.tui.app.model import AppModel
        rec0 = TraceRecord(index=0, kind="tools", summary="工具列表")
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec0], [rec0]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        model.trace_inspector_scroll = 4
        model.trace_inspector_cursor = 6
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("enter")) is True
        assert model.fullscreen == "trace_tools"
        assert model.trace_tools_selected == 0
        assert model.trace_tools_pane == "ledger"
        assert model.trace_tools_scroll == 0
        assert model.trace_tools_cursor == 0
        assert model.trace_pane == "ledger"
        assert model.trace_inspector_scroll == 0
        assert model.trace_inspector_cursor == 0

    def test_escape_from_subagent_resets_pane(self, monkeypatch):
        """subagent 轨迹内 Esc 返回主轨迹 → 复位 pane/scroll/cursor。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        from src.tui.app.model import AppModel
        rec0 = _plain_rec(["hi"])
        monkeypatch.setattr(
            tv, "build_subagent_trace_records",
            lambda label, model: ([rec0], [rec0]),
        )
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_subagent_label = "sa-1"
        model.trace_pane = "inspector"
        model.trace_inspector_scroll = 2
        model.trace_inspector_cursor = 4
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("escape")) is True
        assert model.trace_subagent_label is None
        assert model.trace_pane == "ledger"
        assert model.trace_inspector_scroll == 0
        assert model.trace_inspector_cursor == 0

    def test_navigate_resets_scroll_and_cursor(self, monkeypatch):
        """台账导航切换记录 → 检查器滚动/光标复位 0（新记录从头查看）。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.trace_view import TraceView
        from src.tui.app.model import AppModel
        rec0 = _plain_rec(["a"], index=0)
        rec1 = _plain_rec(["b"], index=1)
        rec2 = _plain_rec(["c"], index=2)
        monkeypatch.setattr(
            tv, "build_trace_records",
            lambda model: ([rec0, rec1, rec2], [rec0, rec1, rec2]),
        )
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "ledger"
        model.trace_selected = 0
        model.trace_inspector_scroll = 6
        model.trace_inspector_cursor = 8
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True  # 导航到中间（非末条）
        assert model.trace_selected == 1
        assert model.trace_inspector_scroll == 0
        assert model.trace_inspector_cursor == 0


# ═══════════════════════════════════════════════════════════
# 7. trace_tools_view 端到端：l/h 面板切换 + 右栏滚动
# ═══════════════════════════════════════════════════════════

class TestTraceToolsViewVimPane:

    @staticmethod
    def _schemas():
        return [
            ("bash",
             {"command": {"type": "string", "description": "命令参数说明"}},
             ["command"], "执行命令 " * 300),
            ("ls", {"path": {"type": "string"}}, [], "列目录"),
        ]

    def _setup(self, monkeypatch):
        from src.tui.app import trace as trace_mod
        from src.tui.app.trace_tools_view import TraceToolsView
        from src.tui.app.model import AppModel
        monkeypatch.setattr(
            trace_mod, "_tools_schema_cache",
            (_time.monotonic(), self._schemas()),
        )
        model = AppModel()
        model.fullscreen = "trace_tools"
        rec, root = _render_root(TraceToolsView, {"model": model, "width": 80})
        return model, rec, root

    def test_l_moves_to_inspector(self, monkeypatch):
        """左栏焦点 l → trace_tools_pane=inspector。"""
        from src.tui.app.trace_tools_view import TraceToolsView
        model, rec, root = self._setup(monkeypatch)
        router = rec._build_input_router(root)
        assert router(_ev("char", "l")) is True
        assert model.trace_tools_pane == "inspector"

    def test_inspector_jk_moves_cursor(self, monkeypatch):
        """右栏焦点 j/k → trace_tools_cursor ±1（光标移动，当前行高亮）。"""
        from src.tui.app.trace_tools_view import TraceToolsView
        model, rec, root = self._setup(monkeypatch)
        model.trace_tools_pane = "inspector"
        rec.render(root, h(TraceToolsView, {"model": model, "width": 80}), 80, 24)
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True
        assert model.trace_tools_cursor == 1
        assert router(_ev("char", "k")) is True
        assert model.trace_tools_cursor == 0

    def test_inspector_h_returns_ledger(self, monkeypatch):
        """右栏焦点 h → trace_tools_pane=ledger。"""
        from src.tui.app.trace_tools_view import TraceToolsView
        model, rec, root = self._setup(monkeypatch)
        model.trace_tools_pane = "inspector"
        rec.render(root, h(TraceToolsView, {"model": model, "width": 80}), 80, 24)
        router = rec._build_input_router(root)
        assert router(_ev("char", "h")) is True
        assert model.trace_tools_pane == "ledger"

    def test_ledger_jk_navigates_tools(self, monkeypatch):
        """左栏焦点 j/k → ListView 导航工具列表（trace_tools_selected 写回）。"""
        from src.tui.app.trace_tools_view import TraceToolsView
        model, rec, root = self._setup(monkeypatch)
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True
        assert model.trace_tools_selected == 1
        assert router(_ev("char", "k")) is True
        assert model.trace_tools_selected == 0

    def test_navigate_resets_scroll_and_cursor(self, monkeypatch):
        """左栏导航切换工具 → 右栏滚动/光标复位 0。"""
        from src.tui.app.trace_tools_view import TraceToolsView
        model, rec, root = self._setup(monkeypatch)
        model.trace_tools_scroll = 4
        model.trace_tools_cursor = 6
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True
        assert model.trace_tools_selected == 1
        assert model.trace_tools_scroll == 0
        assert model.trace_tools_cursor == 0

    def test_escape_returns_trace(self, monkeypatch):
        """Esc → 返回主轨迹（fullscreen="trace"，既有语义）。"""
        from src.tui.app.trace_tools_view import TraceToolsView
        model, rec, root = self._setup(monkeypatch)
        router = rec._build_input_router(root)
        assert router(_ev("escape")) is True
        assert model.fullscreen == "trace"


# ═══════════════════════════════════════════════════════════
# 8. 工具参数检查器滚动窗口（trace_tools _inspector_children scroll）
# ═══════════════════════════════════════════════════════════

class TestToolsInspectorScroll:

    def _texts(self, children):
        return [str(c.props.get("children", "")) for c in children]

    def _long_desc(self):
        return "描述 " * 200  # wrap 后多行（超视口）

    def test_scroll_zero_shows_head(self):
        """scroll=0：显示头部内容 + 「… 后 N 行省略」。"""
        from src.tui.app.trace_tools_view import _inspector_children
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"],
            self._long_desc(), 30, 8,
        )
        texts = self._texts(children)
        assert any(t.startswith("\u2026 后 ") for t in texts)
        assert not any(t.startswith("\u2026 前 ") for t in texts)

    def test_scroll_middle_shows_top_and_bottom(self):
        """scroll 中间：置顶省略 + 窗口 + 后置省略。"""
        from src.tui.app.trace_tools_view import _inspector_children
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"],
            self._long_desc(), 30, 8, scroll=4,
        )
        texts = self._texts(children)
        assert any(t.startswith("\u2026 前 4 行省略") for t in texts)
        assert any(t.startswith("\u2026 后 ") for t in texts)

    def test_scroll_bottom_reaches_end(self):
        """scroll 到底：仅置顶省略（无后置）。"""
        from src.tui.app.trace_tools_view import _inspector_children
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"],
            self._long_desc(), 30, 8, scroll=200,
        )
        texts = self._texts(children)
        assert any(t.startswith("\u2026 前 ") for t in texts)
        assert not any(t.startswith("\u2026 后 ") for t in texts)

    def test_scroll_default_zero_backward_compatible(self):
        """省略 scroll 参数（既有调用面）→ 默认 0。"""
        from src.tui.app.trace_tools_view import _inspector_children
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"],
            "短描述", 30, 30,
        )
        texts = self._texts(children)
        assert "短描述" in texts
        assert "\u25b8 \u53c2\u6570" in texts  # 小节标题可见

    def test_tools_content_rows_cap(self):
        """_tools_inspector_content_rows 超上限 → 截断 + 提示行。"""
        from src.tui.app.trace_tools_view import (
            _INSPECTOR_MAX_ROWS, _tools_inspector_content_rows,
        )
        # 超长描述（40 宽下每行 40 字符 → 远超上限行数）
        rows, keys = _tools_inspector_content_rows(
            "bash", {"command": {"type": "string"}}, ["command"],
            "x" * (_INSPECTOR_MAX_ROWS * 80), 40,
        )
        assert len(rows) == _INSPECTOR_MAX_ROWS + 1
        last = rows[-1]
        assert isinstance(last, list)
        assert "内容过长" in "".join(r.text for r in last)
        assert len(keys) == len(rows)

    # ── 光标行高亮（2026-08-19：右边高亮当前行背景色） ──

    def test_cursor_row_highlighted(self):
        """cursor 所在行背景高亮；其余行不高亮（trace_tools 右栏）。"""
        from src.tui.app.trace_tools_view import _inspector_children
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"],
            "描述 " * 50, 30, 8, scroll=0, cursor=1,
        )
        highlighted = [
            c for c in children
            if c.props.get("style") is not None
            and c.props.get("style").bg is not None
        ]
        assert len(highlighted) == 1, "应恰好 1 行光标高亮"
        assert str(highlighted[0].props.get("children", "")).startswith("描述 ")

    def test_cursor_default_no_highlight(self):
        """cursor 缺省（-1）→ 无背景高亮（向后兼容）。"""
        from src.tui.app.trace_tools_view import _inspector_children
        children = _inspector_children(
            "bash", {"command": {"type": "string"}}, ["command"],
            "短描述", 30, 30,
        )
        for c in children:
            style = c.props.get("style")
            if style is not None:
                assert style.bg is None
