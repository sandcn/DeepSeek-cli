"""ink 框架架构修复回归测试（P0/P1/P2 一批）。

覆盖本次架构修复：
  1. ``HookContext`` 多会话隔离（Reconciler 私有上下文 / hooks 门面代理）；
  2. ``Fiber`` 扩展状态字段显式声明（消除动态挂载的隐式属性）；
  3. memo 组件从「删除」恢复时整棵子树 ``deleted`` 复位（effects/refs/
     input hooks 不丢）；
  4. 父链变化时 context 逐 fiber 缓存失效；
  5. committed 内容区起始行**显式注入**（输出历史不再硬编码偏移）；
  6. 单帧重写行数超限 → 降级为受控全量重写（防病态大重写冻结 UI）；
  7. 帧差异区间收集复用稳定前缀跳过；
  8. ``_set_props`` 浅引用全等快路径；
  9. ``use_input`` 兼容包装缓存以 handler 对象为键（不可哈希安全）；
 10. ``AppModel`` 未声明字段访问告警（拼写错误可观测）；
 11. 渲染线程恒定 30Hz（无空闲跳过，``render_interval`` 不可改变）；
 12. ``StaticLines`` 前缀缓存键用 lines 对象身份（非 ``id()``）；
 13. ``_measure`` 的 TEXT 分支提取为 ``_measure_text``（行为不变）。
"""

from __future__ import annotations

import io
import logging
import threading

import pytest

from src.tui.ink import hooks as H
from src.tui.ink import h, TEXT, BOX, memo, create_context, use_context, use_effect
from src.tui.ink.output import Frame, Line


# ═══════════════════════════════════════════════════════════
# 1. HookContext 多会话隔离
# ═══════════════════════════════════════════════════════════

def test_reconciler_hook_contexts_are_independent():
    from src.tui.ink.reconciler import Reconciler

    a = Reconciler(schedule_callback=lambda: None)
    b = Reconciler(schedule_callback=lambda: None)
    assert a.hook_context is not b.hook_context
    assert a.hook_context.schedule_callback is not None
    assert b.hook_context.schedule_callback is not None
    # 后构造者不得覆盖先构造者的回调
    cb_a = a.hook_context.schedule_callback
    assert a.hook_context.schedule_callback is cb_a


def test_hooks_module_proxy_routes_to_current_context():
    ctx = H.HookContext()
    H.push_context(ctx)
    try:
        H._schedule_callback = "cb-x"
        assert ctx.schedule_callback == "cb-x"
        assert H._schedule_callback == "cb-x"
        H._app_control = {"exit": 1}
        assert ctx.app_control == {"exit": 1}
    finally:
        H.pop_context()
    # 弹出后回到默认上下文（不残留刚写入的值）
    assert H.current_context() is H.default_context()
    assert H._schedule_callback != "cb-x"


def test_context_snapshot_restore_roundtrip():
    ctx = H.HookContext()
    ctx.any_key_pressed = True
    ctx.focus_ids = ["a"]
    snap = ctx.snapshot()
    ctx.any_key_pressed = False
    ctx.focus_ids = []
    ctx.restore(snap)
    assert ctx.any_key_pressed is True
    assert ctx.focus_ids == ["a"]


def test_two_sessions_render_independently():
    """两个独立 Reconciler 各自渲染，hooks 状态互不干扰（顺序渲染）。"""
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import components as _components

    def Comp(props):
        return h(TEXT, {"children": props["tag"]})

    outs = []
    for tag in ("one", "two"):
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        rec.render(root, h(Comp, {"tag": tag}), 20, 3)
        frame = _components.render_frame(root, 20)
        outs.append("".join(line.plain for line in frame.lines).strip())
    assert outs == ["one", "two"]


# ═══════════════════════════════════════════════════════════
# 2. Fiber 扩展字段显式声明
# ═══════════════════════════════════════════════════════════

