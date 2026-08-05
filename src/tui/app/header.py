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
    工具卡 detail 呼吸同步）；空闲静态 242（渲染循环空闲跳过，零额外成本）。
    独立 runs 列表——不污染 ``_title_runs`` 渐变缓存（use_memo 引用级命中）。
    """
    from src.app_init._args import VERSION
    if active:
        return [StyledRun(f" \u00b7 {VERSION}", Style(fg=time_glow(_VER_LO, _VER_HI, _VER_PERIOD)))]
    return [StyledRun(f" \u00b7 {VERSION}", Style(fg=242))]


def TopHeader(props) -> object:
    """顶部标题栏组件。

    Props:
        model: AppModel 实例（status_active 驱动版本号呼吸）。
        width: 终端宽度（未使用——渐变 runs 与宽度无关）。
    """
    # 渐变标题：空依赖 use_memo 缓存（引用级身份复用）
    title_styled = use_memo(_title_runs, ())
    # 呼吸 ✦：独立 TEXT 元素（0.1s 时间桶刷新，不污染渐变缓存）
    dot_color = time_glow(_DOT_LO, _DOT_HI, _DOT_PERIOD)
    # ★ BEAUTY-31：版本号独立 TEXT——活跃期呼吸（渲染循环已推进，零额外
    #   成本）；空闲静态（回退模块级常量引用，diff 身份短路）。
    model = props.get("model")
    st = getattr(model, "status", None)
    active = bool(st is not None and getattr(st, "status_active", False))
    ver_runs = _version_runs(active)
    # ★ 阶段2（标准布局容器重构）：BOX(flexDirection=row) → Row（语义化门面，
    #   Row = BOX + flexDirection=row，props 透传，输出与重构前一致）。
    return h(Row, {"height": 1}, [
        h(TEXT, {"styled": [StyledRun("\u2726 ", Style(fg=dot_color))], "height": 1}),
        h(TEXT, {"styled": title_styled, "height": 1}),
        h(TEXT, {"styled": ver_runs, "height": 1}),
    ])


__all__ = ["TopHeader", "_title_runs", "_gradient_runs", "_version_runs"]
