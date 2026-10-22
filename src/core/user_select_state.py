"""UserSelectState — 用户选择弹窗状态（核心层纯数据类）。

下沉自 ``tui.app._state_types``（零 TUI 运行时依赖），供 ``user_select``
工具（基础设施层）与表现层组件共享，消除工具对表现层的反向依赖。
表现层 ``tui.app._state_types`` 仅 re-export 兼容。

并发语义（2026-08-19）：多个并发 user_select 工具调用各自构造一个本状态
并 append 到模型并发队列；``try_set_final`` 为跨线程 first-write-wins
终态写入，``mark_answered`` 支持提交前重答。

★ 2026-10-22（用户需求：删除 user_select 的 ``timeout`` 参数，改为「不操作
界面一分钟后才算超时」）：新增 ``idle_timeout`` 字段与 ``touch_activity()``
——工具路径以 ``IDLE_TIMEOUT_SECONDS``（60s）作为空闲窗口，弹窗每收到一次
界面操作就重置 ``deadline``；固定 deadline 的调用方 ``idle_timeout`` 为 0，
行为不变。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

__all__ = ["IDLE_TIMEOUT_SECONDS", "UserSelectState"]

#: 空闲超时窗口（秒）：user_select 弹窗 **1 分钟没有任何操作**（按键/滚动）
#: 才判定超时（回退 default_options）。
#: ★ 2026-10-22（用户需求：删除 user_select 的 timeout 参数，改为「不操作
#:   界面一分钟后才算超时」）：工具路径构造 state 时写入 ``idle_timeout``，
#:   弹窗每收到一次输入就经 ``touch_activity`` 把 ``deadline`` 重置为
#:   ``now + idle_timeout``；命令系统选择（CommandUiAdapter）等固定 deadline
#:   调用方 ``idle_timeout`` 保持 0，计时语义不变。
IDLE_TIMEOUT_SECONDS = 60.0


@dataclass
class UserSelectState:
    """用户选择弹窗状态（user_select 工具注入，UserSelectPopup 组件消费）。

    Attributes:
        visible: 弹窗是否显示。
        seq: 弹窗会话序号（每次打开递增，供组件重挂载）。
        title: 弹窗标题（问题文本）。
        header: 极短标签（≤12 显示宽；Claude AskUserQuestion 风格 chip，
            用于单问题标题 chip 与多问题 tab 标签）；空串时回退 title
            （渲染层截断）。★ 2026-10-22（用户需求：user_select TUI 与
            Claude 对齐）：新增可选字段，旧构造/旧测试默认空串零回归。
        options: 选项字符串列表。
        option_descriptions: 与 options 等长的说明列表。
        multi_select: 是否多选。
        default_options: 默认选项（超时/取消/非交互回退）。
        selected: 当前高亮索引（组件维护）。
        checked: 多选勾选索引列表（组件维护）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        idle_timeout: 空闲计时窗口（秒）；> 0 时弹窗每收到一次界面操作就
            经 ``touch_activity`` 把 ``deadline`` 顺延 ``now + idle_timeout``
            （「不操作界面 N 分钟后才算超时」）。0 = 固定 deadline 语义
            （命令系统选择等调用方，保持原有计时不变）。
        answered: 该问题是否已回答（提交前可重答）。
        done: 交互是否已整体提交/超时（终态锁定）。
        action: 结束方式（confirmed/cancel/timeout）。
        result: 选中的 options 子集（含自由输入时附加的自定义文本）。
        allow_custom: 是否允许用户输入选项外的自定义答案（工具路径为
            True；命令系统选择路径默认 False，保持 index 契约）。
        typing: 外部编辑器（Ctrl+G，默认 vim）当前是否正在运行（跨线程
            标志；组件写、工具轮询读）——为 True 时暂停空闲超时，编辑器
            返回后把运行时长补回 ``deadline``。★ 2026-10-22：超时改为
            「界面 1 分钟没有操作才超时」后，弹窗内正常打字/浏览由每次
            按键（``touch_activity``）重置倒计时；只有外部编辑器独占终端、
            界面收不到按键时才需要本标志暂停计时。
        custom: 本次回答的自由输入文本（空串 = 未使用自定义输入）。
        _final_lock: 终态原子写锁。
    """

    visible: bool = False
    seq: int = 0
    title: str = ""
    #: 极短标签（chip / tab 标签；空串回退 title）。
    header: str = ""
    options: list = field(default_factory=list)
    option_descriptions: list = field(default_factory=list)
    multi_select: bool = False
    default_options: list = field(default_factory=list)
    selected: int = 0
    checked: list = field(default_factory=list)
    deadline: float = 0.0
    #: 空闲计时窗口（秒，0 = 不启用；见 ``touch_activity``）。
    idle_timeout: float = 0.0
    answered: bool = False
    done: bool = False
    action: str = ""
    result: list = field(default_factory=list)
    # ★ 2026-10-21（用户需求：user_select 支持输入选项外内容并返回，
    #   输入时停止自动超时）：以下字段均有默认值——旧构造/旧测试/命令
    #   路径零回归。★ 2026-10-22：暂停标记 ``typing`` 只用于外部编辑器
    #   （见字段说明），界面内按键经 ``touch_activity`` 重置倒计时。
    allow_custom: bool = False
    typing: bool = False
    custom: str = ""
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def touch_activity(self, now: "float | None" = None) -> bool:
        """按空闲计时刷新超时截止（界面每发生一次操作调用一次）。

        ``idle_timeout`` > 0 且未进入终态（done）时，把 ``deadline`` 重置为
        ``now + idle_timeout``——「不操作界面 N 分钟后才算超时」：用户持续
        操作（按键 / 滚动 / 输入自定义答案）不会被打断。``idle_timeout``
        为 0 的调用方（命令系统选择等固定 deadline 场景）不改动
        ``deadline``，保持原有计时语义（零回归）。

        Args:
            now: 当前 ``time.monotonic()``（测试可注入；None 取实时值）。

        Returns:
            True 本次刷新生效；False 未启用空闲计时或已终态。
        """
        if self.done:
            return False
        try:
            idle = float(self.idle_timeout or 0.0)
        except (TypeError, ValueError):
            return False
        if idle <= 0:
            return False
        self.deadline = (time.monotonic() if now is None else float(now)) + idle
        return True

    def mark_answered(self, action: str, result: list, custom: str = "") -> bool:
        """标记该问题已回答（不置 done，提交前可反复覆盖）。

        Args:
            action: 回答方式（confirmed/cancel）。
            result: 选中的值列表（自定义输入时含用户文本）。
            custom: 本次回答的自由输入文本；默认空串（普通选择/取消——
                同时清空此前可能残留的自定义标记）。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.result = list(result)
            self.custom = str(custom or "")
            self.answered = True
            return True

    def try_set_final(self, action: str, result: list, custom: str = "") -> bool:
        """原子写入终态（first-write-wins）；done 已置位则返回 False。

        Args:
            action: 结束方式（confirmed/cancel/timeout）。
            result: 选中值列表（自定义输入时含用户文本）。
            custom: 本次回答的自由输入文本；默认空串。需要保留此前的
                自定义标记时（Submit 页统一提交已回答问题），调用方显式
                传入 ``getattr(state, "custom", "")``。
        """
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.result = list(result)
            self.custom = str(custom or "")
            self.done = True
            return True
