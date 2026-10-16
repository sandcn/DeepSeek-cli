"""Agent 主循环中间件插件化测试。

覆盖：
- 中间件注册表内置项（id + 工厂）；
- Agent 从注册表装配内置中间件；
- disable_builtin_middleware 生效；
- register_middleware 追加扩展；
- agent_middleware 插件按清单 config 禁用（Fiber 卸载恢复）。
"""

from __future__ import annotations

import json

import pytest

from src.core.middleware.registry import (
    builtin_middleware_factories,
    builtin_middleware_ids,
    disable_builtin_middleware,
    middleware_factories,
    register_middleware,
)
from src.plugins.bootstrap import build_kernel, shutdown_kernel


def _agent_middleware_names():
    from src.core.agent import Agent

    return [type(m).__name__ for m in Agent().pipeline.async_middlewares]


def test_builtin_middleware_ids():
    assert builtin_middleware_ids() == ["interrupt", "observability", "audit"]


def test_agent_assembles_builtin_middleware():
    names = _agent_middleware_names()
    assert "_InterruptCheckMiddleware" in names
    assert "_AsyncObservabilityMiddleware" in names
    assert "_AuditLogMiddleware" in names


def test_disable_builtin_middleware():
    undo = disable_builtin_middleware(["audit"])
    try:
        assert len(builtin_middleware_factories()) == 2
        assert "_AuditLogMiddleware" not in _agent_middleware_names()
    finally:
        undo()
    assert "_AuditLogMiddleware" in _agent_middleware_names()


def test_register_extension_middleware():
    class _Marker:
        async def before_model_call(self, ctx):
            return None

    undo = register_middleware(lambda: _Marker())
    try:
        assert len(middleware_factories()) == 1
        assert "_Marker" in _agent_middleware_names()
    finally:
        undo()
    assert middleware_factories() == ()


async def test_agent_middleware_plugin_disable(tmp_path):
    patch = {"replace": [{
        "id": "runtime::agent_middleware",
        "config": {"disabled": ["audit"]},
    }]}
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(patch), encoding="utf-8")
    kernel = await build_kernel("cli", patch_paths=[str(path)])
    try:
        assert len(builtin_middleware_factories()) == 2
        assert "_AuditLogMiddleware" not in _agent_middleware_names()
    finally:
        await shutdown_kernel(kernel)
    assert len(builtin_middleware_factories()) == 3
    assert "_AuditLogMiddleware" in _agent_middleware_names()
