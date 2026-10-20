"""内置会话投影单元声明 — 每个单元由清单中的独立插件条目注册。

「一切皆插件」：内置投影（``turnBoundary``）的**声明**集中在本模块的规格表
中；每一项都由清单中的**独立插件条目**（``session_projection``，经
``src.plugins.session_projection_entries``）显式注册，因而可被 Profile/Bundle
声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换。无清单（单元测试、
独立调用）时无接管，全部内置投影默认注册（向后兼容）。

投影单元为 ``(initial, folder)``：

- ``initial() -> state``：状态初值工厂；
- ``folder(state, event) -> state``：增量折叠函数（纯函数）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

from ..events.agent_types import SessionEventType

InitialFn = Callable[[], Any]
FolderFn = Callable[[Any, Any], Any]


def _turn_boundary_initial() -> Dict[str, Any]:
    return {"turn": 0, "open": False, "steps": 0, "interrupted": False}


def _turn_boundary_folder(state: Dict[str, Any], event: Any) -> Dict[str, Any]:
    if event.type == SessionEventType.TURN_START:
        state = {"turn": state.get("turn", 0) + 1, "open": True, "steps": 0, "interrupted": False}
    elif event.type == SessionEventType.STEP_START:
        state = {**state, "steps": state.get("steps", 0) + 1}
    elif event.type == SessionEventType.TURN_END:
        state = {**state, "open": False, "interrupted": bool((event.data or {}).get("interrupted", False))}
    return state


#: 会话标题投影（对应 dsh 的 ``foldSessionTitle``）：折叠最新的
#: ``session/title`` 事件，给出客户端可见标题与来源归因。标题事件只写入日志，
#: 不参与 ``derive_messages``（模型输入），因此该投影只服务展示层与会话列表。
def _title_initial() -> Dict[str, Any]:
    return {
        "title": None,
        "source": None,
        "model": "",
        "message_seqs": (),
        "updated_at": 0.0,
    }


def _title_folder(state: Dict[str, Any], event: Any) -> Dict[str, Any]:
    if event.type != SessionEventType.TITLE:
        return state
    data = event.data or {}
    title = str(data.get("title") or "").strip()
    if not title:
        return state
    try:
        updated_at = float(data.get("updated_at") or event.timestamp or 0.0)
    except (TypeError, ValueError):
        updated_at = 0.0
    seqs = data.get("message_seqs") or ()
    return {
        "title": title,
        "source": str(data.get("source") or "fallback"),
        "model": str(data.get("model") or ""),
        "message_seqs": tuple(seqs) if isinstance(seqs, (list, tuple)) else (),
        "updated_at": updated_at,
    }


#: 内置投影声明（name → (initial, folder)）——每项由清单中的独立插件条目注册。
BUILTIN_PROJECTIONS: Dict[str, Tuple[InitialFn, FolderFn]] = {
    "turnBoundary": (_turn_boundary_initial, _turn_boundary_folder),
    "title": (_title_initial, _title_folder),
}


def builtin_projection_names() -> list[str]:
    """全部内置投影名（含被接管/禁用的，按声明顺序）。"""
    return list(BUILTIN_PROJECTIONS)


def builtin_projection_spec(name: str) -> Tuple[InitialFn, FolderFn]:
    try:
        return BUILTIN_PROJECTIONS[name]
    except KeyError:
        raise KeyError(f"未知内置会话投影: {name!r}（可用: {list(BUILTIN_PROJECTIONS)}）") from None


__all__ = [
    "InitialFn",
    "FolderFn",
    "BUILTIN_PROJECTIONS",
    "builtin_projection_names",
    "builtin_projection_spec",
]
