"""状态栏 token 快照惰性加载共享模块 — 仅依赖标准库。

消除 bottom_bar/status.py 和 status_bar_widget.py 中的重复实现。
本模块不依赖 src/tui/widgets/bottom_bar/ 下的任何模块，避免循环导入。

迁移说明（2026-07-29 TUI 重构）：
  - 从 src/tui/widgets/_snapshot.py 迁移至 TUI 根层级
  - 导入路径更新为 ..core.stats

快照内容（2026-10-22 用户需求）：在 token 速度统计之上合并
``total_input_tokens``——**所有 Agent**（主 Agent + 所有 SubAgent）的
上下文增量累计输入 token（本次 input − 同 Agent 上次 input；首次 / 缩小 /
切模型计全量，内部压缩 / 标题调用不计），供状态行常驻展示。费用与缓存明细
仍使用 ``get_total_input_tokens()`` 的 API 计费口径。

消费方说明（2026-07-31 方向F）：本模块仅含私有函数 ``_get_snapshot``，
**非死代码**——被 ``src/tui/app/status_bar.py`` 的 ``_snapshot()``（状态栏
token/速度展示）与 ``src/tui/_ink_bridge.py`` 的 ``get_status_elapsed()``
消费（惰性加载，异常静默）。
**保留不删**；恢复 Token 速度展示或迁移至统一快照源时须同步上述消费方。

★ review 方向（2026-08-05）：docstring 中旧消费方 ``src/tui/_bottom_bar/_bar.py``
已随 TUI 重构删除（_bottom_bar 目录不存在）——实际消费方为上列
``app/status_bar.py`` 与 ``_ink_bridge.py``（均已 search 验证）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

_logger = logging.getLogger(__name__)

# ── 模块级缓存 ──────────────────────────────────────────
# P3-18：Optional[callable] → Optional[Callable[[], Any]]（from typing 导入）
# ★ P2（review 2026-08-22）：类型标注与实际赋值不符——实际可被赋值为
#   False（bool，标记「模块不可用」），原 Optional 语义误导（None 与 False
#   语义不同：None=未加载，False=加载失败不可用）。改用 ``Callable | bool``。
_TOKEN_SPEED_SNAPSHOT: Callable[[], Any] | bool | None = None  # 也可赋值为 False（标记不可用）


def _build_snapshot() -> dict:
    """构建状态栏 token 快照：速度统计 + 全 Agent 输入总量。

    Returns:
        ``get_token_speed_snapshot()`` 的字典（``total_tokens`` /
        ``per_second_speed`` 等）+ ``total_input_tokens``（所有 Agent 的
        上下文增量累计输入 token）。读取失败时：速度快照回退空字典、输入
        总量回退 0，绝不向上抛异常（状态栏渲染是 best-effort 路径）。
    """
    from ..core.stats import get_token_speed_snapshot, get_total_agent_input_tokens

    try:
        data = dict(get_token_speed_snapshot() or {})
    except Exception:
        _logger.debug("token 速度快照读取失败", exc_info=True)
        data = {}
    try:
        data["total_input_tokens"] = max(0, int(get_total_agent_input_tokens() or 0))
    except Exception:
        _logger.debug("Agent 新增输入 token 读取失败", exc_info=True)
        data["total_input_tokens"] = 0
    return data


def _get_snapshot():
    """获取状态栏 token 快照函数引用（惰性加载，异常静默）。

    ★ P3（review）：异常集与「异常静默」声明对齐——修复前仅捕获
    ``ImportError``，stats 模块内部其它异常（如依赖缺失导致的其它错误）
    会向上传播（与文档声明不符）。现捕获 ``Exception`` 并记 debug（可观测），
    仍标记不可用。
    """
    global _TOKEN_SPEED_SNAPSHOT
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            # 仅探测依赖可用性；实际读取由 ``_build_snapshot`` 执行（含异常兜底）。
            # ★ 2026-10-23（lint 卫生）：副作用导入用 importlib 显式表达——
            #   pyflakes（ruff 缺失时的回退检查器）不识别 ``# noqa: F401``，
            #   原 ``from ..core.stats import ...  # noqa: F401`` 会报未使用导入。
            import importlib

            importlib.import_module("src.core.stats")
            _TOKEN_SPEED_SNAPSHOT = _build_snapshot
        except Exception:
            _logger.debug("token 速度快照惰性加载失败，标记不可用", exc_info=True)
            _TOKEN_SPEED_SNAPSHOT = False  # 标记不可用
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None
