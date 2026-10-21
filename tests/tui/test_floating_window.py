"""通用浮动窗口 — 渲染/定位/叠加/还原回归测试（2026-10-21 用户需求）。

需求（用户原话要点）：
  - ``/loop`` 在底部框**最上方的分隔线最右边**显示浮动窗口，**向上展开**、
    带边框：显示总次数；提词下一行显示当前运行次数（每轮第 1 或 2 次）；
  - 浮动窗口是**通用**机制：任意坐标位置、可叠加多个窗口（按优先级）、
    窗口内可用任意 React Ink 组件；
  - 窗口关闭后原内容必须原样还原；被覆盖内容变化（向上运动）时窗口锚定
    输入区，取消覆盖后原样显示。

测试以真实 App 组件树渲染整帧（``Reconciler`` + ``render_frame``），并在
布局后调用生产路径的 ``apply_floating_layout``——与 ``InkSession`` 渲染帧
完全一致（不是简化桩）。
"""

from __future__ import annotations

import re

import pytest

from src.tui._width import wcswidth_simple
from src.tui.app.app import App
from src.tui.app.floating import (
    FloatingWindowSpec,
    LoopFloatingWindow,
    floating_layer_elements,
    loop_window_spec,
)
from src.tui.app.model import AppModel
from src.tui.ink import components as ink_components
from src.tui.ink import h, strip_ansi
from src.tui.ink._floating import apply_floating_layout
from src.tui.ink._paint_canvas import _line_as_dict
from src.tui.ink.output import Line
from src.tui.ink.reconciler import Reconciler

# ── 渲染辅助 ───────────────────────────────────────────

_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _plain_lines(frame) -> list[str]:
    """Frame → 纯文本行（去 ANSI）。"""
    return [strip_ansi(line.render()) for line in frame.lines]


def _norm(lines: list[str]) -> list[str]:
    """归一化时间戳（跨秒渲染导致的时间行差异，与浮窗无关）。"""
    return [_TIMESTAMP_RE.sub("<TS>", line) for line in lines]


def _cell(line, col: int) -> str:
    """取一行在**显示列** col 上的字符（CJK 安全；空位返回空串）。"""
    entry = _line_as_dict(line).get(col)
    return entry[0] if entry else ""


def _render(model, width: int = 60):
    """渲染真实 App 整帧（含浮窗布局修正），返回（纯文本行, frame, root）。"""
    rec = Reconciler()
    rec.hook_context.install_headless(width, 40)
    root = Reconciler.create_root()
    rec.render(root, h(App, {"model": model, "width": width}), width, 40)
    apply_floating_layout(root, width)
    frame = ink_components.render_frame(root, width)
    return _plain_lines(frame), frame, root


def _plain_component(component, props: dict, width: int = 60) -> list[str]:
    """渲染单个组件（内容组件级断言用）。"""
    from src.tui.ink import renderToString

    out = renderToString(h(component, props), {"columns": width})
    return [strip_ansi(line) for line in out.split("\n")]


@pytest.fixture(autouse=True)
def _isolate_workspace(monkeypatch):
    """屏蔽 workspace 段（分支/目录随运行环境变化）。"""
    import src.tui.app.status_bar as sb

    monkeypatch.setattr(sb, "_workspace_segment", lambda ctx: [])


def _model_with_loop(total: int = 3, round_no: int = 1, run: int = 1,
                     prompt: str = "完成所有任务", active: bool = True) -> AppModel:
    model = AppModel()
    model.loop_status.active = active
    model.loop_status.total = total
    model.loop_status.round = round_no
    model.loop_status.run = run
    model.loop_status.runs_per_round = 2
    model.loop_status.prompt = prompt
    model.floating_windows = [loop_window_spec()]
    return model


def _input_top(lines: list[str]) -> int:
    """输入区最上方分隔线（CPU/MEM 行）的文档行号。"""
    for i, line in enumerate(lines):
        if "CPU:" in line and "MEM:" in line:
            return i
    raise AssertionError("未找到输入区顶部分隔线（CPU/MEM 行）")


def _bottom_area_top(root) -> int:
    """底部框（状态栏 + 输入区 / 模态底部视图）顶部行号（生产锚点）。"""
    from src.tui.ink._floating import _bottom_area_top as _resolve

    y = _resolve(root)
    assert y >= 0, "未找到底部框锚点（dataFloatAnchor=bottom_area）"
    return y


