"""App 上下屏 Box 结构（2026-10-23 用户需求）。

约束（用户确认）：
  - 上屏（消息区：顶部标题栏 + 聊天历史 + 解析行）用 Box 包着；
  - 下屏（底部区：状态栏 + 输入区）同样用 Box 包着；
  - 上屏 Box 高度随内容自动扩张（内容多高就多高，不撑满屏幕）；
  - 两个 Box 的宽度都跟随终端宽度（``width="100%"``）；
  - 两个 Box 都无边框（``border=0``）；
  - 下屏 Box 保留 ``dataFloatAnchor="bottom_area"``（浮窗锚点依赖它）。

覆盖的连带风险：
  - 下屏 Box 仍紧随上屏 Box（``y == upper.y + upper.h``），浮窗锚点行不变；
  - ``InputArea`` 的 ``dataInputArea`` 标记未被外层 Box 改动（光标定位）；
  - 宽度变化（resize）后两个 Box 的 ``layout_box.w`` 同步跟随；
  - 内容增长时上屏 Box 增高、下屏 Box 高度不变且整体下移（内容驱动）。
"""

from __future__ import annotations

from src.tui.app.app import build_app_element
from src.tui.app.model import AppModel
from src.tui.ink import components
from src.tui.ink._layout_tree import layout_children
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.reconciler import Reconciler


def _render(model, width: int):
    """渲染 App 一帧（与生产路径同一 reconciler / layout / paint）。"""
    reconciler = Reconciler()
    root = Reconciler.create_root()
    reconciler.render(root, build_app_element(model, width), width, 0)
    frame = components.render_frame(root, width, viewport_height=0)
    return root, frame


def _split_boxes(root):
    """返回 (上屏 Box fiber, 下屏 Box fiber)。

    App 根 host 的两个正常流子节点即上下屏 Box（浮窗为 absolute 子节点，
    会被 ``layout_children`` 返回；本测试不打开浮窗，故恰好两个）。
    """
    app_host = layout_children(root)[0]
    assert app_host.type == "app"
    children = layout_children(app_host)
    assert len(children) == 2, f"期望上下屏两个 Box，实际 {len(children)} 个"
    return children[0], children[1]


def _committed(n: int) -> list:
    """构造 n 行已提交聊天历史（StaticLines 直接发射）。"""
    return [Line([StyledRun(f"line-{i}", None)]) for i in range(n)]


def test_upper_and_lower_are_borderless_full_width_boxes() -> None:
    """两个 Box：width=100%、border=0、宽度等于终端宽度，下屏紧随上屏。"""
    for width in (40, 80, 120):
        root, frame = _render(AppModel(), width)
        upper, lower = _split_boxes(root)

        for name, box in (("上屏", upper), ("下屏", lower)):
            assert box.props.get("width") == "100%", f"{name} Box 宽度应为 100%"
            assert int(box.props.get("border", 0) or 0) == 0, f"{name} Box 应无边框"
            assert box.props.get("flexDirection") == "column"
            assert box.layout_box.w == width, f"{name} Box 宽度应跟随终端宽度 {width}"
            assert len(frame.lines) >= box.layout_box.h

        assert int(upper.props.get("flexGrow", 0) or 0) == 1, "上屏 Box 应 flexGrow=1"
        assert upper.layout_box.y == 0
        assert lower.layout_box.y == upper.layout_box.y + upper.layout_box.h
        assert lower.props.get("dataFloatAnchor") == "bottom_area"


def test_split_boxes_paint_no_border(monkeypatch) -> None:
    """两个 Box 不进入边框绘制路径（border=0 真正生效，不是仅 props 声明）。"""
    painted = []
    original = components._paint_border

    def _spy(fiber, *args, **kwargs):
        painted.append(fiber)
        return original(fiber, *args, **kwargs)

    monkeypatch.setattr(components, "_paint_border", _spy)

    root, _frame = _render(AppModel(), 60)
    upper, lower = _split_boxes(root)

    assert upper not in painted, "上屏 Box 不应绘制边框"
    assert lower not in painted, "下屏 Box 不应绘制边框"


def test_upper_box_height_grows_with_content_bottom_box_follows() -> None:
    """上屏 Box 高度随内容自动扩张；下屏 Box 高度不变并整体下移。"""
    small_model = AppModel()
    small_model.committed_lines = _committed(20)
    big_model = AppModel()
    big_model.committed_lines = _committed(30)

    root_small, frame_small = _render(small_model, 80)
    root_big, frame_big = _render(big_model, 80)
    upper_small, lower_small = _split_boxes(root_small)
    upper_big, lower_big = _split_boxes(root_big)

    growth = upper_big.layout_box.h - upper_small.layout_box.h
    assert growth == 10, f"新增 10 行内容，上屏 Box 应增高 10 行（实际 {growth}）"
    assert lower_big.layout_box.h == lower_small.layout_box.h, "下屏 Box 高度不应变化"
    assert lower_big.layout_box.y == upper_big.layout_box.y + upper_big.layout_box.h
    assert len(frame_big.lines) == len(frame_small.lines) + 10


def test_split_boxes_follow_terminal_width_on_resize() -> None:
    """同一 reconciler 在终端宽度变化后，两个 Box 的宽度同步跟随。"""
    model = AppModel()
    reconciler = Reconciler()
    root = Reconciler.create_root()

    reconciler.render(root, build_app_element(model, 80), 80, 0)
    upper, lower = _split_boxes(root)
    assert (upper.layout_box.w, lower.layout_box.w) == (80, 80)

    reconciler.render(root, build_app_element(model, 40), 40, 0)
    upper, lower = _split_boxes(root)
    assert (upper.layout_box.w, lower.layout_box.w) == (40, 40)


def test_input_area_marker_stays_inside_bottom_box() -> None:
    """下屏 Box 包裹后，InputArea 的 dataInputArea 标记仍可被会话层找到。"""
    root, _frame = _render(AppModel(), 80)
    _upper, lower = _split_boxes(root)

    marked = []

    def _walk(fiber):
        if fiber is None:
            return
        # 标记会同时出现在函数组件 fiber（无 layout_box）与其产出的 host
        # Box 上——只收集 host fiber（布局盒真实存在，供光标定位使用）。
        if fiber.props.get("dataInputArea") and not fiber.is_function:
            marked.append(fiber)
        node = fiber.child
        while node is not None:
            _walk(node)
            node = node.sibling

    _walk(lower)
    assert marked, "下屏 Box 内应存在 dataInputArea 标记（光标定位依赖）"
    assert marked[0].layout_box is not None
    assert marked[0].layout_box.y >= lower.layout_box.y
