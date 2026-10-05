"""通知后端条目插件 — 清单中每个内置通知后端一个独立插件条目。

「一切皆插件」：内置通知后端（termux/linux/windows）不再硬编码在
``src.notifications`` 里，而是由清单中的独立条目声明::

    - id: notification_backend_termux
      plugin: src.plugins.notification_backends:apply_notification_backend
      config:
        id: termux                        # 内置后端 id（可被 patch/overlay 定位）
        # backend: my_pkg.MyBackend        # 可选：替换实现（点分路径）
        # kwargs: {..}                     # 可选：替换实现的构造参数

插件挂载时把该 id 的内置后端注册进通知后端注册表（``factory=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置
后端随之缺席（``notifications`` 聚合插件经 ``managed_notification_backends``
抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[], Any]]:
    ref = config.get("backend")
    if not ref:
        return None
    kwargs = dict(config.get("kwargs") or {})

    def _factory():
        from .tool_plugin import import_attr

        cls = import_attr(ref)
        return cls(**kwargs)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("notification_backend")
def apply_notification_backend(ctx):
    from ..notifications.registry import register_builtin_notification_backend

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("notification_backend 条目缺少 config.id")
    undo = register_builtin_notification_backend(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_notification_backend"]
