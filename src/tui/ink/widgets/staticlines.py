"""staticlines — StaticLines 静态行批量渲染标准组件。

React Ink 标准组件：渲染静态行列表（``lines: list[Line]``）。对齐
``<Static>`` 语义（静态内容冻结复用），同时支持**增量追加**（lines 引用
不变、长度增长时只发射新增行——聊天历史增量提交场景）。

设计动机（性能核心）：聊天历史（committed-lines）可达 1000+ 行。若逐行
``h(TEXT)`` 组件化，reconciler/layout/paint 每帧 O(全部行) 遍历，无变化帧
~51ms（实验：1000 行 TEXT 元素），60Hz 渲染预算紧张且随历史线性增长。
本组件经 host 机制**批量发射**：measure/paint 直接操作画布，行级 Line
身份复用 + 帧前缀缓存——无变化帧 O(1)（跳过画布写入），增量提交 O(新增)。
实验：1000 行无变化帧 ~0.7ms（含框架整体开销）。

组件形式（React Ink 标准组件）：函数组件 ``StaticLines(props)`` 返回
``h("static-lines", props)``；host 注册于模块导入时（register_host）。
组件树表达 ``h(StaticLines, {"lines": ...})``，对外与 Box/Text/Static 等
标准组件一致，内部保留批量渲染性能机制。

帧前缀缓存（``fiber._committed_prefix``）：
  - 命中（同 lines 引用/行数/y/w）→ 跳过画布写入（render_frame 复用前缀）；
  - lines 原地 extend（引用不变、长度增长）→ 仅追加新增行（增量提交）；
  - 行宽守卫：缓存重建时 O(n) 检查超宽行（reflow 未执行时截断，保持行宽
    不变量 E-COMMITTED-OVERFLOW）。
"""

from __future__ import annotations

from ..element import h
from ..helpers import truncate_line
from ..output import Line

__all__ = ["StaticLines"]

#: ``box.x != 0`` 合并行缓存可缓存的 base 行键数上限——超过（背景铺满整行的
#: 大 dict）时快照/比较成本与逐帧合并同阶，不缓存（避免每帧 O(宽度) 拷贝）。
_MERGED_CACHE_MAX_BASE_KEYS = 32
#: base 行不可缓存哨兵（大 dict 行：本行逐帧合并，缓存条目记 None）。
_UNCACHEABLE_BASE = object()


def _document_width(fiber) -> int:
    """文档总宽度（根 fiber 布局盒宽度；未知返回 0）。

    合并行缓存把「父容器边框/底色 + 本块内容」合成为 ``Line``（不再经
    ``components._to_lines`` 的字典行 E-OVERFLOW-GUARD 截断），构建时按
    文档宽度截断一次，保持「帧行宽 <= 文档宽度」不变量（正常布局不触发）。
    """
    node = fiber
    while node.return_ is not None:
        node = node.return_
    box = node.layout_box
    return int(box.w) if box is not None else 0


def _base_snapshot(base):
    """base 画布行快照（用于判定「除本块内容外该行未变」）。

    ``_merge_line`` 会**原地改写** dict 行（``row.update(slice_)``）——必须
    在合并前浅拷贝；None / Line（契约上合并后只读）直接引用。大 dict 行
    （键数 > ``_MERGED_CACHE_MAX_BASE_KEYS``）返回不可缓存哨兵。
    """
    if base is None or isinstance(base, Line):
        return base
    if isinstance(base, dict) and len(base) <= _MERGED_CACHE_MAX_BASE_KEYS:
        return dict(base)
    return _UNCACHEABLE_BASE


def _base_same(snapshot, base) -> bool:
    """当前 base 行是否与缓存快照一致（一致才可复用上一帧合并结果）。"""
    if snapshot is base:
        return True
    if snapshot is None or isinstance(snapshot, Line):
        return False
    if snapshot is _UNCACHEABLE_BASE:
        return False
    return isinstance(base, dict) and base == snapshot


def _coerce_line(item) -> Line:
    """把行元素归一化为 ink ``Line``（非 Line 输入不使 paint 崩溃）。

    正常调用方传 ``list[Line]``（零转换、保持对象身份/增量缓存）。异常输入
    （str / None / AnsiLine 等）按行文本或 runs 转成 Line：修复前 paint 直接
    访问 ``ln.width`` → AttributeError（被绘制层捕获后整块内容空白）。
    """
    if isinstance(item, Line):
        return item
    line = Line()
    if item is None:
        return line
    runs = getattr(item, "runs", None)
    if runs is not None:
        for run in runs:
            text = getattr(run, "text", None)
            if text:
                line.append(
                    text,
                    getattr(run, "style", None),
                    getattr(run, "link", None),
                )
        return line
    line.append(str(item), None)
    return line