def _window_box(frame, width: int, win_w: int) -> tuple[int, int]:
    """返回浮窗占据的行区间 [top, bottom]（按左边界显示列定位）。

    以最下方的 ``└`` 为底边，向上找同列连续的 ``│`` 直到 ``┌``——避免
    其它界面元素（工具卡/面板）恰好在同列出现竖线导致误判。
    """
    x = width - win_w
    bottoms = [
        i for i, line in enumerate(frame.lines)
        if _cell(line, x) == "\u2514"
    ]
    assert bottoms, "未找到浮窗底边框"
    bottom = max(bottoms)
    top = None
    for i in range(bottom - 1, -1, -1):
        cell = _cell(frame.lines[i], x)
        if cell == "\u250c":
            top = i
            break
        if cell != "\u2502":
            break
    assert top is not None, "未找到浮窗顶边框"
    return top, bottom


# ── 循环浮窗：位置 / 边框 / 内容 ─────────────────────────

def test_loop_window_anchors_above_bottom_area_right_aligned():
    """循环浮窗右对齐底部框上方、向上展开、带边框——绝不覆盖底部框。

    底部框 = 状态栏（2 行）+ 输入区（分隔线/输入行/时间戳/模式行）。窗口
    底行位于底部框顶部（状态栏分隔线）上一行；窗口所有行都在底部框之上。
    """
    width = 70
    win_w = 30
    model = _model_with_loop(total=3, round_no=1, run=1)
    lines, frame, root = _render(model, width)
    bottom_area = _bottom_area_top(root)
    top, bottom = _window_box(frame, width, win_w)

    # 向上展开：底边在底部框上方一行；窗口整体不进入底部框
    assert bottom == bottom_area - 1
    assert top == bottom - 4  # 3 行内容 + 上下边框
    assert bottom < bottom_area
    assert _cell(frame.lines[top], width - win_w) == "\u250c"
    assert _cell(frame.lines[top], width - 1) == "\u2510"
    assert _cell(frame.lines[bottom], width - win_w) == "\u2514"
    assert _cell(frame.lines[bottom], width - 1) == "\u2518"
    # 右对齐：右侧边框落在终端最后一列
    for row in range(top + 1, bottom):
        assert _cell(frame.lines[row], width - 1) == "\u2502"
    # 底部框首行（状态栏分隔线）不被窗口遮挡，输入区分隔线完整可见
    assert _cell(frame.lines[bottom_area], width - win_w) not in (
        "\u250c", "\u2502", "\u2514",
    )
    input_sep = _input_top(lines)
    assert "CPU:" in lines[input_sep] and "MEM:" in lines[input_sep]
    assert input_sep > bottom  # 窗口完全在底部框（含输入区）之上


def test_loop_window_shows_total_and_run_counts():
    """窗口显示总次数、提词、当前运行次数（1 或 2 / 每轮 2 次）。"""
    width = 70
    model = _model_with_loop(total=5, round_no=2, run=1, prompt="继续完成所有")
    lines, _, _ = _render(model, width)
    text = "\n".join(lines)

    assert "总次数: 5" in text
    assert "第 2/5 轮" in text
    assert "继续完成所有" in text
    assert "当前运行次数: 1/2" in text

    # 第 2 次运行
    model.loop_status.run = 2
    lines2, _, _ = _render(model, width)
    assert "当前运行次数: 2/2" in "\n".join(lines2)


def test_loop_window_content_hidden_when_inactive():
    """循环状态非 active 时内容组件返回空（窗口正常情况下已关闭）。"""
    model = _model_with_loop(active=False)
    out = _plain_component(LoopFloatingWindow, {
        "model": model, "width": 24, "window": loop_window_spec(),
    })
    assert all(not line.strip() for line in out)


# ── 通用机制：不占高度 / 关闭还原 / 覆盖内容变化 ─────────

def test_floating_window_does_not_change_document_height():
    """浮窗是绝对定位层——文档行数与无浮窗时一致。"""
    base = AppModel()
    lines_base, _, _ = _render(base, 70)
    with_win = _model_with_loop()
    lines_win, _, _ = _render(with_win, 70)
    assert len(lines_win) == len(lines_base)


