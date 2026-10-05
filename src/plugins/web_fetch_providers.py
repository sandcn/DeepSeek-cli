"""Web 抓取提供者条目插件 — 清单中每个内置提供者一个独立插件条目。

「一切皆插件」：内置抓取提供者（http）不再由工具直接调用，而是由清单中的
独立条目声明::

    - id: web_fetch_provider_http
      plugin: src.plugins.web_fetch_providers:apply_fetch_provider
      config:
        id: http                           # 内置提供者 id（可被 patch/overlay 定位）
        # provider: my_pkg.MyFetcher       # 可选：替换实现（点分路径）

插件挂载时把该 id 的内置提供者注册进抓取提供者注册表（``provider=None`` 用
默认实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，
对应内置提供者随之缺席（``web_fetch`` 聚合插件经 ``managed_fetch_providers``
抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict, key: str) -> Optional[Callable[[], Any]]:
    ref = config.get(key)
    if not ref:
        return None

    def _factory():
        from .tool_plugin import import_attr

        return import_attr(ref)()

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("web_fetch_provider")
def apply_fetch_provider(ctx):
    from ..tools.fetch_provider_registry import register_builtin_fetch_provider

    spec_id = ctx.config.get("id") or ctx.config.get("name")
    if not spec_id:
        raise ValueError("web_fetch_provider 条目缺少 config.id")
    undo = register_builtin_fetch_provider(spec_id, _make_override_factory(ctx.config, "provider"))
    ctx.effect(lambda: undo)


__all__ = ["apply_fetch_provider"]
