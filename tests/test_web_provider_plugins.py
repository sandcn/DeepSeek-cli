"""Web 搜索/抓取提供者插件测试 — 每个内置提供者一个清单条目。

覆盖：
- 清单为每个内置搜索/抓取提供者声明独立条目（id 一一对应）；
- 默认 profile 经独立条目注册全部内置提供者；
- overlay 禁用单个提供者真正生效（聚合插件抑制默认装配）；
- 条目 config 的 provider 引用可替换实现；
- 工具经内核服务解析提供者 / 无内核回退进程级注册表；
- 直接 API：register/unregister/set_managed/disable 与 reset。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)

import src.tools.fetch_provider_registry as fpr
import src.tools.search_provider_registry as spr


class _FakeSearchProvider:
    async def search(self, query, client=None):
        from src.tools.search_providers import SearchResult

        result = SearchResult()
        result.sources = [{"title": "fake", "link": "https://example.com"}]
        return result


class _FakeFetchProvider:
    async def fetch(self, url, client=None):
        return {"title": "fake", "url": url, "domain": "example.com", "date": "", "body": "body"}

    def format(self, data):
        return data["body"]


@pytest.fixture(autouse=True)
def _clean_registries():
    spr.reset()
    fpr.reset()
    yield
    spr.reset()
    fpr.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_provider():
    search = []
    fetch = []
    for entry, plug in _resolved():
        name = getattr(plug, "name", "")
        if name == "web_search_provider":
            search.append((entry.id, (entry.config or {}).get("id")))
        elif name == "web_fetch_provider":
            fetch.append((entry.id, (entry.config or {}).get("id")))
    assert [spec_id for _, spec_id in search] == spr.builtin_search_provider_ids()
    assert [spec_id for _, spec_id in fetch] == fpr.builtin_fetch_provider_ids()
    assert all(entry_id.startswith("web::web_search_provider_") for entry_id, _ in search)
    assert all(entry_id.startswith("web::web_fetch_provider_") for entry_id, _ in fetch)


def test_tree_declares_web_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "web" in tree.bundles()
    assert "web" in tree.bundle("core").includes


async def test_default_profile_registers_all_builtin(cli_kernel):
    assert set(spr.builtin_search_provider_factories()) == {"deepseek"}
    assert set(fpr.builtin_fetch_provider_factories()) == {"http"}
    assert cli_kernel.resolve_service("web_search").resolve_provider() is not None
    assert cli_kernel.resolve_service("web_fetch").resolve_provider() is not None


async def test_service_introspection(cli_kernel):
    ws = cli_kernel.resolve_service("web_search")
    wf = cli_kernel.resolve_service("web_fetch")
    assert ws.provider_names() == ["deepseek"]
    assert wf.provider_names() == ["http"]
    assert ws.builtin_provider_ids() == spr.builtin_search_provider_ids()
    assert wf.builtin_provider_ids() == fpr.builtin_fetch_provider_ids()


async def _build_with_disable(ids):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), {"disable": list(ids)})
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


async def test_overlay_disable_search_provider():
    kernel = await _build_with_disable(["web::web_search_provider_deepseek"])
    try:
        assert spr.builtin_search_provider_factories() == {}
        assert spr.resolve_search_provider() is None
    finally:
        await kernel.dispose()


async def test_overlay_disable_fetch_provider():
    kernel = await _build_with_disable(["web::web_fetch_provider_http"])
    try:
        assert fpr.builtin_fetch_provider_factories() == {}
        assert fpr.resolve_fetch_provider() is None
    finally:
        await kernel.dispose()


async def test_entry_replaces_search_provider():
    from src.plugins.config import apply as config_apply
    from src.plugins.web_search import apply as ws_apply
    from src.plugins.web_search_providers import apply_search_provider

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(ws_apply)
    await kernel.settle()
    kernel.mount(
        apply_search_provider,
        config={"id": "deepseek", "provider": "tests.test_web_provider_plugins._FakeSearchProvider"},
    )
    await kernel.settle()
    try:
        provider = spr.resolve_search_provider("deepseek")
        assert isinstance(provider, _FakeSearchProvider)
    finally:
        await kernel.dispose()


async def test_entry_replaces_fetch_provider():
    from src.plugins.config import apply as config_apply
    from src.plugins.web_fetch import apply as wf_apply
    from src.plugins.web_fetch_providers import apply_fetch_provider

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(wf_apply)
    await kernel.settle()
    kernel.mount(
        apply_fetch_provider,
        config={"id": "http", "provider": "tests.test_web_provider_plugins._FakeFetchProvider"},
    )
    await kernel.settle()
    try:
        provider = fpr.resolve_fetch_provider("http")
        assert isinstance(provider, _FakeFetchProvider)
    finally:
        await kernel.dispose()


def test_resolve_fallback_without_kernel():
    from src.tools.fetch_providers import HttpPageFetcher
    from src.tools.search_providers import DeepSeekSearchProvider

    assert isinstance(spr.resolve_search_provider(), DeepSeekSearchProvider)
    assert isinstance(fpr.resolve_fetch_provider(), HttpPageFetcher)


def test_register_and_managed_roundtrip():
    undo_reg = spr.register_builtin_search_provider("deepseek")
    try:
        assert set(spr.builtin_search_provider_factories()) == {"deepseek"}
    finally:
        undo_reg()
    undo_manage = spr.set_managed_builtin_search_providers(["deepseek"])
    try:
        assert spr.builtin_search_provider_factories() == {}
        undo_reg = spr.register_builtin_search_provider("deepseek")
        try:
            assert set(spr.builtin_search_provider_factories()) == {"deepseek"}
        finally:
            undo_reg()
    finally:
        undo_manage()
    assert set(spr.builtin_search_provider_factories()) == {"deepseek"}
    with pytest.raises(KeyError):
        spr.register_builtin_search_provider("nope")


def test_extension_search_provider():
    undo = spr.register_search_provider("mine", lambda: _FakeSearchProvider())
    try:
        assert "mine" in spr.search_provider_factories()
        assert isinstance(spr.resolve_search_provider("mine"), _FakeSearchProvider)
    finally:
        undo()
    assert spr.search_provider_factories() == {}


def test_disable_and_fetch_extension():
    undo = fpr.disable_builtin_fetch_providers(["http"])
    try:
        assert fpr.builtin_fetch_provider_factories() == {}
    finally:
        undo()
    assert set(fpr.builtin_fetch_provider_factories()) == {"http"}
