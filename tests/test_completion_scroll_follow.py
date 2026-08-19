"""补全弹窗跟随光标滚动测试（2026-08-19，用户需求：补全弹窗候选很多时
按 ↑↓ 能移动到没有显示的行）。

bug：补全弹窗候选多于可见行数时按 ↑/↓，可视窗口 ``_visible_window`` 用
无状态贴顶语义（``offset = min(selected, total - limit)``）——每按一次键
窗口立即滚动一行、高亮钉在窗口首行（用户看到「高亮不动、列表乱滚」，
无法感知按上下能移动到未显示的行）。

修复：跟随光标滚动（与 ListView 轨迹视图语义一致）——光标在窗口内移动
时窗口不动（高亮逐行移动），仅越过窗口边界时滚动（贴底/贴顶）：

  - ``SelectInput`` / ``MultiSelect`` / ``RadioList``：滚动窗口 offset
    state + ref 镜像；事件期导航后 ``_scroll_follow`` 推进；渲染窗口传
    当前 offset（``_visible_window`` 增加 ``current_offset`` 参数）；
  - 受控 ``index``（CompletionPopup 的 completion.selected——PgUp/PgDn
    翻页 / 边界回绕写回）跳变时同样滚动窗口使受控选中项保持可见；
  - ``_completion_scroll_offset``（split_desc 分栏回退路径）：``current``
    参数 + ``completion._popup_scroll`` 跨帧持久化，同一跟随语义。
"""

from __future__ import annotations

import pytest

from src.tui._input_parser import KeyEvent
from src.tui.ink.element import h, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components
from src.tui.ink import hooks as _hooks
from src.tui.ink.widgets._interactive_common import _visible_window
from src.tui.ink.widgets.interactive import SelectInput, MultiSelect
from src.tui.ink.widgets.radio import RadioList
from src.tui.app._popup_builder import (
    _completion_scroll_offset,
    _build_popup_lines,
)
from src.tui.app.model import AppModel
from src.tui.app.app import App


@pytest.fixture(autouse=True)
def _reset_router_callback():
    """测试后复位 input router 发布回调（防跨测试泄漏）。"""
    yield
    _hooks.set_input_router_callback(None)


@pytest.fixture(autouse=True)
def _fix_popup_rows(monkeypatch):
    """固定补全弹窗选项行数预算（消除真实终端高度依赖）。"""
    monkeypatch.setattr("src.tui._input_metrics._completion_item_rows", lambda: 8)


# ── 1. _visible_window 跟随光标滚动语义（纯函数） ──────────


class TestVisibleWindowFollow:
    """窗口跟随光标：窗口内不动 / 越下边界贴底 / 越上边界贴顶。"""

    def test_no_limit_returns_all(self):
        """limit=None → 全量显示（无滚动，offset 恒 0）。"""
        assert _visible_window(5, 20, None, 3) == (0, 20)

    def test_total_within_limit_returns_all(self):
        """total <= limit → 全量显示。"""
        assert _visible_window(3, 8, 8, 2) == (0, 8)
        assert _visible_window(0, 3, 8, 5) == (0, 3)

    def test_cursor_inside_window_keeps_offset(self):
        """★ 修复核心：光标在窗口内移动 → 窗口不动（高亮逐行移动）。"""
        # 窗口 [2..9]（current=2），光标 5 在窗口内 → 保持 offset=2
        assert _visible_window(5, 20, 8, 2) == (2, 8)
        # 光标从 5 移到 9（窗口末行）仍不滚动
        assert _visible_window(9, 20, 8, 2) == (2, 8)

    def test_cursor_cross_bottom_sticks_bottom(self):
        """光标越过窗口下边界 → 窗口贴底（高亮在末行）。"""
        # 窗口 [2..9]，光标 10 越过末行 → offset = 10-8+1 = 3
        assert _visible_window(10, 20, 8, 2) == (3, 8)

    def test_cursor_cross_top_sticks_top(self):
        """光标越过窗口上边界 → 窗口贴顶（高亮在首行）。"""
        # 窗口 [2..9]，光标 1 越过首行 → offset = 1
        assert _visible_window(1, 20, 8, 2) == (1, 8)

    def test_current_offset_clamped(self):
        """current 越界（items 动态缩小后残留旧 offset）→ 钳制到末屏。"""
        # current=15 但 total=12, limit=8 → max_offset=4 → 钳制 4；光标 3
        # 在 [4..11] 之外（上方）→ 贴顶 3
        assert _visible_window(3, 12, 8, 15) == (3, 8)
        # 光标 5 在钳制后窗口 [4..11] 内 → 保持钳制值 4
        assert _visible_window(5, 12, 8, 15) == (4, 8)

    def test_last_item_max_offset(self):
        """光标到末项 → offset = total - limit（末屏不越界）。"""
        assert _visible_window(19, 20, 8, 12) == (12, 8)

    def test_first_item_returns_top(self):
        """光标回首项 → 窗口回顶（offset=0）。"""
        assert _visible_window(0, 20, 8, 5) == (0, 8)

    def test_negative_or_zero_rows(self):
        """total/n_rows 非法（0/负）→ offset 0。"""
        assert _visible_window(0, 0, 8, 3) == (0, 0)
        assert _visible_window(1, 5, 0, 3) == (0, 5)
        assert _visible_window(1, 5, -2, 3) == (0, 5)


