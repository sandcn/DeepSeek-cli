"""``/pet`` 宠物浮窗：规格、姿态、内容渲染与透明行裁剪。"""

from __future__ import annotations

import pytest

from src.tui.app.pet import (
    PET_BUBBLE_PERIOD,
    PET_BUBBLE_VISIBLE,
    PET_CYCLE,
    PET_MAX_DX,
    PET_STAGE_HEIGHT,
    PET_STAGE_WIDTH,
    PetFloatingWindow,
    action_window,
    pet_pose_at,
    pet_window_spec,
)
from src.tui.ink import h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi


def _render(t: float, width: int = 40) -> str:
    return strip_ansi(
        renderToString(
            h(PetFloatingWindow, {
                "width": width,
                "started_at": 0.0,
                "now": t,
            }),
            {"columns": width},
        )
    )


# ── 规格：无边框 / 透明背景 / 内容自适应 ─────────────────


def test_pet_window_spec_is_borderless_transparent_and_autosized() -> None:
    spec = pet_window_spec(started_at=0.0)

    assert spec.id == "pet"
    assert spec.border is False, "用户要求窗口无框"
    assert spec.background is None, "透明背景：只显示行内容"
    assert spec.width is None, "内容自适应宽度（行宽大小不一）"
    assert spec.padding_x == 0
    assert spec.close_on_escape is False, "Esc 保留给中断/清空输入"
    assert spec.refresh_interval == pytest.approx(1.0 / 30.0)
    assert spec.idle_static is False
    assert callable(spec.render)


def test_pet_window_renders_with_expected_stage_size() -> None:
    spec = pet_window_spec(started_at=0.0)
    assert spec.bottom == "bottom_area"
    assert spec.x == "right"
    assert spec.priority > 100  # 与 /loop 同屏时宠物在上层


def test_pet_window_composites_into_app_frame() -> None:
    from src.tui.app.app import build_app_element
    from src.tui.app.model import AppModel

    model = AppModel()
    model.floating_windows = [pet_window_spec(started_at=0.0)]
    text = strip_ansi(
        renderToString(build_app_element(model, 70), {"columns": 70})
    )
    # 发饰与鱼身是每一帧都存在的稳定标记（眼睛/气泡会随动作/时间变化）。
    assert "✿" in text
    assert "█" in text
    # 透明背景：窗口空白列不写画布，底层欢迎卡内容仍可见。
    assert "运行环境" in text
    assert "◆ 主题" in text


# ── 姿态时间函数 ─────────────────────────────────────────


def _mid(name: str) -> float:
    start, duration = action_window(name)
    return start + duration * 0.5


def test_idle_blinks_and_bob_does_not_freeze() -> None:
    pose_a = pet_pose_at(0.05)
    pose_b = pet_pose_at(1.5)
    assert pose_a.action == "idle"
    assert pose_a.eyes == ("-", "-"), "idle 起始处眨眼"
    assert pose_b.eyes == ("◕", "◕")
    assert {pose_a.dy, pose_b.dy} == {0, -1}, "空闲时上下浮动"


def test_jump_goes_up_and_returns() -> None:
    start, duration = action_window("jump")
    takeoff = pet_pose_at(start + 0.01)
    airborne = pet_pose_at(start + duration * 0.5)
    landing = pet_pose_at(start + duration * 0.99)

    assert takeoff.dy == 0
    assert airborne.dy == -2
    assert airborne.eyes == ("^", "^")
    assert landing.dy == 0
    assert landing.effect in ("dust", "spark")


def test_wiggle_moves_left_and_right() -> None:
    start, duration = action_window("wiggle")
    dxs = {
        pet_pose_at(start + duration * f).dx
        for f in (0.05, 0.15, 0.25)
    }
    assert dxs == {1, -1}
    assert pet_pose_at(start + 0.1).effect == "wiggle"


def test_dash_uses_full_stage_width_and_flips() -> None:
    start, duration = action_window("dash")
    right = pet_pose_at(start + duration * 0.25)
    left = pet_pose_at(start + duration * 0.75)

    assert abs(right.dx) == PET_MAX_DX
    assert abs(left.dx) == PET_MAX_DX
    assert right.dx == -left.dx
    assert right.effect == "motion"


def test_spin_dizzy_and_flips() -> None:
    start, duration = action_window("spin")
    frames = [pet_pose_at(start + duration * f).flip for f in (0.05, 0.2, 0.35)]
    assert len(set(frames)) == 2, "转圈动作应左右翻转"
    pose = pet_pose_at(start + duration * 0.2)
    assert pose.eyes == ("@", "@")
    assert pose.effect == "spark"


