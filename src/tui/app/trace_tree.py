"""轨迹检查器树渲染（从 trace_view 拆分，2026-08-19 树控件）。

工具调用参数/返回值以树形态展示：任意 JSON/文本值 → 树节点列表 → 可见行
（缩进 + 展开指示符 + 键值分色 + 完整换行）。样式取自 ``trace_styles``，
行数据模型为 ink ``StyledRun``（不依赖 trace_view 组件，避免循环依赖）。
"""

from __future__ import annotations

import json

from src.tui.ink import StyledRun
from src.tui.ink.helpers import wrap_runs_by_width

from .trace_styles import _S_TEXT, _S_TREE_KEY, _S_TREE_VAL

#: 树节点指示符/缩进（对齐 ink Tree 控件渲染语义）
_TREE_OPEN = "\u25be "    # ▾ 展开
_TREE_CLOSED = "\u25b8 "  # ▸ 折叠
_TREE_LEAF = "  "         # 叶子占位
_TREE_INDENT = 2          # 每层缩进空格数

#: 树递归深度上限（防御异常深层 JSON 触发 RecursionError）
_TREE_MAX_DEPTH = 200


def _value_to_tree(value, key: str = "", depth: int = 0) -> list:
    """任意值 → 树控件 data 格式节点列表（{label, children}）。

    dict → 键值对子节点（key 非空包装为 ``key (N 项)``）；list/tuple → 下标
    子节点（``[i]``）；标量 → 叶子 ``key: value``（JSON 字面量语义）。
    """
    if depth > _TREE_MAX_DEPTH:
        return []
    if isinstance(value, dict):
        if not value:
            return [{"label": (f"{key}: {{}}" if key else "{}"), "children": []}]
        children: list = []
        for k, v in value.items():
            children.extend(_value_to_tree(v, str(k), depth + 1))
        if key:
            return [{"label": f"{key} ({len(value)} 项)", "children": children}]
        return children
    if isinstance(value, (list, tuple)):
        if not value:
            return [{"label": (f"{key}: []" if key else "[]"), "children": []}]
        children = []
        for i, v in enumerate(value):
            children.extend(_value_to_tree(v, f"[{i}]", depth + 1))
        if key:
            return [{"label": f"{key} ({len(value)} 项)", "children": children}]
        return children
    if value is None:
        display = "null"
    elif isinstance(value, bool):
        display = "true" if value else "false"
    else:
        display = str(value)
    return [{"label": (f"{key}: {display}" if key else display), "children": []}]


def _args_to_tree(args) -> list:
    """工具调用参数 → 树节点列表（str JSON / dict；None/空 → []）。"""
    if args is None:
        return []
    if isinstance(args, dict):
        return _value_to_tree(args)
    text = str(args).strip()
    if not text:
        return []
    try:
        return _value_to_tree(json.loads(text))
    except (ValueError, TypeError):
        return [{"label": text, "children": []}]


def _parse_tree_text(text) -> list:
    """工具返回文本 → 树节点列表（JSON 解析成功 → 树；失败 → 每行一个叶子）。"""
    if text is None:
        return []
    s = str(text).strip()
    if not s:
        return []
    try:
        return _value_to_tree(json.loads(s))
    except (ValueError, TypeError):
        lines = s.splitlines() or [s]
        return [{"label": ln, "children": []} for ln in lines]


def _tree_node_rows(nodes: list, right_w: int, out: list, depth: int = 0,
                    collapsed: set | None = None, path: str = "",
                    keys: list | None = None) -> None:
    """树节点列表 → 可见行（前序；缩进 + 展开指示符；对齐 Tree 控件渲染）。

    折叠节点（``collapsed`` 路径 key 命中）不递归 children；节点路径 key
    写入 ``keys``（与 out 行对齐）；超宽行按栏宽换行显示完整（续行 hanging
    indent）；键值分色（``key: value`` 叶子行）。
    """
    if depth > _TREE_MAX_DEPTH:
        return
    for i, node in enumerate(nodes):
        node_path = f"{path}/{i}" if path else str(i)
        children = node.get("children") or []
        folded = bool(collapsed) and node_path in collapsed
        if children:
            indicator = _TREE_CLOSED if folded else _TREE_OPEN
        else:
            indicator = _TREE_LEAF
        prefix = " " * (depth * _TREE_INDENT)
        label = node.get("label", "")
        if "\n" in label:
            label = label.replace("\n", " ")
        sep_idx = label.find(": ")
        if sep_idx >= 0:
            runs = [
                StyledRun(f"{prefix}{indicator}{label[:sep_idx]}: ", _S_TREE_KEY),
                StyledRun(label[sep_idx + 2:], _S_TREE_VAL),
            ]
        else:
            runs = [StyledRun(f"{prefix}{indicator}{label}", _S_TEXT)]
        _tree_row_wrap(
            runs, len(prefix) + len(indicator), max(1, right_w), out,
            node_key=node_path if children else None, keys=keys,
        )
        if children and not folded:
            _tree_node_rows(
                children, right_w, out, depth + 1, collapsed, node_path, keys,
            )


def _tree_row_wrap(runs: list, hang: int, right_w: int, out: list,
                   node_key: str | None = None, keys: list | None = None) -> None:
    """树行换行：首行预算 right_w；续行 hanging indent=hang（内容完整）。"""
    if keys is not None:
        keys.append(node_key)
    if right_w <= 0:
        out.append(list(runs))
        return
    if not runs:
        out.append([])
        return
    lines = wrap_runs_by_width(runs, right_w, hard=True)
    out.append(list(lines[0].runs))
    if len(lines) <= 1:
        return
    rest: list = []
    for ln in lines[1:]:
        rest.extend(ln.runs)
    indent = hang if hang < right_w else 0
    cont_w = max(1, right_w - indent)
    for ln in wrap_runs_by_width(rest, cont_w, hard=True):
        if keys is not None:
            keys.append(None)
        row = [StyledRun(" " * indent, None)] if indent else []
        row.extend(ln.runs)
        out.append(row)


__all__ = [
    "_TREE_OPEN", "_TREE_CLOSED", "_TREE_LEAF", "_TREE_INDENT", "_TREE_MAX_DEPTH",
    "_value_to_tree", "_args_to_tree", "_parse_tree_text",
    "_tree_node_rows", "_tree_row_wrap",
]
