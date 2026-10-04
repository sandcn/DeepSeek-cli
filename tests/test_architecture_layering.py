"""架构分层守卫测试 — 固化依赖方向与无循环约束

本测试以静态 import 图（AST）为单一事实源，防止架构回归：

1. **分层方向**：``core`` 领域层（``core/ports``、``core/events``、``core/*``、
   ``core/internal/*``、``core/commands/*``）不得直接 import 表现层
   （``tui`` / ``renderer``）。桥接职责集中在 ``core/adapters``（适配器层允许
   依赖表现层）。
2. **无循环依赖**：全项目完整 AST import 图（含函数内延迟 import 与
   TYPE_CHECKING）无强连通分量（SCC）——消除循环导入环。
3. **模块级无循环**：模块顶层 import 图无环（运行时真实循环风险为零）。
"""

from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

# 桥接层白名单：适配器层允许依赖表现层
_BRIDGE_PREFIX = "src.core.adapters"

_FORBIDDEN_PREFIXES = ("src.tui", "src.renderer")


def _iter_py_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _module_name(path: str) -> str:
    rel = os.path.relpath(path, PROJECT_ROOT)
    mod = rel[:-3].replace(os.sep, ".")
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    return mod


def _resolve_relative(base_mod: str, level: int, module: str) -> str:
    """解析相对导入为绝对模块名（以 src 为根，base_mod 不含 .__init__）。"""
    if level == 0:
        return module
    parts = base_mod.split(".")
    pkg = parts[:-1]
    cut = level - 1
    pkg = pkg[: len(pkg) - cut] if cut <= len(pkg) else []
    if module:
        return ".".join(pkg + module.split("."))
    return ".".join(pkg)


def _import_targets(node: ast.AST, mod: str):
    """返回一条 import 语句指向的模块全名列表。"""
    targets = []
    if isinstance(node, ast.ImportFrom):
        tgt = _resolve_relative(mod, node.level or 0, node.module or "")
        if tgt:
            targets.append(tgt)
            if not node.module:  # from . import X —— X 为子模块
                for alias in node.names:
                    targets.append(tgt + "." + alias.name)
    elif isinstance(node, ast.Import):
        targets = [alias.name for alias in node.names]
    return targets


def _collect_edges(module_level_only: bool):
    """构建 import 依赖图。module_level_only=True 时仅收集模块顶层 import。"""
    edges = defaultdict(set)
    for path in _iter_py_files(SRC_DIR):
        mod = _module_name(path)
        if not mod.startswith("src"):
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue

        if module_level_only:
            nodes = []
            skip_ids = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    for child in ast.walk(node):
                        skip_ids.add(id(child))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in skip_ids:
                    nodes.append(node)
        else:
            nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]

        for node in nodes:
            for target in _import_targets(node, mod):
                if target.startswith("src"):
                    edges[mod].add(target)
    return edges


def _find_cycles(edges) -> list:
    """Tarjan SCC，返回 size>1 的分量列表。"""
    sys.setrecursionlimit(100000)
    index = {}
    low = {}
    onstack = {}
    stack = []
    sccs = []
    counter = [0]

    def strongconnect(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstack[v] = True
        for w in edges.get(v, ()):
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif onstack.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstack[w] = False
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(comp)

    for v in list(edges):
        if v not in index:
            strongconnect(v)
    return sccs


def test_module_level_imports_have_no_cycles():
    """模块顶层 import 无环（运行时真实循环风险为零）。"""
    edges = _collect_edges(module_level_only=True)
    cycles = _find_cycles(edges)
    assert not cycles, f"检测到模块级循环依赖: {[sorted(c) for c in cycles]}"


def test_full_ast_imports_have_no_cycles():
    """完整 AST import 图无环（含延迟 import / TYPE_CHECKING）。"""
    edges = _collect_edges(module_level_only=False)
    cycles = _find_cycles(edges)
    assert not cycles, f"检测到循环依赖环: {[sorted(c) for c in cycles]}"


def test_core_domain_does_not_depend_on_presentation():
    """core 领域层不得直接 import 表现层（tui/renderer）。"""
    violations = []
    for path in _iter_py_files(os.path.join(SRC_DIR, "core")):
        mod = _module_name(path)
        if not mod.startswith("src.core"):
            continue
        if mod == _BRIDGE_PREFIX or mod.startswith(_BRIDGE_PREFIX + "."):
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            for target in _import_targets(node, mod):
                for forbidden in _FORBIDDEN_PREFIXES:
                    if target == forbidden or target.startswith(forbidden + "."):
                        violations.append(f"{mod} → {target} (L{node.lineno})")
    assert not violations, "core 领域层违规依赖表现层:\n" + "\n".join(violations)