def test_fiber_extension_fields_have_defaults():
    from src.tui.ink.fiber import Fiber, TAG_HOST, MemoHook

    f = Fiber(TAG_HOST, "text", {})
    for name, default in (
        ("_measure_cache", None),
        ("_wrapped_lines", None),
        ("_input_layout_cache", None),
        ("_committed_chat_cache", None),
        ("_truncated_prefix_cache", None),
        ("_committed_prefix", None),
        ("_resolved_lines", None),
        ("_wrap_cache", None),
        ("_placeholder_fade_key", None),
        ("_focus_id", None),
        ("_committed_chat_present", False),
        ("_has_absolute_present", False),
        ("_is_fallback_root", False),
    ):
        assert hasattr(f, name), name
        assert getattr(f, name) == default, name
    # MemoHook 的 useImperativeHandle 最近 ref（同批显式化）
    assert MemoHook()._last_ref is None


# ═══════════════════════════════════════════════════════════
# 3. memo 删除后复用：整棵子树复活
# ═══════════════════════════════════════════════════════════

def test_deleted_subtree_is_revived_on_memo_reuse():
    from src.tui.ink.reconciler import Reconciler

    calls: list[str] = []

    def Inner(props):
        def _eff():
            calls.append("mount")
            return lambda: calls.append("unmount")
        use_effect(_eff, ())
        return h(TEXT, {"children": "inner"})

    M = memo(Inner)

    def Root(props):
        if props["show"]:
            return h(BOX, None, [h(M, {"key": "m"})])
        return h(TEXT, {"children": "off"})

    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(Root, {"show": True}), 20, 5)
    assert calls == ["mount"]
    rec.render(root, h(Root, {"show": False}), 20, 5)
    assert calls == ["mount", "unmount"]
    rec.render(root, h(Root, {"show": True}), 20, 5)

    # 复用（memo 短路保留旧子树）后不得有 deleted 残留——否则
    # _collect_render_metadata / _collect_input_hooks 会跳过整棵子树。
    stack = [root]
    seen = 0
    while stack:
        f = stack.pop()
        while f is not None:
            assert not f.deleted, f"deleted 残留: {f.type!r}"
            seen += 1
            if f.child is not None:
                stack.append(f.sibling)
                f = f.child
            else:
                f = f.sibling
    assert seen > 1


# ═══════════════════════════════════════════════════════════
# 4. 父链变化 → context 缓存失效
# ═══════════════════════════════════════════════════════════

def test_revive_reused_clears_context_cache_on_parent_change():
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.fiber import Fiber, TAG_HOST

    f = Fiber(TAG_HOST, "text", {})
    old_parent = Fiber(TAG_HOST, "box", {})
    new_parent = Fiber(TAG_HOST, "box", {})
    f.return_ = old_parent
    f._context_cache["__ctx_0__"] = "stale"

    Reconciler._revive_reused(f, new_parent)
    assert f._context_cache == {}
    assert f._context_dirty is True

    g = Fiber(TAG_HOST, "text", {})
    g.return_ = new_parent
    g._context_cache["__ctx_0__"] = "keep"
    Reconciler._revive_reused(g, new_parent)
    assert g._context_cache == {"__ctx_0__": "keep"}


def test_use_context_reads_provider_value_through_tree():
    ctx = create_context("default")
    seen = {}

    def Consumer(props):
        seen["v"] = use_context(ctx)
        return h(TEXT, {"children": str(seen["v"])})

    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink import components as _components

    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(ctx.Provider, {"value": "hello"}, h(Consumer, {})), 30, 3)
    assert seen["v"] == "hello"
    frame = _components.render_frame(root, 30)
    assert "hello" in "".join(line.plain for line in frame.lines)


# ═══════════════════════════════════════════════════════════
# 5. 内容区起始行显式注入
# ═══════════════════════════════════════════════════════════

