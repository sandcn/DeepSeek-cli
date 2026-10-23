"""渲染会话选项 — 框架层默认配置（应用可传入更宽的配置对象）。

``InkSession`` 只读以下 7 个字段（``render_interval`` / ``idle_render_interval`` /
``drain_lock_timeout`` / ``cmd_queue_maxsize`` / ``sys_stats_interval`` /
``max_recover_attempts`` / ``recover_delay``），历史上它直接 import 应用层的
``TuiConfig`` 并调用 ``TuiConfig.defaults()``——框架因此反向依赖应用配置模块。

本模块提供**同名字段、同默认值**的 ``InkOptions``，``InkSession`` 以它作默认值；
生产装配注入 ``TuiConfig``（字段超集，duck typing 即可，含 ``__post_init__``
的派生节流计算）——框架不再知道应用配置类的存在。

默认值约束：必须与 ``src/tui/_config.py`` 保持一致（有测试守护：
``tests/tui/ink/test_layering_contract.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["InkOptions", "DEFAULT_RENDER_HZ", "DEFAULT_RENDER_INTERVAL_SEC"]

#: 默认渲染帧率（Hz）——与 ``src/tui/_config.RENDER_HZ`` 对齐（测试守护）。
DEFAULT_RENDER_HZ: float = 60.0
#: 默认渲染帧间隔（秒）。
DEFAULT_RENDER_INTERVAL_SEC: float = 1.0 / DEFAULT_RENDER_HZ


@dataclass(frozen=True)
class InkOptions:
    """渲染会话默认选项（``TuiConfig`` 的框架侧最小等价物）。"""

    #: 渲染帧间隔（秒）。
    render_interval: float = DEFAULT_RENDER_INTERVAL_SEC
    #: 空闲降频后的帧间隔（秒）。
    idle_render_interval: float = 0.1
    #: 命令排空锁超时（秒），与帧间隔对齐。
    drain_lock_timeout: float = DEFAULT_RENDER_INTERVAL_SEC
    #: 命令队列最大容量。
    cmd_queue_maxsize: int = 10000
    #: 系统监控采集间隔（秒）。
    sys_stats_interval: float = 2.0
    #: 渲染线程崩溃后的最大重建次数。
    max_recover_attempts: int = 3
    #: 崩溃后重建等待（秒）。
    recover_delay: float = 0.5
