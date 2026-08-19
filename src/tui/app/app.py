"""App — 根组件：flexbox column 布局消息区 + 底部区。

组件树结构（非全屏流动模型）：
  - 消息区（flexGrow=1）：顶部标题栏 + Static 聊天历史 + 实时解析行——
    「占据剩余高度」意图（非全屏流动模型高度由内容驱动，无固定视口时
    grow 为空操作；未来引入高度约束即生效）。
  - 底部区：状态栏 + 输入区（固定内容高度）。
  - Static 包裹聊天历史（静态内容，首差异行之前永不重写）

轨迹视图（2026-08-19，Ctrl+H 开关；2026-08-17 迁移到**模态全屏视图通用
机制**）：``model.fullscreen == "trace"`` 时整屏渲染 ``TraceView``（DSH
风格左台账 + 右检查器）——不再显示聊天消息区，显示 DSH 轨迹的全部内容
（轮次/记录/详情/耗时/token）；底部区不渲染（模态独占输入——未消费按键
不落入输入缓冲）。

Claude Code 视觉对齐：顶部标题栏（TopHeader）为文档首行，其后 committed
聊天历史（committed-chat）落到 y>=1 走非顶部前缀路径（render_frame 正确
处理）。工具运行状态由工具卡片顶边框（● 图标）展示（原 ToolStatusHeader
移除）；子代理活动卡片并入 ChatView 消息流（原独立 SubAgentPanel 移除）。
"""

from __future__ import annotations

from src.tui.ink import h, APP, Column
from .chat_view import ChatView, _ParseLine
from .header import TopHeader
from .status_bar import StatusBar
from .user_select import UserSelectPopup
from .editmsg_select import EditMsgSelectPopup
from .config_view import ConfigView
from .input_area import InputArea
from .trace_view import TraceView
from .trace_tools_view import TraceToolsView

#: 模态全屏视图注册表（2026-08-17 通用机制）：view_id → 组件函数。
#: App 在 ``model.fullscreen`` 非空时按 id 查注册表**整屏渲染**对应组件；
#: 组件内部须 ``use_fullscreen(True)`` 声明模态（独占键盘输入——未消费按键
#: 不落入输入缓冲，杜绝看不见的输入）与 ``use_input`` 处理关闭/导航键。
#: 新增全屏视图两步：注册表加条目 + 设置 ``model.fullscreen``——整屏渲染 /
#: 输入接管 / 光标隐藏（全屏无输入区自动隐藏）全部自动生效，无需改 App 分支。
FULLSCREEN_VIEWS: dict = {
    "trace": TraceView,
    # ★ 2026-08-17（用户需求：轨迹 Trace 工具列表 Enter 进入新界面）：工具
    #   列表详情视图——主轨迹选中 #0 工具列表记录按 Enter → ``model.fullscreen
    #   = "trace_tools"`` → 整屏渲染 TraceToolsView（左右布局：左工具名列表
    #   上下选择 + 右树控件显示需要的参数）；Esc/Ctrl+H 返回主轨迹
    #   （``model.fullscreen = "trace"``）。
    "trace_tools": TraceToolsView,
    # ★ 2026-08-20（用户需求：config 命令独立界面）：配置中心视图——
    #   /config 命令 → ``model.fullscreen = "config"`` → 整屏渲染
    #   ConfigView（配置列表浏览 + Enter 编辑 + Esc/Ctrl+H 关闭）；
    #   关闭（命令线程清理 fullscreen 置空）后恢复完整聊天界面。
    "config": ConfigView,
}

