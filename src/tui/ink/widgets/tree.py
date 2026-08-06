"""tree — React Ink 风格树形控件（Tree）。

支持展开/折叠 + 键盘导航（up/down 移动光标、space/enter 切换/选择）。
节点形态（对齐常见 React 树控件）：

    data = [
        {"label": "root", "children": [
            {"label": "leaf1"},
            {"label": "dir", "children": [
                {"label": "nested"},
            ]},
        ]},
        "plain-leaf",          # 字符串简写（叶子）
    ]

- 折叠节点子级不参与可见列表（前序遍历跳过 open=False 的 children）；
- 光标仅在可见节点间移动（折叠/展开时钳制）；
- ``space``/``enter``：有子级 → 切换 open；无子级 → ``onSelect(node)``。

依赖约束：仅依赖 element / output / core.style / _screen / hooks（Layer 0/1），
无父包依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_effect, use_ref
from ..widgets.layout import Column
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_clamp_index 原本地定义——收敛
#   至 _widget_common 单一真源。
from ._widget_common import _clamp_index

_logger = logging.getLogger(__name__)

__all__ = ["Tree"]


#: 展开/折叠指示符（几何符号单宽）
_TREE_OPEN = "\u25be "   # ▾
_TREE_CLOSED = "\u25b8 "  # ▸
_TREE_LEAF = "  "

#: 默认缩进宽度（每层空格数）
_TREE_INDENT = 2

#: 光标行样式（默认青色 fg=6）
_TREE_HIGHLIGHT = Style(fg=6)

#: 叶子节点选中样式（可选，默认 None——不区分）
_TREE_LEAF_STYLE = None

#: 递归深度上限（P3-22 防御）：超过则停止展开 children（截断深层，避免
#: Python 默认递归深度 ~1000 触发 RecursionError）。200 层对正常树结构远
#: 超裕量；文档化：深度 > 200 的树子级将被截断。
_TREE_MAX_DEPTH = 200


def _normalize_tree(items, depth: int = 0) -> list[dict]:
    """将 data 规范化为 ``{"label", "children", "open"}`` 节点列表。

    支持三种形态：
      - dict：``label``/``children``/``open`` 字段（children 递归归一化）；
      - str：叶子（label == 文本）；
      - 其他：``str()`` 化叶子。

    ★ 健壮性（渲染错误防御）：data 不可迭代（None/标量/对象）时回退空列表
      ——修复前 ``for item in items or []`` 对不可迭代的 data（如 int/bool）
      抛 TypeError，Tree 渲染崩溃。
    ★ 2026-08-06：可迭代守卫排除 str/bytes——str 是 Iterable（逐字符），
      但作为 data 会被逐字符拆成叶子节点（意外语义），与 _table/listview
      的守卫写法对齐。
    ★ P3（review 2026-08-06）：递归深度守卫——``depth > _TREE_MAX_DEPTH``
      时停止展开 children（截断深层，避免 RecursionError）。
    """
    if not hasattr(items, "__iter__") or isinstance(items, (str, bytes)):
        return []
    if depth > _TREE_MAX_DEPTH:
        return []
    out: list[dict] = []
    for item in items or []:
        if isinstance(item, dict):
            children = _normalize_tree(item.get("children", []), depth + 1)
            out.append({
                "label": str(item.get("label", "")),
                "children": children,
                "open": bool(item.get("open", True)),
            })
        else:
            out.append({"label": str(item), "children": [], "open": False})
    return out


def _collect_visible(
    nodes: list[dict],
    open_set,
    depth: int = 0,
    out: list | None = None,
) -> list[tuple[dict, int]]:
    """收集可见节点（前序遍历，折叠节点的子级跳过）。

    ``open_set``：展开节点集合（存储节点 id——label 可能重复；不可哈希
    兜底用稳定字符串键）。折叠（不在 open_set）→ 子级不收集。

    ★ P3（review）：递归深度守卫——``depth > _TREE_MAX_DEPTH`` 时停止递归
    并返回已收集列表（避免 RecursionError）。
    """
    if out is None:
        out = []
    if depth > _TREE_MAX_DEPTH:
        return out
    for node in nodes:
        out.append((node, depth))
        # ★ P1（review）：可见性判定仅依据 open_set——修复前 ``node["open"] or
        #   key in open_set`` 中 ``node["open"]`` 恒为 True（_normalize_tree
        #   默认播种 True）且 toggle 只改 open_set 不改 node["open"] → 默认
        #   open=True 节点 toggle 后子级仍全部可见、永远无法折叠。open_set
        #   初始化时已含所有 open=True 节点（_collect_initial 播种），显式
        #   open=False 节点不在 open_set → 折叠语义保持。
        if node["children"] and (_node_key(node) in open_set):
            _collect_visible(node["children"], open_set, depth + 1, out)
    return out


def _node_key(node: dict) -> str:
    """节点唯一键（label 兜底；同 label 兄弟视为同键——可接受权衡）。

    展开集合仅需区分「是否展开」——同 label 兄弟同时展开/折叠语义一致，
    无需严格唯一。不可变 str 保证可哈希。

    ★ P3（review 2026-08-06）：**已知权衡保留**——同 label 兄弟的展开状态
    互相影响（展开一个同 label 节点会同步展开另一个）。替代方案评估：
      - ``id(node)`` 作 key：``_normalize_tree`` 每次渲染重建 node dict，
        id 不稳定 → open_set 恒匹配失败（永远折叠），不可行；
      - 路径 key（如 ``"0/1/2"``）：可区分同 label 兄弟，但 data 增删节点
        后路径漂移、旧 open_set 失效——需与 P3-20（data 身份变化重置）配合
        且改动面大（``_collect_visible``/``_collect_initial`` 均需传路径）。
    权衡收益低（同 label 兄弟同时展开语义影响小）、成本高，保留现状并注释
    说明；如需严格区分建议后续改为路径 key + data 重置。
    """
    return f"n:{node['label']}"


def Tree(props: dict) -> Element:
    """React Ink 风格树形控件。

    Props:
        data: 树节点列表（见模块 docstring）。
        onSelect: ``(node: dict) -> None``——叶子节点 Enter/space 选择回调。
        focus: 是否参与输入路由（默认 True）。
        indent: 每层缩进空格数（默认 2）。
        initialIndex: 初始光标下标（默认 0）。
        highlightStyle: 光标行样式（默认 ``Style(fg=6)`` cyan）。
        nodeStyle: 普通节点样式（默认 None）。
        labelStyle: 叶子节点样式（默认 None）。

    行为：
      - up/down 移动光标（仅可见节点）；
      - space/enter：有子级 → 切换展开/折叠；无子级 → ``onSelect(node)``。

    Returns:
        Column 元素（纵向堆叠的可见节点行）。
    """
    items = _normalize_tree(props.get("data", []))
    on_select = props.get("onSelect")
    focus = bool(props.get("focus", True))
    try:
        indent = max(0, int(props.get("indent", _TREE_INDENT)))
    except (TypeError, ValueError, OverflowError):
        indent = _TREE_INDENT
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial_index = 0
    highlight_style = props.get("highlightStyle") or _TREE_HIGHLIGHT
    node_style = props.get("nodeStyle")
    leaf_style = props.get("labelStyle")

    # 展开集合：初始收集 data 中显式 ``open=True`` 的节点
    initial_open = set()

    def _collect_initial(nodes: list[dict], depth: int = 0):
        if depth > _TREE_MAX_DEPTH:
            return
        for node in nodes:
            if node["open"]:
                initial_open.add(_node_key(node))
            _collect_initial(node["children"], depth + 1)

    _collect_initial(items)
    open_set, set_open_set = use_state(initial_open)
    cursor, set_cursor = use_state(initial_index)
    # ★ P3（review 2026-08-06）：open_set 不随 data 变化更新——``use_state``
    #   仅初始化一次，data 变化后新节点默认折叠（旧节点展开状态也可能与
    #   新 data 不匹配）。对 data **身份变化**增加 ``use_effect`` 重置：data
    #   引用变化（调用方传入新 list 对象）→ 重新播种 open_set（收集新 data
    #   的 ``open=True`` 节点）。deps 用 data 原始引用（身份比较）：
    #   - 调用方保持 data 引用稳定（如 use_memo 缓存）→ 展开状态跨渲染保持；
    #   - data 每次新引用 → 展开状态重置（data 变化语义）。
    #   effect 内比较 open_set 与 initial_open 相同则不 set（避免挂载时无谓
    #   重渲染）。
    def _reset_open_on_data_change():
        if open_set != initial_open:
            set_open_set(initial_open)

    use_effect(_reset_open_on_data_change, (props.get("data", []),))
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 state。
    open_ref = use_ref(open_set)
    cursor_ref = use_ref(cursor)
    open_ref.current = open_set
    cursor_ref.current = cursor

    def _handle(event) -> bool:
        if not focus or not items:
            return False
        cur_vis = _collect_visible(items, open_ref.current)
        if not cur_vis:
            return False
        cur = _clamp_index(cursor_ref.current, len(cur_vis))
        if event.kind == "arrow_up":
            if cur > 0:
                cursor_ref.current = cur - 1
                set_cursor(cursor_ref.current)
            return True
        if event.kind == "arrow_down":
            if cur < len(cur_vis) - 1:
                cursor_ref.current = cur + 1
                set_cursor(cursor_ref.current)
            return True
        if event.kind == "space" or (event.kind == "char" and event.char == " ") or event.kind == "enter":
            node = cur_vis[cur][0]
            if node["children"]:
                key = _node_key(node)
                new_open = set(open_ref.current)
                if key in new_open:
                    new_open.discard(key)
                else:
                    new_open.add(key)
                open_ref.current = new_open
                set_open_set(new_open)
            else:
                # 叶子：选择回调
                if on_select is not None:
                    try:
                        on_select(node)
                    except Exception:
                        # ★ 2026-08-06：补日志（修复前静默吞，与 listview 对齐）
                        _logger.debug("Tree onSelect 回调异常", exc_info=True)
            return True
        return False

    use_input(_handle, focus)

    # 渲染期重建可见节点（响应 open_set 变化）+ 钳制光标
    visible_now = _collect_visible(items, open_set)
    cursor_shown = _clamp_index(cursor, len(visible_now))
    rows = []
    for i, (node, depth) in enumerate(visible_now):
        is_sel = i == cursor_shown
        has_children = bool(node["children"])
        indicator = ""
        if has_children:
            indicator = _TREE_OPEN if (_node_key(node) in open_set) else _TREE_CLOSED
        else:
            indicator = _TREE_LEAF
        prefix = " " * (depth * indent)
        style = highlight_style if is_sel else (node_style if has_children else leaf_style)
        label = node["label"]
        # 强制单行：label 可能含 \n（树节点标题来自多行输入）——归一化防
        # 行级 diff 宽度不变量破坏。
        if "\n" in label:
            label = label.replace("\n", " ")
        rows.append(
            h(TEXT, {
                "children": prefix + indicator + label,
                "style": style,
                "height": 1,
                "key": f"tree-{i}",
            })
        )
    # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
    return h(Column, None, rows)


__all__ = ["Tree"]
