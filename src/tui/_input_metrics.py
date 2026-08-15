"""输入区布局度量 — 补全弹窗高度 / 反向搜索状态（模块边界优化，2026-08-05）。

背景（方向A：ink 依赖净化）：``ink/session.py`` 的 ``_position_cursor``
需要补全弹窗高度（``_completion_height``）与反向历史搜索激活判定
（``_is_search_active``）——原实现从 ``app/input_area.py`` 导入，造成
**底层 ink 框架反向依赖上层 app 组件**（分层倒置）。本模块将这些「输入区
布局度量」辅助下沉为独立顶层模块，供 ink 层（光标定位）与 app 层（输入区
渲染）共享——依赖方向统一为「上层 → 度量层」。

边界（与 _width.py 拆分同模式）：
  - ``app/input_area.py`` 保持 re-export（``from src.tui.app.input_area import
    _completion_height`` 等旧导入路径不变，测试/外部调用面兼容）。
  - 单一真源：``app.input_area._completion_height is _input_metrics._completion_height``。
  - 依赖方向：本模块 → ``_input_layout``（_wrap_by_width，纯布局函数层；
    2026-08-05 循环依赖消除后不再经 ``_input`` 输入门面）/ ``_screen``
    （TerminalWidthCache 函数内惰性 import）；**不得 import ``app.*`` /
    ``ink.*`` / ``_input`` 门面**（防分层倒置/循环）。

移动说明：原 ``app/input_area.py`` 中 ``_LOCKED_PAD_LIMIT`` /
``_desc_column_width`` / ``_completion_item_rows`` / ``_completion_height`` /
``_is_search_active`` 定义逐行迁移至此（零逻辑改动）。``_completion_height``
对 ``completion.locked_height`` 的写回副作用保留（高度锁定语义）。
"""

from __future__ import annotations

#: _wrap_by_width 单一真源在 ``_input_layout``（输入区渲染/度量共享，防双实现漂移；
#:   2026-08-05 循环依赖消除后从纯布局函数层导入——原从 ``_input`` 导入依赖输入门面）。
from src.tui._input_layout import _wrap_by_width


