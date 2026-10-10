"""AppModel 状态类型 — 纯数据类（从 model.py 拆分，2026-08-05 架构优化）。

职责：聊天 UI 应用模型的**纯状态容器**——不承载行为逻辑，仅定义数据结构。
从 ``model.py`` 拆分独立，使模型行为（AppModel 方法）与状态类型分层清晰。

Layer 0 — 仅依赖 dataclass/typing（无 TUI 运行时依赖）。
"""

from __future__ import annotations

import threading
from typing import Any

from src._compat import dataclass
from dataclasses import field
from enum import Enum

__all__ = [
    "ReasoningState",
    "ChatBlock",
    "CompletionState",
    "UserSelectState",
    "EditMsgSelectState",
    "ConfigViewState",
    "PluginViewState",
    "ModelViewState",
    "StatusState",
    "HistorySearchState",
    # ── 2026-10 新增全屏视图状态（ListViewState 基类 + 各视图子类） ──
    "ListViewState",
    "SessionsViewState",
    "ChangesViewState",
    "SandboxStatsViewState",
    "SandboxHistoryViewState",
    "SandboxRecordsViewState",
    "ThemeViewState",
    "SkillViewState",
    "McpViewState",
    "UsageViewState",
    "SearchViewState",
    "KeymapViewState",
    "NotifyViewState",
    "ExportViewState",
    "LogsViewState",
]


class ReasoningState(Enum):
    """推理通道状态机（与 _ReasoningState 等价语义）。"""

    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(eq=False)
class ChatBlock:
    """聊天块 — 一组已渲染行。

    ★ P3（review）：``eq=False``（身份语义）——ChatBlock 是**身份对象**
    （块查找一律用 ``is``，见 model.py BUG-11）；修复前 dataclass 默认
    ``__eq__`` 为逐字段值比较（含 ``lines``/``extra``/各缓存字段），
    两个内容相同的空块恒等 → ``list.index(block)``/``in`` 等值语义操作
    取到错误块（BUG-11 同根因），且大块比较成本高。

    Attributes:
        kind: 块类型（reasoning/content/user/tool/notification/error/
            write_line/splash/subagent/parse_info/separator）。
        lines: 已渲染的 AnsiLine 列表。
        extra: 附加数据（如工具调用组状态）。
    """

    kind: str
    lines: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    #: 未闭合块预览行（流式实时可见）——段落/代码块/表格等未闭合时由
    #: AnsiStreamRenderer 的预览渲染产出，每次 write 整体替换；块闭合时清空
    #: （确定行经 ``lines`` 落地）。不参与 committed_lines 提交。
    preview_lines: list = field(default_factory=list)
    #: 块的完整 markdown 源文本（流式累积）——终端 resize 时按新宽度整块
    #: 重渲染未关闭块（表格/代码块等定宽内容随宽度重排），修复宽度陈旧。
    source_text: str = ""
    #: 块是否已关闭（不再追加行）。仅连续的已关闭块可提交到增量缓存。
    closed: bool = False
    #: 已提交到缓存的行数（开放块随段落闭合增量提交 → 每帧只处理未提交尾）。
    committed_line_count: int = 0
    #: 关闭块冻结行缓存（list[ink Line]，方向D 步骤15）。关闭时构建全块 ink
    #: Line（含工具状态图标），供 ``_block_styled_lines`` 复用 ``Line.runs``
    #: 引用（免每帧 Style merge）。None=未冻结（开放块/未关闭）。
    _cached_ink_lines: list | None = None
    #: 开放块 styled 引用缓存（dict[AnsiLine, list[StyledRun]]，方向1）——
    #: ``_block_styled_lines`` 按行对象缓存 AnsiLine→StyledRun 转换结果，使
    #: ``_measure`` 的 ``cache[0] is styled`` 身份快路径跨帧命中（大 open 块
    #: 每帧零重建）。行被 block.lines 持有，dict 随 block GC 自然释放。
    _open_styled_cache: dict | None = None
    #: 工具卡内容行渲染结果缓存（dict[(AnsiLine, width, bg), list]，PERF-6）——
    #: ``tool_card_lines`` 对开放工具卡内容行按 ``(行对象, 宽度, 背景色)`` 缓存
    #: 截断（超宽行单行截断 + 省略号）+ 背景填充后的内容 runs（无边框，
    #: 2026-08-06 去边框后不含 pad/边框拼接；背景填充为「整行占满终端宽度」，
    #: 2026-10-05）
    #: ——修复前开放大工具卡（如长 bash 输出）每帧全量重建全部内容行 →
    #: 30Hz 渲染循环下 CPU 100%。行对象被 block.lines 持有，dict 随
    #: block GC 自然释放；关闭块冻结后不再访问。
    _tool_card_body_cache: dict | None = None
    #: 工具卡帧级缓存（tuple[key, list]，PERF-6）——开放工具卡完整输出列表
    #: （含动态状态图标色）在同一 time_glow 桶内跨帧复用，TEXT ``_wrap_cache``
    #: 命中 → 内容行零重建。key 含全部动态因素（行数/状态/呼吸色/省略计数）；
    #: 关闭块冻结后置 None 释放。
    _tool_card_frame_cache: tuple | None = None
    #: 工具卡内容行完整列表缓存（tuple[key, list]，PERF-6b）——内容行跨帧/
    #: 跨桶复用（标题行状态图标色动态，独立重建）。frame_cache 同桶快速路径
    #: miss（跨桶）时兜底复用内容行，TEXT ``_wrap_cache`` 命中。
    _tool_card_body_lines_cache: tuple | None = None


