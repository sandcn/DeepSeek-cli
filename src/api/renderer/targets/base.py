"""targets.base — RenderTarget 抽象基类：统一渲染目标接口。

所有渲染目标（终端/Web/移动端）实现此接口，
VNodePatcher 和 IncrementalVNodeRenderer 通过此接口消费渲染输出。

设计原则：
  - 接口最小化：只暴露渲染目标必需的操作
  - 目标无关：不假设终端/浏览器/原生控件的特性
  - 可组合：多个 RenderTarget 可通过 CompositeRenderTarget 组合
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import field
from src._compat import dataclass
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════
# 渲染上下文
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class RenderTargetContext:
    """渲染目标上下文——跨 RenderTarget 调用传递的共享状态。

    Attributes:
        indent: 当前缩进级别
        depth: 嵌套深度（引用/列表嵌套）
        typing_speed: 打字机速度（0 为即时输出）
        extra: 目标特定的额外参数
    """
    indent: int = 0
    depth: int = 0
    typing_speed: int = 0
    extra: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# RenderTarget 抽象基类
# ═══════════════════════════════════════════════════════════

class RenderTarget(ABC):
    """渲染目标抽象基类。

    定义所有渲染目标必须实现的最小接口。
    每个方法对应一种渲染输出操作。

    注意：当前主渲染路径（IncrementalRenderer）不直接使用此接口，
    而是通过 RenderEngine + OutputAdapter 进行渲染。
    此接口保留供外部扩展及多目标组合场景使用。

    子类必须实现：
      - write: 输出渲染对象
      - write_line: 输出纯文本行
      - clear_line: 清除当前行
      - width 属性: 渲染目标宽度
    """

    @abstractmethod
    def write(self, renderable: Any) -> None:
        """输出一个渲染对象。

        Args:
            renderable: 目标特定的渲染对象
                        （终端为 Rich renderable，Web 为 HTML 字符串）
        """

    @abstractmethod
    def write_line(self, text: str = "") -> None:
        """输出一行纯文本（自动换行）。"""

    @abstractmethod
    def clear_line(self) -> None:
        """清除当前行内容（用于行覆盖/更新）。"""

    @property
    @abstractmethod
    def width(self) -> int:
        """渲染目标的可用宽度（字符数或像素）。"""

    # ── 可选实现（有默认行为） ──────────────────────────

    def write_typing(self, renderable: Any, speed: int = 80,
                     end: str = "\n") -> None:
        """打字机效果输出（默认行为 = 直接 write）。

        Args:
            renderable: 渲染对象
            speed: 字符/秒（0=即时）
            end: 尾部追加字符
        """
        self.write(renderable)

    def write_raw(self, text: str) -> None:
        """快速输出纯文本（跳过格式处理）。"""
        self.write(text)

    def render_inline(self, text: str) -> Any:
        """渲染内联 Markdown 为目标原生格式（可选覆写）。

        默认返回原始文本，子类可覆写以实现内联格式渲染。

        Args:
            text: 含内联 Markdown 的文本

        Returns:
            目标特定的渲染对象
        """
        return text

    def flush(self) -> None:
        """刷出缓冲区（可选覆写）。终端不需要，Web/文件需要。"""

    def close(self) -> None:
        """关闭渲染目标（可选覆写）。释放资源。"""

    def get_output_adapter(self) -> Optional["OutputAdapter"]:
        """获取内部 OutputAdapter（可选覆写）。

        默认返回 None。如果渲染目标内部包装了 OutputAdapter（如
        TerminalRenderTarget），应覆写此方法返回它，避免 VNodePatcher
        等消费者使用 hasattr 探测私有属性破坏 LSP。

        Returns:
            OutputAdapter 实例，或 None（如果目标没有 OutputAdapter）
        """
        return None

    # ── 辅助方法 ────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ═══════════════════════════════════════════════════════════
# CompositeRenderTarget — 多目标组合
# ═══════════════════════════════════════════════════════════

class CompositeRenderTarget(RenderTarget):
    """组合渲染目标——同时输出到多个 RenderTarget。

    用于同时渲染到终端和文件、终端和 Web 等场景。

    用法：
      target = CompositeRenderTarget([term_target, web_target])
      target.write("Hello")  # 同时输出到两个目标
    """

    def __init__(self, targets: list[RenderTarget]):
        self._targets = list(targets)

    def add_target(self, target: RenderTarget) -> None:
        """动态添加渲染目标。"""
        self._targets.append(target)

    def write(self, renderable: Any) -> None:
        for t in self._targets:
            t.write(renderable)

    def write_line(self, text: str = "") -> None:
        for t in self._targets:
            t.write_line(text)

    def clear_line(self) -> None:
        for t in self._targets:
            t.clear_line()

    def write_typing(self, renderable: Any, speed: int = 80,
                     end: str = "\n") -> None:
        for t in self._targets:
            t.write_typing(renderable, speed, end)

    def write_raw(self, text: str) -> None:
        for t in self._targets:
            t.write_raw(text)

    @property
    def width(self) -> int:
        return self._targets[0].width if self._targets else 80

    def flush(self) -> None:
        for t in self._targets:
            t.flush()

    def close(self) -> None:
        for t in self._targets:
            t.close()
