"""user_select 连续弹出标题叠加显示错乱回归测试（2026-08-18）。

bug：多次弹出 user_select（超屏文档场景）后弹窗标题叠加——屏幕上同时
出现旧标题（如 (1/16)）与新标题（如 (7/16)），选项行错位/丢失。

根因（renderer.py 增长路径）：文档超屏（prev_h >= height）时，head_runs
（头部差异区间，重写的是**位移前旧行**）末尾行写到屏幕底部，其 ``\n``
触发终端滚动（内容上移），而渲染器 ``_advance_row`` 仅钳制光标不计数
滚动 → 位移区补滚动（``shift_start-prev_h+1`` 次）叠加 head_runs 的额外
滚动，总滚动次数比理想 delta（新增行数）多 1 → 内容整体上移错位一行：
弹窗导航帧把标题写在错误位置，旧标题残留上方形成双标题（(1/16) 与
(7/16) 同时显示），且选项行错位/丢失。

修复（renderer.py）：增长路径 head_runs 写行循环中**段末行不写 \n**
（``idx < new_h-1`` → ``idx < end-1``）——head_runs 重写旧行不驱动滚动
（滚动只应由位移区新增行承担，次数 = delta），段末行后的光标衔接由
后续位移区定位/补滚动承担。修复后总滚动恰好 = delta，弹窗导航不再错位。

验证：用 pyte 精确模拟终端（含滚动），构造超屏文档（30 行历史 + 16 选项
弹窗 = 49 行 doc、24 行终端）复现原 bug，断言屏幕标题唯一且位置正确。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from src.tui.app.model import AppModel, UserSelectState
from src.tui.app.app import App
from src.tui.ink import h, Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components
from src.tui.ink.renderer import InkRenderer
from src.tui.ink.fiber import InputHook

try:
    import pyte
except ImportError:  # pragma: no cover - 环境未安装 pyte 时跳过终端模拟用例
    pyte = None

#: 16 个选项（对齐用户报障「多选测试 · 16个选项」）
_OPTS16 = [f"城市{i}" for i in range(1, 17)]


def _build_tree(model, width: int):
    return h(App, {"model": model, "width": width})


def _find_fiber(fiber, key_prefix=None):
    """在 fiber 树中按 key 前缀查找（MultiSelect 控件回调注入用）。"""
    if fiber is None:
        return None
    props = fiber.props or {}
    if key_prefix is not None and str(props.get("key", "")).startswith(key_prefix):
        return fiber
    r = _find_fiber(fiber.child, key_prefix)
    if r is not None:
        return r
    return _find_fiber(fiber.sibling, key_prefix)


def _make_model(n_history: int = 30) -> AppModel:
    """构造带 n 条历史消息的模型（30 条 → 弹窗打开后文档超屏）。"""
    model = AppModel()
    model.width = 80
    for i in range(n_history):
        model.committed_lines.append(
            Line([StyledRun(f"历史消息 {i}: 撑高文档", None)])
        )
    return model


def _open_popup(model, seq: int, selected: int = 0) -> None:
    model.user_select = UserSelectState(
        visible=True, seq=seq, title="想去哪个城市旅游？",
        options=list(_OPTS16), multi_select=True, selected=selected, checked=[],
    )
    model.bottom_view = "user_select"


def _close_popup(model, seq: int) -> None:
    model.user_select.try_set_final("confirmed", ["城市1"])
    model.bottom_view = ""
    model.user_select = UserSelectState(seq=seq)


def _render(rec, root, model, renderer, stream) -> None:
    """完整渲染一帧：组件树 → 调和 → 帧 → 渲染器 → pyte 终端。

    stream 为 None（纯转义序列断言场景）时跳过终端模拟。
    """
    el = _build_tree(model, 80)
    rec.render(root, el, 80, 24)
    frame = _components.render_frame(root, 80)
    out_before = len(renderer._stream.getvalue())
    renderer.render(frame)
    if stream is not None:
        stream.feed(renderer._stream.getvalue()[out_before:])


def _title_lines(screen) -> list:
    """pyte 屏幕中标题行（含 ☑ 且含「想去哪个城市旅游」）。"""
    return [line for line in screen.display if "☑" in line and "想去哪个城市旅游" in line]


def _navigate_multi_select(root, steps: int) -> None:
    """向 MultiSelect 控件发送 steps 次 arrow_down（模拟真实按键导航）。

    走控件完整交互链（use_input handler → 内部 cursor state + onHighlight
    回调 → 组件 selected state）——比直接调 onHighlight 更接近真实按键：
    控件内部 selected 与弹窗 selected 同步更新。
    """
    control = _find_fiber(root.child, key_prefix="us-multiselect")
    assert control is not None, "未找到 MultiSelect 控件 fiber"
    handler = None
    for hook in control.hooks:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            handler = hook.handler
            break
    assert handler is not None, "MultiSelect 未绑定 use_input handler"
    for _ in range(steps):
        handler(SimpleNamespace(
            kind="arrow_down", char="", modifier=0, keycode=0, raw=b"\x1b[B",
        ))


@pytest.fixture(autouse=True)
def _fix_popup_rows(monkeypatch):
    """固定弹窗选项行数预算（消除真实终端高度依赖）。

    ``UserSelectPopup._popup_item_rows`` 经 ``TerminalWidthCache.get_default()
    .get_height()`` 读取**真实终端**高度决定 MultiSelect ``limit``——测试构造
    ``pyte.Screen(80, 24)``（24 行终端）与真实终端高度可能不一致（真实终端
    缩到 <19 行时 ``limit<16``，弹窗高度/偏移改变，标题位置断言失败）。
    固定为 21（= 24-3，对齐 24 行终端的 ``max(6, h-3)`` 预算）使 limit=16。
    """
    monkeypatch.setattr("src.tui.app.user_select._popup_item_rows", lambda: 21)


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestPopupTitleOverlap:
    """user_select 弹窗标题叠加（pyte 终端模拟）。"""

    def test_single_popup_navigate_no_overlap(self):
        """超屏文档中弹窗打开 + 导航：标题唯一且更新为 (7/16)（无双标题）。"""
        model = _make_model(30)
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)

        _render(rec, root, model, renderer, stream)
        _open_popup(model, 1)
        _render(rec, root, model, renderer, stream)

        # 模拟用户按 6 次 ↓ 导航到第 7 项（完整控件交互链）
        _navigate_multi_select(root, 6)
        _render(rec, root, model, renderer, stream)

        titles = _title_lines(screen)
        # ★ 修复断言：标题唯一且为 (7/16)——修复前旧标题 (1/16) 残留叠加
        assert len(titles) == 1, f"标题叠加（{len(titles)} 行）: {titles!r}"
        assert "(7/16)" in titles[0]
        # 标题下方紧跟城市1（顺序正确——滚动错位会导致选项丢失/重复）
        title_idx = screen.display.index(titles[0])
        assert "城市1" in screen.display[title_idx + 1]
        # 选项行完整无错位（城市1..城市16 全在）
        joined = "\n".join(screen.display)
        for i in range(1, 17):
            assert f"城市{i}" in joined

    def test_multi_popup_cycle_final_screen(self):
        """连续 3 次完整弹窗周期（打开→导航→回车→清理）后第 4 次打开：标题唯一。"""
        model = _make_model(30)
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)

        _render(rec, root, model, renderer, stream)
        for rnd in range(1, 4):
            _open_popup(model, rnd)
            _render(rec, root, model, renderer, stream)
            _navigate_multi_select(root, 6)
            _render(rec, root, model, renderer, stream)
            _close_popup(model, rnd)
            _render(rec, root, model, renderer, stream)

        _open_popup(model, 4)
        _render(rec, root, model, renderer, stream)
        titles = _title_lines(screen)
        assert len(titles) == 1, f"标题叠加（{len(titles)} 行）: {titles!r}"
        assert "(1/16)" in titles[0]
        joined = "\n".join(screen.display)
        for i in range(1, 17):
            assert f"城市{i}" in joined


class TestRendererGrowthHeadRuns:
    """渲染器增长路径 head_runs 段末 \n 修复的回归用例。"""

    def _render_popup_open(self):
        """构造超屏文档 + 弹窗打开帧（head_runs 延伸到屏幕底部）。"""
        model = _make_model(30)
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)
        _render(rec, root, model, renderer, stream)
        _open_popup(model, 1)
        return model, rec, root, renderer, screen, stream

    @pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
    def test_growth_head_runs_tail_no_extra_scroll(self):
        """增长路径 head_runs 段末行不写 \\n → 总滚动精确 = delta → 标题位置正确。

        弹窗打开（doc 36→49，delta=13）后标题（doc 行 31）应在屏幕行
        31-(49-24)=6（0-based）——修复前 head_runs 段末行 \\n 叠加位移区
        补滚动，总滚动 14 次 > delta 13，内容整体上移错位一行（标题在行 5），
        随后导航帧把新标题写到行 6，旧标题 (1/16) 残留行 5 形成双标题。
        """
        model, rec, root, renderer, screen, stream = self._render_popup_open()
        # ★ 转义序列级断言必须针对**弹窗打开增长帧本身**：记录渲染前位置，
        #   断言该帧输出不含相邻双换行（head_runs 段末 \\n + 位移区补滚动
        #   \\n 叠加会形成 ``\\r\\n\\r\\n``——修复前弹窗打开帧在城市4 处出现；
        #   修复后段末不写 \\n，补滚动单独出现无相邻序列）。
        out_before = len(renderer._stream.getvalue())
        _render(rec, root, model, renderer, stream)
        delta = renderer._stream.getvalue()[out_before:]
        assert "\r\n\r\n" not in delta, (
            "增长路径出现相邻双换行（head_runs 段末 \\n + 补滚动 \\n 叠加），"
            "将导致额外滚动、内容错位"
        )
        titles = _title_lines(screen)
        assert len(titles) == 1, f"标题叠加（{len(titles)} 行）: {titles!r}"
        # ★ 修复断言：标题精确位于 doc 行 31 - 屏幕偏移 25 = 屏幕行 6（0-based）
        title_idx = screen.display.index(titles[0])
        assert title_idx == 6, f"标题屏幕位置错位（应在 6，实际 {title_idx}）: {titles[0]!r}"

    @pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
    def test_shrink_into_screen_no_leftover(self):
        """缩短进入屏幕内（_rewrite_drifted）不残留弹窗旧行。

        超屏文档（30 历史 + 弹窗 = 49 行）→ 弹窗关闭（缩短到 36 行，仍超屏）
        → 再次缩短到屏幕内：最终屏幕应为历史 + 正常底部区，无弹窗标题残留。
        """
        model = _make_model(30)
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)

        _render(rec, root, model, renderer, stream)
        _open_popup(model, 1)
        _render(rec, root, model, renderer, stream)
        # 弹窗关闭（done + 清理）——文档缩短
        model.user_select.try_set_final("confirmed", ["城市1"])
        model.bottom_view = ""
        model.user_select = UserSelectState(seq=1)
        _render(rec, root, model, renderer, stream)
        # 历史进一步缩短到屏幕内（模拟清屏/重排）
        model.committed_lines = model.committed_lines[:5]
        _render(rec, root, model, renderer, stream)
        joined = "\n".join(screen.display)
        assert "☑" not in joined, "缩短后残留弹窗标题"
        assert "标准模式" in joined or "> " in joined, "正常底部区未恢复"

    def test_growth_height_unbounded_no_crash(self):
        """height=0（无约束/测试场景）增长路径不抛异常且帧高度正确。

        有意弱断言（P2-2 降级）：height=0 时 ``delta != 0 and height > 0``
        分支不进入（head_runs 恒空，段末 \\n 修复不参与）——本用例仅回归
        「无约束增长路径不被重构破坏」。
        """
        model = _make_model(5)
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=0)
        _render(rec, root, model, renderer, None)
        _open_popup(model, 1)
        el = _build_tree(model, 80)
        rec.render(root, el, 80, 0)
        frame = _components.render_frame(root, 80)
        renderer.render(frame)  # 不抛异常即通过
        assert frame.height > 0

    def test_multi_head_runs_segments_no_newline(self):
        """多区间 head_runs：每个区间段末行均不写 \\n（段末 \\n 修复的完整语义面）。

        直接构造两处独立差异区间（可见区顶部行 12 + 底部行 34，均为段末行），
        增长 +5 行（delta=5）。修复前：head_runs 段末行写 \\n 会与位移区补
        滚动 \\n 相邻形成 ``\\r\\n\\r\\n`` 序列特征（修复后段末不写 \\n、经
        cursor 定位衔接，无相邻双换行）。
        """
        from src.tui.ink.output import Frame

        def _mk(lines):
            return Frame([Line([StyledRun(t, None)]) for t in lines])

        prev_lines = [f"L{i}" for i in range(36)]
        new_lines = list(prev_lines)
        new_lines[12] = "HEAD'"
        new_lines[34] = "TAIL'"
        new_lines += [f"NEW{i}" for i in range(5)]
        renderer = InkRenderer(stream=io.StringIO(), height=24)
        renderer.render(_mk(prev_lines))  # 首帧（超屏 36 行）
        out_before = len(renderer._stream.getvalue())
        renderer.render(_mk(new_lines))
        delta = renderer._stream.getvalue()[out_before:]
        # 段末行不写 \n → 无相邻双换行（修复前段末 \n + 补滚动 \n 相邻）
        assert "\r\n\r\n" not in delta, (
            "多区间 head_runs 段末行写 \\n 叠加补滚动（相邻双换行），"
            "将导致额外滚动、内容错位"
        )
