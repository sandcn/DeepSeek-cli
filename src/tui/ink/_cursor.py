"""输入光标定位 — 计算输入区光标的视觉行列并放置（模块边界优化 2026-08-05）。

从 ``ink/session.py`` 提取（方向B：InkSession 职责拆分）——``_position_cursor``
/``_find_input_fiber`` 的光标定位计算独立成纯函数模块：session 的渲染循环只
负责「找 fiber → 调 position_cursor」，布局/坐标计算独立可测。

依赖方向（与 ink 框架约束一致）：
  - ``_input_metrics``（补全弹窗高度/反向搜索状态）— 度量层
  - ``_input_layout``（输入布局/光标可视位置）— 纯函数布局层
  本模块不得 import ``_input`` 门面 / ``app.*``（防 ink → 输入门面/app 反向
  依赖；2026-08-05 重构：布局函数经 ``_input_layout`` 直接引用，不再绕经
  ``_input`` re-export）。
"""

from __future__ import annotations

import logging

from src.tui._input_layout import _compute_input_layout, _cursor_visual_from_layout
from src.tui._input_metrics import _completion_height, _is_search_active
from ._ansi_utils import visual_width

_logger = logging.getLogger(__name__)


def find_input_fiber(root_fiber):
    """在 host 树中查找输入区 fiber（标准组件 dataInputArea 容器或旧 host）。

    ★ 标准 React Ink 组件化：InputArea 标准组件返回 Column（props 含
    ``dataInputArea=True`` 标记 + 透传输入区状态）——查找条件为
    ``props.dataInputArea`` 或旧 ``type == "input-area"``（兼容）。

    ★ P3-1 修复（review 方向）：显式栈迭代替代递归（与 ``_find_committed_chat``
    一致）——修复前 ``walk(f2.child)`` 递归无深度限制，深层嵌套树（1000+
    层）触发 RecursionError（Python 默认递归上限 ~1000）。显式栈保持原 DFS
    顺序（child 深度优先、sibling 从左到右）：sibling 链先压栈（栈底）、
    child 后压栈（栈顶先处理）。

    ★ P3-21 修复（2026-08-19，长 sibling 链指数爆炸）：原实现**每 pop 一个
    节点都将其完整 sibling 链重新压栈**——线性兄弟链（如轨迹视图检查器的
    N 个内容行）上，后续节点被每个前缀节点反复压入：节点被压次数 = 2^(位置)
    → O(2^N) 指数时间（21 行链 ≈ 200 万次压栈、23 行链 ≈ 800 万次，渲染
    线程卡死数秒）。修复：**压栈时 ``pushed`` 集合去重**——每个节点只压栈
    一次（sibling 链遍历跳过已压节点），总复杂度 O(N + Σ链长)。DFS 语义
    （child 深度优先、sibling 从左到右）与无环树结果完全一致；有环/共享
    节点（异常树）由去重天然防死循环。
    """
    from .fiber import Fiber

    pushed: set = set()
    stack = [root_fiber]
    pushed.add(id(root_fiber))
    while stack:
        f = stack.pop()
        if f.is_host and (
            f.type == "input-area"
            or bool(f.props.get("dataInputArea"))
        ):
            return f
        # sibling 链逆序压入（LIFO 弹出恢复从左到右），child 最后压入
        # （栈顶先处理——原递归 child 深度优先语义）。
        # ★ P3-21：已压栈（pushed）的 sibling/child 跳过——线性兄弟链上
        #   后续节点不被每个前缀节点重复压入（原实现 O(2^N) 指数）；
        #   sibling 链遍历遇已压节点即停——异常树（sibling 环）遍历本身
        #   不死循环（正常树中同层链元素一次性压入，遇 pushed 即链尾）。
        sibs = []
        s = f.sibling
        while s is not None and id(s) not in pushed:
            pushed.add(id(s))
            sibs.append(s)
            s = s.sibling
        for i in range(len(sibs) - 1, -1, -1):
            stack.append(sibs[i])
        if f.child is not None and id(f.child) not in pushed:
            pushed.add(id(f.child))
            stack.append(f.child)
    return None


def position_cursor(renderer, width: int, fiber) -> None:
    """计算输入光标位置并放置（从 session._position_cursor 提取的纯计算）。

    渲染后定位输入光标（从文档底部相对移动）。依赖 fiber 的布局盒
    （``layout_box``）与 props（text/cursor_pos/prompt/completion/
    history_search）；``width`` 为终端宽度（光标列右边界 clamp 用）。

    异常语义（与原 session._position_cursor 一致）：
      - popup_height 计算失败 → 回退 0（记 debug），不中断光标定位；
      - place_cursor 失败由调用方（session._position_cursor）兜底记 debug。
    """
    box = fiber.layout_box
    if box is None:
        return
    text = str(fiber.props.get("text", ""))
    # ★ P1（review 2026-08-22）：cursor_pos 显式为 None/非数字时 int() 抛
    #   TypeError，导致本函数整段失败、光标每帧无法定位（静默退化）。与
    #   下方 popup_height 的防御一致，回退 -1 并记 debug。
    try:
        cursor_pos = int(fiber.props.get("cursor_pos", -1))
    except (TypeError, ValueError):
        cursor_pos = -1
        _logger.debug("position_cursor: cursor_pos 非法，回退 -1", exc_info=True)
    prompt = str(fiber.props.get("prompt", "> "))
    completion = fiber.props.get("completion")
    # 方向1 步骤4（缺失 completion 属性守卫）：popup_height 与 row 计算
    # 纳入 try/except——completion 缺 ``items`` 等属性时抛 AttributeError
    # 中断渲染；修复后缺属性回退 popup_height=0（记 debug），place_cursor
    # 调用保持独立 try。
    try:
        popup_height = _completion_height(completion, box.w)
    except Exception:
        popup_height = 0
        _logger.debug(
            "position_cursor: completion 属性缺失，回退 popup_height=0",
            exc_info=True,
        )
    max_input = max(1, box.w - visual_width(prompt))
    # ★ PERF-1：优先复用换行布局缓存（每帧至多 1 次换行；缓存写回
    #   dataInputArea 容器 fiber——InputArea 组件内部 _build_lines 写的是
    #   临时 fiber（_input_elements SimpleNamespace），此处是真实 Column
    #   fiber，二者分离；写回后同 text/max_input 帧零重复换行计算）。
    #   未命中时经 _compute_input_layout 计算并写回。
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        _, wrapped_by_logical = cached[1]
    else:
        rows, wrapped_by_logical = _compute_input_layout(text, max_input)
        fiber._input_layout_cache = ((text, max_input), (rows, wrapped_by_logical))
    vis_row, vis_col = _cursor_visual_from_layout(text, cursor_pos, wrapped_by_logical)
    # 输入文本起始行 = box.y + popup_height + 1（上分隔线之后）
    row = box.y + popup_height + 1 + vis_row + 1
    # ★ P0-1：反向历史搜索激活时 input_area 在输入文本行前追加 1 行
    #   (reverse-i-search) 覆盖行（_build_lines 已正确增行）——光标行偏移须
    #   同步计入（与 input_area._build_lines 共享 _is_search_active
    #   高度辅助，保持一致）。
    if _is_search_active(fiber.props.get("history_search")):
        row += 1
    # ★ 方向6（光标列右边界 clamp）：超宽输入（vis_col 超终端宽度）时
    #   光标列钳制到终端宽度（修复前 col 越界溢出导致光标定位异常）。
    col = min(box.x + visual_width(prompt) + vis_col + 1, width)
    renderer.place_cursor(row, col)


__all__ = ["find_input_fiber", "position_cursor"]
