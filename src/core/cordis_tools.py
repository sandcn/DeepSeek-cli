"""运行时自修改工具（cordis 工具族）名单 — 单一真源。

cordis 工具族允许 Agent 在运行时自省与改写当前进程的插件内核
（``cordis_inspect`` / ``cordis_define`` / ``cordis_run`` / ``cordis_stop`` /
``cordis_undefine``）。它等价于**进程内任意代码执行**：动态插件运行在同一
进程，可篡改治理层（工具策略、全局禁用集合、Agent 类型排除表、服务注册表），
从而自我解除约束——这是它被全局禁用的根本原因。

本名单是以下三处的**唯一**真源，避免同一事实多处硬编码而漂移：

- 「全局禁用工具」注册表与静态兜底（``src.tools.tool_policy``）；
- SubAgent 类型的工具排除集合（``src.core.agent_types``）；
- 清单中的全局禁用条目与工具元数据条目（``src.plugins.manifest``）。

零依赖模块：可被 ``src.core`` / ``src.tools`` / ``src.plugins`` 任一层安全
导入，不引入循环依赖。
"""

from __future__ import annotations

#: cordis 工具族全部工具名（声明顺序即展示顺序）
CORDIS_TOOLS: tuple[str, ...] = (
    "cordis_inspect",
    "cordis_define",
    "cordis_run",
    "cordis_stop",
    "cordis_undefine",
)

__all__ = ["CORDIS_TOOLS"]