# ── 2. 控件端到端（reconciler + input router 发键） ────────


class _CtlHarness:
    """控件级端到端夹具：reconciler 渲染 + router 发按键事件。

    state dict 由 Root 闭包每帧读取——模拟真实组件树（props 变化 →
    重渲染），fiber 复用（hooks/offset state 跨帧保留）。
    """

    def __init__(self, component: str, items: list, limit: int,
                 controlled: bool = False):
        self.state = {
            "items": items, "limit": limit, "index": 0,
            "highlights": [], "selects": [],
        }
        self.component = component
        self.rec = Reconciler(schedule_callback=None)
        self.root = self.rec.create_root()
        self.router = None

        def _on_router(router):
            self.router = router

        _hooks.set_input_router_callback(_on_router)
        comp = {
            "select": SelectInput, "multi": MultiSelect, "radio": RadioList,
        }[component]

        def _ri_select(item, idx, is_sel):
            return h(TEXT, {
                "children": ("*opt-%02d" % idx) if is_sel else (" opt-%02d" % idx),
                "key": f"it-{idx}",
            })

        def _ri_multi(item, idx, is_cursor, is_checked):
            return h(TEXT, {
                "children": ("*opt-%02d" % idx) if is_cursor else (" opt-%02d" % idx),
                "key": f"it-{idx}",
            })

        def Root(props):
            st = self.state
            p = {
                "items": st["items"],
                "limit": st["limit"],
                "initialIndex": 0,
                "focus": True,
            }
            if component == "select":
                p["renderItem"] = _ri_select
                p["onHighlight"] = lambda i: st["highlights"].append(i)
                if controlled:
                    p["index"] = st["index"]
            elif component == "multi":
                p["renderItem"] = _ri_multi
                p["onHighlight"] = lambda i: st["highlights"].append(i)
                p["onSubmit"] = lambda vals: st["selects"].append(vals)
            return h(comp, p)

        self._root_fn = Root

    def render(self) -> list:
        """渲染一帧，返回可视行文本列表（'*' 前缀 = 高亮行）。"""
        self.rec.render(self.root, h(self._root_fn, {}), 80, 40)
        frame = _components.render_frame(self.root, 80)
        return ["".join(r.text for r in ln.runs).rstrip() for ln in frame.lines]

    def key(self, kind: str, char: str = "") -> bool:
        """经 input router 发一个按键事件（True=被控件消费）。

        router 在首次渲染后发布——尚未渲染时先渲染一帧（自愈）。
        """
        if self.router is None:
            self.render()
        assert self.router is not None, "渲染一帧后 router 才可用"
        return bool(self.router(KeyEvent(kind=kind, char=char)))

    def window(self) -> tuple:
        """当前可视窗口 (首项 idx, 末项 idx) 与高亮 idx（-1 无高亮）。"""
        import re

        lines = self.render()
        rows = [ln for ln in lines if "opt-" in ln]
        if not rows:
            return (None, None, -1)

        def _idx(r: str):
            m = re.search(r"opt-(\d+)", r)
            return int(m.group(1)) if m else None

        first = _idx(rows[0])
        last = _idx(rows[-1])
        sel = next(
            (_idx(r) for r in rows
             if r.startswith("*") or r.lstrip().startswith("\u25c9")),
            -1,
        )
        return (first, last, sel)


_20 = [f"opt-{i:02d}" for i in range(20)]