@dataclass
class CompletionState:
    """补全弹窗状态（_CmplHandler 注入）。"""

    visible: bool = False
    title: str = "补全"
    items: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    selected: int = 0
    start_pos: int = 0
    orig_prefix: str = ""
    types: list = field(default_factory=list)
    match_prefix: str = ""
    popup_height: int = 0
    # 斜杠命令描述（Claude TUI parity 步骤 3.7；与 items 对齐，缺省空列表）
    descriptions: list = field(default_factory=list)
    # 分栏说明模式（user_select 使用）：True 时弹窗左侧选项列表、
    # 右侧显示当前选中项说明；False（命令补全等）保持描述右侧灰显的既有行为
    split_desc: bool = False
    # ★ 弹窗高度锁定（补全弹窗闪烁修复 + 补白上限）：弹窗打开期间优先保持
    # （items 小幅减少时高度只增不减，补白 ≤ _LOCKED_PAD_LIMIT）——打字时
    # items 数量变化（5→2→1）若弹窗高度随之下调，input_area 高度变化触发
    # 文档缩短重排（物理缓冲无 delete-line，缩短短暂残留 → 漂移 → 全量重写
    # → 视觉闪烁）。锁定后 items 小幅减少时高度保持（底部短暂留白），doc
    # 高度不变 → 等高 diff 只重写弹窗行（不闪）；items 增加时高度跟随（增高，
    # 增长滚动自然）。items **大幅**减少（补白超过 _LOCKED_PAD_LIMIT，如
    # 20→1 项）时允许缩小——避免弹窗底部大片空白。hide_completions 重置为 0。
    locked_height: int = 0
    # ★ P3-6：补全弹窗行缓存（tuple[popup_snap, list[Line]]，PERF-7）——
    #   ``_popup_builder._build_popup_lines`` 动态挂载；dataclass 显式声明
    #   （防类型不完整/动态属性隐患），None=未缓存（弹窗不可见/内容变化后
    #   重建）。
    _popup_lines_cache: tuple | None = None
    # ★ P3（review）：补全弹窗滚动偏移显式声明——修复前由
    #   ``_popup_builder`` 动态挂载（``getattr(..., 0)`` + 动态赋值），与
    #   ``_popup_lines_cache`` 的「dataclass 显式声明（防类型不完整/动态属性
    #   隐患）」约定不一致（dataclass ``__eq__``/静态检查亦不覆盖）。
    _popup_scroll: int = 0


# UserSelectState 实现已下沉核心层（core.user_select_state），此处 re-export 兼容。
from ...core.user_select_state import UserSelectState  # noqa: E402


