"""app 层 Code Review 修复测试（P1-1 / P2-1 ~ P2-9）。

覆盖修复点：
  - P1-1  status_bar 模型名点 FadeIn 渐显窗口内 0.1s 桶（空闲首次出现动画可见）
  - P2-1  header 宽屏预算充足时不调用 truncate_runs（title/ver 引用复用零重建）
  - P2-2  _build_lines 快照键对 completion.texts/descriptions=None 不崩溃
  - P2-3  _input_snap_key 直接放 text_str（str 按值比较，无 hash+len 碰撞）
  - P2-4  _do_display_messages 对非 dict 消息跳过
  - P2-5  close_tool_box 图标替换扫描失败时头部插入 icon（不丢标题首 run）
  - P2-6  user_select options 空且可见时自动以 default_options 置 done 回退
  - P2-7  _popup_builder 位置提示 total 统一 len(items)（texts 长度不一致不错位）
  - P2-8  status_bar _build_status_runs/use_memo deps 对 tool_* 字段 getattr 防御
  - P2-9  _build_lines fading 判断经 _default_fx_params() 惰性读取 TuiConfig
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.tui.app.input_area as ia
import src.tui.app.status_bar as sb
import src.tui.ink.helpers as helpers
from src.tui.app import _fx
from src.tui.app._popup_builder import _build_popup_lines
from src.tui.app.apply import _do_display_messages
from src.tui.app.header import TopHeader
from src.tui.app.input_area import _build_lines, _input_snap_key
from src.tui.app.model import AppModel
from src.tui.app.status_bar import StatusBar, _build_status_runs
from src.tui.app.user_select import UserSelectPopup
from src.tui.core.style import Style
from src.tui.ink import Line, StyledRun, hooks
from src.tui.ink.fiber import Fiber, TAG_FUNCTION


# ── 渲染辅助（与 test_header.py 同模式：Fiber + hooks 环境） ─────────

def _render(component, props, fiber=None):
    """在 hook 环境下渲染函数组件；fiber 复用可跨渲染保留 hook 状态。

    返回 (元素, fiber)。同 fiber 二次渲染前须 ``fiber.reset_hooks()``
    （模拟 reconciler 每次渲染前清零 hook_index）。
    """
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    hooks._push_current(fiber)
    try:
        return component(props), fiber
    finally:
        hooks._pop_current()


def _render_twice(component, props1, props2=None):
    """同 fiber 渲染两次（第二次前 reset_hooks），返回 (el1, el2)。"""
    props2 = props1 if props2 is None else props2
    el1, fiber = _render(component, props1)
    fiber.reset_hooks()
    el2, _ = _render(component, props2, fiber)
    return el1, el2


def _model_stub(status_active: bool = False, **status_kw) -> SimpleNamespace:
    """最小模型桩：model.status.status_active + 可选字段。"""
    st = {"status_active": status_active, "model_name": ""}
    st.update(status_kw)
    return SimpleNamespace(status=SimpleNamespace(**st))


# ═══════════════════════════════════════════════════════════
# P1-1：status_bar 渐显窗口内 0.1s 桶
# ═══════════════════════════════════════════════════════════

def test_status_bar_idle_fade_window_01s_bucket(monkeypatch):
    """P1-1：空闲渐显窗口（dot_elapsed < fade_duration）内 0.1s 桶。

    0.15s 后仍在 1s 桶内（int(100.15)==100）但跨 0.1s 桶
    （int(100.15/0.1)==1001 ≠ 1000）→ use_memo 重建 → _build_status_runs
    再次调用——渐显动画在空闲首次出现模型名时平滑推进。
    修复前：空闲恒 1s 桶，0.15s 同桶缓存命中（不重建）→ 渐显不可见。
    """
    calls = {"n": 0}
    orig = sb._build_status_runs

    def counting(model, *args, **kwargs):
        calls["n"] += 1
        return orig(model, *args, **kwargs)

    monkeypatch.setattr(sb, "_build_status_runs", counting)

    current = [100.0]
    monkeypatch.setattr(sb.time, "monotonic", lambda: current[0])

    model = _model_stub(status_active=False, model_name="test-model")
    props = {"model": model, "width": 80}
    _, fiber = _render(StatusBar, props)
    assert calls["n"] == 1, "首次渲染应构建状态行"

    # 0.15s 后：同 1s 桶、跨 0.1s 桶
    current[0] = 100.15
    fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        StatusBar(props)
    finally:
        hooks._pop_current()
    assert calls["n"] == 2, "渐显窗口内 0.15s 跨 0.1s 桶应触发重建（渐显平滑推进）"


def test_status_bar_idle_after_fade_returns_1s_bucket(monkeypatch):
    """P1-1：渐显结束后（dot_elapsed >= fade_duration）回退 1s 桶。

    0.6s（fade 结束边界）后 time_dep 进入 1s 桶；0.15s 内（同 1s 桶）
    缓存命中不再重建——PERF-3 缓存语义保持。
    """
    calls = {"n": 0}
    orig = sb._build_status_runs

    def counting(model, *args, **kwargs):
        calls["n"] += 1
        return orig(model, *args, **kwargs)

    monkeypatch.setattr(sb, "_build_status_runs", counting)

    current = [100.0]
    monkeypatch.setattr(sb.time, "monotonic", lambda: current[0])

    model = _model_stub(status_active=False, model_name="test-model")
    props = {"model": model, "width": 80}
    _, fiber = _render(StatusBar, props)
    assert calls["n"] == 1

    # 渐显窗口结束（dot_elapsed = 0.7 >= fade_duration 0.6，留足浮点边界）→ 回 1s 桶
    current[0] = 100.7
    fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        StatusBar(props)
    finally:
        hooks._pop_current()
    assert calls["n"] == 2, "渐显结束应重建（time_dep 从 0.1s 桶切到 1s 桶）"

    # 同 1s 桶内（0.15s 后）缓存命中不重建
    current[0] = 100.85
    fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        StatusBar(props)
    finally:
        hooks._pop_current()
    assert calls["n"] == 2, "渐显结束后同 1s 桶不应重建（PERF-3 缓存语义保持）"


# ═══════════════════════════════════════════════════════════
# P2-1：header 宽屏不重建
# ═══════════════════════════════════════════════════════════

def test_header_wide_no_truncate_reuse(monkeypatch):
    """P2-1：宽屏预算充足时不调用 truncate_runs——title/ver 引用跨帧复用。

    修复前无条件截断：每次调用新建 runs 列表（引用变化）→ TEXT 缓存每帧
    miss → 整个 header 每帧重建。
    """
    calls = {"n": 0}
    orig = helpers.truncate_runs

    def counting(runs, w):
        calls["n"] += 1
        return orig(runs, w)

    monkeypatch.setattr(helpers, "truncate_runs", counting)

    props = {"model": _model_stub(status_active=False), "width": 80}
    el1, fiber = _render(TopHeader, props)
    assert calls["n"] == 0, "宽屏预算充足不应调用 truncate_runs"

    fiber.reset_hooks()
    el2, _ = _render(TopHeader, props, fiber)
    assert calls["n"] == 0, "宽屏跨帧仍不应调用 truncate_runs"
    # 渐变标题（children[1]）与版本号（children[2]）styled 引用跨帧复用
    assert el2.children[1].props.get("styled") is el1.children[1].props.get("styled"), "标题 runs 引用应跨帧复用"
    assert el2.children[2].props.get("styled") is el1.children[2].props.get("styled"), "版本号 runs 引用应跨帧复用"


def test_header_narrow_still_truncates(monkeypatch):
    """P2-1 回归：窄屏预算不足时仍调用 truncate_runs（总宽 <= width）。"""
    calls = {"n": 0}
    orig = helpers.truncate_runs

    def counting(runs, w):
        calls["n"] += 1
        return orig(runs, w)

    monkeypatch.setattr(helpers, "truncate_runs", counting)

    props = {"model": _model_stub(status_active=False), "width": 10}
    el, _ = _render(TopHeader, props)
    assert calls["n"] >= 1, "窄屏预算不足应调用 truncate_runs"
    total = 0
    for child in el.children:
        styled = child.props.get("styled")
        if styled:
            total += sum(r.width for r in styled)
    assert total <= 10, f"窄屏总宽 {total} > 10"


# ═══════════════════════════════════════════════════════════
# P2-2：_build_lines 快照键 None 防御
# ═══════════════════════════════════════════════════════════

def _build_lines_fiber(text="ab", completion=None, width=40):
    return SimpleNamespace(
        props={
            "text": text,
            "completion": completion,
            "status_active": False,
            "cpu": 0,
            "mem": 0,
            "history_search": None,
        },
        layout_box=SimpleNamespace(w=width, x=0, y=0),
        _lines_cache=None,
        _input_layout_cache=None,
        _placeholder_fade_key=None,
    )


def test_build_lines_completion_texts_none_regression():
    """P2-2：_build_lines 快照键对 completion.texts/descriptions=None 不崩溃。

    修复前 ``len(completion.texts)`` 抛 TypeError（外部注入 None）。
    """
    comp = SimpleNamespace(
        visible=True, items=["a", "b"], texts=None, descriptions=None,
        selected=0, split_desc=False,
    )
    fiber = _build_lines_fiber(completion=comp)
    lines = _build_lines(fiber, include_popup=False)
    assert isinstance(lines, list)
    assert lines, "输入区行应非空"


# ═══════════════════════════════════════════════════════════
# P2-3：_input_snap_key str 值比较
# ═══════════════════════════════════════════════════════════

def _snap_key(text: str):
    return _input_snap_key(
        {
            "text": text,
            "completion": None,
            "status_active": False,
            "cpu": 0,
            "mem": 0,
            "history_search": None,
        },
        80,
        100.0,
    )


def test_input_snap_key_text_str_value():
    """P2-3：_input_snap_key 直接放 text_str（str 按值比较）。

    修复前 ``hash(text_str)+len(text_str)`` 指纹：哈希碰撞（不同文本 hash+len
    相同）可致错误命中；且首个元素为 int 而非文本值。
    """
    k1 = _snap_key("hello")
    assert k1[0] == "hello", "deps 首个元素应为 text_str 值本身（非 hash）"
    assert isinstance(k1[0], str)

    # 相同文本（不同 str 对象）→ 相同 key（str 按值比较）
    k2 = _snap_key("hello")
    assert k1 == k2

    # 文本变化 → key 不同
    k3 = _snap_key("hellp")
    assert k1 != k3


# ═══════════════════════════════════════════════════════════
# P2-4：_do_display_messages 非 dict 消息防御
# ═══════════════════════════════════════════════════════════

def test_display_messages_non_dict_skipped():
    """P2-4：_do_display_messages 对非 dict 消息跳过（不崩溃）。

    修复前 ``msg.get`` 抛 AttributeError 中断回放。
    """
    model = AppModel()
    cmd = SimpleNamespace(messages=[
        {"role": "user", "content": "hi"},
        "not-a-dict",
        None,
        42,
        {"role": "user", "content": "world"},
    ])
    _do_display_messages(model, cmd)  # 不抛异常
    # 两条 user 消息均渲染（非 dict 被跳过）
    user_blocks = [b for b in model.blocks if b.kind == "user"]
    assert len(user_blocks) == 2, f"应渲染 2 条 user 消息: {len(user_blocks)}"


# ═══════════════════════════════════════════════════════════
# P2-5：close_tool_box 图标替换不丢首 run
# ═══════════════════════════════════════════════════════════

def test_close_tool_box_icon_insert_keep_first_run():
    """P2-5：close_tool_box 图标替换扫描失败时头部插入 icon（不丢标题首 run）。

    构造 committed 标题行 runs[0] 非图标（标题被截断/结构异常），扫描找不到
    图标字符——修复前 ``icon + runs[1:]`` 丢弃 runs[0]（"bash" 丢失）；修复后
    ``icon + runs`` 头部插入并保留全部内容。
    """
    from src.renderer.ansi.helpers import AnsiLine

    model = AppModel()
    model.open_tool_box("t1", "bash", "pwd")
    block = model.tool_boxes["t1"]
    block.lines.append(AnsiLine.of("  out"))
    model.commit_open_block(block)
    offset = block.extra["_first_committed_offset"]
    assert offset is not None and 0 <= offset < len(model.committed_lines)

    # 模拟标题行被截断：runs[0] 不是状态图标字符
    model.committed_lines[offset] = Line([
        StyledRun("bash", Style(fg=23)),
        StyledRun("pwd", Style(fg=242)),
    ])

    model.close_tool_box("t1", True)

    new_line = model.committed_lines[offset]
    assert new_line.runs[0].text.strip() == "\u2714", "图标应插入头部"
    assert any(r.text == "bash" for r in new_line.runs), "标题首 run 不应丢失"
    assert any(r.text == "pwd" for r in new_line.runs), "标题其余 run 不应丢失"


# ═══════════════════════════════════════════════════════════
# P2-6：user_select options 空自动回退
# ═══════════════════════════════════════════════════════════

def test_user_select_empty_options_auto_done():
    """P2-6：options 空且可见时自动以 default_options 置 done=True 回退。

    修复前弹窗静默不可见（visible=False），无超时（deadline=0）时工具协程
    永远轮询 ``us.done`` → 交互卡死。
    """
    from src.tui.app._state_types import UserSelectState

    us = UserSelectState(
        visible=True, seq=1, options=[], default_options=["def"],
        done=False,
    )
    model = SimpleNamespace(user_select=us)
    props = {"model": model, "width": 40}
    el, _ = _render(UserSelectPopup, props)

    assert us.done is True, "options 空应自动置 done"
    assert us.action == "confirmed"
    assert us.result == ["def"]
    # 弹窗不可见（零高度不占行）
    assert el.type == "text"


def test_user_select_empty_options_no_double_write():
    """P2-6：自动回退幂等——done 已置位后不再覆盖（first-write-wins）。"""
    from src.tui.app._state_types import UserSelectState

    us = UserSelectState(
        visible=True, seq=1, options=[], default_options=["def"],
        done=True, action="timeout", result=["timeout-result"],
    )
    model = SimpleNamespace(user_select=us)
    props = {"model": model, "width": 40}
    el, fiber = _render(UserSelectPopup, props)

    # done 已由工具置位 → 不覆盖（保留 timeout 结果）
    assert us.action == "timeout"
    assert us.result == ["timeout-result"]

    # 再次渲染仍不覆盖
    fiber.reset_hooks()
    el2, _ = _render(UserSelectPopup, props, fiber)
    assert us.action == "timeout"
    assert us.result == ["timeout-result"]


# ═══════════════════════════════════════════════════════════
# P2-7：_popup_builder total 计算
# ═══════════════════════════════════════════════════════════

def _popup_stub(items, texts=None, descriptions=None, selected=0, title="t"):
    return SimpleNamespace(
        visible=True, items=items,
        texts=texts if texts is not None else items,
        descriptions=descriptions or [],
        selected=selected, split_desc=False, title=title,
        match_prefix="", types=[], locked_height=0,
        _popup_lines_cache=None,
    )


def test_popup_total_uses_items_len():
    """P2-7：标题位置提示总数用 len(items)（texts 长度不一致不错位）。

    修复前 ``total = len(texts)``（texts 短时标题显示 ``(3/1)`` 荒谬位置）。
    """
    comp = _popup_stub(items=["a", "b", "c"], texts=["x"], selected=2)
    lines = _build_popup_lines(comp, 40, now=0.0)
    assert lines, "弹窗行应非空"
    assert "(3/3)" in lines[0].plain, f"位置提示应 (3/3): {lines[0].plain!r}"


def test_popup_total_texts_longer_still_items_len():
    """P2-7：texts 比 items 长时 total 仍为 len(items)（渲染候选行数）。"""
    comp = _popup_stub(items=["a", "b"], texts=["x", "y", "z"], selected=1)
    lines = _build_popup_lines(comp, 40, now=0.0)
    assert "(2/2)" in lines[0].plain, f"位置提示应 (2/2): {lines[0].plain!r}"


# ═══════════════════════════════════════════════════════════
# P2-8：status_bar tool_* 字段 getattr 防御
# ═══════════════════════════════════════════════════════════

def test_build_status_runs_missing_tool_fields():
    """P2-8：_build_status_runs 对缺 tool_total/count/fail 的桩状态不崩溃。

    修复前直接 ``st.tool_total`` 属性访问 → AttributeError（测试桩/异常状态
    对象缺字段）。
    """
    st = SimpleNamespace(
        status_active=True, model_name="m", main_phase="", bg_bash_count=0,
    )
    model = SimpleNamespace(status=st)
    runs = _build_status_runs(model, 0.0)
    assert isinstance(runs, list)
    assert runs, "模型名 runs 应非空"


def test_status_bar_deps_getattr_missing_fields(monkeypatch):
    """P2-8：use_memo deps 对缺 tool_* 字段的桩状态不崩溃（StatusBar 渲染）。"""
    current = [100.0]
    monkeypatch.setattr(sb.time, "monotonic", lambda: current[0])
    model = _model_stub(status_active=False, model_name="m")
    props = {"model": model, "width": 80}
    el, _ = _render(StatusBar, props)
    assert el is not None


# ═══════════════════════════════════════════════════════════
# P2-9：_build_lines fading 惰性读取
# ═══════════════════════════════════════════════════════════

def test_build_lines_fade_duration_lazy(monkeypatch):
    """P2-9：_build_lines fading 判断经 _default_fx_params() 惰性读取。

    修复前用固化快照 _DEFAULT_FADE_DURATION(0.6)：配置 fade_duration 改 0.4
    后 elapsed=0.5 仍判 fading（0.5<0.6）→ 0.1s 桶；修复后惰性读取 0.4 →
    elapsed=0.5 非 fading → 空闲 0.25s 桶。
    """
    monkeypatch.setattr(ia.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(_fx, "_default_fx_params", lambda: (0.4, 10.0))

    fiber = _build_lines_fiber(text="", completion=None)
    fiber._placeholder_fade_key = ("ph", 99.5)  # fade_elapsed = 0.5

    lines = _build_lines(fiber, include_popup=False)
    assert isinstance(lines, list)
    snap = fiber._lines_cache[0]
    time_bucket = snap[-1]
    assert time_bucket == int(100.0 / 0.25), (
        f"fade_duration=0.4 < elapsed=0.5 → 非 fading → 0.25s 桶，实际 {time_bucket}"
    )
