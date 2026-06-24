"""Static 组件 — 累加式终端输出。

提供 <Static items={[...]}> 组件，特性：
  - 已渲染的 item 不会被后续渲染覆盖
  - 新 item 追加到已有输出之后
  - key-based 追踪（通过 (index, hash(str(item))) 组合判断是否已渲染）
  - 默认最大缓存 1000 条，超出时自动丢弃最旧项

继承自 TuiComponent，独立于现有组件系统。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable

from .._components import TuiComponent
from .._styled import StyledText


class Static(TuiComponent):
    """Static 组件 — 累加渲染。

    每次 render 时比较 items 与上次已渲染的 keys，
    仅渲染尚未输出的新 item，追加到已有输出末尾。
    已渲染的 item 不会被后续更新覆盖。

    使用场景：
      - 日志列表：每次新增一行日志，历史日志保持不动
      - 已完成任务列表：新任务追加，旧任务不变
      - 历史消息展示

    属性：
        items: 要累加渲染的列表项。
        children: 渲染函数 (item, index) -> TuiComponent。
        max_cache: 最大缓存条目数（默认 1000，超出时丢弃最旧项）。

    示例：
        static = Static(
            items=log_lines,
            children=lambda line, idx: Text(line),
        )
    """

    def __init__(
        self,
        items: list[Any] | None = None,
        children: Callable[[Any, int], TuiComponent] | None = None,
        max_cache: int = 1000,
    ):
        """初始化 Static 组件。

        Args:
            items: 要累加渲染的列表项。
            children: 渲染函数，接收 (item, index)，返回 TuiComponent 子类实例。
            max_cache: 最大缓存条目数，默认 1000。
        """
        super().__init__()
        self.items: list[Any] = list(items) if items is not None else []
        self._render_fn: Callable[[Any, int], TuiComponent] | None = children
        self.max_cache: int = max_cache

        # ── 渲染追踪状态 ──
        # 已渲染 item 的 key 集合，用于判断是否跳过
        self._rendered_keys: set[tuple] = set()
        # 已渲染 key 的顺序队列（与 _rendered_output 一一对应）
        self._rendered_key_order: deque[tuple] = deque()
        # 已渲染的输出行队列（按渲染顺序，使用 deque 提升 pop(0) 性能）
        self._rendered_output: deque[str] = deque()

    @property
    def children(self) -> list[TuiComponent]:
        """Static 不使用传统 children 列表。

        Static 的 children 参数是渲染函数而非组件列表，
        因此覆写此属性返回空列表以避免误导。
        """
        return []

    def _make_key(self, item: Any, index: int) -> tuple:
        """生成 item 的唯一追踪 key。

        使用 (index, hash(str(item))) 组合：
          - index 确保位置稳定，即使对象被回收也能复用位置
          - hash(str(item)) 检测同一位置的内容变化，变化时重新渲染

        Args:
            item: 列表项。
            index: 在 items 中的索引。

        Returns:
            用于追踪的 (index, hash) 元组。
        """
        return (index, hash(str(item)))

    def render(self) -> str | StyledText:
        """渲染新 item（追加到已缓存输出之后）。

        遍历 items，对每个未渲染的 item：
          1. 生成追踪 key
          2. 若 key 已存在 → 跳过（已渲染且内容未变）
          3. 若 key 不存在 → 调用 _render_fn(item, index) 获取组件
          4. 渲染组件获取输出文本
          5. 追加到 _rendered_output
          6. 将 key 加入 _rendered_keys
          7. 若超出 max_cache → 丢弃最旧项

        Returns:
            全部已渲染内容的拼接字符串（用换行符连接）。
            无渲染内容时返回空字符串。
        """
        # 无渲染函数时直接返回已缓存内容
        if self._render_fn is None:
            return "\n".join(self._rendered_output) if self._rendered_output else ""

        for index, item in enumerate(self.items):
            key = self._make_key(item, index)

            # 已渲染且内容未变 → 跳过
            if key in self._rendered_keys:
                continue

            # 渲染新 item
            component = self._render_fn(item, index)
            output = component.render()

            # 统一转为字符串存储
            if isinstance(output, StyledText):
                output_str = str(output)
            else:
                output_str = output

            self._rendered_output.append(output_str)
            self._rendered_keys.add(key)
            self._rendered_key_order.append(key)

            # 超出 max_cache 时丢弃最旧项
            while len(self._rendered_output) > self.max_cache:
                self._rendered_output.popleft()
                evicted_key = self._rendered_key_order.popleft()
                self._rendered_keys.discard(evicted_key)

        if not self._rendered_output:
            return ""

        return "\n".join(self._rendered_output)

    # ── 辅助方法 ──────────────────────────────────────

    def clear(self) -> None:
        """清空所有已渲染缓存。

        调用后下次 render 将重新渲染所有 items。
        """
        self._rendered_keys.clear()
        self._rendered_key_order.clear()
        self._rendered_output.clear()

    def append_item(self, item: Any) -> None:
        """手动追加单个 item（不触发渲染）。

        用于在非 render 周期内向 items 追加数据。
        对应的渲染将在下次 render() 调用时发生。

        Args:
            item: 要追加的列表项。
        """
        self.items.append(item)
