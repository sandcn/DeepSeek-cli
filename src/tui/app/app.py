"""App — 根组件：flexbox column 布局消息区 + 底部区。

组件树结构（非全屏流动模型）：
  - 消息区（flexGrow=1）：顶部标题栏 + Static 聊天历史 + 实时解析行——
    「占据剩余高度」意图（非全屏流动模型高度由内容驱动，无固定视口时
    grow 为空操作；未来引入高度约束即生效）。
  - 底部区：状态栏 + 输入区（固定内容高度）。
  - Static 包裹聊天历史（静态内容，首差异行之前永不重写）

Claude Code 视觉对齐：顶部标题栏（TopHeader）为文档首行，其后 committed
聊天历史（committed-chat）落到 y>=1 走非顶部前缀路径（render_frame 正确
处理）。工具运行状态由工具卡片顶边框（● 图标）展示（原 ToolStatusHeader
移除）；子代理活动卡片并入 ChatView 消息流（原独立 SubAgentPanel 移除）。
"""

from __future__ import annotations

from src.tui.ink import h, APP, STATIC, BOX, TEXT, StyledRun
from .chat_view import ChatView, register as _register_committed
from .header import TopHeader
from .status_bar import StatusBar
from . import input_area as _input_area


def App(props) -> object:
    """App 根组件。

    Props:
        model: AppModel 实例。
        width: 终端宽度。
    """
    model = props["model"]
    width = props.get("width", 80)

    input_props = {
        "text": model.input_text,
        "cursor_pos": model.input_cursor,
        "prompt": "> ",
        "completion": model.completion,
        "status_active": model.status.status_active,
        "cpu": model.status.cpu,
        "mem": model.status.mem,
        # 方向D 步骤14：Ctrl+R 反向历史搜索状态（input-area 渲染覆盖行）
        "history_search": model.history_search,
    }

    # ★ 方向1（Flexbox 布局消息区 + 底部区）：根容器 flexbox column——消息区
    #   （flexGrow=1，聊天历史/实时解析行/工具状态头/subagent 面板）与底部区
    #   （状态栏 + 输入区）分组。非全屏流动模型下 grow 为空操作（高度内容
    #   驱动），结构清晰、语义明确；未来引入高度约束（视口 pin）即生效。
    message_area = [
        # Claude Code 视觉对齐：顶部渐变标题栏（文档首行；committed-chat 因
        # 此落到 y>=1，走非顶部前缀路径——render_frame 已正确回退全量）
        h(TopHeader, {"model": model, "width": width}),
        # 子代理活动卡片已并入 ChatView 消息流（原独立 SubAgentPanel 组件移除）
        h(STATIC, None, [h(ChatView, {"model": model})]),
        h(_ParseLine, {"model": model}),
    ]
    bottom_area = [
        h(StatusBar, {"model": model, "width": width}),
        h("input-area", input_props),
    ]
    return h(APP, {"width": width, "flexDirection": "column"}, [
        h(BOX, {"flexGrow": 1}, message_area),
        h(BOX, None, bottom_area),
    ])


def _ParseLine(props) -> object:
    """实时解析进度行（同位置刷新；model.parse_line 为 None 时不占行）。"""
    model = props["model"]
    line = model.parse_line
    if line is None:
        return h(BOX, None, [])
    runs = [StyledRun(r.text, r.style) for r in line.runs if r.text]
    return h(BOX, None, [h(TEXT, {"styled": runs})])


def build_app_element(model, width: int, animator=None) -> object:
    """构建根元素（session 渲染入口）。

    animator: 保留参数（兼容旧调用面），App 组件已不使用动画上下文。
    # deprecated: animator 参数已废弃，仅兼容旧调用面
    """
    return h(App, {"model": model, "width": width})


# 模块导入时注册 input-area / committed-chat host 组件
_input_area.register()
_register_committed()

__all__ = ["App", "build_app_element"]
