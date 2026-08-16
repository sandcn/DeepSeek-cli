"""TUI 全量 React Ink 化守卫测试 — 渲染层必须使用 ink 控件与布局。

背景（2026-08-16 用户需求「所有 TUI 都要用 React Ink 控件跟布局实现所有」）：
TUI 界面渲染已全量迁移到 ink 框架（``src/tui/ink/`` 组件树 + flexbox 布局 +
hooks）。为防止回归（新增界面/组件绕过 ink 直接拼 ANSI / 直写终端），以
AST 静态分析守护三条规则：

  R5 渲染模块必须依赖 ink —— tui 模块中凡含 ``h(`` 调用（构建组件树）或
     ``use_*`` hook 调用者，必须运行时依赖 ``src.tui.ink``（组件/hooks/
     布局门面），禁止脱离 ink 手工构建界面；
  R6 界面渲染层禁止直写终端 —— ``tui.app.*``（组件树）与
     ``tui.ink.widgets.*``（标准控件库）不得出现 ``sys.stdout`` /
     ``sys.__stdout__`` 写入或 ``print()`` 调用（终端 I/O 统一由
     ``tui._screen`` / ``tui.ink.session`` 等基础设施承担）；
  R7 h 字符串 host 合规 —— 所有 ``h("<字符串>", ...)`` 的字符串类型必须
     是 ink 内置 host（box/text/static/spacer/app/fragment）或经
     ``register_host`` 注册的 host（如 static-lines），禁止未注册的自定义
     host 标签（渲染内核无法布局/绘制）。

实现说明（与 ``test_arch_guard.py`` 同模式）：
  - 依赖解析为纯 AST 静态分析（不 import 被检模块，测试自身零副作用）；
  - 正确解析相对导入（from .x / from ..x / 多级）与绝对导入
    （from src.tui.x / import src.tui.x）；
  - TYPE_CHECKING 保护块内的导入/调用不计入「运行时」检查（只在类型
    检查时求值，不参与运行时初始化/渲染）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TUI_ROOT = Path(__file__).resolve().parents[2] / "src" / "tui"

#: ink 内置 host 标签（与 src/tui/ink/element.py 常量一致）
_BUILTIN_HOSTS = {"box", "text", "static", "spacer", "app", "fragment"}

#: ink hooks 门面导出名（R5 判定「使用了 hooks」的调用名）
_HOOK_NAMES = {
    "use_state", "use_reducer", "use_ref", "use_effect", "useLayoutEffect",
    "use_memo", "use_callback", "use_context", "useId", "use_input",
    "use_error_state", "useMeasure", "usePrevious", "useApp", "useFocus",
    "useStdin", "useStdout", "useStderr", "useSyncExternalStore", "usePaste",
    "useBoxMetrics", "useWindowSize", "useFocusManager", "useCursor",
    "useAnimation",
}


def _module_id(path: Path) -> str:
    """文件路径 → tui 子模块名（tui/app/apply.py → tui.app.apply）。"""
    rel = path.relative_to(TUI_ROOT.parent)  # 相对 src/
    parts = list(rel.with_suffix("").parts)
    if rel.name == "__init__.py":
        parts = list(rel.parent.parts)
    return ".".join(parts)


def _resolve_relative(module_path: Path, level: int, module: str | None) -> str | None:
    """解析相对导入（from .x / from ..x / ...）为 tui 子模块名。

    与 ``test_arch_guard`` 同逻辑——tui 内部模块的相对导入解析为
    ``tui.xxx`` 形式（绝对名），供依赖判定。
    """
    parts = list(module_path.parts)
    pkg_parts = parts[:-1]
    base = pkg_parts[: max(0, len(pkg_parts) - (level - 1))]
    resolved = base + (module.split(".") if module else [])
    name = ".".join(resolved)
    return name if name.startswith("tui") else None


def _type_checking_import_ids(tree: ast.AST) -> set[int]:
    """收集 TYPE_CHECKING 保护块内所有 Import/ImportFrom 节点的 id。"""
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

    Returns:
        tui 子模块名集合（如 ``tui.ink.output``）。
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


def _iter_py_files() -> list[Path]:
    """全部 tui 模块文件路径（排除 __pycache__）。"""
    return sorted(
        py for py in TUI_ROOT.rglob("*.py") if "__pycache__" not in str(py)
    )


def _type_checking_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """收集 TYPE_CHECKING 保护块的行号区间（(start, end) 含两端）。

    ast.walk 含 Module 等无 lineno 节点——只统计 If 节点；区间覆盖
    ``If`` 自身行号到 ``end_lineno``（Python 3.9 起支持，缺失回退起始行）。
    """
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                lo = getattr(node, "lineno", 0)
                hi = getattr(node, "end_lineno", lo)
                ranges.append((lo, hi))
    return ranges


def _in_type_checking(ranges: list[tuple[int, int]], node: ast.AST) -> bool:
    """判断节点行号是否位于任一 TYPE_CHECKING 保护块区间内。"""
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    for lo, hi in ranges:
        if lo <= lineno <= hi:
            return True
    return False


@pytest.fixture(scope="module")
def render_graph() -> dict[str, dict]:
    """模块级 fixture：全部 tui 模块的静态分析结果。

    Returns:
        {module_id: {"edges": set, "has_h": bool, "has_hook": bool,
                     "h_hosts": list[str], "stdout_writes": bool}}
    """
    result: dict[str, dict] = {}
    for py in _iter_py_files():
        mid = _module_id(py)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            result[mid] = {"edges": set(), "has_h": False, "has_hook": False,
                           "h_hosts": [], "stdout_writes": False}
            continue
        edges = _runtime_edges(py)
        tc_ranges = _type_checking_ranges(tree)
        has_h = False
        has_hook = False
        h_hosts: list[str] = []
        stdout_writes = False
        for node in ast.walk(tree):
            if _in_type_checking(tc_ranges, node):
                continue
            if isinstance(node, ast.Call):
                f = node.func
                # h(...) 调用：函数名为 h（Element 工厂）
                if isinstance(f, ast.Name) and f.id == "h":
                    has_h = True
                    if node.args and isinstance(node.args[0], ast.Constant) \
                            and isinstance(node.args[0].value, str):
                        h_hosts.append(node.args[0].value)
                # hook 调用：函数名为 use_* 门面
                if isinstance(f, ast.Name) and f.id in _HOOK_NAMES:
                    has_hook = True
                # print(...) 调用
                if isinstance(f, ast.Name) and f.id == "print":
                    stdout_writes = True
                # sys.stdout.write(...) / sys.__stdout__.write(...)
                if isinstance(f, ast.Attribute) and f.attr == "write":
                    v = f.value
                    if isinstance(v, ast.Attribute) and v.attr in (
                        "stdout", "__stdout__",
                    ):
                        stdout_writes = True
        result[mid] = {
            "edges": edges,
            "has_h": has_h,
            "has_hook": has_hook,
            "h_hosts": h_hosts,
            "stdout_writes": stdout_writes,
        }
    return result


# ═══════════════════════════════════════════════════════════
# R5 — 渲染模块必须依赖 ink
# ═══════════════════════════════════════════════════════════

def test_render_modules_depend_on_ink(render_graph) -> None:
    """R5：含 ``h(`` 或 ``use_*`` hook 调用的模块必须运行时依赖 ink。

    界面渲染只能经 ink 框架（组件树 h / hooks / 布局门面）表达——模块
    不 import ink 却调用 h/hooks 意味着脱离框架手工渲染（渲染内核无法
    布局/绘制，属架构违规）。ink 框架内部模块（tui.ink.*）自身即为
    实现，不适用本规则。
    """
    violations = []
    for mid, info in sorted(render_graph.items()):
        if mid == "tui.ink" or mid.startswith("tui.ink."):
            continue
        if not (info["has_h"] or info["has_hook"]):
            continue
        depends_ink = any(
            d == "tui.ink" or d.startswith("tui.ink.") for d in info["edges"]
        )
        if not depends_ink:
            violations.append(mid)
    assert not violations, (
        "R5 违规：以下模块调用 h()/hooks 但未依赖 ink 框架（脱离组件树渲染）: "
        f"{violations}"
    )


# ═══════════════════════════════════════════════════════════
# R6 — 界面渲染层禁止直写终端
# ═══════════════════════════════════════════════════════════

def test_ui_render_layers_no_direct_terminal_write(render_graph) -> None:
    """R6：组件树（tui.app.*）与标准控件库（tui.ink.widgets.*）禁止直写终端。

    终端 I/O 统一由基础设施（tui._screen / tui.ink.session 等）承担；
    界面渲染层出现 ``sys.stdout.write`` / ``sys.__stdout__`` / ``print``
    意味着绕过 ink 渲染管线手工输出（破坏帧 diff / 光标定位 / 布局）。
    """
    violations = []
    for mid, info in sorted(render_graph.items()):
        if mid == "tui.app" or mid.startswith("tui.app."):
            pass
        elif mid == "tui.ink.widgets" or mid.startswith("tui.ink.widgets."):
            pass
        else:
            continue
        if info["stdout_writes"]:
            violations.append(mid)
    assert not violations, (
        "R6 违规：界面渲染层直写终端（应经 ink 渲染管线/基础设施输出）: "
        f"{violations}"
    )


# ═══════════════════════════════════════════════════════════
# R7 — h 字符串 host 合规
# ═══════════════════════════════════════════════════════════

def _registered_hosts() -> set[str]:
    """AST 收集全部 ``register_host("<tag>", ...)`` 注册的 host 标签。"""
    tags: set[str] = set()
    for py in _iter_py_files():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == "register_host":
                    if node.args and isinstance(node.args[0], ast.Constant) \
                            and isinstance(node.args[0].value, str):
                        tags.add(node.args[0].value)
    return tags


def test_h_string_host_types_registered(render_graph) -> None:
    """R7：``h("<字符串>", ...)`` 的字符串类型必须是已知 host。

    已知 host = ink 内置 host（box/text/static/spacer/app/fragment）∪
    ``register_host`` 注册的自定义 host（如 static-lines）。未注册字符串
    会落入渲染内核默认容器分支或无法布局/绘制（静默错误或越界）。
    """
    known = _BUILTIN_HOSTS | _registered_hosts()
    violations = []
    for mid, info in sorted(render_graph.items()):
        for host in info["h_hosts"]:
            if host not in known:
                violations.append(f"{mid}: h({host!r})")
    assert not violations, (
        "R7 违规：h() 使用了未注册 host 字符串（应使用内置 host / 标准组件 / "
        f"register_host 注册的 host）: {violations}"
    )


# ═══════════════════════════════════════════════════════════
# 工具正确性自检（防止守卫测试自身退化）
# ═══════════════════════════════════════════════════════════

def test_guard_graph_covers_render_modules(render_graph) -> None:
    """自检：守卫图覆盖全部 tui 模块，且已知渲染模块被标记。"""
    assert "tui.app.app" in render_graph
    assert "tui.app.chat_view" in render_graph
    assert "tui.app.input_area" in render_graph
    assert "tui.app.trace_view" in render_graph
    assert "tui.ink.widgets.staticlines" in render_graph
    assert len(render_graph) >= 100


def test_known_render_modules_marked(render_graph) -> None:
    """自检：已知渲染组件模块确实含 h()/hook 调用（解析器未漏检）。"""
    assert render_graph["tui.app.app"]["has_h"] or render_graph["tui.app.app"]["has_hook"]
    assert render_graph["tui.app.header"]["has_h"]
    assert render_graph["tui.app.status_bar"]["has_hook"]
    assert render_graph["tui.app.user_select"]["has_hook"]
    assert render_graph["tui.app.trace_view"]["has_hook"]


def test_known_h_host_strings_detected(render_graph) -> None:
    """自检：static-lines 字符串 host 被正确收集（R7 有对象可查）。"""
    assert "static-lines" in render_graph["tui.ink.widgets.staticlines"]["h_hosts"]


def test_layer0_not_render(render_graph) -> None:
    """自检：Layer 0 基础模块（_config/_const/_width）不含渲染调用。"""
    for mid in ("tui._config", "tui._const", "tui._width"):
        info = render_graph[mid]
        assert not info["has_h"] and not info["has_hook"], f"{mid} 不应含渲染调用"
