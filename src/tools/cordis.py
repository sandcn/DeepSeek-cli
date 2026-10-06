"""Cordis 工具族 — Agent 在运行时自省与自修改插件树。

对应 dsh 的 ``cordis_inspect`` / ``cordis_define`` / ``cordis_run`` /
``cordis_stop`` / ``cordis_undefine``：模型可以检查当前运行时的服务与插件，
现场写一个插件文件挂载到自己的内核上，再按需停止/删除。

安全边界：动态插件只写进项目 ``.chat/runtime_plugins/`` 目录并挂载到当前
进程内存（不写 ``~/.chat_config``、不改 profile、不跨重启）。动态插件运行在
**同一进程**，可篡改治理层（工具策略、全局禁用集合、服务注册表），因此本
工具族等价于进程内任意代码执行——沙盒只约束诚实代码，不是安全边界，应像
对待 bash 一样对待它。

★ 本工具族已全局禁用（``src.core.cordis_tools.CORDIS_TOOLS`` →
``tool_policy`` 全局禁用集合）：工具发现/注册阶段即被拒绝，任何 agent（主
Agent 与全部 SubAgent 类型）都不能加载。类与实现保留，供内核 / 测试直接使用。

语义约定（修复后）：

- 插件的 **文件名**（``<name>.py`` 的 ``<name>``）与插件内声明的 **插件名**
  （``@plugin('x')`` 的 ``x``）可以不同；``run`` / ``stop`` / ``undefine``
  的参数接受二者之一，内部按名与按文件统一解析；
- ``run`` 对同名文件的重复挂载是**幂等替换**：先卸载旧实例再挂载新实例，
  避免 Fiber 堆叠与僵尸服务；
- 多插件文件会挂载并报告全部插件，任一未激活（PENDING/FAILED）都以失败文本
  返回（以 ``(`` 开头），并给出阻塞原因。
"""

from __future__ import annotations

import os
import re

from .base import Func
from ..paths import RUNTIME_PLUGINS_DIR, ensure_runtime_plugins_dir

#: 插件名最大长度（同时约束生成的文件名长度）
_MAX_PLUGIN_NAME_LEN = 128


def _kernel_or_error():
    from ..kernel import get_current_kernel

    kernel = get_current_kernel()
    if kernel is None:
        return None, "(无内核：当前进程未挂载插件内核，无法自省/自修改)"
    return kernel, ""


# ── 名字 / 路径解析 ─────────────────────────────────────────


def _strip_py_suffix(name: str) -> str:
    raw = str(name or "").strip()
    return raw[:-3] if raw.lower().endswith(".py") else raw


