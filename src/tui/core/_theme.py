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
import threading
import time
from dataclasses import fields as _dc_fields
from functools import lru_cache
from typing import Any, Callable

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


# ★ P3（review）：``_PALETTE_SLOTS`` 与 ``Palette`` 字段集一致性断言——修复前
#   两者仅靠人工对齐（新增字段时无校验，易漂移）。契约：槽位表是 Palette 的
#   子集（Palette 可含未列入槽位表的辅助字段，反之不允许）。
assert set(_PALETTE_SLOTS) <= {
    f.name for f in _dc_fields(Palette)
}, "_PALETTE_SLOTS 含 Palette 不存在的槽位"


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


def _nord_palette() -> Palette:
    """Nord 主题（冷色调，蓝灰基底——Nord 官方色板 256 色近似）。"""
    return Palette(
        accent=Style(fg=110), accent_bold=Style(fg=110, bold=True),
        dim=Style(fg=244), sep=Style(fg=238), time=Style(fg=66),
        user_icon=Style(fg=74, bold=True), user_text=Style(fg=253),
        notice=Style(fg=244), text=Style(fg=253),
        token=Style(fg=109), speed=Style(fg=222),
        tool_ok=Style(fg=150), tool_fail=Style(fg=174), tool_running=Style(fg=222),
        border=Style(fg=240), code_bg=Style(bg=236),
        selection_bg=Style(bg=60), selection_fg=Style(fg=15),
        placeholder=Style(fg=244),
    )


def _dracula_palette() -> Palette:
    """Dracula 主题（暗紫底 + 高饱和糖果色——Dracula 官方色板 256 色近似）。"""
    return Palette(
        accent=Style(fg=141), accent_bold=Style(fg=141, bold=True),
        dim=Style(fg=103), sep=Style(fg=60), time=Style(fg=117),
        user_icon=Style(fg=212, bold=True), user_text=Style(fg=255),
        notice=Style(fg=103), text=Style(fg=255),
        token=Style(fg=141), speed=Style(fg=215),
        tool_ok=Style(fg=84), tool_fail=Style(fg=203), tool_running=Style(fg=215),
        border=Style(fg=61), code_bg=Style(bg=236),
        selection_bg=Style(bg=60), selection_fg=Style(fg=15),
        placeholder=Style(fg=103),
    )


def _gruvbox_palette() -> Palette:
    """Gruvbox 主题（暖色复古——Gruvbox dark 官方色板 256 色近似）。"""
    return Palette(
        accent=Style(fg=108), accent_bold=Style(fg=108, bold=True),
        dim=Style(fg=245), sep=Style(fg=237), time=Style(fg=66),
        user_icon=Style(fg=109, bold=True), user_text=Style(fg=223),
        notice=Style(fg=245), text=Style(fg=223),
        token=Style(fg=108), speed=Style(fg=214),
        tool_ok=Style(fg=106), tool_fail=Style(fg=167), tool_running=Style(fg=214),
        border=Style(fg=240), code_bg=Style(bg=235),
        selection_bg=Style(bg=58), selection_fg=Style(fg=15),
        placeholder=Style(fg=245),
    )


class ThemeRegistry:
    """主题注册表（按名解析 Palette，不可变）。

    「一切皆插件」：内置主题（dark/light/high-contrast）的**声明**集中在本
    模块的规格表中，每一项都由清单中的**独立插件条目**（``theme``，经
    ``src.plugins.theme_entries``）显式注册，因而可被 Profile/Bundle 声明，
    也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换。无清单（单元测试、独立
    调用）时无接管，全部内置主题默认生效（向后兼容）。
    """

    @classmethod
    def names(cls) -> tuple[str, ...]:
        """当前生效的主题名（内置 + 扩展，内置按声明顺序在前）。"""
        return tuple(active_theme_factories().keys())

    @classmethod
    def get(cls, name: str) -> Palette | None:
        return _get_theme_palette(name)

    @classmethod
    def resolve(cls, name: str) -> Palette:
        """按名解析调色板；未知名回退 dark（零回归安全侧）。"""
        palette = _get_theme_palette(name)
        if palette is not None:
            return palette
        return _get_theme_palette("dark") or Palette()


#: 内置主题声明（id → 工厂）——每项由清单中的独立插件条目注册。
_BUILTIN_THEME_SPECS: dict[str, Callable[[], Palette]] = {
    "dark": lambda: Palette(),
    "light": _light_palette,
    "high-contrast": _high_contrast_palette,
    "nord": _nord_palette,
    "dracula": _dracula_palette,
    "gruvbox": _gruvbox_palette,
}

