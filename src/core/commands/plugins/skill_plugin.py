"""SkillPlugin — 技能管理命令 (/skill)

子命令：
- ``/skill`` / ``/skill list``      — 列出全部可用技能
- ``/skill list installed``         — 列出 GitHub 已安装仓库
- ``/skill install <owner/repo[@ref]>`` — 从 GitHub 安装技能
- ``/skill update <owner/repo|owner__repo|技能名>`` — 更新已安装技能
- ``/skill remove <owner/repo|owner__repo|技能名>`` — 卸载
- ``/skill info <技能名>``          — 查看技能详情
- ``/skill refresh``               — 清空目录缓存（技能文件变更后）

TUI 下 install/update 走异步路径（不阻塞事件循环）；
Web 端经旧命令系统同步执行，install/update 提示到终端执行。
"""

from __future__ import annotations

import logging
from typing import Any, List

from ....skills import (
    GithubSkillInstaller,
    SkillInstallError,
    default_registry,
    is_model_invocable,
    is_user_invocable,
)
from ..base import CommandMeta, get_plugin_registry
from .base import InteractiveCommandPlugin

_logger = logging.getLogger(__name__)

# 终端配色（TUI 有 ChatUI 时使用）
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_CYAN = "\x1b[36m"


class SkillPlugin(InteractiveCommandPlugin):
    """技能管理 (/skill)。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="skill",
            description="技能管理：list / install <owner/repo> / update / remove / info / refresh",
            usage="[list|install <仓库>|update <目标>|remove <目标>|info <技能名>|refresh]",
            group="general",
        )

    # ── 输出辅助 ─────────────────────────────────────────

    def _write(self, text: str) -> None:
        loop = self._loop
        if loop is not None and getattr(loop, "_chat_ui", None) is not None:
            loop._chat_ui.write_line(text)
        else:
            self.output(text + "\n")

    def _write_lines(self, lines: List[str]) -> None:
        for line in lines:
            self._write(line)

    @staticmethod
    def _flag(invocation, model: bool, user: bool) -> str:
        parts = []
        if model:
            parts.append("模型")
        if user:
            parts.append("用户")
        return "/".join(parts) if parts else "无"

    # ── 子命令实现 ───────────────────────────────────────

    def _cmd_list(self, only_installed: bool = False) -> None:
        registry = default_registry()
        lines = [f"  {_DIM}可用技能:{_RESET}"]
        if only_installed:
            installer = GithubSkillInstaller(installed_root=registry.installed_root())
            installed = installer.list_installed()
            if not installed:
                self._write(f"  {_YELLOW}尚未安装任何 GitHub 技能{_RESET}")
                return
            for item in installed:
                skills = ", ".join(item["skills"]) or "-"
                lines.append(
                    f"  {_CYAN}{item['id']}{_RESET}  {_DIM}{item['owner']}/{item['repo']}"
                    f"@{item['ref']} → {skills}{_RESET}"
                )
            self._write_lines(lines)
            return
        skills = registry.list()
        if not skills:
            self._write(f"  {_YELLOW}暂无可用技能。{_RESET}")
            self._write(
                f"  {_DIM}技能存放在项目 ./.skills（目录包 <name>/SKILL.md 或 <name>.md）；"
                f"安装: /skill install owner/repo{_RESET}"
            )
            return
        for skill in skills:
            flag = self._flag(skill.invocation, is_model_invocable(skill), is_user_invocable(skill))
            when = f" {_DIM}{skill.when_to_use}{_RESET}" if skill.when_to_use else ""
            lines.append(f"  {_CYAN}{skill.name}{_RESET} [{flag}] {skill.description}{when}")
        self._write_lines(lines)

    def _cmd_info(self, name: str) -> None:
        registry = default_registry()
        skill = registry.get(name)
        if skill is None:
            self._write(f"  {_YELLOW}技能 \"{name}\" 不存在{_RESET}")
            return
        self._write_lines([
            f"  {_CYAN}{skill.name}{_RESET} — {skill.description}",
            f"  {_DIM}来源: {skill.source} · 提供者: {skill.provider} · "
            f"rank: {skill.rank}{_RESET}",
            f"  {_DIM}调用: 模型={'是' if is_model_invocable(skill) else '否'} / "
            f"用户={'是' if is_user_invocable(skill) else '否'}{_RESET}",
            f"  {_DIM}路径: {skill.path or '-'}{_RESET}",
        ])
        if skill.when_to_use:
            self._write(f"  {_DIM}适用: {skill.when_to_use}{_RESET}")
        self._write(f"  {_DIM}正文 {len(skill.content)} 字符，/skill refresh 可重新加载{_RESET}")

    def _cmd_refresh(self, ctx=None) -> None:
        default_registry().invalidate()
        self._write(f"  {_GREEN}+ 技能目录缓存已刷新{_RESET}")
        if self._rebuild_prompt(ctx):
            self._write(f"  {_DIM}  系统提示词技能章节已重建{_RESET}")

    @staticmethod
    def _rebuild_prompt(ctx) -> bool:
        """技能变更后重建系统提示词（技能章节随之更新）。"""
        session = getattr(ctx, "session", None)
        agent = getattr(session, "agent", None)
        rebuild = getattr(agent, "rebuild_system_prompt", None)
        if rebuild is None:
            return False
        try:
            rebuild()
            return True
        except Exception:
            _logger.exception("技能变更后重建系统提示词失败")
            return False

    async def _cmd_install(self, spec: str, *, update: bool = False, ctx=None) -> None:
        if not spec:
            self._write(f"  {_YELLOW}用法: /skill {'update' if update else 'install'} "
                        f"<owner/repo[@ref]>{_RESET}")
            return
        registry = default_registry()
        installer = GithubSkillInstaller(installed_root=registry.installed_root())
        self._write(f"  {_DIM}{'更新' if update else '安装'}中: {spec} ...{_RESET}")
        try:
            result = await installer.install(
                spec, reuse_ref=update, on_change=registry.invalidate,
            )
        except SkillInstallError as e:
            self._write(f"  {_RED}x {e}{_RESET}")
            return
        except Exception as e:
            _logger.exception("技能安装失败")
            self._write(f"  {_RED}x 安装失败: {e}{_RESET}")
            return
        skills = ", ".join(result["skills"])
        self._write(
            f"  {_GREEN}+ {'更新' if update else '安装'}成功: {result['owner']}/{result['repo']}"
            f"@{result['ref']} ({result['commit'][:12] or '-'}){_RESET}"
        )
        self._write(f"  {_GREEN}  技能: {skills}{_RESET}")
        self._write(f"  {_DIM}  位置: {result['dir']}{_RESET}")
        if self._rebuild_prompt(ctx):
            self._write(f"  {_DIM}  系统提示词技能章节已重建{_RESET}")

    def _cmd_remove(self, target: str, ctx=None) -> None:
        if not target:
            self._write(f"  {_YELLOW}用法: /skill remove <owner/repo|owner__repo|技能名>{_RESET}")
            return
        registry = default_registry()
        installer = GithubSkillInstaller(installed_root=registry.installed_root())
        if installer.uninstall(target, on_change=registry.invalidate):
            self._write(f"  {_GREEN}+ 已卸载: {target}{_RESET}")
            if self._rebuild_prompt(ctx):
                self._write(f"  {_DIM}  系统提示词技能章节已重建{_RESET}")
        else:
            self._write(f"  {_YELLOW}未找到已安装的: {target}{_RESET}")

    # ── 命令入口 ─────────────────────────────────────────

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行（TUI 路径）。"""
        arg = (ctx.arg or "").strip()
        if not arg or arg == "list":
            self._cmd_list()
            return True
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "list":
            self._cmd_list(only_installed=(rest == "installed"))
        elif sub == "install":
            await self._cmd_install(rest)
        elif sub == "update":
            await self._cmd_install(rest, update=True)
        elif sub == "remove":
            self._cmd_remove(rest)
        elif sub == "info":
            if rest:
                self._cmd_info(rest)
            else:
                self._write(f"  {_YELLOW}用法: /skill info <技能名>{_RESET}")
        elif sub == "refresh":
            self._cmd_refresh()
        elif sub in ("help", "-h", "--help"):
            self._write(self.help_text())
        else:
            self._write(f"  {_YELLOW}未知子命令: {sub}，输入 /skill help 查看用法{_RESET}")
        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本（Web/旧命令系统路径）。"""
        arg = (ctx.arg or "").strip()
        if not arg or arg == "list":
            self._cmd_list()
            return True
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "install" or sub == "update":
            # 在线下载会阻塞事件循环，Web 端提示到终端执行
            self._write(
                f"  {_YELLOW}Web 端不支持在线安装/更新，请在终端执行 "
                f"/skill {sub} {rest}{_RESET}"
            )
        elif sub == "list":
            self._cmd_list(only_installed=(rest == "installed"))
        elif sub == "remove":
            self._cmd_remove(rest)
        elif sub == "info":
            if rest:
                self._cmd_info(rest)
        elif sub == "refresh":
            self._cmd_refresh()
        elif sub in ("help", "-h", "--help"):
            self._write(self.help_text())
        else:
            self._write(f"  {_YELLOW}未知子命令: {sub}{_RESET}")
        return True


# 模块级自注册（与 ModelPlugin 等一致）
get_plugin_registry().register(SkillPlugin())


__all__ = ["SkillPlugin"]
