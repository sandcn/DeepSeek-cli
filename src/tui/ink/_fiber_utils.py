"""fiber 树查找工具 — 框架通用遍历（不依赖应用布局）。

``find_input_fiber`` 按 **props 契约**（``dataInputArea=True`` 或历史
``type == "input-area"``）定位输入区容器 fiber。该约定与 React Ink 的 props
标记同级，属于框架可理解的「组件契约」，因此保留在框架内；具体输入区**长
什么样 / 光标落在哪一行**属于应用知识，见 ``src/tui/_input_cursor.py``（经
``InkSession(cursor_provider=...)`` 注入）。
"""

from __future__ import annotations

from .markers import is_input_area_fiber

__all__ = ["find_input_fiber"]


def find_input_fiber(root_fiber):
    """在 host 树中查找输入区 fiber（``dataInputArea`` 标记或旧 host 类型）。

    实现要点（均为历史回归修复，勿简化）：
      - **显式栈迭代**替代递归：深层嵌套树（1000+ 层）不会 RecursionError；
      - 压栈时用 ``pushed`` 集合去重：线性 sibling 链上原实现会把后续节点
        反复压栈（O(2^N) 指数），去重后 O(N + Σ链长)，DFS 语义不变；
      - 过滤已删除 fiber（``deleted``）：避免返回失效节点导致光标定位错位。

    Returns:
        命中的 host fiber；未找到返回 None。
    """
    pushed: set = set()
    stack = [root_fiber]
    pushed.add(id(root_fiber))
    while stack:
        fiber = stack.pop()
        if getattr(fiber, "deleted", False):
            continue
        if is_input_area_fiber(fiber):
            return fiber
        siblings = []
        sibling = fiber.sibling
        while sibling is not None and id(sibling) not in pushed:
            pushed.add(id(sibling))
            siblings.append(sibling)
            sibling = sibling.sibling
        for index in range(len(siblings) - 1, -1, -1):
            stack.append(siblings[index])
        if fiber.child is not None and id(fiber.child) not in pushed:
            pushed.add(id(fiber.child))
            stack.append(fiber.child)
    return None
