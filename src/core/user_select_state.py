"""UserSelectState — 用户选择弹窗状态（核心层纯数据类）。

下沉自 ``tui.app._state_types``（零 TUI 运行时依赖），供 ``user_select``
工具（基础设施层）与表现层组件共享，消除工具对表现层的反向依赖。
表现层 ``tui.app._state_types`` 仅 re-export 兼容。

并发语义（2026-08-19）：多个并发 user_select 工具调用各自构造一个本状态
并 append 到模型并发队列；``try_set_final`` 为跨线程 first-write-wins
终态写入，``mark_answered`` 支持提交前重答。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

__all__ = ["UserSelectState"]


@dataclass
class UserSelectState:
    """用户选择弹窗状态（user_select 工具注入，UserSelectPopup 组件消费）。

    Attributes:
        visible: 弹窗是否显示。
        seq: 弹窗会话序号（每次打开递增，供组件重挂载）。
        title: 弹窗标题。
        options: 选项字符串列表。
        option_descriptions: 与 options 等长的说明列表。
        multi_select: 是否多选。
        default_options: 默认选项（超时/取消/非交互回退）。
        selected: 当前高亮索引（组件维护）。
        checked: 多选勾选索引列表（组件维护）。
        deadline: 超时截止（time.monotonic()）；0 表示无限等待。
        answered: 该问题是否已回答（提交前可重答）。
        done: 交互是否已整体提交/超时（终态锁定）。
        action: 结束方式（confirmed/cancel/timeout）。
        result: 选中的 options 子集。
        _final_lock: 终态原子写锁。
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
    _final_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def mark_answered(self, action: str, result: list) -> bool:
        """标记该问题已回答（不置 done，提交前可反复覆盖）。"""
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.result = list(result)
            self.answered = True
            return True

    def try_set_final(self, action: str, result: list) -> bool:
        """原子写入终态（first-write-wins）；done 已置位则返回 False。"""
        with self._final_lock:
            if self.done:
                return False
            self.action = action
            self.result = list(result)
            self.done = True
            return True
