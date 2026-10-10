"""模态视图通用骨架（P2）。

全屏模态（``use_fullscreen``——App 整屏渲染对应视图）与底部模态
（``use_modal``——App 底部区只渲染对应视图）共享同一套骨架契约：

  1. **模态声明**：visible 期间独占键盘输入（未消费按键被 input router
     吞掉，不落入输入缓冲——杜绝「看不见的输入」）；
  2. **关闭键判定**：``Esc`` / ``Ctrl+H``（``\\x08``）为统一关闭键
     （config_view / plugin_view 关闭视图；trace_tools_view 返回上级轨迹）；
  3. **不可见占位**：不可见帧渲染零高度空 ``TEXT``（不占行、保持
     fiber 树结构稳定）。

此前各视图（config_view / plugin_view / trace_view / trace_tools_view /
user_select / editmsg_select）各自复刻上述逻辑。本模块收敛为可复用辅助，
新增模态视图只需 ``use_modal_scope`` + ``is_modal_close_key`` +
``empty_modal_frame``，无需重复三处样板。

依赖约束：仅依赖 ink 框架（hooks / TEXT / h），不依赖 tools 层。
"""

from __future__ import annotations

from src.tui.ink import TEXT, h
from src.tui.ink.hooks import use_fullscreen, use_modal

__all__ = [
    "is_modal_close_key", "is_fullscreen_close_key", "empty_modal_frame",
    "use_modal_scope", "close_fullscreen_view",
]


def is_modal_close_key(event) -> bool:
    """模态视图统一关闭键判定：``Esc`` 或 ``Ctrl+H``（``\\x08``）。

    Args:
        event: 输入事件（读 ``kind`` / ``char``）。

    Returns:
        True — 关闭键（调用方按自身语义处理：关闭视图 / 返回上级）。
    """
    kind = getattr(event, "kind", "")
    if kind == "escape":
        return True
    return kind == "ctrl_key" and getattr(event, "char", "") == "\x08"


def is_fullscreen_close_key(event, extra_kinds=()) -> bool:
    """全屏视图关闭键：``Esc`` / ``Ctrl+H`` + 额外功能键（如 ``f11`` / ``f12``）。

    ``extra_kinds`` 用于「**专属开关键同时充当关闭键**」的视图（文件变更审查器
    的 F11、会话日志的 F12）。这类键**必须由视图消费**（``use_input`` 返回
    True）——否则会被 ``use_fullscreen`` 模态吞掉：既不触发视图关闭逻辑，也
    落不到快捷键回调（表现为「按一下 F11 关不掉」）。

    Args:
        event: 输入事件（读 ``kind``）。
        extra_kinds: 额外关闭键的 ``kind``（如 ``("f11",)``）。

    Returns:
        True — 关闭键。
    """
    if is_modal_close_key(event):
        return True
    kind = getattr(event, "kind", "")
    return bool(kind) and kind in tuple(extra_kinds or ())


def empty_modal_frame() -> object:
    """不可见模态视图的零高度占位元素（统一 ``h(TEXT, {"children": ""})``）。

    ★ 组件函数必须返回元素（不能返回 None）——不可见时返回空 TEXT：零高度、
    不占行，且保持 fiber 树结构稳定（避免 BOX↔TEXT 类型切换导致 fiber
    销毁重建）。
    """
    return h(TEXT, {"children": ""})


def close_fullscreen_view(model, state, view_id: str) -> None:
    """关闭模态全屏视图：写终态 + 清 ``model.fullscreen``（若仍指向本视图）。

    ★ 关键（2026-10-10 修复）：全屏视图既可由命令路径打开（``/changes`` 等
    ——命令线程 ``open_fullscreen_view`` 轮询到 ``done`` 后清理 ``fullscreen``），
    也可由**快捷键直开**（F11 ——不经命令队列，没有命令线程清理）。若视图
    组件只置终态、不清 ``fullscreen``，直开路径下 App 会继续按 ``fullscreen``
    整屏渲染该组件（组件已 ``done`` → 渲染空占位）——**界面卡在全屏空视图，
    无法返回聊天界面**（Esc 关闭看似无效）。

    Args:
        model: AppModel（读写 ``fullscreen``）。
        state: 视图状态（须有 ``try_set_final``）；None 时只清 ``fullscreen``。
        view_id: 本视图 id（仅当 ``fullscreen`` 仍指向它时清空——用户可能
            已切到其它视图）。
    """
    if state is not None:
        try:
            state.try_set_final("cancel")
        except Exception:
            pass
    if getattr(model, "fullscreen", "") == view_id:
        model.fullscreen = ""


def use_modal_scope(visible: bool, *, fullscreen: bool = True) -> None:
    """模态视图声明（统一入口）。

    Args:
        visible: 视图是否可见（激活）。
        fullscreen: True → ``use_fullscreen``（App 整屏渲染的全屏视图）；
            False → ``use_modal``（App 底部区只渲染的底部视图）。

    ★ Rules of Hooks：本函数无条件调用，内部 hook 亦无条件调用（visible
    仅作为 hook 参数传入，不做条件分支）。
    """
    if fullscreen:
        use_fullscreen(visible)
    else:
        use_modal(visible)