class TestSelectInputFollowScroll:
    """★ 用户场景：补全弹窗 20 项、可见 8 行——按 ↓ 高亮逐行下移、
    越过末行后窗口滚动（能移动到未显示的行）。"""

    def test_down_moves_highlight_inside_window(self):
        """↓×7：高亮从首行移到末行，窗口保持 [0..7] 不动。"""
        t = _CtlHarness("select", _20, limit=8)
        first, last, sel = t.window()
        assert (first, last, sel) == (0, 7, 0)
        for expect in range(1, 8):
            assert t.key("arrow_down") is True
            first, last, sel = t.window()
            assert sel == expect, f"按↓后高亮应在 {expect}"
            assert (first, last) == (0, 7), "高亮未到末行前窗口不应滚动"

    def test_down_cross_bottom_scrolls_one_row(self):
        """第 8 次 ↓：窗口滚动 1 行（cmd-00 消失）且高亮贴底（末行）。"""
        t = _CtlHarness("select", _20, limit=8)
        for _ in range(8):
            t.key("arrow_down")
        first, last, sel = t.window()
        assert sel == 8
        assert (first, last) == (1, 8), "越过末行后窗口应滚 1 行、高亮贴底"
        # 再按 ↓：继续贴底滚动
        t.key("arrow_down")
        first, last, sel = t.window()
        assert (first, last, sel) == (2, 9, 9)

    def test_up_moves_highlight_back_without_scroll(self):
        """从贴底按 ↑：高亮上移回窗口内，窗口不动；越过首行才回滚。"""
        t = _CtlHarness("select", _20, limit=8)
        for _ in range(10):
            t.key("arrow_down")
        assert t.window() == (3, 10, 10)
        t.key("arrow_up")
        assert t.window() == (3, 10, 9), "窗口内 ↑ 高亮上移、窗口不动"
        # ↑ 回到窗口首行之上方（sel 3 → 2 < offset=3）→ 贴顶
        for _ in range(8):
            t.key("arrow_up")
        first, last, sel = t.window()
        assert sel == 1
        assert first == 1, "越过首行上方后窗口应上滚贴顶"
        t.key("arrow_up")
        assert t.window() == (0, 7, 0), "回到首项 → 窗口回顶"

    def test_vim_jk_navigation_same_scroll(self):
        """vim j/k（consumeAll 弹窗模式）导航同样跟随滚动。"""
        t = _CtlHarness("select", _20, limit=8)
        # consumeAll=False 时 j/k 不由控件消费（放行输入框）——用 ↓ 等价验证
        for _ in range(9):
            t.key("arrow_down")
        first, last, sel = t.window()
        assert (first, last, sel) == (2, 9, 9)

    def test_controlled_index_jump_scrolls_window(self):
        """受控 index 跳变（PgUp/PgDn 写回 completion.selected）窗口跟随。"""
        t = _CtlHarness("select", _20, limit=8, controlled=True)
        assert t.window() == (0, 7, 0)
        # 模拟 PgDn 翻页：外部受控 index 0 → 5（仍在首屏内 → 窗口不动）
        t.state["index"] = 5
        assert t.window() == (0, 7, 5)
        # 受控 index 跳到 15（越过窗口）→ 窗口贴底使 15 可见
        t.state["index"] = 15
        first, last, sel = t.window()
        assert sel == 15
        assert last == 15, "受控跳变后选中项应在窗口末行（贴底）"

    def test_controlled_wraparound_bottom_visible(self):
        """受控回绕（首项 ↑ 经 cycle_completion(-1) → 末项）贴底可见。"""
        t = _CtlHarness("select", _20, limit=8, controlled=True)
        t.state["index"] = 19  # 模拟回绕到末项
        first, last, sel = t.window()
        assert sel == 19
        assert last == 19, "回绕末项后高亮应在窗口末行"

    def test_controlled_wrap_back_to_top(self):
        """受控回绕（末项 ↓ → 首项）窗口回顶。"""
        t = _CtlHarness("select", _20, limit=8, controlled=True)
        t.state["index"] = 19
        t.render()
        t.state["index"] = 0  # 回绕回首项
        assert t.window() == (0, 7, 0)

    def test_items_shrink_clamps_offset(self):
        """items 动态缩小（打字过滤）→ offset 钳制、selected 回首屏可见。"""
        t = _CtlHarness("select", _20, limit=8, controlled=True)
        t.state["index"] = 10
        assert t.window() == (3, 10, 10)
        # 打字过滤：items 20 → 5（show_completions 刷新 selected=0）
        t.state["items"] = _20[:5]
        t.state["index"] = 0
        first, last, sel = t.window()
        assert (first, last, sel) == (0, 4, 0), "缩小后窗口回顶全量显示"

    def test_no_limit_no_scroll_rows_all(self):
        """limit=None（全量模式）零回归：所有项渲染、无滚动窗口。"""
        t = _CtlHarness("select", _20[:5], limit=None)
        first, last, sel = t.window()
        assert (first, last, sel) == (0, 4, 0)
        t.key("arrow_down")
        assert t.window() == (0, 4, 1)

    def test_on_highlight_fired_per_key(self):
        """每次导航 onHighlight 回调一次（写回 completion.selected 链路）。"""
        t = _CtlHarness("select", _20, limit=8)
        t.key("arrow_down")
        t.key("arrow_down")
        assert t.state["highlights"] == [1, 2]


