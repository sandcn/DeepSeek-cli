"""StatusBar — 状态栏组件（移植 _format_status / _build_separator_line）。

渲染为一行：渐变分隔线 + 内嵌状态文本（模型名/工具计数/耗时/token/速度）。
数据源：AppModel.status + api 快照（_get_snapshot）。

PERF-3/PERF-5：内部用 ``use_memo`` 缓存 ``_build_status_runs`` 结果
（deps = 状态关键字段快照 + 时间桶），组件树重建时对未变更 live 区短路。

BEAUTY-1/PERF-3（方向A 步骤1）：模型名点 FadeIn 渐显窗口内按 0.1s 时间桶
刷新（``time_dep = int(t/0.1)``，渐显平滑推进），渐显结束后回退 1s 桶
（PERF-3 缓存语义保持）——修复 1s 桶内渐显冻结、桶边界跳变。

方向6 步骤6.4 评估结论（布局比例/信息密度/配色，记录于 docstring 可追溯）：
  - 布局比例：非全屏流动模型内容驱动、无视口 pin，聊天区/输入区/状态栏为
    自然文档流，**无固定比例可调**；调整（如压缩聊天区）违背非全屏模型
    设计约束 → **评估不做**。
  - 状态栏信息密度：当前已含模型名/工具计数/耗时/token/速度，信息完备；
    增加密度损害可读性，减少丢失关键状态 → **评估不做**。
  - 配色：dark 已对齐 ``_SEMANTIC_COLOR`` 槽位（方向3 步骤15 收敛），
    light/high-contrast 主题族已注册；无调整需求 → **评估不做**。
"""

from __future__ import annotations

import time

from src.tui.core.style import Style
from src.tui.ink import h, BOX, TEXT, Line, StyledRun, use_memo, use_ref
from src.tui.app import _fx
from src.tui.app._theme import time_glow, _S_ACCENT, _S_ACCENT_BOLD, _S_DIM, _S_SEP, _S_TIME
# 方向C 步骤4：_format_duration 唯一真源在 src/tui/_format.py（Layer 0）；
# 模块级 re-export 保持 patch("src.tui.app.status_bar._format_duration") 路径有效。
from src.tui._format import format_duration as _format_duration

_S_TOKEN = Style(fg=68)
_S_SPEED = Style(fg=214)
_S_TOOL_OK = Style(fg=41)
_S_TOOL_FAIL = Style(fg=196)

# PERF-5：快照查询 TTL 缓存（≤1Hz；渲染线程单写，GIL 原子赋值足够）
# 方向D 步骤16：TTL 常量化（_SNAPSHOT_TTL）——与状态栏 1s 时间桶对齐，
# 快照与显示节奏不产生错位。
_SNAPSHOT_TTL = 1.0
_snapshot_cache: tuple[float, dict] = (0.0, {})

# BEAUTY-1/PERF-3：TuiConfig 惰性获取（避免模块加载环；配置不可变可安全缓存）
_CFG = None


def _get_cfg():
    global _CFG
    if _CFG is None:
        from src.tui._config import TuiConfig
        _CFG = TuiConfig.defaults()
    return _CFG


def _snapshot() -> dict:
    global _snapshot_cache
    now = time.monotonic()
    if now - _snapshot_cache[0] < _SNAPSHOT_TTL:
        return _snapshot_cache[1]
    try:
        from src.tui._snapshot import _get_snapshot
        fn = _get_snapshot()
        data = fn() if fn is not None else {}
    except Exception:
        data = {}
    _snapshot_cache = (now, data)
    return data


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
    deps = 状态关键字段快照 + 时间桶（BEAUTY-1：渐显窗口内 0.1s 桶，
    结束后 1s 桶）。字段快照须显式列出（不能传整个 model/status 对象，
    否则恒变 → 缓存失效）。
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
    # BEAUTY-1/PERF-3：渐显窗口内按 0.1s 桶刷新（平滑渐显），结束后回 1s 桶
    # （PERF-3 缓存语义保持）。fade_duration_sec<=0（配置异常）→ 回退纯 1s 桶。
    cfg = _get_cfg()
    fade_sec = cfg.fade_duration_sec
    fading = fade_sec > 0 and dot_elapsed < fade_sec
    time_dep = int(time.monotonic() / 0.1) if fading else int(time.monotonic() / 1.0)
    status_runs = use_memo(
        lambda: _build_status_runs(model, dot_elapsed),
        (
            st.status_active,
            st.model_name,
            st.tool_total,
            st.tool_count,
            st.tool_fail,
            time_dep,
        ),
    )
    # 分割线（上面）
    # ★ 方向6（分隔线宽度统一）：分隔线铺满 width 列（修复前 width-2 与
    #   状态行 col2 缩进宽度不一致）；状态行前缀 2 列 + 内容经 truncate_line
    #   截断至 width（内容从 col3 起 ≤ width-2）——宽度统一为 width。
    sep = Line.of("\u2501" * max(1, width), _S_SEP)
    # 状态行（下面）
    status_line = Line.of("  ", None)
    if status_runs:
        for run in status_runs:
            status_line.append_run(run)
    # ★ 方向4（状态行溢出截断）：超长状态 runs 截断至 width——复用
    #   ink.helpers.truncate_line（subagent_panel 已用 truncate_runs 族，
    #   status_bar 用 truncate_line 保持 Line 结构；修复前溢出静默裁剪）。
    if status_line.width > width:
        from src.tui.ink.helpers import truncate_line
        status_line = truncate_line(status_line, width)
    return h(BOX, None, [
        h(TEXT, {"styled": sep.runs, "height": 1}),
        h(TEXT, {"styled": status_line.runs, "height": 1}),
    ])


__all__ = ["StatusBar"]
