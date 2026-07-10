"""core/adapters — 端口适配器实现（依赖倒置）

适配器层是六边形架构的「适配器」部分，桥接核心层端口抽象与基础设施层实现。
此层允许导入 ui/、config/ 等基础设施模块（这是适配器层的职责）。

导出清单:
- DefaultOutputAdapter / get_default_output_port — 默认输出实现
- DisplayEventBusAdapter — EventPort 默认实现
- DefaultDisplayAdapter — 显示默认实现
- DefaultInterruptAdapter / MockInterruptAdapter — InterruptPort 默认实现
"""

from .output import DefaultOutputAdapter, get_default_output_port
from .events import DisplayEventBusAdapter
from .display import DefaultDisplayAdapter
from .interrupt import DefaultInterruptAdapter, MockInterruptAdapter


__all__ = [
    "DefaultOutputAdapter",
    "get_default_output_port",
    "DisplayEventBusAdapter",
    "DefaultDisplayAdapter",
    "DefaultInterruptAdapter",
    "MockInterruptAdapter",
]