def test_content_start_injected_explicitly():
    from src.tui.ink.renderer import InkRenderer

    collected: list[str] = []
    r = InkRenderer(stream=io.StringIO(), line_callback=collected.append, height=0)
    r.set_content_line_count(0, start=3)
    r.set_content_line_count(2, start=3)
    frame = Frame([Line.of(f"L{i}") for i in range(6)])
    r._emit_content_lines(frame)
    assert collected == ["L3\n", "L4\n"]


def test_content_start_default_is_header_offset():
    from src.tui.ink.renderer import InkRenderer, _CONTENT_LINE_OFFSET

    r = InkRenderer(stream=io.StringIO(), height=0)
    assert r._content_start == _CONTENT_LINE_OFFSET


# ═══════════════════════════════════════════════════════════
# 6. 病态大重写降级
# ═══════════════════════════════════════════════════════════

def test_oversized_rewrite_degrades_to_full_rewrite():
    from src.tui.ink.renderer import InkRenderer, _MAX_REWRITE_ROWS

    stream = io.StringIO()
    r = InkRenderer(stream=stream, height=0)
    n = _MAX_REWRITE_ROWS + 50
    r.render(Frame([Line.of(f"a{i}") for i in range(n)]))
    stream.seek(0)
    stream.truncate(0)
    r.render(Frame([Line.of(f"b{i}") for i in range(n)]))
    out = stream.getvalue()
    assert "\033[2J" in out, "超限应降级为受控全量重写（清屏 + 单次全量写入）"
    assert "b0" in out and f"b{n - 1}" in out


def test_rewrite_within_limit_stays_incremental():
    from src.tui.ink.renderer import InkRenderer

    stream = io.StringIO()
    r = InkRenderer(stream=stream, height=0)
    r.render(Frame([Line.of(f"a{i}") for i in range(50)]))
    stream.seek(0)
    stream.truncate(0)
    lines = [Line.of(f"a{i}") for i in range(50)]
    lines[3] = Line.of("changed")
    r.render(Frame(lines))
    out = stream.getvalue()
    assert "\033[2J" not in out, "阈值内应保持增量（无清屏）"
    assert "changed" in out


# ═══════════════════════════════════════════════════════════
# 7. 帧差异区间复用稳定前缀跳过
# ═══════════════════════════════════════════════════════════

def test_diff_runs_skips_stable_prefix():
    from src.tui.ink._frame_diff import _diff_runs

    prefix = [Line.of("p0"), Line.of("p1")]
    prev = Frame(
        prefix + [Line.of("t0"), Line.of("t1")],
        stable_prefix=prefix, stable_prefix_offset=0, stable_prefix_len=2,
    )
    new = Frame(
        prefix + [Line.of("t0"), Line.of("T1")],
        stable_prefix=prefix, stable_prefix_offset=0, stable_prefix_len=2,
    )
    assert _diff_runs(prev, new, 4) == [(3, 4)]


def test_diff_runs_without_stable_prefix_compares_all():
    from src.tui.ink._frame_diff import _diff_runs

    prev = Frame([Line.of("a"), Line.of("b")])
    new = Frame([Line.of("A"), Line.of("b")])
    assert _diff_runs(prev, new, 2) == [(0, 1)]


def test_skip_interval_requires_same_prefix_object():
    from src.tui.ink._frame_diff import _skip_interval

    a = [Line.of("x")]
    b = [Line.of("x")]
    prev = Frame([Line.of("x")], stable_prefix=a, stable_prefix_offset=0, stable_prefix_len=1)
    same = Frame([Line.of("x")], stable_prefix=a, stable_prefix_offset=0, stable_prefix_len=1)
    diff = Frame([Line.of("x")], stable_prefix=b, stable_prefix_offset=0, stable_prefix_len=1)
    assert _skip_interval(prev, same) == (0, 1)
    assert _skip_interval(prev, diff) == (0, 0)


# ═══════════════════════════════════════════════════════════
# 8. props 浅引用全等快路径
# ═══════════════════════════════════════════════════════════

