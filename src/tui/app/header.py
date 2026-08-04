"""TopHeader — 顶部标题栏组件（Claude Code 视觉对齐）。

渲染 ``✦ DeepSeek CLI · v2.2.0``：
  - ``✦`` 亮色（品红系 fg=213）**时间基呼吸**（BEAUTY-7：0.1s 时间桶
    ``time_glow`` 在品红邻域脉动；流式/活跃期间随 10Hz 渲染平滑呼吸）。
  - ``DeepSeek CLI`` 空间渐变：**Gradient 标准控件**（React Ink 生态
    ink-gradient 等价物）——渐变算法单一真源在
    ``src/tui/ink/widgets/gradient.py``，色标 ``[45, 39, 141, 213]``
    （青 → 蓝 → 紫 → 品红）。
  - 版本号 dim（fg=242），版本号经 ``src.app_init._args.VERSION`` 导入。

标准控件/布局重构（阶段4）：
  - 渐变标题：手写 ``TEXT styled``（``_title_runs``）→ **Gradient 控件**
    （``use_memo(deps=())`` 缓存元素引用；Gradient 内部 ``use_memo`` 缓存
    渐变 runs 引用 → TEXT ``_paint_cache`` 跨帧命中，每帧零重建）。
  - 呼吸 ✦ 独立 TEXT 元素——渐变缓存不被呼吸刷新（修复前单 TEXT 呼吸会每
    0.1s 全量重建渐变 runs）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, StyledRun, h, Row, use_memo, Gradient
from src.tui.app._theme import time_glow

#: 渐变标题色标（青 → 蓝 → 紫 → 品红）
_GRADIENT_STOPS = (45, 39, 141, 213)

#: ✦ 呼吸色域（品红 213 邻域脉动）
_DOT_LO = 205
_DOT_HI = 219
_DOT_PERIOD = 6.0


def _gradient_runs(text: str) -> list[StyledRun]:
    """逐字符空间渐变（青→蓝→紫→品红，per-char lerp_color 插值）。

    ★ 标准控件收敛（阶段3/4）：渐变算法单一真源迁入 Gradient 控件
    （``src/tui/ink/widgets/gradient.py::_gradient_runs``）——header 保留薄
    委托（兼容既有 patch/调用面；色标取本模块 ``_GRADIENT_STOPS``）。
    输出等价（per-char lerp_color 插值）。
    """
    from src.tui.ink.widgets.gradient import _gradient_runs as _g
    return _g(text, _GRADIENT_STOPS)


def _version_runs() -> list[StyledRun]:
    """版本号 dim runs（独立于渐变——版本号不参与渐变插值）。"""
    from src.app_init._args import VERSION
    return [StyledRun(f" \u00b7 {VERSION}", Style(fg=242))]


def _title_runs() -> list[StyledRun]:
    """构建渐变标题 + dim 版本号 runs（不含呼吸 ✦——独立元素渲染）。

    保留兼容调用面（TopHeader 渲染改用 Gradient 控件，本函数供测试/外部
    引用）。
    """
    runs = _gradient_runs("DeepSeek CLI")
    runs.extend(_version_runs())
    return runs


def TopHeader(props) -> object:
    """顶部标题栏组件。

    Props:
        model: AppModel 实例（保留签名兼容，渐变与宽度无关不消费）。
        width: 终端宽度（未使用——渐变 runs 与宽度无关）。
    """
    # 渐变标题：Gradient 标准控件（空依赖 use_memo 缓存元素引用；Gradient
    # 内部 use_memo 缓存渐变 runs 引用 → TEXT _paint_cache 跨帧命中）。
    gradient_el = use_memo(
        lambda: h(Gradient, {"text": "DeepSeek CLI", "colors": _GRADIENT_STOPS}),
        (),
    )
    # 版本号 dim：独立 TEXT（空依赖 use_memo 缓存 styled 引用）
    version_styled = use_memo(_version_runs, ())
    # 呼吸 ✦：独立 TEXT 元素（0.1s 时间桶刷新，不污染渐变缓存）
    dot_color = time_glow(_DOT_LO, _DOT_HI, _DOT_PERIOD)
    # ★ 阶段2（标准布局容器重构）：BOX(flexDirection=row) → Row（语义化门面，
    #   Row = BOX + flexDirection=row，props 透传，输出与重构前一致）。
    return h(Row, {"height": 1}, [
        h(TEXT, {"styled": [StyledRun("\u2726 ", Style(fg=dot_color))], "height": 1}),
        gradient_el,
        h(TEXT, {"styled": version_styled, "height": 1}),
    ])


__all__ = ["TopHeader", "_title_runs", "_gradient_runs"]
