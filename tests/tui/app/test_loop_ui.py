"""``/loop`` TUI 展示：状态栏 token 宽限期与循环浮窗内容测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.tui.app._state_types import LoopStatusState
from src.tui.app.floating import LoopFloatingWindow
from src.tui.app.input_area import _build_bg_task_prefix
from src.tui.app.status_bar import (
    _LOOP_PROMPT_TOKEN_GRACE_SEC,
    _tokens_visible,
)
from src.tui.ink import h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi


def _status(**overrides):
    base = {
        "compaction_active": 0,
        "compaction_last_end_ts": 0.0,
        "title_generating": False,
        "title_last_end_ts": 0.0,
        "loop_prompt_generating": False,
        "loop_prompt_last_end_ts": 0.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _render(model, width: int = 80) -> str:
    return strip_ansi(
        renderToString(h(LoopFloatingWindow, {"model": model, "width": width}), {"columns": width})
    )


# ── 状态栏 tok / tok/s 可见性 ────────────────────────────


def test_tokens_hidden_when_idle():
    assert _tokens_visible(False, _status(), 100.0) is False


def test_tokens_visible_while_loop_prompt_generating():
    assert _tokens_visible(False, _status(loop_prompt_generating=True), 100.0) is True


def test_tokens_visible_during_loop_prompt_grace():
    status = _status(loop_prompt_last_end_ts=100.0)
    assert _tokens_visible(False, status, 100.0 + _LOOP_PROMPT_TOKEN_GRACE_SEC - 0.1) is True
    assert _tokens_visible(False, status, 100.0 + _LOOP_PROMPT_TOKEN_GRACE_SEC + 0.1) is False


def test_tokens_visible_when_status_active_even_with_loop_fields():
    assert _tokens_visible(True, _status(), 100.0) is True


# ── 模式行「✎ 提词」 ────────────────────────────────────


def test_mode_line_shows_loop_prompt_marker():
    line = _build_bg_task_prefix(
        None, 0, 0, compaction_active=0,
        title_generating=True, loop_prompt_generating=True,
    )
    text = "".join(run.text for run in line.runs)
    assert "✎ 标题" in text
    assert "✎ 提词" in text
    assert text.index("✎ 标题") < text.index("✎ 提词")


def test_mode_line_hides_loop_prompt_marker_when_idle():
    line = _build_bg_task_prefix(None, 0, 0)
    assert "提词" not in "".join(run.text for run in line.runs)


# ── 循环浮窗 ─────────────────────────────────────────────


def _model(**loop_fields):
    state = LoopStatusState(active=True, **loop_fields)
    return SimpleNamespace(loop_status=state)


def test_floating_window_finite_progress():
    model = _model(
        total=3,
        round=2,
        prompt="修复登录页",
        next_prompt="补充回归测试",
        round_started_at=time.monotonic() - 125,
    )
    text = _render(model)
    assert "⟳ /loop" in text
    assert "第 2/3 次" in text
    assert "67%" in text
    assert "2:05" in text
    assert "修复登录页" in text
    assert "补充回归测试" in text


def test_floating_window_infinite_progress():
    model = _model(total=0, round=4, prompt="继续", round_started_at=time.monotonic() - 1)
    text = _render(model)
    assert "第 4 次" in text
    assert "∞" in text


def test_floating_window_generating_state():
    model = _model(total=0, round=1, prompt="继续", generating=True)
    text = _render(model)
    assert "生成提词中…" in text
    assert "等待生成" not in text


def test_floating_window_waiting_state():
    model = _model(total=0, round=1, prompt="继续")
    text = _render(model)
    assert "等待生成" in text


def test_floating_window_inactive_renders_empty():
    model = SimpleNamespace(loop_status=LoopStatusState(active=False))
    assert _render(model) == ""


def test_floating_window_narrow_width_does_not_wrap():
    model = _model(
        total=10,
        round=5,
        prompt="很长的提词" * 30,
        next_prompt="很长的下一轮提词" * 30,
        round_started_at=time.monotonic() - 65,
    )
    text = _render(model, width=24)
    # 每行不超过窗口宽度；5 行内容 + 可能的首尾空行。
    for line in text.splitlines():
        assert len(line) <= 24
    assert "…" in text
