"""插件引用解析 — 把清单中的字符串引用解析为 Plugin。

支持形式：

- ``module.attr`` 或 ``module:attr``：导入模块并取属性；
- ``module``：导入模块，优先取 ``plugin`` / ``PLUGIN`` 属性，否则扫描模块内
  所有 Plugin 对象；若模块只暴露一个可调用的 ``apply``，也识别为该插件；
- 直接传入的 Plugin / 可调用对象 / Service 子类。

本模块独立于 loader 与 config_tree，避免循环依赖。
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from typing import Any

from .errors import PluginError
from .plugin import Plugin, as_plugin


def resolve(ref: Any) -> Plugin:
    """把引用解析为 Plugin 实例。"""
    if isinstance(ref, Plugin):
        return ref
    if inspect.isclass(ref) or callable(ref):
        return as_plugin(ref)
    if not isinstance(ref, str) or not ref:
        raise PluginError(f"非法插件引用: {ref!r}")

    module_name, _, attr = ref.partition(":")
    if attr:
        module = _import_module(module_name)
        target = getattr(module, attr, None)
        if target is None:
            raise PluginError(f"模块 {module_name} 无属性 {attr!r}")
        return as_plugin(target, name=attr)
    try:
        module = _import_module(module_name)
    except PluginError:
        resolved = _resolve_dotted(module_name)
        if resolved is not None:
            return resolved
        raise
    return _plugin_from_module(module, module_name)


def _resolve_dotted(dotted: str) -> Plugin | None:
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:cut])
        attr = ".".join(parts[cut:])
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        target: Any = module
        for piece in attr.split("."):
            target = getattr(target, piece, None)
            if target is None:
                break
        if target is not None and not inspect.ismodule(target):
            return as_plugin(target, name=attr)
    return None


def _import_module(module_name: str):
    if module_name.endswith(".py") or os.path.sep in module_name:
        return _import_from_path(module_name)
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise PluginError(f"无法导入插件模块 {module_name!r}: {exc}") from exc


def _import_from_path(path: str):
    if not os.path.isfile(path):
        raise PluginError(f"插件文件不存在: {path!r}")
    module_name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法从路径加载插件: {path!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _plugin_from_module(module: Any, module_name: str) -> Plugin:
    for attr in ("plugin", "PLUGIN"):
        target = getattr(module, attr, None)
        if isinstance(target, Plugin):
            return target
    candidates = [
        obj for obj in vars(module).values()
        if isinstance(obj, Plugin)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise PluginError(
            f"模块 {module_name!r} 暴露多个插件: {[c.name for c in candidates]}；请用 module:attr 指定"
        )
    apply_fn = getattr(module, "apply", None)
    if callable(apply_fn):
        return as_plugin(apply_fn, name=module_name.rsplit(".", 1)[-1])
    raise PluginError(f"模块 {module_name!r} 未找到插件定义")


__all__ = ["resolve"]
