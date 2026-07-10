"""WebSocket 处理器包 — WebSocket 连接全生命周期

子模块职责：
  - sandbox:   沙盒变更检测与事件构建（bridge/display/routing 依赖）
  - utils:     消息索引重建（routing 依赖）
  - connection: WebSocket 连接建立与升级（routing 依赖）
  - commands:  Web 命令执行（routing 依赖）
  - edit:      消息编辑接口（routing 依赖）

共享常量已迁移到 src/webui/types.py，本 __init__.py 通过导入重新导出
以保持向后兼容。
"""

from __future__ import annotations

# ── 共享常量（从 types.py 导入，保持向后兼容） ──
from ..types import _MESSAGE_PREVIEW_LENGTH
