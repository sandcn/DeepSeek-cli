"""渲染文本控制序列消毒回归测试（2026-10-23 用户报障）。

用户报障：上屏流式输出时**偶发**把内容写到下屏最顶的分割线里，随后「闪一下」
（下一帧）就恢复；**bash 工具输出与浮动窗口时更容易出现**。

根因（本文件锁定的防护面）：
  - 渲染文本里的**非 SGR 控制序列**（``\\x1b[1A`` 光标上移、``\\x1b[K`` 清行、
    OSC 标题等）与**危险 C0 控制字符**（``\\x0b``/``\\x0c`` 换行、``\\x08``
    退格、``\\x07`` 响铃）宽度按 0 计，终端却真的执行光标/换屏动作 →
    渲染器「光标/物理缓冲记账」与真实屏幕脱节，整屏错位一行（上屏最后一行
    压到下屏顶部分割线），下一帧重绘才恢复。
  - bash 工具输出（``AppModel.append_tool_output`` → ``ansi_to_line``）修复前
    只解析 SGR、其余序列原样保留；浮窗内容 / 历史回放 / 通知经
    ``StyledRun`` 直接进入渲染模型，同样没有消毒。

修复：
  1. ``src._ansi_text`` 新增 ``sanitize_control_text`` / ``iter_sgr_text`` /
     ``has_dangerous_control``（只保留 SGR + 可见字符，保留 ``\\t``）；
  2. ``renderer.ansi.helpers.ansi_to_runs`` 与 ``tui.app._ansi_runs._parse``
     改用 ``iter_sgr_text``（工具输出 / diff 块路径）；
  3. ``StyledRun.__post_init__`` / ``StyledRun.fast`` 作为最后一道防线统一消毒
     （浮窗、历史回放、通知等所有渲染文本入口）；
  4. ``StyledRun`` 保留 ``\\n``（多行文本由上游 ``wrap_*`` 拆分，消毒不得把
     多行并成一行——否则固定高度 Text 裁剪语义被破坏）。

覆盖：
  1. 宽度口径与 Termux ``WcWidth`` 一致（VS16 不改变基字符宽度，勿按 Unicode
     emoji presentation 算 2 列——高估同样会让记账与终端不一致）；
  2. ``ansi_to_runs`` / ``ansi_to_line`` 只保留 SGR；
  3. ``StyledRun`` 消毒（含 ``fast`` 快路径回退）与 ``\\n`` 保留；
  4. 端到端：含控制序列的工具卡输出经真实渲染管线写入终端模拟器后，屏幕
     与帧内容严格一致（修复前会错位一行）。
"""

from __future__ import annotations

import io

import pytest

from src._ansi_text import (
    has_dangerous_control,
    iter_sgr_text,
    sanitize_control_text,
)
from src._text_width import string_width
from src.renderer.ansi.helpers import ansi_to_line, ansi_to_runs
from src.tui.app.app import build_app_element
from src.tui.app.model import AppModel
from src.tui.ink import components
from src.tui.ink.output import Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.renderer import InkRenderer

#: 各类会驱动终端动作的控制序列/字符。
_DIRTY_SAMPLES = (
    "A\x1b[1AB",        # CSI 光标上移（bash 进度/重绘输出常见）
    "X\x1b[2KY",        # CSI 清行
    "T\x1b]0;title\x07U",  # OSC 标题（BEL 结束）
    "V\x0bW",           # VT 垂直制表（换行）
    "F\x0cG",           # FF 换页（换行）
    "H\x08I",           # BS 退格
    "J\x07K",           # BEL
)


def _plain(line: Line) -> str:
    return "".join(r.text for r in line.runs)


# ── 1. 宽度口径（与 Termux WcWidth 一致，勿按 emoji presentation 算 2） ──


def test_vs16_sequences_keep_base_width() -> None:
    """``❤️``/``⚠️``/``☑️`` 宽 1：Termux ``WcWidth`` 把 VS16 计零宽且这些基字符
    不在 ``WIDE_EASTASIAN`` 表内。按 2 列计算会高估，同样导致记账错位。"""
    assert string_width("\u2764\ufe0f") == 1   # ❤️
    assert string_width("\u26a0\ufe0f") == 1   # ⚠️
    assert string_width("\u2611\ufe0f") == 1   # ☑️
    # 真正的宽 emoji 仍为 2（不在 VS16 语义内）。
    assert string_width("\u2705") == 2         # ✅
    assert string_width("\U0001f600") == 2     # 😀


# ── 2. ANSI→runs：只保留 SGR ─────────────────────────────


#: 消毒后应保留的可见文本（CSI/OSC 的参数与终结字节随序列整段丢弃）。
_EXPECTED = {
    "A\x1b[1AB": "AB",
    "X\x1b[2KY": "XY",
    "T\x1b]0;title\x07U": "TU",
    "V\x0bW": "VW",
    "F\x0cG": "FG",
    "H\x08I": "HI",
    "J\x07K": "JK",
}


@pytest.mark.parametrize("dirty", _DIRTY_SAMPLES)
def test_ansi_to_line_strips_control_sequences(dirty: str) -> None:
    """``ansi_to_line`` 的纯文本不含任何控制序列/控制字符。"""
    plain = ansi_to_line(dirty).plain
    assert not has_dangerous_control(plain), repr(plain)
    assert plain == _EXPECTED[dirty]