@dataclass
class EditMsgSelectState:
    """消息编辑选择弹窗状态（/editmsg 专用，独立于 user_select）。

    ★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
    /editmsg 消息选择从 user_select 协议（model.user_select +
    UserSelectPopup + bottom_view="user_select"）拆分为独立实现——
    独立状态（本类，model.editmsg_select）+ 独立组件
    （EditMsgSelectPopup）+ 独立底部视图（bottom_view="editmsg"）。

    与 UserSelectState 的差异：
      - 仅单选消息（无 multi_select / 无 option_descriptions 分栏）；
      - 每条消息只显示一行——options 为单行摘要（_user_msg_summary 生成，
        不再使用多行 option_lines）。

    Attributes:
        visible: 弹窗是否显示（编辑器打开/关闭）。
        seq: 弹窗会话序号（每次打开递增）——App 组件用 key 强制
            EditMsgSelectPopup 重挂载，重置组件内部 state（连续多次编辑
            不残留旧选中）。
        title: 弹窗标题。
        options: 消息单行摘要列表（每条消息一行）。
        previews: 每条消息的**全文预览**文本（与 options 对齐；弹窗下方
            预览区显示当前选中消息完整内容——2026-10-07 增强）。
        preview_scroll: 预览区滚动偏移（``[``/``]`` 滚动长消息全文）。
        filter: 弹窗内搜索过滤文本（``/`` 进入搜索输入；空串=不过滤）。
        search_mode: 是否处于弹窗内搜索输入模式（True 时组件独占按键输入，
            列表控件不参与导航）——2026-10-07 增强。
        selected: 当前高亮索引（组件维护；**原始消息索引**——过滤视图下
            组件负责视图位置 ↔ 原始索引换算）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        done: 交互是否已结束（组件写入）。
        action: 结束方式（confirmed/cancel/timeout）。
        result: 选中的摘要文本列表（组件写入，取消为空）。
        _final_lock: 终态写入锁（done/action/result 三字段原子写，
            first-write-wins 跨线程安全——编辑器轮询超时 vs 组件确认竞态）。
    """

    visible: bool = False
    seq: int = 0
    title: str = ""
    options: list = field(default_factory=list)
    previews: list = field(default_factory=list)
    filter: str = ""
    search_mode: bool = False
    selected: int = 0
    # ── 2026-10-07 第三批（editmsg 预览滚动） ──
    # preview_scroll: 预览区滚动偏移（``[``/``]`` 滚动长消息全文预览；
    #   切换选中消息复位 0）。
    preview_scroll: int = 0
    deadline: float = 0.0
    done: bool = False
    action: str = ""
    result: list = field(default_factory=list)
    #: 终态写入锁（repr/比较忽略——纯同步原语，非状态数据）
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def try_set_final(self, action: str, result: list) -> bool:
        """原子写入终态（first-write-wins，跨线程安全）。

        done/action/result 三字段在锁内一次性提交：done 已置位（其他线程
        已确认/已超时）时返回 False 且不覆盖，调用方放弃写入。

        用于统一组件侧 _commit（Enter/Esc）与编辑器轮询超时分支的终态写入
        （与 UserSelectState.try_set_final 同语义——独立实现，不共用代码）。

        Args:
            action: 结束方式（confirmed/cancel/timeout）。
            result: 终态结果列表（入参浅拷贝，调用方后续修改不影响）。

        Returns:
            True 本次写入生效；False 终态已由其他线程置位。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.result = list(result)
            self.done = True
            return True


@dataclass
class ConfigViewState:
    """配置中心视图状态（/config 命令注入，ConfigView 组件消费）。

    ★ 2026-08-20（用户需求：config 命令独立界面）：/config 打开全屏配置
    视图（``model.fullscreen == "config"``），ConfigView 组件整屏渲染配置
    列表并支持浏览/编辑。与 user_select/editmsg 同构的**跨线程协议**
    （命令线程轮询 done 清理，组件写导航/编辑状态）：

      - 命令线程（``_cmd_config``）：构建 entries
        （``view_model.build_config_entries``）→ 设置本状态（visible=True,
        seq+1, entries）→ ``model.fullscreen="config"`` →
        request_bottom_redraw → 轮询 ``done``（带 deadline 超时）→ finally
        清理（重置本状态 + fullscreen 置空 + request_bottom_redraw）；
      - 组件（ConfigView）：浏览模式写 ``selected``；Enter 进入编辑模式
        （editing/edit_key/edit_value）；编辑确认经类型校验后**直接调用
        ``update_config`` 持久化**（config loader 有锁 + 原子写，线程安全）
        并刷新 ``entries[i].value/value_text`` + 写 ``message``；Esc 关闭
        经 ``try_set_final("cancel")`` 原子终态写入（first-write-wins）。

    Attributes:
        visible: 视图是否显示（命令打开/清理）。
        seq: 视图会话序号（每次打开递增）——App 组件用 key 强制
            ConfigView 重挂载，重置内部 use_state（连续打开不残留选中）。
        entries: 配置项列表（``view_model.build_config_entries`` 产出；
            组件只读，编辑确认后更新 value/value_text 显示）。
        selected: 当前选中配置项索引（组件导航维护）。
        editing: 是否处于编辑模式（Enter 进入 / 确认或取消退出）。
        edit_mode: 编辑界面类型（"input"=文本/JSON 输入行；"select"=
            候选选项选择列表——枚举/布尔/模型等有候选集合的配置项；
            "json"=子 JSON 结构化编辑界面——list/dict 有子结构的配置项；
            "json_input"=json 界面内的子输入——编辑元素/追加条目）。
        edit_key: 当前编辑项的写回键（CONFIG_KEYS 大写键名或直接键名）。
        edit_value: 文本输入模式的编辑缓冲（字符累积/退格删除）。
        edit_options: 选择模式的候选值列表（来自 entry["options"]）。
        edit_options_desc: 与 edit_options 等长的候选说明列表。
        edit_selected: 选择模式当前高亮索引（组件导航维护）。
        edit_json_data: json 界面正在编辑的**根**子 JSON 数据（list/dict）。
        edit_json_path: json 界面当前容器的**递归路径段**（list/dict 段；
            空=顶层根容器）。嵌套条目（值为 list/dict）Enter 递归进入
            （path 追加段），Esc 逐级返回上层；回到顶层 Esc 才保存写回。
        edit_json_keys: 当前容器为 dict 时的键列表（有序；list 为空）。
        edit_json_selected: json 界面当前选中条目索引（组件导航维护）。
        edit_json_action: json 子输入提交动作（"edit"=更新选中条目 /
            "append"=追加新条目；list 元素与 dict 值统一文本编辑）。
        edit_error: 编辑校验/写入失败提示（空串=无错误）。
        message: 操作反馈消息（如「已更新 model = xxx」）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        done: 交互是否已结束（Esc 关闭或命令超时置位）。
        action: 结束方式（cancel/timeout）。
        _final_lock: 终态写入锁（done/action 原子写，first-write-wins
            跨线程安全——组件 Esc 关闭 vs 命令超时竞态）。
    """

    visible: bool = False
    seq: int = 0
    entries: list = field(default_factory=list)
    selected: int = 0
    editing: bool = False
    edit_mode: str = "input"
    edit_key: str = ""
    edit_value: str = ""
    edit_options: list = field(default_factory=list)
    edit_options_desc: list = field(default_factory=list)
    edit_selected: int = 0
    edit_json_data: Any = None
    edit_json_path: list = field(default_factory=list)
    edit_json_keys: list = field(default_factory=list)
    edit_json_selected: int = 0
    edit_json_action: str = "edit"
    edit_error: str = ""
    message: str = ""
    # ── 2026-10-07（config 增强：搜索 / 帮助 / 恢复默认 / 复制 / 撤销 / 来源） ──
    # search_mode: 搜索输入模式（``/`` 进入；True 时组件独占按键输入）。
    search_mode: bool = False
    # search_query: 搜索输入缓冲。
    search_query: str = ""
    # search_pattern: 已执行的搜索文本（"" = 无搜索）。
    search_pattern: str = ""
    # search_matches: 匹配配置项索引列表。
    search_matches: list = field(default_factory=list)
    # search_idx: 当前匹配在 matches 中的位置（-1 = 未定位）。
    search_idx: int = -1
    # search_filter: 过滤模式（True = 列表只显示匹配配置项）。
    search_filter: bool = False
    # help_open: 帮助面板开关（``?`` 切换——右栏/主区显示键位速查）。
    help_open: bool = False
    # help_scroll: 帮助面板滚动偏移（主区覆盖渲染时内容滚动）。
    help_scroll: int = 0
    # undo_stack: 撤销栈（[(key, 旧值, 旧显示文本, path), ...]——``u`` 逐条撤回）。
    undo_stack: list = field(default_factory=list)
    # rc_file: 配置文件路径（头部「来源」显示；命令线程注入）。
    rc_file: str = ""
    # ── 2026-10-07 第三批（分组折叠 / 撤销面板 / 导出导入） ──
    # collapsed_groups: 折叠的配置分组集合（按 path 首段分组；``za``/``zc``/
    #   ``zo`` 切换当前组、``zC``/``zO`` 全部；组内条目从列表隐藏）。
    collapsed_groups: set = field(default_factory=set)
    # pending_prefix: 待定多键前缀（``z``——``za``/``zc``/``zo``/``zC``/``zO``）。
    pending_prefix: str = ""
    # undo_cursor: 撤销历史面板（``edit_mode="undo"``）当前选中索引。
    undo_cursor: int = 0
    deadline: float = 0.0
    done: bool = False
    action: str = ""
    #: 终态写入锁（repr/比较忽略——纯同步原语，非状态数据）
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def reset_edit_state(self) -> None:
        """复位编辑态字段（★ P3 review：集中重置入口）。

        ``config_view._cancel_edit`` 原为散点复位，新增字段易遗漏（如
        ``edit_json_action`` 曾残留 ``"append"``、``message`` 残留陈旧提示）；
        集中在单一实现，两者共用（``_cancel_edit`` 委托本方法）。
        """
        self.editing = False
        self.edit_mode = "input"
        self.edit_error = ""
        self.edit_options = []
        self.edit_options_desc = []
        self.edit_json_data = None
        self.edit_json_path = []
        self.edit_json_keys = []
        self.edit_json_selected = 0
        self.edit_json_action = "edit"
        self.message = ""

    def try_set_final(self, action: str) -> bool:
        """原子写入终态（first-write-wins，跨线程安全）。

        done/action 在锁内一次性提交：done 已置位（其他线程已关闭/已超时）
        时返回 False 且不覆盖，调用方放弃写入。与 UserSelectState/
        EditMsgSelectState 的 try_set_final 同语义——独立实现，不共用代码。

        Args:
            action: 结束方式（cancel/timeout）。

        Returns:
            True 本次写入生效；False 终态已由其他线程置位。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.done = True
            return True


