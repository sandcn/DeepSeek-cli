"""StatusBar — 状态栏组件（移植 _format_status / _build_separator_line）。

渲染为一行：渐变分隔线 + 内嵌状态文本（模型名/工具计数/耗时/token/速度）。
数据源：AppModel.status + api 快照（_get_snapshot）。

PERF-3/PERF-5：内部用 ``use_memo`` 缓存 ``_build_status_runs`` 结果
（deps = 状态关键字段快照 + 时间桶），组件树重建时对未变更 live 区短路。

「一切皆插件」：状态栏各信息段（模型名 / 工具计数 / 耗时 / token / 速度）由
``src.tui.app._status_segments`` 注册表驱动——每个段一个清单插件条目
（``status_segment``），可被 Patch/Overlay 禁用或替换；本模块保留各段的
**构建实现**（``_model_segment`` 等）与装配逻辑。

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
from weakref import WeakKeyDictionary

from src.tui.core.style import Style
from src.tui.ink import h, TEXT, Line, StyledRun, use_memo, use_ref, Column
from src.tui.app import _fx
from src.tui.app._theme import time_glow, _S_ACCENT, _S_ACCENT_BOLD, _S_DIM, _S_TIME
# ★ 方向5：分隔线样式统一真源（_theme.sep_style）——别名 _theme_sep_style
# 避免与下方局部变量 sep_style 命名冲突。
from src.tui.app._theme import sep_style as _theme_sep_style
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
from src.tui.app._fx import SPINNER_FRAMES as _SPINNER_FRAMES  # noqa: F401

# PERF-5：快照查询 TTL 缓存（≤1Hz；渲染线程单写，GIL 原子赋值足够）
# 方向D 步骤16：TTL 常量化（_SNAPSHOT_TTL）——与状态栏 1s 时间桶对齐，
# 快照与显示节奏不产生错位。
_SNAPSHOT_TTL = 1.0

#: 压缩驱动的 token/速度展示期使用的快照 TTL（秒）。
#:
#: 压缩摘要调用结束的瞬间 token 才进入全局统计——若沿用 1s TTL，压缩期
#: （``compaction_active > 0``）缓存下来的旧快照（总 tok 仍为 0）会再存活
#: 最多 1s，叠加状态栏 1s 时间桶后，用户最坏要 ~2s 才看到这次压缩的 token。
#: 压缩驱动展示期（非活跃期）改用短 TTL，保证「压缩刚结束」即可看到。
_COMPACTION_SNAPSHOT_TTL = 0.2

#: 快照 TTL 缓存（弱引用键控 model 实例）——不写在 model 属性上：
#: 渲染必须是纯函数（渲染期写 model 属性即副作用：第二次渲染可能不再调用
#: ``fn()``，返回值随渲染次数变化，且与 memo 短路/并发渲染/双调用校验冲突）。
#: 弱引用键控同时保持多实例隔离（各 AppModel 独立 TTL，互不串扰），且 model
#: 被回收时缓存条目自动消失。
_snapshot_cache: "WeakKeyDictionary" = WeakKeyDictionary()

#: 压缩结束后「总tok / tok/s」在状态栏继续展示的宽限期（秒）。
#:
#: 压缩摘要是**静默**流式调用（后台生成、不进入活跃期门控）：token 在压缩
#: 期间实时入账，随后压缩立即结束（``compact · N`` 归零）。若只在「压缩进行
#: 中」展示，用户几乎看不到这次压缩产生的 token——故压缩结束后仍展示本宽限期
#: 长度，之后回到活跃期门控（不长期占用状态栏）。
_COMPACTION_TOKEN_GRACE_SEC = 5.0


def _tokens_visible(status_active: bool, status, now: float) -> bool:
    """「总tok / tok/s」段是否可见。

    可见条件（任一）：
      - 活跃期（``status_active``，原有语义）；
      - 正在压缩（``compaction_active > 0``）——空闲期手动 ``/compact`` 也可见；
      - 压缩结束后的 ``_COMPACTION_TOKEN_GRACE_SEC`` 宽限期内——压缩摘要为
        静默后台流式调用（不进入活跃期门控），宽限期让用户看得到「总tok」
        增量与该次压缩的「tok/s」。

    Args:
        status_active: 状态栏是否处于活跃期（轮次进行中）。
        status: AppModel.status 状态对象（缺字段按未压缩处理）。
        now: 当前单调时钟时间戳（与 ``compaction_last_end_ts`` 同源）。

    Returns:
        True 表示 tokens / speed 段应渲染。
    """
    if status_active:
        return True
    if int(getattr(status, "compaction_active", 0) or 0) > 0:
        return True
    end_ts = float(getattr(status, "compaction_last_end_ts", 0.0) or 0.0)
    if end_ts <= 0:
        return False
    return (now - end_ts) < _COMPACTION_TOKEN_GRACE_SEC


def _snapshot(model, ttl: float = _SNAPSHOT_TTL) -> dict:
    """api 快照查询（TTL 缓存，默认 ≤1Hz）。

    ★ 多实例隔离 + 渲染纯净：缓存挂模块级 ``WeakKeyDictionary``（键为 model
    实例）——各 AppModel 互不串扰，且**不写 model 属性**（渲染期无副作用）。
    渲染线程单写，GIL 原子赋值足够。

    Args:
        model: AppModel 实例（弱引用键）。
        ttl: 缓存有效期（秒）。活跃期用 ``_SNAPSHOT_TTL``（1s，与状态栏时间桶
            对齐）；压缩驱动的展示期用 ``_COMPACTION_SNAPSHOT_TTL``（短 TTL，
            让「压缩刚结束」的 token 增量立即可见）。
    """
    now = time.monotonic()
    try:
        cache = _snapshot_cache.get(model)
    except TypeError:
        cache = None  # 不可弱引用/不可哈希的模型：不缓存（每次都查）
    if cache is not None and now - cache[0] < ttl:
        return cache[1]
    try:
        from src.tui._snapshot import _get_snapshot
        fn = _get_snapshot()
        data = fn() if fn is not None else {}
    except Exception:
        data = {}
    try:
        _snapshot_cache[model] = (now, data)
    except TypeError:
        pass
    return data


class StatusContext:
    """状态栏段构建上下文（段实现读取的运行时快照）。"""

    __slots__ = (
        "model", "status", "status_active", "tokens_visible", "dot_elapsed",
        "spinner_char", "reasoning_effort", "snapshot",
    )

    def __init__(self, model, status, status_active, dot_elapsed,
                 spinner_char, reasoning_effort, snapshot, tokens_visible=None):
        self.model = model
        self.status = status
        self.status_active = status_active
        #: 总 tok / tok/s 段是否可见（活跃期，或压缩中/压缩刚结束的宽限期——
        #: 压缩摘要是静默后台流式调用，空闲期（手动 ``/compact``）产生的 token
        #: 只有放开活跃期门控才看得到）。未显式传入时跟随 ``status_active``
        #: （向后兼容既有构造调用）。
        self.tokens_visible = bool(status_active) if tokens_visible is None else bool(tokens_visible)
        self.dot_elapsed = dot_elapsed
        self.spinner_char = spinner_char
        self.reasoning_effort = reasoning_effort
        self.snapshot = snapshot

    def get(self, key, default=None):
        return self.snapshot.get(key, default)


# ═══════════════════════════════════════════════════════════
# 内置状态栏段（清单条目经点分引用解析到这些函数）
# ═══════════════════════════════════════════════════════════


def _model_segment(ctx: StatusContext) -> list:
    """模型名段（含 spinner 点 + 推理等级标签；空闲/活跃样式区分）。"""
    st = ctx.status
    _model_name = getattr(st, "model_name", "")
    model_part: list[StyledRun] = []
    if not _model_name:
        return model_part
    if ctx.status_active:
        # BEAUTY-1：模型名点出现时从暗色渐显到呼吸色（时间基）
        dot_color = _fx.fade_color(ctx.dot_elapsed, None, 238, _glow(36, 45, 4))
        dot_style = Style(fg=dot_color)
        # BEAUTY-9：流式期间模型名整体呼吸（亮青 45 邻域脉动，8s 周期）。
        model_name_style = Style(fg=time_glow(45, 55, 8.0), bold=True)
    else:
        dot_style = _S_ACCENT
        model_name_style = _S_ACCENT_BOLD
    model_part.append(StyledRun(f"{ctx.spinner_char} ", dot_style))
    model_part.append(StyledRun(_model_name, model_name_style))
    # ★ 推理等级标签（2026-08-14）：模型名后追加当前推理等级
    #   （low/medium/high/max，/reasoning 命令配置）。暗灰 dim 弱化；格式
    #   [level] 与 /reasoning 命令显示一致。
    eff = (ctx.reasoning_effort or "").strip().lower()
    if eff:
        model_part.append(StyledRun(f" [{eff}]", _S_DIM))
    return model_part


def _tools_segment(ctx: StatusContext) -> list:
    """工具计数段（活跃期；运行中 ⚙ n/m，完成 ✔ m / ✔ n/m ✖ f）。"""
    if not ctx.status_active:
        return []
    st = ctx.status
    # ★ P2-8（review 修复）：统一 getattr 防御——测试桩/异常状态对象可能缺
    #   tool_total/tool_count/tool_fail 字段（直接属性访问 AttributeError）。
    tool_total = getattr(st, "tool_total", 0) or 0
    tool_count = getattr(st, "tool_count", 0) or 0
    tool_fail = getattr(st, "tool_fail", 0) or 0
    if tool_total <= 0:
        return []
    parts: list[StyledRun] = []
    # ★ BEAUTY-16（动效）：工具失败计数警示呼吸。
    fail_style = Style(fg=time_glow(196, 208, 8.0))
    if tool_count > 0:
        # 运行中：⚙ 运行数/总数（箭头色呼吸——与旧 ``n→m`` 同语义，格式更清晰）
        arrow_style = Style(fg=time_glow(45, 55, 8.0))
        parts.append(StyledRun("\u2699 ", arrow_style))
        parts.append(StyledRun(f"{tool_count}/{tool_total}", _S_TOOL_OK))
    else:
        # 全部结束：✔ 总数（无失败）或 ✔ 成功数/总数 ✖ 失败数
        # ★ P2-9：计数源不一致时钳制到 0，避免显示负数完成数。
        done = max(0, tool_total - tool_count - tool_fail)
        if tool_fail > 0:
            parts.append(StyledRun("\u2714 ", _S_TOOL_OK))
            parts.append(StyledRun(f"{done}/{tool_total}", _S_TOOL_OK))
            parts.append(StyledRun(" \u2716 ", fail_style))
            parts.append(StyledRun(f"{tool_fail}", fail_style))
        else:
            parts.append(StyledRun("\u2714 ", _S_TOOL_OK))
            parts.append(StyledRun(f"{tool_total}", _S_TOOL_OK))
    return parts


def _elapsed_segment(ctx: StatusContext) -> list:
    """耗时段（活跃期；⏱ 图标 + 呼吸色）。"""
    if not ctx.status_active:
        return []
    elapsed = ctx.get("elapsed_seconds", 0.0)
    if elapsed <= 0:
        return []
    # ★ BEAUTY-20：耗时显示呼吸（活跃期浅蓝 110→120，12s 周期）。
    # ★ BEAUTY-36：耗时加 ⏱ 图标前缀。
    elapsed_style = Style(fg=time_glow(110, 120, 12.0)) if ctx.status_active else _S_TIME
    return [StyledRun(f"\u23f1 {_format_duration(elapsed)}", elapsed_style)]


def _tokens_segment(ctx: StatusContext) -> list:
    """token 段（活跃期或压缩期/压缩宽限期；◆ 图标 + 呼吸色）。"""
    if not ctx.tokens_visible:
        return []
    total = ctx.get("total_tokens", 0)
    if total <= 0:
        return []
    # ★ BEAUTY-20：token 计数呼吸（活跃期紫蓝 68→78，12s 周期）。
    # ★ BEAUTY-36：token 加 ◆ 图标前缀。
    token_style = Style(fg=time_glow(68, 78, 12.0)) if ctx.status_active else _S_TOKEN
    tok = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
    return [StyledRun(f"\u25c6 {tok}t", token_style)]


def _speed_segment(ctx: StatusContext) -> list:
    """速度段（活跃期或压缩期/压缩宽限期；» 图标 + 呼吸色）。"""
    if not ctx.tokens_visible:
        return []
    speed = ctx.get("per_second_speed", 0.0)
    if speed <= 0:
        return []
    # ★ BEAUTY-20：速度显示呼吸（活跃期橙黄 214→224，12s 周期）。
    # ★ BEAUTY-36：速度加 » 图标前缀。
    speed_style = Style(fg=time_glow(214, 224, 12.0)) if ctx.status_active else _S_SPEED
    # 单一真源：format_speed（subagent 卡与状态栏统一 tok/s 显示）
    return [StyledRun(f"\u00bb {_format_speed(speed)}", speed_style)]


def _messages_segment(ctx: StatusContext) -> list:
    """会话消息数段（空闲/活跃均显示；消息源未注入时为空）。"""
    count = 0
    try:
        source = getattr(ctx.model, "message_source", None)
        if callable(source):
            count = len(source() or [])
    except Exception:
        count = 0
    if count <= 0:
        return []
    return [StyledRun(f"\u2709 {count}", Style(fg=110))]


def _build_status_runs(model, dot_elapsed: float = 0.0,
                       spinner_char: str = "\u00b7",
                       reasoning_effort: str | None = None) -> list[StyledRun]:
    """构建状态文本 runs（段注册表驱动：模型名/工具计数/耗时/token/速度）。

    Args:
        model: AppModel 实例。
        dot_elapsed: 模型名点 FadeIn 渐显已流逝时间（BEAUTY-1，时间基）；
            >=duration 后返回呼吸色（动画结束）。
        spinner_char: 活跃状态指示字符（BEAUTY-7：streaming 时 30Hz spinner
            帧；空闲为静态 ``·``）。
        reasoning_effort: 当前推理等级（low/medium/high/max）；None 或空串不显示。

    段由 ``src.tui.app._status_segments`` 注册表提供（每个段一个清单条目）：
    ``model`` 段为模型名部分（不与其它段用分隔符连接），其余段（tools/
    elapsed/messages/tokens/speed）按声明顺序用 `` · `` 连接。各段自行门控
    可见性：tools/elapsed 仅活跃期渲染；tokens/speed 在活跃期或压缩中/压缩
    结束宽限期内渲染（``_tokens_visible``，压缩摘要是静默后台流式调用，空闲期
    ``/compact`` 产生的 token 也要看得到）；messages 为常驻段（空闲也显示
    ——2026-10-07 状态栏信息增强）。
    """
    from ._status_segments import active_segment_ids, resolve_segment

    st = model.status
    # ★ P3（review）：与同函数其它字段（tool_total/tool_count 等）防御风格
    #   统一——测试桩模型缺字段时回退默认值而非 AttributeError。
    status_active = bool(getattr(st, "status_active", False))
    # ★ 2026-10（用户需求）：压缩消耗的 token 要能在状态栏看到——tokens/speed
    #   段除活跃期外，压缩中与压缩结束宽限期内也可见（空闲期手动 /compact
    #   也能看到本次压缩的 总tok / tok/s）。快照仅在需要展示时查询（空闲且
    #   无压缩时不查，保持零开销）。
    tokens_visible = _tokens_visible(status_active, st, time.monotonic())
    # 压缩驱动的展示期（非活跃期）用短快照 TTL——压缩摘要结束后 token 才入账，
    # 长 TTL 会让压缩期缓存的旧快照（总 tok=0）拖慢显示。
    snap_ttl = _SNAPSHOT_TTL if status_active else _COMPACTION_SNAPSHOT_TTL
    snap = _snapshot(model, snap_ttl) if tokens_visible else {}
    ctx = StatusContext(
        model=model, status=st, status_active=status_active,
        dot_elapsed=dot_elapsed, spinner_char=spinner_char,
        reasoning_effort=reasoning_effort, snapshot=snap,
        tokens_visible=tokens_visible,
    )

    model_part: list[StyledRun] = []
    segment_runs: list[list[StyledRun]] = []
    for spec_id in active_segment_ids():
        fn = resolve_segment(spec_id)
        if fn is None:
            continue
        runs = fn(ctx) or []
        if not runs:
            continue
        if spec_id == "model":
            model_part = list(runs)
        else:
            # ★ 段级分隔（2026-10-07）：一个段的多个 run 视为**整体**——段与段
            #   之间才插入 `` · `` 分隔符（修复前逐 run 插入：``_tools_segment``
            #   的 ``⚙`` 与计数、图标与数值之间被分隔符拆开，显示成
            #   ``⚙ · 1/1``）。
            segment_runs.append(list(runs))

    # ★ 后台任务数量已迁至模式行行首（input_area._build_mode_line）——状态栏
    #   不再显示。
    # ★ 2026-10-07（状态栏信息增强）：不再在非活跃状态提前返回——各段实现
    #   自行门控（tools/elapsed/tokens/speed 非活跃返回 []），而 messages 段
    #   **空闲也显示**（信息常驻）。
    if not segment_runs:
        return model_part
    sep = StyledRun(" \u00b7 ", _S_DIM)
    joined: list[StyledRun] = []
    for i, runs in enumerate(segment_runs):
        if i > 0:
            joined.append(sep)
        joined.extend(runs)
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
    # ★ P3（review）：属性读取经 getattr 归一化——与 ``_build_status_runs``
    #   的防御风格一致（测试桩模型缺字段时不再 AttributeError 中断渲染）。
    st_active = bool(getattr(st, "status_active", False))
    st_model_name = getattr(st, "model_name", "")
    # BEAUTY-1：模型名点渐显起始时间（use_ref 跨渲染保持；status_active 切换
    # 或 model_name 变化时重置——模型名变化后新名称出现重新渐显，time.monotonic
    # 时间基，非帧计数）。★ 方向4：fade 键含 model_name——修复前仅含
    # status_active，切换模型（Ctrl+N）时旧 fade 状态残留（新模型名直接以
    # 呼吸色显示，无渐显过渡）。
    dot_fade_ref = use_ref(None)
    fade_key = (st_active, st_model_name)
    if dot_fade_ref.current is None or dot_fade_ref.current[0] != fade_key:
        dot_fade_ref.current = (fade_key, time.monotonic())
    dot_elapsed = time.monotonic() - dot_fade_ref.current[1]
    # BEAUTY-1/PERF-3：渐显窗口内按 0.1s 桶刷新（平滑渐显），结束后回 1s 桶
    # （PERF-3 缓存语义保持）。fade_duration_sec<=0（配置异常）→ 回退纯 1s 桶。
    # ★ P1-1（review 修复）：按「渐显窗口」而非 status_active 决定桶粒度——
    #   修复前空闲恒 1s 桶：模型名首次出现（status_active=False）时渐显
    #   （fade_duration 默认 0.6s）在 1s 桶内冻结/步进，渐显动画实际不可见。
    #   修复后 dot_elapsed < fade_duration 期间（含空闲）用 0.1s 桶平滑渐显，
    #   结束后回 1s 桶（与 docstring 声明一致）。
    # BEAUTY-7：status_active 期间恒用 0.1s 桶——streaming spinner + 模型点
    #   呼吸以 30Hz 平滑推进（流式期间帧率本就 30Hz，零额外渲染成本）；
    #   空闲非渐显期回 1s 桶（静态显示，CPU 保持低占用）。
    if st_active:
        time_dep = int(time.monotonic() / 0.1)
        # BEAUTY-7：streaming spinner 帧（30Hz）——spinner_frame 返回帧索引，
        # 必须经 _SPINNER_FRAMES 查表取字符（修复前直接格式化索引 → 显示数字
        # 0-9 循环）。
        spinner_char = _fx.spinner_char()
    else:
        # fade_duration 惰性读取（_default_fx_params，与 fade_color 一致——
        # 修复前用固化快照 _DEFAULT_FADE_DURATION 判断渐显窗口）
        fade_duration_sec = _fx._default_fx_params()[0]
        if fade_duration_sec > 0 and dot_elapsed < fade_duration_sec:
            # 空闲渐显窗口：0.1s 桶平滑渐显（静态点字符——空闲 spinner 为 ·）
            time_dep = int(time.monotonic() / 0.1)
        else:
            time_dep = int(time.monotonic() / 1.0)
        spinner_char = "\u00b7"
    # ★ 推理等级（/reasoning 命令配置 low/medium/high/max）：模型名后显示
    #   [level]。读取配置（RC 内存缓存 + 单键缓存，每帧读取开销极小；异常
    #   回退空串不显示）。作为 use_memo deps——update_config 清配置缓存后
    #   下帧读取新值 → deps 变化 → 状态栏即时刷新（/reasoning 切换无需
    #   额外同步链路）。
    try:
        from src.config import REASONING_EFFORT as _eff
        reasoning_effort = str(_eff or "")
    except Exception:
        reasoning_effort = ""
    status_runs = use_memo(
        lambda: _build_status_runs(model, dot_elapsed, spinner_char, reasoning_effort),
        (
            st_active,
            st_model_name,
            getattr(st, "tool_total", 0),
            getattr(st, "tool_count", 0),
            getattr(st, "tool_fail", 0),
            # ★ 2026-08-19（用户需求）：阶段标签（…思考/…回答/…解析）全部
            #   删除——main_phase 不再进 deps（状态栏不再依赖阶段切换）。
            time_dep,
            # ★ BUG-43（review 方向）：deps 补充 spinner_char——修复前依赖
            #   time_dep（0.1s 桶）兜底，``int(now/0.1)`` 与 ``int(now*10)``
            #   浮点边界偶发错位 ≤1 帧（spinner 帧与缓存不同步）。
            spinner_char,
            # ★ 推理等级（2026-08-14）：切换（/reasoning）后 deps 变化 →
            #   重建状态行（模型名后 [level] 即时刷新）。
            reasoning_effort,
            # ★ 2026-10（用户需求：压缩消耗的 token 要能在状态栏看到）：
            #   压缩状态变化（开始/结束）立即重建状态行——空闲期压缩中与
            #   压缩结束宽限期内 tokens/speed 段的出现/消失即时生效
            #   （仅 compaction_active 需入 deps；宽限期的到期由 time_dep
            #   的 1 秒桶驱动，最迟 1 秒内收起）。
            getattr(st, "compaction_active", 0),
        ),
    )
    # 分割线（上面）
    # ★ 方向6（分隔线宽度统一）：分隔线铺满 width 列；状态行前缀 2 列 + 内容经
    #   truncate_line 截断至 width——宽度统一为 width。
    # 方向3（动效）：流式/活跃期间分隔线用青色呼吸（32-45，8s 周期）；
    #   空闲保持静态深灰（_S_SEP）。
    # ★ 方向5：分隔线样式统一经 _theme.sep_style。
    # ★ 全面控件化（方案B）：分隔线经标准控件 ``Divider`` 渲染。
    sep_style = _theme_sep_style(st_active)
    # 状态行（下面）
    # ★ 性能（PERF-10）：状态行 Line **缓存**（use_memo 键 status_runs 引用）
    status_line = use_memo(
        lambda: _build_status_line(status_runs),
        (status_runs,),
    )
    # ★ 方向4（状态行溢出截断）：超长状态 runs 截断至 width
    if status_line.width > width:
        from src.tui.ink.helpers import truncate_line
        status_line = truncate_line(status_line, width)
    # ★ 方向4（空状态压缩）：无模型名且无统计（status_runs 空）时只渲染分隔线
    #   一行。
    from src.tui.ink.widgets.display import Divider
    if not status_runs:
        return h(Column, None, [
            h(Divider, {"width": width, "char": "\u2501", "style": sep_style}),
        ])
    return h(Column, None, [
        h(Divider, {"width": width, "char": "\u2501", "style": sep_style}),
        h(TEXT, {"styled": status_line.runs, "height": 1}),
    ])


def _build_status_line(status_runs: list) -> Line:
    """构建状态行 Line（前缀 2 列 + 状态 runs）。

    ★ PERF-10：独立函数供 use_memo 缓存（StatusBar 每帧调用）——status_runs
    引用不变时复用同一 Line 对象（跨帧同一 runs 列表 → TEXT 引用级缓存命中）。
    """
    line = Line.of("  ", None)
    if status_runs:
        for run in status_runs:
            line.append_run(run)
    return line


__all__ = ["StatusBar"]
