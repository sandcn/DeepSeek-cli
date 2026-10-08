"""skill 视图辅助 — ``/skill`` 打开全屏技能浏览器（SkillView）。

``/skill``（无参数 / ``list``）在有活跃 ChatUI 时打开全屏技能浏览器
（``model.fullscreen == "skill"``）；视图内操作（install / update / remove /
refresh）经 ``applied_seq`` / ``refresh_seq`` 回传给命令线程执行。

install / update 为异步下载（``GithubSkillInstaller.install``）——在命令线程
（``asyncio.to_thread`` 的工作线程，无事件循环）用 ``asyncio.run`` 驱动，
不阻塞 TUI 事件循环。
"""

from __future__ import annotations

import asyncio
import logging

_logger = logging.getLogger(__name__)


def _build_skill_entries() -> list:
    """技能注册表 → 浏览器条目列表。"""
    try:
        from ...skills import default_registry
    except Exception:
        return []
    try:
        registry = default_registry()
        summaries = registry.list()
    except Exception:
        _logger.debug("读取技能列表失败", exc_info=True)
        return []
    out: list = []
    for s in summaries:
        invocation = getattr(s, "invocation", None)
        out.append({
            "name": str(getattr(s, "name", "")),
            "description": str(getattr(s, "description", "") or ""),
            "source": str(getattr(s, "source", "") or ""),
            "provider": str(getattr(s, "provider", "") or ""),
            "path": str(getattr(s, "path", "") or ""),
            "when_to_use": str(getattr(s, "when_to_use", "") or ""),
            "model_invocable": bool(getattr(invocation, "model_invocable", True)),
            "user_invocable": bool(getattr(invocation, "user_invocable", True)),
        })
    return out


def _do_skill_op(report: dict) -> str:
    """执行一次技能操作（install/update/remove），返回结果提示文本。"""
    action = str(report.get("action", "") or "")
    if not action:
        return ""
    try:
        from ...skills import GithubSkillInstaller, default_registry
    except Exception as exc:
        return f"技能模块不可用：{exc}"
    registry = default_registry()
    installer = GithubSkillInstaller(installed_root=registry.installed_root())
    if action in ("install", "update"):
        spec = str(report.get("spec") or report.get("target") or "").strip()
        if not spec:
            return "未提供目标"
        try:
            result = asyncio.run(installer.install(
                spec, reuse_ref=(action == "update"), on_change=registry.invalidate,
            ))
        except Exception as exc:
            return f"{'更新' if action == 'update' else '安装'}失败：{exc}"
        label = "已更新" if action == "update" else "已安装"
        return f"{label}: {result.get('owner', '')}/{result.get('repo', '')}"
    if action == "remove":
        target = str(report.get("target", "") or "").strip()
        if not target:
            return "未选择目标"
        try:
            ok = installer.uninstall(target, on_change=registry.invalidate)
        except Exception as exc:
            return f"卸载失败：{exc}"
        return "已卸载" if ok else "未找到可卸载的目标"
    return ""


def _open_skill_ui(ctx) -> bool:
    """打开全屏技能浏览器视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_skill_view_state_cls
    from ._view_opener import open_fullscreen_view

    refresh_seq = {"v": 0}
    applied_seq = {"v": 0}

    def setup(model, state):
        state.entries = _build_skill_entries()

    def tick(state) -> bool:
        if state.refresh_seq > refresh_seq["v"]:
            refresh_seq["v"] = state.refresh_seq
            try:
                from ...skills import default_registry

                default_registry().invalidate()
            except Exception:
                pass
            state.entries = _build_skill_entries()
            state.status_message = "已刷新"
            return False
        if state.applied_seq > applied_seq["v"]:
            applied_seq["v"] = state.applied_seq
            state.busy = True
            try:
                msg = _do_skill_op(state.applied or {})
            finally:
                state.busy = False
            state.status_message = msg
            state.entries = _build_skill_entries()
            state.delete_confirm = ""
        return False

    return open_fullscreen_view(
        ctx, view_id="skill", state_attr="skill_view",
        state_cls=get_skill_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="技能浏览器已关闭", timeout_hint="技能浏览器超时关闭",
    )


def open_skill_ui_safe(ctx) -> bool:
    """安全打开技能浏览器（异常回退 False，供 skill 插件 async 路径调用）。"""
    try:
        return _open_skill_ui(ctx)
    except Exception:
        _logger.debug("打开技能浏览器失败（回退文本）", exc_info=True)
        return False


__all__ = ["open_skill_ui_safe", "_build_skill_entries", "_open_skill_ui"]
