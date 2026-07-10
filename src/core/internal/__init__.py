"""core/internal — 内部实现模块（按领域分组）

子包结构：
- session/ — 会话消息管理、状态、持久化、压缩
- agent/ — 工具回调、子代理生成、输出捕获
- commands/ — 命令注册表
- shared/ — 缓存、沙盒历史

⚠️ 旧导入路径兼容：所有 from src.core.internal._xxx import yyy 仍然有效
"""
from __future__ import annotations

# 向后兼容：保持旧导入路径有效
from .session import *
from .agent import *
from .commands import *
from .shared import *