def _desc_column_width(width: int) -> int:
    """分栏说明模式右栏宽度（user_select：说明在选项右侧显示）。

    取终端宽度 1/3，钳制到 [8, 40]，且给左栏选项至少预留 12 列——
    极窄终端（width<20）下右栏同步缩小，避免左栏被挤压溢出。
    极窄分支：宽度下限钳制到可用宽度（≤ width-1，左栏至少 1 列）——
    修复前 ``max(8, ...)`` 在 width<20 时右栏恒 8 超过终端总宽，
    分栏行总宽溢出终端。
    """
    if int(width) < 20:
        # P3（2026-08-07）：极窄终端右栏超宽修复——width <= 1 时原
        # ``max(1, ...)`` 返回 1（右栏等于终端总宽甚至超宽）。下限钳制到
        # ``max(0, ...)``：右栏宽度不超可用宽度（≤ width-1），width<=1 时
        # 右栏为 0（左栏占满，不溢出）。
        return max(0, min(int(width) - 1, int(width) // 2))
    return max(8, min(int(width) // 3, 40, int(width) - 12))


#: 补全弹窗高度锁定的最大允许补白行数——items 减少时弹窗高度保持（防闪烁），
#: 但补白超过此值（items 大幅减少）时允许缩小（避免弹窗底部大片空白）。
_LOCKED_PAD_LIMIT = 3


def _completion_item_rows() -> int:
    """补全弹窗候选项最大行数（终端高度约束，防超屏）。

    预留顶部标题 1 + 弹窗标题 1 + 弹窗提示行 1 + 状态栏 1 + 输入区分隔线 1
    + 输入行 1 + 输入下分隔线 1 + 时间戳 1 + 模式行 1 ≈ 9 行（2026-08-14
    新增模式行后底部固定占用 +1）；候选项 + 说明行数限制在
    ``max(6, h - 11)``。正常补全（≤20 项）不受影响；极长说明 / user_select
    大量选项时弹窗不超屏。

    ★ 性能（方向4）：终端高度经 ``TerminalWidthCache`` 读取——修复前每次
    调用直接 ``_get_terminal_size()``（fcntl.ioctl），补全弹窗可见时
    ``_completion_height`` 在 ``_measure`` 与 ``_position_cursor`` 每帧各
    调一次 → 每帧 2 次 ioctl。TTL 缓存避免重复系统调用。

    Returns:
        候选项（含说明）最大渲染行数。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, h - 11)
    except Exception:
        return 12


def _completion_height(completion, width=None) -> int:
    """补全弹窗高度（标题 + 候选项 + 提示行）。

    分栏说明模式（split_desc 且存在说明）下，高度取选项数与当前选中项说明
    换行行数的较大值——说明可多行，弹窗随说明行数增高。

    方向4（超屏防护）：候选项行数经 ``_completion_item_rows`` 限制——大量
    选项 / 超长说明时弹窗不超终端高度（渲染截断与高度一致，光标定位正确）。

    ★ 高度锁定（补全弹窗闪烁修复 + 补白上限）：弹窗打开期间优先返回
    ``locked_height``（items 小幅减少时**只增不减**）——打字时 items 数量变化
    （5→2→1）若高度随之下调，input_area 高度变化触发文档缩短重排（物理缓冲
    无 delete-line → 漂移 → 全量重写 → 视觉闪烁）；锁定后 items 小幅减少时
    高度保持（底部短暂留白，≤ ``_LOCKED_PAD_LIMIT`` 行），doc 高度不变 →
    等高 diff 只重写弹窗行（不闪）；items 增加时高度跟随（增高，增长滚动
    自然）。
    但补白超过 ``_LOCKED_PAD_LIMIT``（items **大幅**减少，如 20→1 项）时允许
    缩小到当前 need——避免弹窗底部渲染十余行空白（视觉异常；一次 diff 重写
    换取无空白更优）。弹窗关闭（hide_completions）重置 locked_height=0。

    Args:
        completion: CompletionState 或 None。
        width: 终端宽度（分栏说明模式需要）。

    Returns:
        弹窗高度（行数）；弹窗不可见/无 items 时 0。
    """
    if completion is None or not completion.visible or not completion.items:
        return 0
    n = len(completion.items)
    descs = completion.descriptions or []
    if not (getattr(completion, "split_desc", False) and descs) or width is None:
        need = min(n, _completion_item_rows()) + 2
    else:
        desc_w = _desc_column_width(width)
        # ★ M4/M5（2026-08-15）：selected 钳制统一 + 类型防御——与
        #   ``_popup_builder._build_popup_lines`` 的 ``desc_sel`` 同源（绘制
        #   语义）：① M5：selected 非 int（None/str 外部注入）时 ``int()``
        #   归一化失败回退 0，不抛 TypeError（与 ``_popup_builder`` 一致）；
        #   ② M4：钳制统一按 ``min(len(descs)-1, len(items)-1)``——descs/
        #   items 长度不齐（异常数据）时高度测量与绘制 desc_sel 同源，
        #   弹窗不截断/不底部空白。分栏分支前置条件 items/descs 均非空
        #   （函数开头已判），``len(completion.items)-1`` / ``len(descs)-1``
        #   安全。
        try:
            sel = max(0, min(int(completion.selected), len(descs) - 1, len(completion.items) - 1))
        except (TypeError, ValueError):
            sel = 0
        desc_lines = _wrap_by_width(descs[sel] or "", desc_w)
        need = min(max(n, len(desc_lines)), _completion_item_rows()) + 2
    # 高度锁定（补全弹窗闪烁修复 + 补白上限）：
    #   - items 增加 → 高度跟随（增长滚动自然）。
    #   - items 小幅减少（need 与 locked_height 差距 <= _LOCKED_PAD_LIMIT）
    #     → 高度保持（底部补白 ≤ 上限），doc 高度不变 → 等高 diff 只重写
    #     弹窗行（消除打字时 items 数量变化引发的全量重写闪烁）。
    #   - items 大幅减少（差距 > _LOCKED_PAD_LIMIT，如 20→1 项）→ 允许缩小
    #     到 need——避免弹窗底部渲染十余行空白（视觉异常；一次 diff 重写
    #     换取无空白更优）。
    locked = getattr(completion, "locked_height", 0)
    if need > locked:
        completion.locked_height = need
    elif locked - need > _LOCKED_PAD_LIMIT:
        completion.locked_height = need
    return completion.locked_height


def _is_search_active(search) -> bool:
    """反向历史搜索是否激活（history_search 非 None 且 active，方向D 步骤14）。"""
    return search is not None and bool(getattr(search, "active", False))


__all__ = [
    "_LOCKED_PAD_LIMIT",
    "_desc_column_width",
    "_completion_item_rows",
    "_completion_height",
    "_is_search_active",
]