@dataclass
class PluginViewState:
    """已加载插件视图状态（/plugin 命令注入，PluginView 组件消费）。

    /plugin 打开全屏已加载插件视图（``model.fullscreen == "plugin"``）——
    PluginView 组件整屏渲染（左插件列表 + 右详细信息）。与 config/user_select
    同构的跨线程协议：命令线程设置本状态（visible=True, seq+1, entries）→
    ``model.fullscreen="plugin"`` → request_bottom_redraw → 轮询 ``done``
    （带 deadline 超时）→ finally 清理；组件写导航状态，Esc 经
    ``try_set_final("cancel")`` 原子终态写入（first-write-wins）。

    Attributes:
        visible: 视图是否显示（命令打开/清理）。
        seq: 视图会话序号（每次打开递增；供重挂载/调试）。
        entries: 已加载插件条目列表（``plugins.view_model.build_plugin_entries``
            产出；组件只读）。
        selected: 左栏当前选中条目索引（组件导航维护）。
        scroll: 右栏详情滚动偏移（0=顶部）。
        cursor: 右栏详情光标行（绝对行索引，vim cursorline 语义）。
        pane: 当前焦点面板（"list"=左列表 / "detail"=右详情）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        done: 交互是否已结束（Esc 关闭或命令超时置位）。
        action: 结束方式（cancel/timeout）。
        _final_lock: 终态写入锁（done/action 原子写，first-write-wins
            跨线程安全——组件 Esc 关闭 vs 命令超时竞态）。
    """

    visible: bool = False
    seq: int = 0
    entries: list = field(default_factory=list)
    selected: int = 0
    scroll: int = 0
    cursor: int = 0
    pane: str = "list"
    # ── 2026-10-07（plugin 增强：搜索 / 帮助 / 复制 / 统计） ──
    # search_mode: 搜索输入模式（``/`` 进入；True 时组件独占按键输入）。
    search_mode: bool = False
    # search_query: 搜索输入缓冲。
    search_query: str = ""
    # search_pattern: 已执行的搜索文本（"" = 无搜索）。
    search_pattern: str = ""
    # search_matches: 匹配插件条目索引列表（按列表项顺序）。
    search_matches: list = field(default_factory=list)
    # search_idx: 当前匹配在 matches 中的位置（-1 = 未定位）。
    search_idx: int = -1
    # search_filter: 过滤模式（True = 列表只显示匹配插件）。
    search_filter: bool = False
    # help_open: 帮助面板开关（``?`` 切换——右栏显示键位速查）。
    help_open: bool = False
    # status_message: 底部状态提示（复制/无匹配等操作反馈；空串不渲染）。
    status_message: str = ""
    # ── 2026-10-07 第三批（依赖关系视图 / 状态分类过滤） ──
    # relation_open: 依赖关系面板开关（``r`` 切换——右栏显示依赖/被依赖/
    #   提供服务关系，Enter 跳转到相关插件）。
    relation_open: bool = False
    # relation_cursor / relation_scroll: 关系面板光标行与滚动偏移。
    relation_cursor: int = 0
    relation_scroll: int = 0
    # filter_state: 按状态过滤（""=全部；非空=只显示该状态插件）。
    filter_state: str = ""
    # filter_kind: 按分类过滤（""=全部；非空=只显示该分类插件）。
    filter_kind: str = ""
    deadline: float = 0.0
    done: bool = False
    action: str = ""
    #: 终态写入锁（repr/比较忽略——纯同步原语，非状态数据）
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def try_set_final(self, action: str) -> bool:
        """原子写入终态（first-write-wins，跨线程安全）。

        done/action 在锁内一次性提交：done 已置位（其他线程已关闭/已超时）
        时返回 False 且不覆盖，调用方放弃写入。与 ConfigViewState/
        UserSelectState 的 try_set_final 同语义——独立实现，不共用代码。

        Args:
            action: 结束方式（cancel/timeout）。

        Returns:
            True 本次写入生效；False 终态已由其他线程置位。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.done = True
            return True


@dataclass
class ModelViewState:
    """模型选择器视图状态（/models 命令注入，ModelView 组件消费）。

    /models（或 /model 无参数）打开全屏模型选择器（``model.fullscreen ==
    "model"``）——ModelView 组件整屏渲染（模型列表 + 增删改选表单）。与
    config/plugin 同构的**跨线程协议**：

      - 命令线程（``_cmd_models``）：构建 entries
        （``config.model_profiles.build_model_entries``）→ 设置本状态
        （visible=True, seq+1, entries）→ ``model.fullscreen="model"`` →
        request_bottom_redraw → 轮询 ``done``（带 deadline 超时），期间检测
        ``applied_seq`` 变化即把选中条目应用到当前会话（写 RC + 同步
        session.model / 状态栏）→ finally 清理；
      - 组件（ModelView）：浏览/表单/字段输入三态；选择条目写
        ``applied_seq``+``applied``（命令线程应用）；档案增删改经
        ``config.model_profiles.save_profiles`` 直接持久化（loader 有锁 +
        原子写，线程安全）；Esc 关闭经 ``try_set_final("cancel")`` 原子终态
        写入（first-write-wins）。

    Attributes:
        visible: 视图是否显示（命令打开/清理）。
        seq: 视图会话序号（每次打开递增）——App key 强制重挂载，重置内部
            use_state（连续打开不残留选中/表单态）。
        entries: 模型条目列表（``build_model_entries`` 产出；组件只读）。
        selected: 当前选中条目索引（组件导航维护）。
        message: 操作反馈消息（如「已保存」「已删除」）。
        edit_error: 表单校验 / 写入失败提示（空串=无错误）。
        editing: 是否处于表单模式（新增/编辑档案）。
        edit_mode: 表单子模式（"form"=字段列表；"field"=当前字段输入）。
        form_index: 正在编辑的档案下标（None = 新增）。
        form_values: 表单字段值（``config.model_profiles.FIELD_KEYS`` 为键）。
        form_selected: 表单当前选中字段索引。
        form_edit_value: 字段输入缓冲（字符累积/退格删除）。
        form_edit_cursor: 字段输入光标位置（0..len；支持 ←/→/Home/End
            移动与中间插入/删除）。
        form_is_new: 是否新增（决定保存时 append 还是替换）。
        search_mode/search_query/search_pattern/search_matches/search_idx/
            search_filter: 搜索输入 / 已执行模式 / 匹配列表 / 当前匹配 / 过滤。
        help_open/help_scroll: 帮助面板开关与滚动偏移。
        applied_seq: 应用（选择）计数（组件递增，命令线程比对检测新选择）。
        applied: 待应用条目（组件写，命令线程读取并应用到会话）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        done: 交互是否已结束（Esc 关闭或命令超时置位）。
        action: 结束方式（cancel/timeout）。
        _final_lock: 终态写入锁（done/action 原子写，first-write-wins
            跨线程安全——组件 Esc 关闭 vs 命令超时竞态）。
    """

    visible: bool = False
    seq: int = 0
    entries: list = field(default_factory=list)
    selected: int = 0
    message: str = ""
    edit_error: str = ""
    # ── 表单（新增 / 编辑档案） ──
    editing: bool = False
    edit_mode: str = "form"
    form_index: Any = None
    form_values: dict = field(default_factory=dict)
    form_selected: int = 0
    form_edit_value: str = ""
    form_edit_cursor: int = 0
    form_is_new: bool = False
    # 「提供商」字段的选择界面（edit_mode == "select"）
    form_select_options: list = field(default_factory=list)
    form_select_desc: list = field(default_factory=list)
    form_select_index: int = 0
    # ── 搜索 ──
    search_mode: bool = False
    search_query: str = ""
    search_pattern: str = ""
    search_matches: list = field(default_factory=list)
    search_idx: int = -1
    search_filter: bool = False
    # ── 帮助面板 ──
    help_open: bool = False
    help_scroll: int = 0
    # ── 应用（选择）结果回传：组件递增 applied_seq，命令线程检测后应用 ──
    applied_seq: int = 0
    applied: Any = None
    deadline: float = 0.0
    done: bool = False
    action: str = ""
    #: 终态写入锁（repr/比较忽略——纯同步原语，非状态数据）
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def reset_edit_state(self) -> None:
        """复位表单/提示态字段（集中重置入口，进入/退出表单共用）。"""
        self.editing = False
        self.edit_mode = "form"
        self.form_index = None
        self.form_values = {}
        self.form_selected = 0
        self.form_edit_value = ""
        self.form_edit_cursor = 0
        self.form_is_new = False
        self.form_select_options = []
        self.form_select_desc = []
        self.form_select_index = 0
        self.edit_error = ""
        self.message = ""

    def try_set_final(self, action: str) -> bool:
        """原子写入终态（first-write-wins，跨线程安全）。

        与 ConfigViewState / PluginViewState 的 try_set_final 同语义——
        独立实现，不共用代码。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.done = True
            return True


