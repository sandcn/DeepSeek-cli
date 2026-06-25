"""FrameRenderer — 已废弃，由 VNode 内联渲染替代。

SubAgent 面板数据现通过 CmdSubagentSlotUpdate + TuiState.subagent_slots 
在 VNode 渲染路径（strategy.py）中内联输出。此文件保留空壳避免 import 报错。
"""
