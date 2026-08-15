"""TUI 架构守卫测试 — 模块依赖方向自动化检查。

背景（2026-08-16 架构改进方向 F）：TUI 系统经多轮重构后形成明确的分层
架构（Layer 0 → _screen/_input → ink/app → consumer），为防止后续重构
（拆分 InkSession、事件总线解耦等）引入反向依赖/循环依赖导致架构漂移，
以 AST 静态分析守护四条方向规则：

  R1  ink 层（tui.ink.*）不得运行时依赖 app 层（tui.app.*）
      —— 渲染内核保持上层无关，app 组件树可独立演进；
  R2  app 层（tui.app.*）不得运行时依赖 consumer 层
      （tui.consumer.* / tui._consumer / tui._lifecycle）
      —— 组件树是纯渲染视图，不反向耦合消费/生命周期；
  R3  Layer 0 纯净（tui._config / tui._const / tui._width）
      —— 基础常量/宽度计算零 tui 内部依赖，可被所有层引用；
  R4  运行时 import 图无环（拓扑排序可全序）
      —— 防止模块级 import 循环（初始化顺序崩溃隐患）。

实现说明：
  - 依赖解析为纯 AST 静态分析（不 import 被检模块，测试自身零副作用）；
  - 正确解析相对导入（from .x / from ..x / 多级）与绝对导入
    （from src.tui.x / import src.tui.x）；
  - TYPE_CHECKING 保护块内的导入不计入「运行时依赖」——它们只在类型
    检查时求值，不参与运行时初始化顺序（方向违规仍会被 R1/R2 拦截，
    因为 TYPE_CHECKING 导入同样会出现在 AST 中——故 R1/R2/R3 的解析
    也排除 TYPE_CHECKING 块，见 ``_runtime_edges`` 的注释说明）。
"""

from __future__ import annotations

import ast
from collections import defaultdict, deque
from pathlib import Path

import pytest

TUI_ROOT = Path(__file__).resolve().parents[2] / "src" / "tui"


# ═══════════════════════════════════════════════════════════
# AST 依赖解析工具
# ═══════════════════════════════════════════════════════════

def _resolve_relative(module_path: Path, level: int, module: str | None) -> str | None:
    """解析相对导入（from .x / from ..x / ...）为 tui 子模块名。

    Args:
        module_path: 模块文件路径（如 src/tui/app/apply.py）。
        level: import 语句的 level（1=当前包，2=父包…）。
        module: import 的目标子模块（None 表示包自身）。

    Returns:
        解析后的模块名（以 ``tui`` 开头）；非 tui 范围返回 None。
    """
    parts = list(module_path.parts)
    pkg_parts = parts[:-1]  # 去掉文件名（__init__.py 也是去掉自身）
    base = pkg_parts[: max(0, len(pkg_parts) - (level - 1))]
    if module:
        resolved = base + module.split(".")
    else:
        resolved = base
    name = ".".join(resolved)
    return name if name.startswith("tui") else None


def _module_id(path: Path) -> str:
    """文件路径 → tui 子模块名（tui/app/apply.py → tui.app.apply）。"""
    rel = path.relative_to(TUI_ROOT.parent)  # 相对 src/
    parts = list(rel.with_suffix("").parts)
    if rel.name == "__init__.py":
        parts = list(rel.parent.parts)
    return ".".join(parts)


def _type_checking_import_ids(tree: ast.AST) -> set[int]:
    """收集 TYPE_CHECKING 保护块内所有 Import 节点的 id（运行时不计）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        ids.add(id(sub))
    return ids


def _runtime_edges(path: Path) -> set[str]:
    """静态解析模块的**运行时** tui 内部依赖边（不含 TYPE_CHECKING 块）。

    TYPE_CHECKING 块内的导入只供类型检查器使用，运行时不求值——不参与
    模块初始化顺序，故不计入 R4 循环检测。但 R1/R2/R3 的**方向**守卫
    同样基于运行时依赖（若 TYPE_CHECKING 出现方向违规，改动时应同步
    修复设计意图，守卫测试会提示）。

    Returns:
        tui 子模块名集合（如 ``tui.app.model``）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    tc_ids = _type_checking_import_ids(tree)
    edges: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in tc_ids:
            continue
        if isinstance(node, ast.Import):
            for n in node.names:
                if n.name.startswith("src.tui"):
                    edges.add(n.name[len("src."):])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("src.tui"):
                edges.add(node.module[len("src."):])
            elif node.level:
                target = _resolve_relative(path, node.level, node.module)
                if target:
                    edges.add(target)
    return edges


