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

用户选择视图（2026-08-17 **模态底部视图通用机制**）：``model.bottom_view``
非空时按 ``BOTTOM_VIEWS`` 注册表**独占渲染底部区**（状态栏 + 输入区隐藏——
「底部框不显示」），视图组件渲染在**原底部框位置**（屏幕底部区域）；消息区
保持正常显示。user_select 弹窗从底部框内嵌组件（原与状态栏/输入区同层渲染）
独立为该机制的第一个底部视图——显示时底部框隐藏、独占键盘（组件经
``use_fullscreen(True)`` 声明模态，未消费按键不落入输入缓冲）。

Claude Code 视觉对齐：顶部标题栏（TopHeader）为文档首行，其后 committed
聊天历史（committed-chat）落到 y>=1 走非顶部前缀路径（render_frame 正确
处理）。工具运行状态由工具卡片顶边框（● 图标）展示（原 ToolStatusHeader
移除）；子代理活动卡片并入 ChatView 消息流（原独立 SubAgentPanel 移除）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h, APP, TEXT, StyledRun, Column
# ★ P3-7：time_glow/_fx 模块顶部集中导入（_theme/_fx 仅依赖 core 层，无
#   app 依赖，模块级导入无循环风险；input_area 已同模式导入）——修复前
#   _ParseLine 每帧在函数体内重复惰性导入。
from src.tui.app import _fx
from src.tui.app._theme import time_glow
from .chat_view import ChatView
from .header import TopHeader
from .status_bar import StatusBar
from .user_select import UserSelectPopup
from .input_area import InputArea
from .trace_view import TraceView

#: 模态全屏视图注册表（2026-08-17 通用机制）：view_id → 组件函数。
#: App 在 ``model.fullscreen`` 非空时按 id 查注册表**整屏渲染**对应组件；
#: 组件内部须 ``use_fullscreen(True)`` 声明模态（独占键盘输入——未消费按键
#: 不落入输入缓冲，杜绝看不见的输入）与 ``use_input`` 处理关闭/导航键。
#: 新增全屏视图两步：注册表加条目 + 设置 ``model.fullscreen``——整屏渲染 /
#: 输入接管 / 光标隐藏（全屏无输入区自动隐藏）全部自动生效，无需改 App 分支。
FULLSCREEN_VIEWS: dict = {
    "trace": TraceView,
}

#: 模态底部视图注册表（2026-08-17 通用机制，与 FULLSCREEN_VIEWS 对称）：
#: view_id → (组件函数, key 生成函数 | None)。App 在 ``model.bottom_view``
#: 非空时按 id 查注册表**独占渲染底部区**（状态栏 + 输入区隐藏——底部框
#: 不显示），视图渲染在**原底部框位置**（屏幕底部区域）；消息区保持正常
#: 显示。组件内部须 ``use_fullscreen(True)`` 声明模态（未消费按键不落入
#: 输入缓冲——底部框隐藏时不可见输入不污染缓冲）与 ``use_input`` 处理
#: 交互（导航/确认/取消，可委托标准控件 SelectInput/MultiSelect 等）。
#: 新增底部视图两步：注册表加条目 + 设置 ``model.bottom_view``——独占渲染 /
#: 输入接管 / 光标隐藏（InputArea 不在树中自动隐藏）全部自动生效，无需改
#: App 分支。
#: key 生成函数（可选）：返回组件 props 的 ``key``（强制重挂载语义，如
#: user_select 用 ``us-{seq}`` 每次打开重置组件内部 state）；None 时不传
#: key（组件无重挂载需求）。key_fn 返回空串/None/抛异常时同样不传 key
#: （App 渲染分支防御）。
BOTTOM_VIEWS: dict = {
    "user_select": (
        UserSelectPopup,
        lambda model: f"us-{getattr(getattr(model, 'user_select', None), 'seq', 0)}",
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
        # ★ 标准 React Ink 组件化（2026-08-05）：InputArea 标准组件接收
        #   width（布局宽度同源，修复 model.width 与布局宽度不一致）。
        "width": width,
    }

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
        h(_ParseLine, {"model": model, "width": width}),
    ]

    # ★ 模态底部视图（2026-08-17 通用机制，与 FULLSCREEN_VIEWS 对称）：
    #   model.bottom_view 非空 → 按 BOTTOM_VIEWS 注册表**独占渲染底部区**
    #   （状态栏 + 输入区不显示——「底部框不显示」），视图渲染在**原底部框
    #   位置**（屏幕底部区域）；消息区保持正常显示。组件经 use_fullscreen(True)
    #   声明模态（未消费按键不落入输入缓冲——底部框隐藏时不可见输入不污染
    #   缓冲）。关闭（视图自身 use_input 处理 / 调用方清理 bottom_view）后
    #   恢复完整底部框（状态栏 + 输入区）。底部区元素类型切换（StatusBar/
    #   InputArea ↔ 底部视图）由调和器卸载/重建（10Hz 渲染循环自动）。
    #   未知视图 id 防御回退正常底部框（防注册表删除后残留状态崩溃）。
    #   ★ 兼容回退：旧调用方（测试桩/历史路径）仅设置 ``model.user_select``
    #   （visible=True）而未写 ``model.bottom_view`` 时，按 user_select
    #   底部视图处理——与 UserSelectPopup 内部 visible 判定一致（visible 且
    #   未 done 且有 options），保证旧路径弹窗仍独占底部区显示。options 为空
    #   时回退路径无法渲染弹窗（UserSelectPopup auto-done 兜底不执行）——
    #   此处与组件同语义置 done 回退（见下方分支），防 deadline=0 调用方
    #   轮询 ``us.done`` 永久挂起。
    bottom_view_id = getattr(model, "bottom_view", "") or ""
    if not bottom_view_id:
        _us = getattr(model, "user_select", None)
        if (
            _us is not None
            and getattr(_us, "visible", False)
            and not getattr(_us, "done", True)
        ):
            if getattr(_us, "options", None):
                bottom_view_id = "user_select"
            else:
                # ★ 兼容回退边界（review 方向）：options 空且可见未完成——
                #   UserSelectPopup 的 auto-done 兜底仅在组件渲染时执行；回退
                #   路径下 options 空 → 不渲染弹窗 → 兜底不执行 → 工具协程
                #   （deadline=0 无限等待）轮询 us.done 永久挂起。此处与组件
                #   P2-6 同语义回退（置 done；first-write-wins——done 已置位
                #   则跳过，本分支已排除 done 置位）。
                _us.done = True
                _us.action = "confirmed"
                _us.result = list(getattr(_us, "default_options", None) or [])
    if bottom_view_id:
        entry = BOTTOM_VIEWS.get(bottom_view_id)
        if entry is not None:
            view, key_fn = entry
            view_props = {"model": model, "width": width}
            try:
                key = key_fn(model) if key_fn is not None else None
            except Exception:
                key = None
            if key:
                view_props["key"] = key
            return h(APP, {"width": width, "flexDirection": "column"}, [
                h(Column, {"flexGrow": 1}, message_area),
                h(view, view_props),
            ])

    # 正常底部框：状态栏 + 输入区（user_select 弹窗已独立为模态底部视图——
    # 2026-08-17 用户需求：弹窗显示时底部框隐藏，弹窗界面渲染在原底部框
    # 位置；底部视图注册表 BOTTOM_VIEWS 承载）。
    bottom_area = [
        h(StatusBar, {"model": model, "width": width}),
        # ★ 标准 React Ink 组件化（2026-08-05）：input-area 自定义 host →
        #   InputArea 标准函数组件（内部 Column + CompletionPopup + TEXT 行）。
        #   组件以 ``dataInputArea`` 标记容器，session._position_cursor 据此
        #   定位输入区（兼容 host "input-area" 别名查找）。
        h(InputArea, input_props),
    ]
    return h(APP, {"width": width, "flexDirection": "column"}, [
        h(Column, {"flexGrow": 1}, message_area),
        h(Column, None, bottom_area),
    ])


