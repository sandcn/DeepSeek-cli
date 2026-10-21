"""回归测试：布局/绘制不变量、焦点路由、条件 hook 清理。

这些用例覆盖 2026-10-21 审查中确认的可复现缺陷：
  - 固定高度/收缩后的 Text 必须被裁剪到 layout box；
  - row flexBasis 必须重新测量内容；
  - row 百分比宽度以父内容宽度为基准；
  - margin 作为真实外边距；
  - Text height/minHeight/百分比；
  - focus 三态路由（未聚焦组件不得收到输入）；
  - 条件 hook 减少时清理残留 hook。
"""

from __future__ import annotations

from src.tui.ink import Box, Row, Text, h
from src.tui.ink._render_api import renderToString
from src.tui.ink.fiber import Fiber
from src.tui.ink.hooks import use_effect, use_input, useFocus
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.reconciler import Reconciler


def _layout(element, width: int = 40):
    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    reconciler.render(root, element, width, 24)
    return root


def _find_fiber(root: Fiber, type_name: str) -> Fiber | None:
    stack = [root]
    while stack:
        f = stack.pop()
        while f is not None:
            if f.is_function and getattr(f.type, "__name__", "") == type_name:
                return f
            if f.child is not None:
                stack.append(f.child)
            f = f.sibling
    return None


def _plain(element, width: int = 20) -> str:
    return strip_ansi(renderToString(element, {"columns": width}))


class _Event:
    kind = "char"
    char = "x"
    modifier = 0
    keycode = 0
    raw = None
    kitty_bits = -1
    event_type = ""


def test_fixed_height_text_is_clipped_to_box() -> None:
    plain = _plain(
        h(
            Box,
            {"width": 12, "height": 3, "border": 1},
            h(Text, {"flexShrink": 1}, "aa\nbb\ncc"),
        ),
        width=12,
    )
    assert "aa" in plain
    # 收缩后 Text box.h=1：bb/cc 不得覆盖父容器下边框。
    assert "bb" not in plain
    assert plain.count("└") == 1
    assert "└──────────┘" in plain


def test_flex_basis_reflows_text_and_does_not_overlap_sibling() -> None:
    plain = _plain(
        h(
            Box,
            {"width": 10, "flexDirection": "row", "height": 1},
            h(Text, {"flexBasis": 3}, "ABCDEFGHIJ"),
            h(Text, None, "X"),
        ),
        width=10,
    )
    assert plain == "ABCX"
    assert "EFGHIJ" not in plain


def test_row_percentage_uses_parent_content_width() -> None:
    root = _layout(
        h(
            Box,
            {"width": 40, "flexDirection": "row", "height": 1},
            h(Box, {"width": 20, "height": 1}),
            h(Box, {"width": "50%", "height": 1}),
        ),
        width=80,
    )
    row = root.child.child
    assert row is not None
    first_fn = row.child
    assert first_fn is not None
    first = first_fn.child
    assert first is not None
    second_fn = first_fn.sibling
    assert second_fn is not None
    second = second_fn.child
    assert second is not None
    assert first.layout_box.w == 20
    assert second.layout_box.x == 20
    assert second.layout_box.w == 20


def test_margin_is_applied_as_outer_spacing() -> None:
    root = _layout(
        h(
            Box,
            {"width": 20},
            h(Box, {"width": 5, "margin": 2}, h(Text, None, "a")),
            h(Text, None, "b"),
        ),
        width=20,
    )
    parent = root.child.child
    assert parent is not None
    boxed_fn = parent.child
    assert boxed_fn is not None
    boxed = boxed_fn.child
    assert boxed is not None
    assert boxed.layout_box.x == 2
    assert boxed.layout_box.y == 2
    following_fn = boxed_fn.sibling
    assert following_fn is not None
    following = following_fn.child
    assert following is not None
    assert following.layout_box.y == 5


