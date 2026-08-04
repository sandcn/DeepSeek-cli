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

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Column

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


def _normalize_tree(items) -> list[dict]:
    """将 data 规范化为 ``{"label", "children", "open"}`` 节点列表。

    支持三种形态：
      - dict：``label``/``children``/``open`` 字段（children 递归归一化）；
      - str：叶子（label == 文本）；
      - 其他：``str()`` 化叶子。
    """
    out: list[dict] = []
    for item in items or []:
        if isinstance(item, dict):
            children = _normalize_tree(item.get("children", []))
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
    兜底用稳定字符串键）。折叠（open=False 或不在 open_set）→ 子级不收集。
    """
    if out is None:
        out = []
    for node in nodes:
        out.append((node, depth))
        if node["children"] and (node["open"] or _node_key(node) in open_set):
            _collect_visible(node["children"], open_set, depth + 1, out)
    return out


def _node_key(node: dict) -> str:
    """节点唯一键（label 兜底；同 label 兄弟视为同键——可接受权衡）。

    展开集合仅需区分「是否展开」——同 label 兄弟同时展开/折叠语义一致，
    无需严格唯一。不可变 str 保证可哈希。
    """
    return f"n:{node['label']}"


def _clamp_index(idx: int, total: int) -> int:
    """将光标索引钳制到合法范围 ``[0, total-1]``（可见节点动态变化越界防护）。"""
    if total <= 0:
        return 0
    if idx < 0:
        return 0
    if idx >= total:
        return total - 1
    return idx


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

    def _collect_initial(nodes: list[dict]):
        for node in nodes:
            if node["open"]:
                initial_open.add(_node_key(node))
            _collect_initial(node["children"])

    _collect_initial(items)
    open_set, set_open_set = use_state(initial_open)
    cursor, set_cursor = use_state(initial_index)
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
                        pass
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