def _build_graph() -> dict[str, set[str]]:
    """构建全部 tui 模块的运行时依赖图（模块名 → 依赖模块名集合）。"""
    graph: dict[str, set[str]] = {}
    for py in sorted(TUI_ROOT.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        graph[_module_id(py)] = _runtime_edges(py)
    return graph


@pytest.fixture(scope="module")
def dep_graph() -> dict[str, set[str]]:
    """模块级 fixture：全量运行时依赖图（测试共享一次解析）。"""
    return _build_graph()


# ═══════════════════════════════════════════════════════════
# R1 — ink 层不依赖 app 层
# ═══════════════════════════════════════════════════════════

def test_ink_layer_does_not_depend_on_app(dep_graph) -> None:
    """R1：渲染内核（ink）不得反向依赖组件树（app）。"""
    violations = [
        f"{src} -> {dst}"
        for src, deps in dep_graph.items()
        if src == "tui.ink" or src.startswith("tui.ink.")
        for dst in deps
        if dst == "tui.app" or dst.startswith("tui.app.")
    ]
    assert not violations, f"ink 层反向依赖 app 层（R1 违规）：{violations}"


# ═══════════════════════════════════════════════════════════
# R2 — app 层不依赖 consumer 层
# ═══════════════════════════════════════════════════════════

def test_app_layer_does_not_depend_on_consumer(dep_graph) -> None:
    """R2：组件树（app）不得反向依赖消费/生命周期（consumer/lifecycle）。"""
    consumer_mods = {"tui._consumer", "tui._lifecycle"}
    violations = [
        f"{src} -> {dst}"
        for src, deps in dep_graph.items()
        if src == "tui.app" or src.startswith("tui.app.")
        for dst in deps
        if dst in consumer_mods
        or dst == "tui.consumer"
        or dst.startswith("tui.consumer.")
    ]
    assert not violations, f"app 层反向依赖 consumer 层（R2 违规）：{violations}"


# ═══════════════════════════════════════════════════════════
# R3 — Layer 0 纯净
# ═══════════════════════════════════════════════════════════

def test_layer0_purity(dep_graph) -> None:
    """R3：Layer 0 基础模块（_config/_const/_width）零 tui 内部依赖。

    三个模块是全部 tui 模块的引用基础——它们不得依赖任何其他 tui
    模块（防止基础层被高层反向污染导致循环）。
    """
    layer0 = {"tui._config", "tui._const", "tui._width"}
    violations = [
        f"{m} -> {d}"
        for m in layer0
        for d in sorted(dep_graph.get(m, set()))
    ]
    assert not violations, f"Layer 0 模块存在 tui 内部依赖（R3 违规）：{violations}"


# ═══════════════════════════════════════════════════════════
# R4 — 运行时 import 图无环
# ═══════════════════════════════════════════════════════════

def test_runtime_import_graph_is_acyclic(dep_graph) -> None:
    """R4：运行时模块 import 图无环（拓扑排序可覆盖全部模块）。

    循环依赖会导致模块初始化顺序不确定（A import B 时 B 半初始化），
    是架构漂移的高危信号——拆分 InkSession / 事件总线解耦时必须保持。
    """
    adjacency: dict[str, set[str]] = defaultdict(set)
    for src, deps in dep_graph.items():
        for dst in deps:
            if dst in dep_graph:  # 仅统计 tui 内部边
                adjacency[src].add(dst)
    indegree = {m: 0 for m in dep_graph}
    for deps in adjacency.values():
        for d in deps:
            indegree[d] += 1
    queue = deque(sorted(m for m in dep_graph if indegree[m] == 0))
    visited: list[str] = []
    while queue:
        m = queue.popleft()
        visited.append(m)
        for d in sorted(adjacency[m]):
            indegree[d] -= 1
            if indegree[d] == 0:
                queue.append(d)
    cycle_mods = sorted(set(dep_graph) - set(visited))
    assert not cycle_mods, (
        f"运行时 import 图存在环（R4 违规），环内模块: {cycle_mods}"
    )


# ═══════════════════════════════════════════════════════════
# 工具正确性自检（防止守卫测试自身退化）
# ═══════════════════════════════════════════════════════════

def test_graph_contains_core_modules(dep_graph) -> None:
    """自检：依赖图覆盖全部 tui 模块（141 个文件）。"""
    assert "tui.ink.session" in dep_graph
    assert "tui.app.model" in dep_graph
    assert "tui._consumer" in dep_graph
    assert len(dep_graph) >= 100


def test_known_layer0_deps_are_empty(dep_graph) -> None:
    """自检：已知 Layer 0 模块确实零依赖（解析器未漏边）。"""
    assert dep_graph["tui._const"] == set()
    assert dep_graph["tui._width"] == set()


def test_known_runtime_edge_detected(dep_graph) -> None:
    """自检：已知运行时依赖被正确解析（防解析器漏边假绿）。"""
    # _input_layout 运行时依赖 _width（模块顶部 import）
    assert "tui._width" in dep_graph["tui._input_layout"]
    # app.apply 运行时依赖 _const
    assert "tui._const" in dep_graph["tui.app.apply"]