def test_text_height_percentage_and_min_height() -> None:
    root = _layout(
        h(
            Box,
            {"width": 20, "height": 20},
            h(Text, {"height": "50%"}, "x"),
            h(Text, {"minHeight": 3}, "y"),
        ),
        width=20,
    )
    parent = root.child.child
    assert parent is not None
    first_fn = parent.child
    assert first_fn is not None
    first = first_fn.child
    assert first is not None
    assert first.layout_box.h == 10
    second_fn = first_fn.sibling
    assert second_fn is not None
    second = second_fn.child
    assert second is not None
    assert second.layout_box.h == 3


def test_focus_routes_only_to_focused_component() -> None:
    stores: list[str] = []

    def A(props):
        use_input(lambda event: stores.append("A") or True)
        useFocus({"isActive": True, "autoFocus": True})
        return h(Text, None, "A")

    def B(props):
        use_input(lambda event: stores.append("B") or True)
        return h(Text, None, "B")

    reconciler = Reconciler(None)
    routers = []
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()
    reconciler.render(root, h(Box, None, h(A), h(B)), 80, 24)
    router = routers[-1]
    assert router is not None
    assert router(_Event()) is True
    assert stores == ["A"]


def test_unfocused_component_does_not_receive_input() -> None:
    stores: list[str] = []

    def A(props):
        use_input(lambda event: stores.append("A") or True)
        useFocus({"isActive": True})
        return h(Text, None, "A")

    reconciler = Reconciler(None)
    routers = []
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()
    reconciler.render(root, h(Box, None, h(A)), 80, 24)
    router = routers[-1]
    assert router is not None
    # 焦点管理器存在但无聚焦组件：事件被吞掉，不落到未聚焦的 A，
    # 也不落到旧输入缓冲（Tab 可先切换焦点）。
    assert router(_Event()) is True
    assert stores == []


def test_conditional_hook_is_removed_when_branch_disappears() -> None:
    def Comp(props):
        if props.get("on"):
            use_effect(lambda: None, ())
        return h(Text, None, "x")

    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    reconciler.render(root, h(Comp, {"on": True}), 20, 24)
    fiber = _find_fiber(root, "Comp")
    assert fiber is not None
    assert len(fiber.hooks) == 1
    reconciler.render(root, h(Comp, {"on": False}), 20, 24)
    fiber = _find_fiber(root, "Comp")
    assert fiber is not None
    assert fiber.hooks == []


def test_backpressure_timeout_is_bounded() -> None:
    from src.tui.ink._session_queue_mixin import _PUT_NO_DROP_TIMEOUT

    # 30s 会阻塞事件循环/生产者；修复后必须是有界短等待。
    assert 0 < _PUT_NO_DROP_TIMEOUT <= 2.0


def test_stop_on_render_thread_does_not_self_join() -> None:
    import threading

    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    session._render_thread = threading.current_thread()
    session._render_running = True
    session._cmd_event = threading.Event()
    session.stop()
    assert session._render_running is False
    assert session._cmd_event.is_set()


def test_column_justify_space_between() -> None:
    plain = _plain(
        h(
            Box,
            {"width": 10, "height": 5, "justifyContent": "space-between"},
            h(Text, None, "a"),
            h(Text, None, "b"),
        ),
        width=10,
    )
    lines = plain.splitlines()
    assert lines[0] == "a"
    assert lines[-1] == "b"
    assert "a" not in "".join(lines[1:-1])


def test_use_stdout_write_is_redirected_while_rendering() -> None:
    import io
    from types import SimpleNamespace

    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    session._render_running = True
    err = io.StringIO()
    out = io.StringIO()
    session._stderr_stream = err
    session._ink_renderer = SimpleNamespace(stream=out)
    session._write_stdout_safe("hello")
    # TUI 运行期间外部写不得触碰渲染流（否则破坏光标/帧记账）。
    assert err.getvalue() == "hello"
    assert out.getvalue() == ""
