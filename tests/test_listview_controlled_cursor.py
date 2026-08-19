"""ListView 受控光标 ref 基准修复回归测试（2026-08-19）。

根因：受控模式渲染期无条件 ``cursor_ref.current = cursor``（内部 state）
覆盖导航基准 ref——尾部跟随场景（TraceView ``trace_selected=-1`` →
cursor prop=末行）内部 state 恒为初始 0（无人导航、set_cursor 从未提交），
每帧渲染把 ref 拉回首行；受控同步块因受控值未变跳过重置 → handler 基准
=首行 → 按上键无处可移（事件被 use_fullscreen 模态吞掉，用户看到「轨迹
Trace 按上键不移动」）。

修复：受控模式渲染期 ref 基准统一为外部受控值（``cursor_prop``）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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


# ═══════════════════════════════════════════════════════════
# 1. 回归：受控末行 + 二次渲染后按上键仍可移动
# ═══════════════════════════════════════════════════════════

class TestControlledTailFollowArrowUp:

    def test_arrow_up_after_second_render_moves(self):
        """★ 修复断言：受控光标=末行，第二帧渲染后按上键仍从末行上移。

        修复前：第二帧渲染把 cursor_ref 覆盖为内部 state（0=首行），
        受控值未变跳过重置 → 上键基准=首行 → 无处可移返回 False。
        """
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2", "i3", "i4"], "height": 3,
            "cursor": 4, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        # 第二帧渲染（模拟生产 10Hz 渲染循环：按键前已渲染多帧）
        rec.render(root, h(ListView, dict(props)), 80, 24)
        handler = _find_input_handler(root.child)
        # ★ 修复断言：从末行（4）上移到 3，而非从首行无处可移
        assert handler(_ev("arrow_up")) is True
        assert nav == [3]

    def test_arrow_up_first_frame_unchanged(self):
        """首帧后按上键正常（既有语义零回归）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "cursor": 2, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_up")) is True
        assert nav == [1]

    def test_first_row_arrow_up_releases(self):
        """受控光标=首行时上键无移动 → 放行（返回 False）。"""
        nav: list = []
        props = {
            "items": ["i0", "i1"], "height": 2,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        rec.render(root, h(ListView, dict(props)), 80, 24)
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_up")) is False
        assert nav == []


# ═══════════════════════════════════════════════════════════
# 2. 同批连续导航语义保持（P3 review 2026-08-18 既有契约）
# ═══════════════════════════════════════════════════════════

class TestControlledBatchNavigationKept:

    def test_two_ups_same_batch_from_tail(self):
        """同批两次上键（无中间渲染）：基准沿 _move 推进值（末→末-2）。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 3, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_up")) is True
        assert handler(_ev("arrow_up")) is True
        assert nav == [2, 1]

    def test_two_downs_same_batch_advance_two(self):
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_down")) is True
        assert nav == [1, 2]

    def test_external_cursor_change_resynced_on_render(self):
        """外部受控值直接跳变（新渲染到达）→ 渲染期同步基准。"""
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2", "i3"], "height": 4,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        handler(_ev("arrow_down"))  # 内部 ref=1
        props2 = dict(props, cursor=3)
        rec.render(root, h(ListView, props2), 80, 24)
        handler2 = _find_input_handler(root.child)
        assert handler2(_ev("arrow_down")) is False  # 末项无移动放行
        assert handler2(_ev("arrow_up")) is True
        assert nav[-1] == 2

    def test_navigate_then_render_keeps_written_value(self):
        """导航写回受控值后渲染：基准与受控值一致（不回退内部 state）。"""
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2", "i3", "i4", "i5"], "height": 4,
            "cursor": 5, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        handler(_ev("arrow_up"))  # → 4
        # 模拟外部（TraceView _on_navigate）写回后下一帧：cursor prop=4
        props2 = dict(props, cursor=4)
        rec.render(root, h(ListView, props2), 80, 24)
        handler2 = _find_input_handler(root.child)
        assert handler2(_ev("arrow_up")) is True
        assert nav == [4, 3]


# ═══════════════════════════════════════════════════════════
# 3. 非受控模式零回归
# ═══════════════════════════════════════════════════════════

class TestUncontrolledUnchanged:

    def test_uncontrolled_navigation_unchanged(self):
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2"], "height": 3,
            "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_down")) is True
        assert handler(_ev("arrow_down")) is False  # 末项放行
        assert nav == [1, 2]

    def test_uncontrolled_rerender_keeps_state(self):
        """非受控二次渲染：内部 state（导航推进值）保持为基准。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 3,
            "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        handler(_ev("arrow_down"))
        handler(_ev("arrow_down"))  # state → 2
        rec.render(root, h(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 3,
            "onNavigate": nav.append, "focus": True,
        }), 80, 24)
        handler2 = _find_input_handler(root.child)
        assert handler2(_ev("arrow_down")) is True  # 2 → 3（非末项，仍可移）
        assert handler2(_ev("arrow_down")) is False  # 末项放行
        assert handler2(_ev("arrow_up")) is True
        assert nav == [1, 2, 3, 2]