_theme_lock = threading.RLock()
_ABSENT = object()
_registered_builtin_themes: dict[str, Callable[[], Palette]] = {}
_managed_builtin_themes: set = set()
_disabled_builtin_themes: set = set()
_extension_themes: dict[str, Callable[[], Palette]] = {}
#: 主题名 → Palette 实例缓存（保持「主题调色板实例长期稳定」既有语义，
#: 组件以 palette 字段作 use_memo 依赖时引用长期命中）
_palette_instances: dict[str, Palette] = {}


def builtin_theme_names() -> list[str]:
    """全部内置主题名（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_THEME_SPECS)


def _normalize_theme_ids(ids) -> list[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: list[str] = []
    for item in ids or ():
        if item not in _BUILTIN_THEME_SPECS:
            raise KeyError(f"未知内置主题: {item!r}（可用: {list(_BUILTIN_THEME_SPECS)}）")
        selected.append(item)
    return selected


def active_theme_factories() -> dict[str, Callable[[], Palette]]:
    """当前生效的主题工厂（内置 + 扩展；内置按声明顺序在前）。"""
    with _theme_lock:
        result: dict[str, Callable[[], Palette]] = {}
        for name, default in _BUILTIN_THEME_SPECS.items():
            if name in _disabled_builtin_themes:
                continue
            override = _registered_builtin_themes.get(name)
            if override is not None:
                result[name] = override
                continue
            if name in _managed_builtin_themes:
                continue
            result[name] = default
        for name, factory in _extension_themes.items():
            result[name] = factory
        return result


def _invalidate_palette_instances() -> None:
    with _theme_lock:
        _palette_instances.clear()


def _get_theme_palette(name: str) -> Palette | None:
    """按名取得（缓存的）调色板实例；不存在返回 None。"""
    with _theme_lock:
        cached = _palette_instances.get(name)
        if cached is not None:
            return cached
        factory = active_theme_factories().get(name)
        if factory is None:
            return None
        palette = factory()
        _palette_instances[name] = palette
        return palette


def register_builtin_theme(name: str, factory: Any = None) -> Callable[[], None]:
    """注册/覆盖一个内置主题（``factory=None`` 用默认工厂）；返回幂等撤销。"""
    if name not in _BUILTIN_THEME_SPECS:
        raise KeyError(f"未知内置主题: {name!r}（可用: {list(_BUILTIN_THEME_SPECS)}）")
    with _theme_lock:
        previous = _registered_builtin_themes.get(name, _ABSENT)
        _registered_builtin_themes[name] = factory if factory is not None else _BUILTIN_THEME_SPECS[name]
    _invalidate_palette_instances()

    def _undo() -> None:
        with _theme_lock:
            if previous is _ABSENT:
                _registered_builtin_themes.pop(name, None)
            else:
                _registered_builtin_themes[name] = previous
        _invalidate_palette_instances()

    return _undo


def unregister_builtin_theme(name: str) -> bool:
    with _theme_lock:
        removed = _registered_builtin_themes.pop(name, None) is not None
    if removed:
        _invalidate_palette_instances()
    return removed


def set_managed_builtin_themes(ids) -> Callable[[], None]:
    """声明这些内置主题 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_theme_ids(ids)
    with _theme_lock:
        added = [item for item in selected if item not in _managed_builtin_themes]
        _managed_builtin_themes.update(added)
    _invalidate_palette_instances()

    def _undo() -> None:
        with _theme_lock:
            for item in added:
                _managed_builtin_themes.discard(item)
        _invalidate_palette_instances()

    return _undo


def managed_theme_names() -> list[str]:
    with _theme_lock:
        return sorted(_managed_builtin_themes)


def disable_builtin_themes(ids) -> Callable[[], None]:
    """禁用一个或多个内置主题（返回幂等撤销）。"""
    selected = _normalize_theme_ids(ids)
    with _theme_lock:
        added = [item for item in selected if item not in _disabled_builtin_themes]
        _disabled_builtin_themes.update(added)
    _invalidate_palette_instances()

    def _undo() -> None:
        with _theme_lock:
            for item in added:
                _disabled_builtin_themes.discard(item)
        _invalidate_palette_instances()

    return _undo


