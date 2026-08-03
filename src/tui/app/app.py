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

from src.tui.core.style import Style
from src.tui.ink import h, APP, BOX, TEXT, StyledRun
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
        # ★ 传 width 给 ChatView：内部截断宽度与布局宽度同源（props width），
        #   修复 model.width 与布局宽度不一致时 subagent/工具卡行被 wrap 拆成
        #   两行（第二行只剩边框字符）的显示错乱。
        h(ChatView, {"model": model, "width": width}),
        h(_ParseLine, {"model": model}),
        h(_StreamingLine, {"model": model}),
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
    """实时解析进度行（同位置刷新；model.parse_line 为 None 时不占行）。

    方向3（动效）：解析进度行前缀/内容呼吸色（时间基）——解析活跃时进度行
    从暗灰 242 呼吸到 252 亮灰，视觉提示「正在解析」（空闲静态保持原色）。

    方向4（动效）：前缀 ``~`` 替换为时间基 spinner（⠋⠙⠹… 10Hz 推进）——
    解析进行中更生动（与 subagent 卡片 spinner 共用语义）。
    """
    model = props["model"]
    line = model.parse_line
    if line is None:
        return h(BOX, None, [])
    # ★ 呼吸色：仅当解析行存在时计算（每帧一次 time_glow，0.1s 桶缓存命中）
    from src.tui.app._theme import time_glow
    from src.tui.app import _fx
    glow = time_glow(242, 252, 8.0)
    # 时间基 spinner（解析进度行常驻 live，10Hz 渲染时平滑推进）
    # ★ 方向4：帧序列唯一真源 _fx.SPINNER_FRAMES（原内联字符串收敛）
    sp = _fx.spinner_char()
    runs = []
    first_text = True
    for r in line.runs:
        if not r.text:
            continue
        st = r.style
        # 解析行基础样式为 Style(fg=242)（apply.py _S_PARSE）——运行时替换为
        # 呼吸色（保留其他属性）；非 242 样式（防御：未来改样式）原样保留。
        if st is not None and getattr(st, "fg", None) == 242:
            st = Style(fg=glow)
        text = r.text
        # 首个文本 run 中的 `~` 前缀替换为 spinner（apply 结构：
        # ``f"  ~ {tool_names}..."``——`~` 出现在首个 run 行首前缀位）
        # ★ BUG-40（review 方向）：仅替换**行首固定前缀位**（前导空格后的
        #   第一个 `~`）——修复前 ``text.replace("~", sp, 1)`` 替换首个 run 内
        #   **第一个** `~`，工具名/参数含 `~`（如 ``~/proj``）时替换错误字符。
        if first_text:
            stripped = text.lstrip(" ")
            lead = len(text) - len(stripped)
            if stripped.startswith("~"):
                text = text[:lead] + sp + stripped[1:]
                first_text = False
        runs.append(StyledRun(text, st))
    return h(BOX, None, [h(TEXT, {"styled": runs})])


def _StreamingLine(props) -> object:
    """生成中指示行（BEAUTY-9：内容流式期间动画块 + 呼吸色）。

    条件：``model.status.status_active`` 且内容通道开放
    （``content_block_index >= 0`` 且 ``not content_closed``）——避免推理阶段
    （status_active=True 但 content 未开）误显示；空闲/非内容阶段零高度
    （不占行，与 _ParseLine 同模式）。

    视觉：时间基 spinner（⠋⠙⠹… 10Hz）+ 青色呼吸「生成中」——对齐 Claude
    Code 生成中反馈；仅内容流式期间常驻 live 区，10Hz 渲染时平滑推进。
    """
    model = props["model"]
    if not model.status.status_active:
        return h(BOX, None, [])
    if model.content_block_index < 0 or model.content_closed:
        return h(BOX, None, [])
    from src.tui.app._theme import time_glow
    from src.tui.app import _fx
    c = time_glow(36, 49, 5.0)
    # ★ 方向4：spinner 帧序列唯一真源 _fx.SPINNER_FRAMES（原内联字符串收敛）
    sp = _fx.spinner_char()
    return h(BOX, None, [
        h(TEXT, {"styled": [StyledRun(f"{sp} 生成中", Style(fg=c))]}),
    ])


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
