"""staticlines — StaticLines 静态行批量渲染标准组件。

React Ink 标准组件：渲染静态行列表（``lines: list[Line]``）。对齐
``<Static>`` 语义（静态内容冻结复用），同时支持**增量追加**（lines 引用
不变、长度增长时只发射新增行——聊天历史增量提交场景）。

设计动机（性能核心）：聊天历史（committed-lines）可达 1000+ 行。若逐行
``h(TEXT)`` 组件化，reconciler/layout/paint 每帧 O(全部行) 遍历，无变化帧
~51ms（实验：1000 行 TEXT 元素），10Hz 渲染预算紧张且随历史线性增长。
本组件经 host 机制**批量发射**：measure/paint 直接操作画布，行级 Line
身份复用 + 帧前缀缓存——无变化帧 O(1)（跳过画布写入），增量提交 O(新增)。
实验：1000 行无变化帧 ~0.7ms（含框架整体开销）。

组件形式（React Ink 标准组件）：函数组件 ``StaticLines(props)`` 返回
``h("static-lines", props)``；host 注册于模块导入时（register_host）。
组件树表达 ``h(StaticLines, {"lines": ...})``，对外与 Box/Text/Static 等
标准组件一致，内部保留批量渲染性能机制。

帧前缀缓存（``fiber._static_prefix``）：
  - 命中（同 lines 引用/行数/y/w）→ 跳过画布写入（render_frame 复用前缀）；
  - lines 原地 extend（引用不变、长度增长）→ 仅追加新增行（增量提交）；
  - 行宽守卫：缓存重建时 O(n) 检查超宽行（reflow 未执行时截断，保持行宽
    不变量 E-COMMITTED-OVERFLOW）。
"""

from __future__ import annotations

from ..element import h
from ..registry import register_host

__all__ = ["StaticLines"]


def _measure(fiber, avail_w) -> tuple[int, int]:
    """测量：宽度取可用宽度，高度 = 行数。"""
    lines = fiber.props.get("lines") or []
    if not isinstance(lines, list):
        # ★ P3（review）防御层：绕过组件函数直接 h("static-lines") 时 lines
        #   可能非 list（生成器）——list() 化防 ``len()`` TypeError（组件函数
        #   StaticLines 已守卫，此处兜底非常规路径；生成器一次性语义由调用方
        #   保证）。
        try:
            lines = list(lines)
        except TypeError:
            lines = []
    return (avail_w, len(lines))