def test_props_identical_fast_path():
    from src.tui.ink.reconciler import _props_identical

    shared = [1, 2]
    assert _props_identical({"a": shared, "b": 1}, {"a": shared, "b": 1})
    assert not _props_identical({"a": [1, 2]}, {"a": [1, 2]})
    assert not _props_identical({"a": 1}, {"a": 1, "b": 2})
    assert not _props_identical({"a": 1}, {"b": 1})


def test_set_props_keeps_reference_when_values_identical():
    from src.tui.ink.fiber import Fiber, TAG_HOST
    from src.tui.ink.reconciler import Reconciler

    shared = [1, 2]
    f = Fiber(TAG_HOST, "text", {"a": shared})
    old = f.props
    Reconciler._set_props(f, {"a": shared})
    assert f.props is old  # 引用保持（测量缓存可命中）


# ═══════════════════════════════════════════════════════════
# 9. use_input 兼容包装缓存（对象键 + 不可哈希安全）
# ═══════════════════════════════════════════════════════════

def test_compat_handler_cache_keyed_by_handler_object():
    from src.tui.ink import _hooks_input as HI

    HI.clear_compat_handler_cache()
    try:
        def handler(event):
            return True

        wrapped = HI._make_compat_handler(handler)
        assert HI._make_compat_handler(handler) is wrapped
        assert next(iter(HI._compat_handler_cache)) is handler

        class Unhashable:
            __hash__ = None

            def __call__(self, event, key):
                return True

        obj = Unhashable()
        obj.__name__ = "unhashable_handler"
        # 不可哈希：不缓存但不得抛异常
        assert callable(HI._make_compat_handler(obj))
    finally:
        HI.clear_compat_handler_cache()


# ═══════════════════════════════════════════════════════════
# 10. AppModel 未声明字段告警
# ═══════════════════════════════════════════════════════════

def test_appmodel_unknown_public_field_warns(caplog):
    from src.tui.app.model import AppModel

    model = AppModel()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(AttributeError):
            model.no_such_field  # noqa: B018
    assert "no_such_field" in caplog.text
    # getattr 默认值语义不变
    assert getattr(model, "another_missing", "fallback") == "fallback"


# ═══════════════════════════════════════════════════════════
# 11. 渲染线程恒定 30Hz（帧率不可改变）
# ═══════════════════════════════════════════════════════════

def test_render_interval_is_fixed_30hz():
    from src.tui._config import RENDER_HZ, RENDER_INTERVAL_SEC, TuiConfig

    cfg = TuiConfig.defaults()
    assert RENDER_HZ == 30.0
    assert cfg.render_interval == pytest.approx(RENDER_INTERVAL_SEC)
    # 帧率不可改变：任何覆盖构造都被 __post_init__ 强制回真源值
    assert cfg.with_overrides(render_interval=0.5).render_interval == pytest.approx(
        RENDER_INTERVAL_SEC
    )
    assert TuiConfig(render_interval=0.1).render_interval == pytest.approx(
        RENDER_INTERVAL_SEC
    )
    # 空闲按需渲染开关已移除（帧率不可被配置改变）
    assert not hasattr(cfg, "idle_render")


def test_should_render_always_renders_each_tick():
    """渲染线程恒定 30Hz：每拍都渲染（空闲不跳过，节拍由渲染循环保证）。"""
    from src.tui.ink.session import InkSession
    from src.tui._config import TuiConfig

    ctx = H.HookContext()
    stub = object.__new__(InkSession)
    stub._config = TuiConfig.defaults()
    stub._bottom_redraw_requested = threading.Event()
    stub._dirty = False
    stub._model = None
    stub._hook_ctx = ctx

    H.push_context(ctx)
    try:
        assert stub._should_render() is True   # 空闲：仍渲染（恒定 30Hz）
        assert stub._should_render() is True   # 每拍都渲染
        # 重绘请求消费为脏标记后清空（不提前、不跳过）
        stub._bottom_redraw_requested.set()
        assert stub._should_render() is True
        assert not stub._bottom_redraw_requested.is_set()
    finally:
        H.pop_context()


