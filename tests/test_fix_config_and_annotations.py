"""修复回归测试 — 配置地址解析 / 命令包导出 / 类型注解可解析性。

覆盖三项修复：
  1. ``config.loader.get_base_url(provider)`` 引用了未导入的 ``PROVIDERS``
     → 任何带 provider 的调用直接 ``NameError``（修复：补默认元数据导入）；
  2. ``src.core.internal.commands`` 的 ``__all__`` 声明了星号导入不含的
     下划线名 → ``from ... import *`` 抛 ``AttributeError``（修复：显式导入）；
  3. ``PipelineContext.agent`` / ``CommandPluginRegistry`` 的注解引用了未导入
     的名字 → ``typing.get_type_hints`` 抛 ``NameError``（修复：TYPE_CHECKING 导入）。
"""

from __future__ import annotations

import pathlib
import typing

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── 1. get_base_url（P1：NameError） ──────────────────────────


def test_get_base_url_returns_provider_default():
    from src.config.loader import get_base_url

    assert get_base_url("deepseek").startswith("http")


def test_get_base_url_no_provider_returns_empty():
    from src.config.loader import get_base_url

    assert get_base_url() == ""
    assert get_base_url(None) == ""


def test_get_base_url_unknown_provider_is_empty_string():
    from src.config.loader import get_base_url

    assert get_base_url("no-such-provider") == ""


def test_config_package_reexports_get_base_url():
    from src.config import get_base_url

    assert get_base_url("anthropic").startswith("http")


def test_config_service_base_url_with_provider():
    """内核 ``ctx.config.base_url(provider)`` 实际调用 loader.get_base_url。"""
    from src.plugins.config import ConfigService

    service = ConfigService.__new__(ConfigService)  # 跳过内核注入
    assert service.base_url("deepseek").startswith("http")


# ── 2. 命令包导出（P2：__all__ 悬空名） ───────────────────────


def test_internal_commands_all_names_exist():
    from src.core.internal import commands as mod

    missing = [name for name in mod.__all__ if not hasattr(mod, name)]
    assert missing == []


def test_internal_commands_star_import_works():
    ns: dict = {}
    exec("from src.core.internal.commands import *", ns)  # noqa: S102 - 测试用途
    for name in (
        "_commands",
        "_cmd_help",
        "_format_cost_duration",
        "_pop_assistant_tool_messages",
    ):
        assert name in ns, f"星号导入缺少 {name}"


# ── 3. 注解可解析（P2：未定义注解名） ─────────────────────────


def test_pipeline_context_annotations_have_no_undefined_names():
    """``PipelineContext`` 的注解不得引用未定义名（修复前为 ``NameError``）。

    注：Python 3.9 运行期无法求值 ``Exception | None`` 这类 PEP 604 写法，
    ``typing.get_type_hints`` 会抛 ``TypeError``（语言限制，与未定义名无关），
    因此这里只断言「不再出现 NameError」。
    """
    from src.core.pipeline import PipelineContext

    ann = PipelineContext.__annotations__
    assert "agent" in ann
    # 注解类型为 Any（agent ↔ pipeline 互依，跨模块导入会形成 import 环）
    assert ann["agent"] in (typing.Any, "Any")
    try:
        typing.get_type_hints(PipelineContext)
    except NameError as exc:  # pragma: no cover - 修复前形态
        pytest.fail(f"注解引用了未定义名: {exc}")
    except TypeError:
        pass  # Python 3.9：无法在运行期求值 ``X | Y``


def test_pipeline_avoids_agent_import_cycle():
    """agent ↔ pipeline 互依：任何跨模块导入（含 ``TYPE_CHECKING``）都会形成
    import 环，被架构守卫（``tests/test_architecture_layering.py``）禁止，
    因此注解类型使用 ``Any``。"""
    import src.core.pipeline as pipeline

    source = (REPO_ROOT / "src" / "core" / "pipeline.py").read_text(encoding="utf-8")
    assert "from .agent import" not in source
    assert "agent" in pipeline.PipelineContext.__annotations__


def test_commands_base_avoids_plugins_import_cycle():
    """commands.base ↔ commands.plugins.base 互依：跨模块导入（含 TYPE_CHECKING）
    会形成 import 环（架构守卫禁止），因此注解类型使用 ``Any``。"""
    from src.core.commands.base import CommandPluginRegistry

    source = (REPO_ROOT / "src" / "core" / "commands" / "base.py").read_text(
        encoding="utf-8",
    )
    assert "from .plugins.base import" not in source
    assert CommandPluginRegistry()._interactive_plugins == {}
