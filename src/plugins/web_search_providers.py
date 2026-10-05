"""Web 搜索提供者条目插件 — 清单中每个内置提供者一个独立插件条目。

「一切皆插件」：内置搜索提供者（deepseek）不再由工具类硬编码，而是由清单中
的独立条目声明::

    - id: web_search_provider_deepseek
      plugin: src.plugins.web_search_providers:apply_search_provider
      config:
        id: deepseek                       # 内置提供者 id（可被 patch/overlay 定位）
        # provider: my_pkg.MyProvider      # 可选：替换实现（点分路径）

插件挂载时把该 id 的内置提供者注册进搜索提供者注册表（``provider=None`` 用
默认实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，
对应内置提供者随之缺席（``web_search`` 聚合插件经 ``managed_search_providers``
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


@plugin("web_search_provider")
def apply_search_provider(ctx):
    from ..tools.search_provider_registry import register_builtin_search_provider

    spec_id = ctx.config.get("id") or ctx.config.get("name")
    if not spec_id:
        raise ValueError("web_search_provider 条目缺少 config.id")
    undo = register_builtin_search_provider(spec_id, _make_override_factory(ctx.config, "provider"))
    ctx.effect(lambda: undo)


__all__ = ["apply_search_provider"]