# ═══════════════════════════════════════════════════════════
# 12. StaticLines 前缀缓存键用对象身份
# ═══════════════════════════════════════════════════════════

def test_staticlines_prefix_key_tracks_lines_object():
    from src.tui.ink.widgets import staticlines as SL
    from src.tui.ink.fiber import Fiber, TAG_HOST
    from src.tui.ink._layout_measure import LayoutBox

    f = Fiber(TAG_HOST, "static-lines", {})
    f.layout_box = LayoutBox(0, 0, 10, 1)

    lines1 = [Line.of("a")]
    f.props = {"lines": lines1}
    SL._paint(f, [None])
    key1 = f._committed_prefix[0]
    assert key1[0] is lines1

    # 换新列表（同内容/同长度）→ 键对象不同（不再依赖 id 复用风险）
    lines2 = [Line.of("a")]
    f.props = {"lines": lines2}
    SL._paint(f, [None])
    key2 = f._committed_prefix[0]
    assert key2[0] is lines2 and key2[0] is not lines1


# ═══════════════════════════════════════════════════════════
# 13. _measure TEXT 分支提取（行为不变）
# ═══════════════════════════════════════════════════════════

def test_measure_text_extracted_and_equivalent():
    from src.tui.ink._layout_measure import _measure, _measure_text
    from src.tui.ink.fiber import Fiber, TAG_HOST

    f = Fiber(TAG_HOST, "text", {"children": "hello"})
    box = _measure(f, 0, 0, 20, True)
    assert (box.w, box.h) == (20, 1)

    g = Fiber(TAG_HOST, "text", {"children": "hello"})
    box2 = _measure_text(g, 0, 0, 20, True, None)
    assert (box2.w, box2.h) == (20, 1)


def test_measure_empty_text_zero_height():
    from src.tui.ink._layout_measure import _measure
    from src.tui.ink.fiber import Fiber, TAG_HOST

    f = Fiber(TAG_HOST, "text", {"children": ""})
    box = _measure(f, 0, 0, 10, True)
    assert box.h == 0


# ═══════════════════════════════════════════════════════════
# 14. input router 同步点（消除时序窗口）+ 渲染互斥
# ═══════════════════════════════════════════════════════════

def test_flush_input_router_renders_synchronously_with_render_lock():
    """有 ``_render_lock`` 时 ``flush_input_router`` 由调用线程直接渲染一帧
    （无需「猜帧数」等待），返回 True。"""
    from src.tui.ink.session import InkSession

    stub = object.__new__(InkSession)
    stub._render_running = True
    stub._render_lock = threading.RLock()
    stub._input = None
    calls: list[int] = []
    stub._render_frame = lambda: calls.append(1)
    assert stub.flush_input_router(1.0) is True
    assert calls == [1]


def test_flush_input_router_falls_back_without_render_lock():
    """无 ``_render_lock``（stub/旧用法）走回退等待路径——不主动渲染。"""
    from src.tui.ink.session import InkSession

    stub = object.__new__(InkSession)
    stub._render_running = True
    stub._input = None
    stub._frame_seq = 0
    stub._frame_seq_lock = threading.Lock()
    stub._frame_flush_waiters = []
    stub._bottom_redraw_requested = threading.Event()
    stub._dirty = False
    stub._cmd_event = threading.Event()
    stub._frame_active = False
    rendered: list[int] = []
    stub._render_frame = lambda: rendered.append(1)
    result = stub.flush_input_router(0.05)
    assert result is False  # 等待超时（无渲染线程推进帧号）
    assert rendered == []


def test_render_frame_uses_render_lock_when_present():
    from src.tui.ink._session_frame_mixin import _SessionFrameMixin

    stub = object.__new__(type("S", (_SessionFrameMixin,), {}))
    stub._render_lock = threading.RLock()
    stub._frame_active = False
    inner: list[int] = []
    stub._render_frame_impl = lambda: inner.append(1)
    stub._render_frame()
    assert inner == [1]
    assert stub._frame_active is False