def _ParseLine(props) -> object:
    """实时解析进度行（同位置刷新；model.parse_line 为 None 时不占行）。

    方向3（动效）：解析进度行前缀/内容呼吸色（时间基）——解析活跃时进度行
    从暗灰 242 呼吸到 252 亮灰，视觉提示「正在解析」（空闲静态保持原色）。

    方向4（动效）：前缀 ``~`` 替换为时间基 spinner（⠋⠙⠹… 10Hz 推进）——
    解析进行中更生动（与 subagent 卡片 spinner 共用语义）。
    """
    model = props["model"]
    width = props.get("width") or 0
    line = model.parse_line
    if line is None:
        # ★ P2（review）：空状态统一返回空 TEXT（h=0 不占行），与活跃状态
        #   TEXT 类型一致——避免 BOX↔TEXT 类型切换导致 fiber 销毁重建。
        return h(TEXT, {"children": ""})
    # ★ 呼吸色：仅当解析行存在时计算（每帧一次 time_glow，0.1s 桶缓存命中；
    #   time_glow/_fx 已模块顶部导入——P3-7）
    glow = time_glow(242, 252, 8.0)
    # ★ BEAUTY-30（体验动效）：spinner 金色呼吸色（178→190 脉动，8s 周期，
    #   与解析行文本呼吸同步周期）——解析进行中 spinner 更醒目（金色提示
    #   「工具解析中」，与状态栏 parsing 阶段标签 178 同色系）。
    sp_glow = time_glow(178, 190, 8.0)
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
                # ★ BEAUTY-30：spinner 独立金色呼吸 run（前导空格保持原样、
                #   spinner 金色、剩余文本呼吸灰）——视觉上 spinner 与文本
                #   区分（原实现整段同呼吸灰）。
                if lead > 0:
                    runs.append(StyledRun(text[:lead], st))
                runs.append(StyledRun(sp, Style(fg=sp_glow)))
                text = stripped[1:]
                first_text = False
        runs.append(StyledRun(text, st))
    # ★ 窄屏防溢出：解析进度行截断至终端宽度（不拆 CJK）——修复前多工具
    #   并行解析行（``  ~ tool1, tool2 ... 123t 12.3s``）在窄终端被自动换行
    #   拆成多行，破坏「同位置刷新」的进度行语义（视觉跳动/错乱）。
    if width and width > 0:
        from src.tui.ink.helpers import truncate_runs
        runs = truncate_runs(runs, width)
    # ★ 阶段2（标准布局容器重构）：单子 BOX 展开为直接 TEXT（父容器 Column
    #   中 fill 语义与 BOX 内 TEXT 等价，输出与重构前一致）。
    return h(TEXT, {"styled": runs})


def build_app_element(model, width: int) -> object:
    """构建根元素（session 渲染入口）。"""
    return h(App, {"model": model, "width": width})


__all__ = ["App", "build_app_element"]
