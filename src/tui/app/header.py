"""TopHeader — 顶部标题栏组件（Claude Code 视觉对齐）。

渲染 ``✦ DeepSeek CLI · v2.2.0``：
  - ``✦`` 亮色（品红系 fg=213）**时间基呼吸**（BEAUTY-7：0.1s 时间桶
    ``time_glow`` 在品红邻域脉动；流式/活跃期间随 10Hz 渲染平滑呼吸）。
  - ``DeepSeek CLI`` 空间渐变：逐字符 ``lerp_color`` 插值，色标
    ``[45, 39, 141, 213]``（青 → 蓝 → 紫 → 品红）。
  - 版本号 dim（fg=242），版本号经 ``src.app_init._args.VERSION`` 导入。
  - ★ BEAUTY-31（2026-08-05）：版本号独立 TEXT——活跃期（status_active）
    呼吸（暗灰 242↔252，8s 周期，与工具卡 detail/模型名呼吸同步），空闲
    静态 242（零额外渲染成本）。

性能：渐变 runs 与终端宽度无关（内容固定）→ ``use_memo(deps=())`` 缓存
同一 ``styled`` 列表引用 → TEXT ``_paint_cache``（键含 styled 引用）跨帧
复用 Line 对象 → diff 身份短路（每帧零重建）。呼吸 ✦ 独立 TEXT 元素——
渐变/版本 runs 缓存不被呼吸刷新（修复前单 TEXT 呼吸会每 0.1s 全量重建
渐变 runs）。★ BEAUTY-31：版本号同样独立 TEXT——呼吸不污染渐变缓存。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, StyledRun, h, Row, use_memo
from src.tui.app._theme import time_glow

#: 渐变标题色标（青 → 蓝 → 紫 → 品红）
_GRADIENT_STOPS = (45, 39, 141, 213)

#: ✦ 呼吸色域（品红 213 邻域脉动）
_DOT_LO = 205
_DOT_HI = 219
_DOT_PERIOD = 6.0

#: 版本号呼吸色域（暗灰 242↔252，8s 周期——与工具卡 detail/模型名呼吸同步）
_VER_LO = 242
_VER_HI = 252
_VER_PERIOD = 8.0

#: 版本号空闲静态 runs（P3-4：模块级单例缓存——首次空闲渲染时惰性构建
#   （VERSION 须函数内惰性导入：src/app_init/_args 经 config→tui 链存在
#   模块加载循环，模块级导入会 ImportError——原实现即函数内导入），后续帧
#   返回同一引用跨帧复用，diff 身份短路零重建；None=未构建）。
_VERSION_RUNS_STATIC: list | None = None


def _gradient_runs(text: str) -> list[StyledRun]:
    """逐字符空间渐变（青→蓝→紫→品红，per-char lerp_color 插值）。

    ★ 标准控件收敛（阶段3）：渐变算法单一真源迁入 Gradient 控件
    （``src/tui/ink/widgets/gradient.py::_gradient_runs``）——header 与本模块
    保留薄委托（兼容既有 patch/调用面；色标取本模块 ``_GRADIENT_STOPS``）。
    输出等价（per-char lerp_color 插值）。
    """
    from src.tui.ink.widgets.gradient import _gradient_runs as _g
    return _g(text, _GRADIENT_STOPS)


def _title_runs() -> list[StyledRun]:
    """构建渐变标题 runs（不含版本号——独立 TEXT 元素，BEAUTY-31 呼吸）。"""
    return _gradient_runs("DeepSeek CLI")


def _version_runs(active: bool) -> list[StyledRun]:
    """版本号 runs（活跃期呼吸 / 空闲静态 242）。

    ★ BEAUTY-31（体验动效）：活跃期版本号暗灰 242→252 脉动（8s 周期，与
    工具卡 detail 呼吸同步）；空闲静态 242——返回模块级单例
    ``_VERSION_RUNS_STATIC``（P3-4：首次空闲渲染惰性构建后同一引用跨帧复用，
    diff 身份短路零额外成本；修复前每次渲染新建 StyledRun 列表）。
    独立 runs 列表——不污染 ``_title_runs`` 渐变缓存（use_memo 引用级命中）。
    """
    global _VERSION_RUNS_STATIC
    if active:
        # VERSION 惰性导入（app_init._args 存在模块加载循环，见模块级注释）
        from src.app_init._args import VERSION
        return [StyledRun(f" \u00b7 {VERSION}", Style(fg=time_glow(_VER_LO, _VER_HI, _VER_PERIOD)))]
    if _VERSION_RUNS_STATIC is None:
        from src.app_init._args import VERSION
        _VERSION_RUNS_STATIC = [StyledRun(f" \u00b7 {VERSION}", Style(fg=242))]
    return _VERSION_RUNS_STATIC


def TopHeader(props) -> object:
    """顶部标题栏组件。

    Props:
        model: AppModel 实例（status_active 驱动版本号呼吸）。
        width: 终端宽度（窄屏截断预算——优先级：✦ 保留 > 渐变标题 > 版本号）。
    """
    # 渐变标题：空依赖 use_memo 缓存（引用级身份复用）
    title_styled = use_memo(_title_runs, ())
    # 呼吸 ✦：独立 TEXT 元素（0.1s 时间桶刷新，不污染渐变缓存）
    dot_color = time_glow(_DOT_LO, _DOT_HI, _DOT_PERIOD)
    # M1：读取 width prop（app.py:66 已传，修复前未使用）——窄屏截断预算
    width = props.get("width", 0)
    # ★ BEAUTY-31：版本号独立 TEXT——活跃期呼吸（渲染循环已推进，零额外
    #   成本）；空闲静态（回退模块级常量引用，diff 身份短路）。
    model = props.get("model")
    st = getattr(model, "status", None)
    active = bool(st is not None and getattr(st, "status_active", False))
    ver_runs = _version_runs(active)
    # ★ M1（BUG 修复，2026-08-15）：窄终端截断防御——三个 TEXT（✦/渐变标题/
    #   版本号）在 Row(height=1) 中按内容宽度排列，极窄终端（width<26）总宽
    #   超宽。修复：按 width 预算截断——优先级：✦（2 列）保留 > 渐变标题 >
    #   版本号（最可丢弃）；与 StatusBar/_ParseLine 防御模式一致（truncate_runs
    #   按显示宽度截断，不拆 CJK）。宽度不足时版本号先消失、标题次之、✦ 保留；
    #   预算为 0 时对应 runs 截断为空（TEXT 渲染空行零高度不影响 Row height=1）。
    #   ✦ 本身亦受 width 物理约束（width=1 时截断为仅 "✦" 宽 1 去空格，保证
    #   总宽 <= width 不超宽）。
    if width > 0:
        from src.tui.ink.helpers import truncate_runs
        dot_runs = [StyledRun("\u2726 ", Style(fg=dot_color))]
        # ★ P2-1（review 修复）：仅预算不足时调用 truncate_runs——修复前
        #   无条件截断：宽屏（预算充足）下每次调用新建 runs 列表（引用变化）
        #   → 渐变标题/版本号 TEXT 缓存（键含 styled 引用）每帧 miss → 整个
        #   header 每帧重建。修复后预算充足直接复用缓存引用（title_styled 为
        #   use_memo 缓存引用；ver_runs 空闲为模块级单例），仅 dot（呼吸色）
        #   独立重建——宽屏零额外重建。
        dot_w = 2  # "✦ " 固定宽 2 列
        title_w = sum(r.width for r in title_styled)
        ver_w = sum(r.width for r in ver_runs)
        if dot_w + title_w + ver_w > width:
            dot_runs = truncate_runs(dot_runs, width)
            dot_w = sum(r.width for r in dot_runs)
            title_budget = max(0, width - dot_w)
            title_styled = truncate_runs(title_styled, title_budget)
            title_w = sum(r.width for r in title_styled)
            ver_budget = max(0, width - dot_w - title_w)
            ver_runs = truncate_runs(ver_runs, ver_budget)
    else:
        dot_runs = [StyledRun("\u2726 ", Style(fg=dot_color))]
    # ★ 阶段2（标准布局容器重构）：BOX(flexDirection=row) → Row（语义化门面，
    #   Row = BOX + flexDirection=row，props 透传，输出与重构前一致）。
    # ★ 全面控件化（方案B）：渐变标题经标准控件 ``Gradient`` 渲染
    #   （``h(Gradient, {"styled": title_styled})``——styled 注入模式：
    #   宽屏 use_memo 缓存引用 / 窄屏截断后注入，视觉与 _gradient_runs
    #   等价）；✦ 与版本号保持 TEXT（基础控件）。
    from src.tui.ink.widgets.gradient import Gradient
    return h(Row, {"height": 1}, [
        h(TEXT, {"styled": dot_runs, "height": 1}),
        h(Gradient, {"styled": title_styled, "height": 1}),
        h(TEXT, {"styled": ver_runs, "height": 1}),
    ])


__all__ = ["TopHeader", "_title_runs", "_gradient_runs", "_version_runs"]