#: 模态底部视图注册表（2026-08-17 通用机制）：view_id → 组件 或
#: ``(组件, key_fn)`` 元组。
#: App 在 ``model.bottom_view`` 非空时按 id 查注册表**只渲染对应组件**作为
#: 底部区（状态栏/输入区不显示——「弹窗打开时底部框不显示，弹窗在原来
#: 底部框位置独立显示」）；组件内部须 ``use_modal(True)`` 声明模态（独占
#: 键盘输入——输入区已不渲染，未消费按键不落入输入缓冲，杜绝看不见的
#: 输入）与 ``use_input`` 处理导航/确认/取消键。
#: key 约定：无内部 use_state 的简单组件用固定 key ``bv-{view_id}``（fiber
#: 复用保持组件状态）；带内部状态且须每次打开重置的组件用 ``(组件, key_fn)``
#: 元组（key_fn 接收 model 返回 key 字符串——如 UserSelectPopup 用
#: ``model.user_select.seq`` 递增序号强制重挂载，重置内部选中/勾选 state）。
#: 新增底部视图两步：注册表加条目 + 设置 ``model.bottom_view``——底部区
#: 渲染 / 输入接管 / 光标隐藏（输入区不渲染自动隐藏）全部自动生效，无需
#: 改 App 分支。
BOTTOM_VIEWS: dict = {
    "user_select": (
        UserSelectPopup,
        # ★ 2026-08-19（并发 tab 弹窗）：key 固定为常量——弹窗激活期间
        #   **不因新问题加入/问题确认而重挂载**（active_tab 焦点保留，否则
        #   Tab 切到的问题会被新 append 强制重置回第一个）。连续会话（关闭
        #   帧被渲染节流合并跳过 → fiber 复用）由组件内部会话检测防御
        #   （us_ref/prev_states_ref 实例变化时重置焦点与选中）。
        lambda model: "us-popup",
    ),
    # ★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
    #   /editmsg 消息选择独立为底部视图——独立状态 model.editmsg_select +
    #   独立组件 EditMsgSelectPopup（每条消息只显示一行）。key 用
    #   editmsg_select.seq 递增序号强制重挂载，重置组件内部 use_state。
    "editmsg": (
        EditMsgSelectPopup,
        lambda model: f"em-{getattr(getattr(model, 'editmsg_select', None), 'seq', 0)}",
    ),
}


def App(props) -> object:
    """App 根组件。

    Props:
        model: AppModel 实例。
        width: 终端宽度。
    """
    model = props["model"]
    width = props.get("width", 80)

    # ★ 模态全屏视图（2026-08-17 通用机制）：model.fullscreen 非空 → 按
    #   注册表**整屏只显示对应视图**（其他 TUI 组件不渲染、不接收键盘输入
    #   ——模态独占）。组件经 use_fullscreen(True) 声明模态（未消费按键不落入
    #   输入缓冲）；Esc/Ctrl+H 等关闭键由组件自身 use_input 处理。关闭后恢复
    #   完整组件树（顶部标题栏 + 聊天 + 状态栏 + 输入区）。根元素类型切换
    #   （APP ↔ 全屏视图）由调和器卸载/重建整树（10Hz 渲染循环自动）。
    #   未知视图 id 防御回退正常界面（防注册表删除后残留状态崩溃）；此时
    #   model.fullscreen 残留非空为设计（toggle 回调可覆盖；App 渲染分支/
    #   输入接管/光标隐藏均按实际渲染结果判断，无误判路径）。
    fullscreen_id = getattr(model, "fullscreen", "") or ""
    if fullscreen_id:
        view = FULLSCREEN_VIEWS.get(fullscreen_id)
        if view is not None:
            return h(view, {"model": model, "width": width})

    # ★ 方向1（Flexbox 布局消息区 + 底部区）：根容器 flexbox column——消息区
    #   （flexGrow=1，聊天历史/实时解析行/工具状态头/subagent 面板）与底部区
    #   （状态栏 + 输入区）分组。非全屏流动模型下 grow 为空操作（高度内容
    #   驱动），结构清晰、语义明确；未来引入高度约束（视口 pin）即生效。
    # ★ 阶段2（标准布局容器重构）：消息区/底部区 BOX → Column（语义化门面，
    #   Column 即 BOX + flexDirection=column，输出与重构前等价）。
    message_area = [
        # Claude Code 视觉对齐：顶部渐变标题栏（文档首行；committed-chat 因
        # 此落到 y>=1，走非顶部前缀路径——render_frame 已正确回退全量）
        h(TopHeader, {"model": model, "width": width}),
        # 子代理活动卡片已并入 ChatView 消息流（原独立 SubAgentPanel 组件移除）
        # ★ 传 width 给 ChatView：内部截断宽度与布局宽度同源（props width），
        #   修复 model.width 与布局宽度不一致时 subagent/工具卡行被 wrap 拆成
        #   两行（第二行只剩边框字符）的显示错乱。
        h(ChatView, {"model": model, "width": width}),
    ]
    # ★ 2026-08-20（用户需求：subagent 界面打开时接收参数进度行在 subagent
    #   界面上面）：无 subagent 卡片（``subagent_lines`` 为空）时进度行渲染在
    #   ChatView 之后（原位置）；subagent 界面打开时进度行由 ChatView 在
    #   SubAgentCard **之前**渲染（两处互斥不重复）。
    if not getattr(model, "subagent_lines", None):
        message_area.append(h(_ParseLine, {"model": model, "width": width}))
    # ★ 模态底部视图（2026-08-17 通用机制）：model.bottom_view 非空 → 底部区
    #   **只渲染**注册表中对应的底部视图组件（状态栏/输入区不显示——「弹窗
    #   打开时底部框不显示，弹窗在原来底部框位置独立显示」）。组件经
    #   use_modal(True) 声明模态（未消费按键不落入输入缓冲——输入区已不渲染，
    #   字符落入输入缓冲会「看不见地」改变用户输入）；Esc/Enter 等关闭键由
    #   组件自身 use_input 处理。关闭（工具/协议清理 bottom_view）后恢复
    #   正常底部区（状态栏 + 输入区），调和器按 key 差异自动重建。
    #   未知视图 id 防御回退正常底部区（防注册表删除后残留状态崩溃）；此时
    #   model.bottom_view 残留非空为设计（清理方负责恢复；App 渲染分支/输入
    #   接管/光标隐藏均按实际渲染结果判断，无误判路径）。
    bottom_view_id = getattr(model, "bottom_view", "") or ""
    if bottom_view_id:
        entry = BOTTOM_VIEWS.get(bottom_view_id)
        if entry is not None:
            if isinstance(entry, tuple):
                view, key_fn = entry
                key = key_fn(model)
            else:
                view, key = entry, f"bv-{bottom_view_id}"
            bottom_area = [
                h(view, {"model": model, "width": width, "key": key}),
            ]
        else:
            bottom_area = _normal_bottom_area(model, width)
    else:
        bottom_area = _normal_bottom_area(model, width)
    return h(APP, {"width": width, "flexDirection": "column"}, [
        h(Column, {"flexGrow": 1}, message_area),
        h(Column, None, bottom_area),
    ])


