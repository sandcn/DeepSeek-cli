"""颜色渐变基础设施（Layer 0 Core）。

提供 xterm-256 调色板查找表、十六进制颜色转换、线性渐变插值工具。

包含：
  - _build_xterm_palette(): 构建 xterm-256 调色板的 RGB 查找表
  - _XTERM_PALETTE: 预计算的 xterm 全色表（模块级常量）
  - hex_to_256(): 十六进制转最接近的 xterm-256 色号
  - gradient_step(): 带 @lru_cache 的线性插值单步色号
  - gradient_range(): 带 @lru_cache 的均匀分布色号列表

设计原则：
  - 纯函数，无 I/O 副作用
  - 使用 @lru_cache 避免重复计算热点渐变
  - 不导入任何 src.ui 或 src.tui 内部模块
"""
from tui_framework.core.gradient import *

# ── 显式导入以 _ 开头的私有符号（* 导入不包含 _ 前缀名称） ──
from tui_framework.core.gradient import _build_xterm_palette, _XTERM_PALETTE
