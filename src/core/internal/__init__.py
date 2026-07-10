"""core/internal — 内部实现模块（按领域分组）

子包结构：
- session/ — 会话消息管理、状态、持久化、压缩
- agent/ — 工具回调、子代理生成、输出捕获
- commands/ — 命令注册表
- shared/ — 缓存、沙盒历史

【架构】此目录为纯实现细节。外部代码统一通过 src.core.internal 子包入口导入。
"""
from __future__ import annotations

from .session import *
from .agent import *
from .commands import *
from .shared import *
