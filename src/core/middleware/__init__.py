"""Agent Pipeline 中间件包

提供 Agent 对话管线的可插拔中间件组件：
- AsyncObservabilityMiddleware — 可观测性（指标 + 追踪）
- AuditLogMiddleware          — 审计日志
- InterruptCheckMiddleware    — 中断信号检查
- ToolRegistryAdapter         — 工具注册表适配器
- StateMachineMiddleware      — 状态机状态自动转换
"""

from .state_machine import StateMachineMiddleware

__all__ = [
    "StateMachineMiddleware",
]
