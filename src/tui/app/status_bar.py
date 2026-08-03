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
from src.tui._format import format_speed as _format_speed

_S_TOKEN = Style(fg=68)
_S_SPEED = Style(fg=214)
_S_TOOL_OK = Style(fg=41)

# BEAUTY-7：streaming braille spinner 帧序列（与 _subagent_render 共用语义）
# ★ 方向4：唯一真源 _fx.SPINNER_FRAMES——本模块保留别名（兼容既有 patch 路径；
#   值与原 `\u280b\u2819...` 转义串完全一致）。
from src.tui.app._fx import SPINNER_FRAMES as _SPINNER_FRAMES

# PERF-5：快照查询 TTL 缓存（≤1Hz；渲染线程单写，GIL 原子赋值足够）
# 方向D 步骤16：TTL 常量化（_SNAPSHOT_TTL）——与状态栏 1s 时间桶对齐，
# 快照与显示节奏不产生错位。
_SNAPSHOT_TTL = 1.0
_snapshot_cache: tuple[float, dict] = (0.0, {})


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


def _build_status_runs(model, dot_elapsed: float = 0.0,
                       spinner_char: str = "\u00b7") -> list[StyledRun]:
    """构建状态文本 runs（模型名/工具计数/耗时/token/速度）。

    Args:
        model: AppModel 实例。
        dot_elapsed: 模型名点 FadeIn 渐显已流逝时间（BEAUTY-1，时间基）；
            >=duration 后返回呼吸色（动画结束）。
        spinner_char: 活跃状态指示字符（BEAUTY-7：streaming 时 10Hz spinner
            帧；空闲为静态 ``·``）。
    """
    st = model.status
    status_active = st.status_active

    model_part: list[StyledRun] = []
    if st.model_name:
        if status_active:
            # BEAUTY-1：模型名点出现时从暗色渐显到呼吸色（时间基）
            dot_color = _fx.fade_color(dot_elapsed, None, 238, _glow(36, 45, 4))
            dot_style = Style(fg=dot_color)
            # BEAUTY-9：流式期间模型名整体呼吸（亮青 45 邻域脉动，8s 周期）——
            # 与分隔线/输入区分隔线呼吸同步，活跃状态更有活力。
            model_name_style = Style(fg=time_glow(45, 55, 8.0), bold=True)
        else:
            dot_style = _S_ACCENT
            model_name_style = _S_ACCENT_BOLD
        model_part.append(StyledRun(f"{spinner_char} ", dot_style))
        model_part.append(StyledRun(st.model_name, model_name_style))
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
            # ★ BEAUTY-16（动效）：工具失败计数警示呼吸——tool_fail>0 时
            #   总数从暗红 196 呼吸到亮红 208（8s，醒目但不过度闪烁），提示
            #   有工具失败；无失败保持成功绿。time_glow 0.1s 桶缓存。
            if st.tool_fail > 0:
                count_style = Style(fg=time_glow(196, 208, 8.0))
            else:
                count_style = _S_TOOL_OK
            # ★ BEAUTY-15（动效）：工具计数箭头呼吸——活跃期箭头亮青脉动
            # （45-55，8s，与模型名/分隔线呼吸同步），空闲静态强调色。
            # time_glow 0.1s 桶缓存，10Hz 渲染时平滑推进。
            arrow_style = Style(fg=time_glow(45, 55, 8.0)) if status_active else _S_ACCENT
            parts.append(StyledRun(f"{st.tool_count}\u2192", arrow_style))
            parts.append(StyledRun(f"{tool_total}", count_style))
        else:
            done = tool_total - st.tool_count - st.tool_fail
            parts.append(StyledRun(f"{done}", _S_TOOL_OK))
            parts.append(StyledRun("/", _S_DIM))
            if st.tool_fail > 0:
                parts.append(StyledRun(
                    f"{tool_total}", Style(fg=time_glow(196, 208, 8.0)),
                ))
            else:
                parts.append(StyledRun(f"{tool_total}", _S_TOOL_OK))
    if elapsed > 0:
        parts.append(StyledRun(_format_duration(elapsed), _S_TIME))
    if total > 0:
        tok = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
        parts.append(StyledRun(f"{tok}t", _S_TOKEN))
    if speed > 0:
        # 单一真源：format_speed（subagent 卡与状态栏统一 tok/s 显示）
        parts.append(StyledRun(_format_speed(speed), _S_SPEED))

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


def _glow(lo: int, hi: int, period: float) -> int:
    """状态点呼吸色（时间基正弦插值）。参数语义与 ``time_glow(lo, hi, period)`` 一致。"""
    return time_glow(lo, hi, period)


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
    # BEAUTY-1：模型名点渐显起始时间（use_ref 跨渲染保持；status_active 切换
    # 或 model_name 变化时重置——模型名变化后新名称出现重新渐显，time.monotonic
    # 时间基，非帧计数）。★ 方向4：fade 键含 model_name——修复前仅含
    # status_active，切换模型（Ctrl+N）时旧 fade 状态残留（新模型名直接以
    # 呼吸色显示，无渐显过渡）。
    dot_fade_ref = use_ref(None)
    fade_key = (st.status_active, st.model_name)
    if dot_fade_ref.current is None or dot_fade_ref.current[0] != fade_key:
        dot_fade_ref.current = (fade_key, time.monotonic())
    dot_elapsed = time.monotonic() - dot_fade_ref.current[1]
    # BEAUTY-1/PERF-3：渐显窗口内按 0.1s 桶刷新（平滑渐显），结束后回 1s 桶
    # （PERF-3 缓存语义保持）。fade_duration_sec<=0（配置异常）→ 回退纯 1s 桶。
    # BEAUTY-7：status_active 期间恒用 0.1s 桶——streaming spinner + 模型点
    #   呼吸以 10Hz 平滑推进（流式期间帧率本就 10Hz，零额外渲染成本）；
    #   空闲回 1s 桶（静态显示，CPU 保持低占用）。
    if st.status_active:
        time_dep = int(time.monotonic() / 0.1)
        # BEAUTY-7：streaming spinner 帧（10Hz）——spinner_frame 返回帧索引，
        # 必须经 _SPINNER_FRAMES 查表取字符（修复前直接格式化索引 → 显示数字
        # 0-9 循环）。
        from src.tui.app import _fx
        spinner_char = _SPINNER_FRAMES[_fx.spinner_frame(10.0, _SPINNER_FRAMES)]
    else:
        time_dep = int(time.monotonic() / 1.0)
        spinner_char = "\u00b7"
    status_runs = use_memo(
        lambda: _build_status_runs(model, dot_elapsed, spinner_char),
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
    # 方向3（动效）：流式/活跃期间分隔线用青色呼吸（32-45，8s 周期）——
    #   活跃状态的分隔线更生动；空闲保持静态深灰（_S_SEP）。
    if st.status_active:
        sep_style = Style(fg=time_glow(32, 45, 8.0))
    else:
        sep_style = _S_SEP
    sep = Line.of("\u2501" * max(1, width), sep_style)
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
    # ★ 方向4（空状态压缩）：无模型名且无统计（status_runs 空）时只渲染分隔线
    #   一行——避免启动期 / 未配置模型时状态栏空行占位（视觉更紧凑）。
    if not status_runs:
        return h(BOX, None, [
            h(TEXT, {"styled": sep.runs, "height": 1}),
        ])
    return h(BOX, None, [
        h(TEXT, {"styled": sep.runs, "height": 1}),
        h(TEXT, {"styled": status_line.runs, "height": 1}),
    ])


__all__ = ["StatusBar"]