def test_ansi_to_runs_keeps_sgr_style() -> None:
    """SGR 样式保留（红线/颜色不被消毒误伤）。"""
    runs = ansi_to_runs("\x1b[31mRED\x1b[0m")
    assert [r.text for r in runs] == ["RED"]
    assert any(r.style is not None and r.style.fg is not None for r in runs)


def test_iter_sgr_text_only_emits_sgr() -> None:
    """``iter_sgr_text`` 只产出文本段与 SGR 段（其余序列丢弃）。"""
    items = list(iter_sgr_text("A\x1b[31mB\x1b[1A\x0bC\x1b]0;t\x07D"))
    kinds = [k for k, _ in items]
    assert "sgr" in kinds
    assert all(k in ("text", "sgr") for k in kinds)
    assert "".join(seg for k, seg in items if k == "text") == "ABCD"


# ── 3. StyledRun：最后一道防线 ────────────────────────────


@pytest.mark.parametrize("dirty", _DIRTY_SAMPLES)
def test_styled_run_sanitizes_control(dirty: str) -> None:
    run = StyledRun(dirty)
    assert not has_dangerous_control(run.text), repr(run.text)
    assert run.width == string_width(run.text)


def test_styled_run_fast_falls_back_for_dirty_text() -> None:
    """``fast`` 快路径遇控制字符回退常规构造（消毒 + 重算宽度）。"""
    run = StyledRun.fast("A\x1b[1AB", None, None, 2)
    assert not has_dangerous_control(run.text)
    assert run.text == "AB"
    assert run.width == 2


def test_styled_run_keeps_newline_for_upstream_wrap() -> None:
    """消毒保留 ``\\n``（多行文本由上游 wrap 拆分，不能并成一行）。"""
    run = StyledRun("aa\nbb")
    assert run.text == "aa\nbb"
    # 危险控制字符仍剔除、制表符保留待 expand_tabs 展开。
    run2 = StyledRun("a\x0bb\tc")
    assert "\x0b" not in run2.text
    assert "\t" not in run2.text  # expand_tabs 已展开


def test_sanitize_control_text_keep_newline_flag() -> None:
    assert sanitize_control_text("a\nb\x0bc") == "abc"
    assert sanitize_control_text("a\nb\x0bc", keep_newline=True) == "a\nbc"


# ── 4. 端到端：工具卡输出含控制序列时屏幕与帧一致 ──────────


def _render_to_screen(model, width: int, height: int):
    """真实渲染管线（reconciler → render_frame → InkRenderer → pyte）。"""
    pyte = pytest.importorskip("pyte")
    reconciler = Reconciler()
    root = Reconciler.create_root()
    reconciler.render(root, build_app_element(model, width), width, height)
    frame = components.render_frame(root, width, viewport_height=height)

    stream = io.StringIO()
    screen = pyte.Screen(width, height)
    parser = pyte.Stream(screen)
    renderer = InkRenderer(stream=stream, height=height)
    renderer.render(frame)
    payload = stream.getvalue()
    stream.seek(0)
    stream.truncate(0)
    if payload:
        parser.feed(payload)
    return frame, [line.rstrip() for line in screen.display]


def test_tool_output_control_sequences_do_not_shift_screen() -> None:
    """bash 工具输出含 ESC/VT/BS 时，屏幕内容与帧严格一致（不错位一行）。"""
    width, height = 60, 20
    model = AppModel()
    model.open_tool_box("t1", "bash", "printf test")
    model.append_tool_output(
        "t1",
        "line-A\nline-B\x1b[1Aline-C\nline-D\x0bline-E\x1b[2Kline-F\x08G",
    )

    frame, screen_lines = _render_to_screen(model, width, height)
    texts = ["".join(r.text for r in line.runs) for line in frame.lines]
    assert not any(has_dangerous_control(t) for t in texts), "帧内不得含控制字符"

    n = len(texts)
    expected = [""] * (height - n) + texts if n <= height else texts[-height:]
    assert screen_lines == [t.rstrip() for t in expected]

    # 工具卡正文行确实按「剔除控制字符后」的内容渲染。
    joined = "\n".join(texts)
    assert "line-Bline-C" in joined
    assert "line-Dline-Eline-F" in joined


def test_floating_window_control_sequences_do_not_shift_screen() -> None:
    """浮窗内容含控制字符时屏幕与帧一致（StyledRun 最后防线）。"""
    from src.tui.app.floating import FloatingWindowSpec
    from src.tui.ink import TEXT, h

    width, height = 70, 22
    model = AppModel()

    def _render_window(props):
        return h(TEXT, {"children": "Win\x1b[1Aline\x0bX\x08Y"})

    model.floating_windows = [
        FloatingWindowSpec(
            id="t", render=_render_window, width=24, bottom="bottom_area",
        ),
    ]
    frame, screen_lines = _render_to_screen(model, width, height)
    texts = ["".join(r.text for r in line.runs) for line in frame.lines]
    assert not any(has_dangerous_control(t) for t in texts)
    n = len(texts)
    expected = [""] * (height - n) + texts if n <= height else texts[-height:]
    assert screen_lines == [t.rstrip() for t in expected]