def _validate_plugin_name(name: str) -> str:
    """校验并归一化插件名（文件 stem）；非法抛 ``ValueError``。"""
    raw = str(name or "").strip()
    safe = _strip_py_suffix(raw)
    if not safe or safe.startswith(".") or safe.startswith("-"):
        raise ValueError(f"插件名非法: {name!r}")
    if safe in ("__init__", "__main__"):
        raise ValueError(f"插件名非法（Python 保留模块名）: {name!r}")
    if len(safe) > _MAX_PLUGIN_NAME_LEN:
        raise ValueError(f"插件名过长（最大 {_MAX_PLUGIN_NAME_LEN} 字符）: {name!r}")
    separators = {"/", "\\", os.sep, os.altsep}
    if any(sep and sep in safe for sep in separators):
        raise ValueError(f"插件名非法（含路径分隔符）: {name!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in safe):
        raise ValueError(f"插件名非法（含控制字符）: {name!r}")
    if ":" in safe:
        raise ValueError(f"插件名非法（含冒号）: {name!r}")
    from .file_ops import dos_device_names

    if safe.split(".")[0].upper() in dos_device_names():
        raise ValueError(f"插件名非法（Windows 保留设备名）: {name!r}")
    return safe


def _plugin_path(name: str) -> str:
    safe = _validate_plugin_name(name)
    path = os.path.join(str(RUNTIME_PLUGINS_DIR), f"{safe}.py")
    # 纵深防御：与 write_file 等文件工具共用同一路径安全校验（设备文件/系统
    # 关键路径/Windows 保留名/NTFS 流），拒绝时抛 ValueError 由调用方转为错误文本。
    from .file_ops import validate_path_security

    validate_path_security(path)
    return path


def _fiber_file_stem(fiber) -> str:
    """从 Fiber 反推其来源插件文件的 stem（非文件插件返回空串）。

    模块名格式来自 ``kernel.loader.module_name_for``（延迟导入，避免 tools →
    kernel 的模块级依赖）：``<prefix><sha1[:8]>_<stem>``。
    """
    definition = getattr(fiber, "definition", None)
    source = str(getattr(definition, "source", "") or "")
    if not source:
        return ""
    from ..kernel.loader import MODULE_NAME_PREFIX

    match = re.match(rf"^{re.escape(MODULE_NAME_PREFIX)}[0-9a-f]{{8}}_(.+)$", source)
    return match.group(1) if match else ""


def _file_module_name(stem: str) -> str:
    """``<stem>.py`` 对应的 loader 模块名（用于精确识别「本目录文件插件」）。"""
    from ..kernel.loader import module_name_for

    return module_name_for(_plugin_path(stem))


def _runtime_fibers(kernel, stem: str, *, include_disposed: bool = False) -> list:
    """返回来自 ``RUNTIME_PLUGINS_DIR/<stem>.py`` 的 Fiber（按模块名精确匹配）。

    用模块名（含源文件绝对路径哈希）而非文件名 stem 匹配——同名但位于其它
    目录（外部 ``./plugins``、内置插件）的 Fiber 不会被误判/误卸载。
    """
    if not stem:
        return []
    try:
        target = _file_module_name(stem)
    except ValueError:
        return []
    result = [
        fiber for fiber in kernel.fibers()
        if str(getattr(getattr(fiber, "definition", None), "source", "") or "") == target
    ]
    if not include_disposed:
        result = [fiber for fiber in result if not fiber.disposed]
    return result


def _resolve_runtime_fiber(kernel, name: str):
    """按「运行时插件文件名或插件名」解析 Fiber。

    优先 ``RUNTIME_PLUGINS_DIR/<name>.py`` 精确匹配（可解析「插件名 ≠ 文件名」
    的运行时插件），其次内核按插件名匹配（``kernel.fiber``）。
    """
    raw = str(name or "").strip()
    if not raw:
        return None
    stem = _strip_py_suffix(raw)
    matches = _runtime_fibers(kernel, stem)
    if matches:
        return matches[-1]
    for candidate in (raw, stem):
        if not candidate:
            continue
        fiber = kernel.fiber(candidate)
        if fiber is not None:
            return fiber
    return None


def _resolve_plugin_file(kernel, name: str) -> str:
    """解析运行时插件文件路径（支持插件名或文件名）；无法确定返回空串。"""
    candidates: list = []
    try:
        candidates.append(_plugin_path(name))
    except ValueError:
        pass
    if not candidates or not os.path.isfile(candidates[0]):
        fiber = _resolve_runtime_fiber(kernel, name)
        stem = _fiber_file_stem(fiber) if fiber is not None else ""
        if stem:
            try:
                candidates.append(_plugin_path(stem))
            except ValueError:
                pass
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[0] if candidates else ""


def _unwatch(kernel, path: str) -> None:
    """取消对某运行时插件文件的热重载监听（无监听/无 watcher 时静默）。"""
    if not path:
        return
    try:
        kernel.watcher().unwatch_file(path)
    except Exception:
        pass


def _remove_quietly(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


async def _dispose_existing(kernel, stem: str) -> bool:
    """卸载同一运行时插件文件的既有活动实例（幂等重挂载）；返回是否替换。"""
    existing = _runtime_fibers(kernel, stem)
    if not existing:
        return False
    for fiber in existing:
        try:
            await fiber.dispose()
        except Exception:
            pass
    await kernel.settle()
    return True


# ── 工具实现 ────────────────────────────────────────────────


class CordisInspectFunc(Func):
    """自省当前内核：已提供服务的 key/类型、插件 Fiber 状态与依赖。"""

    name = "cordis_inspect"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "cordis_inspect",
                "description": (
                    "检查当前 Agent 运行时的插件内核：列出已提供的服务（ctx.<key>）、"
                    "插件 Fiber 的状态与依赖。用于了解当前系统真实具备的能力。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "what": {
                            "type": "string",
                            "enum": ["all", "services", "plugins"],
                            "description": "查看内容：all（默认）/ services / plugins",
                        },
                        "name": {
                            "type": "string",
                            "description": "可选：只看某个服务或插件名",
                        },
                    },
                    "required": [],
                },
            },
        }

    def __init__(self, what: str = "all", name: str = ""):
        super().__init__()
        self.what = what or "all"
        self.name_filter = name or ""

    async def execute(self) -> str:
        if self.what not in ("all", "services", "plugins"):
            return f"(无效的 what 值: {self.what!r}；可用: all / services / plugins)"
        kernel, error = _kernel_or_error()
        if kernel is None:
            return error

        lines = []
        if self.what in ("all", "plugins"):
            lines.append("fibers:")
            for fiber in kernel.fibers():
                if self.name_filter and fiber.name != self.name_filter:
                    continue
                state = fiber.state.value
                extra = f" error={fiber.error!r}" if fiber.error else ""
                lines.append(f"  - {fiber.name}: {state}{extra} inject={list(fiber.inject)}")
        if self.what in ("all", "services"):
            lines.append("services:")
            for key in sorted(kernel.service_keys()):
                if self.name_filter and key != self.name_filter:
                    continue
                value = kernel.resolve_service(key)
                lines.append(f"  - {key}: {type(value).__name__}")
        return "\n".join(lines) if lines else "(无匹配项)"