def test_sing_wink_sleep_have_signature_poses() -> None:
    sing = pet_pose_at(_mid("sing"))
    assert sing.effect == "note"
    assert sing.mouth == "o"

    wink = pet_pose_at(_mid("wink"))
    assert wink.eyes[0] != wink.eyes[1]
    assert wink.effect == "heart"

    sleep = pet_pose_at(_mid("sleep"))
    assert sleep.eyes == ("∪", "∪")
    assert sleep.effect == "z"


def test_action_cycle_wraps() -> None:
    assert pet_pose_at(0.0).action == "idle"
    assert pet_pose_at(PET_CYCLE).action == "idle"
    assert pet_pose_at(PET_CYCLE - 0.01).action == "sleep"


def test_bubble_periodically_speaks() -> None:
    assert pet_pose_at(0.2).line, "开局立即说一句"
    assert pet_pose_at(PET_BUBBLE_PERIOD * 0.5).line == ""
    assert pet_pose_at(PET_BUBBLE_VISIBLE + 0.01).line == ""
    assert pet_pose_at(PET_BUBBLE_PERIOD + 0.2).line


# ── 内容渲染：行宽大小不一 / 只画内容 ─────────────────────


def test_bubble_is_inside_fixed_stage_height() -> None:
    """气泡叠在舞台顶部，不额外增加窗口高度（避免被视口顶部裁掉）。"""
    from src.tui.app.pet import _compose_grid

    pose = pet_pose_at(0.2)
    assert pose.line
    grid = _compose_grid(pose, 0.2)
    assert len(grid) == PET_STAGE_HEIGHT
    assert "╭" in "".join(cell[0] for cell in grid[0])


def test_render_contains_blue_fish_face_and_accessories() -> None:
    text = _render(0.2)
    assert "✿" in text, "娘化发饰"
    assert "◕" in text, "大眼"
    assert "ω" in text, "小嘴"
    assert "╭" in text and "╯" in text, "气泡"


def test_render_rows_have_different_widths() -> None:
    text = _render(0.2)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    widths = {len(line) for line in lines}
    assert len(widths) > 3, f"行宽应大小不一，实际: {sorted(widths)}"


def test_render_jump_moves_up_two_rows() -> None:
    from src.tui.app.pet import _compose_grid

    jump_t = _mid("jump")
    idle_grid = _compose_grid(pet_pose_at(0.2, bubble=False), 0.2)
    jump_grid = _compose_grid(pet_pose_at(jump_t, bubble=False), jump_t)

    def flower_row(grid) -> int:
        for index, row in enumerate(grid):
            if any(cell[0] == "✿" for cell in row):
                return index
        raise AssertionError("未找到发饰")

    assert flower_row(jump_grid) == flower_row(idle_grid) - 2


def test_render_uses_blue_body_and_pink_accessories() -> None:
    raw = renderToString(
        h(PetFloatingWindow, {
            "width": 40,
            "started_at": 0.0,
            "now": 0.2,
        }),
        {"columns": 40},
    )
    assert "\x1b[38;5;39m" in raw, "亮蓝鱼身"
    assert "\x1b[38;5;25m" in raw, "深蓝描边"
    assert "\x1b[38;5;211m" in raw, "粉色腮红/爱心"


def test_narrow_width_is_clamped() -> None:
    text = _render(0.2, width=10)
    for line in text.splitlines():
        assert len(line) <= 10
    assert text.strip(), "窄终端仍应显示内容"


def test_stage_dimensions_match_constants() -> None:
    # 内容组件内部使用固定舞台：窗口高度不会随小动作抖动。
    assert PET_STAGE_WIDTH == 30
    assert PET_STAGE_HEIGHT == 12


def test_runtime_type_alias_avoids_pep604_for_python39() -> None:
    """回归：模块级运行期别名不得用 ``X | None``（PEP 604 需 3.10+）。

    ``Cell`` 是运行期赋值（不是注解），Python 3.9 下 ``Style | None`` 会让
    模块导入直接抛 TypeError——``/pet`` 因此被误报「当前界面不支持宠物浮窗」。
    用 AST 静态检查，避免只在 3.10+ 环境跑测试时漏检。
    """
    import ast
    import inspect

    from src.tui.app import pet as pet_module

    tree = ast.parse(inspect.getsource(pet_module))
    cell_assigns = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "Cell" for t in node.targets)
    ]
    assert cell_assigns, "未找到 Cell 运行期别名"
    for node in cell_assigns:
        pep604 = [
            sub for sub in ast.walk(node.value)
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)
        ]
        assert not pep604, "运行期类型别名必须兼容 Python 3.9（用 Optional）"

