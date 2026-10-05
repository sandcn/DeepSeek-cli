"""Cordis 工具族 — Agent 在运行时自省与自修改插件树。

对应 dsh 的 ``cordis_inspect`` / ``cordis_define`` / ``cordis_run`` /
``cordis_stop`` / ``cordis_undefine``：模型可以检查当前运行时的服务与插件，
现场写一个插件文件挂载到自己的内核上，再按需停止/删除。

安全边界：动态插件只写进项目 ``.chat/runtime_plugins/`` 目录并挂载到当前
进程内存（不写 ``~/.chat_config``、不改 profile、不跨重启）。沙盒只约束
诚实代码，不是安全边界——应像对待 bash 一样对待它。

★ 本工具族已全局禁用（``tool_policy.GLOBAL_DISABLED_TOOLS``）：工具发现阶段
即被跳过，任何 agent（主 Agent 与全部 SubAgent 类型）都不能加载。类与实现
保留，供内核 / 测试直接使用。
"""

from __future__ import annotations

import os

from .base import Func
from ..paths import RUNTIME_PLUGINS_DIR, ensure_runtime_plugins_dir


def _kernel_or_error():
    from ..kernel import get_current_kernel

    kernel = get_current_kernel()
    if kernel is None:
        return None, "(无内核：当前进程未挂载插件内核，无法自省/自修改)"
    return kernel, ""


def _plugin_path(name: str) -> str:
    raw = str(name or "").strip()
    safe = raw[:-3] if raw.endswith(".py") else raw
    if (not safe or safe.startswith(".")
            or "/" in safe or "\\" in safe or os.sep in safe
            or (os.altsep and os.altsep in safe)):
        raise ValueError(f"插件名非法: {name!r}")
    return os.path.join(str(RUNTIME_PLUGINS_DIR), f"{safe}.py")


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
        ensure_runtime_plugins_dir()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(self.code)
        os.replace(tmp, path)
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
                    "（注册是可逆副作用；用 cordis_stop 卸载）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "插件名"}},
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
            path = _plugin_path(self.plugin_name)
        except ValueError as exc:
            return f"({exc})"
        if not os.path.isfile(path):
            return f"(插件文件不存在: {path}；先用 cordis_define 定义)"
        try:
            fiber = kernel.mount_file(path)
        except Exception as exc:
            return f"(挂载失败: {exc})"
        await kernel.settle()
        state = fiber.state.value
        if fiber.error is not None:
            return f"挂载失败: {fiber.name} -> {state} error={fiber.error!r}"
        return f"已挂载插件: {fiber.name} ({state})"


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
                    "properties": {"name": {"type": "string", "description": "插件名"}},
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
        fiber = kernel.fiber(name)
        if fiber is None and os.path.basename(name).endswith(".py"):
            fiber = kernel.fiber(os.path.basename(name)[:-3])
        if fiber is None:
            return f"(未找到插件: {name})"
        await fiber.dispose()
        await kernel.settle()
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
                    "properties": {"name": {"type": "string", "description": "插件名"}},
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
        fiber = kernel.fiber(name)
        if fiber is not None:
            await fiber.dispose()
            await kernel.settle()
            stopped = f"（已停止 {fiber.name}）"
        try:
            path = _plugin_path(name)
        except ValueError as exc:
            return f"({exc})"
        if os.path.isfile(path):
            os.remove(path)
            return f"已删除插件文件: {path}{stopped}"
        return f"(插件文件不存在: {path}){stopped}"
