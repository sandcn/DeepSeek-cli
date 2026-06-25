"""SubAgent 面板帧渲染器 — 已废弃，由 VNode 内联渲染替代。

SubAgent 面板信息现通过 CmdSubagentSlotUpdate + TuiState.subagent_slots 
在 VNode 渲染路径中内联输出。此文件保留空壳避免 import 报错。
"""

from __future__ import annotations