def _paint(fiber, canvas) -> None:
    """绘制：将 lines 批量写入画布（前缀缓存命中跳过）。

    画布行直接写入 Line 对象（box.x==0 快路径）——diff 阶段身份短路
    （跨帧同 Line 对象恒相等跳过）。前缀缓存挂在 fiber（fiber 复用即命中，
    替换/重建自然失效）。
    """
    box = fiber.layout_box
    if box is None:
        return
    lines = fiber.props.get("lines") or []
    if not isinstance(lines, list):
        # ★ P3（review）防御层：同 _measure——非常规路径（绕过组件函数）下
        #   lines 可能非 list（生成器），list() 化防 ``len()`` TypeError。
        try:
            lines = list(lines)
        except TypeError:
            lines = []
    n = len(lines)
    if box.x == 0:
        # ★ 增量快路径（大历史 O(1)/帧）：静态行跨帧身份复用——前缀缓存挂
        #   在 fiber（fiber 复用即命中，替换/重建自然失效）。
        #   方向1 步骤4（非顶部前缀缓存）：前缀键 ``(id(lines), n, box.y)``
        #   覆盖非顶部路径（box.y != 0）——非顶部同样维护 ``_static_prefix``
        #   （命中即跳过画布重写）；render_frame 消费前缀时校验
        #   ``box.y == 0``——顶部才允许前缀复用，非顶部前缀与画布尾部重建
        #   偏移语义不一致时由 render_frame 回退全量（防御层，成本 O(1)）。
        key = (id(lines), n, box.y, box.w)
        cached = getattr(fiber, "_static_prefix", None)
        if cached is not None and cached[0] == key:
            return  # 前缀未变：跳过画布重写（render_frame 复用缓存）
        if (
            cached is not None
            and cached[0][0] == key[0]
            and cached[0][2] == key[2]
            and cached[0][3] == key[3]
            and n > cached[0][1]
        ):
            # lines 原地 extend（引用不变、长度增长）→ 仅追加新增行
            prefix = cached[1]
            prefix.extend(lines[cached[0][1]:])
            all_ok = bool(cached[2]) and all(
                ln.width <= box.w for ln in lines[cached[0][1]:]
            )
        else:
            prefix = list(lines)
            # ★ 行宽守卫（E-COMMITTED-OVERFLOW 防御）：reflow 未执行/失败
            #   （终端宽度变化后 committed_lines 按旧宽度 wrap）时前缀含超宽
            #   行——render_frame 前缀复用路径不经 E-OVERFLOW-GUARD（复用 Line
            #   对象免截断），超宽行直接进入帧破坏行宽不变量。此处仅在缓存
            #   重建时 O(n) 检查一次（非每帧，缓存命中零开销），标记
            #   all_ok=False 供 render_frame 回退全量路径（经截断）。
            #   ★ 渲染错误（BUG-74）：缓存键含 box.w——修复前键为
            #   ``(id(lines), n, box.y)``：终端宽度变化（reflow 前/失败/布局
            #   宽度与 model.width 不一致）时 id/lines 引用、行数、y 均未变
            #   → 缓存错误命中 → 旧宽度超宽行直接进入帧（防线被缓存绕过）。
            #   key 含布局宽度后宽度变化强制重建并重新检查 all_ok。
            all_ok = bool(box.w > 0) and all(
                ln.width <= box.w for ln in prefix
            )
        fiber._static_prefix = (key, prefix, all_ok)
        # 兼容别名：旧字段 ``_committed_prefix``（既有测试/render_frame 兼容
        # 读取）——与 ``_static_prefix`` 同值同步。
        fiber._committed_prefix = (key, prefix, all_ok)
        return
    # box.x != 0（缩进/padded）：逐行合并（保留已有边框/内容——修复前
    # ``canvas[row] = padded`` 整体替换：父容器边框（行内已写 cols x0/x1）
    # 被 padded 空格覆盖，缩进框内 committed 行丢失左/右边框）。
    from ..components import _merge_line
    for i, line in enumerate(lines):
        row = box.y + i
        if 0 <= row < len(canvas):
            canvas[row] = _merge_line(canvas[row], box.x, line)


def StaticLines(props: dict):
    """React Ink 标准组件：静态行批量渲染。

    Props:
        lines: list[Line] — 静态行列表（每行含样式 runs）。支持增量追加
            （同列表对象原地 extend，引用不变、长度增长→只发射新增行）。

    ★ P3（review）：lines 守卫——非 list（生成器/元组等）时 ``list()`` 化：
    修复前 ``_measure``/``_paint`` 假定 list，``len()`` 对生成器抛 TypeError。
    list 输入保持原引用（``id`` 稳定 → 增量快路径不受影响）；生成器等一次性
    可迭代 list() 化后同一渲染批次内 measure/paint 共享同一 props（引用稳定），
    生成器「每帧传入新生成器」的一次性语义由调用方保证。

    Returns:
        "static-lines" host 元素（批量发射，保留前缀缓存性能机制）。
    """
    lines = props.get("lines") or []
    if not isinstance(lines, list):
        try:
            lines = list(lines)
        except TypeError:
            lines = []
        props = dict(props)
        props["lines"] = lines
    return h("static-lines", props)


# 模块导入时注册 host（组件库 import 即可用；幂等）
register_host("static-lines", _measure, _paint)

