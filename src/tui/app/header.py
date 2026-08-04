"""TopHeader — 顶部标题栏组件（Claude Code 视觉对齐）。

渲染 ``✦ DeepSeek CLI · v2.2.0``：
  - ``✦`` 亮色（品红系 fg=213）**时间基呼吸**（BEAUTY-7：0.1s 时间桶
    ``time_glow`` 在品红邻域脉动；流式/活跃期间随 10Hz 渲染平滑呼吸）。
  - ``DeepSeek CLI`` 空间渐变：逐字符 ``lerp_color`` 插值，色标
    ``[45, 39, 141, 213]``（青 → 蓝 → 紫 → 品红）。
  - 版本号 dim（fg=242），版本号经 ``src.app_init._args.VERSION`` 导入。

性能：渐变 runs 与终端宽度无关（内容固定）→ ``use_memo(deps=())`` 缓存
同一 ``styled`` 列表引用 → TEXT ``_paint_cache``（键含 styled 引用）跨帧
复用 Line 对象 → diff 身份短路（每帧零重建）。呼吸 ✦ 独立 TEXT 元素——
渐变/版本 runs 缓存不被呼吸刷新（修复前单 TEXT 呼吸会每 0.1s 全量重建
渐变 runs）。
"""

from __future__ import annotations

from src.tui.core.color import lerp_color
from src.tui.core.style import Style
from src.tui.ink import TEXT, StyledRun, h, Row, use_memo
from src.tui.app._theme import time_glow

#: 渐变标题色标（青 → 蓝 → 紫 → 品红）
_GRADIENT_STOPS = (45, 39, 141, 213)

#: ✦ 呼吸色域（品红 213 邻域脉动）
_DOT_LO = 205
_DOT_HI = 219
_DOT_PERIOD = 6.0


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
    """构建渐变标题 + dim 版本号 runs（不含呼吸 ✦——独立元素渲染）。"""
    from src.app_init._args import VERSION
    runs = _gradient_runs("DeepSeek CLI")
    runs.append(StyledRun(f" \u00b7 {VERSION}", Style(fg=242)))
    return runs


def TopHeader(props) -> object:
    """顶部标题栏组件。

    Props:
        model: AppModel 实例（保留签名兼容，渐变与宽度无关不消费）。
        width: 终端宽度（未使用——渐变 runs 与宽度无关）。
    """
    # 渐变标题 + 版本号：空依赖 use_memo 缓存（引用级身份复用）
    title_styled = use_memo(_title_runs, ())
    # 呼吸 ✦：独立 TEXT 元素（0.1s 时间桶刷新，不污染渐变缓存）
    dot_color = time_glow(_DOT_LO, _DOT_HI, _DOT_PERIOD)
    # ★ 阶段2（标准布局容器重构）：BOX(flexDirection=row) → Row（语义化门面，
    #   Row = BOX + flexDirection=row，props 透传，输出与重构前一致）。
    return h(Row, {"height": 1}, [
        h(TEXT, {"styled": [StyledRun("\u2726 ", Style(fg=dot_color))], "height": 1}),
        h(TEXT, {"styled": title_styled, "height": 1}),
    ])


__all__ = ["TopHeader", "_title_runs", "_gradient_runs"]