# ═══════════════════════════════════════════════════════════
# 4. None 分隔行（TraceView 台账语义）保持
# ═══════════════════════════════════════════════════════════

class TestSeparatorRowsKept:

    def test_separator_rows_skipped_on_navigation(self):
        """None 分隔行不可选：受控末行上移跳过分隔行。"""
        nav: list = []
        items = ["i0", None, "i1", None, "i2"]
        props = {
            "items": items, "height": 3,
            "cursor": 4, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        rec.render(root, h(ListView, dict(props)), 80, 24)
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_up")) is True
        assert nav == [2]  # 跳过 row 3（None 分隔行）

    def test_up_from_first_selectable_releases(self):
        nav: list = []
        props = {
            "items": [None, "i0", "i1"], "height": 3,
            "cursor": 1, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        rec.render(root, h(ListView, dict(props)), 80, 24)
        handler = _find_input_handler(root.child)
        assert handler(_ev("arrow_up")) is False


# ═══════════════════════════════════════════════════════════
# 5. 受控契约：外部不写回 → 重渲染基准回退受控值
# ═══════════════════════════════════════════════════════════

class TestControlledFallbackToProp:

    def test_external_no_writeback_render_falls_back_to_prop(self):
        """★ 语义契约（2026-08-19 修复引入）：外部**不消费** onNavigate 写回
        （受控值保持旧值）时，下一帧渲染基准回退到受控 prop——批内导航
        推进值仅存活到下一次渲染（React 受控组件语义）。"""
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2", "i3", "i4"], "height": 3,
            "cursor": 4, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        handler(_ev("arrow_up"))  # 批内推进 → 3
        # 外部不写回（cursor prop 保持 4），新渲染到达 → 基准回退受控值 4
        rec.render(root, h(ListView, dict(props)), 80, 24)
        handler2 = _find_input_handler(root.child)
        assert handler2(_ev("arrow_down")) is False  # 受控值=末项，下键放行
        assert handler2(_ev("arrow_up")) is True
        assert nav == [3, 3]  # 批内 3 → 渲染回退 4 → 再上移又到 3


# ═══════════════════════════════════════════════════════════
# 6. 翻页 / 首末键：边缘钳制 + 批内连续导航
# ═══════════════════════════════════════════════════════════

class TestPageAndJumpNavigation:

    def test_page_down_clamps_to_last_selectable(self):
        """★ P3（review，翻页边缘钳制）：距末项 2 行按 PgDn → 钳制到末项
        （修复前越界整体失败返回 False，用户看到近边缘翻页完全不动）。"""
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2", "i3", "i4", "i5"], "height": 3,
            "cursor": 3, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        assert handler(_ev("page_down")) is True  # 3+3=6 越界 → 钳制末项 5
        assert nav == [5]

    def test_page_up_clamps_to_first_selectable(self):
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2", "i3", "i4"], "height": 3,
            "cursor": 1, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        assert handler(_ev("page_up")) is True  # 1-3=-2 越界 → 钳制首项 0
        assert nav == [0]

    def test_page_down_at_last_releases(self):
        """已在末项按 PgDn → 无移动放行（返回 False）。"""
        nav: list = []
        props = {
            "items": ["i0", "i1", "i2"], "height": 3,
            "cursor": 2, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        assert handler(_ev("page_down")) is False
        assert nav == []

    def test_page_down_skips_separator_rows(self):
        """翻页越过不可选区落在可选项上（None 分隔行跳过）。"""
        nav: list = []
        items = ["i0", None, "i1", "i2", None, "i3"]
        props = {
            "items": items, "height": 2,
            "cursor": 0, "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        assert handler(_ev("page_down")) is True  # 0+2=2 → i1（可选中点即可）
        assert nav == [2]

    def test_home_end_batch_navigation(self):
        """home/end 批内连续导航：第二次以 _move 推进值为基准。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3", "i4"], "height": 3,
            "cursor": 2, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("home")) is True
        assert handler(_ev("end")) is True
        assert nav == [0, 4]

    def test_g_G_batch_navigation(self):
        """vim 风格 g/G（TraceView 台账语义）：批内连续首末跳转。"""
        nav: list = []
        rec, root = _render_root(ListView, {
            "items": ["i0", "i1", "i2", "i3"], "height": 3,
            "cursor": 1, "onNavigate": nav.append, "focus": True,
        })
        handler = _find_input_handler(root.child)
        assert handler(_ev("char", "G")) is True
        assert handler(_ev("char", "g")) is True
        assert nav == [3, 0]

    def test_page_navigation_scroll_offset_kept(self):
        """非受控翻页导航后视口滚动（光标保持可见）：下帧渲染 rows 覆盖光标行。"""
        from src.tui.ink.element import TEXT
        nav: list = []
        items = [f"i{i}" for i in range(10)]
        props = {
            "items": items, "height": 3,
            "onNavigate": nav.append, "focus": True,
        }
        rec, root = _render_root(ListView, dict(props))
        handler = _find_input_handler(root.child)
        assert handler(_ev("page_down")) is True  # → 3
        assert handler(_ev("page_down")) is True  # → 6
        assert nav == [3, 6]
        # 渲染后光标行（i6）应在可见窗口内（offset 滚动至 [4,7)）
        rec.render(root, h(ListView, dict(props)), 80, 24)
        texts: list = []

        def _collect(fiber):
            if fiber is None:
                return
            if not fiber.is_function and fiber.type == TEXT:
                ch = fiber.props.get("children")
                if isinstance(ch, str):
                    texts.append(ch)
            _collect(fiber.child)
            _collect(fiber.sibling)

        _collect(root.child)
        assert any(t == "i6" for t in texts)


# ═══════════════════════════════════════════════════════════
# 5. TraceView 端到端：尾部跟随 + 二次渲染 + 上键写回
# ═══════════════════════════════════════════════════════════

class TestTraceViewTailFollowNavigation:

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
        """固定 #0 工具列表记录（P3 review）：解耦全局 ToolRegistry 自动
        发现——隔离/并行测试环境注册表为空时记录数漂移导致断言误报。"""
        from src.tui.app import trace as trace_mod
        from src.tui.app.trace import TraceRecord
        monkeypatch.setattr(
            trace_mod, "_tools_record",
            lambda: TraceRecord(index=0, kind="tools", summary="工具列表"),
        )

    def test_arrow_up_after_rerender_moves_selection(self):
        """★ 端到端回归：打开轨迹（尾部跟随）+ 二次渲染后按上键 →
        trace_selected 从 -1 写回倒数第二条（修复前恒 -1 不动）。"""
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        # 二次渲染（生产 10Hz 循环：按键前已多帧）
        rec.render(root, h(TraceView, {"model": model, "width": 100}), 100, 24)
        # 经 input router 分发（TraceView 放行 → ListView 消费）
        router = rec._build_input_router(root)
        assert router is not None
        assert router(_ev("arrow_up")) is True
        assert model.trace_selected == 10  # 12 条记录（tools+sys+5×2）→ 倒数第二

    def test_arrow_up_repeated_after_rerender(self):
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        rec.render(root, h(TraceView, {"model": model, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        assert router(_ev("arrow_up")) is True
        # 每次按键后一帧渲染（模拟渲染循环），连续三次上键持续上移
        for expect in (9, 8):
            rec.render(root, h(TraceView, {"model": model, "width": 100}), 100, 24)
            router = rec._build_input_router(root)
            assert router(_ev("arrow_up")) is True
            assert model.trace_selected == expect

    def test_arrow_down_still_works_after_rerender(self):
        from src.tui.app.trace_view import TraceView
        model = self._make_model()
        model.fullscreen = "trace"
        model.trace_selected = 0  # 手动定位首条
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        rec.render(root, h(TraceView, {"model": model, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        assert router(_ev("arrow_down")) is True
        assert model.trace_selected == 1
