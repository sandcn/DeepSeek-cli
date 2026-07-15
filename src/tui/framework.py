"""
TUI 框架统一入口 — `Framework` 单例 + 公开 API。

提供：
  - Framework: 全局单例框架管理器（组件工厂 + 效果注册表 + 样式表 + 动画上下文）
  - create_component(): 创建组件并触发生命周期
  - frame_from_context(): 安全获取当前帧号的统一入口
  - get_animator(): 获取全局动画上下文实例

设计原则：
  - 单例管理：框架全局唯一，通过 Framework.get_default() 获取
  - 延迟导入：所有组件/效果模块在首次使用时才导入，避免循环依赖
  - 线程安全：单例创建和 API 调用均使用 threading.Lock 保护
  - 零 I/O：不涉及终端或文件 I/O，纯管理职责
"""
from tui_framework.framework import *

__all__: list[str] = [
    "Framework",
    "create_component",
    "frame_from_context",
    "get_animator",
]
