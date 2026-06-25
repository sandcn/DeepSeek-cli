"""TUI 层级渲染系统 — 类型定义。

Layer 枚举定义渲染层级，数值越大越靠前（覆盖低层）。
预留间隙方便未来插入中间层级。
"""

from enum import IntEnum


class Layer(IntEnum):
    """渲染层级枚举。
    
    数值越大越靠前（覆盖低层）。预留间隙 (10/20/30) 方便插入中间层级。
    """
    BACKGROUND = 0    # 背景层（暂未使用）
    CONTENT = 10      # 主内容层（默认层级）
    OVERLAY = 20      # 覆盖层（通知、错误、弹出层）


# 类型别名：层级 buffer = 行列表，None 表示该位置透明（允许下层穿透）
LayerBuffer = list[str | None]

# 默认层级
DEFAULT_LAYER: Layer = Layer.CONTENT

# 最大层级数
MAX_LAYERS: int = 8