def _resolve_lines(fiber) -> list:
    """解析 props.lines 为 list（list 直接返回；其他可迭代 list 化）。

    ★ P2（review）：解析结果挂 fiber 缓存（键为原始对象身份）——修复前
    ``_measure`` 将 list 化结果**写回 fiber.props**（违反 reconciler
    「props 从不原地修改，仅整体替换」契约：污染 ``_set_props`` 值比较
    基准，props 引用每帧更新 → ``_measure_cache`` 引用级命中失效）。
    改挂 fiber 属性后 props 保持不可变；生成器场景 measure/paint 共享同
    一次 list 化结果（measure 先耗尽生成器，paint 经缓存取同一列表——
    修复写回前「paint 拿到已耗尽生成器渲染空行」的原始问题同样解决）。
    """
    lines = fiber.props.get("lines") or []
    if isinstance(lines, list):
        return lines
    cached = fiber._resolved_lines
    if cached is not None and cached[0] is lines:
        return cached[1]
    try:
        resolved = list(lines)
    except TypeError:
        resolved = []
    fiber._resolved_lines = (lines, resolved)
    return resolved


def _measure(fiber, avail_w) -> tuple[int, int]:
    """测量：宽度取可用宽度，高度 = 行数。"""
    lines = _resolve_lines(fiber)
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
    # ★ P2（review）：经 ``_resolve_lines`` 解析（非 list 可迭代 list 化 +
    #   fiber 缓存）——修复前 _paint 独立 list() 化，measure 先耗尽生成器
    #   时 paint 拿到空列表渲染空行；缓存后共享同一次解析结果。
    lines = _resolve_lines(fiber)
    n = len(lines)
    # ★ 2026-10-23（画布正确性）：``box.x == 0`` 的前缀复用要求 committed 行在
    #   画布上「没有其他内容」。父容器底色（``_paint_box_background``）会先把
    #   这些行填满空格样式，修复前前缀路径直接用缓存行替换画布 → 底色丢失
    #   （StaticLines 位于带 backgroundColor 的容器内时）。检测覆盖区间内是否
    #   有非空画布行：有则**本帧放弃前缀复用**（置 None），走下方合并路径把
    #   内容按真实绘制顺序合并进 base 行；冲突持续期间前缀缓存保持 None
    #   （不每帧重建），底色消失后自动恢复前缀快路径。
    #   无冲突时 ``canvas[start:end].count(None)`` 为 C 级 O(行数)（1500 行
    #   实测 ~2.7µs，见 tests/tui/ink/test_canvas_width_cache.py）。
    prefix_conflict = False
    if box.x == 0 and n > 0:
        start = max(0, box.y)
        end = min(len(canvas), box.y + n)
        prefix_conflict = end > start and (
            canvas[start:end].count(None) != (end - start)
        )
    if box.x == 0 and not prefix_conflict:
        # x==0 走帧前缀复用（_committed_prefix）；清空合并行缓存
        # （防切回缩进/带边框布局后复用陈旧行，也释放大历史的缓存内存）。
        if fiber._merged_rows_cache is not None:
            fiber._merged_rows_cache = None
        # ★ 增量快路径（大历史 O(1)/帧）：静态行跨帧身份复用——前缀缓存挂
        #   在 fiber（fiber 复用即命中，替换/重建自然失效）。
        #   方向1 步骤4（非顶部前缀缓存）：前缀键 ``(lines, n, box.y, box.w)``
        #   覆盖非顶部路径（box.y != 0）——非顶部同样维护 ``_committed_prefix``
        #   （命中即跳过画布重写）；render_frame 消费前缀时校验
        #   ``box.y == 0``——顶部才允许前缀复用，非顶部前缀与画布尾部重建
        #   偏移语义不一致时由 render_frame 回退全量（防御层，成本 O(1)）。
        # ★ 缓存键用 **lines 对象本身**（身份比较）而非 ``id(lines)``——修复前
        #   以 id 为键：lines 被替换（props 换新列表）且旧列表被 GC 后，新列表
        #   可能复用同一 id，若行数/位置/宽度恰好相同则缓存**误命中**（渲染
        #   旧内容）。对象作键使缓存持强引用，其地址不可能被新对象复用（与
        #   ``components.render_frame`` 的 ``_truncated_prefix_cache`` 同契约）。
        key = (lines, n, box.y, box.w)
        cached = fiber._committed_prefix
        if cached is not None:
            ck = cached[0]
            if ck[0] is lines and ck[1] == n and ck[2] == box.y and ck[3] == box.w:
                return  # 前缀未变：跳过画布重写（render_frame 复用缓存）
        if (
            cached is not None
            and cached[0][0] is lines
            and cached[0][2] == box.y
            and cached[0][3] == box.w
            and n > cached[0][1]
        ):
            # lines 原地 extend（引用不变、长度增长）→ 仅追加新增行
            prefix = cached[1]
            new_lines = [_coerce_line(line) for line in lines[cached[0][1]:]]
            prefix.extend(new_lines)
            all_ok = bool(cached[2]) and all(
                ln.width <= box.w for ln in new_lines
            )
        else:
            prefix = [_coerce_line(line) for line in lines]
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
        fiber._committed_prefix = (key, prefix, all_ok)
        return
    if box.x == 0:
        # base 层冲突（父容器底色/前置内容与本块行重叠）：放弃前缀复用
        # （render_frame 走全量画布路径，合并结果保留 base 层）。
        fiber._committed_prefix = None
    # box.x != 0（缩进/padded）或 box.x == 0 但存在 base 层冲突：
    # 逐行合并（保留已有边框/内容——修复前 ``canvas[row] = padded`` 整体替换：
    # 父容器边框（行内已写 cols x0/x1）被 padded 空格覆盖，缩进框内 committed
    # 行丢失左/右边框）。
    # ★ P3（review）：直接从 ``_paint_canvas`` 导入绘制工具（不反向依赖
    #   components 主模块）。
    # ★ 性能（2026-10-23 画布）：跨帧**合并行缓存**——行内容对象同一且 base 行
    #   （父边框/底色/兄弟内容）快照未变时直接复用上一帧合并后的 Line。
    #   修复前每帧对每行整行重建列键 dict + 逐字符合并（1200 行实测 24ms/帧，
    #   60Hz 下掉帧）。失效条件：lines 对象替换（cache[0] 身份）、(x, w) 变化
    #   （resize / 缩进变化）、行内容对象替换、base 行内容变化；lines 原地
    #   增长（增量提交）时旧行条目继续有效，仅新增行计算。
    from .._paint_canvas import _canvas_row_to_line, _merge_line

    cache = fiber._merged_rows_cache
    entries: list = ()
    if cache is not None and cache[0] is lines and cache[1] == (box.x, box.w):
        entries = cache[2]
    new_entries: list = []
    limit = _document_width(fiber)
    for i, line in enumerate(lines):
        row = box.y + i
        if row < 0 or row >= len(canvas):
            new_entries.append(None)  # 保持条目与行索引对齐
            continue
        base = canvas[row]
        coerced = _coerce_line(line)
        cached = entries[i] if i < len(entries) else None
        if (
            cached is not None
            and cached[0] is coerced
            and _base_same(cached[1], base)
        ):
            canvas[row] = cached[2]
            new_entries.append(cached)
            continue
        snapshot = _base_snapshot(base)
        merged = _merge_line(base, box.x, coerced)
        if not isinstance(merged, Line):
            # 合并结果归一化为 Line（跨帧可复用的稳定对象；后续兄弟合并
            #   对 Line 行走 clone/dict 路径，不原地改写 → 缓存安全）。
            merged = _canvas_row_to_line(merged)
        if limit > 0 and merged.width > limit:
            # E-OVERFLOW-GUARD：Line 行不经 ``_to_lines`` 的字典行截断，
            #   布局异常导致超宽时在此截断（正常布局不触发）。
            merged = truncate_line(merged, limit)
        canvas[row] = merged
        new_entries.append(
            None if snapshot is _UNCACHEABLE_BASE
            else (coerced, snapshot, merged)
        )
    fiber._merged_rows_cache = (lines, (box.x, box.w), new_entries)


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


# host 注册迁移到清单条目（src.plugins.host_entries）——``static-lines`` 的
# measure/paint 由注册表内置规格声明，见 src.tui.ink.registry。