class CordisDefineFunc(Func):
    """把插件源码写入 ``.chat/runtime_plugins/<name>.py``（不自动挂载）。"""

    name = "cordis_define"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "cordis_define",
                "description": (
                    "在 .chat/runtime_plugins/<name>.py 写入一段插件源码（定义 apply(ctx) "
                    "或 @plugin）。写入后需用 cordis_run 挂载。用于 Agent 在运行时为自己"
                    "创造新能力。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "插件名（用作文件名）"},
                        "code": {"type": "string", "description": "插件源码（Python）"},
                    },
                    "required": ["name", "code"],
                },
            },
        }

    def __init__(self, name: str, code: str):
        super().__init__()
        self.plugin_name = name
        self.code = code

    async def execute(self) -> str:
        try:
            path = _plugin_path(self.plugin_name)
        except ValueError as exc:
            return f"({exc})"
        if not self.code or not str(self.code).strip():
            return "(插件源码不能为空)"
        try:
            compile(self.code, path, "exec")
        except SyntaxError as exc:
            return f"(插件源码语法错误: {exc})"
        tmp = path + ".tmp"
        try:
            ensure_runtime_plugins_dir()
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(self.code)
            os.replace(tmp, path)
        except OSError as exc:
            _remove_quietly(tmp)
            return f"(写入失败: {exc})"
        self._publish_tool_text(f"  + 已定义插件 {os.path.basename(path)}")
        return f"已定义插件: {path}（用 cordis_run 挂载）"


