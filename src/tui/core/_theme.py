"""core/_theme — app 组件共享样式与时间基 glow（Layer 0，公共工具层）。

从 ``app/_theme.py`` 下沉（2026-08-05 重构：公共动画/样式工具归位 core 层）：
共享不可变样式常量池、语义化调色板注册表、``time_glow`` 呼吸色、``sep_style``
分隔线样式——均为无 app 组件依赖的公共工具。``app/_theme.py`` 保持 re-export
存根（旧导入路径 + 测试 patch 路径兼容）并保留 ``sep_line``（依赖 ink.output，
属 UI 组件层，不下沉）；``_subagent_render``（被 core/parallel_executor 依赖）
改从本模块引用，消除「subagent 渲染 → app 域」的分层倒置。

内容：

  - 共享不可变样式常量池（享元模式）：_S_ACCENT / _S_ACCENT_BOLD /
    _S_DIM / _S_SEP / _S_TIME，跨组件复用零拷贝。
  - 语义化调色板（Palette / ThemeRegistry / resolve_theme /
    get_active_palette / _invalidate_palette_cache）：dark/light/
    high-contrast 三套主题。
  - time_glow()：时间基正弦插值呼吸色号，替代 AnimatorContext 帧基
    sine_color（原帧计数恒为 0 → 观感静态；时间基才能产生真实呼吸，
    对齐 Claude Code React Ink 观感）。
  - sep_style()：分隔线样式（活跃呼吸 / 空闲静态，PERF-11 对象稳定性）。

依赖约束：仅依赖 src/tui/_const（语义色槽，Layer 0 常量）、
src/tui/core.style.Style 与标准库（math/time），不依赖 _animator、不依赖
任何 app 组件，可独立导入。

方向6 步骤6.4 评估结论（配色）：dark 主题已对齐 ``_SEMANTIC_COLOR`` 槽位
（方向3 步骤15 收敛），light/high-contrast 主题族已注册；无调整需求 →
**评估不做**（记录于 docstring 可追溯）。
"""

from __future__ import annotations

import math
import time
from functools import lru_cache

from src._compat import dataclass
from src.tui._const import _SEMANTIC_COLOR
from src.tui.core.style import Style

# ── 共享样式常量池 ────────────────────────────────────────────
_S_ACCENT = Style(fg=45)                 # 强调色（亮青）
_S_ACCENT_BOLD = Style(fg=45, bold=True)  # 强调色加粗
_S_DIM = Style(fg=242)                   # 弱化色（暗灰）
_S_SEP = Style(fg=237)                   # 分隔线色（深灰）
_S_TIME = Style(fg=110)                  # 时间戳色（浅蓝）

# 方向C 步骤4：收敛 apply.py / input_area.py 多处使用的样式常量（享元共享池）
_S_USER_ICON = Style(fg=81, bold=True)   # 用户消息图标 `>`
_S_USER_TEXT = Style(fg=252)             # 用户消息文本
_S_NOTICE = Style(fg=242)                # 通知/助手消息前缀
_S_TEXT = Style(fg=252)                  # 输入区输入文本


# ═══════════════════════════════════════════════════════════
# 语义化调色板注册表（Claude TUI parity 步骤 1.1）
# ═══════════════════════════════════════════════════════════
# 从散落硬编码（_theme/_const/各组件）提取统一语义色槽；dark 各槽值
# 与现有 _S_*/_C_* 常量数值完全一致（零视觉回归），light/high-contrast
# 为新增主题族。现有 _S_* 常量保留为 dark palette 对应槽的别名。

#: 语义色槽名称（Palette 字段），供 resolve_theme/ThemeRegistry 消费。
_PALETTE_SLOTS: tuple[str, ...] = (
    "accent", "accent_bold", "dim", "sep", "time",
    "user_icon", "user_text", "notice", "text",
    "token", "speed", "tool_ok", "tool_fail", "tool_running",
    "border", "code_bg", "selection_bg", "selection_fg", "placeholder",
)


@dataclass(frozen=True)
class Palette:
    """语义化调色板（冻结，不可变）。

    每个字段为语义色槽对应的 ``Style``。dark 各槽与既有常量值一致，
    light/high-contrast 为独立主题族（组件经 ``get_active_palette()``
    按需解析，暗色下渲染结果与硬编码现状逐字节一致）。

    方向3 步骤15（样式/颜色单一真源）：dark 各槽中与 ``_SEMANTIC_COLOR``
    槽位表共有的语义色改从槽位读取（唯一真源防漂移），值与既有 ``_S_*``
    常量完全一致（零视觉回归）；light/high-contrast 为独立主题族不引用槽位。
    """

    accent: Style = Style(fg=_SEMANTIC_COLOR["accent"])
    accent_bold: Style = Style(fg=_SEMANTIC_COLOR["accent"], bold=True)
    dim: Style = Style(fg=_SEMANTIC_COLOR["dim"])
    sep: Style = Style(fg=_SEMANTIC_COLOR["sep"])
    time: Style = Style(fg=_SEMANTIC_COLOR["time"])
    user_icon: Style = _S_USER_ICON
    user_text: Style = _S_USER_TEXT
    notice: Style = _S_NOTICE
    text: Style = _S_TEXT
    token: Style = Style(fg=_SEMANTIC_COLOR["token"])
    speed: Style = Style(fg=_SEMANTIC_COLOR["speed"])
    tool_ok: Style = Style(fg=_SEMANTIC_COLOR["tool_ok"])
    tool_fail: Style = Style(fg=_SEMANTIC_COLOR["tool_fail"])
    tool_running: Style = Style(fg=_SEMANTIC_COLOR["speed"])
    border: Style = Style(fg=_SEMANTIC_COLOR["border"])
    code_bg: Style = Style(bg=235)
    selection_bg: Style = Style(bg=_SEMANTIC_COLOR["select_bg"])
    selection_fg: Style = Style(fg=_SEMANTIC_COLOR["select_fg"])
    placeholder: Style = Style(fg=_SEMANTIC_COLOR["placeholder"])