@dataclass
class StatusState:
    """状态栏数据（移植 BottomBarStatus 状态域）。"""

    model_name: str = ""
    tool_count: int = 0
    tool_fail: int = 0
    tool_total: int = 0
    main_phase: str = ""
    main_phase_start: float = 0.0
    tool_phase_start: float = 0.0
    status_active: bool = False
    cpu: int = 0
    mem: int = 0
    #: 后台 bash 任务总数（主 agent + 全部 subagent 聚合，运行中未完成）
    bg_bash_count: int = 0
    #: 后台 subagent 任务总数（主 agent 派发，运行中未完成）
    bg_subagent_count: int = 0
    #: 正在压缩上下文的 Agent 总数（主 agent + 全部 subagent 聚合）
    compaction_active: int = 0
    #: 最近一次压缩结束的单调时钟时间戳（0 = 本会话尚未压缩）——压缩在空闲期
    #: 结束后，状态栏在宽限期内短暂展示「总tok / tok/s」，让压缩消耗的 token
    #: 可见（压缩摘要是静默后台流式调用，不进入活跃期门控）。
    compaction_last_end_ts: float = 0.0


@dataclass
class HistorySearchState:
    """反向历史搜索状态（方向D 步骤14，Ctrl+R 配置门控）。

    Attributes:
        query: 搜索查询（进入搜索时的缓冲文本）。
        matches: 匹配历史列表（最近优先，history[0] 为最新）。
        index: 当前匹配索引（-1 表示无匹配）。
        active: 是否处于搜索模式（True 时 input-area 渲染搜索覆盖行）。
    """

    query: str = ""
    matches: list = field(default_factory=list)
    index: int = -1
    active: bool = False


