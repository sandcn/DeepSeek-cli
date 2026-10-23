"""``/pet`` 宠物浮窗：规格、姿态、动作、台词、内容渲染与透明行裁剪。"""

from __future__ import annotations

import time

import pytest

from src.tui._width import wcswidth_simple
from src.tui.app.pet import (
    PET_ACTIONS,
    PET_BUBBLE_PERIOD,
    PET_BUBBLE_VISIBLE,
    PET_CYCLE,
    PET_LINES,
    PET_MAX_DX,
    PET_SPRITE_HEIGHT,
    PET_SPRITE_WIDTH,
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
    model.floating_windows = [pet_window_spec(started_at=time.monotonic())]
    text = strip_ansi(
        renderToString(build_app_element(model, 70), {"columns": 70})
    )
    # 启动欢迎页空间有限时可能裁掉顶部发饰，但鱼身必须可见。
    assert "█" in text
    # 透明背景：窗口空白列不写画布，底层欢迎卡内容仍可见。
    assert "运行环境" in text
    assert "◆ 主题" in text


def test_pet_layer_is_above_loop_window() -> None:
    """/pet 与 /loop 同屏时宠物在上层（循环浮窗由 consumer 让到左侧）。"""
    from src.tui.app.app import build_app_element
    from src.tui.app.floating import loop_window_spec
    from src.tui.app.model import AppModel

    model = AppModel()
    model.floating_windows = [
        loop_window_spec(),
        pet_window_spec(started_at=time.monotonic()),
    ]
    text = strip_ansi(
        renderToString(build_app_element(model, 120), {"columns": 120})
    )
    assert "█" in text, "宠物应绘制在浮窗层"
    assert pet_window_spec().priority > loop_window_spec().priority


# ── 动作目录与姿态时间函数 ───────────────────────────────


def _mid(name: str) -> float:
    start, duration = action_window(name)
    return start + duration * 0.5


def test_action_catalog_has_feeding_actions() -> None:
    names = [name for name, _duration in PET_ACTIONS]
    assert len(names) == 22
    assert len(set(names)) == len(names)
    for action in (
        "idle", "eat", "jump", "chew", "wiggle", "dash", "burp",
        "spin", "sing", "chase", "wink", "food_coma", "dance", "sleep",
        "stroll", "sniff_tok", "tok_feast", "happy_run",
        "peek", "stretch", "beg", "nod",
    ):
        assert action in names
    assert PET_CYCLE == pytest.approx(sum(duration for _name, duration in PET_ACTIONS))


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
    assert airborne.dy == -3
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


def test_chase_runs_for_rice_and_mirrors() -> None:
    start, duration = action_window("chase")
    right = pet_pose_at(start + duration * 0.25)
    left = pet_pose_at(start + duration * 0.75)
    assert abs(right.dx) >= PET_MAX_DX
    assert right.dx == -left.dx
    assert right.flip != left.flip
    assert right.effect == "crumb_motion"


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


def test_eating_poses_are_distinct() -> None:
    eat = pet_pose_at(_mid("eat"))
    chew = pet_pose_at(_mid("chew"))
    burp = pet_pose_at(_mid("burp"))
    coma = pet_pose_at(_mid("food_coma"))
    dance = pet_pose_at(_mid("dance"))

    assert eat.effect == "steam_rice"
    assert eat.mouth in ("▽", "ω")
    assert chew.effect == "crumb"
    assert burp.effect == "burp"
    assert coma.effect == "full"
    assert coma.dy > 0, "吃撑应躺平下沉"
    assert dance.effect == "dance"


def test_tok_actions_render_tok_snack() -> None:
    from src.tui.app.pet import _compose_grid

    sniff = pet_pose_at(_mid("sniff_tok"))
    feast = pet_pose_at(_mid("tok_feast"))
    assert sniff.effect == "sniff_tok"
    assert feast.effect == "tok_feast"
    assert "tok" in "".join(ch for row in _compose_grid(feast, _mid("tok_feast"), width=100) for ch, _style in row)
    assert "tok" in "".join(ch for row in _compose_grid(sniff, _mid("sniff_tok"), width=100) for ch, _style in row)


def test_pet_stays_in_40_column_window() -> None:
    """删除随机游走后，宠物始终限制在右侧 40 列浮窗内。"""
    text = _render(0.2, width=130)
    for line in text.splitlines():
        assert wcswidth_simple(line) <= PET_STAGE_WIDTH


def test_action_cycle_wraps() -> None:
    assert pet_pose_at(0.0).action == "idle"
    assert pet_pose_at(PET_CYCLE).action == "idle"
    assert pet_pose_at(PET_CYCLE - 0.01).action == "sleep"


def test_bubble_periodically_speaks() -> None:
    assert pet_pose_at(0.2).line, "开局立即说一句"
    assert pet_pose_at(PET_BUBBLE_PERIOD * 0.5).line == ""
    assert pet_pose_at(PET_BUBBLE_VISIBLE + 0.01).line == ""
    assert pet_pose_at(PET_BUBBLE_PERIOD + 0.2).line


def test_dialogue_pool_is_large_and_rice_themed() -> None:
    assert len(PET_LINES) >= 36
    assert any("饭" in line for line in PET_LINES)
    assert any("白饭" in line for line in PET_LINES)
    assert any("干饭" in line for line in PET_LINES)
    assert PET_BUBBLE_PERIOD <= 7.0
    assert PET_BUBBLE_VISIBLE >= 3.0


def test_every_action_has_specific_dialogue() -> None:
    from src.tui.app import pet as pet_module

    names = {name for name, _duration in PET_ACTIONS}
    assert set(pet_module._ACTION_LINES) == names
    for lines in pet_module._ACTION_LINES.values():
        assert len(lines) >= 3
        for line in lines:
            assert line
            assert wcswidth_simple(line) <= PET_STAGE_WIDTH - 4


# ── 内容渲染：行宽大小不一 / 只画内容 / 干饭道具 ──────────


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


def test_render_contains_rice_bowl_and_steam() -> None:
    eat = _render(_mid("eat"))
    assert "╭" in eat and "╰" in eat, "饭碗"
    assert "▁" in eat and "▒" in eat, "白饭"
    assert "≈" in eat, "热气"
    assert "▽" in eat or "ω" in eat, "干饭嘴型"

    coma = _render(_mid("food_coma"))
    assert "░" in coma, "空碗"
    assert "z" in coma or "Z" in coma, "吃撑睡觉"


def test_render_rows_have_different_widths() -> None:
    text = _render(0.2)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    widths = {len(line) for line in lines}
    assert len(widths) > 3, f"行宽应大小不一，实际: {sorted(widths)}"


def test_all_actions_fit_canvas_width() -> None:
    for name, duration in PET_ACTIONS:
        start, _duration = action_window(name)
        for fraction in (0.05, 0.3, 0.5, 0.75, 0.95):
            text = _render(start + duration * fraction)
            for line in text.splitlines():
                assert wcswidth_simple(line) <= PET_STAGE_WIDTH, (
                    f"{name} 行宽超出舞台: {line!r}"
                )


def test_render_jump_moves_up_three_rows() -> None:
    from src.tui.app.pet import _compose_grid

    jump_t = _mid("jump")
    idle_grid = _compose_grid(pet_pose_at(0.2, bubble=False), 0.2)
    jump_grid = _compose_grid(pet_pose_at(jump_t, bubble=False), jump_t)

    def flower_row(grid) -> int:
        for index, row in enumerate(grid):
            if any(cell[0] == "✿" for cell in row):
                return index
        raise AssertionError("未找到发饰")

    assert flower_row(jump_grid) == flower_row(idle_grid) - 3


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
    assert "\x1b[38;5;45m" in raw, "亮青蓝描边"
    assert "\x1b[38;5;211m" in raw, "粉色腮红/爱心"
    assert "\x1b[38;5;231m" in raw, "白饭/大眼"
    assert "\x1b[48;5;39m" in raw, "身体底色（避免半填充字形露黑块）"


def test_narrow_width_is_clamped() -> None:
    text = _render(0.2, width=10)
    for line in text.splitlines():
        assert wcswidth_simple(line) <= 10
    assert text.strip(), "窄终端仍应显示内容"


def test_stage_dimensions_match_constants() -> None:
    # 内容组件内部使用固定舞台：窗口高度不会随小动作抖动。
    assert PET_STAGE_WIDTH == 40
    assert PET_STAGE_HEIGHT == 16
    assert PET_SPRITE_WIDTH == 28
    assert PET_SPRITE_HEIGHT == 11
    assert PET_MAX_DX == 6


def test_visible_height_crops_bottom_rows_without_bubble() -> None:
    """启动欢迎页空间不足时保留身体/表情/饭碗，并隐藏会被裁掉的气泡。"""
    text = strip_ansi(
        renderToString(
            h(PetFloatingWindow, {
                "width": 40,
                "started_at": 0.0,
                "now": 2.5,
                "geometry": {"visible": True, "height": 10},
            }),
            {"columns": 40},
        )
    )
    assert len(text.splitlines()) <= 10
    assert "◕" in text, "表情必须保留"
    assert "╭" in text, "饭碗必须保留"
    assert "啊呜" not in text, "气泡不应只露半截"


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


def test_pet_and_loop_windows_take_separate_sides() -> None:
    """宠物与 /loop 同开时循环浮窗让到左侧，关闭宠物后恢复右侧。"""
    from types import SimpleNamespace

    from src.tui._consumer import ChatUIConsumer

    class _Bridge:
        def __init__(self, model):
            self.model = model

        def open_floating_window(self, window) -> bool:
            wid = str(getattr(window, "id", ""))
            self.model.floating_windows = [
                w for w in self.model.floating_windows
                if str(getattr(w, "id", "")) != wid
            ] + [window]
            return True

        def update_floating_window(self, window_id: str, **changes) -> bool:
            for index, window in enumerate(self.model.floating_windows):
                if str(getattr(window, "id", "")) == window_id:
                    self.model.floating_windows[index] = window.copy_with(**changes)
                    return True
            return False

        def close_floating_window(self, window_id: str) -> bool:
            kept = [
                w for w in self.model.floating_windows
                if str(getattr(w, "id", "")) != window_id
            ]
            if len(kept) == len(self.model.floating_windows):
                return False
            self.model.floating_windows = kept
            return True

    model = SimpleNamespace(floating_windows=[])
    ui = ChatUIConsumer.__new__(ChatUIConsumer)
    ui._rs = model
    ui._bb = _Bridge(model)

    ui.open_loop_floating_window(1, "干饭")
    assert _window(model, "loop").x == "right"

    assert ui.open_pet_floating_window() is True
    assert _window(model, "loop").x == "left"
    assert _window(model, "pet").x == "right"

    assert ui.close_pet_floating_window() is True
    assert _window(model, "loop").x == "right"

    # 先开宠物、再开 /loop：loop 直接让到左侧。
    assert ui.open_pet_floating_window() is True
    ui.close_loop_floating_window()
    ui.open_loop_floating_window(2, "再干一碗")
    assert _window(model, "loop").x == "left"
    assert _window(model, "pet").x == "right"


def _window(model, window_id: str):
    for window in model.floating_windows:
        if str(getattr(window, "id", "")) == window_id:
            return window
    raise AssertionError(f"未找到浮窗: {window_id}")


# ── 透视背景 / 动画闪烁（2026-10-23 用户报障） ─────────────

def test_pet_transparent_overlay_and_animation_does_not_clear_rows() -> None:
    """完整渲染链路：宠物透明区透出底层文本；动画帧不对宠物行写 EL（防闪）。

    用户报障：「pet 没有透视背景，并且渲染会闪」。

      - 透明背景：``/pet`` 的 ``background=None``，窗口空隙必须保留底层内容；
        修复前 ``_merge_line`` 为 marginLeft 补的无样式空格被当成窗口单元格，
        覆盖底层文本形成「挖空矩形带」；
      - 闪烁：宠物行宽不足时渲染器先写 ``\\033[K`` 清整行再重写，30fps 动画
        期间整行（含宠物）反复消失再出现。修复后浮窗矩形内整行补足终端宽度，
        直接覆盖、无 EL。
    """
    import io
    import re

    pyte = pytest.importorskip("pyte")
    from src.tui._config import TuiConfig
    from src.tui._const import DisplayMsgsCmd
    from src.tui._screen import TerminalWidthCache
    from src.tui.app.app import build_app_element
    from src.tui.app.apply import apply_cmd
    from src.tui.app.model import AppModel
    from src.tui.ink import components
    from src.tui.ink.session import InkSession

    width, height = 80, 24
    clock = {"t": 0.0}
    spec = pet_window_spec(started_at=0.0)

    def _render(props):
        return PetFloatingWindow({**props, "started_at": 0.0, "now": clock["t"]})

    spec = spec.copy_with(render=_render)
    cache = TerminalWidthCache(ttl=60)
    cache.set_dimensions(width, height)
    model = AppModel()
    model.status.status_active = False
    model.floating_windows = [spec]
    stream = io.StringIO()
    session = InkSession(
        model=model, apply_cmd=apply_cmd, build_tree=build_app_element,
        width_cache=cache, config=TuiConfig.defaults(), stream=stream,
    )
    screen = pyte.Screen(width, height)
    parser = pyte.Stream(screen)
    apply_cmd(model, DisplayMsgsCmd(messages=[
        {"role": "user", "content": f"消息 {index} " + "内容" * 30}
        for index in range(10)
    ]))

    def step(t: float) -> tuple[list[int], object]:
        clock["t"] = t
        session._dirty = True
        session._idle_ok = False
        before = stream.tell()
        session._render_frame()
        stream.seek(before)
        data = stream.read()
        stream.seek(0)
        stream.truncate(0)
        el_rows: list[int] = []
        for token in re.split(r"(\x1b\[[0-9;?]*[A-Za-z]|\r|\n)", data):
            if not token:
                continue
            parser.feed(token)
            if token == "\x1b[K":
                el_rows.append(screen.cursor.y)
        return el_rows, session._last_frame

    step(0.0)
    petf = session._root_fiber._float_frame_windows[0]
    pet_y = int(petf.layout_box.y)
    pet_h = int(petf._float_visible_h)
    pet_x = int(petf.layout_box.x)
    frame = session._last_frame
    offset = frame.height - height  # 底部对齐：屏幕行 = 文档行 - offset
    pet_rows = {
        row - offset for row in range(pet_y, pet_y + pet_h)
        if 0 <= row - offset < height
    }
    assert pet_rows

    # 动画时间点（idle 浮沉 / 眨眼 / 动作切换）逐帧渲染：宠物行不得被 EL 清行。
    for t in (0.4, 1.15, 2.4, 3.7, 6.0):
        el_rows, frame = step(t)
        bad = [row for row in el_rows if row in pet_rows]
        assert not bad, f"宠物行被 EL 清行（会闪）：t={t} rows={bad}"

    with_pet = frame
    with_pet_screen = [line.rstrip() for line in screen.display]
    # 浮窗矩形内整行补足终端宽度（渲染器不需要 EL 清行）。
    for row in range(pet_y, pet_y + pet_h):
        assert with_pet.lines[row].width == width, (
            row, with_pet.lines[row].width,
        )

    # 透明背景：宠物矩形左侧（宠物未覆盖列）的底层文本与无宠物帧逐行一致。
    model.floating_windows = []
    step(6.0)
    base = session._last_frame
    assert base.height == with_pet.height

    def text_range(line, start, end):
        out = type(line)()
        components._append_line_range(out, line, start, end)
        return out.plain

    left_w = max(0, min(pet_x, width))
    for row in range(pet_y, pet_y + pet_h):
        assert text_range(with_pet.lines[row], 0, left_w).rstrip() == (
            text_range(base.lines[row], 0, left_w).rstrip()
        ), f"宠物覆盖了左侧底层内容：row={row}"

    # 最终屏幕与帧内容一致（底部对齐）。
    got = [line.rstrip() for line in with_pet_screen]
    want = [line.plain.rstrip() for line in with_pet.lines[-height:]]
    assert got == want