def test_closing_window_restores_covered_content_exactly():
    """关闭浮窗后下一帧与「从未打开」的帧逐行完全一致（原样还原）。"""
    width = 70
    model = AppModel()
    before, _, _ = _render(model, width)

    model.floating_windows = [loop_window_spec()]
    model.loop_status.active = True
    model.loop_status.total = 2
    opened, _, _ = _render(model, width)
    assert opened != before  # 窗口确实覆盖了内容

    model.floating_windows = []
    after, _, _ = _render(model, width)
    assert _norm(after) == _norm(before)


def test_window_restores_when_covered_content_moves():
    """被覆盖内容变化（聊天历史上移/增长）时：窗口仍锚定输入区，关闭后恢复新内容。"""
    width = 70
    model = _model_with_loop()
    # 预置 3 行已提交聊天内容（避免空状态的欢迎卡在设置 committed_lines
    # 后消失，干扰位移断言）
    model.committed_lines = [Line.of(f"历史内容 {i}") for i in range(6)]
    lines_before, frame_before, root_before = _render(model, width)
    sep_before = _bottom_area_top(root_before)
    top_before, bottom_before = _window_box(frame_before, width, 30)
    assert bottom_before == sep_before - 1

    # 再增长 3 行已提交聊天内容（文档增长，底部框随之下移）
    model.committed_lines = [Line.of(f"历史内容 {i}") for i in range(9)]
    lines_after, frame_after, root_after = _render(model, width)
    sep_after = _bottom_area_top(root_after)
    top_after, bottom_after = _window_box(frame_after, width, 30)
    assert sep_after == sep_before + 3
    assert bottom_after == sep_after - 1
    assert top_after == top_before + 3

    # 关闭窗口 → 被覆盖区域恢复为**最新**内容（含新增历史行）
    model.floating_windows = []
    restored, _, _ = _render(model, width)
    assert "历史内容 0" in "\n".join(restored)
    assert "\u250c" not in "\n".join(restored)


# ── 通用机制：任意坐标 / 叠加优先级 / 行宽 ──────────────

def test_window_supports_absolute_coordinates():
    """任意坐标：x=2、top=1 时窗口左上角落在 (2, 1)。"""
    model = AppModel()
    model.floating_windows = [FloatingWindowSpec(
        id="abs",
        render=lambda props: h("text", {"children": "ABS"}),
        width=12,
        x=2,
        top=1,
        bottom=None,
        border=True,
    )]
    lines, _, _ = _render(model, 70)
    assert lines[1][2:3] == "\u250c"
    assert "ABS" in lines[2]
    assert lines[0][0] != "\u250c"  # top=1 之前不绘制窗口


def test_windows_stack_by_priority_without_leaking_lower_layer():
    """同位置叠加：高优先级（数值大，后绘制）完全覆盖低优先级内容。"""
    def content(label):
        return lambda props: h("text", {"children": label})

    model = AppModel()
    model.floating_windows = [
        FloatingWindowSpec(id="low", render=content("LOWLAYER"), width=20,
                           x=0, top=2, bottom=None),
        FloatingWindowSpec(id="high", render=content("HILAYER"), width=20,
                           x=0, top=2, bottom=None, priority=50),
    ]
    lines, _, _ = _render(model, 70)
    text = "\n".join(lines)
    assert "HILAYER" in text
    assert "LOWLAYER" not in text


def test_window_rows_never_exceed_terminal_width():
    """任意宽度下窗口行不破坏行宽不变量（极窄屏也钳制宽度）。"""
    for width in (12, 20, 30, 70):
        model = _model_with_loop()
        _, frame, _ = _render(model, width)
        for line in frame.lines:
            assert line.width <= width, (width, line.width, line.render())


def test_loop_window_width_clamped_on_narrow_terminal():
    """终端窄于窗口设计宽度时钳制到终端宽度（不溢出、边框闭合）。"""
    width = 18
    model = _model_with_loop()
    lines, _, _ = _render(model, width)
    assert any("\u250c" in line and "\u2510" in line for line in lines)
    for line in lines:
        assert wcswidth_simple(line) <= width