# ═══════════════════════════════════════════════════════════
# 2026-10 新增全屏视图状态（通用基类 + 各视图子类）
# ═══════════════════════════════════════════════════════════
#
# 新增的一批全屏视图（sessions/changes/theme/skill/mcp/usage/search/
# keymap/notify/export）共享同一套「列表 + 详情 + 搜索 + 帮助 + 终态」交互
# 骨架，其**与具体数据无关**的字段（导航/搜索/帮助/终态）收敛到
# ``ListViewState`` 基类（通用渲染与输入逻辑只读写基类字段）；各视图子类只
# 追加自身特有的数据与操作字段。基类保持 Layer 0（仅 dataclass/threading），
# 通用渲染工具（依赖 ink）在 ``tui/app/_view_common.py``。


@dataclass
class ListViewState:
    """通用视图状态基类（导航 / 搜索 / 帮助 / 过滤 / 终态）。

    Attributes:
        visible: 视图是否显示（命令打开/清理）。
        seq: 视图会话序号（每次打开递增）——App 组件用 key 强制重挂载。
        selected: 主列表当前选中索引。
        scroll: 详情/内容滚动偏移。
        cursor: 详情/内容光标行（vim cursorline 语义）。
        pane: 当前焦点面板（"list" / "detail"）。
        search_mode: 是否处于搜索输入模式。
        search_query: 搜索输入缓冲。
        search_pattern: 已执行的搜索文本（"" = 无搜索）。
        search_matches: 匹配索引列表。
        search_idx: 当前匹配在 matches 中的位置（-1 = 未定位）。
        search_filter: 过滤模式（主列表只显示匹配项）。
        help_open: 帮助面板开关。
        help_scroll: 帮助面板滚动偏移。
        status_message: 底部状态提示（空串不渲染）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        done: 交互是否已结束。
        action: 结束方式（cancel/done/timeout）。
        _final_lock: 终态写入锁（first-write-wins 跨线程安全）。
    """

    visible: bool = False
    seq: int = 0
    selected: int = 0
    scroll: int = 0
    cursor: int = 0
    pane: str = "list"
    search_mode: bool = False
    search_query: str = ""
    search_pattern: str = ""
    search_matches: list = field(default_factory=list)
    search_idx: int = -1
    search_filter: bool = False
    help_open: bool = False
    help_scroll: int = 0
    status_message: str = ""
    deadline: float = 0.0
    done: bool = False
    action: str = ""
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def try_set_final(self, action: str = "cancel") -> bool:
        """原子写入终态（first-write-wins，跨线程安全）。"""
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.done = True
            return True

    def reset_search(self) -> None:
        """清空搜索态（查询/模式/匹配）。"""
        self.search_mode = False
        self.search_query = ""
        self.search_pattern = ""
        self.search_matches = []
        self.search_idx = -1
        self.search_filter = False

    def reset_panels(self) -> None:
        """关闭帮助面板并复位详情光标/滚动。"""
        self.help_open = False
        self.help_scroll = 0
        self.cursor = 0
        self.scroll = 0