def _light_palette() -> Palette:
    """亮色主题（暗字亮底）。"""
    return Palette(
        accent=Style(fg=30), accent_bold=Style(fg=30, bold=True),
        dim=Style(fg=244), sep=Style(fg=250), time=Style(fg=60),
        user_icon=Style(fg=27, bold=True), user_text=Style(fg=234),
        notice=Style(fg=244), text=Style(fg=234),
        token=Style(fg=25), speed=Style(fg=130),
        tool_ok=Style(fg=28), tool_fail=Style(fg=124), tool_running=Style(fg=130),
        border=Style(fg=240), code_bg=Style(bg=253),
        selection_bg=Style(bg=189), selection_fg=Style(fg=0),
        placeholder=Style(fg=244),
    )


def _high_contrast_palette() -> Palette:
    """高对比主题（最大色差）。"""
    return Palette(
        accent=Style(fg=39), accent_bold=Style(fg=39, bold=True),
        dim=Style(fg=250), sep=Style(fg=252), time=Style(fg=69),
        user_icon=Style(fg=33, bold=True), user_text=Style(fg=15),
        notice=Style(fg=250), text=Style(fg=15),
        token=Style(fg=45), speed=Style(fg=214),
        tool_ok=Style(fg=47), tool_fail=Style(fg=196), tool_running=Style(fg=214),
        border=Style(fg=15), code_bg=Style(bg=236),
        selection_bg=Style(bg=22), selection_fg=Style(fg=15),
        placeholder=Style(fg=250),
    )


class ThemeRegistry:
    """主题注册表（按名解析 Palette，不可变）。"""

    _themes: dict[str, Palette] = {
        "dark": Palette(),
        "light": _light_palette(),
        "high-contrast": _high_contrast_palette(),
    }

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(cls._themes.keys())

    @classmethod
    def get(cls, name: str) -> Palette | None:
        return cls._themes.get(name)

    @classmethod
    def resolve(cls, name: str) -> Palette:
        """按名解析调色板；未知名回退 dark（零回归安全侧）。"""
        return cls._themes.get(name, cls._themes["dark"])


def resolve_theme(name: str) -> Palette:
    """按名解析调色板（未知名回退 dark）。"""
    return ThemeRegistry.resolve(name)


#: 活动调色板 TTL 缓存（≤1Hz 刷新；读 config THEME 键）
_ACTIVE_PALETTE_TTL = 1.0
_active_palette_cache: tuple[float, Palette] = (0.0, ThemeRegistry.resolve("dark"))


def get_active_palette() -> Palette:
    """返回当前活动调色板（读 config THEME，TLR 缓存 1s）。

    config 读取失败/未加载 → 回退 dark（零回归安全侧）。
    """
    global _active_palette_cache
    now = time.monotonic()
    if now - _active_palette_cache[0] < _ACTIVE_PALETTE_TTL:
        return _active_palette_cache[1]
    theme = "dark"
    try:
        from src.config.proxy import config
        value = config.get("theme", "dark")
        if isinstance(value, str) and value:
            theme = value
    except Exception:
        pass
    pal = resolve_theme(theme)
    _active_palette_cache = (now, pal)
    return pal


def _invalidate_palette_cache() -> None:
    """使活动调色板缓存失效（/theme 切换后强制重解析）。"""
    global _active_palette_cache
    # 方向2（TTL 边界修复）：失效时缓存值保持当前活动 palette（不硬编码
    # dark）——修复前置 ``(0.0, resolve("dark"))`` 在进程启动 1s 内
    # （``now - 0 < TTL`` 判定）误返回 dark；保持当前 palette 使 TTL 边界
    # 处仍返回既有主题。正常（进程运行 >1s）时时间戳 0 使缓存立即过期 →
    # 下个 get 重读 config 返回新主题。
    _active_palette_cache = (0.0, get_active_palette())