def test_floating_layer_sorted_by_priority():
    """App 浮窗层按 priority 升序拼接（高优先级后在树中 → 绘制在上）。"""
    model = AppModel()
    model.floating_windows = [
        FloatingWindowSpec(id="a", priority=5),
        FloatingWindowSpec(id="b", priority=-1),
        FloatingWindowSpec(id="c", priority=10),
    ]
    els = floating_layer_elements(model, 60)
    ids = [el.props["window"].id for el in els]
    assert ids == ["b", "a", "c"]


def test_window_row_keeps_content_outside_window_columns():
    """浮窗与底层内容同行时：窗口列范围外（左/右）原内容必须保留。

    用户反馈回归（2026-10-21）：「跟浮动窗口同行的要显示另一半的行，现在
    只显示了浮动窗口的内容」——整行合并时浮窗左侧的补位空格覆盖了底层
    内容。修复后仅合并窗口 box 的 [x, x+w) 列片段。
    """
    model = AppModel()
    model.committed_lines = [Line.of("A" * 60) for _ in range(5)]
    model.floating_windows = [FloatingWindowSpec(
        id="win",
        render=lambda props: h("text", {"children": "WIN"}),
        width=20,
        x=10,
        top=2,
        bottom=None,
        border=True,
    )]
    lines, frame, _ = _render(model, 70)
    # 窗口覆盖行（top=2 为顶边框）：左侧 10 列与右侧 30 列之后保留原内容
    assert lines[2][:10] == "A" * 10
    assert _cell(frame.lines[2], 10) == "\u250c"
    assert lines[2][30:40] == "A" * 10
    # 内容行同理（窗口内部为 WIN，外部保留 A）
    assert lines[3][:10] == "A" * 10
    assert "WIN" in lines[3]
    assert lines[3][30:40] == "A" * 10


def test_loop_window_never_covers_modal_bottom_view():
    """模态底部视图（user_select 等）同样属于底部框：浮窗显示在其上方。

    实操回归（2026-10-21）：user_select 替换底部区后，浮窗若按输入区分隔线
    锚定会与弹窗重叠错位。修复：循环浮窗锚定**整个底部框**
    （``bottom_area``），底部视图打开时仍在其上方显示且不遮挡。
    """
    from src.core.user_select_state import UserSelectState

    model = _model_with_loop()
    model.bottom_view = "user_select"
    st = UserSelectState(
        visible=True, seq=1, title="请选择",
        options=["选项 A", "选项 B"], multi_select=False,
    )
    model.user_selects = [st]
    model.user_select = st
    lines_modal, frame_modal, root_modal = _render(model, 70)
    assert "CPU:" not in "\n".join(lines_modal)  # 输入区已被底部视图替换
    bottom_area = _bottom_area_top(root_modal)
    # 窗口底边框位于底部框上方一行（模态弹窗不被遮挡）
    x = 70 - 30
    bottoms = [
        i for i, line in enumerate(frame_modal.lines)
        if _cell(line, x) == "\u2514"
    ]
    assert bottoms and max(bottoms) == bottom_area - 1
    assert "总次数: 3" in "\n".join(lines_modal)
    assert _cell(frame_modal.lines[bottom_area], x) not in (
        "\u250c", "\u2502", "\u2514",
    )

    model.bottom_view = ""
    lines_normal, _, _ = _render(model, 70)
    assert "总次数: 3" in "\n".join(lines_normal)  # 输入区恢复 → 浮窗仍在原位


def test_spec_copy_with_returns_updated_copy():
    """窗口规格不可变更新（update API 依赖 copy_with）。"""
    spec = FloatingWindowSpec(id="w", priority=1, width=20, x="left")
    updated = spec.copy_with(priority=9, x=3)
    assert updated.id == "w" and updated.priority == 9 and updated.x == 3
    assert spec.priority == 1 and spec.x == "left"
    # 未知字段忽略（不污染对象）
    assert updated.copy_with(unknown_field=1).priority == 9


# ── 2026-10-21 缺陷修复回归 ───────────────────────────────
#
# 覆盖「浮窗定位只在 InkSession 路径生效」「透明背景实际不透明」「行宽不变量
# 缺口」「空/重复 id」「可视区钳制」「空闲帧复用预测漏浮窗」「两套 /loop
# 实现重复」等本轮修复项。


