"""编辑器集成 — 外部编辑器（兼容 shim）。

★ 2026-10-21（用户需求：user_select 自定义输入支持 Ctrl+G 用 vim 输入）：
实现已下沉到 ``src._editor``（L0），使 TUI 表现层可复用而无需反向依赖
app_loop。本模块保留原导入路径（``src.app_loop._editor.edit_in_vim_sync``）
兼容既有调用方/测试 monkeypatch 点。
"""

from __future__ import annotations

from src._editor import edit_in_vim_sync

__all__ = ["edit_in_vim_sync"]