class TestMultiSelectFollowScroll:
    """MultiSelect（user_select 多选弹窗）同一跟随滚动语义。"""

    def test_down_follow_scroll(self):
        t = _CtlHarness("multi", _20, limit=8)
        assert t.window() == (0, 7, 0)
        for _ in range(7):
            t.key("arrow_down")
        assert t.window() == (0, 7, 7), "窗口内 ↓ 高亮逐行到末行、窗口不动"
        t.key("arrow_down")
        assert t.window() == (1, 8, 8), "越过末行后窗口滚 1 行、高亮贴底"

    def test_up_cross_top_scrolls_back(self):
        t = _CtlHarness("multi", _20, limit=8)
        for _ in range(12):
            t.key("arrow_down")
        assert t.window() == (5, 12, 12)
        t.key("arrow_up")
        assert t.window() == (5, 12, 11), "窗口内 ↑ 窗口不动"
        # 窗口 [5..12] 内逐行上移到首行（sel=5）
        for _ in range(6):
            t.key("arrow_up")
        assert t.window() == (5, 12, 5), "窗口内 ↑ 高亮移到首行、窗口不动"
        # 再 ↑（sel=4 < offset=5）→ 窗口上滚贴顶
        t.key("arrow_up")
        assert t.window() == (4, 11, 4), "越过首行上方后窗口贴顶"


class TestRadioListFollowScroll:
    """RadioList 单选列表同一跟随滚动语义。"""

    def test_down_follow_scroll(self):
        t = _CtlHarness("radio", _20, limit=8)
        assert t.window() == (0, 7, 0)
        for _ in range(8):
            t.key("arrow_down")
        assert t.window() == (1, 8, 8), "越过末行后窗口滚 1 行、高亮贴底"

    def test_up_sticks_top(self):
        t = _CtlHarness("radio", _20, limit=8)
        for _ in range(4):
            t.key("arrow_down")
        assert t.window() == (0, 7, 4)
        for _ in range(4):
            t.key("arrow_up")
        assert t.window() == (0, 7, 0), "窗口内 ↑ 窗口不动"


# ── 3. 分栏回退路径 _completion_scroll_offset ─────────────


class TestCompletionScrollOffsetFollow:
    """split_desc 分栏弹窗（_build_popup_lines 回退路径）跟随滚动。"""

    def test_inside_window_keeps_offset(self):
        """选中项在窗口内 → offset 不变。"""
        assert _completion_scroll_offset(5, 20, 8, current=2) == 2
        assert _completion_scroll_offset(9, 20, 8, current=2) == 2

    def test_cross_bottom_sticks_bottom(self):
        """越过窗口底部 → 贴底。"""
        assert _completion_scroll_offset(10, 20, 8, current=2) == 3
        assert _completion_scroll_offset(19, 20, 8, current=2) == 12

    def test_cross_top_sticks_top(self):
        """越过窗口顶部 → 贴顶。"""
        assert _completion_scroll_offset(1, 20, 8, current=2) == 1
        assert _completion_scroll_offset(0, 20, 8, current=5) == 0

    def test_current_clamped_and_no_scroll_cases(self):
        """current 钳制 + 非法参数（无滚动）。"""
        # current=15 钳到 4（max_offset=4）；sel=5 在 [4..11] 内 → 保持 4
        assert _completion_scroll_offset(5, 12, 8, current=15) == 4
        assert _completion_scroll_offset(3, 8, 8, current=0) == 0
        assert _completion_scroll_offset(0, 0, 8, current=3) == 0
        assert _completion_scroll_offset(1, 5, 0, current=2) == 0

    def test_build_popup_lines_persists_scroll(self):
        """_build_popup_lines 把滚动偏移写回 completion._popup_scroll。"""
        from src.tui.app._state_types import CompletionState

        c = CompletionState()
        c.visible = True
        c.items = [f"c-{i:02d}" for i in range(20)]
        c.texts = list(c.items)
        c.selected = 10
        c.split_desc = True
        c.descriptions = [f"desc-{i}" for i in range(20)]
        _build_popup_lines(c, 80, 0.0)
        # 初始（current=0）：sel=10 越过首屏 → 贴底 offset=3
        assert getattr(c, "_popup_scroll", 0) == 3
        # 下一帧 sel=9（窗口 [3..10] 内）→ offset 保持 3
        c.selected = 9
        _build_popup_lines(c, 80, 0.0)
        assert c._popup_scroll == 3
        # 下一帧 sel=2（窗口上方）→ 贴顶 2
        c.selected = 2
        _build_popup_lines(c, 80, 0.0)
        assert c._popup_scroll == 2