def _render_twice(model, width: int = 70):
    """同一 fiber 树连续渲染两帧（第二帧命中 committed 前缀复用 → 走合成层）。"""
    rec = Reconciler()
    rec.hook_context.install_headless(width, 40)
    root = Reconciler.create_root()
    element = h(App, {"model": model, "width": width})
    rec.render(root, element, width, 40)
    apply_floating_layout(root, width)
    ink_components.render_frame(root, width)  # 首帧：建立 staticlines 前缀缓存
    rec.render(root, element, width, 40)
    apply_floating_layout(root, width)
    frame = ink_components.render_frame(root, width)
    return _plain_lines(frame), frame


def test_render_to_string_applies_float_layout():
    """公共渲染 API（renderToString）同样执行浮窗定位——不再粘在文档左上角。"""
    from src.tui.ink import renderToString

    model = _model_with_loop(total=3)
    out = renderToString(h(App, {"model": model, "width": 70}), {"columns": 70})
    # renderToString 返回 ANSI 文本——断言前先去样式序列（与 _plain_component 一致）
    lines = [strip_ansi(line) for line in out.split("\n")]
    assert "总次数: 3" in out
    # 文档第 0 行是顶部标题栏：浮窗必须锚定底部框上方，绝不压在上面
    assert "\u250c" not in lines[0]
    # 右对齐生效（窗口右上角落在终端最后一列）
    assert any(line.endswith("\u2510") for line in lines)


def test_transparent_window_keeps_underlying_content():
    """``background=None``（透明）窗口只覆盖实际字符列，空白列透出底层内容。"""
    model = AppModel()
    model.committed_lines = [Line.of("A" * 60) for _ in range(6)]
    model.floating_windows = [FloatingWindowSpec(
        id="ghost",
        render=lambda props: h("text", {"children": "WIN"}),
        width=20,
        x=0,
        top=2,
        bottom=None,
        border=True,
        background=None,
    )]
    lines, _ = _render_twice(model, 70)
    assert lines[3][0] == "\u2502"       # 左/右边框仍绘制
    assert "WIN" in lines[3]
    # 内容右侧的空白列（含右内边距）不覆盖底层 —— 修复前被清成空格
    assert lines[3][5:19] == "A" * 14
    assert lines[3][19] == "\u2502"

    # 对照：默认不透明背景（black）覆盖整段矩形
    model.floating_windows = [FloatingWindowSpec(
        id="solid",
        render=lambda props: h("text", {"children": "WIN"}),
        width=20,
        x=0,
        top=2,
        bottom=None,
        border=True,
    )]
    lines_solid, _ = _render_twice(model, 70)
    assert "WIN" in lines_solid[3]
    assert lines_solid[3][5:19] == " " * 14


def test_merge_window_row_truncates_to_terminal_width():
    """窗口贴右边界 + 宽字符跨越终端边界时，合并结果仍不超过终端宽度。"""
    from src.tui.ink.components import _merge_window_row

    base = Line.of("A" * 5)
    merged = _merge_window_row(
        base, {0: ("\u4e2d", None, None)}, 4, 2, 5, False,
    )
    assert merged is not None
    assert merged.width <= 5


def test_floating_box_width_never_exceeds_terminal():
    """极窄终端（1/2/3 列）下浮窗宽度不放大于终端宽度。"""
    from src.tui.app.floating import FloatingWindow

    spec = loop_window_spec()
    for term in (1, 2, 3, 5):
        el = FloatingWindow({"window": spec, "model": AppModel(), "width": term})
        assert el.props["width"] <= term


def test_empty_and_duplicate_window_ids_are_dropped():
    """空 id / 重复 id 不生成浮窗节点（重复 key + 左上角残留窗口的来源）。"""
    model = AppModel()
    model.floating_windows = [
        FloatingWindowSpec(id=""),
        FloatingWindowSpec(id="   "),
        FloatingWindowSpec(id="dup", width=10, x=0, top=1, bottom=None),
        FloatingWindowSpec(id="dup", width=12, x=0, top=1, bottom=None),
    ]
    els = floating_layer_elements(model, 60)
    assert [el.props["window"].id for el in els] == ["dup"]
    assert els[0].props["window"].width == 10  # 保留先出现的规格