def _normal_bottom_area(model, width: int) -> list:
    """正常底部区：状态栏 + 输入区（底部视图未激活时渲染）。"""
    return [
        h(StatusBar, {"model": model, "width": width}),
        # ★ 标准 React Ink 组件化（2026-08-05）：input-area 自定义 host →
        #   InputArea 标准函数组件（内部 Column + CompletionPopup + TEXT 行）。
        #   组件以 ``dataInputArea`` 标记容器，session._position_cursor 据此
        #   定位输入区（兼容 host "input-area" 别名查找）。
        h(InputArea, {
            "text": model.input_text,
            "cursor_pos": model.input_cursor,
            "prompt": "> ",
            "completion": model.completion,
            "status_active": model.status.status_active,
            "cpu": model.status.cpu,
            "mem": model.status.mem,
            # ★ 后台任务计数（2026-08-19 用户需求：模式行行首显示）——
            #   bash 与 subagent 分列传入；任务注册/完成/移除时经
            #   BgBashCountCmd 更新 model.status，props 变化 → InputArea
            #   use_memo deps 变化 → 模式行行首即时刷新。
            "bg_bash_count": model.status.bg_bash_count,
            "bg_subagent_count": model.status.bg_subagent_count,
            # 方向D 步骤14：Ctrl+R 反向历史搜索状态（input-area 渲染覆盖行）
            "history_search": model.history_search,
            # ★ 标准 React Ink 组件化（2026-08-05）：InputArea 标准组件接收
            #   width（布局宽度同源，修复 model.width 与布局宽度不一致）。
            "width": width,
        }),
    ]


def build_app_element(model, width: int) -> object:
    """构建根元素（session 渲染入口）。"""
    return h(App, {"model": model, "width": width})


__all__ = ["App", "build_app_element"]
