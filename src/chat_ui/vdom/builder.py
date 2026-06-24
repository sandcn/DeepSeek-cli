"""_vnode_builder — TuiState → VNode 树构建器。

纯函数：输入 TuiState，输出 VNode 树。
由 TuiEngine._phase_render 在 VNode 路径中调用。
"""

from __future__ import annotations

from ..vdom.vnode import VNode
from ..state.store import TuiState


def build_vnode_tree(state: TuiState) -> VNode:
    """从 TuiState 构建 VNode 树。

    树结构：
        root (key="root")
        ├── content_area (key="content_area")
        │   ├── thinking_block (key="thinking") — 推理文本
        │   ├── answer_block (key="answer") — 回答文本
        │   ├── user_messages (key="user_msgs") — 用户消息列表
        │   ├── tool_outputs (key="tool_outputs") — 工具输出
        │   ├── notifications (key="notifications") — 通知
        │   ├── errors (key="errors") — 错误
        │   └── write_lines (key="write_lines") — 单行输出
        └── bottom_bar (key="bottom_bar")
            ├── status_line (key="status") — 状态行
            ├── input_bar (key="input") — 输入栏（React Ink 风格）
            └── completion_popup (key="completion") — 补全弹窗

    每个 VNode 使用稳定的 key 以便 diff 算法正确匹配。
    """
    children: list[VNode] = []

    # ── 内容区 ──
    content_children: list[VNode] = []

    # 用户消息
    if state.user_messages:
        content_children.append(VNode(
            type="user_messages",
            key="user_msgs",
            props={"messages": tuple(state.user_messages)},
        ))

    # 推理文本
    if state.reasoning_text:
        content_children.append(VNode(
            type="thinking_block",
            key="thinking",
            props={"text": state.reasoning_text, "phase": state.phase},
        ))

    # 回答文本
    if state.content_text:
        content_children.append(VNode(
            type="answer_block",
            key="answer",
            props={"text": state.content_text, "phase": state.phase},
        ))

    # 工具输出
    if state.tool_outputs:
        content_children.append(VNode(
            type="tool_outputs",
            key="tool_outputs",
            props={"outputs": tuple(state.tool_outputs)},
        ))

    # 通知
    if state.notifications:
        content_children.append(VNode(
            type="notifications",
            key="notifications",
            props={"items": tuple(state.notifications)},
        ))

    # 错误
    if state.errors:
        content_children.append(VNode(
            type="errors",
            key="errors",
            props={"items": tuple(state.errors)},
        ))

    # 单行输出
    if state.write_lines:
        content_children.append(VNode(
            type="write_lines",
            key="write_lines",
            props={"lines": tuple(state.write_lines)},
        ))

    # SubAgent 帧
    if state.subagent_frames:
        content_children.append(VNode(
            type="subagent_frames",
            key="subagent_frames",
            props={"frames": state.subagent_frames},
        ))

    if content_children:
        children.append(VNode(
            type="content_area",
            key="content_area",
            children=content_children,
        ))

    # ── 底部栏 ──
    bottom_children: list[VNode] = []

    # 状态行
    status = state.status
    status_text = status.render() if hasattr(status, 'render') else ""
    bottom_children.append(VNode(
        type="status_line",
        key="status",
        props={
            "text": status_text,
            "model": status.model,
            "tokens": status.tokens,
            "elapsed": status.elapsed,
            "tool_count": status.tool_count,
            "tool_fail": status.tool_fail,
            "streaming": status.streaming,
        },
    ))

    # 输入栏（React Ink 风格 — 通过 InputBarComponent 产出 VNode）
    from ..components.base import InputBarComponent
    input_bar = InputBarComponent(
        text=state.input_line.text,
        cursor_pos=state.input_line.cursor_pos,
    )
    bottom_children.append(input_bar.render_vnode())

    # 补全弹窗
    completion = state.completion
    if completion.visible:
        bottom_children.append(VNode(
            type="completion_popup",
            key="completion",
            props={
                "items": tuple(completion.items),
                "selected": completion.selected,
                "visible": True,
            },
        ))

    if bottom_children:
        children.append(VNode(
            type="bottom_bar",
            key="bottom_bar",
            children=bottom_children,
        ))

    return VNode(
        type="root",
        key="root",
        children=children,
        props={"version": state.version},
    )