def test_window_clamped_into_viewport():
    """绝对坐标浮窗落在可视区上方时被钳进可视区（否则终端永不显示）。"""
    width = 70
    model = AppModel()
    model.committed_lines = [Line.of(f"hist {i}") for i in range(40)]
    model.floating_windows = [FloatingWindowSpec(
        id="fixed",
        render=lambda props: h("text", {"children": "FIXED"}),
        width=12,
        x=0,
        top=0,
        bottom=None,
    )]
    rec = Reconciler()
    rec.hook_context.install_headless(width, 40)
    root = Reconciler.create_root()
    rec.render(root, h(App, {"model": model, "width": width}), width, 40)
    frame = ink_components.render_frame(root, width, viewport_height=10)
    lines = _plain_lines(frame)
    assert "FIXED" in "\n".join(lines)
    top = next(i for i, line in enumerate(lines) if "\u250c" in line)
    assert top >= len(lines) - 10, (top, len(lines))


def test_invalid_anchor_values_fall_back_quietly():
    """非法锚点/坐标不再静默落到"看着像 bug"的位置（解析失败返回 None + 记 debug）。"""
    from src.tui.ink._floating import _resolve_row, _resolve_x

    assert _resolve_row(
        "bogus", doc_height=10, input_top=6, input_bottom=9,
    ) is None
    assert _resolve_row(3.5, doc_height=10, input_top=6, input_bottom=9) is None
    assert _resolve_x("bogus", width=80, win_w=10) == 70


def test_zero_size_window_is_hidden_not_stuck_at_origin():
    """无法解析出矩形（零尺寸布局盒）的浮窗被隐藏，而不是停在左上角。"""
    from src.tui.ink._floating import apply_floating_layout as _apply

    model = AppModel()
    model.floating_windows = [FloatingWindowSpec(
        id="zero",
        render=lambda props: h("text", {"children": "Z"}),
        width=10, x=0, top=1, bottom=None,
    )]
    rec = Reconciler()
    rec.hook_context.install_headless(70, 40)
    root = Reconciler.create_root()
    rec.render(root, h(App, {"model": model, "width": 70}), 70, 40)
    _apply(root, 70)
    # 人为把窗口盒置零（模拟布局盒缺失/零尺寸），再定位一次
    from src.tui.ink._floating import _collect_float_fibers

    fiber = _collect_float_fibers(root, include_hidden=True)[0]
    fiber.layout_box.w = 0
    fiber.layout_box.h = 0
    _apply(root, 70)
    assert fiber._float_hidden is True
    assert "Z" not in "\n".join(_plain_lines(ink_components.render_frame(root, 70)))


def test_idle_frame_predicate_disabled_by_floating_window():
    """有浮窗时禁止空闲帧复用（避免浮窗内容冻结在旧帧）。"""
    from src.tui._assembly_steps import _make_idle_frame_predicate

    model = AppModel()
    assert _make_idle_frame_predicate(model, None)() is True
    model.floating_windows = [loop_window_spec()]
    assert _make_idle_frame_predicate(model, None)() is False


def test_loop_window_helpers_share_one_entry_point():
    """两套 /loop 实现共用的浮窗控制入口（静默降级 + 三参兼容）。"""
    from src.app_loop._float_window import (
        close_loop_window,
        open_loop_window,
        set_loop_window_status,
    )

    class _Stub:
        def __init__(self):
            self.calls = []

        def open_loop_floating_window(self, total, prompt):
            self.calls.append(("open", total, prompt))

        def set_loop_status(self, **fields):
            self.calls.append(("status", dict(fields)))

        def close_loop_floating_window(self):
            self.calls.append(("close",))

    stub = _Stub()
    open_loop_window(stub, 3, "继续完成所有")
    set_loop_window_status(stub, round=1, run=2)
    close_loop_window(stub)
    assert stub.calls == [
        ("open", 3, "继续完成所有"),
        ("status", {"round": 1, "run": 2}),
        ("close",),
    ]

    # 无方法 / 方法异常 / 旧两参签名收到 runs_per_round 时静默降级（绝不抛）
    open_loop_window(None, 1, "x")

    class _Broken:
        def open_loop_floating_window(self, *args, **kwargs):
            raise RuntimeError("boom")

    open_loop_window(_Broken(), 1, "x")
    open_loop_window(stub, 1, "x", runs_per_round=3)  # _Stub 只有两参 → 忽略
    assert len(stub.calls) == 3
