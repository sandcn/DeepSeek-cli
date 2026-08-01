"""StatusBar — 状态栏组件（移植 _format_status / _build_separator_line）。

渲染为一行：渐变分隔线 + 内嵌状态文本（模型名/工具计数/耗时/token/速度）。
数据源：AppModel.status + api 快照（_get_snapshot）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, Line, StyledRun
from src.tui._animator import AnimatorContext

_S_ACCENT = Style(fg=45)
_S_ACCENT_BOLD = Style(fg=45, bold=True)
_S_DIM = Style(fg=242)
_S_TIME = Style(fg=110)
_S_TOKEN = Style(fg=68)
_S_SPEED = Style(fg=214)
_S_TOOL_OK = Style(fg=41)
_S_TOOL_FAIL = Style(fg=196)
_S_SEP = Style(fg=237)


def _snapshot() -> dict:
    try:
        from src.tui._snapshot import _get_snapshot
        fn = _get_snapshot()
        if fn is None:
            return {}
        return fn()
    except Exception:
        return {}


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}:{secs:02d}"
    return f"{mins // 60}:{mins % 60:02d}:{secs:02d}"


def _build_status_runs(model, animator) -> list[StyledRun]:
    """构建状态文本 runs（模型名/工具计数/耗时/token/速度）。"""
    st = model.status
    runs: list[StyledRun] = []
    status_active = st.status_active

    model_part: list[StyledRun] = []
    if st.model_name:
        dot_style = Style(fg=_glow(animator, 36, 45, 4)) if status_active else _S_ACCENT
        model_part.append(StyledRun("\u00b7 ", dot_style))
        model_part.append(StyledRun(st.model_name, _S_ACCENT_BOLD))
    if not status_active:
        return model_part

    snap = _snapshot()
    total = snap.get("total_tokens", 0)
    elapsed = snap.get("elapsed_seconds", 0.0)
    speed = snap.get("per_second_speed", 0.0)
    tool_total = st.tool_total

    parts: list[StyledRun] = []
    if tool_total > 0:
        if st.tool_count > 0:
            count_style = _S_TOOL_FAIL if st.tool_fail > 0 else _S_TOOL_OK
            parts.append(StyledRun(f"{st.tool_count}\u2192", _S_ACCENT))
            parts.append(StyledRun(f"{tool_total}", count_style))
        else:
            done = tool_total - st.tool_count - st.tool_fail
            parts.append(StyledRun(f"{done}", _S_TOOL_OK))
            parts.append(StyledRun("/", _S_DIM))
            parts.append(StyledRun(f"{tool_total}", _S_TOOL_FAIL if st.tool_fail > 0 else _S_TOOL_OK))
    if elapsed > 0:
        parts.append(StyledRun(_format_duration(elapsed), _S_TIME))
    if total > 0:
        tok = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
        parts.append(StyledRun(f"{tok}t", _S_TOKEN))
    if speed > 0:
        speed_str = f"{speed:.1f}" if speed >= 1 else f"{speed:.2f}"
        parts.append(StyledRun(f"{speed_str}t/s", _S_SPEED))

    if not parts:
        return model_part
    sep = StyledRun(" \u00b7 ", _S_DIM)
    joined: list[StyledRun] = []
    for i, p in enumerate(parts):
        if i > 0:
            joined.append(sep)
        joined.append(p)
    if model_part:
        return model_part + [StyledRun("  ", None)] + joined
    return joined


def _glow(animator, base: int, hi: int, amp: int) -> int:
    return animator.sine_color(base, hi, amp) if animator.breath_frame > 0 else base


def StatusBar(props) -> object:
    """StatusBar 组件：分割线一行在上，状态文本一行在下。"""
    model = props["model"]
    animator = props.get("animator") or AnimatorContext.get_default()
    width = props.get("width", 80)
    status_runs = _build_status_runs(model, animator)
    # 分割线（上面）
    sep = Line.of("\u2501" * max(1, width - 2), _S_SEP)
    # 状态行（下面）
    status_line = Line.of("  ", None)
    if status_runs:
        for run in status_runs:
            status_line.append_run(run)
    return h(BOX, None, [
        h(TEXT, {"styled": sep.runs, "height": 1}),
        h(TEXT, {"styled": status_line.runs, "height": 1}),
    ])


__all__ = ["StatusBar"]
