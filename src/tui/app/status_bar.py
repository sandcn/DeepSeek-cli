"""StatusBar — 状态栏组件（移植 _format_status / _build_separator_line）。

渲染为一行：渐变分隔线 + 内嵌状态文本（模型名/工具计数/耗时/token/速度）。
数据源：AppModel.status + api 快照（_get_snapshot）。

窄屏溢出（2026-10-21 用户需求）：终端过窄、状态项放不下时，**不截断**状态项
——能完整放下的项留在状态行，放不下的项（含只差一点点就放不下的）整体上移到
**顶部分隔线（最顶那条 ``━``）的最右侧**显示（左侧分隔线按剩余宽度填充）。
即：状态行只出现「显示完整」的项，显示不完整的项一律去分隔线右侧；
溢出内容本身仍超宽时才截断（尾部 ``…``），并保留至少
``_STATUS_OVERFLOW_MIN_FILL`` 列分隔线以维持「分隔线」观感。

PERF-3/PERF-5：内部用 ``use_memo`` 缓存 ``_build_status_items`` / 宽度布局
结果（deps = 状态关键字段快照 + 时间桶；布局 deps = 项引用 + width），组件树
重建时对未变更 live 区短路。

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

import threading
import time
from typing import NamedTuple
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
from src.tui._format import format_tokens as _format_tokens

_S_TOKEN = Style(fg=68)
_S_SPEED = Style(fg=214)
_S_TOOL_OK = Style(fg=41)
_S_WORKSPACE_BRANCH = Style(fg=110)
_S_WORKSPACE_PATH = Style(fg=242)
# 多模态读图能力徽标（模型名旁的「图」）：青绿加粗，和模型名的青蓝主色区分开，
# 又不抢眼——只有模型档案开启「多模态读图」时才出现。
_S_MULTIMODAL = Style(fg=141, bold=True)

# ── 窄屏溢出（状态项上移到顶部分隔线右侧）────────────────────────
#: 段间分隔文本（非模型段之间）。
_STATUS_ITEM_SEP = " \u00b7 "
#: 模型段与后续段之间的间距（沿用原有「双空格」视觉，不加分隔符）。
_STATUS_MODEL_GAP = "  "
#: 状态行行首缩进宽度（``_build_status_line`` 的 ``"  "`` 前缀）。
_STATUS_LINE_INDENT = 2
#: 溢出内容移到分隔线右侧时，左侧至少保留的分隔线列数——保证那一行仍然
#: 看得出是分隔线（仅在终端宽于该值时生效；更窄时用满整行）。
_STATUS_OVERFLOW_MIN_FILL = 2

# BEAUTY-7：streaming braille spinner 帧序列（与 _subagent_render 共用语义）
# ★ 方向4：唯一真源 _fx.SPINNER_FRAMES——本模块保留别名（兼容既有 patch 路径；
#   值与原 `\u280b\u2819...` 转义串完全一致）。
from src.tui.app._fx import SPINNER_FRAMES as _SPINNER_FRAMES  # noqa: F401

# PERF-5：快照查询 TTL 缓存（≤1Hz；渲染线程单写，GIL 原子赋值足够）
# 方向D 步骤16：TTL 常量化（_SNAPSHOT_TTL）——与状态栏 1s 时间桶对齐，
# 快照与显示节奏不产生错位。
_SNAPSHOT_TTL = 1.0

#: 思考级别读取缓存（模型名 → (时间, 值)）。状态栏 0.1s 桶重建时分段读取
#: 当前档案（load_profiles 读盘）；短 TTL 缓存把频率降到 ≤2Hz，模型名变化
#: 时立即失效（/models 切换即时生效）。
_REASONING_EFFORT_TTL = 0.5
_reasoning_effort_cache: dict[str, tuple[float, str]] = {}
_reasoning_effort_lock = threading.Lock()


def _cached_reasoning_effort(model_name: str) -> str:
    """读取当前生效思考级别（按模型名短 TTL 缓存；异常回退空串）。"""
    key = str(model_name or "")
    now = time.monotonic()
    with _reasoning_effort_lock:
        hit = _reasoning_effort_cache.get(key)
        if hit is not None and now - hit[0] < _REASONING_EFFORT_TTL:
            return hit[1]
    try:
        from src.config.model_profiles import current_reasoning_effort as _current_effort
        value = str(_current_effort() or "")
    except Exception:
        value = ""
    with _reasoning_effort_lock:
        _reasoning_effort_cache[key] = (now, value)
    return value


#: 思考模式开关缓存（全局开关，不按模型区分）。Ctrl+T 切换后 ≤0.5s 内
#: 状态栏自动刷新（关闭时模型名后显示 ``[off]``）。
_THINKING_ENABLED_TTL = 0.5
_thinking_enabled_cache: tuple[float, bool] | None = None
_thinking_enabled_lock = threading.Lock()


def _cached_thinking_enabled() -> bool:
    """读取全局思考模式开关（短 TTL 缓存；异常回退 True=官方默认开启）。"""
    global _thinking_enabled_cache
    now = time.monotonic()
    with _thinking_enabled_lock:
        hit = _thinking_enabled_cache
        if hit is not None and now - hit[0] < _THINKING_ENABLED_TTL:
            return hit[1]
    try:
        from src.config.model_profiles import thinking_enabled as _thinking_enabled
        value = bool(_thinking_enabled())
    except Exception:
        value = True
    with _thinking_enabled_lock:
        _thinking_enabled_cache = (now, value)
    return value


#: 多模态读图能力缓存（模型名 → (时间, 是否支持)）。与思考级别同样的短 TTL：
#: /models 切换档案或保存「多模态读图」开关后 ≤0.5s 内状态栏自动刷新。
_multimodal_cache: dict[str, tuple[float, bool]] = {}
_multimodal_lock = threading.Lock()


def _cached_multimodal(model_name: str) -> bool:
    """当前模型档案是否开启「多模态读图」（按模型名短 TTL 缓存）。"""
    key = str(model_name or "")
    if not key:
        return False
    now = time.monotonic()
    with _multimodal_lock:
        hit = _multimodal_cache.get(key)
        if hit is not None and now - hit[0] < _REASONING_EFFORT_TTL:
            return hit[1]
    try:
        from src.config.model_profiles import model_supports_multimodal as _supports
        value = bool(_supports(key))
    except Exception:
        value = False
    with _multimodal_lock:
        _multimodal_cache[key] = (now, value)
    return value

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

#: 标题生成结束后「总tok / tok/s」在状态栏继续展示的宽限期（秒）。
#:
#: 用户需求 2026-10-20：标题请求走**流式 API**，其 token 与 tok/s 也要在
#: status 行可见。标题在空闲期（消费用户消息后、主回答并发）生成，本身不进入
#: 活跃期门控——生成期间 + 结束后宽限期内展示，之后自动收起。
_TITLE_TOKEN_GRACE_SEC = 8.0

#: ``/loop`` 续跑提词生成结束后「总tok / tok/s」的宽限期（秒）。
#:
#: 与标题生成同理：续跑提词是 silent 辅助流式调用，生成期间 + 结束后宽限期
#: 内让这次请求的 token 消耗可见。
_LOOP_PROMPT_TOKEN_GRACE_SEC = 8.0


def _tokens_visible(status_active: bool, status, now: float) -> bool:
    """「tok/s」段是否可见（token 入/出段常驻，不受本门控）。

    可见条件（任一）：
      - 活跃期（``status_active``，原有语义）；
      - 正在压缩（``compaction_active > 0``）——空闲期手动 ``/compact`` 也可见；
      - 压缩结束后的 ``_COMPACTION_TOKEN_GRACE_SEC`` 宽限期内——压缩摘要为
        静默后台流式调用（不进入活跃期门控），宽限期让用户看得到该次压缩的
        「tok/s」；
      - 正在生成会话标题，或标题生成结束后的 ``_TITLE_TOKEN_GRACE_SEC``
        宽限期内（用户需求 2026-10-20：标题请求的 tok/s 也要在 status 行
        可见；标题同样是静默后台流式调用）。

    Args:
        status_active: 状态栏是否处于活跃期（轮次进行中）。
        status: AppModel.status 状态对象（缺字段按未压缩/未生成标题处理）。
        now: 当前单调时钟时间戳（与 ``compaction_last_end_ts`` /
            ``title_last_end_ts`` 同源）。

    Returns:
        True 表示 speed 段应渲染。
    """
    if status_active:
        return True
    if int(getattr(status, "compaction_active", 0) or 0) > 0:
        return True
    end_ts = float(getattr(status, "compaction_last_end_ts", 0.0) or 0.0)
    if end_ts > 0 and (now - end_ts) < _COMPACTION_TOKEN_GRACE_SEC:
        return True
    if bool(getattr(status, "title_generating", False)):
        return True
    title_end_ts = float(getattr(status, "title_last_end_ts", 0.0) or 0.0)
    if title_end_ts > 0 and (now - title_end_ts) < _TITLE_TOKEN_GRACE_SEC:
        return True
    # /loop 续跑提词：生成中 / 结束后宽限期内同样展示 token 与 tok/s。
    if bool(getattr(status, "loop_prompt_generating", False)):
        return True
    loop_end_ts = float(getattr(status, "loop_prompt_last_end_ts", 0.0) or 0.0)
    return (
        loop_end_ts > 0
        and (now - loop_end_ts) < _LOOP_PROMPT_TOKEN_GRACE_SEC
    )


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
        "spinner_char", "reasoning_effort", "snapshot", "multimodal",
        "thinking_enabled",
    )

    def __init__(self, model, status, status_active, dot_elapsed,
                 spinner_char, reasoning_effort, snapshot, tokens_visible=None,
                 multimodal=False, thinking_enabled=True):
        self.model = model
        self.status = status
        self.status_active = status_active
        #: tok/s 段是否可见（活跃期，或压缩中/压缩刚结束的宽限期——压缩摘要
        #: 是静默后台流式调用，空闲期（手动 ``/compact``）产生的速度只有放开
        #: 活跃期门控才看得到）。token 入/出段**常驻**，不读本字段。未显式
        #: 传入时跟随 ``status_active``（向后兼容既有构造调用）。
        self.tokens_visible = bool(status_active) if tokens_visible is None else bool(tokens_visible)
        self.dot_elapsed = dot_elapsed
        self.spinner_char = spinner_char
        self.reasoning_effort = reasoning_effort
        self.multimodal = bool(multimodal)
        #: 全局思考模式开关（Ctrl+T；False 时模型名后显示 ``[off]``）。
        self.thinking_enabled = bool(thinking_enabled)
        self.snapshot = snapshot

    def get(self, key, default=None):
        return self.snapshot.get(key, default)


# ═══════════════════════════════════════════════════════════
# 内置状态栏段（清单条目经点分引用解析到这些函数）
# ═══════════════════════════════════════════════════════════


def _model_segment(ctx: StatusContext) -> list:
    """模型名段（含 spinner 点 + 当前模型思考级别标签；空闲/活跃样式区分）。"""
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
    # ★ 多模态读图能力徽标（2026-10-19 用户需求）：模型档案开启「多模态读图」
    #   时在模型名旁显示一个「图」字——用户一眼可知当前模型能否调用 read_image。
    #   青绿色加粗，与模型名主色区分；关闭时不占位（false 不显示，保持简洁）。
    if getattr(ctx, "multimodal", False):
        model_part.append(StyledRun(" \u56fe", _S_MULTIMODAL))
    # ★ 思考级别标签（2026-08-14）：模型名后追加当前生效模型的思考级别
    #   （low/medium/high/max，/models 中按模型配置，空则回退全局默认）。
    #   暗灰 dim 弱化；格式 [level] 与 /models 表单显示一致。
    eff = (ctx.reasoning_effort or "").strip().lower()
    if not getattr(ctx, "thinking_enabled", True):
        # ★ 2026-10（用户需求：Ctrl+T 切换思考模式）：关闭时显示 [off]，
        #   替代思考级别标签（此时 effort 不生效，避免误导）。
        model_part.append(StyledRun(" [off]", _S_DIM))
    elif eff:
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
    """token 段（常驻；◆ 入/出 累计 tok + 活跃期呼吸色）。

    显示口径（2026-10-22 用户需求）：
      - ``入``：**所有 Agent**（主 Agent + 所有 SubAgent）的上下文增量
        累计输入 token——同一 Agent 本次 input 减上次 input；首次 / 上下文
        缩小 / 切模型计全量；压缩 / 标题等内部调用不计。不是每次完整
        prompt 的简单累加（避免重复计历史上下文）；
      - ``出``：全局生成 token 总量（现有 ``total_tokens`` 口径）。
    输入总量只在请求结束（真实 usage 到达）时更新，因此本段**空闲也常驻**，
    不受 ``tokens_visible`` 的活跃期门控。某一侧为 0 / 不可用时只显示另一侧，
    避免在失败路径显示误导性的 ``入 0t``。
    """
    try:
        total_input = max(0, int(ctx.get("total_input_tokens", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        total_input = 0
    try:
        total_output = max(0, int(ctx.get("total_tokens", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        total_output = 0
    if total_input <= 0 and total_output <= 0:
        return []
    # ★ BEAUTY-20：token 计数呼吸（活跃期紫蓝 68→78，12s 周期）。
    # ★ BEAUTY-36：token 加 ◆ 图标前缀。
    token_style = Style(fg=time_glow(68, 78, 12.0)) if ctx.status_active else _S_TOKEN
    parts: list[str] = []
    if total_input > 0:
        parts.append(f"入 {_format_tokens(total_input)}t")
    if total_output > 0:
        parts.append(f"出 {_format_tokens(total_output)}t")
    return [StyledRun("\u25c6 " + " \u00b7 ".join(parts), token_style)]


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
    """会话消息数段（空闲/活跃均显示；消息源未注入时为空）。

    只统计**对话消息**：顶层 system 提词不显示（空对话不应显示 ``✉ 2``
    这类由系统提词造成的误导性计数）。
    """
    count = 0
    try:
        source = getattr(ctx.model, "message_source", None)
        if callable(source):
            messages = source() or []
            count = sum(
                1 for m in messages
                if not (isinstance(m, dict) and m.get("role") == "system")
            )
    except Exception:
        count = 0
    if count <= 0:
        return []
    return [StyledRun(f"\u2709 {count}", Style(fg=110))]


def _workspace_segment(ctx: StatusContext) -> list:
    """工作区段：Git 分支 + 当前目录（``⎇ main  ~/proj``）。

    分支/目录查询复用欢迎卡环境信息实现（``_welcome._git_branch`` 有 5s
    缓存；目录读取开销极小）。两者都不可用时返回空列表（不占状态栏）。
    目录按显示宽度截断，避免长路径挤掉 token/速度等运行信息。
    """
    try:
        from ._welcome import _git_branch, _short_cwd
        from src.tui._width import truncate_width

        branch = str(_git_branch() or "")
        cwd = str(_short_cwd() or "")
    except Exception:
        return []
    if not branch and not cwd:
        return []
    runs: list[StyledRun] = []
    if branch:
        runs.append(StyledRun(f"\u2387 {branch}", _S_WORKSPACE_BRANCH))
    if cwd:
        if runs:
            runs.append(StyledRun("  ", None))
        runs.append(StyledRun(truncate_width(cwd, 32), _S_WORKSPACE_PATH))
    return runs


class StatusItems(NamedTuple):
    """状态栏「项」列表（每项一个段，段内多 run 为一个整体）。

    Attributes:
        items: 段 runs 列表（按显示顺序；``model`` 段存在时恒为第 0 项）。
        model_first: 第 0 项是否为模型名段（决定模型段后使用双空格间距而非
            `` · `` 分隔符）。
    """

    items: list[list[StyledRun]]
    model_first: bool


class StatusLayout(NamedTuple):
    """状态栏宽度布局结果（窄屏时状态项分流到两行）。

    Attributes:
        line_runs: 状态行（分隔线下方那行）要显示的 runs——只含**能完整
            放下**的项。
        overflow_runs: 放不下的项（连续后缀）的 runs——显示在顶部分隔线
            最右侧（见 ``StatusBar``）。
    """

    line_runs: list[StyledRun]
    overflow_runs: list[StyledRun]


def _build_status_items(model, dot_elapsed: float = 0.0,
                        spinner_char: str = "\u00b7",
                        reasoning_effort: str | None = None,
                        multimodal: bool = False,
                        thinking_enabled: bool = True) -> StatusItems:
    """构建状态栏「项」列表（段注册表驱动：模型名/工具计数/耗时/token/速度）。

    每项是一个段的 runs（段内多 run 为整体）；项与项之间**不**预置分隔符，
    由 ``_join_status_items`` / ``_layout_status_items`` 按显示位置统一插入
    （窄屏时项可能被分流到分隔线右侧，分隔符必须随实际相邻关系变化）。

    段由 ``src.tui.app._status_segments`` 注册表提供（每个段一个清单条目）：
    ``model`` 段为模型名部分（不与其它段用 `` · `` 连接），其余段（tools/
    elapsed/messages/tokens/speed）按声明顺序用 `` · `` 连接。各段自行门控
    可见性：tools/elapsed 仅活跃期渲染；tokens 为**常驻段**（空闲也显示
    全 Agent 累计输入/输出——2026-10-22 用户需求）；speed 在活跃期或压缩中/
    压缩结束宽限期内渲染（``_tokens_visible``，压缩摘要是静默后台流式调用，
    空闲期 ``/compact`` 产生的速度也要看得到）；messages 为常驻段（空闲也
    显示——2026-10-07 状态栏信息增强）。

    Args:
        model: AppModel 实例。
        dot_elapsed: 模型名点 FadeIn 渐显已流逝时间（BEAUTY-1，时间基）；
            >=duration 后返回呼吸色（动画结束）。
        spinner_char: 活跃状态指示字符（BEAUTY-7：streaming 时 60Hz spinner
            帧；空闲为静态 ``·``）。
        reasoning_effort: 当前生效模型的思考级别（low/medium/high/max，/models
            按模型配置；None 或空串不显示）。
        multimodal: 当前模型档案是否开启「多模态读图」（true 时模型名旁显示
            「图」徽标）。
        thinking_enabled: 全局思考模式开关（Ctrl+T；false 时模型名后显示
            ``[off]`` 替代思考级别标签）。

    Returns:
        ``StatusItems``（项列表 + 首项是否为模型名段）。
    """
    from ._status_segments import active_segment_ids, resolve_segment

    st = model.status
    # ★ P3（review）：与同函数其它字段（tool_total/tool_count 等）防御风格
    #   统一——测试桩模型缺字段时回退默认值而非 AttributeError。
    status_active = bool(getattr(st, "status_active", False))
    # ★ 2026-10（用户需求）：压缩消耗的速度要能在状态栏看到——speed 段除
    #   活跃期外，压缩中与压缩结束宽限期内也可见。
    # ★ 2026-10-22（用户需求）：token 入/出段**常驻**（空闲也显示全 Agent
    #   累计输入/输出），因此快照空闲也必须查询；1s TTL 与状态栏 1s 时间桶
    #   对齐，额外开销仅为一次 O(1) 快照读取。
    tokens_visible = _tokens_visible(status_active, st, time.monotonic())
    # 压缩/标题驱动的展示期（非活跃期）用短快照 TTL——压缩摘要结束后 token
    # 才入账，长 TTL 会让展示期缓存的旧快照拖慢显示；其余场景用 1s TTL。
    snap_ttl = (
        _COMPACTION_SNAPSHOT_TTL
        if (tokens_visible and not status_active)
        else _SNAPSHOT_TTL
    )
    snap = _snapshot(model, snap_ttl)
    ctx = StatusContext(
        model=model, status=st, status_active=status_active,
        dot_elapsed=dot_elapsed, spinner_char=spinner_char,
        reasoning_effort=reasoning_effort, snapshot=snap,
        tokens_visible=tokens_visible, multimodal=multimodal,
        thinking_enabled=thinking_enabled,
    )

    model_part: list[StyledRun] = []
    segment_runs: list[list[StyledRun]] = []
    for spec_id in active_segment_ids():
        # 单个插件段的导入/执行异常不得拖垮整条状态栏（修复前任一扩展段
        # 抛异常会让本帧状态栏构建整体失败）。
        try:
            fn = resolve_segment(spec_id)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "状态栏段导入失败: %s", spec_id, exc_info=True,
            )
            continue
        if fn is None:
            continue
        try:
            runs = fn(ctx) or []
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "状态栏段执行失败: %s", spec_id, exc_info=True,
            )
            continue
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
    items: list[list[StyledRun]] = []
    if model_part:
        items.append(model_part)
    items.extend(segment_runs)
    return StatusItems(items, bool(model_part))


def _item_separator(index: int, model_first: bool) -> StyledRun:
    """第 ``index`` 项**之前**的分隔 run（``index == 0`` 时为空串 run）。

    模型名段之后用双空格（沿用原视觉、不加分隔符），其余项之间用 `` · ``。
    """
    if index <= 0:
        return StyledRun("", None)
    if model_first and index == 1:
        return StyledRun(_STATUS_MODEL_GAP, None)
    return StyledRun(_STATUS_ITEM_SEP, _S_DIM)


def _join_status_items(items, model_first: bool = False,
                       start_index: int = 0) -> list[StyledRun]:
    """把一段项列表按分隔符连接为 runs。

    ``start_index`` 为片段在完整项列表中的起点——分隔符按项的**绝对位置**
    决定（模型段后为双空格、其余为 `` · ``），但片段**首项前不插分隔符**
    （片段可能从状态行中间或分隔线右侧开始，行首不应出现悬空分隔符）。

    Args:
        items: 项 runs 列表（片段）。
        model_first: 第 0 项是否为模型名段。
        start_index: 片段首项在完整列表中的下标。

    Returns:
        连接后的 runs。
    """
    runs: list[StyledRun] = []
    for offset, item in enumerate(items):
        index = start_index + offset
        if offset > 0:
            runs.append(_item_separator(index, model_first))
        runs.extend(item)
    return runs


def _layout_status_items(items, width, model_first: bool = False) -> StatusLayout:
    """按终端宽度把状态项分流到「状态行」与「顶部分隔线右侧」。

    规则（2026-10-21 用户需求）：从第一项开始按顺序尝试放入状态行（行首
    ``_STATUS_LINE_INDENT`` 缩进 + 项间分隔）；**整项**放不下即停——该项及其
    后所有项整体成为溢出片段，显示在顶部分隔线最右侧。因此状态行上的每一项
    都是完整显示的（不再出现被截断的半个项）。

    Args:
        items: ``StatusItems.items``（段 runs 列表）。
        width: 状态栏可用总宽（终端列宽，含行首缩进）。
        model_first: 第 0 项是否为模型名段。

    Returns:
        ``StatusLayout``（状态行 runs + 溢出项 runs；无溢出时后者为空列表）。
    """
    items = list(items or ())
    if not items:
        return StatusLayout([], [])
    try:
        width = int(width)
    except (TypeError, ValueError, OverflowError):
        width = 0
    if width <= 0:
        # 无宽度上下文：状态行放不下任何内容（与旧宽度截断语义一致）
        return StatusLayout([], _join_status_items(items, model_first))
    from src.tui.ink.helpers import _runs_total_width

    used = _STATUS_LINE_INDENT
    split_at = len(items)
    for index, item in enumerate(items):
        item_width = _runs_total_width(item)
        sep_width = 0
        if index > 0:
            sep_width = _runs_total_width([_item_separator(index, model_first)])
        if used + sep_width + item_width > width:
            split_at = index
            break
        used += sep_width + item_width
    return StatusLayout(
        _join_status_items(items[:split_at], model_first, 0),
        _join_status_items(items[split_at:], model_first, split_at),
    )


def _overflow_trailing_runs(overflow_runs, width) -> list[StyledRun]:
    """顶部分隔线右侧的溢出内容（行首留一格与 ``━`` 分隔；超宽时截断）。

    分隔线左侧至少保留 ``_STATUS_OVERFLOW_MIN_FILL`` 列（终端过窄时以整行
    为预算）。溢出内容本身仍超宽时按 ``…`` 截断（保持「还有更多信息」的
    视觉提示，不静默丢弃）。
    """
    runs = list(overflow_runs or ())
    if not runs:
        return []
    try:
        width = int(width)
    except (TypeError, ValueError, OverflowError):
        width = 0
    if width <= 0:
        return []
    budget = width
    if width > _STATUS_OVERFLOW_MIN_FILL:
        budget = width - _STATUS_OVERFLOW_MIN_FILL
    from src.tui.ink.helpers import truncate_runs_ellipsis

    return truncate_runs_ellipsis([StyledRun(" ", None)] + runs, budget)


def _build_status_runs(model, dot_elapsed: float = 0.0,
                       spinner_char: str = "\u00b7",
                       reasoning_effort: str | None = None,
                       multimodal: bool = False,
                       thinking_enabled: bool = True) -> list[StyledRun]:
    """构建状态文本 runs（段级连接后的扁平单行序列）。

    参数语义同 ``_build_status_items``。本函数是**不考虑宽度**的完整状态行
    （供自省 / 测试与不需要分流的调用方使用）；``StatusBar`` 渲染走
    ``_build_status_items`` + ``_layout_status_items``（窄屏时项会上移到顶部
    分隔线）。
    """
    built = _build_status_items(
        model, dot_elapsed, spinner_char, reasoning_effort,
        multimodal, thinking_enabled,
    )
    return _join_status_items(built.items, built.model_first)


def _glow(lo: int, hi: int, period: float) -> int:
    """状态点呼吸色（时间基正弦插值）。参数语义与 ``time_glow(lo, hi, period)`` 一致。"""
    return time_glow(lo, hi, period)


def StatusBar(props) -> object:
    """StatusBar 组件：分割线一行在上，状态文本一行在下。

    PERF-3：``use_memo`` 缓存 ``_build_status_items(model)`` 结果——
    deps = 状态关键字段快照 + 时间桶（BEAUTY-1：渐显窗口内 0.1s 桶，
    结束后 1s 桶）。字段快照须显式列出（不能传整个 model/status 对象，
    否则恒变 → 缓存失效）。

    窄屏溢出（2026-10-21 用户需求）：``_layout_status_items`` 按 width 把
    状态项分流——状态行只保留能**完整**显示的项，放不下的项整体显示在顶部
    分隔线最右侧（``Divider`` 的 ``trailing``）；状态行因此不再出现被截断的
    半个项。
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
    #   呼吸以 60Hz 平滑推进（流式期间帧率本就 60Hz，零额外渲染成本）；
    #   空闲非渐显期回 1s 桶（静态显示，CPU 保持低占用）。
    if st_active:
        time_dep = int(time.monotonic() / 0.1)
        # BEAUTY-7：streaming spinner 帧（60Hz）——spinner_frame 返回帧索引，
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
    # ★ 思考级别（/models 中按模型配置 low/medium/high/max；空则回退全局
    #   默认）：模型名后显示 [level]。读取当前生效档案的有效值（与 API 请求
    #   同源，列表规模小，每帧开销可忽略；异常回退空串不显示）。作为
    #   use_memo deps——/models 切换档案或保存新值后下帧 deps 变化 →
    #   状态栏即时刷新（无需额外同步链路）。
    reasoning_effort = _cached_reasoning_effort(st_model_name)
    # ★ 多模态读图徽标（2026-10-19 用户需求）：模型名旁「图」字——与思考级别
    #   同样短 TTL 缓存；/models 切换档案或修改开关后下帧自动刷新。
    multimodal = _cached_multimodal(st_model_name)
    # ★ 思考模式开关（Ctrl+T）：关闭时模型名后显示 [off]；短 TTL 缓存，
    #   切换后 ≤0.5s 自动刷新（作为 use_memo deps 变化触发重建）。
    thinking_enabled = _cached_thinking_enabled()
    status_items = use_memo(
        lambda: _build_status_items(
            model, dot_elapsed, spinner_char, reasoning_effort, multimodal,
            thinking_enabled,
        ),
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
            # ★ 思考级别（2026-08-14）：/models 切换档案或保存新值后 deps
            #   变化 → 重建状态行（模型名后 [level] 即时刷新）。
            reasoning_effort,
            # ★ 多模态读图（2026-10-19）：/models 修改开关后 deps 变化 →
            #   模型名旁「图」徽标即时出现/消失。
            multimodal,
            # ★ 思考模式开关（Ctrl+T）：切换后 [off] 标签即时出现/消失。
            thinking_enabled,
            # ★ 2026-10（用户需求：压缩消耗的 token 要能在状态栏看到）：
            #   压缩状态变化（开始/结束）立即重建状态行——空闲期压缩中与
            #   压缩结束宽限期内 tokens/speed 段的出现/消失即时生效
            #   （仅 compaction_active 需入 deps；宽限期的到期由 time_dep
            #   的 1 秒桶驱动，最迟 1 秒内收起）。
            getattr(st, "compaction_active", 0),
            # ★ 2026-10-20（用户需求：标题消耗的 tok / tok/s 要记录到 status 行）：
            #   标题生成中 / 结束后宽限期内 tokens+speed 段可见——两项都要进
            #   deps（否则 memo 命中旧结果，段的出现与收起都不会刷新）。
            getattr(st, "title_generating", False),
            getattr(st, "title_last_end_ts", 0.0),
            # ★ 2026-10-21（/loop 续跑提词）：生成中 / 结束后宽限期内
            #   tokens+speed 段可见——同样进 deps 以保证即时出现/收起。
            getattr(st, "loop_prompt_generating", False),
            getattr(st, "loop_prompt_last_end_ts", 0.0),
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
    # ★ 2026-10-21（用户需求：终端过窄时显示不完整的项去最顶分隔线最右侧）：
    #   按 width 把状态项分流——状态行只保留**完整**放得下的项，其余项整体
    #   上移到顶部分隔线右侧（deps = items 引用 + width；width 变化即重排）。
    layout = use_memo(
        lambda: _layout_status_items(
            status_items.items, width, status_items.model_first,
        ),
        (status_items, width),
    )
    # 状态行（下面）
    # ★ 性能（PERF-10）：状态行 Line **缓存**（use_memo 键 layout 引用）
    status_line = use_memo(
        lambda: _build_status_line(layout.line_runs),
        (layout,),
    )
    # ★ 方向4（状态行溢出截断）：防御性兜底——分流后正常不超宽；仅当宽度
    #   上下文异常（如 CJK 簇测宽差异）时截断，保持行宽不变量。
    if width > 0 and status_line.width > width:
        from src.tui.ink.helpers import truncate_line
        status_line = truncate_line(status_line, width)
    # 顶部分隔线右侧的溢出内容（行首留一格；仍超宽时 ``…`` 截断）。
    trailing_runs = use_memo(
        lambda: _overflow_trailing_runs(layout.overflow_runs, width),
        (layout, width),
    )
    from src.tui.ink.widgets.display import Divider
    divider_props = {"width": width, "char": "\u2501", "style": sep_style}
    if trailing_runs:
        # 右侧溢出项（Divider 按「左侧填充 + 右侧内容」布局，行宽恒 = width）
        divider_props["trailing"] = trailing_runs
    divider = h(Divider, divider_props)
    # ★ 方向4（空状态压缩 / 极窄屏全溢出）：状态行无完整项可显示时只渲染
    #   分隔线一行（溢出内容仍在分隔线右侧）。
    if not layout.line_runs:
        return h(Column, None, [divider])
    return h(Column, None, [
        divider,
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


__all__ = ["StatusBar", "_SPINNER_FRAMES"]
