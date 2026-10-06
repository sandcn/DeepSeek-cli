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

__all__ = ["is_modal_close_key", "empty_modal_frame", "use_modal_scope"]


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


def empty_modal_frame() -> object:
    """不可见模态视图的零高度占位元素（统一 ``h(TEXT, {"children": ""})``）。

    ★ 组件函数必须返回元素（不能返回 None）——不可见时返回空 TEXT：零高度、
    不占行，且保持 fiber 树结构稳定（避免 BOX↔TEXT 类型切换导致 fiber
    销毁重建）。
    """
    return h(TEXT, {"children": ""})


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
