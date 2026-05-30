"""types — TokenType 枚举 + Token 数据类 + RenderContext。

集中存放渲染管道共享的类型定义，消除跨模块的字符串耦合和状态碎片。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import field
from src._compat import dataclass
from enum import Enum, auto


# ═══════════════════════════════════════════════════════════
# TokenType 枚举（替代字符串 type）
# ═══════════════════════════════════════════════════════════

class TokenType(Enum):
    """所有 Markdown Token 类型的枚举。每个值对应 RenderEngine 中的一个 handler。"""
    PARAGRAPH = auto()
    EMPTY_LINE = auto()
    HEADING = auto()
    HR = auto()
    BLOCKQUOTE = auto()
    LIST_ITEM = auto()
    DEFINITION_ITEM = auto()

    # 代码块
    CODE_FENCE_OPEN = auto()
    CODE_LINE = auto()
    CODE_FENCE_CLOSE = auto()
    CODE_BLOCK = auto()  # 整块代码（由 CodeBlockBatcher 管道过滤器生成）

    # 数学块
    MATH_BLOCK_OPEN = auto()
    MATH_LINE = auto()
    MATH_BLOCK_CLOSE = auto()

    # Mermaid 图表
    MERMAID_BLOCK_OPEN = auto()
    MERMAID_LINE = auto()
    MERMAID_BLOCK_CLOSE = auto()

    # Details 折叠块
    DETAILS_OPEN = auto()
    DETAILS_LINE = auto()
    DETAILS_CLOSE = auto()

    # Admonition 告示
    ADMONITION_OPEN = auto()
    ADMONITION_LINE = auto()
    ADMONITION_CLOSE = auto()

    # HTML 块级元素
    HTML_BLOCK_OPEN = auto()
    HTML_BLOCK_LINE = auto()
    HTML_BLOCK_CLOSE = auto()

    # 引用块（成对 Token，用于递归解析）
    BLOCKQUOTE_OPEN = auto()
    BLOCKQUOTE_LINE = auto()
    BLOCKQUOTE_CLOSE = auto()

    # 硬换行（替代 content 中嵌入的 <br>）
    LINE_BREAK = auto()

    # 表格
    TABLE = auto()

    # Fenced Div（::: 自定义容器块）
    FENCED_DIV_OPEN = auto()
    FENCED_DIV_LINE = auto()
    FENCED_DIV_CLOSE = auto()

    # [TOC] 占位符
    TOC_MARKER = auto()


# ═══════════════════════════════════════════════════════════
# Token 数据类（从 parser.py 迁移至此）
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class Token:
    """解析产出的结构化 Token。type 字段使用 TokenType 枚举。"""
    type: TokenType
    content: str = ""
    meta: dict = field(default_factory=dict)

    def __repr__(self):
        truncated = (self.content[:37] + "...") if len(self.content) > 40 else self.content
        return f"Token({self.type.name}, {truncated!r}, meta={self.meta})"


# ═══════════════════════════════════════════════════════════
# RenderContext — 跨 Parser / Engine 的共享状态容器
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class RenderContext:
    """Parser 和 Engine 共享的渲染上下文。

    集中管理引用链接、脚注等跨组件状态，消除两端各自维护同一份数据的碎片问题。
    """
    ref_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    """参考式链接映射 [ref_id] → (url, title)"""

    fn_map: dict[str, str] = field(default_factory=dict)
    """脚注定义映射 [^ref_id] → content"""

    fn_order: list[str] = field(default_factory=list)
    """脚注引用出现顺序（用于按序输出）"""

    abbr_map: dict[str, str] = field(default_factory=dict)
    """缩写定义映射 [abbr] → full_text，来自语法 `*[ABBR]: Full Text`。"""

    fn_counter: int = 0
    """脚注引用序号计数器"""

    metrics: Counter = field(default_factory=Counter)
    """性能/事件指标收集器"""

    start_time: float = 0.0
    """渲染开始时间戳（time.monotonic），用于统计渲染耗时"""

    token_count: int = 0
    """已处理的 Token 总数，用于渲染统计摘要"""

    heading_numbering: bool = False
    """是否启用标题自动编号。由 IncrementalRenderer 根据配置设置。"""

    heading_counters: dict[int, int] = field(default_factory=dict)
    """标题计数器，{level: count}，用于标题自动编号。"""

    toc: list | None = None
    """TOC（目录）条目列表。由 HeadingAnchorFilter 收集，RenderEngine.emit_toc() 使用。"""

    def fn_next_number(self) -> int:
        """获取下一个脚注编号并递增计数器。"""
        self.fn_counter += 1
        return self.fn_counter

    def __repr__(self):
        return (
            f"RenderContext(ref_map={len(self.ref_map)} entries, "
            f"fn_order={self.fn_order}, "
            f"fn_counter={self.fn_counter})"
        )

    def __str__(self):
        return self.__repr__()
