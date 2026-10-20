"""静态行区域定位（app 层）——把「聊天已提交历史」语义从 ink 框架剥离。

背景（架构解耦）：
  ``src/tui/ink/components.py`` 原内置 ``_find_committed_chat``，直接知道
  「聊天历史挂在 ``static-lines`` host 上」这一业务概念，且 ``InkSession``
  直接调用它计算输出历史的起始行——框架层因此依赖 app 层语义。

  现在：ink 只保留通用 ``static-lines`` 组件与前缀优化；**业务侧的
  「committed 历史在树中的位置」由本模块提供**，经
  ``InkSession.set_content_span_provider`` 注入。未注入时框架不计算内容
  起始行（输出历史回调安全跳过），不再内置聊天语义。

约束：本模块仅依赖 fiber 的公开字段（``is_host`` / ``type`` /
``layout_box`` / ``child`` / ``sibling`` / ``deleted``），不 import ink 内部。
"""

from __future__ import annotations

#: 聊天已提交历史的 host 类型（StaticLines 组件渲染的 host 标签）。
COMMITTED_HOST_TYPE = "static-lines"


def find_committed_span(root):
    """查找组件树中的静态行 host（已提交聊天历史），返回其 fiber 或 None。

    查找结果缓存于 root fiber（``_committed_chat_cache``）——fiber 按 key
    跨帧复用，静态行 host 位置稳定；缓存失效条件：fiber 被删除或类型不再
    匹配。``root._committed_chat_present`` 由布局期置位，未挂载时 O(1) 返回。

    Args:
        root: 根 fiber（InkSession 的 root）。

    Returns:
        Fiber | None: 静态行 host fiber。
    """
    if root is None or not getattr(root, "_committed_chat_present", False):
        return None
    cached = getattr(root, "_committed_chat_cache", None)
    if (
        cached is not None
        and getattr(cached, "is_host", False)
        and getattr(cached, "type", None) == COMMITTED_HOST_TYPE
        and not getattr(cached, "deleted", False)
    ):
        return cached
    stack = [root]
    found = None
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if getattr(f, "is_host", False) and getattr(f, "type", None) == COMMITTED_HOST_TYPE:
            found = f
            break
        child = getattr(f, "child", None)
        while child is not None:
            stack.append(child)
            child = getattr(child, "sibling", None)
    root._committed_chat_cache = found
    return found


def committed_content_span(root, model):
    """返回 ``(起始行, 已提交行数)``——供 InkSession 输出历史接线。

    业务语义（committed_lines 长度 + static-lines host 的布局 y）全部留在
    app 层；ink 框架只接收两个数字，不再读取 ``committed_lines``。

    Args:
        root: 根 fiber。
        model: AppModel（读取 ``committed_lines``）。

    Returns:
        tuple[int | None, int]: 起始屏幕行（None=未知）与已提交内容行数。
    """
    fiber = find_committed_span(root)
    start = None
    if fiber is not None and getattr(fiber, "layout_box", None) is not None:
        start = max(0, fiber.layout_box.y)
    count = len(getattr(model, "committed_lines", None) or [])
    return start, count


__all__ = ["COMMITTED_HOST_TYPE", "find_committed_span", "committed_content_span"]
