"""chat_ui 线程本地重入保护模块。

Layer 0 — 仅依赖 threading，供 _error_handler.emit() 使用。
防止 emit → logger → emit 递归。

从 _state.py 分离（关注点分离原则：_state.py 聚焦全局实例引用管理，
本模块聚焦线程本地重入保护）。
"""

from __future__ import annotations

import threading

# ── 线程本地重入保护（防止 emit → logger → emit 递归） ──
_handler_reentrant = threading.local()