class CordisRunFunc(Func):
    """把 ``.chat/runtime_plugins/<name>.py`` 挂载到当前内核（热挂载）。"""

    name = "cordis_run"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "cordis_run",
                "description": (
                    "从 .chat/runtime_plugins/<name>.py 加载并挂载插件到当前内核"
                    "（注册是可逆副作用；用 cordis_stop 卸载）。同名文件重复挂载会"
                    "先卸载旧实例再挂载（幂等替换）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "插件名或文件名"}},
                    "required": ["name"],
                },
            },
        }

    def __init__(self, name: str):
        super().__init__()
        self.plugin_name = name

    async def execute(self) -> str:
        kernel, error = _kernel_or_error()
        if kernel is None:
            return error
        try:
            stem = _validate_plugin_name(self.plugin_name)
            path = _plugin_path(stem)
        except ValueError as exc:
            return f"({exc})"
        if not os.path.isfile(path):
            return f"(插件文件不存在: {path}；先用 cordis_define 定义)"

        replaced = await _dispose_existing(kernel, stem)
        try:
            fibers = kernel.mount_file_all(path)
        except Exception as exc:
            return f"(挂载失败: {exc})"
        await kernel.settle()
        try:
            kernel.watch_file(path)
        except Exception:
            pass

        lines = []
        activated = True
        for fiber in fibers:
            if fiber.error is not None:
                activated = False
                lines.append(f"{fiber.name}: {fiber.state.value} error={fiber.error!r}")
            elif not fiber.active:
                activated = False
                reasons = kernel.why_blocked(fiber.name)
                lines.append(f"{fiber.name}: {fiber.state.value}（未激活: {'; '.join(reasons)}）")
            else:
                lines.append(f"{fiber.name}: {fiber.state.value}")
        detail = ", ".join(lines)
        suffix = "（已替换旧实例）" if replaced else ""
        if not activated:
            return f"(挂载未激活: {detail}{suffix})"
        return f"已挂载插件: {detail}{suffix}"


class CordisStopFunc(Func):
    """卸载指定插件 Fiber（连带撤销其注册的副作用）。"""

    name = "cordis_stop"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "cordis_stop",
                "description": "卸载当前内核中指定名字的插件（撤销其服务/监听器等注册）。",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "插件名或文件名"}},
                    "required": ["name"],
                },
            },
        }

    def __init__(self, name: str):
        super().__init__()
        self.plugin_name = name

    async def execute(self) -> str:
        kernel, error = _kernel_or_error()
        if kernel is None:
            return error
        name = str(self.plugin_name or "")
        fiber = _resolve_runtime_fiber(kernel, name)
        if fiber is None:
            return f"(未找到插件: {name})"
        path = _resolve_plugin_file(kernel, name) or _resolve_plugin_file(kernel, fiber.name)
        try:
            await fiber.dispose()
        except Exception as exc:
            return f"(停止失败: {exc})"
        await kernel.settle()
        _unwatch(kernel, path)
        return f"已停止插件: {fiber.name}"


class CordisUndefineFunc(Func):
    """卸载插件并删除其 ``.chat/runtime_plugins/`` 文件。"""

    name = "cordis_undefine"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "cordis_undefine",
                "description": "停止运行时插件并删除其插件文件。",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "插件名或文件名"}},
                    "required": ["name"],
                },
            },
        }

    def __init__(self, name: str):
        super().__init__()
        self.plugin_name = name

    async def execute(self) -> str:
        kernel, error = _kernel_or_error()
        if kernel is None:
            return error
        name = str(self.plugin_name or "")
        stopped = ""
        fiber = _resolve_runtime_fiber(kernel, name)
        if fiber is not None:
            try:
                await fiber.dispose()
            except Exception as exc:
                return f"(停止失败: {exc})"
            await kernel.settle()
            stopped = f"（已停止 {fiber.name}）"
        path = _resolve_plugin_file(kernel, name)
        if not path and fiber is not None:
            path = _resolve_plugin_file(kernel, fiber.name)
        if not path:
            return f"(插件名非法或文件不存在: {name}){stopped}"
        _unwatch(kernel, path)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as exc:
                return f"(删除失败: {exc}){stopped}"
            return f"已删除插件文件: {path}{stopped}"
        return f"(插件文件不存在: {path}){stopped}"
