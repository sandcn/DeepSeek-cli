"""AppModel 状态类型 — 纯数据类（从 model.py 拆分，2026-08-05 架构优化）。

职责：聊天 UI 应用模型的**纯状态容器**——不承载行为逻辑，仅定义数据结构。
从 ``model.py`` 拆分独立，使模型行为（AppModel 方法）与状态类型分层清晰。

Layer 0 — 仅依赖 dataclass/typing（无 TUI 运行时依赖）。
"""

from __future__ import annotations

import threading

from src._compat import dataclass
from dataclasses import field
from enum import Enum

__all__ = [
    "ReasoningState",
    "ChatBlock",
    "CompletionState",
    "UserSelectState",
    "EditMsgSelectState",
    "StatusState",
    "HistorySearchState",
]


class ReasoningState(Enum):
    """推理通道状态机（与 _ReasoningState 等价语义）。"""

    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass
class ChatBlock:
    """聊天块 — 一组已渲染行。

    Attributes:
        kind: 块类型（reasoning/content/user/tool/notification/error/
            write_line/splash/subagent/parse_info/separator）。
        lines: 已渲染的 AnsiLine 列表。
        extra: 附加数据（如工具调用组状态）。
    """

    kind: str
    lines: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
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
    #: 工具卡内容行 wrap 结果缓存（dict[(AnsiLine, width), list]，PERF-6）——
    #: ``tool_card_lines`` 对开放工具卡内容行按 ``(行对象, 宽度)`` 缓存
    #: wrap+截断后的内容 runs（无边框，2026-08-06 去边框后不含 pad/边框拼接）
    #: ——修复前开放大工具卡（如长 bash 输出）每帧全量 ``wrap_line`` 重建全部
    #: 内容行 → 10Hz 渲染循环下 CPU 100%。行对象被 block.lines 持有，dict 随
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


@dataclass
class UserSelectState:
    """用户选择弹窗状态（user_select 工具注入，UserSelectPopup 组件消费）。

    React Ink 化（2026-08-05）：user_select 不再走命令补全弹窗
    （CompletionState + show_completions + raw I/O），改为独立的 React Ink
    组件 ``UserSelectPopup`` 渲染与交互（use_input + use_state）。

    ★ 2026-08-19（用户需求：user_select 并发 + tab 切换，参考 Claude
    AskUserQuestion）：多个并发 user_select 工具调用各自构造一个本状态并
    append 到 ``model.user_selects`` 并发队列（真源）——``UserSelectPopup``
    以 tab 形式全部一起显示、Tab/←/→ 切换焦点。``model.user_select``
    保留为兼容字段（指向最近打开/活跃的 state）。

    ★ 2026-08-18（用户需求：editmsg 与 user_select 不能用同一份代码）：
    /editmsg 消息选择已拆分为独立协议（EditMsgSelectState +
    EditMsgSelectPopup + bottom_view="editmsg"），本状态仅服务
    ``user_select`` 工具与 ``CommandUiAdapter``（options 为单行纯文本，
    无多行 option_lines）。

    Attributes:
        visible: 弹窗是否显示（工具打开/关闭）。
        seq: 弹窗会话序号（每次打开递增）——App 组件用 key 强制
            UserSelectPopup 重挂载，重置组件内部 state（连续多次调用
            不残留旧选中/旧勾选）。
        title: 弹窗标题。
        options: 选项字符串列表。
        option_descriptions: 与 options 等长的说明列表（长度不足补齐空串）。
        multi_select: 是否多选。
        default_options: 默认选项（超时/取消/非交互回退）。
        selected: 当前高亮索引（组件维护）。
        checked: 多选勾选索引列表（组件维护，提交时按索引排序）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        answered: 该问题是否**已回答**（Enter 确认/Esc 取消；tab 标记
            [×]）——提交（done）前可**重新回答**（mark_answered 覆盖
            action/result，2026-08-19 用户需求：已经回答的可以重新答）。
        done: 交互是否已结束（**整体提交**或工具超时置位）——done 后 tab
            [×] 终态锁定（只读），工具协程轮询到 done 返回结果。
        action: 结束方式（confirmed/cancel/timeout）。
        result: 选中的 options 子集（组件/工具写入）。
        _final_lock: 终态写入锁（done/action/result 三字段原子写，
            first-write-wins 跨线程安全——工具协程超时 vs 组件确认竞态）。
    """

    visible: bool = False
    seq: int = 0
    title: str = ""
    options: list = field(default_factory=list)
    option_descriptions: list = field(default_factory=list)
    multi_select: bool = False
    default_options: list = field(default_factory=list)
    selected: int = 0
    checked: list = field(default_factory=list)
    deadline: float = 0.0
    answered: bool = False
    done: bool = False
    action: str = ""
    result: list = field(default_factory=list)
    #: 终态写入锁（repr/比较忽略——纯同步原语，非状态数据）
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def mark_answered(self, action: str, result: list) -> bool:
        """标记该问题已回答（Enter 确认 / Esc 取消 / **重新回答**）。

        ★ 2026-08-19（用户需求：已经回答的可以重新答）：与
        ``try_set_final``（整体提交终态，first-write-wins 置 done）区分——
        本方法只写 ``action/result/answered``（不置 done），提交前可**反复
        覆盖**（重答：切回已答 tab 重新导航选择后 Enter，新选择覆盖旧值）。

        Args:
            action: 结束方式（confirmed/cancel）。
            result: 该问题的选择结果（入参浅拷贝）。

        Returns:
            True 本次写入生效；False 已提交/超时（done 置位，终态锁定）。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.result = list(result)
            self.answered = True
            return True

    def try_set_final(self, action: str, result: list) -> bool:
        """原子写入终态（first-write-wins，跨线程安全）。

        done/action/result 三字段在锁内一次性提交：done 已置位（其他线程
        已确认/已超时）时返回 False 且不覆盖，调用方放弃写入。

        用于统一组件侧 _commit（Enter/Esc）与工具侧超时分支的终态写入，
        消除「工具超时分支无条件覆盖组件确认结果」的竞态窗口
        （2026-08-17 修复：UserSelect timeout 精确性验证有机率复现）。

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
        selected: 当前高亮索引（组件维护）。
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
    selected: int = 0
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