# ── 4. 端到端：CompletionPopup 真实组件树（用户场景） ─────


try:
    import pyte
except ImportError:  # pragma: no cover
    pyte = None


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestCompletionPopupEndToEnd:
    """补全弹窗（App 组件树 + input router 发键）跟随滚动端到端。"""

    def _harness(self):
        h_ = _AppHarness()
        h_.open(20)
        return h_

    def test_highlight_moves_row_by_row_then_scrolls(self):
        """★ 用户需求复现：20 项可见 8 行，按 ↓ 高亮逐行下移，
        到末行后再按窗口滚动（cmd-08 等未显示项进入视野）。"""
        t = self._harness()
        # 前 7 次 ↓：高亮 1..7 逐行下移，窗口 [00..07] 不动
        for expect in range(1, 8):
            t.press("arrow_down")
            win, sel = t.popup_state()
            assert sel == expect
            assert win == ("cmd-00", "cmd-07"), "高亮未到末行前窗口不动"
        # 第 8 次 ↓：窗口滚动 1 行、高亮贴底（cmd-08 从未显示变为可见）
        t.press("arrow_down")
        win, sel = t.popup_state()
        assert sel == 8
        assert win == ("cmd-01", "cmd-08"), "越过末行后窗口应滚 1 行、高亮贴底"
        # 持续按 ↓ 到末项：始终贴底可见
        for _ in range(11):
            t.press("arrow_down")
        win, sel = t.popup_state()
        assert sel == 19
        assert win == ("cmd-12", "cmd-19"), "末项贴底可见（能移动到未显示的行）"

    def test_up_from_bottom_scrolls_back(self):
        """到达末项后按 ↑ 逐行上移、窗口逐步回滚至顶部。"""
        t = self._harness()
        for _ in range(19):
            t.press("arrow_down")
        assert t.popup_state() == (("cmd-12", "cmd-19"), 19)
        for _ in range(19):
            t.press("arrow_up")
        win, sel = t.popup_state()
        assert sel == 0
        assert win == ("cmd-00", "cmd-07"), "回到首项 → 窗口回顶"


class _AppHarness:
    """完整 App 组件树 + model.completion 补全弹窗 + router 发键。"""

    def __init__(self):
        self.model = AppModel()
        self.model.width = 80
        self.rec = Reconciler(schedule_callback=None)
        self.root = self.rec.create_root()
        self.router = None

        def _on_router(router):
            self.router = router

        _hooks.set_input_router_callback(_on_router)

    def open(self, n: int):
        c = self.model.completion
        c.visible = True
        c.items = [f"cmd-{i:02d}" for i in range(n)]
        c.texts = list(c.items)
        c.types = ["command"] * n
        c.selected = 0
        self.render()

    def render(self):
        self.rec.render(self.root, h(App, {"model": self.model, "width": 80}), 80, 40)
        return _components.render_frame(self.root, 80)

    def press(self, kind: str):
        assert self.router is not None
        self.router(KeyEvent(kind=kind))
        self.render()

    def popup_state(self):
        """返回 ((窗口首项文本, 窗口末项文本), 高亮项序号)。"""
        frame = self.render()
        texts = [
            "".join(r.text for r in ln.runs)
            for ln in frame.lines
            if ln.runs and "cmd-" in "".join(r.text for r in ln.runs)
            and "Tab" not in "".join(r.text for r in ln.runs)
        ]
        assert texts, "帧中未找到补全候选项行"
        first_t = texts[0].split("cmd-")[1][:2]
        last_t = texts[-1].split("cmd-")[1][:2]
        sel = next(
            (int(r.split("cmd-")[1][:2]) for r in texts if "\u25b6" in r), None,
        )
        return ((f"cmd-{first_t}", f"cmd-{last_t}"), sel)
