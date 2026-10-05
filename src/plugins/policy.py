"""策略插件 — 提供 ``ctx.policy``。

独立的运行时策略：工具可用性（按 agent 类型排除表）、文件写入路径白名单、
沙盒管理。策略不提供新工具，只决定某次调用是否符合约束。

策略经内核事件 ``tools/pre-execute``（waterfall）挂进工具执行管线：任何工具
调用在 dispatch 之前先经策略裁决，拒绝则直接返回拒绝结果（与 dsh 的
``tools/pre-execute`` allow/deny 决策瀑布同构）。
"""

from __future__ import annotations

import logging

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class _PolicySandboxProvider:
    """策略沙盒 Provider — 经 ``validate_path_security`` 做路径安全校验。"""

    def __init__(self, policy):
        self._policy = policy

    def check_path(self, path, *, operation: str = "read"):
        try:
            from ..tools.file_ops import validate_path_security

            validate_path_security(path)
        except Exception as exc:  # noqa: BLE001 - 校验失败即拒绝
            return False, str(exc)
        return True, None

    def check_argv(self, argv):
        return True, None

    def wrap_argv(self, argv):
        return argv

    def wrap_env(self, env):
        return env


class PolicyService(Service):
    """策略服务 — 占据 ``ctx.policy``。"""

    provide = "policy"
    name = "policy"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        # ── 全局禁用工具集合（config 可覆盖静态常量） ──
        from ..tools.tool_policy import GLOBAL_DISABLED_TOOLS

        configured = (config or ctx.config or {}).get("globally_disabled_tools")
        self._global_disabled = (
            frozenset(configured) if configured is not None
            else frozenset(GLOBAL_DISABLED_TOOLS)
        )
        # 注册策略钩子（注册即副作用；卸载时自动移除）
        ctx.on("tools/pre-execute", self._on_pre_execute)
        # 为 sandbox 能力接缝注册策略 Provider（注册即副作用，卸载时回退默认）
        self._sandbox_previous = None
        if ctx.has("sandbox"):
            try:
                self._sandbox_previous = ctx.consume("sandbox").set_provider(
                    _PolicySandboxProvider(self)
                )
                ctx.effect(lambda: (lambda: self._restore_sandbox()))
            except Exception:
                _logger.debug("注册 policy sandbox provider 失败", exc_info=True)

    def _restore_sandbox(self) -> None:
        if self._sandbox_previous is None:
            return
        try:
            self.ctx.consume("sandbox").set_provider(self._sandbox_previous)
        except Exception:
            _logger.debug("恢复 sandbox provider 失败", exc_info=True)
        self._sandbox_previous = None

    # ── 工具可用性 ───────────────────────────────────────

    def globally_disabled_tools(self) -> frozenset:
        """全局禁用工具集合（任何 agent 都不能加载）。"""
        return self._global_disabled

    def excluded_tools(self, agent_type: str = "execute") -> set:
        from ..core.agent_types import excluded_tools

        # 主 Agent / 未标注类型（agent_type 为 None/空）不受子代理排除表约束
        if not agent_type:
            return set()
        return excluded_tools(agent_type)

    def exclusion_map(self) -> dict:
        from ..tools.tool_policy import TOOL_EXCLUSION_MAP

        return TOOL_EXCLUSION_MAP

    def check(self, tool_name: str, agent_type: str = "execute", path: str | None = None):
        """裁决某次工具调用是否允许（不经过全局函数，避免解析递归）。

        ``agent_type`` 为 None/空表示主 Agent（无子代理类型）——排除表不适用，
        仅放行；只有明确的子代理类型（map/review/plan/execute）才按表排除。
        写入路径白名单标识（如 ``plan``）来自 Agent 类型注册表（类型是清单中
        的独立插件条目）。
        """
        from ..core.agent_types import path_whitelist

        if agent_type and tool_name in self.excluded_tools(agent_type):
            return (
                False,
                f"工具 '{tool_name}' 不可用于 '{agent_type}' 类型 agent，"
                f"该 agent 类型的工具白名单已排除此工具",
            )
        if path is not None and path_whitelist(agent_type) == "plan" and tool_name in (
            "write_file",
            "update_file",
            "mkdir",
        ):
            from ..tools.file_ops import get_plan_allowed_dir, is_path_within_dir

            allowed_dir = get_plan_allowed_dir()
            if not is_path_within_dir(path, allowed_dir):
                import os

                return (
                    False,
                    f"plan agent 只能在 {allowed_dir} 目录下写入文件。"
                    f"当前路径: {path}（解析后: {os.path.realpath(path)}），"
                    f"不在允许的目录: {allowed_dir}",
                )
        return (True, None)

    def can_use(self, tool_name: str, agent_type: str = "execute", path: str | None = None):
        from ..tools.base import Func

        return Func.can_use(tool_name, agent_type, path)

    # ── 工具执行管线钩子 ─────────────────────────────────

    async def _on_pre_execute(self, call, next_):
        # ★ 主 Agent 不设 agent_type（None）——不得回退为 "execute"：那会把主
        #   Agent 当成 execute 型 SubAgent，user_select/web_search/subagent 等
        #   被误拒（修复前 bug）。None 交给 check() 按「主 Agent」放行。
        agent_type = call.get("agent_type")
        arguments = call.get("arguments") or {}
        path = arguments.get("path") if isinstance(arguments, dict) else None
        allowed, reason = self.check(call.get("name", ""), agent_type, path)
        if not allowed:
            return {"allow": False, "reason": reason}
        return await next_()

    # ── 文件路径白名单 ───────────────────────────────────

    def plan_allowed_dir(self) -> str:
        from ..tools.file_ops import get_plan_allowed_dir

        return get_plan_allowed_dir()

    def is_path_allowed(self, path: str) -> bool:
        from ..tools.file_ops import get_plan_allowed_dir, is_path_within_dir

        return is_path_within_dir(path, get_plan_allowed_dir())

    # ── 沙盒 ─────────────────────────────────────────────

    def sandbox(self):
        from ..core.sandbox_manager import get_sandbox_manager

        return get_sandbox_manager()


@plugin("policy", provide=["policy"])
def apply(ctx):
    return PolicyService(ctx)