def time_glow(lo: int, hi: int, period: float = 12.0) -> int:
    """时间基正弦插值呼吸色号。

    基于 ``time.monotonic()`` 计算正弦插值，返回值钳制在 [lo, hi] 区间。
    与 AnimatorContext 帧基 glow 不同：时间基与渲染帧率无关，
    即使帧计数恒为 0 也能产生连续呼吸观感。

    PERF-5：0.1s 时间桶缓存——同一时间桶（``int(t/0.1)``）且同 (lo,hi,period)
    参数时返回缓存色号（每帧调用不重复计算正弦）。

    方向6（多桶缓存）：内部经 ``_glow_bucket`` lru_cache（maxsize=32）——
    input_area 与 status_bar 不同 (lo,hi,period) 参数不再互相覆盖单桶缓存
    （修复前单桶互相覆盖导致频繁重算）；桶切换（0.1s）后 key 变化自动失效。

    Args:
        lo: 呼吸下限色号。
        hi: 呼吸上限色号。
        period: 呼吸周期（秒），默认 12 秒。

    Returns:
        [lo, hi] 区间内的 256 色号整数。
    """
    bucket = int(time.monotonic() / 0.1)
    return _glow_bucket(lo, hi, period, bucket)


@lru_cache(maxsize=32)
def _glow_bucket(lo: int, hi: int, period: float, bucket: int) -> int:
    """0.1s 时间桶内计算呼吸色号（多参数多桶缓存，互不覆盖）。

    bucket 为 ``int(time.monotonic() / 0.1)``——同一参数同一时间桶命中缓存
    （key 含 lo/hi/period/bucket，不同参数不互相污染）；桶切换后 bucket 变化
    自动失效；maxsize=32 防无限增长。桶内代表时间取桶中点
    （``(bucket + 0.5) * 0.1``，单调稳定）。
    """
    t = (bucket + 0.5) * 0.1
    ratio = (math.sin(2 * math.pi * t / period) + 1) / 2
    return max(lo, min(hi, lo + int((hi - lo) * ratio)))


#: 分隔线呼吸色（活跃期青色呼吸 32-45，8s 周期）——input_area 上下分隔线
#: 与 status_bar 分隔线共用同一周期/色域（视觉联动）。集中为常量避免三处
#: 内联漂移（方向5 收敛）。
_SEP_BREATH_LO = 32
_SEP_BREATH_HI = 45
_SEP_BREATH_PERIOD = 8.0


@lru_cache(maxsize=64)
def _sep_style_active(bucket: int) -> Style:
    """活跃期分隔线 Style（0.1s 时间桶缓存 Style **对象**）。

    ★ 性能（PERF-11 落地）：status_bar 的 ``sep`` use_memo deps 为
    ``(width, sep_style)``——`sep_style` 活跃期若每次新建 Style 对象，
    use_memo 依赖比较（``_deps_equal`` → ``_object_is``，对 Style 仅做
    ``is`` 引用比较）永远 miss → 分隔线 Line 每帧重建（PERF-11 声称的
    缓存实际未生效）。经本函数缓存后同桶返回**同一 Style 实例** → use_memo
    引用比较命中 → 分隔线 Line 跨帧复用；跨桶（呼吸色变化）自然重建。
    桶号 ``int(time.monotonic()/0.1)`` 与 ``time_glow`` 桶粒度一致。
    """
    return Style(fg=_glow_bucket(
        _SEP_BREATH_LO, _SEP_BREATH_HI, _SEP_BREATH_PERIOD, bucket,
    ))


def sep_style(active: bool) -> Style:
    """分隔线样式（通用组件，方向5 收敛）：活跃呼吸 / 空闲静态。

    流式/活跃期间返回青色呼吸 Style（``time_glow(32, 45, 8.0)``，8s 周期，
    与 status_bar 分隔线同步）；空闲返回静态深灰 ``_S_SEP``（零额外渲染
    成本）。供 input_area 上下分隔线 / status_bar 分隔线统一调用——修复前
    三处各自 ``Style(fg=time_glow(32, 45, 8.0))`` 内联（周期/色域漂移风险）。

    ★ 对象稳定性契约（PERF-11）：活跃期**同一 0.1s 桶内返回同一 Style
    实例**（经 ``_sep_style_active`` 缓存）——调用方用 Style 对象作
    ``use_memo`` deps（status_bar sep）时引用比较可命中，跨帧复用分隔线
    Line；跨桶返回新实例（呼吸色更新）。空闲返回模块级常量 ``_S_SEP``
    （恒同对象）。

    Args:
        active: 是否活跃（流式/工具运行等）。

    Returns:
        分隔线填充 Style。
    """
    if not active:
        return _S_SEP
    bucket = int(time.monotonic() / 0.1)
    return _sep_style_active(bucket)


__all__ = [
    "_S_ACCENT",
    "_S_ACCENT_BOLD",
    "_S_DIM",
    "_S_SEP",
    "_S_TIME",
    "_S_USER_ICON",
    "_S_USER_TEXT",
    "_S_NOTICE",
    "_S_TEXT",
    "time_glow",
    "sep_style",
    "Palette",
    "ThemeRegistry",
    "resolve_theme",
    "get_active_palette",
    "_invalidate_palette_cache",
    "_PALETTE_SLOTS",
]