@dataclass
class SessionsViewState(ListViewState):
    """会话浏览器视图状态（/sessions 打开，SessionsView 消费）。

    Attributes:
        entries: 会话摘要列表（id/title/model/saved_at/message_count）。
        applied_seq: 应用（加载）计数（组件递增，命令线程比对）。
        applied: 待加载会话条目（组件写，命令线程读取）。
        rename_mode: 是否处于重命名输入模式。
        rename_value: 重命名输入缓冲。
        rename_cursor: 重命名输入光标（0..len）。
        delete_confirm: 待删除会话 id（"" = 无待确认删除）。
    """

    entries: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    rename_mode: bool = False
    rename_value: str = ""
    rename_cursor: int = 0
    delete_confirm: str = ""


@dataclass
class ChangesViewState(ListViewState):
    """文件变更审查器视图状态（/changes 打开，ChangesView 消费）。

    Attributes:
        entries: 文件变更条目列表（path/change_label/before/after/records）。
        messages: 消息维度条目列表（index/count/files/tools/time/changes）。
        applied_seq: 应用（回滚）计数（组件递增，命令线程比对）。
        applied: 待回滚条目（组件写，命令线程读取）。
        revert_confirm: 待确认回滚的文件路径（"" = 无待确认）。
        view_mode: 左栏模式（"file" 文件 / "tree" 目录树 / "message" 消息维度）。
        detail_mode: 右栏模式（"diff" 差异 / "history" 单文件历史时间线 /
            "stats" 统计区块）。
        sort_mode: 文件列表排序（"path"/"records"/"recent"/"index"）。
        type_filter: 变更类型过滤（"" = 全部；否则为变更标签，如 "修改"）。
        sections: 统计区块数据（统计面板 / 命令线程构建）。
        revert_all_confirm: 是否处于「回滚全部」二次确认。
        restore_mode: 是否处于「回滚到消息索引」输入模式。
        restore_value: 回滚到消息索引的输入缓冲。
        export_message: 导出反馈文本（空串不渲染）。
    """

    entries: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    applied_consumed: int = 0
    revert_confirm: str = ""
    view_mode: str = "file"
    detail_mode: str = "diff"
    sort_mode: str = "path"
    type_filter: str = ""
    sections: list = field(default_factory=list)
    revert_all_confirm: bool = False
    restore_mode: bool = False
    restore_value: str = ""
    export_message: str = ""


@dataclass
class SandboxStatsViewState(ListViewState):
    """文件沙盒概览视图状态（/sandbox 打开，SandboxStatsView 消费）。

    Attributes:
        sections: 统计区块列表（title/rows；命令线程构建）。
        applied_seq: 操作计数（组件递增，命令线程比对）。
        applied: 待执行操作（组件写：{"action", ...}）。
        refresh_seq: 刷新计数（组件递增，命令线程据此重读统计）。
        clear_confirm: 是否处于「清空沙盒」二次确认。
    """

    sections: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    applied_consumed: int = 0
    refresh_seq: int = 0
    clear_confirm: bool = False


@dataclass
class SandboxHistoryViewState(ListViewState):
    """沙盒消息维度历史视图状态（/sandbox history 打开，SandboxHistoryView 消费）。

    Attributes:
        entries: 消息维度条目（index/count/files/tools/time/changes）。
        applied_seq: 回滚计数（组件递增，命令线程比对）。
        applied: 待回滚动作（组件写：{"action": "restore-message", "index": N}）。
        restore_confirm: 待确认回滚的消息索引（-1 = 无待确认）。
    """

    entries: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    applied_consumed: int = 0
    restore_confirm: int = -1


@dataclass
class SandboxRecordsViewState(ListViewState):
    """沙盒变更记录流水视图状态（/sandbox records 打开，SandboxRecordsView 消费）。

    Attributes:
        entries: 记录条目（path/index/tool/time/change_label/before/after）。
        applied_seq: 回滚计数（组件递增，命令线程比对）。
        applied: 待回滚动作（组件写：{"action": "revert", "path": p}）。
        sort_desc: 是否按消息索引倒序（最新在前）。
        revert_confirm: 待确认回滚的文件路径。
    """

    entries: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    applied_consumed: int = 0
    sort_desc: bool = False
    revert_confirm: str = ""


@dataclass
class ThemeViewState(ListViewState):
    """主题选择器视图状态（/theme 打开，ThemeView 消费）。

    Attributes:
        entries: 主题名列表。
        original: 打开时的主题名（Esc 取消时恢复）。
        applied_seq: 预览应用计数（组件递增，命令线程比对）。
        applied: 待应用主题名（组件写，命令线程读取并写 RC）。
    """

    entries: list = field(default_factory=list)
    original: str = ""
    applied_seq: int = 0
    applied: Any = None


