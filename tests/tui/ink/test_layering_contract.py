"""框架分层契约 — ``src/tui/ink`` 不得依赖应用层 / 核心层 / 渲染器。

架构约定（2026-10 演进）::

    L2  src/tui/app            应用视图（依赖 ink）
    L1  src/tui/ink            框架（element/fiber/hooks/layout/paint/renderer/session）
    L0  src/_text_width、src/tui/_width、src/tui/_ansi_sequences、
        src/tui/_command_protocol、src/tui/core/style       （纯数据/纯函数）

框架**只允许**依赖 L0 白名单与自身包；历史上它经延迟 import 反向依赖了
``app.apply`` / ``app.app`` / ``_input_layout`` / ``_input_metrics`` /
``_system_monitor`` / ``_screen`` / ``_const`` / ``_config`` / ``api._sync_bridge`` /
``core.frame_tick`` / ``renderer.locks`` / ``presentation_data``，导致框架无法
脱离应用测试与复用。这些耦合已于 2026-10 改为「装配注入 + 内置兜底」。

本测试是**可执行的分层规则**：新增 import 一旦越界立即失败，无需人工评审。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INK_ROOT = _REPO_ROOT / "src/tui/ink"

#: 禁止依赖的模块前缀（应用层 / 核心层 / 渲染器 / 工具层 / 应用常量与输入层）。
_FORBIDDEN_PREFIXES = (
    "src.tui.app",
    "src.api",
    "src.core",
    "src.renderer",
    "src.tools",
    "src.config",
    "src.kernel",
    "src.plugins",
    "src.presentation_data",
    "src.declarative",
    "src.tui._const",
    "src.tui._config",
    "src.tui._screen",
    "src.tui._input",
    "src.tui._input_layout",
    "src.tui._input_metrics",
    "src.tui._input_cursor",
    "src.tui._system_monitor",
    "src.tui._assembly",
    "src.tui._assembly_steps",
    "src.tui._consumer",
    "src.tui.pipeline",
    "src.tui.events",
    "src.tui.state",
    "src.tui.subagent",
)

#: 允许依赖的 L0 白名单（纯数据/纯函数/协议；零 UI 状态与零应用逻辑）。
_ALLOWED_L0 = (
    "src._text_width",
    "src._ansi_text",
    "src._compat",
    "src.tui._width",
    "src.tui._ansi_sequences",
    "src.tui._command_protocol",
    "src.tui.core.style",
    "src.tui.core.color",
)


def _ink_modules() -> list[Path]:
    return sorted(path for path in _INK_ROOT.rglob("*.py") if "__pycache__" not in str(path))


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    """返回 ``(行号, 绝对模块名)``；相对导入解析为绝对名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = path.relative_to(_REPO_ROOT).with_suffix("")
    package = list(module.parts[:-1])
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                if node.module:
                    names.append(".".join([*base, node.module]))
                else:
                    names.extend(".".join([*base, alias.name]) for alias in node.names)
            elif node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            found.append((node.lineno, name))
    return found


def test_ink_has_no_application_layer_imports():
    """框架内不得出现应用层 / 核心层 / 渲染器 / 应用常量与输入层的 import。"""
    offenders: list[str] = []
    for path in _ink_modules():
        for lineno, name in _absolute_imports(path):
            if name.startswith(_FORBIDDEN_PREFIXES):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno} -> {name}")
    assert not offenders, (
        "ink 框架出现反向依赖（应改为装配注入 + 内置兜底）：\n" + "\n".join(offenders)
    )


def test_ink_only_depends_on_whitelisted_l0_modules():
    """框架对 ``src.*`` 的依赖必须落在 L0 白名单或自身包内。"""
    allowed = (*_ALLOWED_L0, "src.tui.ink", "src.tui")
    offenders: list[str] = []
    for path in _ink_modules():
        for lineno, name in _absolute_imports(path):
            if not name.startswith("src."):
                continue
            if name.startswith(allowed):
                continue
            offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno} -> {name}")
    assert not offenders, (
        "ink 依赖了白名单外的 src 模块：\n" + "\n".join(offenders)
    )


def test_no_module_level_lazy_import_of_app_layers():
    """禁止用「函数内 import」绕过分层（历史上的实际手法）。

    已有实现均改为注入；若未来有人用延迟 import 把应用逻辑塞回框架，本断言
    会失败——分层规则必须对**函数内 import** 同样生效。
    """
    offenders: list[str] = []
    for path in _ink_modules():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                names: list[str] = []
                if isinstance(child, ast.ImportFrom) and child.module and not child.level:
                    names.append(child.module)
                elif isinstance(child, ast.Import):
                    names.extend(alias.name for alias in child.names)
                for name in names:
                    if name.startswith(_FORBIDDEN_PREFIXES):
                        offenders.append(
                            f"{path.relative_to(_REPO_ROOT)}:{child.lineno} "
                            f"(in {node.name}) -> {name}"
                        )
    assert not offenders, (
        "框架内仍有函数级反向 import（延迟导入绕过分层）：\n" + "\n".join(offenders)
    )


def test_ink_options_match_tui_config_defaults():
    """``ink.options.InkOptions`` 的默认值必须与 ``TuiConfig`` 保持一致。

    框架用自己的默认选项（不 import 应用配置），两份默认值漂移会导致
    「独立 render() 会话」与「生产会话」帧率/队列行为不一致。
    """
    from src.tui._config import TuiConfig
    from src.tui.ink.options import InkOptions

    tui = TuiConfig.defaults()
    ink = InkOptions()
    for field in ("render_interval", "idle_render_interval", "drain_lock_timeout",
                  "cmd_queue_maxsize", "sys_stats_interval",
                  "max_recover_attempts", "recover_delay"):
        assert getattr(ink, field) == getattr(tui, field), f"{field} 默认值漂移"


@pytest.mark.parametrize("module_name", ["session", "renderer", "_render_api", "terminal"])
def test_framework_modules_import_without_application(module_name: str):
    """核心框架模块可独立 import（不触发应用层初始化）。"""
    __import__(f"src.tui.ink.{module_name}")
