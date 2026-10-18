"""TOC 树形连接符 — 解析层与渲染层共享的纯逻辑（无第三方依赖、无 tui 依赖）。

目录（Table of Contents）的树形前缀计算（``┣━``/``┗━``/``┃``）收敛为单一
真源，渲染侧只负责把结果映射到各自的输出模型（``AnsiLine``）。

纯函数实现：输入 ``[{"level", "text"}, ...]``，返回带 ``prefix`` 键的条目
列表（保持输入顺序与其余键）。
"""

from __future__ import annotations

#: 树形连接符字符（与历史实现逐字符一致）
CONN_ANCESTOR = "\u2503"   # ┃ 祖先仍活跃
CONN_INDENT = "   "        # 无祖先连线时占位
CONN_LAST = "\u2517\u2501 "   # ┗━ 同级最后一项
CONN_MID = "\u2523\u2501 "    # ┣━ 同级还有后续项


def build_toc_connectors(toc: list[dict]) -> list[dict]:
    """为 TOC 条目构建树形连接符前缀。

    根据条目层级关系计算每个条目的树前缀（``┣━``/``┗━``/``┃``），使目录以
    树形结构展示层级关系。

    Args:
        toc: TOC 条目列表（每项至少含 ``level`` 与 ``text``）。

    Returns:
        带 ``prefix`` 键的条目列表（空输入返回 ``[]``）。
    """
    if not toc:
        return []

    n = len(toc)
    is_last = [True] * n
    for i in range(n - 1):
        level_i = toc[i]["level"]
        for j in range(i + 1, n):
            if toc[j]["level"] == level_i:
                is_last[i] = False
                break
            elif toc[j]["level"] < level_i:
                break

    ancestors_active: dict[int, bool] = {}
    result: list[dict] = []
    for i, entry in enumerate(toc):
        level = entry["level"]
        prefix_parts: list[str] = []
        for depth in range(1, level):
            if ancestors_active.get(depth, False):
                prefix_parts.append(CONN_ANCESTOR + "  ")
            else:
                prefix_parts.append(CONN_INDENT)
        prefix_parts.append(CONN_LAST if is_last[i] else CONN_MID)
        ancestors_active[level] = not is_last[i]
        for depth in list(ancestors_active.keys()):
            if depth > level:
                del ancestors_active[depth]
        result.append({**entry, "prefix": "".join(prefix_parts)})
    return result


__all__ = ["build_toc_connectors", "CONN_ANCESTOR", "CONN_INDENT", "CONN_LAST", "CONN_MID"]