def register_theme(name: str, factory: Callable[[], Palette]) -> Callable[[], None]:
    """注册一个扩展主题（``factory() -> Palette``）；返回幂等撤销。"""
    if not isinstance(name, str) or not name:
        raise ValueError(f"主题名必须是非空字符串: {name!r}")
    if not callable(factory):
        raise TypeError(f"主题工厂必须可调用: {factory!r}")
    with _theme_lock:
        previous = _extension_themes.get(name, _ABSENT)
        _extension_themes[name] = factory
    _invalidate_palette_instances()

    def _undo() -> None:
        with _theme_lock:
            if previous is _ABSENT:
                _extension_themes.pop(name, None)
            else:
                _extension_themes[name] = previous
        _invalidate_palette_instances()

    return _undo


def unregister_theme(name: str) -> bool:
    with _theme_lock:
        removed = _extension_themes.pop(name, None) is not None
    if removed:
        _invalidate_palette_instances()
    return removed


def theme_factories() -> dict[str, Callable[[], Palette]]:
    with _theme_lock:
        return dict(_extension_themes)


def clear() -> None:
    """清空扩展主题与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _theme_lock:
        _extension_themes.clear()
        _registered_builtin_themes.clear()
    _invalidate_palette_instances()


def reset() -> None:
    """重置全部主题状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _theme_lock:
        _extension_themes.clear()
        _registered_builtin_themes.clear()
        _managed_builtin_themes.clear()
        _disabled_builtin_themes.clear()
    _invalidate_palette_instances()


def resolve_theme(name: str) -> Palette:
    """按名解析调色板（未知名回退 dark）。"""
    return ThemeRegistry.resolve(name)


#: 活动调色板 TTL 缓存（≤1Hz 刷新；读 config THEME 键）
#: ★ P3-20：无锁读写——全局缓存为单字段赋值（GIL 原子性）且仅主线程
#:   （render）/命令线程（/theme 切换）读写，原子替换元组可接受（无中间态）。
#: ★ 修复（P2-9）：初始为 None（未初始化）——启动后首次调用直接读配置
#:   （跳过 TTL），修复前初始 ``(0.0, dark)`` 使进程启动 1s 内恒返回 dark
#:   （config light/high-contrast 不生效）。
_ACTIVE_PALETTE_TTL = 1.0
_active_palette_cache: tuple[float, Palette] | None = None


def _read_theme() -> str:
    """读取 config THEME 值（读取失败/未加载回退 dark）。"""
    theme = "dark"
    try:
        from src.config.proxy import config
        value = config.get("theme", "dark")
        if isinstance(value, str) and value:
            theme = value
    except Exception:
        pass
    return theme


def get_active_palette() -> Palette:
    """返回当前活动调色板（读 config THEME，TTL 缓存 1s）。

    config 读取失败/未加载 → 回退 dark（零回归安全侧）。
    """
    global _active_palette_cache
    now = time.monotonic()
    if _active_palette_cache is not None:
        if now - _active_palette_cache[0] < _ACTIVE_PALETTE_TTL:
            return _active_palette_cache[1]
    pal = resolve_theme(_read_theme())
    _active_palette_cache = (now, pal)
    return pal


def _invalidate_palette_cache() -> None:
    """使活动调色板缓存失效（/theme 切换后强制重解析）。

    ★ 修复（P2-9）：失效时**直接读配置解析新主题**并缓存（时间戳 0.0 使
    下个 get 立即重读，双保险）——修复前 ``(0.0, get_active_palette())``：
    1) 递归读取的仍是失效前的旧主题；2) 时间戳 0 使失效后 1s 内（TTL 边界）
    仍返回旧主题（新主题不生效）。
    """
    global _active_palette_cache
    _active_palette_cache = (0.0, resolve_theme(_read_theme()))


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

    ★ P1-2：入口防御校验——``period <= 0`` 时 ``math.sin`` 除零抛
    ZeroDivisionError（无校验），显式抛 ValueError 明确报错（lru_cache
    不缓存异常结果）；``lo > hi`` 时静默返回 lo 恒值（``max(lo, min(hi, ...))``
    语义混乱），改为交换 lo/hi 宽容处理。
    """
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")
    if lo > hi:
        lo, hi = hi, lo
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
    "builtin_theme_names",
    "active_theme_factories",
    "register_builtin_theme",
    "unregister_builtin_theme",
    "set_managed_builtin_themes",
    "managed_theme_names",
    "disable_builtin_themes",
    "register_theme",
    "unregister_theme",
    "theme_factories",
    "clear",
    "reset",
]
