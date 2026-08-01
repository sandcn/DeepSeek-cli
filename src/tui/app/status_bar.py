"""StatusBar — 状态栏组件（移植 _format_status / _build_separator_line）。

渲染为一行：渐变分隔线 + 内嵌状态文本（模型名/工具计数/耗时/token/速度）。
数据源：AppModel.status + api 快照（_get_snapshot）。

PERF-3/PERF-5：内部用 ``use_memo`` 缓存 ``_build_status_runs`` 结果
（deps = 状态关键字段快照 + 1s 时间桶），组件树重建时对未变更 live 区短路。
"""

from __future__ import annotations

import time

from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, Line, StyledRun, use_memo, use_ref
from src.tui.app import _fx
from src.tui.app._theme import time_glow, _S_ACCENT, _S_ACCENT_BOLD, _S_DIM, _S_SEP, _S_TIME

_S_TOKEN = Style(fg=68)
_S_SPEED = Style(fg=214)
_S_TOOL_OK = Style(fg=41)
_S_TOOL_FAIL = Style(fg=196)

# PERF-5：快照查询 TTL 缓存（≤1Hz；渲染线程单写，GIL 原子赋值足够）
_snapshot_cache: tuple[float, dict] = (0.0, {})


def _snapshot() -> dict:
    global _snapshot_cache
    now = time.monotonic()
    if now - _snapshot_cache[0] < 1.0:
        return _snapshot_cache[1]
    try:
        from src.tui._snapshot import _get_snapshot
        fn = _get_snapshot()
        data = fn() if fn is not None else {}
    except Exception:
        data = {}
    _snapshot_cache = (now, data)
    return data


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}:{secs:02d}"
    return f"{mins // 60}:{mins % 60:02d}:{secs:02d}"


def _build_status_runs(model, dot_elapsed: float = 0.0) -> list[StyledRun]:
    """构建状态文本 runs（模型名/工具计数/耗时/token/速度）。

    Args:
        model: AppModel 实例。
        dot_elapsed: 模型名点 FadeIn 渐显已流逝时间（BEAUTY-1，时间基）；
            >=duration 后返回呼吸色（动画结束）。
    """
    st = model.status
    runs: list[StyledRun] = []
    status_active = st.status_active

    model_part: list[StyledRun] = []
    if st.model_name:
        if status_active:
            # BEAUTY-1：模型名点出现时从暗色渐显到呼吸色（时间基）
            dot_color = _fx.fade_color(dot_elapsed, None, 238, _glow(36, 45, 4))
            dot_style = Style(fg=dot_color)
        else:
            dot_style = _S_ACCENT
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


def _glow(base: int, hi: int, amp: int) -> int:
    return time_glow(base, hi, amp)


def StatusBar(props) -> object:
    """StatusBar 组件：分割线一行在上，状态文本一行在下。

    PERF-3：``use_memo`` 缓存 ``_build_status_runs(model)`` 结果——
    deps = 状态关键字段快照 + 1s 时间桶（PERF-5）。字段快照须显式列出
    （不能传整个 model/status 对象，否则恒变 → 缓存失效）。
    """
    model = props["model"]
    width = props.get("width", 80)
    st = model.status
    # BEAUTY-1：模型名点渐显起始时间（use_ref 跨渲染保持；status_active 切换时重置，
    # time.monotonic 时间基，非帧计数）
    dot_fade_ref = use_ref(None)
    if dot_fade_ref.current is None or dot_fade_ref.current[0] != st.status_active:
        dot_fade_ref.current = (st.status_active, time.monotonic())
    dot_elapsed = time.monotonic() - dot_fade_ref.current[1]
    status_runs = use_memo(
        lambda: _build_status_runs(model, dot_elapsed),
        (
            st.status_active,
            st.model_name,
            st.tool_total,
            st.tool_count,
            st.tool_fail,
            int(time.monotonic() / 1.0),
        ),
    )
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