@dataclass
class SkillViewState(ListViewState):
    """技能浏览器视图状态（/skill 打开，SkillView 消费）。

    Attributes:
        entries: 技能条目列表（name/description/source/...）。
        applied_seq: 操作计数（组件递增，命令线程比对）。
        applied: 待执行操作（组件写：{"action", "target"}）。
        input_mode: 输入模式（""=无；"install"/"update"/"remove"/"info"）。
        input_value: 输入缓冲（仓库 / 技能名）。
        input_cursor: 输入光标（0..len）。
        busy: 是否有操作正在进行（命令线程置位；运行中拒绝新操作）。
        refresh_seq: 刷新计数（组件递增，命令线程据此重读技能列表）。
    """

    entries: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    input_mode: str = ""
    input_value: str = ""
    input_cursor: int = 0
    busy: bool = False
    refresh_seq: int = 0


@dataclass
class McpViewState(ListViewState):
    """MCP 服务器管理视图状态（/mcp 打开，McpView 消费）。

    Attributes:
        entries: MCP 服务器条目列表（name/transport/status/enabled/tools）。
        applied_seq: 操作计数（组件递增，命令线程比对）。
        applied: 待执行操作（组件写：{"action", "target"}）。
        refresh_seq: 刷新计数（组件递增，命令线程据此重读状态）。
        confirm_reconnect: 待确认重连的服务器名（"" = 无待确认）。
    """

    entries: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    refresh_seq: int = 0
    confirm_reconnect: str = ""


@dataclass
class UsageViewState(ListViewState):
    """用量仪表盘视图状态（/usage 打开，UsageView 消费）。

    Attributes:
        sections: 统计区块列表（title/rows；由命令线程构建）。
        refresh_seq: 刷新计数（组件递增，命令线程据此重读统计）。
    """

    sections: list = field(default_factory=list)
    refresh_seq: int = 0


@dataclass
class SearchViewState(ListViewState):
    """对话内全文搜索视图状态（/search 打开，SearchView 消费）。

    Attributes:
        messages: 注入的会话消息列表（命令线程读取；组件搜索数据源）。
        results: 搜索结果列表（组件按搜索文本计算；每项含消息索引/片段）。
        jump_seq: 跳转计数（组件递增，命令线程比对）。
        jump_target: 待跳转的消息索引（组件写，命令线程读取）。
    """

    messages: list = field(default_factory=list)
    results: list = field(default_factory=list)
    jump_seq: int = 0
    jump_target: Any = None


@dataclass
class KeymapViewState(ListViewState):
    """键位自定义编辑器视图状态（/keymap 打开，KeymapView 消费）。

    Attributes:
        entries: 键位绑定条目列表（id/key/action/description）。
        applied_seq: 改键计数（组件递增，命令线程比对）。
        applied: 待应用绑定（组件写：{"id", "key"}）。
        edit_mode: 是否处于改键输入模式。
        edit_value: 改键输入缓冲（组合键文本）。
    """

    entries: list = field(default_factory=list)
    applied_seq: int = 0
    applied: Any = None
    edit_mode: bool = False
    edit_value: str = ""


@dataclass
class NotifyViewState(ListViewState):
    """通知/事件日志视图状态（/notify 打开，NotifyView 消费）。

    Attributes:
        entries: 日志条目列表（kind/time/title/body/level）。
        refresh_seq: 刷新计数（组件递增，命令线程据此重读日志缓冲）。
    """

    entries: list = field(default_factory=list)
    refresh_seq: int = 0


@dataclass
class ExportViewState(ListViewState):
    """导出向导视图状态（/export 打开，ExportView 消费）。

    Attributes:
        format: 导出格式（"md"/"json"）。
        scope: 导出范围（"all"/"user"/"assistant"）。
        path: 输出路径（空 = 默认时间戳命名）。
        message: 操作反馈消息。
        error: 错误提示。
    """

    format: str = "md"
    scope: str = "all"
    path: str = ""
    message: str = ""
    error: str = ""
    editing: bool = False


@dataclass
class LogsViewState(ListViewState):
    """会话日志 / 投影浏览器视图状态（/logs 打开，LogsView 消费）。

    会话事实源 = ``SessionLog``（仅追加事件日志）；视图同时呈现事件流、由事件
    投影出的模型历史、各投影单元状态与「模型可见即已记录」一致性校验——数据
    全部由命令线程构建后写入本状态（组件只读）。

    Attributes:
        entries: 会话事件条目列表（seq/type/label/icon/time/summary/data）。
        messages: 模型历史投影条目列表（derive_messages 结果；每条含
            role/index/summary/content/reasoning/tool_calls）。
        projections: 投影单元状态条目列表（name/state_text/state_rows）。
        verify_ok: 模型历史与日志投影是否一致（None = 不可校验）。
        verify_text: 一致性校验结果文本。
        stats: 统计条目列表（(label, value) 二元组；事件总数 / 消息数 /
            各类型计数等）。
        pane_mode: 右栏模式（"event" 事件详情 / "messages" 消息投影 /
            "projections" 投影状态 / "verify" 一致性校验）。
        follow_tail: 是否跟随最新事件（默认 True——视图打开即定位末条并随
            会话日志增长实时跟进；用户手动导航后置 False，``F`` 键重新开启）。
        refresh_seq: 刷新计数（组件递增，命令线程据此重读会话日志）。
    """

    entries: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    projections: list = field(default_factory=list)
    verify_ok: Any = None
    verify_text: str = ""
    stats: list = field(default_factory=list)
    pane_mode: str = "event"
    follow_tail: bool = True
    refresh_seq: int = 0
